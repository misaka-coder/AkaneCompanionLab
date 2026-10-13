"""Voice event creation and typed MemCore projection at the host boundary."""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from voicecore import VoiceEvent

from ..host import VoiceHostPortResult

_SAFE_REASON = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class AkaneVoiceEventFactory:
    """Create production VoiceCore events without importing its test helpers."""

    def __init__(
        self,
        *,
        conversation_id: str,
        voice_session_id: str,
        conversation_generation: int,
        producer: str = "akane.voice_runtime",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.conversation_id = str(conversation_id or "")
        self.voice_session_id = str(voice_session_id or "")
        self.conversation_generation = int(conversation_generation)
        self.producer = str(producer or "akane.voice_runtime")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def make(self, event_kind: str, **overrides: Any) -> VoiceEvent:
        now = self.clock().astimezone(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=str(overrides.pop("event_id", f"voice_evt_{uuid.uuid4().hex}")),
            event_kind=str(event_kind or ""),
            occurred_at=str(overrides.pop("occurred_at", now)),
            recorded_at=str(overrides.pop("recorded_at", now)),
            conversation_id=str(overrides.pop("conversation_id", self.conversation_id)),
            voice_session_id=str(overrides.pop("voice_session_id", self.voice_session_id)),
            conversation_generation=int(overrides.pop("conversation_generation", self.conversation_generation)),
            producer=str(overrides.pop("producer", self.producer)),
            **overrides,
        )


class MemcoreVoiceProjectionPort:
    """Thin VoiceCore projection adapter over MemcoreManager's typed writer."""

    def __init__(
        self,
        *,
        manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> None:
        self.manager = manager
        self.profile_user_id = str(profile_user_id or "")
        self.session_id = str(session_id or "")
        self.character_pack_id = str(character_pack_id or "")

    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        if not isinstance(projection_record, Mapping):
            return VoiceHostPortResult.failed("voice_projection_record_invalid")
        normalized_record = dict(projection_record)
        target = str(normalized_record.get("target") or "").strip().lower()
        kind = str(normalized_record.get("kind") or "").strip().lower()
        if target in {"host", "runtime"} and kind.startswith("voice."):
            payload = normalized_record.get("payload")
            normalized_record = {
                **normalized_record,
                "target": "memcore",
                "kind": f"event.{kind}",
                "payload": {
                    **(dict(payload) if isinstance(payload, Mapping) else {}),
                    "projection_target": target,
                    "projection_kind": kind,
                },
            }
        try:
            result = self.manager.record_voice_projection(
                normalized_record,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
                actor_stable_id=self.profile_user_id,
            )
        except Exception:
            return VoiceHostPortResult.failed(
                "voice_memcore_projection_failed",
                retryable=True,
            )
        if not isinstance(result, Mapping):
            return VoiceHostPortResult.failed("voice_memcore_projection_result_invalid")
        if bool(result.get("ok")):
            return VoiceHostPortResult.succeeded()
        status = _safe_reason(result.get("status"), fallback="failed")
        reason = _safe_reason(
            result.get("reason"),
            fallback=f"voice_memcore_projection_{status}",
        )
        return VoiceHostPortResult.failed(
            reason,
            retryable=status in {"failed", "unavailable"},
        )


def _safe_reason(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if _SAFE_REASON.fullmatch(text) else str(fallback or "voice_runtime_failed")


def _provider_public_summary(reason: str) -> str:
    if reason == "fun_asr_realtime_disabled":
        return "实时语音识别尚未开启。"
    if reason in {"dashscope_api_key_missing", "dashscope_api_host_missing"}:
        return "实时语音识别配置不完整。"
    if reason == "fun_asr_realtime_config_invalid":
        return "实时语音识别配置无效。"
    return "实时语音识别服务暂时不可用。"
