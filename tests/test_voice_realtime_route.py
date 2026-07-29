from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

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
    VOICE_PLAYBACK_OUTPUT_MODE,
    VoiceASRRealtimeTurnCoordinator,
    VoiceASRSessionBridge,
    VoiceRealtimeCoordinatorResolution,
)


class _RouteDeliveryChannel:
    def __init__(self, *, voice_turn_id: str) -> None:
        self.voice_turn_id = voice_turn_id
        self.delivery_id = "voice-delivery-route-1"
        self.state = "waiting"
        self.notified = False
        self._event: asyncio.Event | None = None
        self._request_taken = False
        self._control_request: Any | None = None
        self._control_taken = False
        self._control_sent = False

    async def wait_activity(self) -> None:
        if self.notified or self.state == "terminal":
            return
        self._event = asyncio.Event()
        await self._event.wait()

    def notify_runtime_change(self) -> None:
        self.notified = True
        if self._event is not None:
            self._event.set()

    def take_outbound(self) -> Any:
        if not self.notified or self._request_taken:
            return None
        self._request_taken = True
        self.notified = False
        return SimpleNamespace(
            delivery_id=self.delivery_id,
            voice_turn_id=self.voice_turn_id,
            response_id="response-route-1",
            speech_unit_id="speech-route-1",
            ordinal=0,
            text="这是首个完整语音单元。",
            media_type="audio/mpeg",
            audio=b"ID3-route-binary-audio",
        )

    def queue_control(self, *, action: str) -> Any:
        self._control_request = SimpleNamespace(
            control_id=f"voice-control-route-{action}",
            command_id=f"voice-command-route-{action}",
            action=action,
            delivery_id=self.delivery_id,
            voice_turn_id=self.voice_turn_id,
            response_id="response-route-1",
            speech_unit_id="speech-route-1",
            resume_token="route-resume-1" if action == "resume" else "",
            interruption_id="route-interruption-1",
            reason="",
        )
        self._control_taken = False
        self._control_sent = False
        self.notify_runtime_change()
        return self._control_request

    def take_control_outbound(self) -> Any:
        if self._control_request is None or self._control_taken:
            return None
        self._control_taken = True
        self.notified = False
        return self._control_request

    def mark_control_sent(self, control_id: str) -> Any:
        if control_id != self._control_request.control_id or self._control_sent:
            return SimpleNamespace(ok=False, reason="route_control_send_invalid", retryable=False)
        self._control_sent = True
        return SimpleNamespace(ok=True, status="sent", reason="", retryable=False)

    def mark_sent(self, delivery_id: str) -> Any:
        if delivery_id != self.delivery_id or self.state != "waiting":
            return SimpleNamespace(ok=False, reason="route_delivery_send_invalid", retryable=False)
        self.state = "sent"
        return SimpleNamespace(ok=True, status="sent", reason="", retryable=False)

    def acknowledge(self, message_type: str, payload: Any) -> Any:
        if message_type == "client.playback.control_ack":
            control = self._control_request
            if (
                control is None
                or not self._control_sent
                or payload.get("control_id") != control.control_id
                or payload.get("command_id") != control.command_id
                or payload.get("action") != control.action
                or payload.get("status") != "applied"
            ):
                return SimpleNamespace(
                    ok=False,
                    status="failed",
                    reason="voice_playback_control_ack_invalid",
                    retryable=False,
                )
            self._control_request = None
            return SimpleNamespace(
                ok=True,
                status="accepted",
                reason="",
                retryable=False,
                response_terminal=False,
            )
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
            response_state="completed" if self.state == "terminal" else "streaming",
            delivery_status="delivered" if self.state == "terminal" else "",
            full_text="这是首个完整语音单元。",
        )

    def response_outcome(self) -> Any:
        return SimpleNamespace(
            response_terminal=self.state == "terminal",
            response_state="completed" if self.state == "terminal" else "streaming",
            delivery_status="delivered" if self.state == "terminal" else "",
            full_text="这是首个完整语音单元。" if self.state == "terminal" else "",
        )

    def close(self, *, reason: str) -> Any:
        self.state = "terminal"
        return self.response_outcome()


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
    def __init__(
        self,
        *,
        finalize_delay: float = 0.0,
        response_status: str = "started",
    ) -> None:
        self.finalize_delay = finalize_delay
        self.response_status = response_status
        self.requests: list[Any] = []
        self.hosts: list[_ReducerHost] = []
        self.providers: list[_ProviderSession] = []
        self.delivery_channels: list[_RouteDeliveryChannel] = []

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
            response_starter=lambda: SimpleNamespace(
                status=self.response_status,
                reason=("" if self.response_status == "started" else "voice_response_start_failed"),
                response_id="response-route-1",
                retryable=self.response_status != "started",
                safe_public_summary=(
                    "" if self.response_status == "started" else "语音已经识别，但回复生成暂时无法启动。"
                ),
            ),
        )
        self.hosts.append(host)
        self.providers.append(provider)
        delivery_channel = None
        if request.output_mode == VOICE_PLAYBACK_OUTPUT_MODE:
            delivery_channel = _RouteDeliveryChannel(voice_turn_id=request.voice_turn_id)
            self.delivery_channels.append(delivery_channel)
        return VoiceRealtimeCoordinatorResolution.succeeded(
            coordinator,
            provider_id="provider.asr.fake_realtime",
            voice_session_id="voice-session-server-1",
            delivery_channel=delivery_channel,
        )


