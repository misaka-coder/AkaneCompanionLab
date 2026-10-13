"""Realtime voice wire contracts and host-neutral request parsing."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping

from .playback_delivery import VOICE_PLAYBACK_OUTPUT_MODE


VOICE_REALTIME_PROTOCOL_VERSION = 1


VOICE_REALTIME_CALL_PROTOCOL_VERSION = 2


VOICE_REALTIME_MAX_FRAME_BYTES = 1024 * 1024


VOICE_REALTIME_FINALIZE_TIMEOUT_SECONDS = 12.0


VOICE_REALTIME_INPUT_INACTIVITY_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class VoiceRealtimeOpenRequest:
    profile_user_id: str
    conversation_id: str
    session_id: str
    voice_turn_id: str
    audio_stream_id: str
    disposition: str
    language: str
    character_pack_id: str
    input_format: str
    sample_rate: int
    channels: int
    output_mode: str = "text_only"
    # Host-issued after websocket authentication; never parsed from client JSON.
    conversation_ref: str = ""


@dataclass(frozen=True)
class VoiceRealtimeCoordinatorResolution:
    status: str
    reason: str = ""
    coordinator: Any | None = None
    provider_id: str = ""
    voice_session_id: str = ""
    retryable: bool = False
    safe_public_summary: str = ""
    delivery_channel: Any | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.coordinator is not None

    @classmethod
    def succeeded(
        cls,
        coordinator: Any,
        *,
        provider_id: str = "",
        voice_session_id: str = "",
        delivery_channel: Any | None = None,
    ) -> VoiceRealtimeCoordinatorResolution:
        return cls(
            status="ready",
            coordinator=coordinator,
            provider_id=str(provider_id or ""),
            voice_session_id=str(voice_session_id or ""),
            delivery_channel=delivery_channel,
        )

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        status: str = "unavailable",
        retryable: bool = False,
        safe_public_summary: str = "",
    ) -> VoiceRealtimeCoordinatorResolution:
        return cls(
            status=status,
            reason=str(reason or "voice_realtime_unavailable"),
            retryable=bool(retryable),
            safe_public_summary=str(safe_public_summary or ""),
        )


VoiceRealtimeCoordinatorFactory = Callable[
    [VoiceRealtimeOpenRequest],
    VoiceRealtimeCoordinatorResolution | Awaitable[VoiceRealtimeCoordinatorResolution],
]


VoiceRealtimeCallFactory = Callable[
    [VoiceRealtimeOpenRequest],
    Any | Awaitable[Any],
]


def _parse_open_request(
    payload: Mapping[str, Any],
) -> tuple[VoiceRealtimeOpenRequest | None, str]:
    protocol_version = payload.get("protocol_version")
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version != VOICE_REALTIME_PROTOCOL_VERSION
    ):
        return None, "voice_realtime_protocol_version_unsupported"

    profile_user_id = _bounded_text(payload.get("profile_user_id"), 160)
    conversation_id = _bounded_text(payload.get("conversation_id"), 160)
    session_id = _bounded_text(payload.get("session_id"), 160)
    if not profile_user_id or not conversation_id or not session_id:
        return None, "voice_realtime_identity_missing"

    input_spec = payload.get("input")
    if not isinstance(input_spec, Mapping):
        return None, "voice_realtime_input_invalid"
    input_format = str(input_spec.get("format") or "").strip().lower()
    sample_rate = input_spec.get("sample_rate")
    channels = input_spec.get("channels")
    if input_format not in {"s16le", "f32le"}:
        return None, "voice_realtime_input_format_unsupported"
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or not 8000 <= sample_rate <= 192000
        or isinstance(channels, bool)
        or not isinstance(channels, int)
        or not 1 <= channels <= 8
    ):
        return None, "voice_realtime_input_invalid"

    language = _bounded_text(payload.get("language"), 32, allow_empty=True)
    if language is None:
        return None, "voice_realtime_language_invalid"
    disposition = str(payload.get("disposition") or "message").strip()
    if disposition not in {"message", "interaction"}:
        return None, "voice_asr_disposition_invalid"
    character_pack_id = _bounded_text(
        payload.get("character_pack_id"),
        160,
        allow_empty=True,
    )
    if character_pack_id is None:
        return None, "voice_realtime_character_pack_invalid"

    output_spec = payload.get("output")
    output_mode = "text_only"
    if output_spec is not None:
        if not isinstance(output_spec, Mapping):
            return None, "voice_realtime_output_invalid"
        output_mode = str(output_spec.get("mode") or "text_only").strip()
        if output_mode not in {"text_only", VOICE_PLAYBACK_OUTPUT_MODE}:
            return None, "voice_realtime_output_mode_unsupported"

    voice_turn_id = _bounded_text(payload.get("voice_turn_id"), 160, allow_empty=True)
    audio_stream_id = _bounded_text(payload.get("audio_stream_id"), 160, allow_empty=True)
    if voice_turn_id is None or audio_stream_id is None:
        return None, "voice_realtime_identity_invalid"
    if not voice_turn_id:
        voice_turn_id = f"voice_turn_{uuid.uuid4().hex}"
    if not audio_stream_id:
        audio_stream_id = f"audio_stream_{uuid.uuid4().hex}"

    return (
        VoiceRealtimeOpenRequest(
            profile_user_id=profile_user_id,
            conversation_id=conversation_id,
            session_id=session_id,
            voice_turn_id=voice_turn_id,
            audio_stream_id=audio_stream_id,
            disposition=disposition,
            language=language,
            character_pack_id=character_pack_id,
            input_format=input_format,
            sample_rate=sample_rate,
            channels=channels,
            output_mode=output_mode,
        ),
        "",
    )


def _parse_call_open_request(
    payload: Mapping[str, Any],
) -> tuple[VoiceRealtimeOpenRequest | None, str]:
    protocol_version = payload.get("protocol_version")
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version != VOICE_REALTIME_CALL_PROTOCOL_VERSION
    ):
        return None, "voice_realtime_protocol_version_unsupported"
    normalized = dict(payload)
    normalized["type"] = "client.open"
    normalized["protocol_version"] = VOICE_REALTIME_PROTOCOL_VERSION
    normalized.pop("voice_turn_id", None)
    normalized.pop("audio_stream_id", None)
    return _parse_open_request(normalized)


def _parse_turn_start_request(
    call_request: VoiceRealtimeOpenRequest,
    payload: Mapping[str, Any],
) -> tuple[VoiceRealtimeOpenRequest | None, str]:
    protocol_version = payload.get(
        "protocol_version",
        VOICE_REALTIME_CALL_PROTOCOL_VERSION,
    )
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version != VOICE_REALTIME_CALL_PROTOCOL_VERSION
    ):
        return None, "voice_realtime_protocol_version_unsupported"
    voice_turn_id = _bounded_text(payload.get("voice_turn_id"), 160)
    audio_stream_id = _bounded_text(payload.get("audio_stream_id"), 160)
    if not voice_turn_id or not audio_stream_id:
        return None, "voice_realtime_turn_identity_missing"
    return (
        replace(
            call_request,
            voice_turn_id=voice_turn_id,
            audio_stream_id=audio_stream_id,
        ),
        "",
    )


def _websocket_json_message_type(message: Mapping[str, Any]) -> str:
    raw = message.get("text")
    if raw is None:
        return ""
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("type") or "")


def _bounded_text(
    value: Any,
    limit: int,
    *,
    allow_empty: bool = False,
) -> str | None:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return "" if allow_empty else None
    if len(normalized) > limit or any(ord(character) < 32 for character in normalized):
        return None
    return normalized


def parse_audio_metadata(payload: Mapping[str, Any]) -> tuple[int, int] | None:
    """Require non-negative integer sequence and clock, excluding booleans."""
    sequence = payload.get("sequence")
    audio_clock_ms = payload.get("audio_clock_ms")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or isinstance(audio_clock_ms, bool)
        or not isinstance(audio_clock_ms, int)
        or audio_clock_ms < 0
    ):
        return None
    return sequence, audio_clock_ms
