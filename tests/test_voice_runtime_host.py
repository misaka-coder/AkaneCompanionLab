from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any

from companion_v01.voice_runtime import (
    AkaneVoiceRuntimeHost,
    VoiceCommandExecutionResult,
    VoiceCommandReceiptLoadResult,
    VoiceCommandReceiptRecord,
    VoiceHostPortResult,
    VoiceProjectionOutboxLoadResult,
    VoiceResponseStreamBridge,
    VoiceTextArtifactResult,
)
from companion_v01.llm_runtime import _TopLevelJSONStreamTap
from voicecore import (
    InputTurnStatus,
    ResponseStatus,
    SpeechUnitStatus,
    voice_command_to_dict,
    voice_event_to_dict,
)
from voicecore.testing import EventFactory


class _FakeJournal:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.failure_reason = ""
        self.duplicate_event_ids: set[str] = set()
        self.projection_records: dict[str, dict[str, Any]] = {}
        self.delivered_projection_ids: set[str] = set()
        self.acknowledgement_failure_reason = ""
        self.command_receipts: dict[str, VoiceCommandReceiptRecord] = {}

    def append_transition(
        self,
        event_record: Mapping[str, Any],
        projection_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult:
        if self.failure_reason:
            return VoiceHostPortResult.failed(self.failure_reason, retryable=True)
        record = dict(event_record)
        if str(record.get("event_id") or "") in self.duplicate_event_ids:
            return VoiceHostPortResult(status="duplicate")
        json.dumps(record)
        self.records.append(record)
        for projection_record in projection_records:
            projection = dict(projection_record)
            projection_id = str(projection["projection_id"])
            existing = self.projection_records.get(projection_id)
            if existing is not None and existing != projection:
                return VoiceHostPortResult.failed("fake_projection_conflict")
            self.projection_records[projection_id] = projection
        return VoiceHostPortResult.succeeded()

    def load_pending_projections(self) -> VoiceProjectionOutboxLoadResult:
        records = tuple(
            record
            for projection_id, record in self.projection_records.items()
            if projection_id not in self.delivered_projection_ids
        )
        return VoiceProjectionOutboxLoadResult.succeeded(records)

    def mark_projection_delivered(self, projection_id: str) -> VoiceHostPortResult:
        if self.acknowledgement_failure_reason:
            return VoiceHostPortResult.failed(
                self.acknowledgement_failure_reason,
                retryable=True,
            )
        if projection_id not in self.projection_records:
            return VoiceHostPortResult.failed("fake_projection_missing")
        if projection_id in self.delivered_projection_ids:
            return VoiceHostPortResult(status="duplicate")
        self.delivered_projection_ids.add(projection_id)
        return VoiceHostPortResult.succeeded()

    def begin_command(self, command_record: Mapping[str, Any]) -> VoiceHostPortResult:
        command = dict(command_record)
        command_id = str(command["command_id"])
        existing = self.command_receipts.get(command_id)
        if existing is not None:
            if dict(existing.command_record) != command:
                return VoiceHostPortResult.failed("fake_command_receipt_conflict")
            if existing.phase == "executing":
                return VoiceHostPortResult(status="duplicate")
            if existing.phase == "released":
                self.command_receipts[command_id] = VoiceCommandReceiptRecord(
                    phase="executing",
                    command_record=command,
                )
                return VoiceHostPortResult.succeeded()
            return VoiceHostPortResult.failed("fake_command_receipt_has_result")
        self.command_receipts[command_id] = VoiceCommandReceiptRecord(
            phase="executing",
            command_record=command,
        )
        return VoiceHostPortResult.succeeded()

    def store_command_observations(
        self,
        command_id: str,
        observation_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult:
        existing = self.command_receipts.get(command_id)
        if existing is None:
            return VoiceHostPortResult.failed("fake_command_receipt_missing")
        normalized = tuple(dict(record) for record in observation_records)
        if existing.phase in {"observations_pending", "completed"}:
            if existing.observation_records == normalized:
                return VoiceHostPortResult(status="duplicate")
            return VoiceHostPortResult.failed("fake_command_observation_conflict")
        if existing.phase != "executing":
            return VoiceHostPortResult.failed("fake_command_receipt_phase_conflict")
        self.command_receipts[command_id] = VoiceCommandReceiptRecord(
            phase="observations_pending",
            command_record=existing.command_record,
            observation_records=normalized,
        )
        return VoiceHostPortResult.succeeded()

    def release_command(self, command_id: str) -> VoiceHostPortResult:
        existing = self.command_receipts.get(command_id)
        if existing is None:
            return VoiceHostPortResult.failed("fake_command_receipt_missing")
        if existing.phase == "released":
            return VoiceHostPortResult(status="duplicate")
        if existing.phase != "executing":
            return VoiceHostPortResult.failed("fake_command_receipt_phase_conflict")
        self.command_receipts[command_id] = VoiceCommandReceiptRecord(
            phase="released",
            command_record=existing.command_record,
        )
        return VoiceHostPortResult.succeeded()

    def load_unfinished_commands(self) -> VoiceCommandReceiptLoadResult:
        return VoiceCommandReceiptLoadResult.succeeded(
            tuple(
                receipt
                for receipt in self.command_receipts.values()
                if receipt.phase in {"executing", "observations_pending"}
            )
        )

    def mark_command_completed(self, command_id: str) -> VoiceHostPortResult:
        existing = self.command_receipts.get(command_id)
        if existing is None:
            return VoiceHostPortResult.failed("fake_command_receipt_missing")
        if existing.phase == "completed":
            return VoiceHostPortResult(status="duplicate")
        if existing.phase != "observations_pending":
            return VoiceHostPortResult.failed("fake_command_receipt_phase_conflict")
        self.command_receipts[command_id] = VoiceCommandReceiptRecord(
            phase="completed",
            command_record=existing.command_record,
            observation_records=existing.observation_records,
        )
        return VoiceHostPortResult.succeeded()


class _FakeProjectionPort:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail_kinds: set[str] = set()
        self.seen_projection_ids: set[str] = set()

    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        record = dict(projection_record)
        if str(record.get("kind") or "") in self.fail_kinds:
            return VoiceHostPortResult.failed("fake_projection_failure", retryable=True)
        projection_id = str(record.get("projection_id") or "")
        if projection_id in self.seen_projection_ids:
            return VoiceHostPortResult(status="duplicate")
        json.dumps(record)
        self.records.append(record)
        self.seen_projection_ids.add(projection_id)
        return VoiceHostPortResult.succeeded()


class _FakeTextArtifactPort:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, str]] = {}
        self.failure_reason = ""

    def put_text(
        self,
        *,
        artifact_key: str,
        text: str,
        media_type: str,
    ) -> VoiceTextArtifactResult:
        if self.failure_reason:
            return VoiceTextArtifactResult.failed(self.failure_reason, retryable=True)
        existing = self.records.get(artifact_key)
        if existing is not None:
            if existing["text"] != text:
                return VoiceTextArtifactResult.failed("fake_artifact_key_conflict")
            return VoiceTextArtifactResult.duplicate(existing["artifact_ref"])
        artifact_ref = f"text:{len(self.records) + 1}"
        self.records[artifact_key] = {
            "artifact_ref": artifact_ref,
            "media_type": media_type,
            "text": text,
        }
        return VoiceTextArtifactResult.succeeded(artifact_ref)


