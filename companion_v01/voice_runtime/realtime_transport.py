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


VOICE_REALTIME_PROTOCOL_VERSION = 1
VOICE_REALTIME_MAX_FRAME_BYTES = 1024 * 1024


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


@dataclass(frozen=True)
class VoiceRealtimeCoordinatorResolution:
    status: str
    reason: str = ""
    coordinator: Any | None = None
    provider_id: str = ""
    voice_session_id: str = ""
    retryable: bool = False
    safe_public_summary: str = ""

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
    ) -> VoiceRealtimeCoordinatorResolution:
        return cls(
            status="ready",
            coordinator=coordinator,
            provider_id=str(provider_id or ""),
            voice_session_id=str(voice_session_id or ""),
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
        self.terminal = False
        self.final_sent = False
        self.started_at = time.perf_counter()
        self.input_bytes = 0
        self.input_frames = 0
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
                done, _pending = await asyncio.wait(
                    wait_for,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self.finalize_task is not None and self.finalize_task in done:
                    await self._handle_finalize_completion()
                    continue

                if self.receive_task not in done:
                    continue
                message = self.receive_task.result()
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
        except Exception:
            await self._send_failed("voice_realtime_transport_failed", retryable=True, terminal=True)
        finally:
            with anyio.CancelScope(shield=True):
                if self.receive_task is not None and not self.receive_task.done():
                    self.receive_task.cancel()
                    await asyncio.gather(self.receive_task, return_exceptions=True)
                if not self.terminal and self.coordinator is not None:
                    try:
                        await self.coordinator.cancel(reason="client_disconnected")
                    except Exception:
                        pass
                if self.finalize_task is not None and not self.finalize_task.done():
                    self.finalize_task.cancel()
                    await asyncio.gather(self.finalize_task, return_exceptions=True)
                self._observe_once(ok=self.final_sent or self.terminal)
        if cancelled is not None:
            raise cancelled

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
        if message_type == "client.endpoint":
            await self._handle_endpoint()
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
                **({"provider_id": self.provider_id} if self.provider_id else {}),
            }
        )

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
            self.coordinator.settle_finalize(),
            name=f"voice-realtime-finalize-{self.open_request.voice_turn_id if self.open_request else 'unknown'}",
        )

    async def _handle_finalize_completion(self) -> None:
        assert self.finalize_task is not None
        task = self.finalize_task
        self.finalize_task = None
        try:
            result = task.result()
        except asyncio.CancelledError:
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
        self.terminal = True
        self._observe_once(ok=True)
        await self.websocket.close(code=1000, reason="voice_realtime_complete")

    async def _handle_cancel(self, payload: Mapping[str, Any]) -> None:
        reason = str(payload.get("reason") or "client_cancelled")[:96]
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
                payload["commit_status"] = str(
                    getattr(getattr(result, "bridge_result", None), "status", "") or ""
                )
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
        self.terminal = True
        self._observe_once(ok=False)
        try:
            await self.websocket.close(code=1011, reason="voice_realtime_failed")
        except Exception:
            pass

    def _observe_once(self, *, ok: bool) -> None:
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
                duration_ms=round(duration_ms, 1),
                input_frames=self.input_frames,
                input_bytes=self.input_bytes,
                provider=self.provider_id,
            )
        except Exception:
            pass


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
