from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from capcore_adapter_speech import (
    ASRSessionMode,
    ASRSessionUpdate,
    NormalizedASRSession,
    PCMStreamNormalizer,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from voicecore import (
    InputTurnStatus,
    TransitionResult,
    TransitionStatus,
    VoiceRuntimeSnapshot,
    initial_snapshot,
    reduce_event,
)
from voicecore.testing import EventFactory

from companion_v01.routes.voice import build_voice_router
from companion_v01.voice_runtime import (
    VOICE_PLAYBACK_OUTPUT_MODE,
    VoiceASRRealtimeTurnCoordinator,
    VoiceASRSessionBridge,
    VoiceRealtimeCoordinatorResolution,
)


@dataclass(frozen=True)
class _DispatchResult:
    status: str
    reason: str
    transition: TransitionResult

    @property
    def accepted(self) -> bool:
        return self.status == TransitionStatus.ACCEPTED.value


class _ReducerHost:
    def __init__(self, factory: EventFactory) -> None:
        self.snapshot: VoiceRuntimeSnapshot = initial_snapshot(
            factory.conversation_id,
            conversation_generation=factory.conversation_generation,
        )
        self.transitions: list[TransitionResult] = []

    def accept_event(self, event: Any) -> _DispatchResult:
        transition = reduce_event(self.snapshot, event)
        self.transitions.append(transition)
        if transition.status is TransitionStatus.ACCEPTED:
            self.snapshot = transition.snapshot
        return _DispatchResult(
            status=transition.status.value,
            reason=transition.reason,
            transition=transition,
        )


class _CallProviderSession:
    def __init__(self, *, commit_delay: float = 0.0) -> None:
        self.commit_delay = commit_delay
        self.feed_count = 0
        self.commit_count = 0
        self.finish_count = 0
        self.cancel_count = 0
        self.finalize_count = 0

    async def feed_audio(self, *, audio: bytes) -> dict[str, Any]:
        if not audio:
            raise ValueError("test audio must not be empty")
        self.feed_count += 1
        turn_number = self.commit_count + 1
        return {
            "quality": "partial",
            "stable_text": "",
            "unstable_tail": f"第{turn_number}轮",
            "provider_receipt_id": f"partial-{turn_number}",
        }

    async def commit_turn(self) -> dict[str, Any]:
        if self.commit_delay:
            await asyncio.sleep(self.commit_delay)
        self.commit_count += 1
        return {
            "quality": "final",
            "stable_text": f"第{self.commit_count}轮语音",
            "provider_receipt_id": f"final-{self.commit_count}",
        }

    async def finish_call(self) -> None:
        self.finish_count += 1

    async def finalize(self) -> dict[str, Any]:
        self.finalize_count += 1
        raise AssertionError("call-scoped transport must not finalize per turn")

    async def cancel(self) -> None:
        self.cancel_count += 1


class _CallDeliveryChannel:
    def __init__(self, *, voice_turn_id: str) -> None:
        self.voice_turn_id = voice_turn_id
        self.delivery_id = f"delivery-{voice_turn_id}"
        self.state = "waiting"
        self.notified = False
        self.close_reason = ""
        self._event: asyncio.Event | None = None
        self._taken = False

    async def wait_activity(self) -> None:
        if self.notified or self.state == "terminal":
            return
        self._event = asyncio.Event()
        await self._event.wait()

    def notify_runtime_change(self) -> None:
        self.notified = True
        if self._event is not None:
            self._event.set()

    def take_control_outbound(self) -> None:
        return None

    def take_outbound(self) -> Any:
        if not self.notified or self._taken:
            return None
        self._taken = True
        self.notified = False
        return SimpleNamespace(
            delivery_id=self.delivery_id,
            voice_turn_id=self.voice_turn_id,
            response_id=f"response-{self.voice_turn_id}",
            speech_unit_id=f"speech-{self.voice_turn_id}",
            ordinal=0,
            text=f"{self.voice_turn_id}的回复。",
            media_type="audio/mpeg",
            audio=b"ID3-call-route-audio",
        )

    def mark_sent(self, delivery_id: str) -> Any:
        if delivery_id != self.delivery_id or self.state != "waiting":
            return SimpleNamespace(
                ok=False,
                reason="route_delivery_send_invalid",
                retryable=False,
            )
        self.state = "sent"
        return SimpleNamespace(ok=True, status="sent", reason="", retryable=False)

    def acknowledge(self, message_type: str, payload: Any) -> Any:
        if payload.get("delivery_id") != self.delivery_id:
            return SimpleNamespace(
                ok=False,
                status="failed",
                reason="voice_playback_delivery_unknown",
                retryable=False,
            )
        expected = {
            "client.playback.enqueued": "sent",
            "client.playback.started": "enqueued",
            "client.playback.completed": "started",
        }
        if message_type not in expected or self.state != expected[message_type]:
            return SimpleNamespace(
                ok=False,
                status="failed",
                reason="voice_playback_ack_out_of_order",
                retryable=False,
            )
        self.state = {
            "client.playback.enqueued": "enqueued",
            "client.playback.started": "started",
            "client.playback.completed": "terminal",
        }[message_type]
        return SimpleNamespace(
            ok=True,
            status="accepted",
            reason="",
            retryable=False,
            response_terminal=self.state == "terminal",
            response_state=("completed" if self.state == "terminal" else "streaming"),
            delivery_status=("delivered" if self.state == "terminal" else ""),
            full_text=f"{self.voice_turn_id}的回复。",
        )

    def response_outcome(self) -> Any:
        return SimpleNamespace(
            response_terminal=self.state == "terminal",
            response_state=("completed" if self.state == "terminal" else "streaming"),
            delivery_status=("delivered" if self.state == "terminal" else ""),
            full_text=(f"{self.voice_turn_id}的回复。" if self.state == "terminal" else ""),
        )

    def close(self, *, reason: str) -> Any:
        self.close_reason = str(reason or "")
        self.state = "terminal"
        return self.response_outcome()


class _CallRuntime:
    def __init__(
        self,
        *,
        open_request: Any,
        provider: _CallProviderSession,
    ) -> None:
        self.open_request = open_request
        self.provider = provider
        self.provider_session = NormalizedASRSession(
            provider_session=provider,
            mode=ASRSessionMode.STREAMING,
        )
        self.provider_id = "provider.asr.call-route"
        self.voice_session_id = "voice-session-call-route"
        self.factory = EventFactory(
            conversation_id=open_request.conversation_id,
            voice_session_id=self.voice_session_id,
        )
        self.host = _ReducerHost(self.factory)
        self.requests: list[Any] = []
        self.delivery_channels: dict[str, _CallDeliveryChannel] = {}
        self.cancelled_responses: list[tuple[str, str]] = []

    def create_turn(self, request: Any) -> VoiceRealtimeCoordinatorResolution:
        self.requests.append(request)
        coordinator = VoiceASRRealtimeTurnCoordinator(
            provider_session=self.provider_session,
            retain_provider_session=True,
            bridge=VoiceASRSessionBridge(
                host=self.host,
                event_factory=self.factory,
                voice_turn_id=request.voice_turn_id,
                audio_stream_id=request.audio_stream_id,
                disposition=request.disposition,
            ),
            pcm_normalizer=PCMStreamNormalizer(
                input_format=request.input_format,
                input_sample_rate=request.sample_rate,
                input_channels=request.channels,
            ),
            response_starter=lambda: SimpleNamespace(
                status="started",
                reason="",
                response_id=f"response-{request.voice_turn_id}",
                retryable=False,
                safe_public_summary="",
            ),
        )
        delivery_channel = None
        if request.output_mode == VOICE_PLAYBACK_OUTPUT_MODE:
            delivery_channel = _CallDeliveryChannel(voice_turn_id=request.voice_turn_id)
            self.delivery_channels[request.voice_turn_id] = delivery_channel
        return VoiceRealtimeCoordinatorResolution.succeeded(
            coordinator,
            provider_id=self.provider_id,
            voice_session_id=self.voice_session_id,
            delivery_channel=delivery_channel,
        )

    def cancel_response(self, *, voice_turn_id: str, reason: str) -> Any:
        if voice_turn_id not in self.delivery_channels:
            return SimpleNamespace(status="failed", reason="voice_turn_unknown")
        self.cancelled_responses.append((voice_turn_id, reason))
        return SimpleNamespace(status="accepted", reason="")

    async def finish(self) -> ASRSessionUpdate:
        return await self.provider_session.finish_call()

    async def cancel(self) -> ASRSessionUpdate:
        return await self.provider_session.cancel()


class _CallFactory:
    def __init__(self, *, commit_delay: float = 0.0) -> None:
        self.commit_delay = commit_delay
        self.open_count = 0
        self.calls: list[_CallRuntime] = []

    async def __call__(self, request: Any) -> Any:
        self.open_count += 1
        call = _CallRuntime(
            open_request=request,
            provider=_CallProviderSession(commit_delay=self.commit_delay),
        )
        self.calls.append(call)
        return SimpleNamespace(
            ready=True,
            call=call,
            reason="",
            retryable=False,
            safe_public_summary="",
        )


class _RuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(
        self,
        name: str,
        *,
        duration_ms: float,
        ok: bool,
    ) -> None:
        self.observed.append((name, ok))


def _call_open_payload(*, output_mode: str = "text_only") -> dict[str, Any]:
    return {
        "type": "client.call.open",
        "protocol_version": 2,
        "profile_user_id": "master",
        "conversation_id": "conversation-call-route",
        "session_id": "session-call-route",
        "character_pack_id": "character-call-route",
        "language": "zh",
        "disposition": "message",
        "input": {
            "format": "s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
        "output": {"mode": output_mode},
    }


def _turn_start_payload(turn_number: int) -> dict[str, Any]:
    return {
        "type": "client.turn.start",
        "protocol_version": 2,
        "voice_turn_id": f"voice-turn-call-route-{turn_number}",
        "audio_stream_id": f"audio-stream-call-route-{turn_number}",
    }


class VoiceRealtimeCallRouteTests(unittest.TestCase):
    def _app(
        self,
        *,
        factory: _CallFactory,
        metrics: _RuntimeMetrics | None = None,
    ) -> FastAPI:
        app = FastAPI()
        app.include_router(
            build_voice_router(
                engine=SimpleNamespace(),
                config_module=SimpleNamespace(DATA_DIR=None),
                tts_client=None,
                runtime_metrics=metrics or _RuntimeMetrics(),
                log_event=lambda *_args, **_kwargs: None,
                realtime_asr_call_factory=factory,
            )
        )
        return app

    @staticmethod
    def _complete_text_turn(websocket: Any, turn_number: int) -> None:
        turn_start = _turn_start_payload(turn_number)
        websocket.send_json(turn_start)
        ready = websocket.receive_json()
        if ready["type"] != "server.turn.ready":
            raise AssertionError(ready)
        websocket.send_json(
            {
                "type": "client.audio",
                "voice_turn_id": turn_start["voice_turn_id"],
                "sequence": 0,
                "audio_clock_ms": 0,
            }
        )
        websocket.send_bytes(b"\x01\x00" * 160)
        partial = websocket.receive_json()
        if partial["type"] != "server.turn.partial":
            raise AssertionError(partial)
        websocket.send_json(
            {
                "type": "client.turn.endpoint",
                "voice_turn_id": turn_start["voice_turn_id"],
            }
        )
        finalizing = websocket.receive_json()
        if finalizing["type"] != "server.turn.finalizing":
            raise AssertionError(finalizing)
        final = websocket.receive_json()
        if final["type"] != "server.turn.final":
            raise AssertionError(final)

    def test_two_turns_reuse_one_websocket_and_one_provider_session(self) -> None:
        factory = _CallFactory()
        metrics = _RuntimeMetrics()
        with TestClient(self._app(factory=factory, metrics=metrics)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_call_open_payload())
                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "server.call.ready")
                self.assertEqual(ready["protocol_version"], 2)

                self._complete_text_turn(websocket, 1)
                self._complete_text_turn(websocket, 2)

                websocket.send_json(
                    {
                        "type": "client.call.close",
                        "reason": "user_hangup",
                    }
                )
                closed = websocket.receive_json()

        self.assertEqual(closed["type"], "server.call.closed")
        self.assertEqual(closed["status"], "finished")
        self.assertEqual(closed["completed_turns"], 2)
        self.assertEqual(factory.open_count, 1)
        self.assertEqual(len(factory.calls), 1)
        call = factory.calls[0]
        self.assertEqual(call.provider.feed_count, 2)
        self.assertEqual(call.provider.commit_count, 2)
        self.assertEqual(call.provider.finish_count, 1)
        self.assertEqual(call.provider.finalize_count, 0)
        self.assertEqual(call.provider.cancel_count, 0)
        self.assertEqual(
            {turn_id: turn.state for turn_id, turn in call.host.snapshot.input_turns.items()},
            {
                "voice-turn-call-route-1": InputTurnStatus.COMMITTED,
                "voice-turn-call-route-2": InputTurnStatus.COMMITTED,
            },
        )
        self.assertEqual(metrics.observed, [("asr_realtime_call", True)])

    def test_duplicate_endpoint_does_not_commit_the_turn_twice(self) -> None:
        factory = _CallFactory(commit_delay=0.05)
        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_call_open_payload())
                websocket.receive_json()
                turn = _turn_start_payload(1)
                websocket.send_json(turn)
                websocket.receive_json()
                websocket.send_json(
                    {
                        "type": "client.audio",
                        "voice_turn_id": turn["voice_turn_id"],
                        "sequence": 0,
                        "audio_clock_ms": 0,
                    }
                )
                websocket.send_bytes(b"\x01\x00" * 160)
                websocket.receive_json()
                endpoint = {
                    "type": "client.turn.endpoint",
                    "voice_turn_id": turn["voice_turn_id"],
                }
                websocket.send_json(endpoint)
                started = websocket.receive_json()
                websocket.send_json(endpoint)
                duplicate = websocket.receive_json()
                final = websocket.receive_json()
                websocket.send_json({"type": "client.call.close"})
                websocket.receive_json()

        self.assertEqual(started["type"], "server.turn.finalizing")
        self.assertEqual(started["status"], "started")
        self.assertEqual(duplicate["type"], "server.turn.finalizing")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(final["type"], "server.turn.final")
        self.assertEqual(factory.calls[0].provider.commit_count, 1)

    def test_playback_ack_remains_routable_after_next_turn_starts(self) -> None:
        factory = _CallFactory()
        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(
                    _call_open_payload(
                        output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
                    )
                )
                websocket.receive_json()
                self._complete_text_turn(websocket, 1)
                speech = websocket.receive_json()
                audio = websocket.receive_bytes()
                self.assertEqual(speech["type"], "server.speech")
                self.assertTrue(audio)

                websocket.send_json(_turn_start_payload(2))
                next_ready = websocket.receive_json()
                self.assertEqual(next_ready["type"], "server.turn.ready")

                delivery_id = speech["delivery_id"]
                for message_type in (
                    "client.playback.enqueued",
                    "client.playback.started",
                    "client.playback.completed",
                ):
                    websocket.send_json(
                        {
                            "type": message_type,
                            "delivery_id": delivery_id,
                        }
                    )
                    ack = websocket.receive_json()
                    self.assertEqual(ack["type"], "server.playback.ack")
                completed = websocket.receive_json()
                self.assertEqual(
                    completed["type"],
                    "server.response.completed",
                )
                self.assertEqual(
                    completed["voice_turn_id"],
                    "voice-turn-call-route-1",
                )

                websocket.send_json(
                    {
                        "type": "client.call.close",
                        "reason": "user_hangup",
                    }
                )
                closed = websocket.receive_json()

        self.assertEqual(closed["type"], "server.call.closed")
        self.assertEqual(closed["status"], "cancelled")
        first_channel = factory.calls[0].delivery_channels["voice-turn-call-route-1"]
        self.assertEqual(first_channel.state, "terminal")
        self.assertEqual(factory.calls[0].provider.cancel_count, 1)

    def test_disconnect_cancels_provider_and_open_input_turn(self) -> None:
        factory = _CallFactory()
        metrics = _RuntimeMetrics()
        with TestClient(self._app(factory=factory, metrics=metrics)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_call_open_payload())
                websocket.receive_json()
                websocket.send_json(_turn_start_payload(1))
                websocket.receive_json()

        deadline = time.monotonic() + 1.0
        while factory.calls[0].provider.cancel_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        call = factory.calls[0]
        self.assertEqual(call.provider.cancel_count, 1)
        self.assertEqual(call.provider.commit_count, 0)
        self.assertEqual(call.provider.finish_count, 0)
        self.assertEqual(
            call.host.snapshot.input_turns["voice-turn-call-route-1"].state,
            InputTurnStatus.CANCELLED,
        )
        self.assertEqual(metrics.observed, [("asr_realtime_call", False)])

    def test_response_cancel_keeps_call_open_and_closes_only_target_delivery(self) -> None:
        factory = _CallFactory()
        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(
                    _call_open_payload(output_mode=VOICE_PLAYBACK_OUTPUT_MODE)
                )
                websocket.receive_json()
                self._complete_text_turn(websocket, 1)
                speech = websocket.receive_json()
                websocket.receive_bytes()
                self.assertEqual(speech["type"], "server.speech")

                websocket.send_json(
                    {
                        "type": "client.response.cancel",
                        "protocol_version": 2,
                        "voice_turn_id": "voice-turn-call-route-1",
                        "reason": "new_voice_turn_committed",
                    }
                )
                cancelled = websocket.receive_json()
                self.assertEqual(cancelled["type"], "server.response.cancelled")
                self.assertEqual(cancelled["voice_turn_id"], "voice-turn-call-route-1")

                websocket.send_json(
                    _turn_start_payload(2)
                )
                next_ready = websocket.receive_json()
                self.assertEqual(next_ready["type"], "server.turn.ready")
                websocket.send_json(
                    {
                        "type": "client.call.close",
                        "reason": "test_complete",
                    }
                )
                closed = websocket.receive_json()

        self.assertEqual(closed["type"], "server.call.closed")
        call = factory.calls[0]
        self.assertEqual(
            call.cancelled_responses,
            [("voice-turn-call-route-1", "new_voice_turn_committed")],
        )
        self.assertEqual(
            call.delivery_channels["voice-turn-call-route-1"].close_reason,
            "new_voice_turn_committed",
        )


if __name__ == "__main__":
    unittest.main()
