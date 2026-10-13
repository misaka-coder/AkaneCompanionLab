"""Client enqueue/start/terminal receipts and truthful VoiceCore observations."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from .contracts import VoicePlaybackDeliveryResult, _DeliveryRecord, _safe_optional_text, _safe_reason


class PlaybackReceipts:
    __slots__ = ()

    def acknowledge(
        self,
        message_type: str,
        payload: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryResult:
        if not isinstance(payload, Mapping):
            return VoicePlaybackDeliveryResult(status="failed", reason="voice_playback_ack_contract_invalid")
        delivery_id = str(payload.get("delivery_id") or "")
        with self._guard:
            control = message_type == "client.playback.control_ack"
            record = (
                self._controls.get(str(payload.get("control_id") or "")) if control else self._records.get(delivery_id)
            )
            if record is None:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_control_unknown" if control else "voice_playback_delivery_unknown",
                )
            if self._closed and record.state != "terminal":
                return VoicePlaybackDeliveryResult(status="closed", reason="voice_playback_channel_closed")
            keys = (record.request.control_id, record.request.delivery_id) if control else (delivery_id,)
            if not self._reserve_receipt(keys):
                return self._receipt_in_progress()
        try:
            if control:
                result = self._ack_control(payload)
            elif message_type == "client.playback.enqueued":
                result = self._ack_enqueued(record)
            elif message_type == "client.playback.started":
                result = self._ack_started(record, payload)
            elif message_type in {"client.playback.completed", "client.playback.interrupted", "client.playback.failed"}:
                result = self._ack_terminal(record, payload, kind=message_type.rsplit(".", 1)[1])
            else:
                result = VoicePlaybackDeliveryResult(status="failed", reason="voice_playback_ack_type_unsupported")
        finally:
            self._release_receipt(keys)
            if self.closed:
                self.close(reason=self._close_reason)
        return self._with_outcome(result)

    def _ack_enqueued(self, record: _DeliveryRecord) -> VoicePlaybackDeliveryResult:
        with self._guard:
            if record.enqueued_acknowledged:
                return self._with_outcome(
                    VoicePlaybackDeliveryResult(
                        status="duplicate",
                        reason="voice_playback_enqueue_already_acknowledged",
                        request=record.request,
                    )
                )
            if record.state == "terminal":
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_enqueue_ack_out_of_order",
                    request=record.request,
                )
            if record.state != "sent":
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_enqueue_ack_too_early",
                    request=record.request,
                )
        event = self._event(
            record.request,
            stage="enqueued",
            event_kind="voice.playback.enqueued",
            payload={"command_id": record.request.command_id},
        )
        dispatched = self._accept_observation(
            event,
            failure_reason="voice_playback_enqueue_dispatch_failed",
            request=record.request,
        )
        if not dispatched.ok:
            return dispatched
        with self._guard:
            record.state = "enqueued"
            record.enqueued_acknowledged = True
        self.notify_runtime_change()
        return self._with_outcome(VoicePlaybackDeliveryResult(status="accepted", request=record.request))

    def _ack_started(
        self,
        record: _DeliveryRecord,
        payload: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryResult:
        resume_token = _safe_optional_text(payload.get("resume_token"), limit=160)
        if resume_token is None:
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason="voice_playback_resume_token_invalid",
                request=record.request,
            )
        with self._guard:
            if record.state == "started":
                if record.started_resume_token == resume_token:
                    return self._with_outcome(
                        VoicePlaybackDeliveryResult(
                            status="duplicate",
                            reason="voice_playback_start_already_acknowledged",
                            request=record.request,
                        )
                    )
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_start_ack_conflict",
                    request=record.request,
                )
            if record.state != "enqueued":
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_start_ack_out_of_order",
                    request=record.request,
                )
        event_payload = {"resume_token": resume_token} if resume_token else {}
        dispatched = self._accept_observation(
            self._event(
                record.request,
                stage="started",
                event_kind="voice.playback.started",
                payload=event_payload,
            ),
            failure_reason="voice_playback_start_dispatch_failed",
            request=record.request,
        )
        if not dispatched.ok:
            return dispatched
        with self._guard:
            record.state = "started"
            record.started_resume_token = resume_token
        self.notify_runtime_change()
        return self._with_outcome(VoicePlaybackDeliveryResult(status="accepted", request=record.request))

    def _ack_terminal(
        self,
        record: _DeliveryRecord,
        payload: Mapping[str, Any],
        *,
        kind: str,
    ) -> VoicePlaybackDeliveryResult:
        played_ms = payload.get("played_ms")
        if played_ms is not None and (isinstance(played_ms, bool) or not isinstance(played_ms, int) or played_ms < 0):
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason="voice_playback_played_ms_invalid",
                request=record.request,
            )
        reason = _safe_reason(payload.get("reason"), fallback=kind)
        with self._guard:
            if record.state == "terminal":
                if (
                    record.terminal_kind == kind
                    and record.terminal_played_ms == played_ms
                    and record.terminal_reason == reason
                ):
                    return self._with_outcome(
                        VoicePlaybackDeliveryResult(
                            status="duplicate",
                            reason="voice_playback_terminal_already_acknowledged",
                            request=record.request,
                        )
                    )
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_terminal_ack_conflict",
                    request=record.request,
                )
            allowed = {"started"} if kind == "completed" else {"sent", "enqueued", "started"}
            if record.state not in allowed:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_terminal_ack_out_of_order",
                    request=record.request,
                )
        result = self._dispatch_terminal(
            record,
            kind=kind,
            played_ms=played_ms,
            reason=reason,
        )
        if result.status == "accepted":
            self._drive_available_commands()
            self.notify_runtime_change()
        return self._with_outcome(result)

    def _dispatch_terminal(
        self,
        record: _DeliveryRecord,
        *,
        kind: str,
        played_ms: int | None,
        reason: str,
    ) -> VoicePlaybackDeliveryResult:
        event_kind = {
            "completed": "voice.playback.completed",
            "interrupted": "voice.playback.interrupted",
            "failed": "voice.playback.failed",
        }[kind]
        payload: dict[str, Any] = {}
        if played_ms is not None:
            payload["played_ms"] = played_ms
        if kind in {"interrupted", "failed"}:
            payload["reason_code"] = reason
        if record.state in {"offered", "sent"}:
            payload["command_id"] = record.request.command_id
        dispatched = self._accept_observation(
            self._event(
                record.request,
                stage=kind,
                event_kind=event_kind,
                payload=payload,
            ),
            failure_reason="voice_playback_terminal_dispatch_failed",
            request=record.request,
        )
        if not dispatched.ok:
            return dispatched
        with self._guard:
            record.state = "terminal"
            record.terminal_kind = kind
            record.terminal_played_ms = played_ms
            record.terminal_reason = reason
        return VoicePlaybackDeliveryResult(status="accepted", request=record.request)
