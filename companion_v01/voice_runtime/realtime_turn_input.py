"""Single-turn PCM input, ASR finalization, interruption, and cancellation."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Mapping

from .realtime_protocol import (
    VOICE_REALTIME_PROTOCOL_VERSION,
    VOICE_REALTIME_MAX_FRAME_BYTES,
    VOICE_REALTIME_FINALIZE_TIMEOUT_SECONDS,
    parse_audio_metadata,
)


class RealtimeTurnInputMixin:
    """Statically inherited methods; all mutable state belongs to the session."""

    async def _handle_interruption_suspected(self, payload: Mapping[str, Any]) -> None:
        audio_clock_ms = payload.get("audio_clock_ms")
        if isinstance(audio_clock_ms, bool) or not isinstance(audio_clock_ms, int) or audio_clock_ms < 0:
            await self._send_failed(
                "voice_interruption_audio_clock_invalid",
                terminal=False,
            )
            return
        try:
            result = self.coordinator.suspect_interruption(
                audio_clock_ms=audio_clock_ms,
            )
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
        accepted = status == "accepted"
        await self.websocket.send_json(
            {
                "type": ("server.interruption.accepted" if accepted else "server.interruption.skipped"),
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "voice_turn_id": (self.open_request.voice_turn_id if self.open_request is not None else ""),
                "status": status or "duplicate",
                "reason": str(getattr(result, "reason", "") or "")[:128],
            }
        )

    async def _handle_audio_header(self, payload: Mapping[str, Any]) -> None:
        if self.finalize_task is not None:
            await self._send_failed("voice_asr_finalize_already_started", terminal=False)
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
        if self.coordinator is None:
            await self._send_failed("client_open_required", terminal=False)
            return
        if self.finalize_task is not None:
            await self._send_failed("voice_asr_finalize_already_started", terminal=False)
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
            await self._send_failed("voice_realtime_audio_feed_failed", retryable=True, terminal=True)
            return
        if getattr(result, "status", "") == "failed":
            await self._send_result_failure(result, terminal=True)
            return
        self.input_bytes += len(audio)
        self.input_frames += 1
        emitted = await self._send_provider_updates(result)
        if not emitted and getattr(result, "status", "") == "duplicate":
            await self.websocket.send_json(
                {
                    "type": "server.audio_accepted",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "status": "duplicate",
                    "sequence": sequence,
                }
            )

    async def _handle_endpoint(self) -> None:
        if self.pending_audio is not None:
            await self._send_failed("audio_payload_missing", terminal=False)
            return
        if self.finalize_task is not None:
            await self.websocket.send_json(
                {
                    "type": "server.finalizing",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "status": "duplicate",
                    "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
                }
            )
            return
        try:
            started = await self.coordinator.start_finalize_pcm()
        except Exception:
            await self._send_failed("voice_realtime_finalize_start_failed", retryable=True, terminal=True)
            return
        if not getattr(started, "ok", False):
            await self._send_result_failure(started, terminal=True)
            return
        await self.websocket.send_json(
            {
                "type": "server.finalizing",
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "status": str(getattr(started, "status", "") or "started"),
                "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
            }
        )
        self.finalize_task = asyncio.create_task(
            self._settle_finalize_with_timeout(),
            name=f"voice-realtime-finalize-{self.open_request.voice_turn_id if self.open_request else 'unknown'}",
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
                safe_public_summary="实时识别收尾超时，请改用普通语音识别。",
                terminal=True,
            )
            return
        except Exception:
            await self._send_failed("voice_realtime_finalize_failed", retryable=True, terminal=True)
            return
        if getattr(result, "status", "") == "failed":
            await self._send_result_failure(result, terminal=True)
            return
        emitted = await self._send_provider_updates(result)
        if not emitted or not self.final_sent:
            await self._send_failed("voice_realtime_final_missing", retryable=True, terminal=True)
            return
        response_status = str(getattr(result, "response_status", "") or "")
        if response_status and response_status not in {"started", "completed"}:
            reason = str(getattr(result, "response_reason", "") or "voice_response_start_failed")[:128]
            await self.websocket.send_json(
                {
                    "type": "server.response.failed",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
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
            self._terminal_reason = reason
            self.terminal = True
            self._observe_once(ok=False, reason=reason)
            await self.websocket.close(code=1011, reason="voice_response_start_failed")
            return
        if self.delivery_channel is not None:
            self.delivery_channel.notify_runtime_change()
            self._arm_delivery_wait()
            return
        self.terminal = True
        self._observe_once(ok=True)
        await self.websocket.close(code=1000, reason="voice_realtime_complete")

    async def _handle_cancel(self, payload: Mapping[str, Any]) -> None:
        reason = str(payload.get("reason") or "client_cancelled")[:96]
        if self.final_sent and self.delivery_channel is not None:
            response_cancel = getattr(self.coordinator, "cancel_response", None)
            if not callable(response_cancel):
                await self._send_failed(
                    "voice_response_cancel_unavailable",
                    retryable=True,
                    terminal=True,
                )
                return
            try:
                result = response_cancel(reason=reason)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                await self._send_failed(
                    "voice_response_cancel_failed",
                    retryable=True,
                    terminal=True,
                )
                return
            if getattr(result, "status", "") == "failed":
                await self._send_result_failure(result, terminal=True)
                return
            self.delivery_channel.close(reason=reason)
            self.terminal = True
            await self.websocket.send_json(
                {
                    "type": "server.cancelled",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "reason": reason,
                    "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
                }
            )
            self._observe_once(ok=True)
            await self.websocket.close(code=1000, reason="voice_realtime_cancelled")
            return
        if self.coordinator is None:
            self.terminal = True
            await self.websocket.send_json(
                {
                    "type": "server.cancelled",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "reason": reason,
                }
            )
            await self.websocket.close(code=1000, reason="voice_realtime_cancelled")
            return
        try:
            result = await self.coordinator.cancel(reason=reason)
        except Exception:
            await self._send_failed("voice_realtime_cancel_failed", retryable=True, terminal=True)
            return
        if getattr(result, "status", "") == "failed":
            await self._send_result_failure(result, terminal=True)
            return
        self.terminal = True
        await self.websocket.send_json(
            {
                "type": "server.cancelled",
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "reason": str(getattr(result, "reason", "") or reason),
                "voice_turn_id": self.open_request.voice_turn_id if self.open_request else "",
            }
        )
        self._observe_once(ok=True)
        await self.websocket.close(code=1000, reason="voice_realtime_cancelled")
