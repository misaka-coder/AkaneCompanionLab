"""Playback control offers, target resolution and client control acknowledgements."""

from __future__ import annotations
import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from voicecore import VoiceEvent
from .contracts import (
    VoicePlaybackControlRequest,
    VoicePlaybackDeliveryResult,
    _ControlRecord,
    _safe_optional_text,
    _safe_reason,
    _playback_key_digest,
    _snapshot_identity_matches,
)


class PlaybackControls:
    __slots__ = ()

    def offer_control(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryResult:
        resolved = self._resolve_control_request(command_record, snapshot_record)
        if isinstance(resolved, str):
            return VoicePlaybackDeliveryResult(status="failed", reason=resolved)
        request = resolved
        with self._guard:
            if self._closed:
                return VoicePlaybackDeliveryResult(
                    status="closed",
                    reason="voice_playback_channel_closed",
                )
            existing_id = self._control_by_command_id.get(request.command_id) or (
                request.control_id if request.control_id in self._controls else ""
            )
            if existing_id:
                existing = self._controls[existing_id]
                if existing.request != request:
                    return VoicePlaybackDeliveryResult(
                        status="failed",
                        reason="voice_playback_control_conflict",
                    )
                return VoicePlaybackDeliveryResult(
                    status="duplicate",
                    reason="voice_playback_control_already_offered",
                )
            self._controls[request.control_id] = _ControlRecord(request=request)
            self._control_by_command_id[request.command_id] = request.control_id
            self._control_outbound.append(request.control_id)
        self.notify_runtime_change()
        return VoicePlaybackDeliveryResult(status="offered")

    def take_control_outbound(self) -> VoicePlaybackControlRequest | None:
        with self._guard:
            if self._closed:
                return None
            while self._control_outbound:
                control_id = self._control_outbound.popleft()
                record = self._controls.get(control_id)
                if record is not None and record.state == "offered":
                    return record.request
        return None

    def mark_control_sent(self, control_id: str) -> VoicePlaybackDeliveryResult:
        with self._guard:
            if self._closed:
                return VoicePlaybackDeliveryResult(status="closed", reason="voice_playback_channel_closed")
            record = self._controls.get(str(control_id or ""))
            if record is None:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_unknown",
                )
            if record.state == "sent":
                return VoicePlaybackDeliveryResult(
                    status="duplicate",
                    reason="voice_playback_control_already_sent",
                )
            if record.state != "offered":
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_send_state_invalid",
                )
            record.state = "sent"
        return VoicePlaybackDeliveryResult(status="sent")

    def _ack_control(self, payload: Mapping[str, Any]) -> VoicePlaybackDeliveryResult:
        control_id = str(payload.get("control_id") or "")
        command_id = str(payload.get("command_id") or "")
        action = str(payload.get("action") or "")
        status = str(payload.get("status") or "")
        played_ms = payload.get("played_ms")
        applied_volume = payload.get("applied_volume")
        if played_ms is not None and (isinstance(played_ms, bool) or not isinstance(played_ms, int) or played_ms < 0):
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason="voice_playback_played_ms_invalid",
            )
        if applied_volume is not None and (
            isinstance(applied_volume, bool)
            or not isinstance(applied_volume, (int, float))
            or not 0 <= applied_volume <= 1
        ):
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason="voice_playback_applied_volume_invalid",
            )
        reason = _safe_reason(payload.get("reason"), fallback="control_failed")
        with self._guard:
            record = self._controls.get(control_id)
            if record is None:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_unknown",
                )
            request = record.request
            if command_id != request.command_id or action != request.action or status not in {"applied", "failed"}:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_ack_conflict",
                )
            if record.state == "terminal":
                if (
                    record.ack_status == status
                    and record.ack_reason == (reason if status == "failed" else "")
                    and record.ack_played_ms == played_ms
                    and record.ack_applied_volume == (float(applied_volume) if applied_volume is not None else None)
                ):
                    return self._with_outcome(
                        VoicePlaybackDeliveryResult(
                            status="duplicate",
                            reason="voice_playback_control_already_acknowledged",
                        )
                    )
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_ack_conflict",
                )
            if record.state != "sent":
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_ack_out_of_order",
                )

        dispatched = self._dispatch_control(
            record,
            status=status,
            reason=reason if status == "failed" else "",
            played_ms=played_ms,
            applied_volume=(float(applied_volume) if applied_volume is not None else None),
        )
        if not dispatched.ok:
            return dispatched
        with self._guard:
            record.state = "terminal"
            record.ack_status = status
            record.ack_reason = reason if status == "failed" else ""
            record.ack_played_ms = played_ms
            record.ack_applied_volume = float(applied_volume) if applied_volume is not None else None
        self._drive_available_commands()
        self.notify_runtime_change()
        return self._with_outcome(VoicePlaybackDeliveryResult(status="accepted"))

    def _dispatch_control(
        self,
        record: _ControlRecord,
        *,
        status: str,
        reason: str,
        played_ms: int | None,
        applied_volume: float | None,
    ) -> VoicePlaybackDeliveryResult:
        request = record.request
        payload: dict[str, Any] = {
            "command_id": request.command_id,
            "action": request.action,
            "status": status,
        }
        if played_ms is not None:
            payload["played_ms"] = played_ms
        if applied_volume is not None:
            payload["applied_volume"] = applied_volume
        if request.resume_token:
            payload["resume_token"] = request.resume_token
        if request.interruption_id:
            payload["interruption_id"] = request.interruption_id
        if status == "failed":
            payload["reason_code"] = reason
        elif request.action == "stop":
            payload["reason_code"] = request.reason or "interrupted"
        event = self._control_event(request, payload=payload)
        dispatched = self._accept_observation(event, failure_reason="voice_playback_control_dispatch_failed")
        if not dispatched.ok:
            return dispatched
        if request.action == "stop" and status == "applied":
            with self._guard:
                delivery = self._records.get(request.delivery_id)
                if delivery is not None:
                    delivery.state = "terminal"
                    delivery.terminal_kind = "interrupted"
                    delivery.terminal_played_ms = played_ms
                    delivery.terminal_reason = request.reason or "interrupted"
        return VoicePlaybackDeliveryResult(status="accepted")

    def _resolve_control_request(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoicePlaybackControlRequest | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_playback_control_contract_invalid"
        if not _snapshot_identity_matches(snapshot_record, self.conversation_id, self.conversation_generation):
            return "voice_playback_snapshot_context_invalid"
        command_id = str(command_record.get("command_id") or "")
        idempotency_key = str(command_record.get("idempotency_key") or "")
        command_kind = str(command_record.get("command_kind") or "")
        control_digest = _playback_key_digest(command_record.get("idempotency_key"))
        payload = command_record.get("payload")
        action = {
            "duck_playback": "duck",
            "resume_playback": "resume",
            "stop_playback": "stop",
        }.get(command_kind)
        if (
            not command_id
            or not idempotency_key
            or control_digest is None
            or _playback_key_digest(command_record.get("command_id")) is None
            or action is None
            or not isinstance(payload, Mapping)
        ):
            return "voice_playback_control_invalid"
        speech_unit_id = str(payload.get("speech_unit_id") or "")
        units = snapshot_record.get("speech_units")
        unit = units.get(speech_unit_id) if isinstance(units, Mapping) else None
        if not speech_unit_id or not isinstance(unit, Mapping):
            return "voice_playback_control_invalid"
        response_id = str(unit.get("response_id") or "")
        responses = snapshot_record.get("responses")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        if not response_id or not isinstance(response, Mapping):
            return "voice_playback_snapshot_context_invalid"
        voice_turn_id = str(response.get("voice_turn_id") or "")
        turn_revision = response.get("source_turn_revision")
        response_generation = response.get("response_generation")
        if (
            voice_turn_id != self.voice_turn_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
        ):
            return "voice_playback_snapshot_context_invalid"
        with self._guard:
            delivery_id = self._delivery_by_speech_unit_id.get(speech_unit_id, "")
            delivery = self._records.get(delivery_id)
        if (
            not delivery_id
            or delivery is None
            or delivery.state == "terminal"
            or delivery.request.response_id != response_id
            or delivery.request.response_generation != response_generation
            or delivery.request.turn_revision != turn_revision
            or unit.get("response_generation") != response_generation
        ):
            return "voice_playback_control_target_unavailable"
        resume_token = _safe_optional_text(payload.get("resume_token"), limit=160)
        interruption_id = _safe_optional_text(payload.get("interruption_id"), limit=160)
        if resume_token is None or interruption_id is None:
            return "voice_playback_control_invalid"
        reason = ""
        if action == "stop":
            reason = _safe_reason(payload.get("reason"), fallback="interrupted")
        return VoicePlaybackControlRequest(
            control_id=f"voice_control_{control_digest[:32]}",
            command_id=command_id,
            action=action,
            delivery_id=delivery_id,
            voice_turn_id=voice_turn_id,
            response_id=response_id,
            speech_unit_id=speech_unit_id,
            turn_revision=turn_revision,
            response_generation=response_generation,
            resume_token=resume_token,
            interruption_id=interruption_id,
            reason=reason,
        )

    def _control_event(
        self,
        request: VoicePlaybackControlRequest,
        *,
        payload: Mapping[str, Any],
    ) -> VoiceEvent:
        snapshot = self.host.snapshot
        input_turn = snapshot.input_turns.get(request.voice_turn_id)
        voice_session_id = str(getattr(input_turn, "voice_session_id", "") or "")
        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(
            f"{request.control_id}:ack:{str(payload.get('status') or '')}".encode("utf-8")
        ).hexdigest()
        return VoiceEvent(
            event_id=f"voice_evt_{digest[:32]}",
            event_kind="voice.playback.control_acknowledged",
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
