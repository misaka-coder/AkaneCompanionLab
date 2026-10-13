"""Authoritative MemCore provider surfaces and bounded projection diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

import config as mod_config
from .response_catalog import _split_active_projection_turns
from .response_contracts import ResponseRequest

logger = logging.getLogger("akane.response_builder")
PROJECTION_READ_MIGRATION_REASONS = frozenset({"legacy_memory_backend"})


def _safe_projection_failure_code(value: Any, *, fallback: str = "projection_detail_unavailable") -> str:
    """Keep diagnostics useful without copying paths or provider data into logs."""

    text = str(value or "").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,159}", text):
        return text
    return str(fallback or "projection_detail_unavailable")[:160]


def _compare_memcore_projection_shadow(
    engine: Any,
    *,
    generation_context: dict[str, Any],
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    current_source_id: str,
    chat_model_override: str,
    execution_target: Any = None,
) -> dict[str, Any]:
    if not bool(getattr(mod_config, "MEMCORE_SHADOW_COMPARE", False)):
        return {"ok": True, "status": "disabled", "reason": "shadow_compare_disabled"}
    manager = getattr(engine, "memcore_manager", None)
    compare = getattr(manager, "compare_context_projection", None)
    runtime = getattr(engine, "llm", None)
    protocol_getter = getattr(runtime, "chat_provider_protocol", None)
    history_normalizer = getattr(runtime, "normalize_chat_history_turns", None)
    if not callable(compare) or not callable(protocol_getter) or not callable(history_normalizer):
        return {"ok": False, "status": "unavailable", "reason": "projection_shadow_dependencies_unavailable"}
    try:
        protocol = (
            str(
                protocol_getter(
                    chat_model_override=chat_model_override,
                    execution_target=execution_target,
                )
                or ""
            )
            .strip()
            .lower()
        )
        history_turns = list(generation_context.get("history_turns") or [])
        history_start = max(0, int(generation_context.get("memcore_history_start_index") or 0))
        actual_history = history_normalizer(
            history_turns[history_start:],
            chat_model_override=chat_model_override,
            execution_target=execution_target,
        )
        result = compare(
            provider_profile=protocol,
            actual_history_messages=actual_history,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            exclude_source_ids=[current_source_id] if current_source_id else [],
        )
        safe_result = (
            dict(result)
            if isinstance(result, dict)
            else {
                "ok": False,
                "status": "failed",
                "reason": "invalid_projection_shadow_result",
            }
        )
        logger.info(
            "memcore projection shadow status=%s profile=%s strict_prefix=%s projection_hash=%s "
            "actual_history_hash=%s first_divergence_index=%s reason=%s",
            str(safe_result.get("status") or "unknown")[:40],
            str(safe_result.get("provider_profile") or "")[:40],
            bool(safe_result.get("strict_prefix")),
            str(safe_result.get("projection_hash") or "")[:64],
            str(safe_result.get("actual_history_hash") or "")[:64],
            int(safe_result.get("first_divergence_index") or 0),
            str(safe_result.get("divergence_reason") or safe_result.get("reason") or "")[:80],
        )
        return safe_result
    except Exception as exc:
        logger.warning("memcore projection shadow unavailable: %s", exc.__class__.__name__)
        return {"ok": False, "status": "failed", "reason": "shadow_compare_failed"}


def _projected_current_message_text(
    projection: dict[str, Any],
    *,
    current_source_id: str,
) -> str:
    current_sid = str(current_source_id or "").strip()
    if not current_sid:
        return ""
    for message in list(projection.get("current_turn_messages") or []):
        if not isinstance(message, dict):
            continue
        source_ids = {
            str(source_id or "").strip()
            for source_id in list(message.get("source_ids") or [])
            if str(source_id or "").strip()
        }
        if current_sid not in source_ids:
            continue
        payload = message.get("payload")
        if not isinstance(payload, dict) or str(payload.get("role") or "") != "user":
            continue
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return ""


def _build_memcore_provider_history(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    current_source_id: str,
    allow_history_only: bool = False,
    chat_model_override: str,
    execution_target: Any = None,
    exclude_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    if _memory_backend() != "memcore":
        return {"ok": False, "status": "migration_window", "reason": "legacy_memory_backend"}
    manager = getattr(engine, "memcore_manager", None)
    build_surface = getattr(manager, "build_context_surface", None)
    runtime = getattr(engine, "llm", None)
    protocol_getter = getattr(runtime, "chat_provider_protocol", None)
    if not callable(build_surface) or not callable(protocol_getter):
        return {"ok": False, "status": "unavailable", "reason": "projection_read_dependencies_unavailable"}
    if not str(current_source_id or "").strip() and not allow_history_only:
        return {"ok": False, "status": "skipped", "reason": "current_source_id_missing"}
    try:
        protocol = (
            str(
                protocol_getter(
                    chat_model_override=chat_model_override,
                    execution_target=execution_target,
                )
                or ""
            )
            .strip()
            .lower()
        )
        surface: dict[str, Any] = {}
        for _attempt in range(2):
            candidate = build_surface(
                provider_profile=protocol,
                current_source_id=current_source_id,
                active_turn_messages=[],
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            surface = candidate if isinstance(candidate, dict) else {}
            if surface.get("ok"):
                break
            if str(surface.get("reason") or "") in PROJECTION_READ_MIGRATION_REASONS:
                break
        if not isinstance(surface, dict) or not surface.get("ok"):
            migration_reason = str((surface or {}).get("reason") or "")
            if migration_reason in PROJECTION_READ_MIGRATION_REASONS:
                return {"ok": False, "status": "migration_window", "reason": migration_reason}
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "projection_build_failed",
                "detail": _safe_projection_failure_code(
                    (surface or {}).get("reason") or (surface or {}).get("status"),
                ),
            }
        current_sid = str(current_source_id).strip()
        history_turns = [dict(item) for item in list(surface.get("history_messages") or []) if isinstance(item, dict)]
        current_payload = surface.get("current_message")
        current_ids = {
            str(item or "").strip()
            for item in (list(surface.get("message_source_ids") or [])[len(history_turns)] if current_payload else [])
            if str(item or "").strip()
        }
        current_source_visible = bool(current_sid and current_sid in current_ids)
        if current_sid and not current_source_visible:
            return {"ok": False, "status": "skipped", "reason": "current_source_not_projected"}
        active_start = len(history_turns) + (1 if current_payload else 0)
        source_ids = list(surface.get("message_source_ids") or [])
        projection_metadata = [
            dict(item) for item in list(surface.get("message_projection_metadata") or []) if isinstance(item, dict)
        ]
        expected_message_count = (
            len(history_turns) + (1 if current_payload else 0) + len(list(surface.get("active_turn_messages") or []))
        )
        if len(projection_metadata) != expected_message_count:
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "context_surface_projection_metadata_missing",
            }
        active_payloads = [
            dict(item) for item in list(surface.get("active_turn_messages") or []) if isinstance(item, dict)
        ]
        active_ids = source_ids[active_start : active_start + len(active_payloads)]
        current_turn_messages: list[dict[str, Any]] = []
        if current_payload is not None and isinstance(current_payload, dict):
            current_metadata = projection_metadata[len(history_turns)]
            metadata_source_ids = {
                str(item or "").strip()
                for item in list(current_metadata.get("source_ids") or [])
                if str(item or "").strip()
            }
            if metadata_source_ids != current_ids:
                return {
                    "ok": False,
                    "status": "unavailable",
                    "reason": "context_surface_projection_metadata_mismatch",
                }
            current_turn_messages.append(
                {
                    "payload": dict(current_payload),
                    "source_ids": list(current_ids),
                    "turn_id": str(current_metadata.get("turn_id") or ""),
                    "projection_index": int(current_metadata.get("projection_index", -1)),
                    "projection_status": str(current_metadata.get("projection_status") or "complete"),
                    "projection_version": int(current_metadata.get("projection_version") or 0),
                }
            )
        current_turn_messages.extend(
            {
                "payload": payload,
                "source_ids": list(active_ids[index]) if index < len(active_ids) else [],
                "turn_id": str(projection_metadata[active_start + index].get("turn_id") or ""),
                "projection_index": int(projection_metadata[active_start + index].get("projection_index", -1)),
                "projection_status": str(
                    projection_metadata[active_start + index].get("projection_status") or "complete"
                ),
                "projection_version": int(projection_metadata[active_start + index].get("projection_version") or 0),
            }
            for index, payload in enumerate(active_payloads)
        )
        if any(
            int(message.get("projection_index", -1)) < 0 or int(message.get("projection_version") or 0) < 1
            for message in current_turn_messages
            if message.get("source_ids")
        ):
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "context_surface_projection_metadata_invalid",
            }
        history_source_ids = [
            str(source_id)
            for group in source_ids[: len(history_turns)]
            for source_id in group
            if str(source_id or "").strip()
        ]
        return {
            "ok": True,
            "status": "active",
            "reason": "",
            "provider_profile": str(surface.get("provider_profile") or ""),
            "history_turns": history_turns,
            "history_message_source_ids": [list(group or []) for group in source_ids[: len(history_turns)]],
            "current_turn_id": str(surface.get("current_turn_id") or ""),
            "current_turn_messages": current_turn_messages,
            "current_message": dict(current_payload) if isinstance(current_payload, dict) else None,
            "active_turn_messages": active_payloads,
            "active_turn_message_source_ids": [list(group or []) for group in active_ids],
            "source_ids": list(dict.fromkeys(history_source_ids)),
            "source_count": len(set(history_source_ids)),
            "message_count": len(history_turns),
            "stable_prefix_hash": str(surface.get("projection_hash") or ""),
            "projection_version": int(surface.get("projection_version") or 1),
            "compaction_generation": int(surface.get("compaction_generation") or 0),
            "projection_generation": int(surface.get("projection_generation") or 0),
            "has_compact_history": bool(surface.get("has_compact_history")),
            "has_compact_arguments_history": bool(surface.get("has_compact_arguments_history")),
            "current_source_visible": current_source_visible,
        }
    except Exception as exc:
        logger.warning("memcore projection read unavailable: %s", exc.__class__.__name__)
        return {"ok": False, "status": "failed", "reason": "projection_read_failed"}


def _projection_failure_context(projection: dict[str, Any], *, prompt_scope: str) -> dict[str, Any]:
    status = str((projection or {}).get("status") or "unavailable").strip()[:40] or "unavailable"
    reason = str((projection or {}).get("reason") or "projection_unavailable").strip()[:120]
    raw_detail = str((projection or {}).get("detail") or "").strip()
    detail = _safe_projection_failure_code(raw_detail) if raw_detail else ""
    return {
        "memcore_projection_failure": {
            "status": status,
            "reason": reason or "projection_unavailable",
            **({"detail": detail} if detail else {}),
        },
        "prompt_scope": str(prompt_scope or "").strip(),
    }


def _memory_backend() -> str:
    backend = str(getattr(mod_config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"


@dataclass
class ResponseProjection:
    """Request-local history and current-turn workset, never cached on the engine."""

    excluded_prompt_sources: set[str]
    current_record: dict[str, Any]
    current_source_id: str
    current_message_text: str
    raw_records: list[dict[str, Any]]
    provider_projection: dict[str, Any]
    projection_read_active: bool
    projection_migration_window: bool
    projection_authoritative: bool
    projection_recovery_failure: dict[str, Any] | None
    event_timeline_authoritative: bool
    raw_text: str = ""
    authoritative_history_turns: list[dict[str, Any]] = field(default_factory=list)
    effective_post_user_turns: list[dict[str, Any]] = field(default_factory=list)

    def read(self, engine: Any, request: ResponseRequest) -> dict[str, Any]:
        return _read_provider_history(
            engine,
            request,
            current_source_id=self.current_source_id,
            excluded_prompt_sources=self.excluded_prompt_sources,
        )

    def adopt(self, provider_projection: dict[str, Any], *, update_authority: bool = False) -> None:
        self.provider_projection = provider_projection
        if update_authority:
            self.projection_read_active = bool(provider_projection.get("ok"))
            self.projection_migration_window = (
                str(provider_projection.get("reason") or "") in PROJECTION_READ_MIGRATION_REASONS
            )
            self.projection_authoritative = not self.projection_migration_window
        if self.projection_read_active and self.projection_authoritative:
            current = _projected_current_message_text(provider_projection, current_source_id=self.current_source_id)
            if current:
                self.current_message_text = current

    def prepare_turns(self, request: ResponseRequest, *, always_overlay: bool = False) -> None:
        prepared = [dict(turn) for turn in list(request.post_user_turns or []) if isinstance(turn, dict)]
        active = [
            dict(turn)
            for turn in list(self.provider_projection.get("active_turn_messages") or [])
            if isinstance(turn, dict)
        ]
        self.effective_post_user_turns = (
            _split_active_projection_turns(self.provider_projection, prepared)
            if self.projection_authoritative and (active or always_overlay)
            else prepared
        )
        self.authoritative_history_turns = (
            [dict(turn) for turn in list(self.provider_projection.get("history_turns") or [])]
            if self.projection_authoritative
            else []
        )


def _read_provider_history(
    engine: Any,
    request: ResponseRequest,
    *,
    current_source_id: str,
    excluded_prompt_sources: set[str],
) -> dict[str, Any]:
    return _build_memcore_provider_history(
        engine,
        profile_user_id=request.profile_user_id,
        session_id=request.session_id,
        character_pack_id=request.character_pack_id,
        current_source_id=current_source_id,
        allow_history_only=request.current_input_transient or request.prompt_scope == "qq_attention",
        chat_model_override=request.chat_model_override,
        execution_target=request.execution_target,
        exclude_source_ids=list(excluded_prompt_sources),
    )


def prepare_projection(engine: Any, request: ResponseRequest) -> ResponseProjection | dict[str, Any]:
    excluded_prompt_sources = {
        str(source_id or "").strip()
        for source_id in list(request.prompt_exclude_source_ids or [])
        if str(source_id or "").strip()
    }
    visible_recent_raw = [
        record
        for record in request.recent_raw
        if str(record.get("source_id") or "").strip() not in excluded_prompt_sources
    ]
    _history_records, current_record = engine._split_history_records(
        recent_raw=visible_recent_raw,
        user_message=request.user_message,
        now_ts=request.now_ts,
    )
    current_message_text = engine._render_current_message_line(
        current_user_record=current_record,
    )
    raw_records = list(visible_recent_raw)
    current_source_id = (
        ""
        if request.current_input_transient
        else str(request.current_user_source_id or current_record.get("source_id") or "").strip()
    )
    provider_projection = _read_provider_history(
        engine,
        request,
        current_source_id=current_source_id,
        excluded_prompt_sources=excluded_prompt_sources,
    )
    projection_read_active = bool(provider_projection.get("ok"))
    projection_migration_window = str(provider_projection.get("reason") or "") in PROJECTION_READ_MIGRATION_REASONS
    projection_authoritative = not projection_migration_window
    projection_recovery_failure: dict[str, Any] | None = None
    if _memory_backend() == "memcore" and not projection_read_active and not projection_migration_window:
        logger.warning(
            "memcore projection unavailable status=%s reason=%s detail=%s current_source=%s",
            str(provider_projection.get("status") or "unavailable")[:40],
            str(provider_projection.get("reason") or "projection_unavailable")[:120],
            _safe_projection_failure_code(provider_projection.get("detail"), fallback="none"),
            "present" if current_source_id else "missing",
        )
        # A completed tool action/result pair is already an exact, bounded
        # provider continuation.  If the broader MemCore history projection is
        # temporarily unavailable, preserve the current turn instead of
        # discarding the successful tool work.  This recovery is deliberately
        # narrow: it carries no legacy/guessed history, only the frozen current
        # stimulus and the explicit post-user tool turns supplied by the host.
        if current_source_id and any(isinstance(turn, dict) for turn in list(request.post_user_turns or [])):
            projection_recovery_failure = dict(
                _projection_failure_context(
                    provider_projection,
                    prompt_scope=request.prompt_scope,
                )["memcore_projection_failure"]
            )
            provider_projection = {
                "ok": True,
                "status": "current_turn_recovery",
                "reason": "",
                "provider_profile": "",
                "history_turns": [],
                "current_turn_id": "",
                "current_turn_messages": [],
                "source_ids": [],
                "source_count": 0,
                "message_count": 0,
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
                "current_source_visible": True,
            }
            projection_read_active = True
            projection_authoritative = True
            logger.warning(
                "memcore current-turn continuation recovery activated reason=%s detail=%s",
                str(projection_recovery_failure.get("reason") or "projection_unavailable")[:120],
                _safe_projection_failure_code(projection_recovery_failure.get("detail"), fallback="none"),
            )
        else:
            return _projection_failure_context(provider_projection, prompt_scope=request.prompt_scope)
    if projection_read_active and projection_authoritative:
        projected_current_message = _projected_current_message_text(
            provider_projection,
            current_source_id=current_source_id,
        )
        if projected_current_message:
            current_message_text = projected_current_message
    event_timeline_authoritative = bool(projection_read_active and projection_authoritative)
    return ResponseProjection(
        excluded_prompt_sources=excluded_prompt_sources,
        current_record=current_record,
        current_source_id=current_source_id,
        current_message_text=current_message_text,
        raw_records=raw_records,
        provider_projection=provider_projection,
        projection_read_active=projection_read_active,
        projection_migration_window=projection_migration_window,
        projection_authoritative=projection_authoritative,
        projection_recovery_failure=projection_recovery_failure,
        event_timeline_authoritative=event_timeline_authoritative,
    )