def _open_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "client.open",
        "protocol_version": 1,
        "profile_user_id": "master",
        "conversation_id": "conversation-realtime-1",
        "session_id": "voice-session-realtime-1",
        "character_pack_id": "character-realtime-1",
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
                self.assertEqual(ready["voice_session_id"], "voice-session-server-1")
                self.assertEqual(ready["normalized_output"]["sample_rate"], 16000)

                websocket.send_json({"type": "client.audio", "sequence": 0, "audio_clock_ms": 0})
                websocket.send_bytes(b"\x01\x00" * 160)
                partial = websocket.receive_json()
                self.assertEqual(partial["type"], "server.partial")
                self.assertEqual(partial["unstable_tail"], "你好")

                websocket.send_json({"type": "client.audio", "sequence": 1, "audio_clock_ms": 10})
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
                self.assertEqual(final["response"]["status"], "started")
                self.assertEqual(
                    final["response"]["response_id"],
                    "response-route-1",
                )

        host = factory.hosts[0]
        request = factory.requests[0]
        self.assertEqual(request.character_pack_id, "character-realtime-1")
        turn = host.snapshot.input_turns[request.voice_turn_id]
        self.assertEqual(turn.state, InputTurnStatus.COMMITTED)
        projections = [projection for transition in host.transitions for projection in transition.projections]
        self.assertEqual(
            [projection.kind for projection in projections],
            ["event.voice.asr_checkpoint", "message.user.voice"],
        )
        self.assertEqual(projections[-1].payload["text"], "你好，伙伴")
        self.assertEqual(metrics.observed, [("asr_realtime", True)])
        self.assertEqual(logs[0]["input_frames"], 2)
        self.assertNotIn("text", logs[0])

    def test_realtime_route_sends_binary_speech_and_waits_for_ordered_playback_acks(self) -> None:
        factory = _CoordinatorFactory()
        payload = _open_payload(output={"mode": VOICE_PLAYBACK_OUTPUT_MODE})

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(payload)
                ready = websocket.receive_json()
                self.assertEqual(
                    ready["output"],
                    {
                        "mode": VOICE_PLAYBACK_OUTPUT_MODE,
                        "acknowledgements_required": True,
                        "playback_controls": ["duck", "resume", "stop"],
                    },
                )
                websocket.send_json({"type": "client.audio", "sequence": 0, "audio_clock_ms": 0})
                websocket.send_bytes(b"\x01\x00" * 160)
                websocket.receive_json()
                websocket.send_json({"type": "client.endpoint"})
                self.assertEqual(websocket.receive_json()["type"], "server.finalizing")
                self.assertEqual(websocket.receive_json()["type"], "server.final")

                speech = websocket.receive_json()
                audio = websocket.receive_bytes()
                self.assertEqual(speech["type"], "server.speech")
                self.assertTrue(speech["binary_follows"])
                self.assertEqual(speech["text"], "这是首个完整语音单元。")
                self.assertEqual(speech["byte_length"], len(audio))
                self.assertEqual(audio, b"ID3-route-binary-audio")
                self.assertNotIn("artifact", str(speech).lower())
                self.assertNotIn("path", str(speech).lower())

                websocket.send_json(
                    {
                        "type": "client.playback.completed",
                        "delivery_id": speech["delivery_id"],
                        "played_ms": 640,
                    }
                )
                rejected = websocket.receive_json()
                self.assertEqual(rejected["type"], "server.failed")
                self.assertEqual(
                    rejected["reason"],
                    "voice_playback_ack_out_of_order",
                )
                self.assertFalse(rejected["terminal"])

                for message_type in (
                    "client.playback.enqueued",
                    "client.playback.started",
                ):
                    ack: dict[str, Any] = {
                        "type": message_type,
                        "delivery_id": speech["delivery_id"],
                    }
                    if message_type == "client.playback.started":
                        ack["resume_token"] = "route-resume-1"
                    websocket.send_json(ack)
                    confirmed = websocket.receive_json()
                    self.assertEqual(confirmed["type"], "server.playback.ack")
                    self.assertEqual(confirmed["ack_type"], message_type)

                control = factory.delivery_channels[0].queue_control(action="duck")
                control_frame = websocket.receive_json()
                self.assertEqual(control_frame["type"], "server.playback.control")
                self.assertEqual(control_frame["control_id"], control.control_id)
                self.assertEqual(control_frame["command_id"], control.command_id)
                self.assertEqual(control_frame["action"], "duck")
                self.assertEqual(control_frame["delivery_id"], speech["delivery_id"])
                websocket.send_json(
                    {
                        "type": "client.playback.control_ack",
                        "control_id": control.control_id,
                        "command_id": control.command_id,
                        "action": "duck",
                        "status": "applied",
                        "played_ms": 320,
                        "applied_volume": 0.2,
                    }
                )
                control_ack = websocket.receive_json()
                self.assertEqual(control_ack["type"], "server.playback.ack")
                self.assertEqual(control_ack["ack_type"], "client.playback.control_ack")
                self.assertEqual(control_ack["control_id"], control.control_id)

                websocket.send_json(
                    {
                        "type": "client.playback.completed",
                        "delivery_id": speech["delivery_id"],
                        "played_ms": 640,
                    }
                )
                completed_ack = websocket.receive_json()
                self.assertEqual(completed_ack["type"], "server.playback.ack")
                self.assertEqual(completed_ack["ack_type"], "client.playback.completed")

                completed = websocket.receive_json()
                self.assertEqual(completed["type"], "server.response.completed")
                self.assertEqual(completed["state"], "completed")
                self.assertEqual(completed["delivery_status"], "delivered")
                self.assertEqual(completed["speech"], "这是首个完整语音单元。")

        self.assertEqual(factory.delivery_channels[0].state, "terminal")

    def test_endpoint_is_idempotent_while_provider_final_is_pending(self) -> None:
        factory = _CoordinatorFactory(finalize_delay=0.15)

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_json({"type": "client.audio", "sequence": 0, "audio_clock_ms": 0})
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

    def test_finalize_timeout_fails_structurally_and_cancels_provider(self) -> None:
        factory = _CoordinatorFactory(finalize_delay=1.0)
        metrics = _RuntimeMetrics()
        logs: list[dict[str, Any]] = []

        with patch(
            "companion_v01.voice_runtime.realtime_transport.VOICE_REALTIME_FINALIZE_TIMEOUT_SECONDS",
            0.05,
        ):
            with TestClient(self._app(factory=factory, metrics=metrics, logs=logs)) as client:
                with client.websocket_connect("/voice/realtime") as websocket:
                    websocket.send_json(_open_payload())
                    websocket.receive_json()
                    websocket.send_json({"type": "client.audio", "sequence": 0, "audio_clock_ms": 0})
                    websocket.send_bytes(b"\x01\x00" * 160)
                    websocket.receive_json()
                    websocket.send_json({"type": "client.endpoint"})
                    finalizing = websocket.receive_json()
                    failed = websocket.receive_json()

        self.assertEqual(finalizing["type"], "server.finalizing")
        self.assertEqual(failed["type"], "server.failed")
        self.assertEqual(failed["reason"], "voice_realtime_finalize_timeout")
        self.assertTrue(failed["terminal"])
        self.assertTrue(factory.providers[0].cancelled)
        self.assertEqual(metrics.observed, [("asr_realtime", False)])
        self.assertEqual(logs[0]["reason"], "voice_realtime_finalize_timeout")

    def test_committed_transcript_reports_response_start_failure_without_hanging(self) -> None:
        factory = _CoordinatorFactory(response_status="failed")
        metrics = _RuntimeMetrics()
        logs: list[dict[str, Any]] = []

        with TestClient(self._app(factory=factory, metrics=metrics, logs=logs)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_json({"type": "client.audio", "sequence": 0, "audio_clock_ms": 0})
                websocket.send_bytes(b"\x01\x00" * 160)
                websocket.receive_json()
                websocket.send_json({"type": "client.endpoint"})
                self.assertEqual(websocket.receive_json()["type"], "server.finalizing")
                final = websocket.receive_json()
                failed = websocket.receive_json()

        self.assertEqual(final["type"], "server.final")
        self.assertEqual(final["response"]["status"], "failed")
        self.assertEqual(failed["type"], "server.response.failed")
        self.assertEqual(failed["reason"], "voice_response_start_failed")
        self.assertEqual(failed["delivery_status"], "not_started")
        self.assertEqual(metrics.observed, [("asr_realtime", False)])
        self.assertEqual(logs[0]["reason"], "voice_response_start_failed")

    def test_invalid_or_out_of_order_messages_fail_structurally_without_fake_audio(self) -> None:
        factory = _CoordinatorFactory()

        with TestClient(self._app(factory=factory)) as client:
            with client.websocket_connect("/voice/realtime") as websocket:
                websocket.send_bytes(b"\x01\x00")
                missing_open = websocket.receive_json()
                self.assertEqual(missing_open["reason"], "client_open_required")
                self.assertFalse(missing_open["terminal"])

                websocket.send_json(_open_payload(input={"format": "mp3", "sample_rate": 16000, "channels": 1}))
                invalid_format = websocket.receive_json()
                self.assertEqual(
                    invalid_format["reason"],
                    "voice_realtime_input_format_unsupported",
                )
                self.assertFalse(invalid_format["terminal"])

                websocket.send_json(_open_payload(output={"mode": "implicit_fake_playback"}))
                invalid_output = websocket.receive_json()
                self.assertEqual(
                    invalid_output["reason"],
                    "voice_realtime_output_mode_unsupported",
                )
                self.assertFalse(invalid_output["terminal"])

                websocket.send_json(_open_payload())
                websocket.receive_json()
                websocket.send_json(
                    {
                        "type": "client.interruption.suspected",
                        "audio_clock_ms": -1,
                    }
                )
                invalid_interruption = websocket.receive_json()
                self.assertEqual(
                    invalid_interruption["reason"],
                    "voice_interruption_audio_clock_invalid",
                )
                self.assertFalse(invalid_interruption["terminal"])

                websocket.send_json(
                    {
                        "type": "client.interruption.suspected",
                        "audio_clock_ms": 120,
                    }
                )
                skipped_interruption = websocket.receive_json()
                self.assertEqual(
                    skipped_interruption["type"],
                    "server.interruption.skipped",
                )
                self.assertEqual(
                    skipped_interruption["reason"],
                    "voice_playback_not_active",
                )
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
                websocket.send_json({"type": "client.audio", "sequence": 0, "audio_clock_ms": 0})
                websocket.send_bytes(b"\x01\x00" * 160)
                websocket.receive_json()
                websocket.send_json({"type": "client.audio", "sequence": 2, "audio_clock_ms": 20})
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
            projection for transition in factory.hosts[0].transitions for projection in transition.projections
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
            projection for transition in factory.hosts[0].transitions for projection in transition.projections
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