class _FakeAudioCommandExecutor:
    def __init__(self, factory: EventFactory) -> None:
        self.factory = factory
        self.command_kinds: list[str] = []
        self.command_records: list[dict[str, Any]] = []
        self.snapshot_records: list[dict[str, Any]] = []
        self.raise_error = False
        self.results_by_idempotency_key: dict[str, VoiceCommandExecutionResult] = {}

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
        self.command_records.append(command)
        self.snapshot_records.append(snapshot)

        if command_kind == "start_response_generation" and not payload.get("response_id"):
            return self._remember(
                command,
                VoiceCommandExecutionResult.succeeded(
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
                ),
            )

        if command_kind == "start_response_generation":
            common = {
                "voice_turn_id": str(payload["voice_turn_id"]),
                "response_id": str(payload["response_id"]),
                "turn_revision": int(payload["turn_revision"]),
                "response_generation": int(payload["response_generation"]),
            }
            return self._remember(
                command,
                VoiceCommandExecutionResult.succeeded(
                    self.factory.make(
                        "voice.response.generation_started",
                        **common,
                        payload={"command_id": command_id},
                    ),
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
            return self._remember(
                command,
                VoiceCommandExecutionResult.succeeded(
                    self.factory.make("voice.tts.started", **common),
                    self.factory.make(
                        "voice.tts.ready",
                        **common,
                        payload={
                            "command_id": command_id,
                            "audio_artifact_ref": "audio:fake-1",
                            "duration_ms": 640,
                        },
                    ),
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
            return self._remember(
                command,
                VoiceCommandExecutionResult.succeeded(
                    self.factory.make("voice.playback.enqueued", **common),
                    self.factory.make(
                        "voice.playback.started",
                        **common,
                        payload={"resume_token": "fake-resume-1"},
                    ),
                    self.factory.make(
                        "voice.playback.completed",
                        **common,
                        payload={"command_id": command_id, "played_ms": 640},
                    ),
                ),
            )

        return VoiceCommandExecutionResult.failed("fake_command_unsupported")

    def recover(
        self,
        command_record: Mapping[str, Any],
        _snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        command = dict(command_record)
        result = self.results_by_idempotency_key.get(str(command.get("idempotency_key") or ""))
        if result is None:
            return VoiceCommandExecutionResult.not_started("fake_command_not_started")
        return result

    def _remember(
        self,
        command_record: Mapping[str, Any],
        result: VoiceCommandExecutionResult,
    ) -> VoiceCommandExecutionResult:
        self.results_by_idempotency_key[str(command_record.get("idempotency_key") or "")] = result
        return result


class VoiceRuntimeHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = EventFactory()
        self.journal = _FakeJournal()
        self.projections = _FakeProjectionPort()
        self.text_artifacts = _FakeTextArtifactPort()
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

    def _start_fake_response(self) -> VoiceResponseStreamBridge:
        self._commit_fake_input()
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self.host.snapshot.responses["response-1"].state, ResponseStatus.GENERATING)
        return VoiceResponseStreamBridge(
            host=self.host,
            response_id="response-1",
            text_artifacts=self.text_artifacts,
            event_factory=self.factory,
        )

    def test_fake_audio_chain_reaches_real_delivery_and_memory_projection(self) -> None:
        bridge = self._start_fake_response()
        segment = bridge.accept_stream_event(
            {"type": "speech_segment", "index": 0, "text": "收到，fake voice 链路已经跑通。"}
        )
        self.assertTrue(segment.accepted, segment)
        events_before_playback = [record["event_kind"] for record in self.journal.records]
        self.assertIn("voice.tts.ready", events_before_playback)
        self.assertNotIn("voice.response.generation_completed", events_before_playback)
        self.assertEqual(self.host.drive_once().status, "succeeded")
        events_before_final = [record["event_kind"] for record in self.journal.records]
        self.assertIn("voice.playback.completed", events_before_final)
        self.assertNotIn("voice.response.generation_completed", events_before_final)
        final = bridge.accept_stream_event(
            {
                "type": "final",
                "payload": {
                    "speech": "收到，fake voice 链路已经跑通。",
                    "memory_metadata": {"keywords": ["voicecore", "fake_audio"]},
                },
            }
        )
        self.assertTrue(final.accepted, final)

        turn = self.host.snapshot.input_turns["turn-1"]
        response = self.host.snapshot.responses["response-1"]
        unit = next(iter(self.host.snapshot.speech_units.values()))
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
        self.assertEqual(assistant["full_text"], "收到，fake voice 链路已经跑通。")
        start_tts = next(record for record in self.executor.command_records if record["command_kind"] == "start_tts")
        self.assertIn(
            start_tts["payload"]["text_artifact_ref"],
            {record["artifact_ref"] for record in self.text_artifacts.records.values()},
        )
        self.assertEqual(
            len([record for record in self.projections.records if record["kind"] == "message.assistant.voice"]),
            1,
        )
        self.assertTrue(all(isinstance(record, dict) for record in self.journal.records))

    def test_stream_bridge_preserves_many_segments_and_punctuation_without_duplicate_memory(self) -> None:
        bridge = self._start_fake_response()
        tap = _TopLevelJSONStreamTap()
        speech = "哈啊？！真的吗。\n？\n第一句。第二句！第三句？第四句。第五句。"
        stream_events = tap.feed(json.dumps({"emotion": "happy", "speech": speech}, ensure_ascii=False))
        stream_events.extend(tap.finish())
        segments = [event for event in stream_events if event.get("type") == "speech_segment"]

        self.assertEqual(
            [event["text"] for event in segments],
            ["哈啊？！", "真的吗。", "？", "第一句。", "第二句！", "第三句？", "第四句。", "第五句。"],
        )
        for event in stream_events:
            result = bridge.accept_stream_event(event)
            self.assertIn(result.status, {"accepted", "ignored"}, result)

        self.assertGreater(len(self.host.snapshot.speech_units), 5)
        duplicate_segment = bridge.accept_stream_event(segments[0])
        self.assertEqual(duplicate_segment.status, "duplicate")
        self.assertEqual(len(self.text_artifacts.records), len(segments))
        self.assertFalse([record for record in self.projections.records if record["kind"] == "message.assistant.voice"])
        final = bridge.accept_stream_event(
            {
                "type": "final",
                "payload": {
                    "speech": speech,
                    "memory_metadata": {"keywords": ["流式语音"]},
                },
            }
        )
        self.assertTrue(final.accepted, final)
        duplicate = bridge.accept_stream_event(
            {
                "type": "final",
                "payload": {
                    "speech": speech,
                    "memory_metadata": {"keywords": ["流式语音"]},
                },
            }
        )
        self.assertEqual(duplicate.status, "duplicate")

        assistant_records = [
            record for record in self.projections.records if record["kind"] == "message.assistant.voice"
        ]
        self.assertEqual(len(assistant_records), 1)
        self.assertEqual(assistant_records[0]["payload"]["full_text"], speech)
        self.assertEqual(
            [record["text"] for record in self.text_artifacts.records.values()],
            [event["text"] for event in segments],
        )

    def test_interrupted_stream_does_not_flush_incomplete_tail_into_speech_unit(self) -> None:
        bridge = self._start_fake_response()
        tap = _TopLevelJSONStreamTap()
        stream_events = tap.feed('{"speech":"第一句。这个残句还没有生成完')
        stream_events.extend(tap.finish())

        self.assertEqual(
            [event["text"] for event in stream_events if event.get("type") == "speech_segment"],
            ["第一句。"],
        )
        self.assertFalse([event for event in stream_events if event.get("type") == "final"])
        for event in stream_events:
            result = bridge.accept_stream_event(event)
            self.assertIn(result.status, {"accepted", "ignored"}, result)
        self.assertEqual(
            [record["text"] for record in self.text_artifacts.records.values()],
            ["第一句。"],
        )
        self.assertFalse([record for record in self.projections.records if record["kind"] == "message.assistant.voice"])

    def test_stream_bridge_reports_artifact_failure_without_declaring_unit(self) -> None:
        bridge = self._start_fake_response()
        self.text_artifacts.failure_reason = "fake_text_store_unavailable"

        result = bridge.accept_stream_event(
            {"type": "speech_segment", "index": 0, "text": "这一句不能假装已经进入语音链。"}
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "fake_text_store_unavailable")
        self.assertTrue(result.artifact_result.retryable)
        self.assertEqual(self.host.snapshot.speech_units, {})

    def test_stream_bridge_rejects_out_of_order_segment_without_hidden_reordering(self) -> None:
        bridge = self._start_fake_response()

        result = bridge.accept_stream_event({"type": "speech_segment", "index": 1, "text": "第二句。"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "speech_segment_out_of_order")
        self.assertEqual(self.text_artifacts.records, {})
        self.assertEqual(self.host.snapshot.speech_units, {})

    def test_drive_once_advances_only_one_command_layer(self) -> None:
        self._commit_fake_input()
        first_command_ids = tuple(self.host.snapshot.pending_commands)
        driven = self.host.drive_once()

        self.assertEqual(driven.status, "succeeded")
        self.assertNotEqual(tuple(self.host.snapshot.pending_commands), first_command_ids)
        self.assertEqual(len(self.executor.command_kinds), 1)
        self.assertFalse(driven.quiescent)

    def test_multi_observation_receipt_recovers_without_reexecuting_command(self) -> None:
        self._start_fake_response()
        self._accept(
            "voice.speech_unit.declared",
            voice_turn_id="turn-1",
            response_id="response-1",
            speech_unit_id="speech-unit-1",
            turn_revision=2,
            response_generation=1,
            payload={
                "ordinal": 0,
                "purpose": "content",
                "text_artifact_ref": "text:durable-unit-1",
            },
        )
        command = next(
            command for command in self.host.snapshot.pending_commands.values() if command.command_kind == "start_tts"
        )
        self.assertTrue(self.journal.begin_command(voice_command_to_dict(command)).ok)
        execution = self.host.execute_command(command)
        self.assertEqual(execution.status, "succeeded")
        self.assertEqual(len(execution.observations), 2)
        self.assertNotIn("command_id", execution.observations[0].payload)
        self.assertEqual(
            execution.observations[-1].payload["command_id"],
            command.command_id,
        )
        self.assertTrue(
            self.journal.store_command_observations(
                command.command_id,
                tuple(voice_event_to_dict(observation) for observation in execution.observations),
            ).ok
        )
        first = self.host.accept_event(execution.observations[0])
        self.assertTrue(first.accepted)
        self.assertIn(command.command_id, self.host.snapshot.pending_commands)

        recovered_host = AkaneVoiceRuntimeHost(
            conversation_id=self.factory.conversation_id,
            conversation_generation=1,
            journal=self.journal,
            projection_port=self.projections,
            command_executor=self.executor,
            capability_snapshot={"playback_interrupt": True},
            restored_snapshot=self.host.snapshot,
        )
        recovered = recovered_host.drive_once()

        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(recovered.command_results, ())
        self.assertEqual(
            [item.command_kind for item in recovered_host.snapshot.pending_commands.values()],
            ["enqueue_playback"],
        )
        self.assertEqual(
            recovered_host.snapshot.speech_units["speech-unit-1"].state,
            SpeechUnitStatus.READY,
        )
        self.assertEqual(self.executor.command_kinds.count("start_tts"), 1)
        self.assertEqual(
            self.journal.command_receipts[command.command_id].phase,
            "completed",
        )

    def test_unknown_executor_outcome_is_recovered_before_safe_retry(self) -> None:
        self._commit_fake_input()
        command_id = next(iter(self.host.snapshot.pending_commands))
        self.executor.raise_error = True

        failed = self.host.drive_once()

        self.assertEqual(failed.status, "failed")
        self.assertEqual(
            self.journal.command_receipts[command_id].phase,
            "executing",
        )
        self.assertEqual(self.executor.command_kinds, [])

        self.executor.raise_error = False
        self.executor.recover = None
        blocked = self.host.drive_once()

        self.assertEqual(blocked.status, "deferred")
        self.assertEqual(blocked.reason, "command_recovery_unavailable")
        self.assertEqual(self.executor.command_kinds, [])

        del self.executor.recover
        recovered = self.host.drive_once()

        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(self.executor.command_kinds, ["start_response_generation"])
        self.assertEqual(
            self.journal.command_receipts[command_id].phase,
            "completed",
        )

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

    def test_pending_projection_blocks_commands_until_delivery_recovers(self) -> None:
        self.projections.fail_kinds.add("message.user.voice")
        self._commit_fake_input()

        blocked = self.host.drive_once()

        self.assertEqual(blocked.status, "deferred")
        self.assertEqual(blocked.reason, "fake_projection_failure")
        self.assertTrue(blocked.pending_projection_ids)
        self.assertEqual(self.executor.command_kinds, [])

        self.projections.fail_kinds.clear()
        recovered = self.host.drive_once()

        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(self.executor.command_kinds, ["start_response_generation"])
        self.assertEqual(
            [record["kind"] for record in self.projections.records],
            ["message.user.voice"],
        )
        self.assertFalse(recovered.pending_projection_ids)

    def test_projection_ack_retry_uses_stable_id_without_duplicate_sink_effect(self) -> None:
        self.journal.acknowledgement_failure_reason = "fake_ack_unavailable"
        self._accept(
            "voice.input.activity_started",
            voice_turn_id="turn-1",
            audio_stream_id="fake-microphone-1",
        )
        self._accept(
            "voice.asr.finalized",
            voice_turn_id="turn-1",
            turn_revision=1,
            payload={"stable_text": "投影确认重试"},
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
        self.assertEqual(len(self.projections.records), 1)
        self.assertTrue(result.projection_drain_result.pending_projection_ids)

        self.journal.acknowledgement_failure_reason = ""
        recovered = self.host.drain_projection_outbox()

        self.assertTrue(recovered.quiescent)
        self.assertEqual(recovered.projection_results[0].status, "duplicate")
        self.assertEqual(len(self.projections.records), 1)
        self.assertEqual(
            self.journal.delivered_projection_ids,
            {self.projections.records[0]["projection_id"]},
        )

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
