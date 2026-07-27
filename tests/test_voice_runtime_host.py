from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any

from companion_v01.voice_runtime import (
    AkaneVoiceRuntimeHost,
    VoiceCommandExecutionResult,
    VoiceHostPortResult,
)
from voicecore import InputTurnStatus, ResponseStatus, SpeechUnitStatus
from voicecore.testing import EventFactory


class _FakeJournal:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.failure_reason = ""
        self.duplicate_event_ids: set[str] = set()

    def append(self, event_record: Mapping[str, Any]) -> VoiceHostPortResult:
        if self.failure_reason:
            return VoiceHostPortResult.failed(self.failure_reason, retryable=True)
        record = dict(event_record)
        if str(record.get("event_id") or "") in self.duplicate_event_ids:
            return VoiceHostPortResult(status="duplicate")
        json.dumps(record)
        self.records.append(record)
        return VoiceHostPortResult.succeeded()


class _FakeProjectionPort:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail_kinds: set[str] = set()

    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        record = dict(projection_record)
        if str(record.get("kind") or "") in self.fail_kinds:
            return VoiceHostPortResult.failed("fake_projection_failure", retryable=True)
        json.dumps(record)
        self.records.append(record)
        return VoiceHostPortResult.succeeded()


class _FakeAudioCommandExecutor:
    def __init__(self, factory: EventFactory) -> None:
        self.factory = factory
        self.command_kinds: list[str] = []
        self.snapshot_records: list[dict[str, Any]] = []
        self.raise_error = False

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        if self.raise_error:
            raise RuntimeError("private fake failure")
        command = dict(command_record)
        payload = dict(command.get("payload") or {})
        command_id = str(command.get("command_id") or "")
        command_kind = str(command.get("command_kind") or "")
        snapshot = dict(snapshot_record)
        self.command_kinds.append(command_kind)
        self.snapshot_records.append(snapshot)

        if command_kind == "start_response_generation" and not payload.get("response_id"):
            return VoiceCommandExecutionResult.succeeded(
                self.factory.make(
                    "voice.response.created",
                    voice_turn_id=str(payload["voice_turn_id"]),
                    response_id="response-1",
                    turn_revision=int(payload["turn_revision"]),
                    response_generation=1,
                    payload={
                        "command_id": command_id,
                        "purpose": str(payload.get("purpose") or "content"),
                        "commitment": "committed",
                    },
                )
            )

        if command_kind == "start_response_generation":
            common = {
                "voice_turn_id": str(payload["voice_turn_id"]),
                "response_id": str(payload["response_id"]),
                "turn_revision": int(payload["turn_revision"]),
                "response_generation": int(payload["response_generation"]),
            }
            return VoiceCommandExecutionResult.succeeded(
                self.factory.make(
                    "voice.response.generation_started",
                    **common,
                    payload={"command_id": command_id},
                ),
                self.factory.make(
                    "voice.speech_unit.declared",
                    **common,
                    speech_unit_id="speech-unit-1",
                    payload={"ordinal": 1, "text_artifact_ref": "text:reply-1"},
                ),
                self.factory.make(
                    "voice.response.generation_completed",
                    **common,
                    payload={
                        "full_text": "收到，fake voice 链路已经跑通。",
                        "memory_metadata": {"keywords": ["voicecore", "fake_audio"]},
                    },
                ),
            )

        if command_kind == "start_tts":
            response_id = str(payload["response_id"])
            response = dict(snapshot["responses"][response_id])
            common = {
                "voice_turn_id": str(response["voice_turn_id"]),
                "response_id": response_id,
                "speech_unit_id": str(payload["speech_unit_id"]),
                "turn_revision": int(response["source_turn_revision"]),
                "response_generation": int(payload["response_generation"]),
            }
            return VoiceCommandExecutionResult.succeeded(
                self.factory.make("voice.tts.started", **common, payload={"command_id": command_id}),
                self.factory.make(
                    "voice.tts.ready",
                    **common,
                    payload={
                        "audio_artifact_ref": "audio:fake-1",
                        "duration_ms": 640,
                    },
                ),
            )

        if command_kind == "enqueue_playback":
            response_id = str(payload["response_id"])
            response = dict(snapshot["responses"][response_id])
            common = {
                "voice_turn_id": str(response["voice_turn_id"]),
                "response_id": response_id,
                "speech_unit_id": str(payload["speech_unit_id"]),
                "turn_revision": int(response["source_turn_revision"]),
                "response_generation": int(payload["response_generation"]),
            }
            return VoiceCommandExecutionResult.succeeded(
                self.factory.make(
                    "voice.playback.enqueued",
                    **common,
                    payload={"command_id": command_id},
                ),
                self.factory.make(
                    "voice.playback.started",
                    **common,
                    payload={"resume_token": "fake-resume-1"},
                ),
                self.factory.make(
                    "voice.playback.completed",
                    **common,
                    payload={"played_ms": 640},
                ),
            )

        return VoiceCommandExecutionResult.failed("fake_command_unsupported")


class VoiceRuntimeHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = EventFactory()
        self.journal = _FakeJournal()
        self.projections = _FakeProjectionPort()
        self.executor = _FakeAudioCommandExecutor(self.factory)
        self.host = AkaneVoiceRuntimeHost(
            conversation_id=self.factory.conversation_id,
            conversation_generation=1,
            journal=self.journal,
            projection_port=self.projections,
            command_executor=self.executor,
            capability_snapshot={"playback_interrupt": True},
        )

    def _accept(self, kind: str, **kwargs: Any):
        result = self.host.accept_event(self.factory.make(kind, **kwargs))
        self.assertTrue(result.accepted, f"{kind}: {result.status}/{result.reason}")
        return result

    def _commit_fake_input(self) -> None:
        self._accept(
            "voice.input.activity_started",
            voice_turn_id="turn-1",
            audio_stream_id="fake-microphone-1",
        )
        self._accept(
            "voice.asr.partial",
            voice_turn_id="turn-1",
            turn_revision=1,
            payload={"stable_text": "测试"},
        )
        self._accept(
            "voice.asr.finalized",
            voice_turn_id="turn-1",
            turn_revision=2,
            payload={"stable_text": "测试 fake voice", "supersedes_revision": 1},
        )
        self._accept(
            "voice.turn.commit_requested",
            voice_turn_id="turn-1",
            turn_revision=2,
            payload={"disposition": "message"},
        )

    def test_fake_audio_chain_reaches_real_delivery_and_memory_projection(self) -> None:
        self._commit_fake_input()
        seen_pending_sets: set[tuple[str, ...]] = set()
        while self.host.snapshot.pending_commands:
            pending = tuple(self.host.snapshot.pending_commands)
            self.assertNotIn(pending, seen_pending_sets, "fake executor stopped making progress")
            seen_pending_sets.add(pending)
            driven = self.host.drive_once()
            self.assertEqual(driven.status, "succeeded", driven)

        turn = self.host.snapshot.input_turns["turn-1"]
        response = self.host.snapshot.responses["response-1"]
        unit = self.host.snapshot.speech_units["speech-unit-1"]
        self.assertEqual(turn.state, InputTurnStatus.COMMITTED)
        self.assertEqual(response.state, ResponseStatus.COMPLETED)
        self.assertEqual(unit.state, SpeechUnitStatus.DELIVERED)
        self.assertEqual(
            self.executor.command_kinds,
            [
                "start_response_generation",
                "start_response_generation",
                "start_tts",
                "enqueue_playback",
            ],
        )
        self.assertEqual(
            [record["kind"] for record in self.projections.records],
            ["message.user.voice", "message.assistant.voice"],
        )
        assistant = self.projections.records[-1]["payload"]
        self.assertEqual(assistant["delivery_status"], "delivered")
        self.assertEqual(assistant["memory_metadata"]["keywords"], ["voicecore", "fake_audio"])
        self.assertTrue(all(isinstance(record, dict) for record in self.journal.records))

    def test_drive_once_advances_only_one_command_layer(self) -> None:
        self._commit_fake_input()
        first_command_ids = tuple(self.host.snapshot.pending_commands)
        driven = self.host.drive_once()

        self.assertEqual(driven.status, "succeeded")
        self.assertNotEqual(tuple(self.host.snapshot.pending_commands), first_command_ids)
        self.assertEqual(len(self.executor.command_kinds), 1)
        self.assertFalse(driven.quiescent)

    def test_journal_failure_prevents_snapshot_and_side_effects(self) -> None:
        self.journal.failure_reason = "fake_journal_unavailable"
        event = self.factory.make(
            "voice.input.activity_started",
            voice_turn_id="turn-1",
            audio_stream_id="fake-microphone-1",
        )
        result = self.host.accept_event(event)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "fake_journal_unavailable")
        self.assertFalse(result.snapshot_advanced)
        self.assertNotIn("turn-1", self.host.snapshot.input_turns)
        self.assertEqual(self.projections.records, [])
        self.assertEqual(self.executor.command_kinds, [])

        self.journal.failure_reason = ""
        retry = self.host.accept_event(event)
        self.assertTrue(retry.accepted)
        self.assertIn("turn-1", self.host.snapshot.input_turns)

    def test_duplicate_event_does_not_repeat_journal_projection_or_command(self) -> None:
        event = self.factory.make(
            "voice.input.activity_started",
            voice_turn_id="turn-1",
            audio_stream_id="fake-microphone-1",
        )
        accepted = self.host.accept_event(event)
        duplicate = self.host.accept_event(event)

        self.assertTrue(accepted.accepted)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertFalse(duplicate.snapshot_advanced)
        self.assertEqual(len(self.journal.records), 1)
        self.assertEqual(self.projections.records, [])

    def test_projection_failure_is_visible_without_rolling_back_durable_state(self) -> None:
        self.projections.fail_kinds.add("message.user.voice")
        self._accept(
            "voice.input.activity_started",
            voice_turn_id="turn-1",
            audio_stream_id="fake-microphone-1",
        )
        self._accept(
            "voice.asr.finalized",
            voice_turn_id="turn-1",
            turn_revision=1,
            payload={"stable_text": "投影失败测试"},
        )
        result = self.host.accept_event(
            self.factory.make(
                "voice.turn.commit_requested",
                voice_turn_id="turn-1",
                turn_revision=1,
                payload={"disposition": "message"},
            )
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "projection_emit_failed")
        self.assertFalse(result.projection_results[0].ok)
        self.assertEqual(self.host.snapshot.input_turns["turn-1"].state, InputTurnStatus.COMMITTED)
        self.assertTrue(self.host.snapshot.pending_commands)

    def test_command_executor_failure_stays_structured_and_pending(self) -> None:
        self._commit_fake_input()
        pending = tuple(self.host.snapshot.pending_commands)
        self.executor.raise_error = True
        driven = self.host.drive_once()

        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.reason, "voice_command_drive_failed")
        self.assertEqual(driven.command_results[0].reason, "command_executor_failed")
        self.assertEqual(tuple(self.host.snapshot.pending_commands), pending)
        self.assertNotIn("private fake failure", str(driven))

    def test_rejected_command_observation_fails_drive_and_keeps_command_pending(self) -> None:
        self._commit_fake_input()
        pending = tuple(self.host.snapshot.pending_commands)

        def invalid_observation(
            command_record: Mapping[str, Any],
            _snapshot_record: Mapping[str, Any],
        ) -> VoiceCommandExecutionResult:
            command_id = str(command_record["command_id"])
            return VoiceCommandExecutionResult.succeeded(
                self.factory.make(
                    "voice.unsupported.observation",
                    payload={"command_id": command_id},
                )
            )

        self.executor.execute = invalid_observation
        driven = self.host.drive_once()

        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.dispatch_results[0].status, "rejected")
        self.assertEqual(tuple(self.host.snapshot.pending_commands), pending)

    def test_journal_duplicate_receipt_can_rebuild_snapshot_after_restart(self) -> None:
        event = self.factory.make(
            "voice.input.activity_started",
            voice_turn_id="turn-1",
            audio_stream_id="fake-microphone-1",
        )
        self.journal.duplicate_event_ids.add(event.event_id)
        result = self.host.accept_event(event)

        self.assertTrue(result.accepted)
        self.assertEqual(result.journal_result.status, "duplicate")
        self.assertIn("turn-1", self.host.snapshot.input_turns)
        self.assertEqual(self.journal.records, [])


if __name__ == "__main__":
    unittest.main()
