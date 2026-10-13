"""Single-turn websocket lifecycle; input and delivery operate on this state."""

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
    VoiceRealtimeOpenRequest,
    VoiceRealtimeCoordinatorResolution,
    VoiceRealtimeCoordinatorFactory,
    VOICE_REALTIME_PROTOCOL_VERSION,
    VOICE_REALTIME_INPUT_INACTIVITY_TIMEOUT_SECONDS,
    _parse_open_request,
)
from .realtime_turn_input import RealtimeTurnInputMixin
from .realtime_turn_delivery import RealtimeTurnDeliveryMixin


class VoiceRealtimeWebSocketSession(RealtimeTurnInputMixin, RealtimeTurnDeliveryMixin):
    """Transport one ordered PCM stream into a VoiceASR turn coordinator."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        coordinator_factory: VoiceRealtimeCoordinatorFactory | None,
        runtime_metrics: Any,
        log_event: Callable[..., None],
        websocket_accepted: bool = False,
        initial_message: Mapping[str, Any] | None = None,
    ) -> None:
        self.websocket = websocket
        self.coordinator_factory = coordinator_factory
        self.runtime_metrics = runtime_metrics
        self.log_event = log_event
        self.websocket_accepted = bool(websocket_accepted)
        self.initial_message = dict(initial_message) if initial_message is not None else None
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
                    task = self.delivery_task
                    self.delivery_task = None
                    try:
                        task.result()
                    except (Exception, asyncio.CancelledError) as exc:
                        self._log_transport_exception(exc)
                        await self._send_failed(
                            "voice_playback_wait_failed",
                            retryable=True,
                            terminal=True,
                        )
                        continue
                    await self._handle_delivery_activity()
                    if not self.terminal:
                        self._arm_delivery_wait()
                    continue

                if self.receive_task not in done:
                    continue
                message = self.receive_task.result()
                self.last_client_activity_at = time.perf_counter()
                self.receive_task = None
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

    def _arm_receive(self) -> None:
        if self.receive_task is not None or self.terminal:
            return
        self.receive_task = asyncio.create_task(
            self.websocket.receive(),
            name="voice-realtime-websocket-receive",
        )

    async def _dispatch_received_message(self, message: Mapping[str, Any]) -> bool:
        if message.get("type") == "websocket.disconnect":
            return False
        if message.get("bytes") is not None:
            await self._handle_audio_bytes(bytes(message["bytes"]))
            return True
        if message.get("text") is not None:
            await self._handle_text(str(message["text"]))
            return True
        await self._send_failed("websocket_message_invalid", terminal=True)
        return True

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
