from __future__ import annotations

import asyncio
import hashlib
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from voicecore import VoiceEvent

from .host import VoiceCommandExecutionResult
from .tts_executor import (
    VoiceAudioArtifactPort,
    VoiceReadableTextArtifactPort,
)


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


class VoicePlaybackDeliveryChannel:
    """Bridge one live client playback queue to VoiceCore observations.

    Sending bytes is not delivery. The channel advances VoiceCore only after
    the client acknowledges enqueue, playback start, and a terminal outcome.
    """

    def __init__(
        self,
        *,
        host: Any,
        voice_turn_id: str,
        conversation_id: str,
        conversation_generation: int,
        text_artifacts: VoiceReadableTextArtifactPort,
        audio_artifacts: VoiceAudioArtifactPort,
        terminal_notifier: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.voice_turn_id = str(voice_turn_id or "")
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        self.text_artifacts = text_artifacts
        self.audio_artifacts = audio_artifacts
        self.terminal_notifier = terminal_notifier
        self._records: dict[str, _DeliveryRecord] = {}
        self._delivery_by_command_id: dict[str, str] = {}
        self._delivery_by_speech_unit_id: dict[str, str] = {}
        self._controls: dict[str, _ControlRecord] = {}
        self._control_by_command_id: dict[str, str] = {}
        self._outbound: deque[str] = deque()
        self._control_outbound: deque[str] = deque()
        self._closed = False
        self._guard = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._activity: asyncio.Event | None = None
        self._terminal_notified = False

    @property
    def closed(self) -> bool:
        with self._guard:
            return self._closed

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
            existing_id = self._delivery_by_command_id.get(request.command_id)
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
            existing_id = self._control_by_command_id.get(request.command_id)
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

    async def wait_activity(self) -> None:
        loop = asyncio.get_running_loop()
        with self._guard:
            if self._loop is not loop or self._activity is None:
                self._loop = loop
                self._activity = asyncio.Event()
            activity = self._activity
            if self._control_outbound or self._outbound or self._response_terminal_unlocked():
                return
            activity.clear()
        await activity.wait()

    def take_outbound(self) -> VoicePlaybackDeliveryRequest | None:
        with self._guard:
            while self._outbound:
                delivery_id = self._outbound.popleft()
                record = self._records.get(delivery_id)
                if record is not None and record.state == "offered":
                    return record.request
        return None

    def take_control_outbound(self) -> VoicePlaybackControlRequest | None:
        with self._guard:
            while self._control_outbound:
                control_id = self._control_outbound.popleft()
                record = self._controls.get(control_id)
                if record is not None and record.state == "offered":
                    return record.request
        return None

    def mark_sent(self, delivery_id: str) -> VoicePlaybackDeliveryResult:
        with self._guard:
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

    def mark_control_sent(self, control_id: str) -> VoicePlaybackDeliveryResult:
        with self._guard:
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

    def acknowledge(
        self,
        message_type: str,
        payload: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryResult:
        if message_type == "client.playback.control_ack":
            return self._ack_control(payload)
        delivery_id = str(payload.get("delivery_id") or "")
        with self._guard:
            record = self._records.get(delivery_id)
            if record is None:
                return VoicePlaybackDeliveryResult(
                    status="failed",
                    reason="voice_playback_delivery_unknown",
                )
        if message_type == "client.playback.enqueued":
            return self._ack_enqueued(record)
        if message_type == "client.playback.started":
            return self._ack_started(record, payload)
        if message_type == "client.playback.completed":
            return self._ack_terminal(record, payload, kind="completed")
        if message_type == "client.playback.interrupted":
            return self._ack_terminal(record, payload, kind="interrupted")
        if message_type == "client.playback.failed":
            return self._ack_terminal(record, payload, kind="failed")
        return VoicePlaybackDeliveryResult(
            status="failed",
            reason="voice_playback_ack_type_unsupported",
        )

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
            or not 0 <= float(applied_volume) <= 1
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

    def close(self, *, reason: str) -> VoicePlaybackDeliveryResult:
        safe_reason = _safe_reason(reason, fallback="client_disconnected")
        with self._guard:
            if self._closed:
                return self.response_outcome(status="duplicate")
            self._closed = True
            records = list(self._records.values())
            controls = list(self._controls.values())
        for control in controls:
            if control.state == "terminal":
                continue
            dispatched = self._dispatch_control(
                control,
                status="failed",
                reason=safe_reason,
                played_ms=None,
                applied_volume=None,
            )
            if dispatched.ok:
                with self._guard:
                    control.state = "terminal"
                    control.ack_status = "failed"
                    control.ack_reason = safe_reason
        for record in records:
            if record.state in {"terminal"}:
                continue
            if record.state in {"offered", "sent"}:
                self._dispatch_terminal(
                    record,
                    kind="failed",
                    played_ms=None,
                    reason=safe_reason,
                )
            elif record.state in {"enqueued", "started"}:
                self._dispatch_terminal(
                    record,
                    kind="interrupted",
                    played_ms=None,
                    reason=safe_reason,
                )
        self._drive_available_commands()
        self.notify_runtime_change()
        return self.response_outcome(status="accepted")

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
        accepted = self.host.accept_event(event)
        if not accepted.accepted and accepted.status != "duplicate":
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason=accepted.reason or "voice_playback_control_dispatch_failed",
            )
        if request.action == "stop" and status == "applied":
            with self._guard:
                delivery = self._records.get(request.delivery_id)
                if delivery is not None:
                    delivery.state = "terminal"
                    delivery.terminal_kind = "interrupted"
                    delivery.terminal_played_ms = played_ms
                    delivery.terminal_reason = request.reason or "interrupted"
        return VoicePlaybackDeliveryResult(status="accepted")

    def notify_runtime_change(self) -> None:
        with self._guard:
            loop = self._loop
            activity = self._activity
        if loop is not None and activity is not None and not loop.is_closed():
            loop.call_soon_threadsafe(activity.set)

    def response_outcome(self, *, status: str = "accepted") -> VoicePlaybackDeliveryResult:
        snapshot = self.host.snapshot
        response = next(
            (
                item
                for item in snapshot.responses.values()
                if str(getattr(item, "voice_turn_id", "") or "") == self.voice_turn_id
            ),
            None,
        )
        response_state = str(getattr(getattr(response, "state", None), "value", "") or "")
        failure = getattr(response, "failure", None)
        terminal = response_state in {"completed", "cancelled", "failed", "discarded"}
        delivery_status = ""
        if response_state == "completed":
            units = [snapshot.speech_units[unit_id] for unit_id in getattr(response, "unit_ids", ())]
            if not units:
                delivery_status = "text_only"
            elif all(str(getattr(unit.state, "value", "")) == "delivered" for unit in units):
                delivery_status = "delivered"
            else:
                delivery_status = "partial"
        result = VoicePlaybackDeliveryResult(
            status=status,
            response_terminal=terminal,
            response_state=response_state,
            delivery_status=delivery_status,
            full_text=str(getattr(response, "full_text", "") or ""),
            response_reason=str(getattr(failure, "reason_code", "") or ""),
            response_retryable=bool(getattr(failure, "retryable", False)),
            safe_public_summary=str(getattr(failure, "safe_public_summary", "") or ""),
        )
        if terminal:
            self._notify_terminal_once()
        return result

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
        accepted = self.host.accept_event(event)
        if not accepted.accepted and accepted.status != "duplicate":
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason=accepted.reason or "voice_playback_enqueue_dispatch_failed",
                request=record.request,
            )
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
        accepted = self.host.accept_event(
            self._event(
                record.request,
                stage="started",
                event_kind="voice.playback.started",
                payload=event_payload,
            )
        )
        if not accepted.accepted and accepted.status != "duplicate":
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason=accepted.reason or "voice_playback_start_dispatch_failed",
                request=record.request,
            )
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
        accepted = self.host.accept_event(
            self._event(
                record.request,
                stage=kind,
                event_kind=event_kind,
                payload=payload,
            )
        )
        if not accepted.accepted and accepted.status != "duplicate":
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason=accepted.reason or "voice_playback_terminal_dispatch_failed",
                request=record.request,
            )
        with self._guard:
            record.state = "terminal"
            record.terminal_kind = kind
            record.terminal_played_ms = played_ms
            record.terminal_reason = reason
        return VoicePlaybackDeliveryResult(status="accepted", request=record.request)

    def _resolve_request(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoicePlaybackDeliveryRequest | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_playback_command_contract_invalid"
        command_id = str(command_record.get("command_id") or "")
        idempotency_key = str(command_record.get("idempotency_key") or "")
        payload = command_record.get("payload")
        if (
            command_record.get("command_kind") != "enqueue_playback"
            or not command_id
            or not idempotency_key
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
        text_result = self.text_artifacts.read_text(str(unit.get("text_artifact_ref") or ""))
        audio_result = self.audio_artifacts.read_audio(audio_artifact_ref)
        if not text_result.ok:
            return "voice_playback_text_artifact_unavailable"
        if not audio_result.ok:
            return "voice_playback_audio_artifact_unavailable"
        delivery_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return VoicePlaybackDeliveryRequest(
            delivery_id=f"voice_delivery_{delivery_digest[:32]}",
            command_id=command_id,
            voice_turn_id=voice_turn_id,
            response_id=response_id,
            speech_unit_id=speech_unit_id,
            turn_revision=turn_revision,
            response_generation=response_generation,
            ordinal=ordinal,
            text=text_result.text,
            audio=audio_result.audio,
            media_type=audio_result.media_type,
            audio_sha256=hashlib.sha256(audio_result.audio).hexdigest(),
        )

    def _resolve_control_request(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoicePlaybackControlRequest | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_playback_control_contract_invalid"
        command_id = str(command_record.get("command_id") or "")
        idempotency_key = str(command_record.get("idempotency_key") or "")
        command_kind = str(command_record.get("command_kind") or "")
        payload = command_record.get("payload")
        action = {
            "duck_playback": "duck",
            "resume_playback": "resume",
            "stop_playback": "stop",
        }.get(command_kind)
        if not command_id or not idempotency_key or action is None or not isinstance(payload, Mapping):
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
        if not delivery_id or delivery is None or delivery.state == "terminal":
            return "voice_playback_control_target_unavailable"
        resume_token = _safe_optional_text(payload.get("resume_token"), limit=160)
        interruption_id = _safe_optional_text(payload.get("interruption_id"), limit=160)
        if resume_token is None or interruption_id is None:
            return "voice_playback_control_invalid"
        reason = ""
        if action == "stop":
            reason = _safe_reason(payload.get("reason"), fallback="interrupted")
        control_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
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

    def _drive_available_commands(self) -> None:
        seen: set[tuple[str, ...]] = set()
        while True:
            pending = tuple(self.host.snapshot.pending_commands)
            if not pending or pending in seen:
                return
            seen.add(pending)
            driven = self.host.drive_once()
            if driven.status != "succeeded":
                return

    def _response_terminal_unlocked(self) -> bool:
        return self.response_outcome().response_terminal

    def _notify_terminal_once(self) -> None:
        with self._guard:
            if self._terminal_notified:
                return
            self._terminal_notified = True
            notifier = self.terminal_notifier
        if notifier is None:
            return
        try:
            notifier(self.voice_turn_id)
        except Exception:
            pass

    def _with_outcome(
        self,
        result: VoicePlaybackDeliveryResult,
    ) -> VoicePlaybackDeliveryResult:
        outcome = self.response_outcome(status=result.status)
        return VoicePlaybackDeliveryResult(
            status=result.status,
            reason=result.reason,
            retryable=result.retryable,
            request=result.request,
            response_terminal=outcome.response_terminal,
            response_state=outcome.response_state,
            delivery_status=outcome.delivery_status,
            full_text=outcome.full_text,
            response_reason=outcome.response_reason,
            response_retryable=outcome.response_retryable,
            safe_public_summary=outcome.safe_public_summary,
        )


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
        resolved = self._command_context(command_record, snapshot_record)
        if isinstance(resolved, str):
            return resolved
        command, common = resolved
        with self._guard:
            channel = self._channels.get(common["voice_turn_id"])
        if channel is None:
            return "voice_playback_channel_unavailable"
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


def _safe_optional_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) > limit or any(ord(character) < 32 for character in normalized):
        return None
    return normalized


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
