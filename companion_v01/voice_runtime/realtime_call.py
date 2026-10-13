"""Call-scoped websocket state and turn lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any, Callable, Mapping

import anyio
from fastapi import WebSocket

from .playback_delivery import VOICE_PLAYBACK_OUTPUT_MODE
from .realtime_protocol import (
    VOICE_REALTIME_CALL_PROTOCOL_VERSION,
    VoiceRealtimeOpenRequest,
    VoiceRealtimeCoordinatorResolution,
    VoiceRealtimeCallFactory,
    _parse_call_open_request,
    _parse_turn_start_request,
)
from .realtime_call_input import RealtimeCallInputMixin
from .realtime_call_delivery import RealtimeCallDeliveryMixin
from .realtime_call_lifecycle import RealtimeCallLifecycleMixin


class VoiceRealtimeCallWebSocketSession(RealtimeCallInputMixin, RealtimeCallDeliveryMixin, RealtimeCallLifecycleMixin):
    """Transport many VoiceCore input turns over one call-scoped WebSocket."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        call_factory: VoiceRealtimeCallFactory | None,
        runtime_metrics: Any,
        log_event: Callable[..., None],
        websocket_accepted: bool = False,
        initial_message: Mapping[str, Any] | None = None,
    ) -> None:
        self.websocket = websocket
        self.call_factory = call_factory
        self.runtime_metrics = runtime_metrics
        self.log_event = log_event
        self.websocket_accepted = bool(websocket_accepted)
        self.initial_message = dict(initial_message) if initial_message is not None else None
        self.call_open_request: VoiceRealtimeOpenRequest | None = None
        self.call: Any | None = None
        self.provider_id = ""
        self.voice_session_id = ""
        self.current_request: VoiceRealtimeOpenRequest | None = None
        self.coordinator: Any | None = None
        self.pending_audio: tuple[int, int] | None = None
        self.finalize_task: asyncio.Task[Any] | None = None
        self.receive_task: asyncio.Task[dict[str, Any]] | None = None
        self.delivery_channels: dict[str, Any] = {}
        self.delivery_tasks: dict[asyncio.Task[None], str] = {}
        self.retired_delivery_tasks: list[asyncio.Task[None]] = []
        self.delivery_owners: dict[str, str] = {}
        self.control_owners: dict[str, str] = {}
        self.turn_final_sent = False
        self.terminal = False
        self.call_closed = False
        self.started_at = time.perf_counter()
        self.last_client_activity_at = self.started_at
        self.input_bytes = 0
        self.input_frames = 0
        self.completed_turns = 0
        self._terminal_reason = ""
        self._observed = False

    async def run(self) -> None:
        if not self.websocket_accepted:
            await self.websocket.accept()
        cancelled: asyncio.CancelledError | None = None
        try:
            if self.initial_message is not None:
                if not await self._dispatch_received_message(self.initial_message):
                    return
                self.initial_message = None
            if not self.terminal:
                self._arm_receive()
            while not self.terminal:
                assert self.receive_task is not None
                wait_for: set[asyncio.Task[Any]] = {
                    self.receive_task,
                    *self.delivery_tasks,
                }
                if self.finalize_task is not None:
                    wait_for.add(self.finalize_task)
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

                completed_deliveries = [task for task in done if task in self.delivery_tasks]
                if completed_deliveries:
                    for task in completed_deliveries:
                        voice_turn_id = self.delivery_tasks.pop(task)
                        try:
                            task.result()
                        except (Exception, asyncio.CancelledError) as exc:
                            self._log_transport_exception(exc)
                            await self._send_failed(
                                "voice_playback_wait_failed",
                                retryable=True,
                                terminal=True,
                            )
                            break
                        await self._handle_delivery_activity(voice_turn_id)
                        if self.terminal:
                            break
                        self._arm_delivery_wait(voice_turn_id)
                    continue

                if self.receive_task not in done:
                    continue
                message = self.receive_task.result()
                self.receive_task = None
                self.last_client_activity_at = time.perf_counter()
                if not await self._dispatch_received_message(message):
                    break
                if not self.terminal:
                    self._arm_receive()
        except asyncio.CancelledError as exc:
            cancelled = exc
        except RuntimeError:
            pass
        except Exception as exc:
            self._log_transport_exception(exc)
            await self._send_failed(
                "voice_realtime_call_transport_failed",
                retryable=True,
                terminal=True,
            )
        finally:
            with anyio.CancelScope(shield=True):
                await self._cleanup()
                self._observe_once(
                    ok=self.call_closed,
                    reason=(self._terminal_reason or ("" if self.call_closed else "client_disconnected")),
                )
        if cancelled is not None:
            raise cancelled

    def _arm_receive(self) -> None:
        if self.receive_task is not None or self.terminal:
            return
        self.receive_task = asyncio.create_task(
            self.websocket.receive(),
            name="voice-realtime-call-websocket-receive",
        )

    async def _dispatch_received_message(self, message: Mapping[str, Any]) -> bool:
        if message.get("type") == "websocket.disconnect":
            return False
        if message.get("bytes") is not None:
            await self._handle_audio_bytes(bytes(message["bytes"]))
            return True
        if message.get("text") is None:
            await self._send_failed("websocket_message_invalid", terminal=True)
            return True
        try:
            payload = json.loads(str(message["text"]))
        except (TypeError, ValueError):
            await self._send_failed("client_message_invalid_json", terminal=False)
            return True
        if not isinstance(payload, Mapping):
            await self._send_failed("client_message_must_be_object", terminal=False)
            return True
        await self._handle_text(payload)
        return True

    async def _handle_text(self, payload: Mapping[str, Any]) -> None:
        message_type = str(payload.get("type") or "")
        if message_type == "client.call.open":
            await self._handle_call_open(payload)
            return
        if self.call is None:
            await self._send_failed("client_call_open_required", terminal=False)
            return
        if message_type == "client.call.close":
            await self._handle_call_close(payload)
            return
        if message_type == "client.turn.start":
            await self._handle_turn_start(payload)
            return
        if message_type == "client.turn.endpoint":
            await self._handle_turn_endpoint(payload)
            return
        if message_type == "client.response.cancel":
            await self._handle_response_cancel(payload)
            return
        if message_type == "client.audio":
            await self._handle_audio_header(payload)
            return
        if message_type == "client.interruption.suspected":
            await self._handle_interruption_suspected(payload)
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

    async def _handle_call_open(self, payload: Mapping[str, Any]) -> None:
        if self.call_open_request is not None:
            await self._send_failed("client_call_open_already_received", terminal=False)
            return
        parsed, reason = _parse_call_open_request(payload)
        if parsed is None:
            await self._send_failed(reason, terminal=False)
            return
        self.call_open_request = parsed
        if self.call_factory is None:
            await self._send_failed(
                "voice_realtime_call_not_configured",
                terminal=True,
            )
            return
        try:
            resolution = self.call_factory(parsed)
            if inspect.isawaitable(resolution):
                resolution = await resolution
        except Exception as exc:
            try:
                self.log_event(
                    "asr_realtime_call_factory_exception",
                    error_type=type(exc).__name__[:96],
                )
            except Exception:
                pass
            await self._send_failed(
                "voice_realtime_call_factory_failed",
                retryable=True,
                terminal=True,
            )
            return
        call = getattr(resolution, "call", None)
        if not bool(getattr(resolution, "ready", False)) or call is None:
            await self._send_failed(
                str(getattr(resolution, "reason", "") or "voice_realtime_call_unavailable"),
                retryable=bool(getattr(resolution, "retryable", False)),
                safe_public_summary=str(getattr(resolution, "safe_public_summary", "") or ""),
                terminal=True,
            )
            return
        self.call = call
        self.provider_id = str(getattr(call, "provider_id", "") or "")
        self.voice_session_id = str(getattr(call, "voice_session_id", "") or "")
        await self.websocket.send_json(
            {
                "type": "server.call.ready",
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_session_id": self.voice_session_id,
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

    async def _handle_turn_start(self, payload: Mapping[str, Any]) -> None:
        if self.coordinator is not None:
            await self._send_failed(
                "voice_realtime_call_input_turn_active",
                terminal=False,
            )
            return
        assert self.call_open_request is not None
        request, reason = _parse_turn_start_request(
            self.call_open_request,
            payload,
        )
        if request is None:
            await self._send_failed(reason, terminal=False)
            return
        try:
            resolution = self.call.create_turn(request)
            if inspect.isawaitable(resolution):
                resolution = await resolution
        except Exception:
            await self._send_failed(
                "voice_realtime_turn_factory_failed",
                retryable=True,
                terminal=True,
            )
            return
        if not isinstance(resolution, VoiceRealtimeCoordinatorResolution):
            await self._send_failed(
                "voice_realtime_turn_factory_result_invalid",
                terminal=True,
            )
            return
        if not resolution.ready:
            await self._send_failed(
                resolution.reason or "voice_realtime_turn_unavailable",
                retryable=resolution.retryable,
                safe_public_summary=resolution.safe_public_summary,
                terminal=resolution.status == "unavailable",
            )
            return
        coordinator = resolution.coordinator
        channel = resolution.delivery_channel
        if request.output_mode == VOICE_PLAYBACK_OUTPUT_MODE and channel is None:
            await self._send_failed(
                "voice_playback_delivery_unavailable",
                retryable=True,
                terminal=True,
            )
            return
        self.current_request = request
        self.coordinator = coordinator
        self.pending_audio = None
        self.turn_final_sent = False
        if channel is not None:
            self.delivery_channels[request.voice_turn_id] = channel
        try:
            opened = await coordinator.open()
        except Exception:
            await self._send_failed(
                "voice_realtime_turn_open_failed",
                retryable=True,
                terminal=True,
            )
            return
        if not bool(getattr(opened, "ok", False)):
            await self._send_result_failure(opened, terminal=True)
            return
        if channel is not None:
            self._arm_delivery_wait(request.voice_turn_id)
        await self.websocket.send_json(
            {
                "type": "server.turn.ready",
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_session_id": self.voice_session_id,
                "voice_turn_id": request.voice_turn_id,
                "audio_stream_id": request.audio_stream_id,
            }
        )

    async def _handle_call_close(self, payload: Mapping[str, Any]) -> None:
        reason = str(payload.get("reason") or "client_call_closed")[:96]
        close_status = "finished"
        if self.finalize_task is not None:
            self.finalize_task.cancel()
            await asyncio.gather(self.finalize_task, return_exceptions=True)
            self.finalize_task = None
        if self.coordinator is not None:
            close_status = "cancelled"
            try:
                await self.coordinator.cancel(reason=reason)
            except Exception:
                await self._send_failed(
                    "voice_realtime_call_cancel_failed",
                    retryable=True,
                    terminal=True,
                )
                return
            self.coordinator = None
            self.current_request = None
            self.pending_audio = None
        for voice_turn_id in tuple(self.delivery_channels):
            self._close_delivery_channel(voice_turn_id, reason=reason)
        try:
            result = await self.call.finish() if close_status == "finished" else await self.call.cancel()
        except Exception:
            await self._send_failed(
                "voice_realtime_call_close_failed",
                retryable=True,
                terminal=True,
            )
            return
        if str(getattr(result, "status", "") or "") == "failed":
            await self._send_failed(
                str(getattr(result, "reason", "") or "voice_realtime_call_close_failed"),
                retryable=bool(getattr(result, "retryable", False)),
                safe_public_summary=str(getattr(result, "safe_public_summary", "") or ""),
                terminal=True,
            )
            return
        self.call_closed = True
        self.terminal = True
        await self.websocket.send_json(
            {
                "type": "server.call.closed",
                "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
                "voice_session_id": self.voice_session_id,
                "status": close_status,
                "reason": reason,
                "completed_turns": self.completed_turns,
            }
        )
        self._observe_once(ok=True)
        await self.websocket.close(
            code=1000,
            reason="voice_realtime_call_closed",
        )

    def _matches_current_turn(self, payload: Mapping[str, Any]) -> bool:
        if self.current_request is None:
            return False
        voice_turn_id = str(payload.get("voice_turn_id") or "")
        return not voice_turn_id or voice_turn_id == self.current_request.voice_turn_id
