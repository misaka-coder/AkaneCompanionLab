from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

import anyio
from fastapi import WebSocket

from .playback_delivery import VOICE_PLAYBACK_OUTPUT_MODE


VOICE_REALTIME_PROTOCOL_VERSION = 1
VOICE_REALTIME_MAX_FRAME_BYTES = 1024 * 1024
VOICE_REALTIME_FINALIZE_TIMEOUT_SECONDS = 12.0
VOICE_REALTIME_INPUT_INACTIVITY_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class VoiceRealtimeOpenRequest:
    profile_user_id: str
    conversation_id: str
    session_id: str
    voice_turn_id: str
    audio_stream_id: str
    disposition: str
    language: str
    character_pack_id: str
    input_format: str
    sample_rate: int
    channels: int
    output_mode: str = "text_only"


@dataclass(frozen=True)
class VoiceRealtimeCoordinatorResolution:
    status: str
    reason: str = ""
    coordinator: Any | None = None
    provider_id: str = ""
    voice_session_id: str = ""
    retryable: bool = False
    safe_public_summary: str = ""
    delivery_channel: Any | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.coordinator is not None

    @classmethod
    def succeeded(
        cls,
        coordinator: Any,
        *,
        provider_id: str = "",
        voice_session_id: str = "",
        delivery_channel: Any | None = None,
    ) -> VoiceRealtimeCoordinatorResolution:
        return cls(
            status="ready",
            coordinator=coordinator,
            provider_id=str(provider_id or ""),
            voice_session_id=str(voice_session_id or ""),
            delivery_channel=delivery_channel,
        )

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        status: str = "unavailable",
        retryable: bool = False,
        safe_public_summary: str = "",
    ) -> VoiceRealtimeCoordinatorResolution:
        return cls(
            status=status,
            reason=str(reason or "voice_realtime_unavailable"),
            retryable=bool(retryable),
            safe_public_summary=str(safe_public_summary or ""),
        )


VoiceRealtimeCoordinatorFactory = Callable[
    [VoiceRealtimeOpenRequest],
    VoiceRealtimeCoordinatorResolution | Awaitable[VoiceRealtimeCoordinatorResolution],
]


