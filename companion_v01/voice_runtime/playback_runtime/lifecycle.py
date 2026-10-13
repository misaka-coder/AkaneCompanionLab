"""One live channel's wakeups, presentations, outcomes and close settlement."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from typing import Any
from .contracts import VoicePlaybackDeliveryResult, VoicePlaybackDeliveryRequest, _safe_reason


class PlaybackLifecycle:
    __slots__ = ()

    def stage_presentation(self, response_id: str, payload: Mapping[str, Any]) -> None:
        """Ephemeral UI data, fenced by the existing response lifecycle."""
        presentation: dict[str, Any] = {}
        emotion = payload.get("emotion")
        if not isinstance(payload.get("utterances"), list) and isinstance(emotion, str) and emotion.strip():
            presentation["emotion"] = emotion.strip()
        with self._guard:
            if not self._closed and response_id not in self._presentations:
                self._presentations[response_id] = presentation

    def _ready_presentation(self) -> tuple[str, dict[str, Any]] | None:
        for response_id, payload in self._presentations.items():
            response = self.host.snapshot.responses.get(response_id)
            if (
                payload
                and response_id not in self._presentation_sent
                and response is not None
                and response.voice_turn_id == self.voice_turn_id
                and response.generation_terminal
                and response.full_text is not None
                and response.commitment.value == "committed"
                and response.playable
                and not response.cancel_requested
                and not response.stop_after_current
                and response.state.value not in {"cancelling", "cancelled", "failed", "discarded"}
            ):
                return response_id, payload
        return None

    def take_presentation(self) -> dict[str, Any] | None:
        with self._guard:
            if self._closed:
                return None
            ready = self._ready_presentation()
            if ready is None:
                return None
            response_id, payload = ready
            self._presentation_sent.add(response_id)
            return {"response_id": response_id, "presentation": payload}

    async def wait_activity(self) -> None:
        loop = asyncio.get_running_loop()
        with self._guard:
            if self._loop is not loop or self._activity is None:
                self._loop = loop
                self._activity = asyncio.Event()
            activity = self._activity
            if (
                self._closed
                or self._control_outbound
                or self._outbound
                or self._ready_presentation()
                or self._response_terminal_unlocked()
                or self.interaction_committed()
            ):
                return
            activity.clear()
        await activity.wait()

    def close(self, *, reason: str) -> VoicePlaybackDeliveryResult:
        safe_reason = _safe_reason(reason, fallback="client_disconnected")
        with self._guard:
            if (
                self._closed
                and all(record.state == "terminal" for record in self._records.values())
                and all(control.state == "terminal" for control in self._controls.values())
            ):
                return self.response_outcome(status="duplicate")
            if not self._closed:
                self._close_reason = safe_reason
            self._closed = True
            safe_reason = self._close_reason
            records = list(self._records.values())
            controls = list(self._controls.values())
        failure = None
        for control in controls:
            keys = (control.request.control_id, control.request.delivery_id)
            with self._guard:
                if control.state == "terminal":
                    continue
                if not self._reserve_receipt(keys):
                    failure = failure or self._receipt_in_progress()
                    continue
            try:
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
                        control.ack_played_ms = None
                        control.ack_applied_volume = None
                else:
                    failure = failure or dispatched
            finally:
                self._release_receipt(keys)
        for record in records:
            keys = (record.request.delivery_id,)
            with self._guard:
                if record.state == "terminal":
                    continue
                if not self._reserve_receipt(keys):
                    failure = failure or self._receipt_in_progress()
                    continue
                kind = "failed" if record.state in {"offered", "sent"} else "interrupted"
            try:
                dispatched = self._dispatch_terminal(record, kind=kind, played_ms=None, reason=safe_reason)
                if not dispatched.ok:
                    failure = failure or dispatched
            finally:
                self._release_receipt(keys)
        self._drive_available_commands()
        self.notify_runtime_change()
        if self.interaction_committed():
            self._notify_terminal_once()
        if failure is not None:
            return self._with_outcome(failure)
        return self.response_outcome(status="accepted")

    def _reserve_receipt(self, keys: tuple[str, ...]) -> bool:
        with self._guard:
            if any(key in self._pending_receipts for key in keys):
                return False
            self._pending_receipts.update(keys)
            return True

    def _release_receipt(self, keys: tuple[str, ...]) -> None:
        with self._guard:
            self._pending_receipts.difference_update(keys)

    @staticmethod
    def _receipt_in_progress() -> VoicePlaybackDeliveryResult:
        return VoicePlaybackDeliveryResult(status="failed", reason="voice_playback_ack_in_progress", retryable=True)

    def _accept_observation(
        self,
        event: Any,
        *,
        failure_reason: str,
        request: VoicePlaybackDeliveryRequest | None = None,
    ) -> VoicePlaybackDeliveryResult:
        try:
            accepted = self.host.accept_event(event)
        except Exception:
            return VoicePlaybackDeliveryResult(status="failed", reason=failure_reason, retryable=True, request=request)
        if not accepted.accepted and accepted.status != "duplicate":
            return VoicePlaybackDeliveryResult(
                status="failed",
                reason=accepted.reason or failure_reason,
                retryable=(
                    bool(getattr(accepted, "retryable", False))
                    or bool(getattr(getattr(accepted, "journal_result", None), "retryable", False))
                    or accepted.status == "deferred"
                ),
                request=request,
            )
        return VoicePlaybackDeliveryResult(status="accepted", request=request)

    def notify_runtime_change(self) -> None:
        with self._guard:
            loop = self._loop
            activity = self._activity
        if loop is not None and activity is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(activity.set)
            except RuntimeError:
                if not loop.is_closed():
                    raise

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

    def interaction_committed(self) -> bool:
        """An input-only backchannel has no response/audio terminal to await."""
        turn = self.host.snapshot.input_turns.get(self.voice_turn_id)
        return (
            str(getattr(getattr(turn, "state", None), "value", "") or "") == "committed"
            and str(getattr(getattr(turn, "disposition", None), "value", "") or "") == "interaction"
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
