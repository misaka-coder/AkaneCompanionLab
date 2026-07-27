from __future__ import annotations

import asyncio
import struct
import unittest
from dataclasses import dataclass

from capcore_adapter_speech import (
    ASRSessionMode,
    ASRSessionOpenResult,
    NormalizedASRSession,
    PCMStreamNormalizer,
)
from companion_v01.voice_runtime import (
    VoiceASRRealtimeTurnCoordinator,
    VoiceASRSessionBridge,
)
from voicecore import (
    InputTurnStatus,
    TransitionResult,
    TransitionStatus,
    VoiceRuntimeSnapshot,
    initial_snapshot,
    reduce_event,
)
from voicecore.testing import EventFactory


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

    def accept_event(self, event):
        transition = reduce_event(self.snapshot, event)
        self.transitions.append(transition)
        if transition.status is TransitionStatus.ACCEPTED:
            self.snapshot = transition.snapshot
        return _DispatchResult(
            status=transition.status.value,
            reason=transition.reason,
            transition=transition,
        )


class _DelayedFinalProviderSession:
    def __init__(self) -> None:
        self.allow_final = asyncio.Event()
        self.cancelled = False
        self.feed_count = 0

    async def feed_audio(self, *, audio: bytes):
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

    async def finalize(self):
        await self.allow_final.wait()
        return {
            "quality": "final",
            "stable_text": "你好，伙伴",
            "provider_receipt_id": "final-1",
        }

    async def cancel(self) -> None:
        self.cancelled = True


class _Adapter:
    def __init__(self, provider_session: _DelayedFinalProviderSession) -> None:
        self.provider_session = provider_session

    async def open_session(self, **_kwargs):
        return ASRSessionOpenResult.succeeded(
            ASRSessionMode.STREAMING,
            NormalizedASRSession(
                provider_session=self.provider_session,
                mode=ASRSessionMode.STREAMING,
            ),
        )


class _FailedAdapter:
    async def open_session(self, **_kwargs):
        return ASRSessionOpenResult.failed(
            ASRSessionMode.STREAMING,
            "asr_provider_auth_failed",
            retryable=False,
            provider_status="http_401",
            safe_public_summary="语音识别服务鉴权失败。",
        )


class VoiceASRRealtimeTurnCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def _coordinator(self, adapter):
        factory = EventFactory()
        host = _ReducerHost(factory)
        bridge = VoiceASRSessionBridge(
            host=host,
            event_factory=factory,
            voice_turn_id="turn-realtime-1",
            audio_stream_id="microphone-1",
        )
        return (
            VoiceASRRealtimeTurnCoordinator(
                adapter=adapter,
                bridge=bridge,
                language="zh",
            ),
            host,
        )

    async def test_checkpoint_can_seed_work_while_final_remains_pending(self) -> None:
        provider = _DelayedFinalProviderSession()
        coordinator, host = self._coordinator(_Adapter(provider))

        opened = await coordinator.open()
        partial = await coordinator.feed_audio(b"first")
        checkpoint = await coordinator.feed_audio(b"second")
        started = coordinator.start_finalize()
        await asyncio.sleep(0)

        self.assertTrue(opened.ok)
        self.assertIsNone(partial.early_candidate)
        self.assertTrue(checkpoint.ok)
        assert checkpoint.early_candidate is not None
        self.assertEqual(checkpoint.early_candidate.stable_text, "你好，伙伴")
        self.assertEqual(checkpoint.early_candidate.source_turn_revision, 2)
        self.assertEqual(started.status, "started")
        self.assertTrue(started.final_pending)
        self.assertEqual(started.early_candidate, checkpoint.early_candidate)
        self.assertTrue(coordinator.final_pending)
        self.assertEqual(
            host.snapshot.input_turns["turn-realtime-1"].state,
            InputTurnStatus.CAPTURING,
        )
        projections_before_final = [
            projection for transition in host.transitions for projection in transition.projections
        ]
        self.assertEqual(
            [projection.kind for projection in projections_before_final],
            ["event.voice.asr_checkpoint"],
        )

        provider.allow_final.set()
        settled = await coordinator.settle_finalize()

        self.assertTrue(settled.ok)
        self.assertFalse(coordinator.final_pending)
        turn = host.snapshot.input_turns["turn-realtime-1"]
        self.assertEqual(turn.state, InputTurnStatus.COMMITTED)
        self.assertEqual(turn.final_revision, 3)
        projections = [projection for transition in host.transitions for projection in transition.projections]
        self.assertEqual(
            [projection.kind for projection in projections],
            ["event.voice.asr_checkpoint", "message.user.voice"],
        )
        self.assertEqual(projections[-1].payload["text"], "你好，伙伴")

        replay = await coordinator.settle_finalize()
        self.assertEqual(replay.status, "duplicate")
        replay_projections = [projection for transition in host.transitions for projection in transition.projections]
        self.assertEqual(replay_projections, projections)

    async def test_open_failure_is_structured_into_the_voice_turn(self) -> None:
        coordinator, host = self._coordinator(_FailedAdapter())

        result = await coordinator.open()

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "asr_provider_auth_failed")
        self.assertFalse(result.retryable)
        self.assertEqual(result.provider_status, "http_401")
        turn = host.snapshot.input_turns["turn-realtime-1"]
        self.assertEqual(turn.state, InputTurnStatus.FAILED)
        assert turn.failure is not None
        self.assertEqual(turn.failure.reason_code, "asr_provider_auth_failed")
        self.assertEqual(turn.failure.safe_public_summary, "语音识别服务鉴权失败。")

    async def test_pcm_frames_normalize_before_provider_and_gap_fails_turn(self) -> None:
        provider = _DelayedFinalProviderSession()
        coordinator, host = self._coordinator(_Adapter(provider))
        coordinator.pcm_normalizer = PCMStreamNormalizer(
            input_format="s16le",
            input_sample_rate=16000,
            input_channels=1,
        )
        await coordinator.open()

        accepted = await coordinator.feed_pcm_frame(
            b"\x01\x00" * 160,
            sequence=0,
            audio_clock_ms=0,
        )
        duplicate = await coordinator.feed_pcm_frame(
            b"\x01\x00" * 160,
            sequence=0,
            audio_clock_ms=0,
        )
        failed = await coordinator.feed_pcm_frame(
            b"\x01\x00" * 160,
            sequence=2,
            audio_clock_ms=20,
        )

        self.assertTrue(accepted.ok)
        assert accepted.pcm_frame is not None
        self.assertEqual(accepted.pcm_frame.output_samples, 160)
        self.assertEqual(provider.feed_count, 1)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(provider.feed_count, 1)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.reason, "pcm_sequence_gap")
        self.assertTrue(provider.cancelled)
        turn = host.snapshot.input_turns["turn-realtime-1"]
        self.assertEqual(turn.state, InputTurnStatus.FAILED)
        assert turn.failure is not None
        self.assertEqual(turn.failure.reason_code, "pcm_sequence_gap")
        self.assertEqual(turn.failure.safe_public_summary, "实时音频输入无效。")

    async def test_pcm_filter_tail_is_sent_before_background_finalize(self) -> None:
        provider = _DelayedFinalProviderSession()
        coordinator, host = self._coordinator(_Adapter(provider))
        coordinator.pcm_normalizer = PCMStreamNormalizer(
            input_format="f32le",
            input_sample_rate=48000,
            input_channels=1,
        )
        await coordinator.open()

        fed = await coordinator.feed_pcm_frame(
            struct.pack("<f", 0.1) * 4800,
            sequence=0,
            audio_clock_ms=0,
        )
        started = await coordinator.start_finalize_pcm()

        self.assertTrue(fed.ok)
        self.assertEqual(started.status, "started")
        self.assertTrue(started.final_pending)
        self.assertGreaterEqual(provider.feed_count, 2)
        assert started.early_candidate is not None
        self.assertEqual(started.early_candidate.stable_text, "你好，伙伴")
        self.assertEqual(
            host.snapshot.input_turns["turn-realtime-1"].state,
            InputTurnStatus.CAPTURING,
        )

        provider.allow_final.set()
        settled = await coordinator.settle_finalize()
        self.assertTrue(settled.ok)
        self.assertEqual(
            host.snapshot.input_turns["turn-realtime-1"].state,
            InputTurnStatus.COMMITTED,
        )


if __name__ == "__main__":
    unittest.main()
