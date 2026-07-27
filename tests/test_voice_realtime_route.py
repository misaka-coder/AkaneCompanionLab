from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from capcore_adapter_speech import (
    ASRSessionMode,
    ASRSessionOpenResult,
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


class _ProviderSession:
    def __init__(self, *, finalize_delay: float = 0.0) -> None:
        self.finalize_delay = finalize_delay
        self.feed_count = 0
        self.cancelled = False
        self.cancel_count = 0

    async def feed_audio(self, *, audio: bytes) -> dict[str, Any]:
        self.feed_count += 1
        if self.feed_count == 1:
            return {
                "quality": "partial",
                "stable_text": "",
                "unstable_tail": "你好",
                "provider_receipt_id": "partial-1",
            }
        return {
            "quality": "stable_checkpoint",
            "stable_text": "你好，伙伴",
            "provider_receipt_id": "checkpoint-1",
            "control_significant": True,
        }

    async def finalize(self) -> dict[str, Any]:
        if self.finalize_delay:
            await asyncio.sleep(self.finalize_delay)
        return {
            "quality": "final",
            "stable_text": "你好，伙伴",
            "provider_receipt_id": "final-1",
        }

    async def cancel(self) -> None:
        self.cancelled = True
        self.cancel_count += 1


class _Adapter:
    def __init__(self, provider_session: _ProviderSession) -> None:
        self.provider_session = provider_session

    async def open_session(self, **_kwargs: Any) -> ASRSessionOpenResult:
        return ASRSessionOpenResult.succeeded(
            ASRSessionMode.STREAMING,
            NormalizedASRSession(
                provider_session=self.provider_session,
                mode=ASRSessionMode.STREAMING,
            ),
        )


class _RuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))


class _CoordinatorFactory:
    def __init__(self, *, finalize_delay: float = 0.0) -> None:
        self.finalize_delay = finalize_delay
        self.requests: list[Any] = []
        self.hosts: list[_ReducerHost] = []
        self.providers: list[_ProviderSession] = []

    def __call__(self, request: Any) -> VoiceRealtimeCoordinatorResolution:
        self.requests.append(request)
        event_factory = EventFactory(
            conversation_id=request.conversation_id,
            voice_session_id=request.session_id,
        )
        host = _ReducerHost(event_factory)
        provider = _ProviderSession(finalize_delay=self.finalize_delay)
        bridge = VoiceASRSessionBridge(
            host=host,
            event_factory=event_factory,
            voice_turn_id=request.voice_turn_id,
            audio_stream_id=request.audio_stream_id,
            disposition=request.disposition,
        )
        coordinator = VoiceASRRealtimeTurnCoordinator(
            adapter=_Adapter(provider),
            bridge=bridge,
            language=request.language,
            pcm_normalizer=PCMStreamNormalizer(
                input_format=request.input_format,
                input_sample_rate=request.sample_rate,
                input_channels=request.channels,
            ),
        )
        self.hosts.append(host)
        self.providers.append(provider)
        return VoiceRealtimeCoordinatorResolution.succeeded(
            coordinator,
            provider_id="provider.asr.fake_realtime",
        )


def _open_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "client.open",
        "protocol_version": 1,
        "profile_user_id": "master",
        "conversation_id": "conversation-realtime-1",
        "session_id": "voice-session-realtime-1",
        "language": "zh",
        "disposition": "message",
        "input": {
            "format": "s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
    }
    payload.update(overrides)
    return payload


