"""Memcore voice projection operations."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from ..owner_private_session import memory_scope

logger = logging.getLogger("akane.memcore")


class MemcoreVoiceProjectionMixin:
    @memory_scope
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

    @memory_scope
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
                **({"channel": payload["channel"]} if payload.get("channel") == "desktop" else {}),
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
        mirror = getattr(self, "owner_private_group_memory", None)
        if mirror is not None and full_text.strip():
            mirror.prepare_completion(system=system, turn_id=turn_id, final_id=source_id,
                                      character_id=character_pack_id)
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
        if mirror is not None and getattr(result, "completed", False):
            mirror.notify_completion()
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
