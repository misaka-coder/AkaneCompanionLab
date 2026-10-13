"""Audio offers, immutable request resolution and outbound queue transitions."""

from __future__ import annotations
import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from voicecore import VoiceEvent
from .contracts import (
    VoicePlaybackDeliveryRequest,
    VoicePlaybackDeliveryResult,
    _DeliveryRecord,
    _same_delivery_request,
    _playback_key_digest,
    _snapshot_identity_matches,
)


class PlaybackDelivery:
    __slots__ = ()

    def offer(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryResult:
        resolved = self._resolve_request(command_record, snapshot_record)
        if isinstance(resolved, str):
            return VoicePlaybackDeliveryResult(status="failed", reason=resolved)
        request = resolved
        with self._guard:
            if self._closed:
                return VoicePlaybackDeliveryResult(
                    status="closed",
                    reason="voice_playback_channel_closed",
                    request=request,
                )
            existing_id = (
                self._delivery_by_command_id.get(request.command_id)
                or (request.delivery_id if request.delivery_id in self._records else "")
                or self._delivery_by_speech_unit_id.get(request.speech_unit_id)
            )
            if existing_id:
                existing = self._records[existing_id]
                if not _same_delivery_request(existing.request, request):
                    return VoicePlaybackDeliveryResult(
                        status="failed",
                        reason="voice_playback_delivery_conflict",
                    )
                return VoicePlaybackDeliveryResult(
                    status="duplicate",
                    reason="voice_playback_already_offered",
                    request=existing.request,
                )
            self._records[request.delivery_id] = _DeliveryRecord(request=request)
            self._delivery_by_command_id[request.command_id] = request.delivery_id
            self._delivery_by_speech_unit_id[request.speech_unit_id] = request.delivery_id
            self._outbound.append(request.delivery_id)
        self.notify_runtime_change()
        return VoicePlaybackDeliveryResult(status="offered", request=request)

    def take_outbound(self) -> VoicePlaybackDeliveryRequest | None:
        with self._guard:
            if self._closed:
                return None
            while self._outbound:
                delivery_id = self._outbound.popleft()
                record = self._records.get(delivery_id)
                if record is not None and record.state == "offered":
                    return record.request
        return None

    def mark_sent(self, delivery_id: str) -> VoicePlaybackDeliveryResult:
        with self._guard:
            if self._closed:
                return VoicePlaybackDeliveryResult(status="closed", reason="voice_playback_channel_closed")
            record = self._records.get(str(delivery_id or ""))
            if record is None:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_delivery_unknown",
                )
            if record.state == "sent":
                return VoicePlaybackDeliveryResult(
                    status="duplicate",
                    reason="voice_playback_audio_already_sent",
                    request=record.request,
                )
            if record.state != "offered":
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_send_state_invalid",
                    request=record.request,
                )
            record.state = "sent"
            sent_request = record.request
            record.request = replace(record.request, audio=b"")
            return VoicePlaybackDeliveryResult(status="sent", request=sent_request)

    def _resolve_request(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryRequest | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_playback_command_contract_invalid"
        if not _snapshot_identity_matches(snapshot_record, self.conversation_id, self.conversation_generation):
            return "voice_playback_snapshot_context_invalid"
        command_id = str(command_record.get("command_id") or "")
        idempotency_key = str(command_record.get("idempotency_key") or "")
        payload = command_record.get("payload")
        delivery_digest = _playback_key_digest(command_record.get("idempotency_key"))
        if (
            command_record.get("command_kind") != "enqueue_playback"
            or not command_id
            or not idempotency_key
            or delivery_digest is None
            or _playback_key_digest(command_record.get("command_id")) is None
            or not isinstance(payload, Mapping)
        ):
            return "voice_playback_command_invalid"
        response_id = str(payload.get("response_id") or "")
        speech_unit_id = str(payload.get("speech_unit_id") or "")
        audio_artifact_ref = str(payload.get("audio_artifact_ref") or "")
        response_generation = payload.get("response_generation")
        ordinal = payload.get("ordinal")
        responses = snapshot_record.get("responses")
        units = snapshot_record.get("speech_units")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        unit = units.get(speech_unit_id) if isinstance(units, Mapping) else None
        if (
            not response_id
            or not speech_unit_id
            or not audio_artifact_ref
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not isinstance(response, Mapping)
            or not isinstance(unit, Mapping)
        ):
            return "voice_playback_command_invalid"
        voice_turn_id = str(response.get("voice_turn_id") or "")
        turn_revision = response.get("source_turn_revision")
        if voice_turn_id != self.voice_turn_id or isinstance(turn_revision, bool) or not isinstance(turn_revision, int):
            return "voice_playback_snapshot_context_invalid"
        if (
            unit.get("response_id") != response_id
            or unit.get("response_generation") != response_generation
            or response.get("response_generation") != response_generation
            or unit.get("ordinal") != ordinal
            or unit.get("audio_artifact_ref") != audio_artifact_ref
        ):
            return "voice_playback_snapshot_context_invalid"
        text_result = self.text_artifacts.read_text(str(unit.get("text_artifact_ref") or ""))
        audio_result = self.audio_artifacts.read_audio(audio_artifact_ref)
        if not text_result.ok:
            return "voice_playback_text_artifact_unavailable"
        if not audio_result.ok:
            return "voice_playback_audio_artifact_unavailable"
        try:
            from ..speech_artifact import decode_speech_artifact

            spoken = decode_speech_artifact(text_result)
            display_text = spoken["text"]
        except (ValueError, TypeError):
            return "voice_playback_text_artifact_invalid"
        return VoicePlaybackDeliveryRequest(
            delivery_id=f"voice_delivery_{delivery_digest[:32]}",
            command_id=command_id,
            voice_turn_id=voice_turn_id,
            response_id=response_id,
            speech_unit_id=speech_unit_id,
            turn_revision=turn_revision,
            response_generation=response_generation,
            ordinal=ordinal,
            text=display_text,
            audio=audio_result.audio,
            media_type=audio_result.media_type,
            audio_sha256=hashlib.sha256(audio_result.audio).hexdigest(),
            emotion=spoken.get("emotion", ""),
        )

    def _event(
        self,
        request: VoicePlaybackDeliveryRequest,
        *,
        stage: str,
        event_kind: str,
        payload: Mapping[str, Any],
    ) -> VoiceEvent:
        snapshot = self.host.snapshot
        input_turn = snapshot.input_turns.get(request.voice_turn_id)
        voice_session_id = str(getattr(input_turn, "voice_session_id", "") or "")
        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(f"{request.delivery_id}:{stage}".encode("utf-8")).hexdigest()
        return VoiceEvent(
            event_id=f"voice_evt_{digest[:32]}",
            event_kind=event_kind,
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=voice_session_id,
            conversation_generation=self.conversation_generation,
            producer="akane.voice_playback_delivery",
            voice_turn_id=request.voice_turn_id,
            response_id=request.response_id,
            speech_unit_id=request.speech_unit_id,
            turn_revision=request.turn_revision,
            response_generation=request.response_generation,
            causation_id=request.command_id,
            payload=dict(payload),
        )
