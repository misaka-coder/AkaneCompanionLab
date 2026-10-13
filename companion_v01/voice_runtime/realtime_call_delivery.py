"""Route delivery notifications and receipts to their original voice turn."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping


from .realtime_protocol import (
    VOICE_REALTIME_CALL_PROTOCOL_VERSION,
)
from .realtime_events import _send_response_presentation, playback_control_payload, speech_payload


class RealtimeCallDeliveryMixin:
    """Route delivery notifications and receipts to their original voice turn."""

    async def _handle_playback_ack(
        self,
        message_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        owner_key = (
            str(payload.get("control_id") or "")
            if message_type == "client.playback.control_ack"
            else str(payload.get("delivery_id") or "")
        )
        owners = self.control_owners if message_type == "client.playback.control_ack" else self.delivery_owners
        voice_turn_id = owners.get(owner_key, "")
        channel = self.delivery_channels.get(voice_turn_id)
        if channel is None:
            await self._send_failed(
                "voice_playback_delivery_unknown",
                terminal=False,
            )
            return
        try:
            result = channel.acknowledge(message_type, payload)
        except Exception:
            await self._send_failed(
                "voice_playback_ack_failed",
                retryable=True,
                terminal=False,
            )
            return
        if not bool(getattr(result, "ok", False)):
            await self._send_failed(
                str(getattr(result, "reason", "") or "voice_playback_ack_rejected"),
                retryable=bool(getattr(result, "retryable", False)),
                terminal=False,
            )
            return
        await self.websocket.send_json(
            {
                "type": "server.playback.ack",
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "ack_type": message_type,
                **(
                    {
                        "control_id": str(payload.get("control_id") or ""),
                        "command_id": str(payload.get("command_id") or ""),
                    }
                    if message_type == "client.playback.control_ack"
                    else {"delivery_id": str(payload.get("delivery_id") or "")}
                ),
                "status": str(getattr(result, "status", "") or "accepted"),
                **({"reason": str(getattr(result, "reason", "") or "")} if getattr(result, "reason", "") else {}),
            }
        )
        if bool(getattr(result, "response_terminal", False)):
            await self._finish_delivered_response(voice_turn_id, result)

    def _arm_delivery_wait(self, voice_turn_id: str) -> None:
        channel = self.delivery_channels.get(voice_turn_id)
        if channel is None or self.terminal:
            return
        interaction_committed = getattr(channel, "interaction_committed", None)
        if (
            self.current_request is not None
            and self.current_request.voice_turn_id == voice_turn_id
            and callable(interaction_committed)
            and interaction_committed()
        ):
            return
        if voice_turn_id in self.delivery_tasks.values():
            return
        task = asyncio.create_task(
            channel.wait_activity(),
            name=f"voice-realtime-call-delivery-{voice_turn_id}",
        )
        self.delivery_tasks[task] = voice_turn_id

    async def _handle_delivery_activity(self, voice_turn_id: str) -> None:
        channel = self.delivery_channels.get(voice_turn_id)
        if channel is None:
            return
        await _send_response_presentation(
            self.websocket,
            channel,
            VOICE_REALTIME_CALL_PROTOCOL_VERSION,
            voice_turn_id,
        )
        interaction_committed = getattr(channel, "interaction_committed", None)
        if callable(interaction_committed) and interaction_committed():
            # Always deliver ASR final first, even if the semantic worker wins
            # the race with the finalize task on this event loop.
            if self.current_request is not None and self.current_request.voice_turn_id == voice_turn_id:
                return
            await self.websocket.send_json(
                {
                    "type": "server.turn.interaction",
                    "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                    "voice_turn_id": voice_turn_id,
                    "disposition": "interaction",
                    "state": "committed",
                }
            )
            self._close_delivery_channel(voice_turn_id, reason="voice_interaction_committed")
            return
        take_control = getattr(channel, "take_control_outbound", None)
        mark_control_sent = getattr(channel, "mark_control_sent", None)
        while callable(take_control):
            control = take_control()
            if control is None:
                break
            await self.websocket.send_json(
                playback_control_payload(control, protocol_version=VOICE_REALTIME_CALL_PROTOCOL_VERSION)
            )
            if not callable(mark_control_sent):
                await self._send_failed(
                    "voice_playback_control_send_confirmation_unavailable",
                    terminal=True,
                )
                return
            sent = mark_control_sent(control.control_id)
            if not bool(getattr(sent, "ok", False)):
                await self._send_failed(
                    str(getattr(sent, "reason", "") or "voice_playback_control_send_confirmation_failed"),
                    retryable=bool(getattr(sent, "retryable", False)),
                    terminal=True,
                )
                return
            self.control_owners[control.control_id] = voice_turn_id
        while True:
            request = channel.take_outbound()
            if request is None:
                break
            await self.websocket.send_json(
                speech_payload(request, protocol_version=VOICE_REALTIME_CALL_PROTOCOL_VERSION)
            )
            await self.websocket.send_bytes(request.audio)
            sent = channel.mark_sent(request.delivery_id)
            if not bool(getattr(sent, "ok", False)):
                await self._send_failed(
                    str(getattr(sent, "reason", "") or "voice_playback_send_confirmation_failed"),
                    retryable=bool(getattr(sent, "retryable", False)),
                    terminal=True,
                )
                return
            self.delivery_owners[request.delivery_id] = voice_turn_id
        outcome = channel.response_outcome()
        if bool(getattr(outcome, "response_terminal", False)):
            await self._finish_delivered_response(voice_turn_id, outcome)

    async def _finish_delivered_response(
        self,
        voice_turn_id: str,
        outcome: Any,
    ) -> None:
        if voice_turn_id not in self.delivery_channels:
            return
        await _send_response_presentation(
            self.websocket,
            self.delivery_channels[voice_turn_id],
            VOICE_REALTIME_CALL_PROTOCOL_VERSION,
            voice_turn_id,
        )
        event_type = "server.response.completed" if outcome.response_state == "completed" else "server.response.failed"
        await self.websocket.send_json(
            {
                "type": event_type,
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_turn_id": voice_turn_id,
                "state": outcome.response_state,
                "delivery_status": outcome.delivery_status,
                "speech": outcome.full_text,
                **(
                    {
                        "reason": (outcome.response_reason or "voice_response_failed"),
                        "retryable": bool(outcome.response_retryable),
                        "message": (outcome.safe_public_summary or "这次语音回复没有生成完成。"),
                    }
                    if outcome.response_state != "completed"
                    else {}
                ),
            }
        )
        self._remove_delivery_channel(voice_turn_id)

    async def _send_provider_updates(self, result: Any) -> bool:
        if self.current_request is None:
            return False
        emitted = False
        update = getattr(result, "provider_update", None)
        for revision in tuple(getattr(update, "revisions", ()) or ()):
            quality = str(getattr(getattr(revision, "quality", None), "value", "") or "")
            message_type = {
                "partial": "server.turn.partial",
                "stable_checkpoint": "server.turn.checkpoint",
                "final": "server.turn.final",
            }.get(quality)
            if message_type is None:
                continue
            if message_type == "server.turn.final" and self.turn_final_sent:
                continue
            payload: dict[str, Any] = {
                "type": message_type,
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_turn_id": self.current_request.voice_turn_id,
                "turn_revision": int(getattr(revision, "revision", 0) or 0),
                "text": str(getattr(revision, "stable_text", "") or ""),
            }
            unstable_tail = str(getattr(revision, "unstable_tail", "") or "")
            language_hint = getattr(revision, "language_hint", None)
            confidence_hint = getattr(revision, "confidence_hint", None)
            if unstable_tail:
                payload["unstable_tail"] = unstable_tail
            if language_hint:
                payload["language"] = str(language_hint)
            if isinstance(confidence_hint, (int, float)) and not isinstance(
                confidence_hint,
                bool,
            ):
                payload["confidence"] = float(confidence_hint)
            if message_type == "server.turn.final":
                input_state = str(getattr(result, "input_state", "") or "")
                if input_state:
                    payload["input_state"] = input_state
                    payload["disposition"] = str(getattr(result, "disposition", "") or "")
                payload["commit_status"] = str(
                    getattr(
                        getattr(result, "bridge_result", None),
                        "status",
                        "",
                    )
                    or ""
                )
                response_status = str(getattr(result, "response_status", "") or "")
                if response_status:
                    payload["response"] = {
                        "status": response_status,
                        "reason": str(getattr(result, "response_reason", "") or ""),
                        "response_id": str(getattr(result, "response_id", "") or ""),
                        "retryable": bool(getattr(result, "response_retryable", False)),
                        "safe_public_summary": str(
                            getattr(
                                result,
                                "response_safe_public_summary",
                                "",
                            )
                            or ""
                        ),
                    }
                self.turn_final_sent = True
            await self.websocket.send_json(payload)
            emitted = True

        candidate = getattr(result, "early_candidate", None)
        if candidate is not None:
            await self.websocket.send_json(
                {
                    "type": "server.turn.candidate_ready",
                    "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                    "candidate_id": str(getattr(candidate, "candidate_id", "") or ""),
                    "voice_turn_id": str(getattr(candidate, "voice_turn_id", "") or ""),
                    "source_turn_revision": int(getattr(candidate, "source_turn_revision", 0) or 0),
                    "stable_text": str(getattr(candidate, "stable_text", "") or ""),
                    "playable": False,
                    "speculative": True,
                }
            )
            emitted = True
        return emitted
