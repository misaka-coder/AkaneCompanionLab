"""Single-turn playback receipts and model-response projection."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .realtime_protocol import VOICE_REALTIME_PROTOCOL_VERSION
from .realtime_events import _send_response_presentation, playback_control_payload, speech_payload


class RealtimeTurnDeliveryMixin:
    """Statically inherited methods; all mutable state belongs to the session."""

    async def _handle_playback_ack(
        self,
        message_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if self.delivery_channel is None:
            await self._send_failed(
                "voice_playback_not_negotiated",
                terminal=False,
            )
            return
        try:
            result = self.delivery_channel.acknowledge(message_type, payload)
        except Exception:
            await self._send_failed(
                "voice_playback_ack_failed",
                retryable=True,
                terminal=False,
            )
            return
        if not result.ok:
            await self._send_failed(
                result.reason or "voice_playback_ack_rejected",
                retryable=result.retryable,
                terminal=False,
            )
            return
        await self.websocket.send_json(
            {
                "type": "server.playback.ack",
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "ack_type": message_type,
                **(
                    {
                        "control_id": str(payload.get("control_id") or ""),
                        "command_id": str(payload.get("command_id") or ""),
                    }
                    if message_type == "client.playback.control_ack"
                    else {"delivery_id": str(payload.get("delivery_id") or "")}
                ),
                "status": result.status,
                **({"reason": result.reason} if result.reason else {}),
            }
        )
        if result.response_terminal:
            await self._finish_delivered_response(result)

    def _arm_delivery_wait(self) -> None:
        if self.delivery_channel is None or self.delivery_task is not None or self.terminal:
            return
        interaction_committed = getattr(self.delivery_channel, "interaction_committed", None)
        if not self.final_sent and callable(interaction_committed) and interaction_committed():
            return
        self.delivery_task = asyncio.create_task(
            self.delivery_channel.wait_activity(),
            name="voice-realtime-playback-delivery",
        )

    async def _handle_delivery_activity(self) -> None:
        if self.delivery_channel is None:
            return
        await _send_response_presentation(
            self.websocket,
            self.delivery_channel,
            VOICE_REALTIME_PROTOCOL_VERSION,
            self.open_request.voice_turn_id if self.open_request else "",
        )
        interaction_committed = getattr(self.delivery_channel, "interaction_committed", None)
        if callable(interaction_committed) and interaction_committed():
            if not self.final_sent:
                return
            await self.websocket.send_json(
                {
                    "type": "server.turn.interaction",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
                    "disposition": "interaction",
                    "state": "committed",
                }
            )
            self.delivery_channel.close(reason="voice_interaction_committed")
            self.terminal = True
            self._observe_once(ok=True)
            await self.websocket.close(code=1000, reason="voice_interaction_committed")
            return
        take_control = getattr(self.delivery_channel, "take_control_outbound", None)
        mark_control_sent = getattr(self.delivery_channel, "mark_control_sent", None)
        while callable(take_control):
            control = take_control()
            if control is None:
                break
            await self.websocket.send_json(
                playback_control_payload(control, protocol_version=VOICE_REALTIME_PROTOCOL_VERSION)
            )
            if not callable(mark_control_sent):
                await self._send_failed(
                    "voice_playback_control_send_confirmation_unavailable",
                    terminal=True,
                )
                return
            sent = mark_control_sent(control.control_id)
            if not sent.ok:
                await self._send_failed(
                    sent.reason or "voice_playback_control_send_confirmation_failed",
                    retryable=sent.retryable,
                    terminal=True,
                )
                return
        while True:
            request = self.delivery_channel.take_outbound()
            if request is None:
                break
            await self.websocket.send_json(speech_payload(request, protocol_version=VOICE_REALTIME_PROTOCOL_VERSION))
            await self.websocket.send_bytes(request.audio)
            sent = self.delivery_channel.mark_sent(request.delivery_id)
            if not sent.ok:
                await self._send_failed(
                    sent.reason or "voice_playback_send_confirmation_failed",
                    retryable=sent.retryable,
                    terminal=True,
                )
                return
        outcome = self.delivery_channel.response_outcome()
        if outcome.response_terminal:
            await self._finish_delivered_response(outcome)

    async def _finish_delivered_response(self, outcome: Any) -> None:
        if self.terminal:
            return
        await _send_response_presentation(
            self.websocket,
            self.delivery_channel,
            VOICE_REALTIME_PROTOCOL_VERSION,
            self.open_request.voice_turn_id if self.open_request else "",
        )
        event_type = "server.response.completed" if outcome.response_state == "completed" else "server.response.failed"
        await self.websocket.send_json(
            {
                "type": event_type,
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
                "state": outcome.response_state,
                "delivery_status": outcome.delivery_status,
                "speech": outcome.full_text,
                **(
                    {
                        "reason": outcome.response_reason or "voice_response_failed",
                        "retryable": bool(outcome.response_retryable),
                        "message": (outcome.safe_public_summary or "这次语音回复没有生成完成。"),
                    }
                    if outcome.response_state != "completed"
                    else {}
                ),
            }
        )
        self.terminal = True
        self._observe_once(ok=outcome.response_state == "completed")
        await self.websocket.close(code=1000, reason="voice_realtime_complete")

    async def _send_provider_updates(self, result: Any) -> bool:
        emitted = False
        update = getattr(result, "provider_update", None)
        for revision in tuple(getattr(update, "revisions", ()) or ()):
            quality = str(getattr(getattr(revision, "quality", None), "value", "") or "")
            message_type = {
                "partial": "server.partial",
                "stable_checkpoint": "server.checkpoint",
                "final": "server.final",
            }.get(quality)
            if message_type is None:
                continue
            if message_type == "server.final" and self.final_sent:
                continue
            payload: dict[str, Any] = {
                "type": message_type,
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
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
            if isinstance(confidence_hint, (int, float)) and not isinstance(confidence_hint, bool):
                payload["confidence"] = float(confidence_hint)
            if message_type == "server.final":
                payload["commit_status"] = str(getattr(getattr(result, "bridge_result", None), "status", "") or "")
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
                self.final_sent = True
            await self.websocket.send_json(payload)
            emitted = True

        candidate = getattr(result, "early_candidate", None)
        if candidate is not None:
            await self.websocket.send_json(
                {
                    "type": "server.candidate_ready",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
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
