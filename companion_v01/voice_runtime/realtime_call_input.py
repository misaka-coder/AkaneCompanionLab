"""Current-turn input, finalization, interruption and response cancellation."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Mapping


from .realtime_protocol import (
    VOICE_REALTIME_CALL_PROTOCOL_VERSION,
    VOICE_REALTIME_MAX_FRAME_BYTES,
    VOICE_REALTIME_FINALIZE_TIMEOUT_SECONDS,
    parse_audio_metadata,
)


class RealtimeCallInputMixin:
    """Current-turn input, finalization, interruption and response cancellation."""

    async def _handle_audio_header(self, payload: Mapping[str, Any]) -> None:
        if self.coordinator is None or self.current_request is None:
            await self._send_failed("client_turn_start_required", terminal=False)
            return
        if not self._matches_current_turn(payload):
            await self._send_failed(
                "voice_realtime_turn_identity_conflict",
                terminal=False,
            )
            return
        if self.finalize_task is not None:
            await self._send_failed(
                "voice_asr_finalize_already_started",
                terminal=False,
            )
            return
        if self.pending_audio is not None:
            await self._send_failed("audio_payload_pending", terminal=False)
            return
        metadata = parse_audio_metadata(payload)
        if metadata is None:
            await self._send_failed("audio_metadata_invalid", terminal=False)
            return
        self.pending_audio = metadata

    async def _handle_audio_bytes(self, audio: bytes) -> None:
        if self.coordinator is None or self.current_request is None:
            await self._send_failed("client_turn_start_required", terminal=False)
            return
        if self.finalize_task is not None:
            await self._send_failed(
                "voice_asr_finalize_already_started",
                terminal=False,
            )
            return
        if self.pending_audio is None:
            await self._send_failed("audio_metadata_required", terminal=False)
            return
        if len(audio) > VOICE_REALTIME_MAX_FRAME_BYTES:
            self.pending_audio = None
            await self._send_failed("audio_frame_too_large", terminal=True)
            return
        sequence, audio_clock_ms = self.pending_audio
        self.pending_audio = None
        try:
            result = await self.coordinator.feed_pcm_frame(
                audio,
                sequence=sequence,
                audio_clock_ms=audio_clock_ms,
            )
        except Exception:
            await self._send_failed(
                "voice_realtime_audio_feed_failed",
                retryable=True,
                terminal=True,
            )
            return
        if str(getattr(result, "status", "") or "") == "failed":
            await self._send_result_failure(result, terminal=True)
            return
        self.input_bytes += len(audio)
        self.input_frames += 1
        emitted = await self._send_provider_updates(result)
        if not emitted and str(getattr(result, "status", "") or "") == "duplicate":
            await self.websocket.send_json(
                {
                    "type": "server.turn.audio_accepted",
                    "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                    "voice_turn_id": self.current_request.voice_turn_id,
                    "status": "duplicate",
                    "sequence": sequence,
                }
            )

    async def _handle_turn_endpoint(self, payload: Mapping[str, Any]) -> None:
        if self.coordinator is None or self.current_request is None:
            await self._send_failed("client_turn_start_required", terminal=False)
            return
        if not self._matches_current_turn(payload):
            await self._send_failed(
                "voice_realtime_turn_identity_conflict",
                terminal=False,
            )
            return
        if self.pending_audio is not None:
            await self._send_failed("audio_payload_missing", terminal=False)
            return
        if self.finalize_task is not None:
            await self.websocket.send_json(
                {
                    "type": "server.turn.finalizing",
                    "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                    "status": "duplicate",
                    "voice_turn_id": self.current_request.voice_turn_id,
                }
            )
            return
        try:
            started = await self.coordinator.start_finalize_pcm()
        except Exception:
            await self._send_failed(
                "voice_realtime_finalize_start_failed",
                retryable=True,
                terminal=True,
            )
            return
        if not bool(getattr(started, "ok", False)):
            await self._send_result_failure(started, terminal=True)
            return
        await self.websocket.send_json(
            {
                "type": "server.turn.finalizing",
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "status": str(getattr(started, "status", "") or "started"),
                "voice_turn_id": self.current_request.voice_turn_id,
            }
        )
        self.finalize_task = asyncio.create_task(
            self._settle_finalize_with_timeout(),
            name=f"voice-realtime-call-finalize-{self.current_request.voice_turn_id}",
        )

    async def _settle_finalize_with_timeout(self) -> Any:
        try:
            return await asyncio.wait_for(
                self.coordinator.settle_finalize(),
                timeout=VOICE_REALTIME_FINALIZE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            try:
                await self.coordinator.cancel(reason="voice_realtime_finalize_timeout")
            except Exception:
                pass
            raise

    async def _handle_finalize_completion(self) -> None:
        assert self.finalize_task is not None
        task = self.finalize_task
        self.finalize_task = None
        try:
            result = task.result()
        except asyncio.CancelledError:
            return
        except TimeoutError:
            await self._send_failed(
                "voice_realtime_finalize_timeout",
                retryable=True,
                safe_public_summary="实时识别收尾超时，本次通话已经结束。",
                terminal=True,
            )
            return
        except Exception:
            await self._send_failed(
                "voice_realtime_finalize_failed",
                retryable=True,
                terminal=True,
            )
            return
        if str(getattr(result, "status", "") or "") == "failed":
            await self._send_result_failure(result, terminal=True)
            return
        emitted = await self._send_provider_updates(result)
        if not emitted or not self.turn_final_sent:
            await self._send_failed(
                "voice_realtime_final_missing",
                retryable=True,
                terminal=True,
            )
            return
        request = self.current_request
        assert request is not None
        response_status = str(getattr(result, "response_status", "") or "")
        if response_status and response_status not in {"started", "completed"}:
            reason = str(getattr(result, "response_reason", "") or "voice_response_start_failed")[:128]
            await self.websocket.send_json(
                {
                    "type": "server.response.failed",
                    "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                    "voice_turn_id": request.voice_turn_id,
                    "state": "failed",
                    "delivery_status": "not_started",
                    "speech": "",
                    "reason": reason,
                    "retryable": bool(getattr(result, "response_retryable", False)),
                    "message": str(
                        getattr(result, "response_safe_public_summary", "") or "语音已经识别，但回复生成暂时无法启动。"
                    )[:160],
                }
            )
            self._close_delivery_channel(request.voice_turn_id, reason=reason)
        else:
            channel = self.delivery_channels.get(request.voice_turn_id)
            if channel is not None:
                channel.notify_runtime_change()
                self._arm_delivery_wait(request.voice_turn_id)
        self.completed_turns += 1
        self.current_request = None
        self.coordinator = None
        self.pending_audio = None
        self.turn_final_sent = False
        self._arm_delivery_wait(request.voice_turn_id)

    async def _handle_interruption_suspected(
        self,
        payload: Mapping[str, Any],
    ) -> None:
        if self.coordinator is None or self.current_request is None:
            await self._send_failed("client_turn_start_required", terminal=False)
            return
        if not self._matches_current_turn(payload):
            await self._send_failed(
                "voice_realtime_turn_identity_conflict",
                terminal=False,
            )
            return
        audio_clock_ms = payload.get("audio_clock_ms")
        if isinstance(audio_clock_ms, bool) or not isinstance(audio_clock_ms, int) or audio_clock_ms < 0:
            await self._send_failed(
                "voice_interruption_audio_clock_invalid",
                terminal=False,
            )
            return
        try:
            result = self.coordinator.suspect_interruption(audio_clock_ms=audio_clock_ms)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            await self._send_failed(
                "voice_interruption_dispatch_failed",
                retryable=True,
                terminal=False,
            )
            return
        status = str(getattr(result, "status", "") or "")
        if status == "failed":
            await self._send_result_failure(result, terminal=False)
            return
        await self.websocket.send_json(
            {
                "type": ("server.interruption.accepted" if status == "accepted" else "server.interruption.skipped"),
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_turn_id": self.current_request.voice_turn_id,
                "status": status or "duplicate",
                "reason": str(getattr(result, "reason", "") or "")[:128],
            }
        )

    async def _handle_response_cancel(
        self,
        payload: Mapping[str, Any],
    ) -> None:
        voice_turn_id = str(payload.get("voice_turn_id") or "")
        reason = str(payload.get("reason") or "client_cancelled")[:96]
        if not voice_turn_id or voice_turn_id not in self.delivery_channels:
            await self._send_failed(
                "voice_playback_delivery_unknown",
                terminal=False,
            )
            return
        cancel_response = getattr(self.call, "cancel_response", None)
        if not callable(cancel_response):
            await self._send_failed(
                "voice_response_cancel_unavailable",
                retryable=True,
                terminal=False,
            )
            return
        try:
            result = cancel_response(
                voice_turn_id=voice_turn_id,
                reason=reason,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            await self._send_failed(
                "voice_response_cancel_failed",
                retryable=True,
                terminal=False,
            )
            return
        if str(getattr(result, "status", "") or "") == "failed":
            await self._send_result_failure(result, terminal=False)
            return
        self._close_delivery_channel(voice_turn_id, reason=reason)
        await self.websocket.send_json(
            {
                "type": "server.response.cancelled",
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_turn_id": voice_turn_id,
                "status": str(getattr(result, "status", "") or "accepted"),
                "reason": reason,
            }
        )
