"""Runtime leases, namespace construction, index warmup and cooperative shutdown."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import Future, wait
import logging
import threading
import time
from typing import Any
import config
from ..assistant_history_projection import configure_assistant_history_projection
from .adapters import build_akane_embedding_provider, build_akane_llm_client, build_akane_token_counter
from ..owner_private_session import memory_scope
from .contracts import _PROMPT_CONTEXT_KIND_PREFIX, _PROMPT_CONTEXT_RENDERER_ID

logger = logging.getLogger("akane.memcore")


def _render_prompt_context_snapshot(entry: Any, timezone: str) -> str:
    """Render trusted host context as instructions, without timeline metadata."""

    del timezone
    return str(getattr(entry, "semantic_text", "") or "").strip()


_PROCESS_RUNTIME_LOCK = threading.RLock()


_PROCESS_RUNTIME: Any | None = None


_PROCESS_RUNTIME_LEASES = 0


def _acquire_process_runtime(memcore: Any) -> Any:
    """Lease the one MemCoreRuntime shared by all live Akane managers."""

    global _PROCESS_RUNTIME, _PROCESS_RUNTIME_LEASES
    with _PROCESS_RUNTIME_LOCK:
        if _PROCESS_RUNTIME is None:
            workers = max(1, min(8, int(getattr(config, "MEMCORE_COMPACTION_WORKERS", 1) or 1)))
            _PROCESS_RUNTIME = memcore.MemCoreRuntime(compaction_workers=workers)
        _PROCESS_RUNTIME_LEASES += 1
        return _PROCESS_RUNTIME


def _release_process_runtime(runtime: Any, *, wait_for_workers: bool = True) -> None:
    """Release a process-runtime lease and close only after the final owner."""

    global _PROCESS_RUNTIME, _PROCESS_RUNTIME_LEASES
    runtime_to_close = None
    with _PROCESS_RUNTIME_LOCK:
        if runtime is not _PROCESS_RUNTIME or _PROCESS_RUNTIME_LEASES < 1:
            return
        _PROCESS_RUNTIME_LEASES -= 1
        if _PROCESS_RUNTIME_LEASES == 0:
            runtime_to_close = _PROCESS_RUNTIME
            _PROCESS_RUNTIME = None
    if runtime_to_close is not None:
        runtime_to_close.close(wait=wait_for_workers)


def _build_persona_text_provider(engine: Any) -> Any:
    """Build a persona_text provider that resolves character identity for memcore compaction prompts.

    Returns a callable (profile_user_id, character_pack_id) -> str that extracts the
    character's system_context persona text. Output/tool contracts are not persona
    material and are never copied into compaction prompts as a fallback.
    """
    default_text = ""

    def _resolve(profile_user_id: str, character_pack_id: str) -> str:
        if not profile_user_id:
            return default_text
        try:
            build_context = getattr(engine, "_build_memory_compaction_persona_context", None)
            if not callable(build_context):
                return default_text
            context = build_context(
                profile_user_id=profile_user_id,
                session_id=profile_user_id,
                character_pack_id=character_pack_id or "",
            )
            system_context = str(context.get("system_context") or "").strip() if isinstance(context, dict) else ""
            return system_context or default_text
        except Exception:
            return default_text

    return _resolve


class MemcoreLifecycleMixin:
    def request_shutdown(self) -> None:
        """Stop new work and cooperatively cancel this manager's active jobs."""

        with self._lock:
            if self._closed:
                return
            self._closing = True
            self._shutdown_event.set()
            self._pending_compactions.clear()
            systems = list(self._systems.values())
            futures = list(self._background_futures)
        for system in systems:
            request_shutdown = getattr(system, "request_shutdown", None)
            if callable(request_shutdown):
                try:
                    request_shutdown()
                except Exception as exc:
                    logger.debug("memcore MemorySystem shutdown signal failed: %s", exc)
        for future in futures:
            future.cancel()

    def close(self, *, timeout: float = 20.0) -> bool:
        """Close without holding Host shutdown open indefinitely.

        Running executor jobs cannot be force-cancelled safely.  When the
        deadline expires their store stays open and the final Future callback
        performs the close after the job has observed cooperative cancellation.
        """

        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            if self._closed:
                return True
        self.request_shutdown()
        with self._lock:
            futures = list(self._background_futures)
        if futures:
            wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        # A Future becomes done before its callbacks finish using the store.
        # Wait for those callbacks too, within the same shutdown deadline.
        with self._jobs_condition:
            self._jobs_condition.wait_for(lambda: not self._background_futures,
                                          timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            active = bool(self._background_futures)
        if active:
            with self._lock:
                self._deferred_close = True
                active_count = len(self._background_futures)
            logger.warning("memcore shutdown deferred active_jobs=%d", active_count)
            # A Future can finish between wait() returning and the deferred
            # flag being stored. Re-check so that race cannot strand the store.
            self._finalize_deferred_close_if_ready()
            with self._lock:
                return self._closed
        return self._finalize_close(wait_for_workers=True)

    def _finalize_close(self, *, wait_for_workers: bool) -> bool:
        with self._lock:
            if self._closed:
                return True
            if self._close_finalizing or self._background_futures:
                return False
            self._close_finalizing = True
            systems = list(self._systems.values())
            self._systems.clear()
            self._session_llms.clear()
            self._session_token_limits.clear()
            self._index_warmup_futures.clear()
            self._compaction_chat_model_overrides.clear()
            self._compaction_turn_model_hints.clear()
            store = self._store
            self._store = None
            self._index = None
            runtime = self._runtime
            uses_process_runtime = self._uses_process_runtime
            self._runtime = None
            self._uses_process_runtime = False
            self._background_futures.clear()
            self._background_compactions.clear()
            self._pending_compactions.clear()
            self._compaction_retry_after.clear()
        for system in systems:
            try:
                system.close()
            except Exception as exc:
                logger.debug("memcore MemorySystem close failed: %s", exc)
        if store is not None:
            try:
                store.close()
            except Exception as exc:
                logger.debug("memcore store close failed: %s", exc)
        if uses_process_runtime and runtime is not None:
            try:
                _release_process_runtime(runtime, wait_for_workers=wait_for_workers)
            except Exception as exc:
                logger.debug("memcore process runtime close failed: %s", exc)
        with self._lock:
            self._available = False
            self._closed = True
            self._closing = False
            self._close_finalizing = False
            self._deferred_close = False
        return True

    def _finalize_deferred_close_if_ready(self) -> None:
        with self._lock:
            ready = self._deferred_close and not self._background_futures
        if ready:
            self._finalize_close(wait_for_workers=False)

    def _bootstrap(self) -> None:
        store = None
        process_runtime = None
        try:
            memcore = self._import_memcore()

            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._embedding = (
                build_akane_embedding_provider(self.embedding_provider)
                if self.retrieval_mode == "hybrid" else None
            )
            self._token_counter = build_akane_token_counter()
            self._memory_config = self._build_memory_config(memcore)
            store = memcore.SQLiteMemoryStore(str(self.storage_path))
            index = memcore.InMemoryVectorIndex(embedding=self._embedding) if self._embedding is not None else None
            if self._runtime is None:
                process_runtime = _acquire_process_runtime(memcore)
                self._runtime = process_runtime
                self._uses_process_runtime = True
            elif not isinstance(self._runtime, memcore.MemCoreRuntime):
                raise TypeError("runtime must be a MemCoreRuntime or None")
            self._store = store
            self._index = index
            self._memcore_module = memcore
            self._available = True
            self._reason = ""
        except Exception as exc:
            if process_runtime is not None:
                _release_process_runtime(process_runtime)
                self._runtime = None
                self._uses_process_runtime = False
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass
            self._available = False
            self._reason = str(exc) or exc.__class__.__name__

    @staticmethod
    def _import_memcore() -> Any:
        import memcore  # type: ignore

        return memcore

    def _build_memory_config(self, memcore: Any) -> Any:
        return memcore.MemoryConfig(
            retrieval_mode=self.retrieval_mode,
            compaction_request_mode=str(getattr(config, "MEMCORE_COMPACTION_REQUEST_MODE", "independent")),
            working_token_trigger=max(0, int(getattr(config, "MEMCORE_WORKING_TOKEN_TRIGGER", 200000))),
            raw_token_trigger=max(
                1000,
                int(getattr(config, "MEMCORE_RAW_TOKEN_TRIGGER", 48000) or 48000),
            ),
            raw_token_batch_ratio=max(
                0.01,
                min(0.99, float(getattr(config, "MEMCORE_RAW_TOKEN_BATCH_RATIO", 0.67) or 0.67)),
            ),
            llm_timeout_s=max(
                1.0,
                min(120.0, float(getattr(config, "MEMCORE_LLM_TIMEOUT_SECONDS", 55.0) or 55.0)),
            ),
            episodic_visible_max=max(
                1,
                int(getattr(config, "EPISODIC_VISIBLE_MAX", 10) or 10),
            ),
            episodic_compact_trigger_count=max(
                1,
                int(getattr(config, "EPISODIC_COMPACT_TRIGGER_COUNT", 10) or 10),
            ),
            episodic_compact_batch_size=max(
                1,
                int(getattr(config, "EPISODIC_COMPACT_BATCH_SIZE", 5) or 5),
            ),
            semantic_visible_limit=max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 5) or 5)),
            retrieval_result_token_budget=max(
                0,
                int(getattr(config, "MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET", 0) or 0),
            ),
            native_timeline_page_token_budget=max(
                1,
                int(getattr(config, "MEMCORE_NATIVE_TIMELINE_PAGE_TOKEN_BUDGET", 12000) or 12000),
            ),
            visible_memory_scope=self.visible_scope,
            enable_flavor=self.enable_flavor,
            operation_projection_policy=str(
                getattr(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "full_until_raw_compaction") or ""
            )
            or "full_until_raw_compaction",
            operation_settlement_min_saved_ratio=float(
                getattr(config, "MEMCORE_OPERATION_SETTLEMENT_MIN_SAVED_RATIO", 0.3)
            ),
            operation_argument_min_utf8_bytes=int(getattr(config, "MEMCORE_OPERATION_ARGUMENT_MIN_UTF8_BYTES", 1024)),
            operation_argument_preview_chars=int(getattr(config, "MEMCORE_OPERATION_ARGUMENT_PREVIEW_CHARS", 96)),
        )

    def _get_system_or_none(
        self,
        *,
        operation: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> Any | None:
        if not self.enabled:
            self._reason = "memory_backend_legacy"
            return None
        if not self.available:
            return None
        try:
            return self._get_system(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        except Exception as exc:
            self._reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s unavailable: %s", operation, self._reason)
            return None

    @memory_scope
    def _get_system(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        warm_index: bool = True,
    ) -> Any:
        if self._closing or self._closed:
            raise RuntimeError("memcore_manager_closed")
        if self._memcore_module is None:
            raise RuntimeError(self._reason or "memcore_not_bootstrapped")
        if self._store is None or self._memory_config is None or self._runtime is None:
            raise RuntimeError(self._reason or "memcore_dependencies_not_ready")
        if self.retrieval_mode == "hybrid" and (self._embedding is None or self._index is None):
            raise RuntimeError(self._reason or "memcore_retrieval_dependencies_not_ready")

        user_id = str(profile_user_id or session_id or "default_user").strip() or "default_user"
        conversation_id = str(session_id or user_id).strip() or user_id
        domain_id = str(character_pack_id or "").strip()
        key = (user_id, conversation_id, domain_id)
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("memcore_manager_closed")
            if key in self._deleting_namespaces:
                raise RuntimeError("subagent_memory_deleted")
            existing = self._systems.get(key)
            if existing is None:
                namespace = self._memcore_module.Namespace(
                    user_id=user_id,
                    tenant_id="",
                    domain_id=domain_id,
                    conversation_id=conversation_id,
                )
                persona_text = ""
                is_child = conversation_id.startswith("subagent_") and user_id == conversation_id
                if self._persona_text_provider is not None and not is_child:
                    try:
                        persona_text = self._persona_text_provider(profile_user_id, character_pack_id)
                    except Exception as exc:
                        logger.debug("memcore persona_text_provider failed: %s", exc)
                renderer_registry = self._memcore_module.default_renderer_registry()
                renderer_registry.register_prefix(
                    _PROMPT_CONTEXT_KIND_PREFIX,
                    renderer_id=_PROMPT_CONTEXT_RENDERER_ID,
                    version=1,
                    renderer=_render_prompt_context_snapshot,
                    compact_renderer=_render_prompt_context_snapshot,
                )
                existing = self._memcore_module.MemorySystem(
                    llm=build_akane_llm_client(
                        self._subagent_summary_runtime(key) if is_child else self.llm,
                        cancellation_requested=self._shutdown_event.is_set,
                        chat_model_override_provider=lambda key=key: self._compaction_chat_model_override(key),
                    ),
                    namespace=namespace,
                    timezone=str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai",
                    storage_dir=str(self.storage_path),
                    config=(self._subagent_memory_config(key) if is_child else (
                        replace(self._memory_config, visible_memory_scope="conversation")
                        if (conversation_id == "master" and user_id == "master")
                        or conversation_id.startswith(("qq_pri_", "qq_group_shared_"))
                        else replace(self._memory_config)
                    )),
                    store=self._store,
                    index=self._index,
                    embedding=self._embedding,
                    token_counter=self._token_counter,
                    persona_text=persona_text,
                    prompt_overrides=self._subagent_prompt_overrides() if is_child else self._build_prompt_overrides(persona_text),
                    renderer_registry=renderer_registry,
                    runtime=self._runtime,
                )
                configure_assistant_history_projection(existing)
                self._systems[key] = existing
        if warm_index:
            self._warm_index_for_system(existing, operation="get_system")
        return existing

    @staticmethod
    def _build_prompt_overrides(persona_text: str) -> Any:
        """Build PromptOverrides with Akane-specific compaction guidance."""
        try:
            from memcore.prompts import PromptOverrides
        except ImportError:
            return None
        extra_semantic = (
            "stable_facts 保留用户明确表达且仍有效的长期偏好、相处约定，以及经确认的身份、关系和长期计划；"
            "单次工具操作的执行结果和系统配置字段（reply_mode 等）不应出现在 stable_facts 里。"
        )
        return PromptOverrides(
            persona_text=persona_text,
            extra_summary_guidance=(
                "qq_private、desktop 表示私人交流，qq_group 表示群聊。"
                "私人经历保留当事人、感受、明确偏好与约定及其适用范围，不得记作群内共同经历。"
            ),
            extra_semantic_guidance=extra_semantic + "私人交流不得记作群内共同经历，长期记忆保留主体和场景归属。",
        )

    def _warm_index_for_system(self, system: Any, *, operation: str) -> None:
        if self.retrieval_mode == "navigation":
            return
        namespace = getattr(system, "namespace", None)
        if namespace is None:
            return
        hard_key = tuple(str(item or "") for item in namespace.hard_key())
        with self._lock:
            if self._closing or self._closed or self._runtime is None:
                return
            if hard_key in self._warmed_index_keys:
                return
            self._warmed_index_keys.add(hard_key)
            try:
                future = self._runtime.submit_index_repair(
                    self._run_index_warmup,
                    system,
                    hard_key,
                    operation,
                )
                self._index_warmup_futures[hard_key] = future
                self._track_background_future_locked(future)
            except Exception as exc:
                self._warmed_index_keys.discard(hard_key)
                logger.warning(
                    "memcore %s index warmup scheduling failed: %s",
                    operation,
                    str(exc) or exc.__class__.__name__,
                )

    def _track_background_future_locked(self, future: Future[Any]) -> None:
        self._background_futures.add(future)
        future.add_done_callback(self._discard_background_future)

    def _discard_background_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._background_futures.discard(future)
            self._jobs_condition.notify_all()
        self._finalize_deferred_close_if_ready()

    def _run_index_warmup(
        self,
        system: Any,
        hard_key: tuple[str, str, str],
        operation: str,
    ) -> None:
        try:
            namespace = getattr(system, "namespace", None)
            if namespace is None:
                return
            raw_limit = getattr(config, "MEMCORE_REINDEX_ON_NAMESPACE_LOAD_LIMIT", None)
            limit = self._coerce_positive_int_or_none(raw_limit)
            system.reindex_all(
                namespace=namespace,
                limit=limit,
                current_conversation_only=False,
                batch_size=max(1, int(getattr(config, "EMBEDDING_REINDEX_BATCH_SIZE", 64) or 64)),
            )
        except Exception as exc:
            with self._lock:
                self._warmed_index_keys.discard(hard_key)
            logger.warning("memcore %s index warmup failed: %s", operation, str(exc) or exc.__class__.__name__)
