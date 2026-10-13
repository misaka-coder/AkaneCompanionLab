"""Host command routing to the original owning live channel and restart recovery."""

from __future__ import annotations
import hashlib
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from voicecore import VoiceEvent
from ..host import VoiceCommandExecutionResult
from .channel import VoicePlaybackDeliveryChannel
from .contracts import _safe_reason, _safe_optional_text, _playback_key_digest, _snapshot_identity_matches


class AkaneVoicePlaybackCommandExecutor:
    """Offer playback audio and control commands to the owning live client."""

    def __init__(self, *, conversation_id: str, conversation_generation: int) -> None:
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        self._channels: dict[str, VoicePlaybackDeliveryChannel] = {}
        self._has_registered_channel = False
        self._guard = threading.RLock()

    def register_channel(
        self,
        voice_turn_id: str,
        channel: VoicePlaybackDeliveryChannel,
    ) -> None:
        with self._guard:
            self._has_registered_channel = True
            self._channels[str(voice_turn_id or "")] = channel

    def unregister_channel(
        self,
        voice_turn_id: str,
        channel: VoicePlaybackDeliveryChannel,
    ) -> None:
        normalized_turn_id = str(voice_turn_id or "")
        with self._guard:
            if self._channels.get(normalized_turn_id) is channel:
                self._channels.pop(normalized_turn_id, None)

    def notify_runtime_change(self, voice_turn_id: str) -> None:
        with self._guard:
            channel = self._channels.get(str(voice_turn_id or ""))
        if channel is not None:
            channel.notify_runtime_change()

    def notify_all_channels(self) -> None:
        with self._guard:
            channels = tuple(self._channels.values())
        for channel in channels:
            channel.notify_runtime_change()

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._context(command_record, snapshot_record)
        if isinstance(context, str):
            if context == "voice_playback_channel_unavailable":
                return self._missing_channel_result(
                    command_record,
                    snapshot_record,
                    reason=(
                        "voice_playback_channel_unavailable"
                        if self._has_registered_channel
                        else "voice_playback_runtime_restarted"
                    ),
                )
            return VoiceCommandExecutionResult.failed(context)
        channel, command, common = context
        command_kind = str(command.get("command_kind") or "")
        offered = (
            channel.offer(command_record, snapshot_record)
            if command_kind == "enqueue_playback"
            else channel.offer_control(command_record, snapshot_record)
        )
        if offered.status in {"offered", "duplicate"}:
            return VoiceCommandExecutionResult.deferred("voice_playback_waiting_for_client_ack")
        if command_kind != "enqueue_playback":
            return VoiceCommandExecutionResult.succeeded(
                self._control_failure_event(
                    command=command,
                    common=common,
                    reason=(
                        "voice_playback_channel_closed"
                        if offered.status == "closed"
                        else _safe_reason(
                            offered.reason,
                            fallback="voice_playback_control_offer_failed",
                        )
                    ),
                )
            )
        if offered.status == "closed":
            return VoiceCommandExecutionResult.succeeded(
                self._failure_event(
                    command=command,
                    common=common,
                    reason="voice_playback_channel_closed",
                )
            )
        return VoiceCommandExecutionResult.succeeded(
            self._failure_event(
                command=command,
                common=common,
                reason=_safe_reason(
                    offered.reason,
                    fallback="voice_playback_offer_failed",
                ),
            )
        )

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._context(command_record, snapshot_record)
        if isinstance(context, str):
            if context == "voice_playback_channel_unavailable":
                return self._missing_channel_result(
                    command_record,
                    snapshot_record,
                    reason="voice_playback_runtime_restarted",
                )
            return VoiceCommandExecutionResult.unknown(context)
        channel, _command, _common = context
        if channel.closed:
            return self.execute(command_record, snapshot_record)
        return VoiceCommandExecutionResult.not_started("voice_playback_offer_safe_to_restore")

    def _context(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> tuple[VoicePlaybackDeliveryChannel, dict[str, Any], dict[str, Any]] | str:
        if not isinstance(snapshot_record, Mapping):
            return "voice_playback_command_contract_invalid"
        identity_matches = _snapshot_identity_matches(
            snapshot_record, self.conversation_id, self.conversation_generation
        )
        if {"conversation_id", "conversation_generation"} & snapshot_record.keys() and not identity_matches:
            return "voice_playback_snapshot_context_invalid"
        resolved = self._command_context(command_record, snapshot_record)
        if isinstance(resolved, str):
            return resolved
        command, common = resolved
        with self._guard:
            channel = self._channels.get(common["voice_turn_id"])
        if channel is None:
            return "voice_playback_channel_unavailable"
        if (
            not identity_matches
            or channel.conversation_id != self.conversation_id
            or channel.conversation_generation != self.conversation_generation
            or channel.voice_turn_id != common["voice_turn_id"]
        ):
            return "voice_playback_snapshot_context_invalid"
        return channel, command, common

    @staticmethod
    def _command_context(
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_playback_command_contract_invalid"
        command = dict(command_record)
        payload = command.get("payload")
        command_kind = str(command.get("command_kind") or "")
        if command_kind not in {
            "enqueue_playback",
            "duck_playback",
            "resume_playback",
            "stop_playback",
        } or not isinstance(payload, Mapping):
            return "voice_playback_command_invalid"
        if (
            _playback_key_digest(command.get("command_id")) is None
            or _playback_key_digest(command.get("idempotency_key")) is None
        ):
            return "voice_playback_command_invalid"
        responses = snapshot_record.get("responses")
        units = snapshot_record.get("speech_units")
        speech_unit_id = str(payload.get("speech_unit_id") or "")
        unit = units.get(speech_unit_id) if isinstance(units, Mapping) else None
        response_id = str(payload.get("response_id") or "")
        if command_kind != "enqueue_playback":
            response_id = str(unit.get("response_id") or "") if isinstance(unit, Mapping) else ""
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        if not isinstance(response, Mapping):
            return "voice_playback_snapshot_context_invalid"
        voice_turn_id = str(response.get("voice_turn_id") or "")
        if (
            not voice_turn_id
            or not isinstance(unit, Mapping)
            or unit.get("response_id") != response_id
            or isinstance(response.get("source_turn_revision"), bool)
            or not isinstance(response.get("source_turn_revision"), int)
            or isinstance(response.get("response_generation"), bool)
            or not isinstance(response.get("response_generation"), int)
        ):
            return "voice_playback_snapshot_context_invalid"
        input_turns = snapshot_record.get("input_turns")
        input_turn = input_turns.get(voice_turn_id) if isinstance(input_turns, Mapping) else None
        return (
            command,
            {
                "voice_turn_id": voice_turn_id,
                "response_id": response_id,
                "speech_unit_id": speech_unit_id,
                "turn_revision": response.get("source_turn_revision"),
                "response_generation": response.get("response_generation"),
                "voice_session_id": (
                    str(input_turn.get("voice_session_id") or "") if isinstance(input_turn, Mapping) else ""
                ),
            },
        )

    def _missing_channel_result(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
        *,
        reason: str,
    ) -> VoiceCommandExecutionResult:
        resolved = self._command_context(command_record, snapshot_record)
        if isinstance(resolved, str):
            return VoiceCommandExecutionResult.failed(resolved)
        command, common = resolved
        if str(command.get("command_kind") or "") == "enqueue_playback":
            event = self._failure_event(
                command=command,
                common=common,
                reason=reason,
            )
        else:
            event = self._control_failure_event(
                command=command,
                common=common,
                reason=reason,
            )
        return VoiceCommandExecutionResult.succeeded(event)

    def _failure_event(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        reason: str,
    ) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(f"{str(command.get('command_id') or '')}:playback_failed".encode("utf-8")).hexdigest()
        return VoiceEvent(
            event_id=f"voice_evt_{digest[:32]}",
            event_kind="voice.playback.failed",
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=str(common.get("voice_session_id") or ""),
            conversation_generation=self.conversation_generation,
            producer="akane.voice_playback_delivery",
            voice_turn_id=str(common["voice_turn_id"]),
            response_id=str(common["response_id"]),
            speech_unit_id=str(common["speech_unit_id"]),
            turn_revision=int(common["turn_revision"]),
            response_generation=int(common["response_generation"]),
            causation_id=str(command.get("command_id") or ""),
            payload={
                "command_id": str(command.get("command_id") or ""),
                "reason_code": reason,
            },
        )

    def _control_failure_event(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        reason: str,
    ) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        command_id = str(command.get("command_id") or "")
        payload = command.get("payload")
        command_kind = str(command.get("command_kind") or "")
        action = {
            "duck_playback": "duck",
            "resume_playback": "resume",
            "stop_playback": "stop",
        }[command_kind]
        digest = hashlib.sha256(f"{command_id}:control_failed".encode("utf-8")).hexdigest()
        event_payload: dict[str, Any] = {
            "command_id": command_id,
            "action": action,
            "status": "failed",
            "reason_code": reason,
        }
        if isinstance(payload, Mapping):
            resume_token = _safe_optional_text(payload.get("resume_token"), limit=160)
            interruption_id = _safe_optional_text(payload.get("interruption_id"), limit=160)
            if resume_token:
                event_payload["resume_token"] = resume_token
            if interruption_id:
                event_payload["interruption_id"] = interruption_id
        return VoiceEvent(
            event_id=f"voice_evt_{digest[:32]}",
            event_kind="voice.playback.control_acknowledged",
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=str(common.get("voice_session_id") or ""),
            conversation_generation=self.conversation_generation,
            producer="akane.voice_playback_delivery",
            voice_turn_id=str(common["voice_turn_id"]),
            response_id=str(common["response_id"]),
            speech_unit_id=str(common["speech_unit_id"]),
            turn_revision=int(common["turn_revision"]),
            response_generation=int(common["response_generation"]),
            causation_id=command_id,
            payload=event_payload,
        )