class VoiceRealtimeRouteTests(unittest.TestCase):
    def _app(
        self,
        *,
        factory: Any = None,
        metrics: _RuntimeMetrics | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> FastAPI:
        runtime_metrics = metrics or _RuntimeMetrics()
        captured_logs = logs if logs is not None else []

        def log_event(event: str, **fields: Any) -> None:
            captured_logs.append({"event": event, **fields})

        app = FastAPI()
        app.include_router(
            build_voice_router(
                engine=SimpleNamespace(),
                config_module=SimpleNamespace(DATA_DIR=None),
                tts_client=None,
                runtime_metrics=runtime_metrics,
                log_event=log_event,
                realtime_asr_coordinator_factory=factory,
            )
        )
        return app

    def test_realtime_route_streams_checkpoint_candidate_and_one_committed_final(self) -> None:
        factory = _CoordinatorFactory()
        metrics = _RuntimeMetrics()
        logs: list[dict[str, Any]] = []

        with TestClient(self._app(factory=factory, metrics=metrics, logs=logs)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "server.ready")
                self.assertEqual(ready["normalized_output"]["sample_rate"], 16000)

                websocket.send_json(
                    {"type": "client.audio", "sequence": 0, "audio_clock_ms": 0}
                )
                websocket.send_bytes(b"\x01\x00" * 160)
                partial = websocket.receive_json()
                self.assertEqual(partial["type"], "server.partial")
                self.assertEqual(partial["unstable_tail"], "你好")

                websocket.send_json(
                    {"type": "client.audio", "sequence": 1, "audio_clock_ms": 10}
                )
                websocket.send_bytes(b"\x02\x00" * 160)
                checkpoint = websocket.receive_json()
                candidate = websocket.receive_json()
                self.assertEqual(checkpoint["type"], "server.checkpoint")
                self.assertEqual(checkpoint["text"], "你好，伙伴")
                self.assertEqual(candidate["type"], "server.candidate_ready")
                self.assertFalse(candidate["playable"])
                self.assertTrue(candidate["speculative"])

                websocket.send_json({"type": "client.endpoint"})
                finalizing = websocket.receive_json()
                final = websocket.receive_json()
                self.assertEqual(finalizing["type"], "server.finalizing")
                self.assertEqual(final["type"], "server.final")
                self.assertEqual(final["text"], "你好，伙伴")
                self.assertEqual(final["commit_status"], "accepted")

        host = factory.hosts[0]
        request = factory.requests[0]
        turn = host.snapshot.input_turns[request.voice_turn_id]
        self.assertEqual(turn.state, InputTurnStatus.COMMITTED)
        projections = [
            projection
            for transition in host.transitions
            for projection in transition.projections
        ]
        self.assertEqual(
            [projection.kind for projection in projections],
            ["event.voice.asr_checkpoint", "message.user.voice"],
        )
        self.assertEqual(projections[-1].payload["text"], "你好，伙伴")
        self.assertEqual(metrics.observed, [("asr_realtime", True)])
        self.assertEqual(logs[0]["input_frames"], 2)
        self.assertNotIn("text", logs[0])

    def test_endpoint_is_idempotent_while_provider_final_is_pending(self) -> None:
        factory = _CoordinatorFactory(finalize_delay=0.15)

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_json(
                    {"type": "client.audio", "sequence": 0, "audio_clock_ms": 0}
                )
                websocket.send_bytes(b"\x01\x00" * 160)
                websocket.receive_json()
                websocket.send_json({"type": "client.endpoint"})
                first = websocket.receive_json()
                websocket.send_json({"type": "client.endpoint"})
                duplicate = websocket.receive_json()
                final = websocket.receive_json()

        self.assertEqual(first["type"], "server.finalizing")
        self.assertEqual(first["status"], "started")
        self.assertEqual(duplicate["type"], "server.finalizing")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(final["type"], "server.final")
        self.assertEqual(len(factory.hosts[0].snapshot.input_turns), 1)

    def test_invalid_or_out_of_order_messages_fail_structurally_without_fake_audio(self) -> None:
        factory = _CoordinatorFactory()

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_bytes(b"\x01\x00")
                missing_open = websocket.receive_json()
                self.assertEqual(missing_open["reason"], "client_open_required")
                self.assertFalse(missing_open["terminal"])

                websocket.send_json(
                    _open_payload(input={"format": "mp3", "sample_rate": 16000, "channels": 1})
                )
                invalid_format = websocket.receive_json()
                self.assertEqual(
                    invalid_format["reason"],
                    "voice_realtime_input_format_unsupported",
                )
                self.assertFalse(invalid_format["terminal"])

                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_bytes(b"\x01\x00")
                missing_metadata = websocket.receive_json()
                self.assertEqual(missing_metadata["reason"], "audio_metadata_required")
                self.assertFalse(missing_metadata["terminal"])

        self.assertEqual(factory.providers[0].feed_count, 0)
        self.assertTrue(factory.providers[0].cancelled)

    def test_sequence_gap_fails_the_turn_and_cancels_provider(self) -> None:
        factory = _CoordinatorFactory()

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_json(
                    {"type": "client.audio", "sequence": 0, "audio_clock_ms": 0}
                )
                websocket.send_bytes(b"\x01\x00" * 160)
                websocket.receive_json()
                websocket.send_json(
                    {"type": "client.audio", "sequence": 2, "audio_clock_ms": 20}
                )
                websocket.send_bytes(b"\x01\x00" * 160)
                failed = websocket.receive_json()

        self.assertEqual(failed["type"], "server.failed")
        self.assertEqual(failed["reason"], "pcm_sequence_gap")
        self.assertTrue(failed["terminal"])
        self.assertTrue(factory.providers[0].cancelled)
        turn = factory.hosts[0].snapshot.input_turns[factory.requests[0].voice_turn_id]
        self.assertEqual(turn.state, InputTurnStatus.FAILED)

    def test_disconnect_cancels_open_provider_without_committing_a_message(self) -> None:
        factory = _CoordinatorFactory()

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                websocket.receive_json()

        deadline = time.monotonic() + 1.0
        while not factory.providers[0].cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(factory.providers[0].cancelled)
        projections = [
            projection
            for transition in factory.hosts[0].transitions
            for projection in transition.projections
        ]
        self.assertEqual(projections, [])

    def test_repeated_client_cancel_has_one_provider_side_effect_and_no_commit(self) -> None:
        factory = _CoordinatorFactory()

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_json({"type": "client.cancel", "reason": "user_stopped"})
                websocket.send_json({"type": "client.cancel", "reason": "user_stopped"})
                cancelled = websocket.receive_json()

        self.assertEqual(cancelled["type"], "server.cancelled")
        self.assertEqual(cancelled["reason"], "user_stopped")
        self.assertEqual(factory.providers[0].cancel_count, 1)
        turn = factory.hosts[0].snapshot.input_turns[factory.requests[0].voice_turn_id]
        self.assertEqual(turn.state, InputTurnStatus.CANCELLED)
        projections = [
            projection
            for transition in factory.hosts[0].transitions
            for projection in transition.projections
        ]
        self.assertEqual(projections, [])

    def test_missing_runtime_factory_reports_unavailable_without_opening_provider(self) -> None:
        metrics = _RuntimeMetrics()

        with TestClient(self._app(metrics=metrics)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                failed = websocket.receive_json()

        self.assertEqual(failed["type"], "server.failed")
        self.assertEqual(failed["reason"], "voice_realtime_not_configured")
        self.assertTrue(failed["terminal"])
        self.assertEqual(metrics.observed, [("asr_realtime", False)])


if __name__ == "__main__":
    unittest.main()
