"""Playback wire/result contracts, local records and stable input helpers."""

from __future__ import annotations
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

VOICE_PLAYBACK_OUTPUT_MODE = "binary_audio_ack_v1"


@dataclass(frozen=True)
class VoicePlaybackDeliveryRequest:
    delivery_id: str
    command_id: str
    voice_turn_id: str
    response_id: str
    speech_unit_id: str
    turn_revision: int
    response_generation: int
    ordinal: int
    text: str
    audio: bytes
    media_type: str
    audio_sha256: str
    emotion: str = ""


@dataclass(frozen=True)
class VoicePlaybackControlRequest:
    control_id: str
    command_id: str
    action: str
    delivery_id: str
    voice_turn_id: str
    response_id: str
    speech_unit_id: str
    turn_revision: int
    response_generation: int
    resume_token: str = ""
    interruption_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class VoicePlaybackDeliveryResult:
    status: str
    reason: str = ""
    retryable: bool = False
    request: VoicePlaybackDeliveryRequest | None = None
    response_terminal: bool = False
    response_state: str = ""
    delivery_status: str = ""
    full_text: str = ""
    response_reason: str = ""
    response_retryable: bool = False
    safe_public_summary: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"accepted", "duplicate", "offered", "sent"}


@dataclass
class _DeliveryRecord:
    request: VoicePlaybackDeliveryRequest
    state: str = "offered"
    enqueued_acknowledged: bool = False
    started_resume_token: str = ""
    terminal_kind: str = ""
    terminal_played_ms: int | None = None
    terminal_reason: str = ""


@dataclass
class _ControlRecord:
    request: VoicePlaybackControlRequest
    state: str = "offered"
    ack_status: str = ""
    ack_reason: str = ""
    ack_played_ms: int | None = None
    ack_applied_volume: float | None = None


def _safe_optional_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) > limit or any(ord(character) < 32 for character in normalized):
        return None
    try:
        normalized.encode("utf-8")
    except UnicodeError:
        return None
    return normalized


def _playback_key_digest(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def _snapshot_identity_matches(snapshot: Mapping[str, Any], conversation_id: str, generation: int) -> bool:
    value = snapshot.get("conversation_generation")
    return (
        snapshot.get("conversation_id") == conversation_id
        and not isinstance(value, bool)
        and isinstance(value, int)
        and value == generation
    )


def _same_delivery_request(
    first: VoicePlaybackDeliveryRequest,
    second: VoicePlaybackDeliveryRequest,
) -> bool:
    return (
        first.delivery_id == second.delivery_id
        and first.command_id == second.command_id
        and first.voice_turn_id == second.voice_turn_id
        and first.response_id == second.response_id
        and first.speech_unit_id == second.speech_unit_id
        and first.turn_revision == second.turn_revision
        and first.response_generation == second.response_generation
        and first.ordinal == second.ordinal
        and first.text == second.text
        and first.emotion == second.emotion
        and first.media_type == second.media_type
        and first.audio_sha256 == second.audio_sha256
    )


def _safe_reason(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    if (
        not normalized
        or len(normalized) > 96
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.:-" for character in normalized)
    ):
        return fallback
    return normalized
