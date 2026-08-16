"""Memcore runtime manager for Akane.

memcore is the primary dialogue memory backend. ``legacy`` and ``dual`` modes
remain as explicit compatibility / migration tools while old Akane memory code
is being retired.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from concurrent.futures import Future, wait
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

import config

from .adapters import build_akane_embedding_provider, build_akane_llm_client, build_akane_token_counter
from .diagnostics import snippet_hashes


SUPPORTED_MEMORY_BACKENDS = frozenset({"legacy", "dual", "memcore"})
MEMCORE_PROVIDER_PROFILE_ALIASES = {
    "openai": "openai_chat",
    "openai_chat": "openai_chat",
    "native_openai": "openai_chat",
    "openai_responses": "openai_responses",
    "responses": "openai_chat",
    "deepseek": "deepseek_chat",
    "deepseek_chat": "deepseek_chat",
    "ollama": "openai_chat",
    # GeminiNativeCompatClient accepts the same OpenAI-shaped message history
    # as the rest of Akane and converts it only at the final wire boundary.
    "gemini": "openai_chat",
    "anthropic": "anthropic_messages",
    "anthropic_messages": "anthropic_messages",
    "native_anthropic": "anthropic_messages",
    "canonical": "canonical_user_assistant",
    "canonical_user_assistant": "canonical_user_assistant",
}
logger = logging.getLogger("akane.memcore")

_EXPLICIT_RETRIEVAL_KIND_ROOTS = ("tool", "event", "skill", "material")
_EXPLICIT_KIND_PATTERN = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*(?:\.\*)?")
_STALE_OPEN_TURN_MAX_AGE_SECONDS = 30 * 60


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


def _release_process_runtime(runtime: Any) -> None:
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
        runtime_to_close.close(wait=True)


def normalize_memory_backend(value: Any) -> str:
    text = str(value or "memcore").strip().lower()
    return text if text in SUPPORTED_MEMORY_BACKENDS else "memcore"


def normalize_visible_scope(value: Any) -> str:
    text = str(value or "user").strip().lower()
    return text if text in {"conversation", "user"} else "user"


def resolve_memcore_provider_profile(value: Any) -> str:
    """Map the actual provider protocol to a MemCore projection profile."""

    return MEMCORE_PROVIDER_PROFILE_ALIASES.get(str(value or "").strip().lower(), "")


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


@dataclass(frozen=True)
class MemcoreRuntimeStatus:
    backend: str
    enabled: bool
    available: bool
    storage_path: str
    visible_scope: str
    enable_flavor: bool
    shadow_compare: bool
    degraded_embedding: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemcoreManager:
    def __init__(
        self,
        *,
        backend: str,
        storage_path: Path,
        visible_scope: str,
        enable_flavor: bool,
        shadow_compare: bool,
        llm: Any,
        embedding_provider: Any,
        persona_text_provider: Callable[[str, str], str] | None = None,
        runtime: Any | None = None,
    ) -> None:
        self.backend = normalize_memory_backend(backend)
        self.storage_path = Path(storage_path)
        self.visible_scope = normalize_visible_scope(visible_scope)
        self.enable_flavor = bool(enable_flavor)
        self.shadow_compare = bool(shadow_compare)
        self.llm = llm
        self.embedding_provider = embedding_provider
        self._persona_text_provider = persona_text_provider
        self._available = False
        self._reason = ""
        self._degraded_embedding = str(getattr(embedding_provider, "name", "") or "").lower() == "hashed"
        self._memcore_module: Any | None = None
        self._llm_client: Any | None = None
        self._embedding: Any | None = None
        self._token_counter: Any | None = None
        self._memory_config: Any | None = None
        self._store: Any | None = None
        self._index: Any | None = None
        self._runtime: Any | None = runtime
        self._uses_process_runtime = False
        self._systems: dict[tuple[str, str, str], Any] = {}
        self._warmed_index_keys: set[tuple[str, str, str]] = set()
        self._background_futures: set[Future[Any]] = set()
        self._background_compactions: dict[tuple[str, str, str, str], Future[Any]] = {}
        self._pending_compactions: dict[tuple[str, str, str, str], tuple[Any, str]] = {}
        self._compaction_retry_after: dict[tuple[str, str, str, str], float] = {}
        self._lock = threading.RLock()
        self._closing = False
        self._closed = False
        if self.enabled:
            self._bootstrap()

    @classmethod
    def from_engine(cls, engine: Any) -> "MemcoreManager":
        storage_path = Path(
            getattr(engine, "memcore_storage_path", Path(engine.base_dir) / "memcore_v01.db")
        )
        return cls(
            backend=getattr(config, "MEMORY_BACKEND", "memcore"),
            storage_path=storage_path,
            visible_scope=getattr(config, "MEMCORE_VISIBLE_SCOPE", "user"),
            enable_flavor=bool(getattr(config, "MEMCORE_ENABLE_FLAVOR", True)),
            shadow_compare=bool(getattr(config, "MEMCORE_SHADOW_COMPARE", False)),
            llm=engine.llm,
            embedding_provider=engine.embedding_provider,
            persona_text_provider=_build_persona_text_provider(engine),
        )

    @property
    def enabled(self) -> bool:
        return self.backend != "legacy"

    @property
    def available(self) -> bool:
        return self._available

    def status(self) -> dict[str, Any]:
        return MemcoreRuntimeStatus(
            backend=self.backend,
            enabled=self.enabled,
            available=self.available,
            storage_path=str(self.storage_path),
            visible_scope=self.visible_scope,
            enable_flavor=self.enable_flavor,
            shadow_compare=self.shadow_compare,
            degraded_embedding=self._degraded_embedding,
            reason=self._reason,
        ).to_dict()

    def close(self) -> None:
        with self._lock:
            if self._closed or self._closing:
                return
            self._closing = True
            self._pending_compactions.clear()
            futures = list(self._background_futures)
        # A shared runtime cannot cancel work by manager. Track our own jobs so
        # this store remains valid until its running warmup/compaction finishes.
        for future in futures:
            future.cancel()
        if futures:
            wait(futures)
        with self._lock:
            systems = list(self._systems.values())
            self._systems.clear()
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
            _release_process_runtime(runtime)
        with self._lock:
            self._available = False
            self._closed = True
            self._closing = False

    def append_standalone_message(
        self,
        record: dict[str, Any],
        *,
        role: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
        observed: bool = False,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        """Append a message that intentionally has no model-response turn."""

        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"user", "assistant"}:
            return self._status(
                "append_standalone_message",
                False,
                "invalid_record",
                source_id=str((record or {}).get("source_id") or ""),
                reason="role_must_be_user_or_assistant",
            )
        return self._append_standalone_turn(
            operation="append_standalone_message",
            role=normalized_role,
            record=record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            actor_stable_id=actor_stable_id,
            actor_display_name=actor_display_name,
            observed=observed,
            target_actor_id=target_actor_id,
            target_actor_display_name=target_actor_display_name,
        )

    def record_voice_projection(
        self,
        projection_record: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Apply one VoiceCore MemCore projection without copying its reducer.

        Provisional voice events remain durable but prompt-invisible.  A final
        user voice message opens the same V2 turn that a later assistant voice
        projection completes, so callers must not submit the transcript again
        through the ordinary text-message path.
        """

        operation = "record_voice_projection"
        if not isinstance(projection_record, dict):
            return self._status(
                operation,
                False,
                "invalid_projection",
                reason="voice_projection_contract_invalid",
            )
        record = dict(projection_record)
        projection_id = str(record.get("projection_id") or "").strip()
        target = str(record.get("target") or "").strip().lower()
        kind = str(record.get("kind") or "").strip().lower()
        source_event_id = str(record.get("source_event_id") or "").strip()
        payload = record.get("payload")
        if (
            not projection_id
            or target != "memcore"
            or not source_event_id
            or not isinstance(payload, dict)
            or (kind not in {"message.user.voice", "message.assistant.voice"} and not kind.startswith("event.voice."))
        ):
            return self._status(
                operation,
                False,
                "invalid_projection",
                reason="voice_projection_contract_invalid",
            )
        try:
            projection_digest = hashlib.sha256(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8", errors="strict")
            ).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            return self._status(
                operation,
                False,
                "invalid_projection",
                reason="voice_projection_payload_not_json",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)

        effective_ts = int(time.time() if timestamp is None else timestamp)
        source_id = f"voice-projection:{projection_id}"
        existing = self._voice_projection_replay_status(
            system=system,
            operation=operation,
            source_id=source_id,
            kind=kind,
            projection_digest=projection_digest,
        )
        if existing is not None:
            return existing
        try:
            if kind == "message.user.voice":
                return self._begin_voice_projection_turn(
                    system=system,
                    operation=operation,
                    source_id=source_id,
                    source_event_id=source_event_id,
                    projection_id=projection_id,
                    payload=payload,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    actor_stable_id=actor_stable_id,
                    actor_display_name=actor_display_name,
                    timestamp=effective_ts,
                    projection_digest=projection_digest,
                )
            if kind == "message.assistant.voice":
                return self._complete_voice_projection_turn(
                    system=system,
                    operation=operation,
                    source_id=source_id,
                    source_event_id=source_event_id,
                    projection_id=projection_id,
                    payload=payload,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    timestamp=effective_ts,
                    projection_digest=projection_digest,
                )
            return self._append_voice_projection_event(
                system=system,
                operation=operation,
                source_id=source_id,
                source_event_id=source_event_id,
                projection_id=projection_id,
                kind=kind,
                payload=payload,
                timestamp=effective_ts,
                projection_digest=projection_digest,
            )
        except Exception as exc:
            logger.warning(
                "memcore voice projection failed error_type=%s",
                exc.__class__.__name__,
            )
            return self._status(
                operation,
                False,
                "failed",
                source_id=source_id,
                reason="voice_projection_write_failed",
            )

    def resolve_voice_projection_turn(
        self,
        *,
        projection_id: str,
        voice_turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Resolve a committed voice stimulus for the host Thinking Agent."""

        operation = "resolve_voice_projection_turn"
        normalized_projection_id = str(projection_id or "").strip()
        normalized_voice_turn_id = str(voice_turn_id or "").strip()
        if not normalized_projection_id or not normalized_voice_turn_id:
            return self._status(
                operation,
                False,
                "invalid_request",
                reason="voice_projection_turn_identity_missing",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._store is None:
            return self._status(
                operation,
                False,
                "unavailable",
                reason=self._reason or "memcore_store_unavailable",
            )
        source_id = f"voice-projection:{normalized_projection_id}"
        try:
            existing = self._store.get_record_by_source_id(source_id)
        except Exception:
            return self._status(
                operation,
                False,
                "failed",
                source_id=source_id,
                reason="voice_projection_turn_read_failed",
            )
        if existing is None:
            return self._status(
                operation,
                False,
                "not_found",
                source_id=source_id,
                reason="voice_projection_turn_not_found",
            )
        if not self._record_belongs_to_namespace(existing, system.namespace):
            return self._status(
                operation,
                False,
                "conflict",
                source_id=source_id,
                reason="voice_projection_owned_by_other_namespace",
            )
        payload = existing.get("payload")
        text = str((payload or {}).get("text") or "").strip() if isinstance(payload, dict) else ""
        expected_turn_id = self._voice_projection_turn_id(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            voice_turn_id=normalized_voice_turn_id,
        )
        if (
            str(existing.get("kind") or "") != "message.user.voice"
            or not isinstance(payload, dict)
            or str(payload.get("voice_turn_id") or "").strip() != normalized_voice_turn_id
            or str(existing.get("turn_id") or "").strip() != expected_turn_id
            or not text
        ):
            return self._status(
                operation,
                False,
                "conflict",
                source_id=source_id,
                reason="voice_projection_turn_contract_mismatch",
            )
        return {
            **self._status(
                operation,
                True,
                "resolved",
                source_id=source_id,
                index_status=str(existing.get("index_status") or ""),
            ),
            "turn_id": expected_turn_id,
            "voice_turn_id": normalized_voice_turn_id,
            "text": text,
            "timestamp": int(existing.get("timestamp") or 0),
        }

    def import_legacy_message(
        self,
        record: dict[str, Any],
        *,
        role: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """One-time maintenance adapter; never use for a live model turn."""

        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"user", "assistant"}:
            return self._status(
                "import_legacy_message",
                False,
                "invalid_record",
                source_id=str((record or {}).get("source_id") or ""),
                reason="role_must_be_user_or_assistant",
            )
        return self._append_standalone_turn(
            operation="import_legacy_message",
            role=normalized_role,
            record=record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            legacy_import=True,
        )

    def begin_input_turn(
        self,
        record: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
        target_actor_id: str = "",
        target_actor_display_name: str = "",
        external_event: dict[str, Any] | None = None,
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Open one V2 model-response turn around a user or external stimulus."""

        operation = "begin_input_turn"
        source_id = str((record or {}).get("source_id") or "").strip()
        if not source_id:
            return self._status(operation, False, "invalid_record", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            opened_at = int(time.time())
            recover_stale = getattr(system, "recover_stale_open_turns", None)
            if callable(recover_stale):
                recovered = tuple(
                    recover_stale(
                        max_age_seconds=_STALE_OPEN_TURN_MAX_AGE_SECONDS,
                        now=opened_at,
                        reason="host_stale_open_turn_recovery",
                    )
                    or ()
                )
                if recovered:
                    logger.warning(
                        "memcore stale open turn recovery status=recovered count=%s",
                        len(recovered),
                    )
            entry = self._build_timeline_input(
                role="user",
                record=record,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
                turn_role="stimulus",
                external_event=external_event,
            )
            resolved_turn_id = str(turn_id or "").strip() or self._stable_turn_id(source_id)
            handle = system.begin_turn(
                stimuli=[entry],
                annotation_target_ids=[source_id],
                turn_id=resolved_turn_id,
                # Turn lifecycle time is host processing time, not the
                # stimulus publication time. External finance/news events may
                # arrive late and must not be born stale.
                opened_at=opened_at,
            )
            stored = handle.stimuli[0]
            writable = str(handle.status) == "open"
            return {
                **self._status(
                    operation,
                    True,
                    "opened" if writable else str(handle.status),
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": str(handle.turn_id),
                "writable": writable,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    def append_turn_intermediate(
        self,
        record: dict[str, Any],
        *,
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "append_turn_intermediate"
        source_id = str((record or {}).get("source_id") or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        if not source_id or not resolved_turn_id:
            return self._status(
                operation,
                False,
                "invalid_record",
                source_id=source_id,
                reason="source_id_and_turn_id_required",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            entry = self._build_timeline_input(
                role="assistant",
                record=record,
                turn_role="intermediate",
            )
            stored = system.append_entry(entry, turn_id=resolved_turn_id)
            return {
                **self._status(
                    operation,
                    True,
                    "recorded",
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    def inspect_turn_source(
        self,
        source_id: str,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Inspect one source id without projecting message content.

        Maintenance migrations use this to distinguish an idempotent rerun from
        a cross-namespace collision.  The public result deliberately omits raw
        content, actor identity, paths, and namespace identifiers.
        """

        operation = "inspect_turn_source"
        sid = str(source_id or "").strip()
        if not sid:
            return self._status(operation, False, "invalid_source", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._store is None:
            return self._status(operation, False, "unavailable", source_id=sid, reason=self._reason)
        try:
            record = self._store.get_record_by_source_id(sid)
            if record is None:
                return {
                    **self._status(operation, True, "missing", source_id=sid),
                    "exists": False,
                }
            namespace = getattr(system, "namespace", None)
            if namespace is None or not self._record_belongs_to_namespace(record, namespace):
                return {
                    **self._status(
                        operation,
                        False,
                        "conflict",
                        source_id=sid,
                        reason="source_id_owned_by_other_namespace",
                    ),
                    "exists": True,
                }
            return {
                **self._status(operation, True, "found", source_id=sid),
                "exists": True,
                "role": str(record.get("role") or ""),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return {
                **self._status(operation, False, "failed", source_id=sid, reason=reason),
                "exists": False,
            }

    def record_tool_batch(
        self,
        *,
        exchanges: list[dict[str, Any]],
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Append one logical parallel batch as all actions followed by all observations."""

        operation = "record_tool_batch"
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return {**self._status(operation, False, "invalid_record", reason="turn_id_required"), "exchanges": []}
        normalized = [dict(item) for item in exchanges if isinstance(item, dict) and item]
        if not normalized:
            return {**self._status(operation, False, "invalid_record", reason="exchanges_required"), "exchanges": []}
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {**self._status(operation, False, "unavailable", reason=self._reason), "exchanges": []}
        try:
            # Construct every exchange first: a construction-visible schema or
            # identity problem in any one of them must abort the whole batch
            # before a single append, so no action/observation half-record survives.
            prepared = [self._build_tool_entry_pair(item) for item in normalized]
            self._validate_tool_batch_prepared(prepared, operation=operation)
            stored_actions = [system.append_entry(action, turn_id=resolved_turn_id) for action, _ in prepared]
            stored_observations = [
                system.append_entry(observation, turn_id=resolved_turn_id) for _, observation in prepared
            ]
            recorded = [
                {
                    "correlation_id": action.correlation_id,
                    "tool_use_source_id": action.source_id,
                    "tool_result_source_id": observation.source_id,
                }
                for action, observation in zip(stored_actions, stored_observations)
            ]
            return {
                **self._status(operation, True, "recorded"),
                "turn_id": resolved_turn_id,
                "exchanges": recorded,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore tool batch record failed: %s", reason)
            return {**self._status(operation, False, "failed", reason=reason), "exchanges": []}

    def append_turn_media_input(
        self,
        *,
        items: list[dict[str, Any]],
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        related_source_ids: list[str] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Append a safe placeholder for provider-only media after a tool result.

        Raw bytes/data URLs deliberately never cross this boundary.  The actual
        multimodal blocks are frozen from the observed provider request, where
        MemCore replaces them with its persistent media-omitted marker.
        """

        operation = "append_turn_media_input"
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return self._status(operation, False, "invalid_record", reason="turn_id_required")
        safe_items: list[dict[str, str]] = []
        for index, raw in enumerate(list(items or [])[:5]):
            if not isinstance(raw, dict):
                continue
            attachment_id = self._safe_media_reference(raw.get("attachment_id"))
            handle = self._safe_media_reference(raw.get("attachment_handle"))
            mime_type = str(raw.get("mime_type") or raw.get("content_type") or "").strip().lower()
            if not re.fullmatch(r"image/[a-z0-9.+-]{1,80}", mime_type):
                mime_type = "image"
            safe_items.append(
                {
                    "attachment_id": attachment_id,
                    "attachment_handle": handle or f"image_{index + 1}",
                    "mime_type": mime_type,
                }
            )
        if not safe_items:
            return self._status(operation, False, "invalid_record", reason="media_items_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        related = sorted(
            {
                str(source_id or "").strip()
                for source_id in list(related_source_ids or [])
                if str(source_id or "").strip()
            }
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "turn_id": resolved_turn_id,
                    "items": safe_items,
                    "related_source_ids": related,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8", errors="ignore")
        ).hexdigest()[:32]
        source_id = f"media:{fingerprint}"
        labels = [item["attachment_handle"] for item in safe_items]
        try:
            memcore = self._memcore_module or self._import_memcore()
            entry = memcore.TimelineEntryInput(
                source_id=source_id,
                kind="material.model_input",
                origin=memcore.EntryOrigin.ENVIRONMENT,
                turn_role=memcore.TurnRole.INTERMEDIATE,
                semantic_text=f"工具为当前模型请求加载了图片：{', '.join(labels)}。",
                timestamp=int(timestamp or time.time()),
                payload={"items": safe_items},
                trace_metadata={"status": "ready", "media_count": len(safe_items)},
                memory_metadata={},
                annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                retrieval_policy=memcore.RetrievalPolicy.EXPLICIT,
                retrieval_visibility=memcore.RetrievalVisibility.EXPLICIT,
                semanticize=False,
                prompt_visible=True,
                compatibility_role="user.attachment image",
            )
            stored = system.append_entry(entry, turn_id=resolved_turn_id)
            return {
                **self._status(
                    operation,
                    True,
                    "recorded",
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": resolved_turn_id,
                "item_count": len(safe_items),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    @staticmethod
    def _safe_media_reference(value: Any) -> str:
        reference = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", reference):
            return ""
        return reference

    def record_material_reference(
        self,
        *,
        item: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return self._record_material_event(
            operation="record_material_reference",
            event_type="reference",
            item=item,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
        )

    def record_material_cleanup(
        self,
        *,
        item: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        return self._record_material_event(
            operation="record_material_cleanup",
            event_type="cleanup",
            item=item,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
            reason=reason,
            delete_storage=delete_storage,
        )

    def record_generated_workspace_cleanup(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        action: str,
        status: str,
        managed: list[dict[str, Any]] | None = None,
        failures: list[dict[str, Any]] | None = None,
        unresolved: list[str] | None = None,
        reason: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Append one safe fact for a channel/UI initiated generated-file cleanup.

        Native model tool calls already persist their tool result in the open
        turn and must not call this adapter.  This method exists for direct QQ
        commands and desktop panel actions that otherwise mutate the generated
        file store without leaving any newer evidence in the model timeline.
        """

        operation = "record_generated_workspace_cleanup"
        profile = str(profile_user_id or "").strip()
        session = str(session_id or "").strip()
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=str(character_pack_id or "").strip(),
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)

        normalized_action = self._kind_segment(action, fallback="archive")
        normalized_status = self._kind_segment(status, fallback="unknown")
        managed_items = [item for item in list(managed or []) if isinstance(item, dict)]
        failure_items = [item for item in list(failures or []) if isinstance(item, dict)]
        unresolved_items = [str(item or "").strip() for item in list(unresolved or []) if str(item or "").strip()]
        safe_files: list[str] = []
        for item in managed_items[:30]:
            handle = self._safe_task_event_text(
                item.get("generated_handle") or item.get("generated_id"),
                limit=120,
            )
            title = self._safe_task_event_text(item.get("output_title"), limit=180)
            if handle and title and title != handle:
                safe_files.append(f"{handle} ({title})")
            elif handle or title:
                safe_files.append(handle or title)

        safe_failures: list[str] = []
        for item in failure_items[:20]:
            target = self._safe_task_event_text(
                item.get("target") or item.get("generated_handle") or item.get("generated_id"),
                limit=120,
            )
            code = self._safe_task_event_text(item.get("code") or item.get("error"), limit=80)
            failure_reason = self._safe_task_event_text(item.get("reason"), limit=240)
            label = ": ".join(part for part in (target, code) if part)
            if failure_reason:
                label = f"{label} - {failure_reason}" if label else failure_reason
            if label:
                safe_failures.append(label)

        safe_unresolved = [
            text
            for item in unresolved_items[:20]
            if (text := self._safe_task_event_text(item, limit=120))
        ]
        safe_reason = self._safe_task_event_text(reason, limit=320)
        fields = {
            "action": normalized_action,
            "status": normalized_status,
            "managed_count": str(len(managed_items)),
            "failure_count": str(len(failure_items)),
            "unresolved_count": str(len(unresolved_items)),
            **({"files": "; ".join(safe_files)} if safe_files else {}),
            **({"failures": "; ".join(safe_failures)} if safe_failures else {}),
            **({"unresolved": ", ".join(safe_unresolved)} if safe_unresolved else {}),
            **({"reason": safe_reason} if safe_reason else {}),
        }
        effective_ts = int(timestamp or time.time())
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "profile_user_id": profile,
                    "session_id": session,
                    "character_pack_id": str(character_pack_id or "").strip(),
                    "timestamp": effective_ts,
                    "fields": fields,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        source_id = f"workspace:generated_cleanup:{effective_ts}:{fingerprint}"
        try:
            stored = system.record_external_event(
                event_type="workspace.generated_cleanup",
                fields=fields,
                source="workspace_management",
                timestamp=effective_ts,
                source_id=source_id,
                topic_terms=["工作台", "生成文件", "清理"],
            )
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(stored.get("source_id") or source_id),
                index_status=str(stored.get("index_status") or ""),
            )
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=failed_reason)

    def record_task_event(
        self,
        *,
        task: dict[str, Any],
        event: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "record_task_event"
        if not isinstance(task, dict) or not isinstance(event, dict):
            return self._status(operation, False, "invalid_record", reason="task_and_event_required")
        task_id = self._safe_task_event_text(task.get("task_id"), limit=120)
        event_id = self._safe_task_event_text(event.get("event_id"), limit=160)
        if not task_id or not event_id:
            return self._status(operation, False, "invalid_record", reason="task_id_and_event_id_required")
        profile = str(profile_user_id or task.get("profile_user_id") or event.get("profile_user_id") or "").strip()
        session = str(session_id or task.get("session_id") or event.get("session_id") or "").strip()
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=str(character_pack_id or "").strip(),
        )
        source_id = f"task:{event_id}"
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)

        event_type = str(event.get("event_type") or "updated").strip().lower()
        if event_type.startswith("task_"):
            event_type = event_type[5:]
        event_suffix = self._kind_suffix(event_type, fallback="updated")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
        pending = task.get("pending_question") if isinstance(task.get("pending_question"), dict) else {}
        raw_request = task.get("raw_request") if isinstance(task.get("raw_request"), dict) else {}
        fields: dict[str, Any] = {
            "task_id": task_id,
            "status": self._safe_task_event_text(task.get("status"), limit=40),
            "goal": self._safe_task_event_text(
                task.get("normalized_goal") or raw_request.get("text"),
                limit=320,
            ),
            "actor": self._safe_task_event_text(event.get("from_actor"), limit=80),
            "message": self._safe_task_event_text(event.get("message"), limit=500),
            "priority": self._safe_task_event_text(event.get("priority"), limit=24),
        }
        if bool(event.get("requires_user")):
            fields["requires_user"] = "true"
        question = self._safe_task_event_text(
            payload.get("question")
            or pending.get("text")
            or pending.get("question")
            or handoff.get("user_question"),
            limit=320,
        )
        if question:
            fields["question"] = question
        handoff_summary = self._safe_task_event_text(handoff.get("summary"), limit=500)
        if handoff_summary:
            fields["handoff_summary"] = handoff_summary
        artifact_handles = self._task_event_artifact_handles(task=task, payload=payload, handoff=handoff)
        if artifact_handles:
            fields["artifacts"] = ", ".join(artifact_handles)
        fields = {key: value for key, value in fields.items() if str(value or "").strip()}

        try:
            stored = system.record_external_event(
                event_type=f"task.{event_suffix}",
                fields=fields,
                source="task_workspace",
                timestamp=int(event.get("created_at") or task.get("updated_at") or time.time()),
                source_id=source_id,
                topic_terms=["任务", event_suffix],
            )
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(stored.get("source_id") or source_id),
                index_status=str(stored.get("index_status") or ""),
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    def acquaintance_note(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        now_ts: int | None = None,
    ) -> str:
        """memcore 相处时间感提示（认识第N天等），无历史返回空字符串。"""
        system = self._get_system_or_none(
            operation="acquaintance_note",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return ""
        try:
            return str(system.acquaintance_note(now_ts=now_ts) or "")
        except Exception as exc:
            logger.debug("memcore acquaintance_note failed: %s", exc)
            return ""

    def stage_turn_metadata(
        self,
        source_id: str,
        memory_metadata: dict[str, Any] | None,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
    ) -> dict[str, Any]:
        operation = "stage_turn_metadata"
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        sid = str(source_id or "").strip()
        if system is None:
            return self._status(operation, False, "unavailable", source_id=sid, reason=self._reason)
        try:
            actor = self._build_actor(actor_stable_id, actor_display_name)
            result = system.stage_turn_metadata(
                sid,
                memory_metadata if isinstance(memory_metadata, dict) else {},
                actor=actor,
            )
            return dict(result, operation=operation)
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore metadata staging failed: %s", reason)
            return self._status(operation, False, "failed", source_id=sid, reason=reason)

    def complete_input_turn(
        self,
        *,
        turn_id: str,
        assistant_record: dict[str, Any],
        memory_metadata: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        provider_output_raw: str = "",
        provider_profile: str = "",
        provider_projection: dict[str, Any] | None = None,
        annotation_status: str = "accepted_model",
    ) -> dict[str, Any]:
        operation = "complete_input_turn"
        resolved_turn_id = str(turn_id or "").strip()
        source_id = str((assistant_record or {}).get("source_id") or "").strip()
        if not resolved_turn_id or not source_id:
            return self._status(
                operation,
                False,
                "invalid_record",
                source_id=source_id,
                reason="turn_id_and_source_id_required",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            resolved_provider_profile = (
                resolve_memcore_provider_profile(provider_profile)
                if isinstance(provider_projection, dict)
                else ""
            )
            result = system.complete_turn(
                turn_id=resolved_turn_id,
                semantic_text=str((assistant_record or {}).get("content") or ""),
                provider_output_raw=str(provider_output_raw or ""),
                memory_annotation=memory_metadata if isinstance(memory_metadata, dict) else None,
                annotation_status=str(annotation_status or "missing"),
                timestamp=int((assistant_record or {}).get("timestamp") or time.time()),
                source_id=source_id,
                payload={
                    "semantic_tags": list((assistant_record or {}).get("semantic_tags") or []),
                },
                provider_profile=resolved_provider_profile,
                provider_projection=(
                    dict(provider_projection)
                    if resolved_provider_profile and isinstance(provider_projection, dict)
                    else None
                ),
            )
            final_entry = getattr(result, "final_entry", None)
            result_status = str(getattr(result, "status", "") or "")
            return {
                **self._status(
                    operation,
                    bool(getattr(result, "completed", False)) or result_status == "already_completed",
                    result_status or "failed",
                    source_id=str(getattr(final_entry, "source_id", "") or source_id),
                    index_status=str(getattr(final_entry, "index_status", "") or ""),
                    reason=str(getattr(result, "reason", "") or ""),
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    def abort_input_turn(
        self,
        *,
        turn_id: str,
        reason: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "abort_input_turn"
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return self._status(operation, False, "invalid_record", reason="turn_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        try:
            result = system.abort_turn(resolved_turn_id, reason=str(reason or "aborted"))
            return {
                **self._status(
                    operation,
                    str(getattr(result, "status", "")) in {"aborted", "already_aborted"},
                    str(getattr(result, "status", "") or "failed"),
                    reason=str(getattr(result, "reason", "") or ""),
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", reason=failed_reason)

    def compact_due_background(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        provider_profile: str = "",
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

    def compact_due_sync(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        provider_profile: str = "",
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
        try:
            stats = dict(system.compact_due_sync(provider_profile=resolved_profile))
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

    def import_legacy_raw_messages(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        operation = "import_legacy_raw_messages"
        if not self.enabled:
            return {
                **self._status(operation, False, "disabled", reason="memory_backend_legacy"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
            }
        if not self.available:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
            }
        iterator = getattr(legacy_store, "iter_messages_for_vector_reindex", None)
        if not callable(iterator):
            return {
                **self._status(operation, False, "invalid_store", reason="iter_messages_for_vector_reindex_required"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
            }

        profile_filter = str(profile_user_id or "").strip()
        character_filter = None if character_pack_id is None else str(character_pack_id or "").strip()
        max_records = self._coerce_positive_int_or_none(limit)
        scanned = upserted = filtered = skipped = failed = 0
        try:
            batches = iterator(batch_size=max(1, int(batch_size or 64)))
            for batch in batches:
                for record in list(batch or []):
                    if max_records is not None and scanned >= max_records:
                        return self._import_result(operation, scanned, upserted, filtered, skipped, failed)
                    if not isinstance(record, dict):
                        skipped += 1
                        continue
                    scanned += 1
                    record_profile = str(record.get("profile_user_id") or "").strip()
                    record_character = str(record.get("character_pack_id") or "").strip()
                    if profile_filter and record_profile != profile_filter:
                        filtered += 1
                        continue
                    if character_filter is not None and record_character != character_filter:
                        filtered += 1
                        continue
                    role = str(record.get("role") or "").strip().lower()
                    if role not in {"user", "assistant"}:
                        skipped += 1
                        continue
                    result = self._append_standalone_turn(
                        operation=operation,
                        role=role,
                        record=record,
                        profile_user_id=record_profile,
                        session_id=str(record.get("session_id") or "").strip(),
                        character_pack_id=record_character,
                        legacy_import=True,
                    )
                    if bool(result.get("ok")):
                        upserted += 1
                    else:
                        failed += 1
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore legacy raw import failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "scanned": scanned,
                "upserted": upserted,
                "filtered": filtered,
                "skipped": skipped,
                "failed": failed + 1,
            }
        return self._import_result(operation, scanned, upserted, filtered, skipped, failed)

    def import_legacy_long_term_memory(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Import legacy episodic and semantic summaries into memcore.

        Raw backfill alone can leave Akane feeling amnesic until memcore has
        recompressed old history. This maintenance entry carries over the
        legacy long-term layers directly, preserving old distilled memories.
        """

        operation = "import_legacy_long_term_memory"
        summaries = self.import_legacy_summaries(
            legacy_store=legacy_store,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )
        semantic = self.import_legacy_semantic_summaries(
            legacy_store=legacy_store,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )
        ok = bool(summaries.get("ok")) and bool(semantic.get("ok"))
        status = "completed" if ok else "partial"
        reason = "; ".join(
            part
            for part in (
                str(summaries.get("reason") or ""),
                str(semantic.get("reason") or ""),
            )
            if part
        )
        return {
            **self._status(operation, ok, status, reason=reason),
            "summaries": summaries,
            "semantic_summaries": semantic,
            "scanned": int(summaries.get("scanned") or 0) + int(semantic.get("scanned") or 0),
            "upserted": int(summaries.get("upserted") or 0) + int(semantic.get("upserted") or 0),
            "filtered": int(summaries.get("filtered") or 0) + int(semantic.get("filtered") or 0),
            "skipped": int(summaries.get("skipped") or 0) + int(semantic.get("skipped") or 0),
            "failed": int(summaries.get("failed") or 0) + int(semantic.get("failed") or 0),
            "conflicted": int(summaries.get("conflicted") or 0) + int(semantic.get("conflicted") or 0),
        }

    def import_legacy_summaries(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self._import_legacy_layer_records(
            operation="import_legacy_summaries",
            legacy_store=legacy_store,
            iterator_name="iter_summaries_for_vector_reindex",
            entry_type="summary",
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )

    def import_legacy_semantic_summaries(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self._import_legacy_layer_records(
            operation="import_legacy_semantic_summaries",
            legacy_store=legacy_store,
            iterator_name="iter_semantic_summaries_for_vector_reindex",
            entry_type="semantic_summary",
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )

    def _import_legacy_layer_records(
        self,
        *,
        operation: str,
        legacy_store: Any,
        iterator_name: str,
        entry_type: str,
        profile_user_id: str,
        character_pack_id: str | None,
        batch_size: int,
        limit: int | None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                **self._status(operation, False, "disabled", reason="memory_backend_legacy"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }
        if not self.available:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }
        iterator = getattr(legacy_store, iterator_name, None)
        if not callable(iterator):
            return {
                **self._status(operation, False, "invalid_store", reason=f"{iterator_name}_required"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }
        if self._store is None:
            return {
                **self._status(operation, False, "unavailable", reason="memcore_store_not_ready"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }

        profile_filter = str(profile_user_id or "").strip()
        character_filter = None if character_pack_id is None else str(character_pack_id or "").strip()
        max_records = self._coerce_positive_int_or_none(limit)
        scanned = upserted = filtered = skipped = failed = conflicted = 0
        try:
            batches = iterator(batch_size=max(1, int(batch_size or 64)))
            for batch in batches:
                for record in list(batch or []):
                    if max_records is not None and scanned >= max_records:
                        return self._import_layer_result(
                            operation, scanned, upserted, filtered, skipped, failed, conflicted
                        )
                    if not isinstance(record, dict):
                        skipped += 1
                        continue
                    scanned += 1
                    record_profile = str(record.get("profile_user_id") or "").strip()
                    record_character = str(record.get("character_pack_id") or "").strip()
                    if profile_filter and record_profile != profile_filter:
                        filtered += 1
                        continue
                    if character_filter is not None and record_character != character_filter:
                        filtered += 1
                        continue
                    result = self._import_one_legacy_layer_record(
                        operation=operation,
                        entry_type=entry_type,
                        record=record,
                        profile_user_id=record_profile,
                        session_id=str(record.get("session_id") or "").strip(),
                        character_pack_id=record_character,
                    )
                    status = str(result.get("status") or "")
                    if bool(result.get("ok")):
                        if status == "skipped":
                            skipped += 1
                        else:
                            upserted += 1
                    elif status == "conflicted":
                        conflicted += 1
                        failed += 1
                    else:
                        failed += 1
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore legacy layer import failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "scanned": scanned,
                "upserted": upserted,
                "filtered": filtered,
                "skipped": skipped,
                "failed": failed + 1,
                "conflicted": conflicted,
            }
        return self._import_layer_result(operation, scanned, upserted, filtered, skipped, failed, conflicted)

    def _import_one_legacy_layer_record(
        self,
        *,
        operation: str,
        entry_type: str,
        record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._store is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        if entry_type == "semantic_summary":
            record_id = str(record.get("semantic_id") or "").strip()
            text = str(record.get("semantic_summary") or "").strip()
            write = self._store.add_semantic_summary
        else:
            record_id = str(record.get("summary_id") or "").strip()
            text = str(record.get("diary_summary") or "").strip()
            write = self._store.add_summary
        if not record_id:
            return self._status(operation, False, "invalid_record", reason="id_required")
        if not text:
            return self._status(operation, True, "skipped", source_id=record_id, reason="empty_text")

        existing = self._store.get_record_by_source_id(record_id)
        if existing is not None:
            if self._record_belongs_to_namespace(existing, system.namespace):
                return self._status(operation, True, "skipped", source_id=record_id, reason="already_imported")
            return self._status(
                operation,
                False,
                "conflicted",
                source_id=record_id,
                reason="source_id_belongs_to_different_namespace",
            )

        payload = dict(record)
        payload["memory_metadata"] = self._legacy_import_metadata(payload.get("memory_metadata"))
        saved = write(namespace=system.namespace, record=payload)
        if entry_type == "summary" and int(record.get("is_semanticized") or 0):
            semantic_id = str(record.get("semantic_id") or "").strip()
            if semantic_id:
                self._store.mark_summaries_semanticized([record_id], semantic_id)
                saved["is_semanticized"] = 1
                saved["semantic_id"] = semantic_id
        try:
            system._reindex_record(saved)
            index_status = "indexed"
        except Exception as exc:
            index_status = "pending"
            logger.warning(
                "memcore legacy layer index pending for %s: %s",
                record_id,
                str(exc) or exc.__class__.__name__,
            )
        return self._status(operation, True, "recorded", source_id=record_id, index_status=index_status)

    def build_context_projection(
        self,
        *,
        provider_profile: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "build_context_projection"
        profile = resolve_memcore_provider_profile(provider_profile)
        if not profile:
            return {
                **self._status(operation, False, "invalid_provider_profile", reason="provider_profile_unsupported"),
                "provider_profile": "",
                "messages": [],
                "payloads": [],
                "source_ids": [],
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
            }
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "provider_profile": profile,
                "messages": [],
                "payloads": [],
                "source_ids": [],
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
            }
        try:
            projection = system.build_context_projection(provider_profile=profile)
            messages = [
                {
                    "turn_id": str(message.turn_id or ""),
                    "payload": dict(message.payload),
                    "source_ids": list(message.source_ids),
                    "payload_hash": str(message.payload_hash or ""),
                    "projection_status": str(message.projection_status),
                    "projection_index": int(message.projection_index),
                    "projection_version": int(message.projection_version),
                }
                for message in projection.messages
            ]
            source_ids = list(
                dict.fromkeys(source_id for message in projection.messages for source_id in message.source_ids)
            )
            return {
                **self._status(operation, True, "ok"),
                "provider_profile": str(projection.provider_profile),
                "messages": messages,
                "payloads": [dict(payload) for payload in projection.payloads],
                "source_ids": source_ids,
                "message_count": len(messages),
                "source_count": len(source_ids),
                "stable_prefix_hash": str(projection.stable_prefix_hash or ""),
                "projection_version": int(projection.projection_version),
                "compaction_generation": int(projection.compaction_generation),
                "projection_generation": int(projection.projection_generation),
                "has_compact_history": bool(getattr(projection, "has_compact_history", False)),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore context projection failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "provider_profile": profile,
                "messages": [],
                "payloads": [],
                "source_ids": [],
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
            }

    def build_context_surface(
        self,
        *,
        provider_profile: str,
        current_source_id: str = "",
        active_turn_messages: list[dict[str, Any]] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Expose the same stable Context Contract used by external hosts."""

        operation = "build_context_surface"
        profile = resolve_memcore_provider_profile(provider_profile)
        if not profile:
            return {
                **self._status(operation, False, "invalid_provider_profile", reason="provider_profile_unsupported"),
                "version": "context_surface_v1",
                "provider_profile": "",
                "history_messages": [],
                "current_message": None,
                "active_turn_messages": [],
                "projection_hash": "",
                "projection_generation": 0,
                "diagnostics": [],
            }
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "version": "context_surface_v1",
                "provider_profile": profile,
                "history_messages": [],
                "current_message": None,
                "active_turn_messages": [],
                "projection_hash": "",
                "projection_generation": 0,
                "diagnostics": [],
            }
        try:
            surface = system.build_context_surface(
                session_id=session_id,
                provider_profile=profile,
                current_source_id=str(current_source_id or "").strip() or None,
                active_turn_messages=tuple(active_turn_messages or ()),
            )
            return {
                **self._status(operation, True, "ok"),
                **surface.as_dict(),
                "messages": [dict(message) for message in surface.messages],
            }
        except Exception:
            logger.warning("memcore context surface failed error_type=%s", "surface_build")
            return {
                **self._status(operation, False, "failed", reason="context_surface_build_failed"),
                "version": "context_surface_v1",
                "provider_profile": profile,
                "history_messages": [],
                "current_message": None,
                "active_turn_messages": [],
                "projection_hash": "",
                "projection_generation": 0,
                "diagnostics": [],
            }

    def run_legacy_path_projection_migration(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Explicit pre-traffic maintenance: migrate legacy path-damage projections.

        Iterates every namespace with frozen projections in the store and runs
        the idempotent V3 migration (re-projection + settlement rebuild).  This
        must run during a release maintenance phase or before the service takes
        traffic, never on the first user request.  Returns per-namespace reports
        and aggregate counts without any payload text.
        """

        operation = "run_legacy_path_projection_migration"
        if not self.available or self._store is None or self._memcore_module is None:
            return self._status(operation, False, "unavailable", reason=self._reason or "memcore_not_ready")
        try:
            list_namespaces = getattr(self._store, "list_projection_namespaces", None)
            if not callable(list_namespaces):
                return self._status(operation, False, "unsupported", reason="store_namespace_listing_unsupported")
            namespaces = list_namespaces()
        except Exception as exc:
            return self._status(
                operation,
                False,
                "failed",
                reason=f"namespace_listing_failed:{type(exc).__name__}",
            )
        from memcore.projection import ProjectionAdapter, default_renderer_registry
        from memcore.projection_migration import migrate_legacy_path_projections

        timezone = str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        registry = default_renderer_registry()
        token_counter = self._token_counter
        count_text = getattr(token_counter, "count_text", None) if token_counter is not None else None
        totals: dict[str, int] = {}
        reports: list[dict[str, Any]] = []
        for tenant_id, user_id, domain_id, conversation_id in namespaces:
            try:
                namespace = self._memcore_module.Namespace(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    domain_id=domain_id,
                    conversation_id=conversation_id,
                )
                adapter = ProjectionAdapter(renderer_registry=registry, timezone=timezone)
                report = migrate_legacy_path_projections(
                    store=self._store,
                    adapter=adapter,
                    namespace=namespace,
                    dry_run=bool(dry_run),
                    count_text=count_text if callable(count_text) else None,
                )
            except Exception as exc:
                report = {"status": "failed", "reason": f"{type(exc).__name__}"}
            reports.append(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "domain_id": domain_id,
                    "conversation_id": conversation_id,
                    "report": report,
                }
            )
            for key in (
                "scanned_memcore_marker_rows",
                "scanned_host_local_path_rows",
                "scanned_host_tmpdir_rows",
                "migrated",
                "version_advanced_only",
                "preserved_irrecoverable_host_redaction",
                "preserved_without_raw_source",
                "settled_rebuilt",
                "settled_rebuilt_noop",
                "settled_rebuilt_fallback",
                "settled_rebuild_failed_dropped",
            ):
                if isinstance(report, dict):
                    totals[key] = totals.get(key, 0) + int(report.get(key) or 0)
        failed_namespaces = sum(1 for item in reports if str(item["report"].get("status") or "") == "failed")
        result = {
            **self._status(
                operation,
                failed_namespaces == 0,
                "ok" if failed_namespaces == 0 else "partial",
            ),
            "dry_run": bool(dry_run),
            "namespace_count": len(namespaces),
            "failed_namespace_count": failed_namespaces,
            "totals": totals,
            "reports": reports,
        }
        logger.info(
            "memcore legacy path projection migration done: namespaces=%s failed=%s dry_run=%s totals=%s",
            len(namespaces),
            failed_namespaces,
            bool(dry_run),
            json.dumps(totals, ensure_ascii=False, sort_keys=True),
        )
        return result

    def record_request_projection(
        self,
        *,
        turn_id: str,
        provider_profile: str,
        turn_messages: list[dict[str, Any]],
        history_messages: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        audit_history_messages: list[dict[str, Any]] | None = None,
        attempt: int = 0,
        model_route: Any = "",
        system_prefix: Any = "",
        tool_schema: Any = (),
        created_at: int | None = None,
    ) -> dict[str, Any]:
        operation = "record_request_projection"
        profile = resolve_memcore_provider_profile(provider_profile)
        if not profile:
            return self._status(
                operation,
                False,
                "invalid_provider_profile",
                reason="provider_profile_unsupported",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        try:
            projection_input = self._memcore_module.ProjectionMessageInput
            prepared = [
                projection_input(
                    provider_profile=profile,
                    payload=dict(message.get("payload") or {}),
                    source_ids=tuple(message.get("source_ids") or ()),
                    projection_index=int(message.get("projection_index", -1)),
                    projection_status=message.get("projection_status") or "complete",
                    projection_version=int(message.get("projection_version") or 1),
                )
                for message in list(turn_messages or [])
                if isinstance(message, dict)
            ]
            if len(prepared) != len(turn_messages or []):
                return self._status(operation, False, "invalid_request", reason="turn_messages_must_be_objects")
            result = system.record_request_projection(
                turn_id=str(turn_id or "").strip(),
                provider_profile=profile,
                turn_messages=prepared,
                history_messages=[dict(message) for message in list(history_messages or [])],
                audit_history_messages=(
                    [dict(message) for message in list(audit_history_messages or [])]
                    if audit_history_messages is not None
                    else None
                ),
                attempt=int(attempt),
                model_route=model_route,
                system_prefix=system_prefix,
                tool_schema=tool_schema,
                created_at=created_at,
            )
            audit = result.audit
            return {
                **self._status(operation, True, "recorded"),
                "turn_id": str(audit.turn_id),
                "attempt": int(audit.attempt),
                "provider_profile": str(audit.provider_profile),
                "projection_count": len(result.projections),
                "source_ids": list(
                    dict.fromkeys(source_id for item in result.projections for source_id in item.source_ids)
                ),
                "projection_hashes": [str(item.payload_hash or "") for item in result.projections],
                "model_route_hash": str(audit.model_route_hash or ""),
                "system_prefix_hash": str(audit.system_prefix_hash or ""),
                "tool_schema_hash": str(audit.tool_schema_hash or ""),
                "history_hash": str(audit.history_hash or ""),
                "full_prefix_hash": str(audit.full_prefix_hash or ""),
                "projection_version": int(audit.projection_version),
                "media_omitted": bool(audit.media_omitted),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore request projection failed: %s", reason)
            return self._status(operation, False, "failed", reason=reason)

    def compare_context_projection(
        self,
        *,
        provider_profile: str,
        actual_history_messages: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        exclude_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        operation = "compare_context_projection"
        projection = self.build_context_projection(
            provider_profile=provider_profile,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if not projection.get("ok"):
            return {
                **self._status(
                    operation,
                    False,
                    str(projection.get("status") or "failed"),
                    reason="projection_unavailable",
                ),
                "provider_profile": str(projection.get("provider_profile") or ""),
                "projection_hash": "",
                "actual_history_hash": "",
                "strict_prefix": False,
                "first_divergence_index": -1,
                "divergence_reason": "projection_unavailable",
                "source_ids": [],
            }
        try:
            excluded = {
                str(source_id or "").strip()
                for source_id in list(exclude_source_ids or [])
                if str(source_id or "").strip()
            }
            projected_messages = [
                dict(message.get("payload") or {})
                for message in list(projection.get("messages") or [])
                if not excluded.intersection(str(item or "") for item in list(message.get("source_ids") or []))
            ]
            projected_source_ids = list(
                dict.fromkeys(
                    str(source_id or "")
                    for message in list(projection.get("messages") or [])
                    if not excluded.intersection(str(item or "") for item in list(message.get("source_ids") or []))
                    for source_id in list(message.get("source_ids") or [])
                    if str(source_id or "")
                )
            )
            actual = [dict(message) for message in list(actual_history_messages or [])]
            stable_hash = self._memcore_module.stable_projection_hash
            canonical_bytes = self._memcore_module.canonical_json_bytes
            strict_prefix = bool(self._memcore_module.is_strict_message_prefix(projected_messages, actual))
            divergence_index = -1
            divergence_reason = ""
            for index, (expected, observed) in enumerate(zip(projected_messages, actual)):
                if canonical_bytes(expected) != canonical_bytes(observed):
                    divergence_index = index
                    divergence_reason = "message_mismatch"
                    break
            if divergence_index < 0 and len(projected_messages) > len(actual):
                divergence_index = len(actual)
                divergence_reason = "actual_history_missing_projection_suffix"
            elif divergence_index < 0 and len(projected_messages) < len(actual):
                divergence_index = len(projected_messages)
                divergence_reason = "actual_history_has_extra_suffix"
            return {
                **self._status(operation, True, "match" if strict_prefix else "diverged"),
                "provider_profile": str(projection.get("provider_profile") or ""),
                "projection_hash": str(stable_hash(projected_messages)),
                "actual_history_hash": str(stable_hash(actual)),
                "strict_prefix": strict_prefix,
                "first_divergence_index": divergence_index,
                "divergence_reason": divergence_reason,
                "projection_message_count": len(projected_messages),
                "actual_history_message_count": len(actual),
                "source_ids": projected_source_ids,
                "source_count": len(projected_source_ids),
                "compaction_generation": int(projection.get("compaction_generation") or 0),
                "projection_generation": int(projection.get("projection_generation") or 0),
            }
        except Exception as exc:
            logger.warning("memcore projection shadow comparison failed: %s", exc.__class__.__name__)
            return {
                **self._status(operation, False, "failed", reason="comparison_failed"),
                "provider_profile": str(projection.get("provider_profile") or ""),
                "projection_hash": "",
                "actual_history_hash": "",
                "strict_prefix": False,
                "first_divergence_index": -1,
                "divergence_reason": "comparison_failed",
                "source_ids": [],
            }

    def read_memory_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        time_range: dict[str, Any] | None = None,
        date_from: str = "",
        date_to: str = "",
        time_periods: list[str] | None = None,
        anchor_source_id: str = "",
        before_turns: int = 0,
        after_turns: int = 0,
        cross_conversation: bool = False,
        projection: str = "conversation",
        page_token_budget: int = 0,
        cursor: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="read_memory_timeline",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status("read_memory_timeline", False, "unavailable", reason=self._reason),
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": [],
                "anchor_source_id": str(anchor_source_id or ""),
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "projection": str(projection or "conversation"),
                "coverage": {},
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }

        try:
            native_arguments = dict(arguments) if isinstance(arguments, dict) else {}
            if arguments is None:
                resolved_cursor = str(cursor or "").strip()
                if resolved_cursor:
                    native_arguments["cursor"] = resolved_cursor
                else:
                    if time_range:
                        native_arguments["time_range"] = dict(time_range)
                    if str(date_from or "").strip():
                        native_arguments["date_from"] = str(date_from).strip()
                    if str(date_to or "").strip():
                        native_arguments["date_to"] = str(date_to).strip()
                    if time_periods:
                        native_arguments["time_periods"] = list(time_periods)
                    if str(anchor_source_id or "").strip():
                        native_arguments["anchor_source_id"] = str(anchor_source_id).strip()
                    if int(before_turns or 0):
                        native_arguments["before_turns"] = int(before_turns)
                    if int(after_turns or 0):
                        native_arguments["after_turns"] = int(after_turns)
                    if bool(cross_conversation):
                        native_arguments["cross_conversation"] = True
                    if str(projection or "conversation") != "conversation":
                        native_arguments["projection"] = str(projection)
                    if int(page_token_budget or 0) > 0:
                        native_arguments["page_token_budget"] = int(page_token_budget)
            result = self._memcore_module.dispatch_native_memory_tool(
                "read_timeline",
                native_arguments,
                mem=system,
            )
            return self._project_native_memory_dispatch(
                operation="read_memory_timeline",
                dispatched=result,
            )
        except Exception as exc:
            reason = f"exception_{type(exc).__name__}"
            logger.warning("memcore timeline read failed: %s", type(exc).__name__)
            return {
                **self._status("read_memory_timeline", False, "failed", reason=reason),
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": [],
                "anchor_source_id": str(anchor_source_id or ""),
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "projection": str(projection or "conversation"),
                "coverage": {},
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }

    def browse_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="browse_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._memcore_module is None:
            return {
                **self._status("browse_memory", False, "unavailable", reason=self._reason),
                "backend": "memcore",
            }
        try:
            dispatched = self._memcore_module.dispatch_native_memory_tool(
                "browse_memory",
                dict(arguments or {}),
                mem=system,
            )
            return self._project_native_memory_dispatch(
                operation="browse_memory",
                dispatched=dispatched,
            )
        except Exception as exc:
            reason = f"exception_{type(exc).__name__}"
            logger.warning("memcore catalog browse failed: %s", type(exc).__name__)
            return {
                **self._status("browse_memory", False, "failed", reason=reason),
                "backend": "memcore",
            }

    def open_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="open_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._memcore_module is None:
            return {
                **self._status("open_memory", False, "unavailable", reason=self._reason),
                "backend": "memcore",
            }
        try:
            dispatched = self._memcore_module.dispatch_native_memory_tool(
                "open_memory",
                dict(arguments or {}),
                mem=system,
            )
            return self._project_native_memory_dispatch(
                operation="open_memory",
                dispatched=dispatched,
            )
        except Exception as exc:
            reason = f"exception_{type(exc).__name__}"
            logger.warning("memcore open-memory read failed: %s", type(exc).__name__)
            return {
                **self._status("open_memory", False, "failed", reason=reason),
                "backend": "memcore",
            }

    def shadow_retrieve_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        current_user_record: dict[str, Any] | None = None,
        query: str,
        entity_anchors: list[str] | None = None,
        topic_terms: list[str] | None = None,
        time_hint: dict[str, Any] | None = None,
        source_layers: list[str] | None = None,
        memory_facets: list[str] | None = None,
        about_roles: list[str] | None = None,
        within_memory_id: str = "",
        exclude_source_ids: list[str] | None = None,
        include_explicit: bool = False,
        kind_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        shadow_enabled = bool(getattr(config, "MEMCORE_SHADOW_COMPARE", self.shadow_compare))
        if not shadow_enabled:
            return self._status("shadow_retrieve_memory", True, "disabled", reason="shadow_compare_disabled")
        result = self._retrieve_memory(
            operation="shadow_retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            within_memory_id=within_memory_id,
            exclude_source_ids=exclude_source_ids,
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
            include_snippets=False,
        )
        if "snippets" in result:
            result = dict(result)
            result.pop("snippets", None)
        return result

    def retrieve_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        current_user_record: dict[str, Any] | None = None,
        query: str,
        entity_anchors: list[str] | None = None,
        topic_terms: list[str] | None = None,
        time_hint: dict[str, Any] | None = None,
        source_layers: list[str] | None = None,
        memory_facets: list[str] | None = None,
        about_roles: list[str] | None = None,
        within_memory_id: str = "",
        exclude_source_ids: list[str] | None = None,
        include_explicit: bool = False,
        kind_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._retrieve_memory(
            operation="retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            within_memory_id=within_memory_id,
            exclude_source_ids=exclude_source_ids,
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
            include_snippets=True,
        )

    def _retrieve_memory(
        self,
        *,
        operation: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        current_user_record: dict[str, Any] | None,
        query: str,
        entity_anchors: list[str] | None,
        topic_terms: list[str] | None,
        time_hint: dict[str, Any] | None,
        source_layers: list[str] | None,
        memory_facets: list[str] | None,
        about_roles: list[str] | None,
        within_memory_id: str,
        exclude_source_ids: list[str] | None,
        include_explicit: bool,
        kind_patterns: list[str] | None,
        include_snippets: bool,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                **({"snippets": []} if include_snippets else {}),
            }

        start = time.perf_counter()
        current = dict(current_user_record or {})
        if not str(current.get("source_id") or "").strip() and exclude_source_ids:
            current["source_id"] = str(exclude_source_ids[0] or "").strip()
        if not int(current.get("timestamp") or 0):
            current["timestamp"] = int(time.time())
        explicit_patterns, explicit_error = self._authorize_explicit_kind_patterns(
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
        )
        if explicit_error:
            status, reason = explicit_error
            return {
                **self._status(operation, False, status, reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                **({"snippets": []} if include_snippets else {}),
            }
        try:
            retrieval = system.retrieve_for_turn_structured(
                current=current,
                query=str(query or ""),
                entity_anchors=[str(item).strip() for item in (entity_anchors or []) if str(item).strip()],
                topic_terms=[str(item).strip() for item in (topic_terms or []) if str(item).strip()],
                time_hint=time_hint if isinstance(time_hint, dict) else None,
                source_layers=[str(item).strip() for item in (source_layers or []) if str(item).strip()],
                memory_facets=[str(item).strip() for item in (memory_facets or []) if str(item).strip()],
                about_roles=[str(item).strip() for item in (about_roles or []) if str(item).strip()],
                within_memory_id=str(within_memory_id or "").strip(),
                exclude_source_ids=[str(item).strip() for item in (exclude_source_ids or []) if str(item).strip()],
                include_explicit=bool(include_explicit),
                kind_patterns=explicit_patterns,
                cross_conversation=True,
            )
            retrieval_status = str(getattr(retrieval, "status", "failed") or "failed")
            if retrieval_status not in {"found", "empty"}:
                return {
                    **self._status(
                        operation,
                        False,
                        retrieval_status,
                        reason=str(getattr(retrieval, "reason", "") or "retrieval_failed"),
                    ),
                    "snippet_count": 0,
                    "snippet_hashes": [],
                    **({"snippets": []} if include_snippets else {}),
                }
            snippets = list(getattr(retrieval, "rendered_texts", ()) or ())
            structured = retrieval.to_dict() if hasattr(retrieval, "to_dict") else {}
            payload = {
                **self._status(operation, True, "ok"),
                "retrieval_status": retrieval_status,
                "snippet_count": len(snippets),
                "snippet_hashes": snippet_hashes(snippets),
                "navigation": list(structured.get("navigation") or []),
                "suggested_next_actions": list(structured.get("suggested_next_actions") or []),
                "effective_filters": dict(structured.get("effective_filters") or {}),
                "candidate_counts": dict(structured.get("candidate_counts") or {}),
                "lineage_scope": dict(structured.get("lineage_scope") or {}),
                "entity_filter_relaxed": bool(structured.get("entity_filter_relaxed")),
                "relaxation_steps": list(structured.get("relaxation_steps") or []),
                "rejected_counts": dict(structured.get("rejected_counts") or {}),
                "returned_match_count": len(list(structured.get("matches") or [])),
                "token_usage": int(structured.get("token_usage") or 0),
                "truncated": bool(structured.get("truncated")),
                "omitted_match_count": int(structured.get("omitted_match_count") or 0),
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
            }
            if include_snippets:
                payload["snippets"] = snippets
                payload["matches"] = list(structured.get("matches") or [])
                if self._memcore_module is not None:
                    receipt_arguments = {
                        "query": str(query or ""),
                        "entity_anchors": [str(item).strip() for item in (entity_anchors or []) if str(item).strip()],
                        "topic_terms": [str(item).strip() for item in (topic_terms or []) if str(item).strip()],
                        "source_layers": [str(item).strip() for item in (source_layers or []) if str(item).strip()],
                        "memory_facets": [str(item).strip() for item in (memory_facets or []) if str(item).strip()],
                        "about_roles": [str(item).strip() for item in (about_roles or []) if str(item).strip()],
                        "time_hint": dict(time_hint or {}) if isinstance(time_hint, dict) else {},
                        "within_memory_id": str(within_memory_id or "").strip(),
                        "include_explicit": bool(include_explicit),
                        "kind_patterns": list(explicit_patterns),
                    }
                    payload["receipt"] = self._memcore_module.build_memory_operation_receipt(
                        "retrieve_for_turn",
                        receipt_arguments,
                        {
                            "ok": True,
                            "status": "ok",
                            "result": {**structured, "snippets": snippets},
                        },
                    )
            return payload
        except ValueError as exc:
            reason = str(exc) or "invalid_filter"
            return {
                **self._status(operation, False, "invalid_filter", reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
                **({"snippets": []} if include_snippets else {}),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
                **({"snippets": []} if include_snippets else {}),
            }

    @staticmethod
    def _authorize_explicit_kind_patterns(
        *,
        include_explicit: bool,
        kind_patterns: list[str] | None,
    ) -> tuple[list[str], tuple[str, str] | None]:
        patterns = [str(item or "").strip().lower() for item in list(kind_patterns or []) if str(item or "").strip()]
        if not include_explicit:
            if patterns:
                return [], ("invalid_request", "kind_patterns_require_include_explicit")
            return [], None
        if not patterns:
            return [], ("invalid_request", "explicit_kind_patterns_required")
        normalized: list[str] = []
        for pattern in patterns:
            if not _EXPLICIT_KIND_PATTERN.fullmatch(pattern):
                return [], ("invalid_request", "invalid_kind_pattern")
            root = pattern.split(".", 1)[0]
            if root not in _EXPLICIT_RETRIEVAL_KIND_ROOTS:
                return [], ("forbidden", "kind_pattern_not_authorized")
            if pattern not in normalized:
                normalized.append(pattern)
        return normalized, None

    def _bootstrap(self) -> None:
        store = None
        process_runtime = None
        try:
            memcore = self._import_memcore()

            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._llm_client = build_akane_llm_client(self.llm)
            self._embedding = build_akane_embedding_provider(self.embedding_provider)
            self._token_counter = build_akane_token_counter()
            self._memory_config = self._build_memory_config(memcore)
            store = memcore.SQLiteMemoryStore(str(self.storage_path))
            index = memcore.InMemoryVectorIndex(embedding=self._embedding)
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
            raw_token_trigger=max(
                1000,
                int(getattr(config, "MEMCORE_RAW_TOKEN_TRIGGER", 48000) or 48000),
            ),
            raw_token_batch_ratio=max(
                0.01,
                min(0.99, float(getattr(config, "MEMCORE_RAW_TOKEN_BATCH_RATIO", 0.67) or 0.67)),
            ),
            episodic_visible_max=max(
                1,
                int(
                    getattr(
                        config,
                        "EPISODIC_VISIBLE_MAX",
                        getattr(config, "RECENT_SUMMARY_LIMIT", 8),
                    )
                    or 8
                ),
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
            semantic_reinforcement_lookback=max(
                1,
                int(getattr(config, "SEMANTIC_REINFORCEMENT_LOOKBACK", 8) or 8),
            ),
            semantic_reinforcement_min_overlap=max(
                1,
                int(getattr(config, "SEMANTIC_REINFORCEMENT_MIN_OVERLAP", 2) or 2),
            ),
            visible_memory_scope=self.visible_scope,
            enable_flavor=self.enable_flavor,
            operation_projection_policy=str(
                getattr(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "full_until_raw_compaction") or ""
            )
            or "full_until_raw_compaction",
        )

    def _append_standalone_turn(
        self,
        *,
        operation: str,
        role: str,
        record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str = "",
        actor_display_name: str = "",
        observed: bool = False,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
        legacy_import: bool = False,
    ) -> dict[str, Any]:
        source_id = str((record or {}).get("source_id") or "").strip()
        if not source_id:
            return self._status(operation, False, "invalid_record", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)

        try:
            entry = self._build_timeline_input(
                role=role,
                record=record,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                observed=observed,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
                turn_role=None,
                legacy_import=legacy_import,
            )
            written = system.append_standalone_entry(entry)
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.source_id or source_id),
                index_status=str(written.index_status or ""),
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    def _begin_voice_projection_turn(
        self,
        *,
        system: Any,
        operation: str,
        source_id: str,
        source_event_id: str,
        projection_id: str,
        payload: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str,
        actor_display_name: str,
        timestamp: int,
        projection_digest: str,
    ) -> dict[str, Any]:
        memcore = self._memcore_module or self._import_memcore()
        voice_turn_id = str(payload.get("voice_turn_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not voice_turn_id or not text:
            return self._status(
                operation,
                False,
                "invalid_projection",
                source_id=source_id,
                reason="voice_user_projection_invalid",
            )
        turn_id = self._voice_projection_turn_id(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            voice_turn_id=voice_turn_id,
        )
        entry = memcore.TimelineEntryInput(
            source_id=source_id,
            kind="message.user.voice",
            origin=memcore.EntryOrigin.USER,
            turn_role=memcore.TurnRole.STIMULUS,
            semantic_text=text,
            timestamp=timestamp,
            payload={
                "text": text,
                "modality": "voice",
                "voice_turn_id": voice_turn_id,
                "turn_revision": payload.get("turn_revision"),
                "language_hint": payload.get("language_hint"),
                "disposition": payload.get("disposition"),
                "semantic_label": payload.get("semantic_label"),
            },
            actor=self._build_actor(
                actor_stable_id or profile_user_id,
                actor_display_name,
            ),
            trace_metadata={
                "projection_id": projection_id,
                "source_event_id": source_event_id,
                "projection_digest": projection_digest,
                "status": "committed",
            },
            memory_metadata={},
            annotation_status=memcore.AnnotationStatus.UNANNOTATED,
            retrieval_policy=memcore.RetrievalPolicy.AUTO,
            retrieval_visibility=memcore.RetrievalVisibility.EXPLICIT,
            semanticize=True,
            prompt_visible=True,
            compatibility_role="user",
        )
        handle = system.begin_turn(
            stimuli=[entry],
            annotation_target_ids=[source_id],
            turn_id=turn_id,
            opened_at=timestamp,
        )
        status = str(getattr(handle, "status", "") or "")
        stored = handle.stimuli[0]
        return {
            **self._status(
                operation,
                True,
                status or "open",
                source_id=stored.source_id,
                index_status=stored.index_status,
            ),
            "turn_id": str(handle.turn_id),
            "voice_turn_id": voice_turn_id,
            "writable": status == "open",
        }

    def _complete_voice_projection_turn(
        self,
        *,
        system: Any,
        operation: str,
        source_id: str,
        source_event_id: str,
        projection_id: str,
        payload: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        timestamp: int,
        projection_digest: str,
    ) -> dict[str, Any]:
        voice_turn_id = str(payload.get("voice_turn_id") or "").strip()
        full_text = str(payload.get("full_text") or "")
        if not voice_turn_id:
            return self._status(
                operation,
                False,
                "invalid_projection",
                source_id=source_id,
                reason="voice_assistant_projection_invalid",
            )
        delivered_units = self._voice_projection_unit_ordinals(payload.get("delivered_units"))
        interrupted_units = self._voice_projection_unit_ordinals(payload.get("interrupted_units"))
        if delivered_units is None or interrupted_units is None:
            return self._status(
                operation,
                False,
                "invalid_projection",
                source_id=source_id,
                reason="voice_assistant_projection_units_invalid",
            )
        turn_id = self._voice_projection_turn_id(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            voice_turn_id=voice_turn_id,
        )
        memory_metadata = payload.get("memory_metadata")
        annotation = memory_metadata if isinstance(memory_metadata, dict) else None
        result = system.complete_turn(
            turn_id=turn_id,
            semantic_text=full_text,
            provider_output_raw="",
            memory_annotation=annotation,
            annotation_status="accepted_model" if annotation is not None else "missing",
            timestamp=timestamp,
            source_id=source_id,
            kind="message.assistant.voice",
            trace_metadata={
                "projection_id": projection_id,
                "source_event_id": source_event_id,
                "projection_digest": projection_digest,
                "status": "committed",
            },
            payload={
                "modality": "voice",
                "voice_turn_id": voice_turn_id,
                "response_id": payload.get("response_id"),
                "response_generation": payload.get("response_generation"),
                "purpose": payload.get("purpose"),
                "full_text_status": payload.get("full_text_status"),
                "delivery_status": payload.get("delivery_status"),
                "delivered_units": delivered_units,
                "interrupted_units": interrupted_units,
                "projection_id": projection_id,
                "source_event_id": source_event_id,
            },
        )
        final_entry = getattr(result, "final_entry", None)
        result_status = str(getattr(result, "status", "") or "")
        if result_status == "already_completed":
            return {
                **self._status(
                    operation,
                    False,
                    "conflict",
                    source_id=source_id,
                    reason="voice_turn_completed_by_other_projection",
                ),
                "turn_id": turn_id,
                "voice_turn_id": voice_turn_id,
            }
        return {
            **self._status(
                operation,
                bool(getattr(result, "completed", False)),
                result_status or "failed",
                source_id=str(getattr(final_entry, "source_id", "") or source_id),
                index_status=str(getattr(final_entry, "index_status", "") or ""),
                reason=str(getattr(result, "reason", "") or ""),
            ),
            "turn_id": turn_id,
            "voice_turn_id": voice_turn_id,
        }

    @staticmethod
    def _voice_projection_unit_ordinals(value: Any) -> list[int] | None:
        if value is None:
            return []
        if not isinstance(value, list):
            return None
        ordinals: list[int] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                return None
            ordinals.append(item)
        return ordinals

    def _voice_projection_replay_status(
        self,
        *,
        system: Any,
        operation: str,
        source_id: str,
        kind: str,
        projection_digest: str,
    ) -> dict[str, Any] | None:
        if self._store is None:
            return self._status(
                operation,
                False,
                "unavailable",
                source_id=source_id,
                reason="memcore_store_unavailable",
            )
        try:
            existing = self._store.get_record_by_source_id(source_id)
        except Exception:
            return self._status(
                operation,
                False,
                "failed",
                source_id=source_id,
                reason="voice_projection_replay_check_failed",
            )
        if existing is None:
            return None
        if not self._record_belongs_to_namespace(existing, system.namespace):
            return self._status(
                operation,
                False,
                "conflict",
                source_id=source_id,
                reason="voice_projection_owned_by_other_namespace",
            )
        trace_metadata = (
            existing.get("trace_metadata")
            if isinstance(existing.get("trace_metadata"), dict)
            else {}
        )
        if (
            str(existing.get("kind") or "") != kind
            or str(trace_metadata.get("projection_digest") or "") != projection_digest
        ):
            return self._status(
                operation,
                False,
                "conflict",
                source_id=source_id,
                reason="voice_projection_idempotency_conflict",
            )
        return self._status(
            operation,
            True,
            "already_completed" if kind == "message.assistant.voice" else "duplicate",
            source_id=source_id,
            index_status=str(existing.get("index_status") or ""),
        )

    def _append_voice_projection_event(
        self,
        *,
        system: Any,
        operation: str,
        source_id: str,
        source_event_id: str,
        projection_id: str,
        kind: str,
        payload: dict[str, Any],
        timestamp: int,
        projection_digest: str,
    ) -> dict[str, Any]:
        memcore = self._memcore_module or self._import_memcore()
        provisional = bool(payload.get("provisional"))
        text = str(payload.get("text") or "")
        entry = memcore.TimelineEntryInput(
            source_id=source_id,
            kind=kind,
            origin=memcore.EntryOrigin.ENVIRONMENT,
            turn_role=None,
            semantic_text=text,
            timestamp=timestamp,
            payload=dict(payload),
            trace_metadata={
                "projection_id": projection_id,
                "source_event_id": source_event_id,
                "projection_digest": projection_digest,
                "status": "provisional" if provisional else "committed",
            },
            memory_metadata={},
            annotation_status=memcore.AnnotationStatus.UNANNOTATED,
            retrieval_policy=(memcore.RetrievalPolicy.NEVER if provisional else memcore.RetrievalPolicy.EXPLICIT),
            retrieval_visibility=(
                memcore.RetrievalVisibility.NEVER if provisional else memcore.RetrievalVisibility.EXPLICIT
            ),
            semanticize=False,
            prompt_visible=not provisional,
            compatibility_role=kind,
        )
        stored = system.append_standalone_entry(entry)
        return self._status(
            operation,
            True,
            "recorded",
            source_id=stored.source_id,
            index_status=stored.index_status,
        )

    def _build_timeline_input(
        self,
        *,
        role: str,
        record: dict[str, Any],
        actor_stable_id: str = "",
        actor_display_name: str = "",
        observed: bool = False,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
        turn_role: str | None,
        external_event: dict[str, Any] | None = None,
        legacy_import: bool = False,
    ) -> Any:
        memcore = self._memcore_module or self._import_memcore()
        raw = dict(record or {})
        source_id = str(raw.get("source_id") or "").strip()
        timestamp = int(raw.get("timestamp") or time.time())
        metadata = raw.get("memory_metadata")
        if legacy_import:
            metadata = memcore.migrate_legacy_memory_metadata(metadata)
        elif not isinstance(metadata, dict):
            metadata = {}
        index_in_vector = bool(raw.get("index_in_vector", True))
        is_standalone = turn_role is None
        is_external_event = external_event is not None
        if external_event is not None:
            event_type = self._kind_suffix(external_event.get("event_type"), fallback="external")
            source = str(external_event.get("source") or "").strip()
            fields = dict(external_event.get("fields") or {})
            payload = dict(fields)
            if source:
                payload["source"] = source
            semantic_text = memcore.render_external_event_text(
                event_type=event_type,
                fields=fields,
                source=source,
            )
            kind = f"event.{event_type}"
            origin = memcore.EntryOrigin.ENVIRONMENT
            compatibility_role = kind
            metadata = metadata or self._external_event_metadata(external_event)
        else:
            content = str(raw.get("content") or "")
            semantic_text = content
            payload = {"text": content}
            addressing = raw.get("message_addressing")
            if isinstance(addressing, dict) and addressing:
                additional_mentions: list[dict[str, str]] = []
                seen_mentions: set[str] = set()
                for mention in list(addressing.get("mentions") or [])[:16]:
                    if not isinstance(mention, dict):
                        continue
                    mention_id = str(mention.get("actor_id") or "").strip()[:160]
                    if not mention_id or mention_id == target_actor_id or mention_id in seen_mentions:
                        continue
                    seen_mentions.add(mention_id)
                    additional_mentions.append(
                        {
                            "actor_id": mention_id,
                            "display_name": str(mention.get("display_name") or "").strip()[:160],
                        }
                    )
                if additional_mentions:
                    payload["mentioned_actors"] = additional_mentions
            if role == "assistant":
                kind = "message.assistant" if is_standalone else "message.assistant.intermediate"
                origin = memcore.EntryOrigin.ASSISTANT
                compatibility_role = "assistant"
            else:
                kind = "message.user.observed" if observed else "message.user"
                origin = memcore.EntryOrigin.USER
                compatibility_role = "user.observed" if observed else "user"
        has_memory_annotation = self._has_memory_annotation(metadata)
        retrieval_policy = (
            memcore.RetrievalPolicy.NEVER
            if role == "assistant" or (is_standalone and not index_in_vector)
            else memcore.RetrievalPolicy.EXPLICIT
            if is_standalone and is_external_event
            else memcore.RetrievalPolicy.AUTO
        )
        retrieval_visibility = (
            memcore.RetrievalVisibility.NEVER
            if retrieval_policy is memcore.RetrievalPolicy.NEVER
            else memcore.RetrievalVisibility.EXPLICIT
            if is_standalone and is_external_event
            else memcore.RetrievalVisibility.DEFAULT
            if is_standalone and has_memory_annotation
            else memcore.RetrievalVisibility.EXPLICIT
        )
        return memcore.TimelineEntryInput(
            source_id=source_id,
            kind=kind,
            origin=origin,
            turn_role=turn_role,
            semantic_text=semantic_text,
            timestamp=timestamp,
            payload=payload,
            actor=(
                self._build_actor(actor_stable_id, actor_display_name) if origin is memcore.EntryOrigin.USER else None
            ),
            target_actor=(self._build_actor(target_actor_id, target_actor_display_name) if target_actor_id else None),
            memory_metadata=metadata,
            annotation_status=(
                memcore.AnnotationStatus.ACCEPTED_LEGACY
                if is_standalone and has_memory_annotation and legacy_import
                else memcore.AnnotationStatus.ACCEPTED_HOST
                if is_standalone and has_memory_annotation and not is_external_event
                else memcore.AnnotationStatus.PLAIN
                if is_standalone and not is_external_event
                else memcore.AnnotationStatus.UNANNOTATED
            ),
            retrieval_policy=retrieval_policy,
            retrieval_visibility=retrieval_visibility,
            semanticize=bool(index_in_vector and role != "assistant"),
            compatibility_role=compatibility_role,
        )

    def _build_tool_entry_pair(self, exchange: dict[str, Any]) -> tuple[Any, Any]:
        memcore = self._memcore_module or self._import_memcore()
        tool_name = str(exchange.get("tool_name") or "unknown").strip() or "unknown"
        tool = self._kind_segment(tool_name, fallback="unknown")
        prefix = str(exchange.get("source_id_prefix") or "").strip()
        effective_ts = int(exchange.get("timestamp") or time.time())
        correlation_id = str(exchange.get("tool_call_id") or "").strip()
        if not correlation_id:
            correlation_id = (
                "call_"
                + hashlib.sha256(f"{tool}|{prefix}|{effective_ts}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            )
        tool_input = exchange.get("tool_input")
        result = exchange.get("result")
        retention_anchor = exchange.get("retention_anchor")
        source = str(exchange.get("source") or "").strip()
        common = {
            "correlation_id": correlation_id,
            "memory_metadata": {},
            "retrieval_policy": memcore.RetrievalPolicy.EXPLICIT,
            "retrieval_visibility": memcore.RetrievalVisibility.EXPLICIT,
            "semanticize": True,
        }
        action = memcore.TimelineEntryInput(
            source_id=f"{prefix}:tool_use" if prefix else "",
            kind=f"tool.{tool}.call",
            origin=memcore.EntryOrigin.ASSISTANT,
            turn_role=memcore.TurnRole.ACTION,
            semantic_text=memcore.render_tool_use_text(tool_input=tool_input),
            timestamp=effective_ts,
            payload={"input": tool_input},
            trace_metadata={"tool_name": tool_name, "status": "running"},
            compatibility_role=f"assistant.tool_call {tool} {correlation_id}",
            **common,
        )
        observation_trace = {
            "tool_name": tool_name,
            "status": str(exchange.get("result_status") or "success"),
        }
        if isinstance(retention_anchor, dict) and retention_anchor:
            observation_trace["retention_anchor"] = dict(retention_anchor)
        observation = memcore.TimelineEntryInput(
            source_id=f"{prefix}:tool_result" if prefix else "",
            kind=f"tool.{tool}.result",
            origin=memcore.EntryOrigin.ENVIRONMENT,
            turn_role=memcore.TurnRole.OBSERVATION,
            semantic_text=memcore.render_tool_result_text(result=result, source=source),
            timestamp=effective_ts + 1,
            payload={"output": result, "source": source},
            trace_metadata=observation_trace,
            compatibility_role=f"tool.{tool} {correlation_id}",
            **common,
        )
        return action, observation

    @staticmethod
    def _validate_tool_batch_prepared(prepared: list[tuple[Any, Any]], *, operation: str) -> None:
        """Reject whole-batch violations before any entry is appended.

        Appends are per-entry in the store; duplicate correlation/source ids would
        only fail on the second append, leaving a partial batch behind. Validating
        up front keeps the batch all-or-nothing for construction-visible problems.
        """
        action_by_correlation: dict[str, str] = {}
        source_ids: set[str] = set()
        for action, observation in prepared:
            correlation = str(getattr(action, "correlation_id", "") or "").strip()
            if correlation:
                if correlation in action_by_correlation:
                    raise ValueError("duplicate_tool_call_id_in_batch")
                action_by_correlation[correlation] = str(action.source_id or "")
            for entry in (action, observation):
                source_id = str(getattr(entry, "source_id", "") or "").strip()
                if source_id and source_id in source_ids:
                    raise ValueError("duplicate_tool_entry_source_id_in_batch")
                if source_id:
                    source_ids.add(source_id)

    @staticmethod
    def _stable_turn_id(source_id: str) -> str:
        digest = hashlib.sha256(str(source_id or "").encode("utf-8", errors="ignore")).hexdigest()[:32]
        return f"turn:{digest}"

    @classmethod
    def _voice_projection_turn_id(
        cls,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        voice_turn_id: str,
    ) -> str:
        material = "\0".join(
            (
                "voice",
                str(profile_user_id or ""),
                str(session_id or ""),
                str(character_pack_id or ""),
                str(voice_turn_id or ""),
            )
        )
        return cls._stable_turn_id(material)

    @staticmethod
    def _kind_segment(value: Any, *, fallback: str) -> str:
        normalized = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower()).strip("_-")
        return normalized[:80] or fallback

    @classmethod
    def _kind_suffix(cls, value: Any, *, fallback: str) -> str:
        parts = [cls._kind_segment(part, fallback="") for part in str(value or "").strip().lower().split(".")]
        normalized = ".".join(part for part in parts if part)
        return normalized[:120].strip(".") or fallback

    @classmethod
    def _external_event_metadata(cls, event: dict[str, Any]) -> dict[str, Any]:
        _ = event
        return {}

    @staticmethod
    def _has_memory_annotation(metadata: dict[str, Any]) -> bool:
        from memcore import memory_metadata_has_signal

        return memory_metadata_has_signal(metadata)

    @staticmethod
    def _safe_task_event_text(value: Any, *, limit: int) -> str:
        """Keep background-task text model-reusable without projecting secrets.

        Operation evidence (goal text, messages, failure targets, discovered
        paths) stays intact so a later continuation round can reuse it.
        Resource backing paths belong to structured fields and must not enter
        these text fields in the first place.
        """
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text)
        text = re.sub(
            r"(?i)\b(api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()[: max(1, int(limit or 1))]

    @classmethod
    def _task_event_artifact_handles(
        cls,
        *,
        task: dict[str, Any],
        payload: dict[str, Any],
        handoff: dict[str, Any],
    ) -> list[str]:
        raw_items = [
            *list(task.get("artifacts") or []),
            *list(payload.get("artifacts") or []),
            *list(handoff.get("artifacts") or []),
        ]
        handles: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, dict):
                value = (
                    item.get("id")
                    or item.get("handle")
                    or item.get("generated_handle")
                    or item.get("attachment_handle")
                )
            else:
                value = item
            handle = cls._safe_task_event_text(value, limit=120)
            if not handle or handle.lower() in seen:
                continue
            seen.add(handle.lower())
            handles.append(handle)
        return handles

    def _record_material_event(
        self,
        *,
        operation: str,
        event_type: str,
        item: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        timestamp: int | None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(item, dict):
            return self._status(operation, False, "invalid_record", reason="item_required")
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        profile = str(profile_user_id or item.get("profile_user_id") or "").strip()
        session = str(session_id or item.get("session_id") or "").strip()
        character = str(
            character_pack_id or detail.get("character_pack_id") or item.get("character_pack_id") or ""
        ).strip()
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id:
            return self._status(operation, False, "invalid_record", reason="attachment_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=character,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=attachment_id, reason=self._reason)

        effective_ts = int(timestamp or item.get("updated_at") or item.get("created_at") or time.time())
        handle = str(item.get("attachment_handle") or "").strip()
        file_id = handle or attachment_id
        status_part = self._attachment_file_status(item, event_type=event_type, delete_storage=delete_storage)
        source_id = f"attachment:{attachment_id}:{event_type}:{status_part}:{effective_ts}"
        try:
            memcore = self._memcore_module or self._import_memcore()
            material_kind = str(item.get("kind") or "file").strip() or "file"
            kind_label = self._kind_segment(material_kind, fallback="material")
            file_key = self._kind_segment(file_id, fallback="file")
            filename = self._attachment_filename(item)
            derived_status = self._attachment_derived_status(item)
            failure = self._attachment_failure(item)
            if event_type == "cleanup":
                semantic_text = memcore.render_material_cleanup_text(
                    file_id=file_id,
                    kind=material_kind,
                    filename=filename,
                    file_status=status_part,
                    derived_status=derived_status,
                    reason=reason,
                )
                origin = memcore.EntryOrigin.ENVIRONMENT
                actor = None
                compatibility_role = f"system.material_cleanup {kind_label} {file_key}"
            else:
                actor_stable_id, actor_display_name = self._attachment_actor_identity(item)
                semantic_text = memcore.render_material_reference_text(
                    file_id=file_id,
                    kind=material_kind,
                    filename=filename,
                    mime_type=str(item.get("mime_type") or ""),
                    file_status=status_part,
                    derived_status=derived_status,
                )
                if failure:
                    semantic_text = "\n".join(
                        [
                            semantic_text,
                            "failure:",
                            f"  code: {failure['code']}",
                            f"  reason: {failure['reason']}",
                            *(
                                [f"  observed_bytes: {failure['observed_bytes']}"]
                                if failure.get("observed_bytes")
                                else []
                            ),
                            *([f"  limit_bytes: {failure['limit_bytes']}"] if failure.get("limit_bytes") else []),
                        ]
                    )
                origin = memcore.EntryOrigin.USER
                actor = self._build_actor(actor_stable_id, actor_display_name)
                compatibility_role = f"user.attachment {kind_label} {file_key}"
            entry = memcore.TimelineEntryInput(
                source_id=source_id,
                kind=f"material.{event_type}",
                origin=origin,
                turn_role=None,
                semantic_text=semantic_text,
                timestamp=effective_ts,
                payload={
                    "file_id": file_id,
                    "kind": material_kind,
                    "filename": filename,
                    "mime_type": str(item.get("mime_type") or ""),
                    "file_status": status_part,
                    "derived_status": derived_status,
                    **({"failure": failure} if failure else {}),
                    **({"reason": str(reason or "")} if event_type == "cleanup" else {}),
                },
                actor=actor,
                memory_metadata={},
                annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                retrieval_policy=memcore.RetrievalPolicy.EXPLICIT,
                retrieval_visibility=memcore.RetrievalVisibility.EXPLICIT,
                semanticize=True,
                compatibility_role=compatibility_role,
            )
            written = system.append_standalone_entry(entry)
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.source_id or source_id),
                index_status=str(written.index_status or ""),
            )
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=failed_reason)

    @staticmethod
    def _attachment_filename(item: dict[str, Any]) -> str:
        for key in ("origin_name", "summary_title", "attachment_handle", "attachment_id"):
            text = str(item.get(key) or "").strip()
            if text:
                return text[:160]
        return "attachment"

    def _build_actor(self, stable_id: str, display_name: str = "") -> Any | None:
        actor_id = str(stable_id or "").strip()
        if not actor_id:
            return None
        memcore = self._memcore_module or self._import_memcore()
        return memcore.Actor(
            stable_id=actor_id[:160],
            display_name=str(display_name or "").strip()[:160],
        )

    @staticmethod
    def _attachment_actor_identity(item: dict[str, Any]) -> tuple[str, str]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        raw_id = str(detail.get("qq_sender_id") or "").strip()
        if not raw_id:
            return "", ""
        stable_id = raw_id if raw_id.startswith("qq:") else f"qq:{raw_id}"
        display_name = str(detail.get("qq_sender_label") or "").strip()
        return stable_id, display_name

    @staticmethod
    def _attachment_file_status(
        item: dict[str, Any],
        *,
        event_type: str,
        delete_storage: bool,
    ) -> str:
        if event_type == "cleanup":
            return "deleted" if delete_storage else "cleared"
        status = str(item.get("status") or "").strip().lower()
        if status == "pending_observation":
            return "processing"
        if status in {"ready", "failed", "cleared"}:
            return status
        return status or "unknown"

    @staticmethod
    def _attachment_derived_status(item: dict[str, Any]) -> str:
        status = str(item.get("status") or "").strip().lower()
        if status == "pending_observation":
            return "processing"
        if status == "failed":
            return "failed"
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        if str(item.get("summary_title") or "").strip() or str(item.get("short_hint") or "").strip() or detail:
            return "ready"
        if status == "ready":
            return "ready"
        return "unknown"

    @staticmethod
    def _attachment_failure(item: dict[str, Any]) -> dict[str, Any]:
        if str(item.get("status") or "").strip().lower() != "failed":
            return {}
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        raw_failure = detail.get("failure") if isinstance(detail.get("failure"), dict) else {}
        code = str(raw_failure.get("code") or item.get("error_message") or "attachment_failed").strip()
        reason = " ".join(str(raw_failure.get("reason") or item.get("short_hint") or "附件处理失败。").split()).strip()
        failure: dict[str, Any] = {
            "code": code[:120],
            "reason": reason[:500],
        }
        for field in ("observed_bytes", "limit_bytes"):
            try:
                value = max(0, int(raw_failure.get(field) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                failure[field] = value
        return failure

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

    def _get_system(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> Any:
        if self._closing or self._closed:
            raise RuntimeError("memcore_manager_closed")
        if self._memcore_module is None or self._llm_client is None or self._embedding is None:
            raise RuntimeError(self._reason or "memcore_not_bootstrapped")
        if self._store is None or self._index is None or self._memory_config is None or self._runtime is None:
            raise RuntimeError(self._reason or "memcore_dependencies_not_ready")

        user_id = str(profile_user_id or session_id or "default_user").strip() or "default_user"
        conversation_id = str(session_id or user_id).strip() or user_id
        domain_id = str(character_pack_id or "").strip()
        key = (user_id, conversation_id, domain_id)
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("memcore_manager_closed")
            existing = self._systems.get(key)
            if existing is None:
                namespace = self._memcore_module.Namespace(
                    user_id=user_id,
                    tenant_id="",
                    domain_id=domain_id,
                    conversation_id=conversation_id,
                )
                persona_text = ""
                if self._persona_text_provider is not None:
                    try:
                        persona_text = self._persona_text_provider(profile_user_id, character_pack_id)
                    except Exception as exc:
                        logger.debug("memcore persona_text_provider failed: %s", exc)
                existing = self._memcore_module.MemorySystem(
                    llm=self._llm_client,
                    namespace=namespace,
                    timezone=str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai",
                    storage_dir=str(self.storage_path),
                    config=self._memory_config,
                    store=self._store,
                    index=self._index,
                    embedding=self._embedding,
                    token_counter=self._token_counter,
                    persona_text=persona_text,
                    prompt_overrides=self._build_prompt_overrides(persona_text),
                    runtime=self._runtime,
                )
                self._systems[key] = existing
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
            "stable_facts 只保留用户反复确认过的偏好、身份、关系和长期计划；"
            "单次工具操作的执行结果和系统配置字段（reply_mode 等）不应出现在 stable_facts 里。"
        )
        return PromptOverrides(
            persona_text=persona_text,
            extra_semantic_guidance=extra_semantic,
        )

    def _warm_index_for_system(self, system: Any, *, operation: str) -> None:
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
                )
            )
        )

    def _finish_compaction(
        self,
        future: Future[Any],
        *,
        compaction_key: tuple[str, str, str, str],
        namespace_hash: str,
        requested_profile: str,
    ) -> None:
        self._log_compaction_result(
            future,
            namespace_hash=namespace_hash,
            requested_profile=requested_profile,
        )
        with self._lock:
            self._background_futures.discard(future)
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

    def _discard_background_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._background_futures.discard(future)

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

    @staticmethod
    def _status(
        operation: str,
        ok: bool,
        status: str,
        *,
        source_id: str = "",
        reason: str = "",
        index_status: str = "",
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "ok": bool(ok),
            "status": status,
            "source_id": str(source_id or ""),
            "index_status": str(index_status or ""),
            "reason": str(reason or ""),
        }

    @staticmethod
    def _coerce_positive_int_or_none(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _record_belongs_to_namespace(record: dict[str, Any], namespace: Any) -> bool:
        return (
            str(record.get("tenant_id") or "") == str(getattr(namespace, "tenant_id", "") or "")
            and str(record.get("user_id") or "") == str(getattr(namespace, "user_id", "") or "")
            and str(record.get("domain_id") or "") == str(getattr(namespace, "domain_id", "") or "")
            and str(record.get("conversation_id") or "") == str(getattr(namespace, "conversation_id", "") or "")
        )

    @staticmethod
    def _legacy_import_metadata(memory_metadata: Any) -> dict[str, Any]:
        from memcore import migrate_legacy_memory_metadata

        return migrate_legacy_memory_metadata(memory_metadata)

    @staticmethod
    def _project_native_memory_dispatch(
        *,
        operation: str,
        dispatched: Any,
    ) -> dict[str, Any]:
        envelope = dict(dispatched) if isinstance(dispatched, dict) else {}
        result = envelope.get("result")
        payload = dict(result) if isinstance(result, dict) else {}
        status = str(payload.get("status") or envelope.get("status") or "failed")
        reason = str(payload.get("reason") or envelope.get("reason") or "")
        return {
            **payload,
            "operation": operation,
            "ok": bool(envelope.get("ok")),
            "status": status,
            "reason": reason,
            "receipt": dict(envelope.get("receipt") or {}),
            "backend": "memcore",
        }

    @classmethod
    def _import_layer_result(
        cls,
        operation: str,
        scanned: int,
        upserted: int,
        filtered: int,
        skipped: int,
        failed: int,
        conflicted: int,
    ) -> dict[str, Any]:
        payload = cls._import_result(operation, scanned, upserted, filtered, skipped, failed)
        payload["conflicted"] = int(conflicted)
        if conflicted and not payload.get("reason"):
            payload["reason"] = "some_records_conflicted"
        return payload

    @classmethod
    def _import_result(
        cls,
        operation: str,
        scanned: int,
        upserted: int,
        filtered: int,
        skipped: int,
        failed: int,
    ) -> dict[str, Any]:
        status = "completed" if failed == 0 else "partial"
        return {
            **cls._status(operation, failed == 0, status, reason="" if failed == 0 else "some_records_failed"),
            "scanned": int(scanned),
            "upserted": int(upserted),
            "filtered": int(filtered),
            "skipped": int(skipped),
            "failed": int(failed),
        }

    @staticmethod
    def _compaction_namespace_hash(system: Any) -> str:
        values = MemcoreManager._compaction_namespace_key(system)
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
