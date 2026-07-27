from __future__ import annotations

import unittest
from dataclasses import dataclass

from capcore_adapter_speech import (
    ASRRevisionQuality,
    ASRSessionMode,
    ASRSessionUpdate,
    ASRTranscriptRevision,
)
from companion_v01.voice_runtime import VoiceASRSessionBridge
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


class VoiceASRSessionBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = EventFactory()
        self.host = _ReducerHost(self.factory)
        self.bridge = VoiceASRSessionBridge(
            host=self.host,
            event_factory=self.factory,
            voice_turn_id="turn-asr-1",
            audio_stream_id="microphone-1",
        )

    def test_revisions_commit_one_authoritative_user_message(self) -> None:
        opened = self.bridge.open_turn()
        partial = self.bridge.accept_update(
            ASRSessionUpdate.accepted(
                ASRSessionMode.STREAMING,
                ASRTranscriptRevision(
                    revision=1,
                    quality=ASRRevisionQuality.PARTIAL,
                    stable_text="我想",
                    unstable_tail="问",
                    provider_receipt_id="asr_revision_1",
                ),
            )
        )
        checkpoint = self.bridge.accept_update(
            ASRSessionUpdate.accepted(
                ASRSessionMode.STREAMING,
                ASRTranscriptRevision(
                    revision=2,
                    quality=ASRRevisionQuality.STABLE_CHECKPOINT,
                    stable_text="我想问天气",
                    language_hint="zh",
                    confidence_hint=0.94,
                    supersedes_revision=1,
                    provider_receipt_id="asr_revision_2",
                    control_significant=True,
                ),
            )
        )
        final_update = ASRSessionUpdate.accepted(
            ASRSessionMode.STREAMING,
            ASRTranscriptRevision(
                revision=3,
                quality=ASRRevisionQuality.FINAL,
                stable_text="我想问天气",
                language_hint="zh",
                supersedes_revision=2,
                provider_receipt_id="asr_revision_3",
            ),
        )
        final = self.bridge.accept_update(final_update)

        self.assertTrue(opened.accepted)
        self.assertTrue(partial.accepted)
        self.assertTrue(checkpoint.accepted)
        self.assertTrue(final.accepted)
        turn = self.host.snapshot.input_turns["turn-asr-1"]
        self.assertEqual(turn.state, InputTurnStatus.COMMITTED)
        self.assertEqual(turn.final_revision, 3)
        self.assertEqual(turn.revisions[3].text, "我想问天气")

        projections = [projection for transition in self.host.transitions for projection in transition.projections]
        self.assertEqual(
            [projection.kind for projection in projections],
            ["event.voice.asr_checkpoint", "message.user.voice"],
        )
        self.assertEqual(projections[-1].payload["text"], "我想问天气")
        self.assertEqual(len(self.host.snapshot.pending_commands), 1)

        replay = self.bridge.accept_update(final_update)
        self.assertEqual(replay.status, "duplicate")
        self.assertEqual(len(self.host.snapshot.pending_commands), 1)
        replay_projections = [
            projection for transition in self.host.transitions for projection in transition.projections
        ]
        self.assertEqual(replay_projections, projections)

    def test_final_only_feed_is_not_misrepresented_as_partial(self) -> None:
        self.bridge.open_turn()
        feed = self.bridge.accept_update(ASRSessionUpdate.accepted(ASRSessionMode.FINAL_ONLY))
        before_final = self.host.snapshot.input_turns["turn-asr-1"]
        self.assertEqual(before_final.state, InputTurnStatus.CAPTURING)
        self.assertEqual(before_final.revisions, {})

        final = self.bridge.accept_update(
            ASRSessionUpdate.accepted(
                ASRSessionMode.FINAL_ONLY,
                ASRTranscriptRevision(
                    revision=1,
                    quality=ASRRevisionQuality.FINAL,
                    stable_text="批量最终文本",
                    provider_receipt_id="batch_final_1",
                ),
            )
        )

        self.assertTrue(feed.accepted)
        self.assertTrue(final.accepted)
        self.assertEqual(
            self.host.snapshot.input_turns["turn-asr-1"].state,
            InputTurnStatus.COMMITTED,
        )

    def test_structured_provider_failure_fails_the_turn(self) -> None:
        self.bridge.open_turn()
        failed = self.bridge.accept_update(
            ASRSessionUpdate.failed(
                ASRSessionMode.STREAMING,
                "asr_stream_temporarily_unavailable",
                retryable=True,
                provider_status="unavailable",
                safe_public_summary="语音识别暂时不可用。",
            )
        )

        self.assertTrue(failed.accepted)
        turn = self.host.snapshot.input_turns["turn-asr-1"]
        self.assertEqual(turn.state, InputTurnStatus.FAILED)
        assert turn.failure is not None
        self.assertEqual(turn.failure.stage, "asr")
        self.assertEqual(
            turn.failure.reason_code,
            "asr_stream_temporarily_unavailable",
        )
        self.assertTrue(turn.failure.retryable)
        self.assertEqual(turn.failure.safe_public_summary, "语音识别暂时不可用。")

        replay = self.bridge.accept_update(
            ASRSessionUpdate.failed(
                ASRSessionMode.STREAMING,
                "asr_stream_temporarily_unavailable",
                retryable=True,
            )
        )
        self.assertEqual(replay.status, "duplicate")

    def test_update_before_open_and_identity_conflict_are_explicit(self) -> None:
        update = ASRSessionUpdate.accepted(ASRSessionMode.STREAMING)
        before_open = self.bridge.accept_update(update)
        self.assertEqual(before_open.status, "failed")
        self.assertEqual(before_open.reason, "voice_turn_not_open")

        self.assertTrue(self.bridge.open_turn().accepted)
        duplicate = self.bridge.open_turn()
        self.assertEqual(duplicate.status, "duplicate")

        conflicting = VoiceASRSessionBridge(
            host=self.host,
            event_factory=self.factory,
            voice_turn_id="turn-asr-1",
            audio_stream_id="microphone-2",
        ).open_turn()
        self.assertEqual(conflicting.status, "failed")
        self.assertEqual(conflicting.reason, "voice_turn_identity_conflict")


if __name__ == "__main__":
    unittest.main()
