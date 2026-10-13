"""Session model selection, bounded compaction scheduling and result diagnostics."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import hashlib
import logging
import re
import time
from typing import Any
import config
from ..owner_private_session import memory_scope
from .contracts import resolve_memcore_provider_profile

logger = logging.getLogger("akane.memcore")


class MemcoreCompactionMixin:
    @memory_scope
    def set_compaction_chat_model_override(
        self, *, profile_user_id: str, session_id: str,
        character_pack_id: str = "", chat_model_override: str = "",
    ) -> None:
        """Remember an explicit session choice, including the empty default choice."""
        user = str(profile_user_id or session_id or "default_user").strip() or "default_user"
        conversation = str(session_id or user).strip() or user
        key = (user, conversation, str(character_pack_id or "").strip())
        with self._lock:
            self._compaction_chat_model_overrides[key] = str(chat_model_override or "").strip()

    def _compaction_chat_model_override(self, key: tuple[str, str, str]) -> str:
        with self._lock:
            return self._compaction_chat_model_overrides.get(key, self._compaction_turn_model_hints.get(key, ""))

    def _remember_compaction_turn_model(
        self, *, profile_user_id: str, session_id: str,
        character_pack_id: str, chat_model_override: str,
    ) -> None:
        # A completed turn carries a snapshot, not a new session selection.
        # It must not undo a model command that arrived while the turn ran.
        user = str(profile_user_id or session_id or "default_user").strip() or "default_user"
        conversation = str(session_id or user).strip() or user
        key = (user, conversation, str(character_pack_id or "").strip())
        with self._lock:
            if key not in self._compaction_chat_model_overrides:
                self._compaction_turn_model_hints[key] = str(chat_model_override or "").strip()

    @memory_scope
    def compact_due_background(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        provider_profile: str = "",
        chat_model_override: str | None = None,
    ) -> dict[str, Any]:
        requested_profile = str(provider_profile or "").strip()
        resolved_profile = resolve_memcore_provider_profile(requested_profile) if requested_profile else ""
        if requested_profile and not resolved_profile:
            return self._status(
                "compact_due_background",
                False,
                "invalid_provider_profile",
                reason="provider_profile_unsupported",
            )
        system = self._get_system_or_none(
            operation="compact_due_background",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status("compact_due_background", False, "unavailable", reason=self._reason)
        if chat_model_override is not None:
            self._remember_compaction_turn_model(
                profile_user_id=profile_user_id, session_id=session_id,
                character_pack_id=character_pack_id, chat_model_override=chat_model_override,
            )
        try:
            with self._lock:
                if self._closing or self._closed:
                    raise RuntimeError("memcore_manager_closed")
                compaction_key = self._compaction_namespace_key(system)
                active = self._background_compactions.get(compaction_key)
                if active is not None and not active.done():
                    self._pending_compactions[compaction_key] = (system, resolved_profile)
                    return {
                        **self._status("compact_due_background", True, "coalesced"),
                        "provider_profile": resolved_profile,
                    }
                if active is not None:
                    self._background_compactions.pop(compaction_key, None)
                    self._pending_compactions.pop(compaction_key, None)
                retry_after = self._compaction_retry_after.get(compaction_key, 0.0)
                retry_after_seconds = max(0.0, retry_after - time.monotonic())
                if retry_after_seconds > 0:
                    return {
                        **self._status("compact_due_background", True, "deferred"),
                        "provider_profile": resolved_profile,
                        "retry_after_seconds": max(1, int(retry_after_seconds + 0.999)),
                    }
                self._compaction_retry_after.pop(compaction_key, None)
                self._submit_compaction_locked(
                    compaction_key=compaction_key,
                    system=system,
                    provider_profile=resolved_profile,
                )
            return {
                **self._status("compact_due_background", True, "scheduled"),
                "provider_profile": resolved_profile,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore background compaction scheduling failed: %s", reason)
            return self._status("compact_due_background", False, "failed", reason=reason)

    @memory_scope
    def compact_due_sync(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        provider_profile: str = "",
        chat_model_override: str | None = None,
    ) -> dict[str, Any]:
        requested_profile = str(provider_profile or "").strip()
        resolved_profile = resolve_memcore_provider_profile(requested_profile) if requested_profile else ""
        if requested_profile and not resolved_profile:
            return self._status(
                "compact_due_sync",
                False,
                "invalid_provider_profile",
                reason="provider_profile_unsupported",
            )
        system = self._get_system_or_none(
            operation="compact_due_sync",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status("compact_due_sync", False, "unavailable", reason=self._reason)
        if chat_model_override is not None:
            self._remember_compaction_turn_model(
                profile_user_id=profile_user_id, session_id=session_id,
                character_pack_id=character_pack_id, chat_model_override=chat_model_override,
            )
        try:
            with self._lock:
                active = self._background_compactions.get(self._compaction_namespace_key(system))
            if active is not None:
                while True:
                    if self._shutdown_event.is_set():
                        return self._status("compact_due_sync", False, "cancelled", reason="shutdown_requested")
                    from ..turn_coordination import current_cancellation_check
                    cancelled = current_cancellation_check()
                    if cancelled and cancelled():
                        return self._status("compact_due_sync", False, "cancelled", reason="request_cancelled")
                    try:
                        stats = dict(active.result(timeout=0.1))
                        break
                    except FutureTimeoutError:
                        continue
            else:
                stats = dict(system.compact_due_sync(provider_profile=resolved_profile))
            self._ensure_history_projection_notice(system, compaction_stats=stats)
            self._log_compaction_stats(
                stats,
                namespace_hash=self._compaction_namespace_hash(system),
                requested_profile=resolved_profile,
            )
            return {
                **self._status("compact_due_sync", True, "completed"),
                "stats": stats,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore sync compaction failed: %s", reason)
            return self._status("compact_due_sync", False, "failed", reason=reason)

    def _submit_compaction_locked(
        self,
        *,
        compaction_key: tuple[str, str, str, str],
        system: Any,
        provider_profile: str,
    ) -> None:
        future = system.compact_due_background(provider_profile=provider_profile)
        self._background_compactions[compaction_key] = future
        self._background_futures.add(future)
        namespace_hash = self._compaction_namespace_hash(system)
        future.add_done_callback(
            lambda completed, key=compaction_key, namespace_hash=namespace_hash, profile=provider_profile: (
                self._finish_compaction(
                    completed,
                    compaction_key=key,
                    namespace_hash=namespace_hash,
                    requested_profile=profile,
                    system=system,
                )
            )
        )
        future.add_done_callback(lambda _completed: self._finalize_deferred_close_if_ready())

    def _finish_compaction(
        self,
        future: Future[Any],
        *,
        compaction_key: tuple[str, str, str, str],
        namespace_hash: str,
        requested_profile: str,
        system: Any = None,
    ) -> None:
        self._log_compaction_result(
            future,
            namespace_hash=namespace_hash,
            requested_profile=requested_profile,
        )
        if system is not None and not future.cancelled():
            try:
                stats = future.result()
            except Exception:
                stats = None
            if isinstance(stats, dict):
                if (system.namespace.user_id, system.namespace.conversation_id, system.namespace.domain_id or "") not in self._deleting_namespaces:
                    self._ensure_history_projection_notice(system, compaction_stats=stats)
        with self._lock:
            self._background_futures.discard(future)
            self._jobs_condition.notify_all()
            if self._background_compactions.get(compaction_key) is not future:
                return
            self._background_compactions.pop(compaction_key, None)
            pending = self._pending_compactions.pop(compaction_key, None)
            compaction_status = self._compaction_future_status(future)
            if compaction_status not in {"compacted", "not_due"}:
                cooldown = self._compaction_failure_cooldown_seconds()
                if cooldown > 0 and not self._closing and not self._closed:
                    self._compaction_retry_after[compaction_key] = time.monotonic() + cooldown
                return
            self._compaction_retry_after.pop(compaction_key, None)
            if pending is None or self._closing or self._closed:
                return
            system, provider_profile = pending
            if (system.namespace.user_id, system.namespace.conversation_id, system.namespace.domain_id or "") in self._deleting_namespaces:
                return
            try:
                self._submit_compaction_locked(
                    compaction_key=compaction_key,
                    system=system,
                    provider_profile=provider_profile,
                )
            except Exception as exc:
                logger.warning(
                    "memcore coalesced compaction scheduling failed: %s",
                    str(exc) or exc.__class__.__name__,
                )

    @staticmethod
    def _compaction_future_status(future: Future[Any]) -> str:
        if future.cancelled():
            return "cancelled"
        try:
            result = future.result()
        except Exception:
            return "failed"
        return str(result.get("status") or "") if isinstance(result, dict) else "invalid_result"

    @staticmethod
    def _compaction_failure_cooldown_seconds() -> float:
        try:
            value = float(getattr(config, "MEMCORE_COMPACTION_FAILURE_COOLDOWN_SECONDS", 60.0) or 0.0)
        except (TypeError, ValueError):
            value = 60.0
        return max(0.0, min(3600.0, value))

    @staticmethod
    def _compaction_namespace_hash(system: Any) -> str:
        values = MemcoreCompactionMixin._compaction_namespace_key(system)
        if values == ("", "", "", ""):
            return "unknown"
        return hashlib.sha256("\x1f".join(values).encode("utf-8", errors="ignore")).hexdigest()[:12]

    @staticmethod
    def _compaction_namespace_key(system: Any) -> tuple[str, str, str, str]:
        namespace = getattr(system, "namespace", None)
        if namespace is None:
            return ("", "", "", "")
        return (
            str(getattr(namespace, "tenant_id", "") or ""),
            str(getattr(namespace, "user_id", "") or ""),
            str(getattr(namespace, "domain_id", "") or ""),
            str(getattr(namespace, "conversation_id", "") or ""),
        )

    @staticmethod
    def _log_compaction_stats(
        stats: Any,
        *,
        namespace_hash: str,
        requested_profile: str,
    ) -> None:
        if not isinstance(stats, dict):
            logger.warning(
                "memcore_compaction status=invalid_result namespace=%s profile=%s",
                str(namespace_hash or "unknown")[:16],
                str(requested_profile or "default")[:40],
            )
            return
        status = str(stats.get("status") or "unknown")[:40]
        profile = str(stats.get("provider_profile") or requested_profile or "default")[:40]
        reason = re.sub(r"[\r\n\t]+", " ", str(stats.get("reason") or ""))[:120]

        def _safe_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        fields = (
            "memcore_compaction status=%s namespace=%s profile=%s reason=%s "
            "before_tokens=%d after_tokens=%d raw_before_tokens=%d raw_after_tokens=%d "
            "terminal_before_tokens=%d terminal_after_tokens=%d "
            "planned_source_tokens=%d selected_source_tokens=%d generation=%d "
            "source_turns=%d source_entries=%d summaries=%d"
        )
        args = (
            status,
            str(namespace_hash or "unknown")[:16],
            profile,
            reason or "none",
            _safe_int(stats.get("before_projected_tokens")),
            _safe_int(stats.get("after_projected_tokens")),
            _safe_int(stats.get("before_raw_projected_tokens")),
            _safe_int(stats.get("after_raw_projected_tokens")),
            _safe_int(stats.get("before_terminal_raw_projected_tokens")),
            _safe_int(stats.get("after_terminal_raw_projected_tokens")),
            _safe_int(stats.get("planned_source_tokens")),
            _safe_int(stats.get("selected_projected_tokens")),
            _safe_int(stats.get("compaction_generation")),
            _safe_int(stats.get("source_turn_count")),
            _safe_int(stats.get("source_entry_count")),
            _safe_int(stats.get("summaries_created")),
        )
        if status == "compacted":
            logger.info(fields, *args)
        elif status in {"failed", "blocked_by_open_turn", "stale_batch"}:
            logger.warning(fields, *args)
        elif status in {"not_due", "busy"}:
            logger.debug(fields, *args)
        else:
            logger.warning(fields, *args)

    @classmethod
    def _log_compaction_result(
        cls,
        future: Any,
        *,
        namespace_hash: str = "unknown",
        requested_profile: str = "",
    ) -> None:
        if future.cancelled():
            logger.debug(
                "memcore_compaction status=cancelled namespace=%s profile=%s",
                str(namespace_hash or "unknown")[:16],
                str(requested_profile or "default")[:40],
            )
            return
        try:
            stats = future.result()
        except Exception as exc:
            logger.warning("memcore background compaction failed: %s", str(exc) or exc.__class__.__name__)
            return
        cls._log_compaction_stats(
            stats,
            namespace_hash=namespace_hash,
            requested_profile=requested_profile,
        )
