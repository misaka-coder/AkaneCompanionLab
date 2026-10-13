"""Immutable generation context, turn resolution and recovery event factories."""
from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from voicecore import VoiceEvent

from ..host import AkaneVoiceRuntimeHost
from ..stream_bridge import VoiceStreamEventFactory


@dataclass(frozen=True)
class VoiceThinkingStartResult:
    status: str
    reason: str = ""
    retryable: bool = False
    safe_public_summary: str = ""
    response_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"started", "duplicate", "completed"}


@dataclass(frozen=True)
class _GenerationJob:
    response_id: str
    voice_turn_id: str
    response_generation: int
    turn_revision: int
    commitment: str
    candidate_id: str
    source_id: str
    memcore_turn_id: str
    message: str
    timestamp: int
    event_factory: VoiceStreamEventFactory
    speech_delivery_enabled: bool = False
    delivery_notifier: Callable[[], None] | None = None
    presentation_sink: Callable[[str, Mapping[str, Any]], None] | None = None


@dataclass
class _GenerationFailure:
    host: AkaneVoiceRuntimeHost
    job: _GenerationJob
    reason: str
    retryable: bool
    event: VoiceEvent | None = None


def _safe_failure_reason(value: Any) -> str:
    reason = str(value or "")
    return reason if re.fullmatch(r"[a-z][a-z0-9_]{0,159}", reason) else "voice_thinking_failure_dispatch_failed"


class GenerationContext:
    """Turn resolution and callback lookup share the owning executor state."""

    def _resolve_turn_context(
        self,
        *,
        snapshot_record: Mapping[str, Any],
        voice_turn_id: str,
    ) -> dict[str, Any]:
        input_turns = snapshot_record.get("input_turns")
        turn = input_turns.get(voice_turn_id) if isinstance(input_turns, Mapping) else None
        projection_id = str(turn.get("committed_projection_id") or "").strip() if isinstance(turn, Mapping) else ""
        resolver = getattr(
            self.memcore_manager,
            "resolve_voice_projection_turn",
            None,
        )
        if not projection_id or not callable(resolver):
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "voice_thinking_turn_context_unavailable",
            }
        try:
            result = resolver(
                projection_id=projection_id,
                voice_turn_id=voice_turn_id,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
            )
        except Exception:
            return {
                "ok": False,
                "status": "failed",
                "reason": "voice_thinking_turn_context_read_failed",
            }
        return (
            dict(result)
            if isinstance(result, Mapping)
            else {
                "ok": False,
                "status": "failed",
                "reason": "voice_thinking_turn_context_result_invalid",
            }
        )

    @staticmethod
    def _resolve_candidate_turn_context(
        *,
        snapshot_record: Mapping[str, Any],
        voice_turn_id: str,
        turn_revision: int,
    ) -> dict[str, Any]:
        input_turns = snapshot_record.get("input_turns")
        turn = input_turns.get(voice_turn_id) if isinstance(input_turns, Mapping) else None
        revisions = turn.get("revisions") if isinstance(turn, Mapping) else None
        revision = None
        if isinstance(revisions, Mapping):
            revision = revisions.get(str(turn_revision), revisions.get(turn_revision))
        if not isinstance(revision, Mapping):
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "voice_candidate_revision_unavailable",
            }
        stable_text = str(revision.get("stable_text") or "")
        unstable_tail = str(revision.get("unstable_tail") or "")
        message = f"{stable_text}{unstable_tail}".strip()
        if not message:
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "voice_candidate_text_unavailable",
            }
        return {
            "ok": True,
            "status": "ready",
            "source_id": f"voice-candidate:{voice_turn_id}:{turn_revision}",
            "turn_id": "",
            "text": message,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }

    def _event_factory_for_turn(
        self,
        voice_turn_id: str,
    ) -> VoiceStreamEventFactory:
        with self._guard:
            factory = self._event_factories.get(voice_turn_id)
        return factory or _RecoveryVoiceEventFactory(
            conversation_id=self.conversation_id,
            conversation_generation=self.conversation_generation,
            voice_turn_id=voice_turn_id,
        )

    def _speech_delivery_enabled(self, voice_turn_id: str) -> bool:
        with self._guard:
            return bool(self._speech_delivery_by_turn.get(voice_turn_id, False))

    def _delivery_notifier(
        self,
        voice_turn_id: str,
    ) -> Callable[[], None] | None:
        with self._guard:
            return self._delivery_notifiers.get(voice_turn_id)

    @staticmethod
    def _notify_delivery(job: _GenerationJob) -> None:
        if job.delivery_notifier is None:
            return
        try:
            job.delivery_notifier()
        except Exception:
            pass


class _RecoveryVoiceEventFactory:
    def __init__(
        self,
        *,
        conversation_id: str,
        conversation_generation: int,
        voice_turn_id: str,
    ) -> None:
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        digest = hashlib.sha256(f"{self.conversation_id}:{voice_turn_id}".encode("utf-8")).hexdigest()
        self.voice_session_id = f"voice_recovery_{digest[:32]}"

    def make(self, event_kind: str, **overrides: Any) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=str(overrides.pop("event_id", f"voice_evt_{uuid.uuid4().hex}")),
            event_kind=str(event_kind or ""),
            occurred_at=str(overrides.pop("occurred_at", now)),
            recorded_at=str(overrides.pop("recorded_at", now)),
            conversation_id=str(overrides.pop("conversation_id", self.conversation_id)),
            voice_session_id=str(overrides.pop("voice_session_id", self.voice_session_id)),
            conversation_generation=int(
                overrides.pop(
                    "conversation_generation",
                    self.conversation_generation,
                )
            ),
            producer=str(overrides.pop("producer", "akane.voice_thinking_recovery")),
            **overrides,
        )