class VoiceRealtimeWebSocketSession:
    """Transport one ordered PCM stream into a VoiceASR turn coordinator."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        coordinator_factory: VoiceRealtimeCoordinatorFactory | None,
        runtime_metrics: Any,
        log_event: Callable[..., None],
    ) -> None:
        self.websocket = websocket
        self.coordinator_factory = coordinator_factory
        self.runtime_metrics = runtime_metrics
        self.log_event = log_event
        self.open_request: VoiceRealtimeOpenRequest | None = None
        self.coordinator: Any | None = None
        self.provider_id = ""
        self.voice_session_id = ""
        self.pending_audio: tuple[int, int] | None = None
        self.finalize_task: asyncio.Task[Any] | None = None
        self.receive_task: asyncio.Task[dict[str, Any]] | None = None
        self.delivery_channel: Any | None = None
        self.delivery_task: asyncio.Task[None] | None = None
        self.terminal = False
        self.final_sent = False
        self.started_at = time.perf_counter()
        self.last_client_activity_at = self.started_at
        self.input_bytes = 0
        self.input_frames = 0
        self._terminal_reason = ""
        self._observed = False

    async def run(self) -> None:
        await self.websocket.accept()
        cancelled: asyncio.CancelledError | None = None
        self.receive_task = asyncio.create_task(
            self.websocket.receive(),
            name="voice-realtime-websocket-receive",
        )
        try:
            while not self.terminal:
                wait_for: set[asyncio.Task[Any]] = {self.receive_task}
                if self.finalize_task is not None:
                    wait_for.add(self.finalize_task)
                if self.delivery_task is not None:
                    wait_for.add(self.delivery_task)
                done, _pending = await asyncio.wait(
                    wait_for,
                    timeout=self._input_inactivity_wait_seconds(),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    await self._send_failed(
                        "voice_realtime_input_timeout",
                        retryable=True,
                        safe_public_summary="实时语音输入等待超时。",
                        terminal=True,
                    )
                    continue

                if self.finalize_task is not None and self.finalize_task in done:
                    await self._handle_finalize_completion()
                    continue

                if self.delivery_task is not None and self.delivery_task in done:
                    self.delivery_task = None
                    await self._handle_delivery_activity()
                    if not self.terminal:
                        self._arm_delivery_wait()
                    continue

                if self.receive_task not in done:
                    continue
                message = self.receive_task.result()
                self.last_client_activity_at = time.perf_counter()
                self.receive_task = asyncio.create_task(
                    self.websocket.receive(),
                    name="voice-realtime-websocket-receive",
                )
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._handle_audio_bytes(bytes(message["bytes"]))
                elif message.get("text") is not None:
                    await self._handle_text(str(message["text"]))
                else:
                    await self._send_failed("websocket_message_invalid", terminal=True)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except RuntimeError:
            pass
        except Exception as exc:
            self._log_transport_exception(exc)
            await self._send_failed("voice_realtime_transport_failed", retryable=True, terminal=True)
        finally:
            with anyio.CancelScope(shield=True):
                if self.receive_task is not None and not self.receive_task.done():
                    self.receive_task.cancel()
                    await asyncio.gather(self.receive_task, return_exceptions=True)
                if self.delivery_task is not None and not self.delivery_task.done():
                    self.delivery_task.cancel()
                    await asyncio.gather(self.delivery_task, return_exceptions=True)
                self._close_unfinished_delivery_channel()
                if not self.terminal and self.coordinator is not None:
                    try:
                        await self.coordinator.cancel(reason="client_disconnected")
                    except Exception:
                        pass
                if self.finalize_task is not None and not self.finalize_task.done():
                    self.finalize_task.cancel()
                    await asyncio.gather(self.finalize_task, return_exceptions=True)
                completed = self.final_sent or self.terminal
                self._observe_once(
                    ok=completed,
                    reason=(self._terminal_reason or ("client_disconnected" if not completed else "")),
                )
        if cancelled is not None:
            raise cancelled

    def _close_unfinished_delivery_channel(self) -> None:
        channel = self.delivery_channel
        if channel is None:
            return
        try:
            outcome = channel.response_outcome()
            if bool(getattr(outcome, "response_terminal", False)):
                return
        except Exception:
            pass
        reason = self._terminal_reason or (
            "client_disconnected" if not self.terminal else "voice_realtime_transport_closed"
        )
        try:
            channel.close(reason=reason)
        except Exception:
            pass

    def _log_transport_exception(self, exc: Exception) -> None:
        try:
            self.log_event(
                "asr_realtime_transport_exception",
                error_type=type(exc).__name__[:96],
                opened=self.open_request is not None,
                provider=self.provider_id,
                final_sent=self.final_sent,
                delivery_attached=self.delivery_channel is not None,
                input_frames=self.input_frames,
                input_bytes=self.input_bytes,
            )
        except Exception:
            pass

    async def _handle_text(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            await self._send_failed("client_message_invalid_json", terminal=False)
            return
        if not isinstance(payload, Mapping):
            await self._send_failed("client_message_must_be_object", terminal=False)
            return
        message_type = str(payload.get("type") or "")

        if message_type == "client.open":
            await self._handle_open(payload)
            return
        if message_type == "client.cancel":
            await self._handle_cancel(payload)
            return
        if self.coordinator is None:
            await self._send_failed("client_open_required", terminal=False)
            return
        if message_type == "client.audio":
            await self._handle_audio_header(payload)
            return
        if message_type == "client.interruption.suspected":
            await self._handle_interruption_suspected(payload)
            return
        if message_type == "client.endpoint":
            await self._handle_endpoint()
            return
        if message_type in {
            "client.playback.enqueued",
            "client.playback.started",
            "client.playback.completed",
            "client.playback.interrupted",
            "client.playback.failed",
            "client.playback.control_ack",
        }:
            await self._handle_playback_ack(message_type, payload)
            return
        await self._send_failed("client_message_type_unsupported", terminal=False)

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

    async def _handle_open(self, payload: Mapping[str, Any]) -> None:
        if self.open_request is not None:
            await self._send_failed("client_open_already_received", terminal=False)
            return
        parsed, reason = _parse_open_request(payload)
        if parsed is None:
            await self._send_failed(reason, terminal=False)
            return
        self.open_request = parsed
        if self.coordinator_factory is None:
            await self._send_failed("voice_realtime_not_configured", terminal=True)
            return
        try:
            resolution = self.coordinator_factory(parsed)
            if inspect.isawaitable(resolution):
                resolution = await resolution
        except Exception:
            await self._send_failed("voice_realtime_factory_failed", retryable=True, terminal=True)
            return
        if not isinstance(resolution, VoiceRealtimeCoordinatorResolution):
            await self._send_failed("voice_realtime_factory_result_invalid", terminal=True)
            return
        if not resolution.ready:
            await self._send_failed(
                resolution.reason or "voice_realtime_unavailable",
                retryable=resolution.retryable,
                safe_public_summary=resolution.safe_public_summary,
                terminal=True,
            )
            return

        self.coordinator = resolution.coordinator
        self.provider_id = resolution.provider_id
        self.voice_session_id = resolution.voice_session_id
        self.delivery_channel = resolution.delivery_channel
        if parsed.output_mode == VOICE_PLAYBACK_OUTPUT_MODE and self.delivery_channel is None:
            await self._send_failed(
                "voice_playback_delivery_unavailable",
                retryable=True,
                terminal=True,
            )
            return
        try:
            opened = await self.coordinator.open()
        except Exception:
            await self._send_failed("voice_realtime_open_failed", retryable=True, terminal=True)
            return
        if not getattr(opened, "ok", False):
            await self._send_result_failure(opened, terminal=True)
            return
        await self.websocket.send_json(
            {
                "type": "server.ready",
                "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                "voice_turn_id": parsed.voice_turn_id,
                "audio_stream_id": parsed.audio_stream_id,
                **({"voice_session_id": self.voice_session_id} if self.voice_session_id else {}),
                "input": {
                    "format": parsed.input_format,
                    "sample_rate": parsed.sample_rate,
                    "channels": parsed.channels,
                },
                "normalized_output": {
                    "format": "s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                },
                "output": {
                    "mode": parsed.output_mode,
                    "acknowledgements_required": (parsed.output_mode == VOICE_PLAYBACK_OUTPUT_MODE),
                    "playback_controls": (
                        ["duck", "resume", "stop"] if parsed.output_mode == VOICE_PLAYBACK_OUTPUT_MODE else []
                    ),
                },
                **({"provider_id": self.provider_id} if self.provider_id else {}),
            }
        )
        self._arm_delivery_wait()

    async def _handle_audio_header(self, payload: Mapping[str, Any]) -> None:
        if self.finalize_task is not None:
            await self._send_failed("voice_asr_finalize_already_started", terminal=False)
            return
        if self.pending_audio is not None:
            await self._send_failed("audio_payload_pending", terminal=False)
            return
        sequence = payload.get("sequence")
        audio_clock_ms = payload.get("audio_clock_ms")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or isinstance(audio_clock_ms, bool)
            or not isinstance(audio_clock_ms, int)
            or audio_clock_ms < 0
        ):
            await self._send_failed("audio_metadata_invalid", terminal=False)
            return
        self.pending_audio = (sequence, audio_clock_ms)

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
        if response_status and response_status != "started":
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
            result = response_cancel(reason=reason)
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
        self.delivery_task = asyncio.create_task(
            self.delivery_channel.wait_activity(),
            name="voice-realtime-playback-delivery",
        )

    async def _handle_delivery_activity(self) -> None:
        if self.delivery_channel is None:
            return
        take_control = getattr(self.delivery_channel, "take_control_outbound", None)
        mark_control_sent = getattr(self.delivery_channel, "mark_control_sent", None)
        while callable(take_control):
            control = take_control()
            if control is None:
                break
            await self.websocket.send_json(
                {
                    "type": "server.playback.control",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "control_id": control.control_id,
                    "command_id": control.command_id,
                    "action": control.action,
                    "delivery_id": control.delivery_id,
                    "voice_turn_id": control.voice_turn_id,
                    "response_id": control.response_id,
                    "speech_unit_id": control.speech_unit_id,
                    **({"resume_token": control.resume_token} if control.resume_token else {}),
                    **({"interruption_id": control.interruption_id} if control.interruption_id else {}),
                    **({"reason": control.reason} if control.reason else {}),
                }
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
            await self.websocket.send_json(
                {
                    "type": "server.speech",
                    "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
                    "delivery_id": request.delivery_id,
                    "voice_turn_id": request.voice_turn_id,
                    "response_id": request.response_id,
                    "speech_unit_id": request.speech_unit_id,
                    "ordinal": request.ordinal,
                    "text": request.text,
                    "media_type": request.media_type,
                    "byte_length": len(request.audio),
                    "binary_follows": True,
                }
            )
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

    async def _send_result_failure(self, result: Any, *, terminal: bool) -> None:
        await self._send_failed(
            str(getattr(result, "reason", "") or "voice_realtime_failed"),
            retryable=bool(getattr(result, "retryable", False)),
            safe_public_summary=str(getattr(result, "safe_public_summary", "") or ""),
            terminal=terminal,
        )

    async def _send_failed(
        self,
        reason: str,
        *,
        retryable: bool = False,
        safe_public_summary: str = "",
        terminal: bool,
    ) -> None:
        payload = {
            "type": "server.failed",
            "protocol_version": VOICE_REALTIME_PROTOCOL_VERSION,
            "status": "failed",
            "reason": str(reason or "voice_realtime_failed")[:128],
            "retryable": bool(retryable),
            "message": str(safe_public_summary or "实时语音链路暂时不可用。")[:160],
            "terminal": bool(terminal),
        }
        try:
            await self.websocket.send_json(payload)
        except Exception:
            pass
        if not terminal:
            return
        self._terminal_reason = str(reason or "voice_realtime_failed")[:128]
        self.terminal = True
        self._observe_once(ok=False, reason=self._terminal_reason)
        try:
            await self.websocket.close(code=1011, reason="voice_realtime_failed")
        except Exception:
            pass

    def _observe_once(self, *, ok: bool, reason: str = "") -> None:
        if self._observed:
            return
        self._observed = True
        duration_ms = (time.perf_counter() - self.started_at) * 1000
        try:
            self.runtime_metrics.observe_request(
                "asr_realtime",
                duration_ms=duration_ms,
                ok=bool(ok),
            )
        except Exception:
            pass
        try:
            self.log_event(
                "asr_realtime_complete",
                ok=bool(ok),
                **({"reason": str(reason or "voice_realtime_failed")[:128]} if not ok else {}),
                duration_ms=round(duration_ms, 1),
                input_frames=self.input_frames,
                input_bytes=self.input_bytes,
                provider=self.provider_id,
            )
        except Exception:
            pass

    def _input_inactivity_wait_seconds(self) -> float | None:
        if self.coordinator is None or self.finalize_task is not None or self.final_sent:
            return None
        elapsed = time.perf_counter() - self.last_client_activity_at
        return max(0.05, VOICE_REALTIME_INPUT_INACTIVITY_TIMEOUT_SECONDS - elapsed)


async def handle_voice_realtime_websocket(
    websocket: WebSocket,
    *,
    coordinator_factory: VoiceRealtimeCoordinatorFactory | None,
    runtime_metrics: Any,
    log_event: Callable[..., None],
) -> None:
    session = VoiceRealtimeWebSocketSession(
        websocket=websocket,
        coordinator_factory=coordinator_factory,
        runtime_metrics=runtime_metrics,
        log_event=log_event,
    )
    await session.run()


def _parse_open_request(
    payload: Mapping[str, Any],
) -> tuple[VoiceRealtimeOpenRequest | None, str]:
    protocol_version = payload.get("protocol_version")
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version != VOICE_REALTIME_PROTOCOL_VERSION
    ):
        return None, "voice_realtime_protocol_version_unsupported"

    profile_user_id = _bounded_text(payload.get("profile_user_id"), 160)
    conversation_id = _bounded_text(payload.get("conversation_id"), 160)
    session_id = _bounded_text(payload.get("session_id"), 160)
    if not profile_user_id or not conversation_id or not session_id:
        return None, "voice_realtime_identity_missing"

    input_spec = payload.get("input")
    if not isinstance(input_spec, Mapping):
        return None, "voice_realtime_input_invalid"
    input_format = str(input_spec.get("format") or "").strip().lower()
    sample_rate = input_spec.get("sample_rate")
    channels = input_spec.get("channels")
    if input_format not in {"s16le", "f32le"}:
        return None, "voice_realtime_input_format_unsupported"
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or not 8000 <= sample_rate <= 192000
        or isinstance(channels, bool)
        or not isinstance(channels, int)
        or not 1 <= channels <= 8
    ):
        return None, "voice_realtime_input_invalid"

    language = _bounded_text(payload.get("language"), 32, allow_empty=True)
    if language is None:
        return None, "voice_realtime_language_invalid"
    disposition = str(payload.get("disposition") or "message").strip()
    if disposition not in {"message", "interaction"}:
        return None, "voice_asr_disposition_invalid"
    character_pack_id = _bounded_text(
        payload.get("character_pack_id"),
        160,
        allow_empty=True,
    )
    if character_pack_id is None:
        return None, "voice_realtime_character_pack_invalid"

    output_spec = payload.get("output")
    output_mode = "text_only"
    if output_spec is not None:
        if not isinstance(output_spec, Mapping):
            return None, "voice_realtime_output_invalid"
        output_mode = str(output_spec.get("mode") or "text_only").strip()
        if output_mode not in {"text_only", VOICE_PLAYBACK_OUTPUT_MODE}:
            return None, "voice_realtime_output_mode_unsupported"

    voice_turn_id = _bounded_text(payload.get("voice_turn_id"), 160, allow_empty=True)
    audio_stream_id = _bounded_text(payload.get("audio_stream_id"), 160, allow_empty=True)
    if voice_turn_id is None or audio_stream_id is None:
        return None, "voice_realtime_identity_invalid"
    if not voice_turn_id:
        voice_turn_id = f"voice_turn_{uuid.uuid4().hex}"
    if not audio_stream_id:
        audio_stream_id = f"audio_stream_{uuid.uuid4().hex}"

    return (
        VoiceRealtimeOpenRequest(
            profile_user_id=profile_user_id,
            conversation_id=conversation_id,
            session_id=session_id,
            voice_turn_id=voice_turn_id,
            audio_stream_id=audio_stream_id,
            disposition=disposition,
            language=language,
            character_pack_id=character_pack_id,
            input_format=input_format,
            sample_rate=sample_rate,
            channels=channels,
            output_mode=output_mode,
        ),
        "",
    )


def _bounded_text(
    value: Any,
    limit: int,
    *,
    allow_empty: bool = False,
) -> str | None:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return "" if allow_empty else None
    if len(normalized) > limit or any(ord(character) < 32 for character in normalized):
        return None
    return normalized
