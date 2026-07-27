from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from companion_v01.voice_runtime import (
    AkaneVoiceRuntimeHost,
    FileVoiceTextArtifactPort,
    SqliteVoiceRuntimeJournal,
    VoiceCommandExecutionResult,
    VoiceHostPortResult,
)
from voicecore import (
    initial_snapshot,
    snapshot_to_dict,
    voice_command_to_dict,
    voice_event_to_dict,
)
from voicecore.testing import EventFactory


class _RecordingProjectionPort:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.seen_projection_ids: set[str] = set()

    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        record = dict(projection_record)
        projection_id = str(record.get("projection_id") or "")
        if projection_id in self.seen_projection_ids:
            return VoiceHostPortResult(status="duplicate")
        self.records.append(record)
        self.seen_projection_ids.add(projection_id)
        return VoiceHostPortResult.succeeded()


class _InertCommandExecutor:
    def execute(
        self,
        _command_record: Mapping[str, Any],
        _snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return VoiceCommandExecutionResult.deferred("durable_port_test_inert")

    def recover(
        self,
        _command_record: Mapping[str, Any],
        _snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return VoiceCommandExecutionResult.not_started("durable_port_test_not_started")


class _FailIfExecutedCommandExecutor:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.recover_calls = 0

    def execute(
        self,
        _command_record: Mapping[str, Any],
        _snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        self.execute_calls += 1
        return VoiceCommandExecutionResult.failed("unexpected_command_execution")

    def recover(
        self,
        _command_record: Mapping[str, Any],
        _snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        self.recover_calls += 1
        return VoiceCommandExecutionResult.failed("unexpected_command_recovery")


class VoiceRuntimeDurablePortTests(unittest.TestCase):
    def _journal(self, state_dir: Path) -> SqliteVoiceRuntimeJournal:
        return SqliteVoiceRuntimeJournal(
            state_dir=state_dir,
            conversation_id="conversation-1",
            conversation_generation=1,
        )

    def _accept_committed_turn(
        self,
        *,
        host: AkaneVoiceRuntimeHost,
        factory: EventFactory,
    ) -> None:
        for event in (
            factory.make(
                "voice.input.activity_started",
                voice_turn_id="turn-1",
                audio_stream_id="microphone-1",
            ),
            factory.make(
                "voice.asr.finalized",
                voice_turn_id="turn-1",
                turn_revision=1,
                payload={"stable_text": "持久化语音测试"},
            ),
            factory.make(
                "voice.turn.commit_requested",
                voice_turn_id="turn-1",
                turn_revision=1,
                payload={"disposition": "message"},
            ),
        ):
            accepted = host.accept_event(event)
            self.assertTrue(accepted.accepted, accepted)

    def test_file_journal_replays_snapshot_and_restores_host_without_reemitting_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            factory = EventFactory()
            projections = _RecordingProjectionPort()
            host = AkaneVoiceRuntimeHost(
                conversation_id=factory.conversation_id,
                conversation_generation=1,
                journal=self._journal(state_dir),
                projection_port=projections,
                command_executor=_InertCommandExecutor(),
                capability_snapshot={"playback_interrupt": True},
            )
            self._accept_committed_turn(host=host, factory=factory)
            expected_snapshot = snapshot_to_dict(host.snapshot)
            self.assertEqual([record["kind"] for record in projections.records], ["message.user.voice"])

            reopened = self._journal(state_dir)
            loaded = reopened.load_events()
            self.assertTrue(loaded.ok, loaded)
            self.assertEqual(len(loaded.events), 3)
            replayed = reopened.replay(
                initial_snapshot(
                    factory.conversation_id,
                    conversation_generation=1,
                    capability_snapshot={"playback_interrupt": True},
                )
            )
            self.assertTrue(replayed.ok, replayed)
            self.assertEqual(snapshot_to_dict(replayed.replay.snapshot), expected_snapshot)

            recovery_projections = _RecordingProjectionPort()
            recovered_host = AkaneVoiceRuntimeHost(
                conversation_id=factory.conversation_id,
                conversation_generation=1,
                journal=reopened,
                projection_port=recovery_projections,
                command_executor=_InertCommandExecutor(),
                capability_snapshot={"playback_interrupt": True},
                restored_snapshot=replayed.replay.snapshot,
            )
            self.assertEqual(snapshot_to_dict(recovered_host.snapshot), expected_snapshot)
            self.assertIsNot(recovered_host.snapshot, replayed.replay.snapshot)
            self.assertTrue(recovered_host.snapshot.pending_commands)
            self.assertEqual(recovery_projections.records, [])

    def test_projection_outbox_recovers_ack_crash_without_duplicate_sink_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            factory = EventFactory()
            journal = self._journal(state_dir)
            projections = _RecordingProjectionPort()
            host = AkaneVoiceRuntimeHost(
                conversation_id=factory.conversation_id,
                conversation_generation=1,
                journal=journal,
                projection_port=projections,
                command_executor=_InertCommandExecutor(),
                capability_snapshot={"playback_interrupt": True},
            )
            for event in (
                factory.make(
                    "voice.input.activity_started",
                    voice_turn_id="turn-1",
                    audio_stream_id="microphone-1",
                ),
                factory.make(
                    "voice.asr.finalized",
                    voice_turn_id="turn-1",
                    turn_revision=1,
                    payload={"stable_text": "投影 outbox 恢复"},
                ),
            ):
                self.assertTrue(host.accept_event(event).accepted)

            database_path = next(state_dir.rglob("journal.sqlite3"))
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TRIGGER reject_projection_ack
                    BEFORE UPDATE OF delivery_status ON voice_projection_outbox
                    BEGIN
                        SELECT RAISE(ABORT, 'private projection ack failure');
                    END;
                    """
                )
            committed = host.accept_event(
                factory.make(
                    "voice.turn.commit_requested",
                    voice_turn_id="turn-1",
                    turn_revision=1,
                    payload={"disposition": "message"},
                )
            )

            self.assertTrue(committed.accepted)
            self.assertEqual(committed.reason, "projection_emit_failed")
            self.assertEqual(len(projections.records), 1)
            self.assertEqual(
                committed.projection_drain_result.reason,
                "voice_projection_acknowledgement_failed",
            )
            self.assertTrue(committed.projection_drain_result.pending_projection_ids)

            replayed = self._journal(state_dir).replay(
                initial_snapshot(
                    factory.conversation_id,
                    conversation_generation=1,
                    capability_snapshot={"playback_interrupt": True},
                )
            )
            self.assertTrue(replayed.ok, replayed)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute("DROP TRIGGER reject_projection_ack")

            recovered_host = AkaneVoiceRuntimeHost(
                conversation_id=factory.conversation_id,
                conversation_generation=1,
                journal=self._journal(state_dir),
                projection_port=projections,
                command_executor=_InertCommandExecutor(),
                capability_snapshot={"playback_interrupt": True},
                restored_snapshot=replayed.replay.snapshot,
            )
            recovered = recovered_host.drain_projection_outbox()

            self.assertTrue(recovered.quiescent)
            self.assertEqual(recovered.projection_results[0].status, "duplicate")
            self.assertEqual(len(projections.records), 1)
            self.assertFalse(self._journal(state_dir).load_pending_projections().records)

    def test_command_observation_receipt_survives_restart_without_reexecution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            factory = EventFactory()
            journal = self._journal(state_dir)
            host = AkaneVoiceRuntimeHost(
                conversation_id=factory.conversation_id,
                conversation_generation=1,
                journal=journal,
                projection_port=_RecordingProjectionPort(),
                command_executor=_InertCommandExecutor(),
                capability_snapshot={"playback_interrupt": True},
            )
            self._accept_committed_turn(host=host, factory=factory)
            command = next(iter(host.snapshot.pending_commands.values()))
            observation = factory.make(
                "voice.response.created",
                voice_turn_id="turn-1",
                response_id="response-1",
                turn_revision=1,
                response_generation=1,
                payload={
                    "command_id": command.command_id,
                    "purpose": "content",
                    "commitment": "committed",
                },
            )
            self.assertTrue(journal.begin_command(voice_command_to_dict(command)).ok)
            self.assertTrue(
                journal.store_command_observations(
                    command.command_id,
                    (voice_event_to_dict(observation),),
                ).ok
            )

            replayed = self._journal(state_dir).replay(
                initial_snapshot(
                    factory.conversation_id,
                    conversation_generation=1,
                    capability_snapshot={"playback_interrupt": True},
                )
            )
            self.assertTrue(replayed.ok, replayed)
            executor = _FailIfExecutedCommandExecutor()
            recovered_host = AkaneVoiceRuntimeHost(
                conversation_id=factory.conversation_id,
                conversation_generation=1,
                journal=self._journal(state_dir),
                projection_port=_RecordingProjectionPort(),
                command_executor=executor,
                capability_snapshot={"playback_interrupt": True},
                restored_snapshot=replayed.replay.snapshot,
            )

            recovered = recovered_host.drive_once()

            self.assertEqual(recovered.status, "succeeded")
            self.assertEqual(executor.execute_calls, 0)
            self.assertEqual(executor.recover_calls, 0)
            self.assertIn("response-1", recovered_host.snapshot.responses)
            self.assertNotIn(command.command_id, recovered_host.snapshot.pending_commands)
            self.assertEqual(
                self._journal(state_dir).load_unfinished_commands().records,
                (),
            )

    def test_file_journal_is_idempotent_and_rejects_conflicting_event_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            factory = EventFactory()
            event = factory.make(
                "voice.input.activity_started",
                voice_turn_id="turn-1",
                audio_stream_id="microphone-1",
            )
            journal = self._journal(state_dir)

            first = journal.append(voice_event_to_dict(event))
            duplicate = self._journal(state_dir).append(voice_event_to_dict(event))
            conflicting_record = voice_event_to_dict(event)
            conflicting_record["audio_stream_id"] = "other-microphone"
            conflict = journal.append(conflicting_record)

            self.assertTrue(first.ok)
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(conflict.status, "failed")
            self.assertEqual(conflict.reason, "voice_journal_event_conflict")
            self.assertNotIn(str(state_dir), str(conflict))

    def test_sqlite_transaction_rolls_back_event_when_head_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            factory = EventFactory()
            journal = self._journal(state_dir)
            first_record = voice_event_to_dict(
                factory.make(
                    "voice.input.activity_started",
                    voice_turn_id="turn-1",
                    audio_stream_id="microphone-1",
                )
            )
            second_record = voice_event_to_dict(
                factory.make(
                    "voice.input.activity_started",
                    voice_turn_id="turn-2",
                    audio_stream_id="microphone-2",
                )
            )
            self.assertTrue(journal.append(first_record).ok)
            database_path = next(state_dir.rglob("journal.sqlite3"))
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TRIGGER reject_second_head_update
                    BEFORE UPDATE ON voice_journal_head
                    WHEN NEW.record_count = 2
                    BEGIN
                        SELECT RAISE(ABORT, 'private head failure');
                    END;
                    """
                )

            failed = journal.append(second_record)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.reason, "voice_journal_write_failed")
            self.assertNotIn("private head failure", str(failed))
            with closing(sqlite3.connect(database_path)) as connection, connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM voice_events").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT record_count FROM voice_journal_head WHERE singleton_id = 1").fetchone()[
                        0
                    ],
                    1,
                )
                connection.execute("DROP TRIGGER reject_second_head_update")

            retry = self._journal(state_dir).append(second_record)
            self.assertTrue(retry.ok)
            loaded = self._journal(state_dir).load_events()
            self.assertTrue(loaded.ok)
            self.assertEqual(len(loaded.events), 2)

    def test_journal_corruption_and_missing_committed_record_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            factory = EventFactory()
            journal = self._journal(state_dir)
            for turn_id in ("turn-1", "turn-2"):
                result = journal.append(
                    voice_event_to_dict(
                        factory.make(
                            "voice.input.activity_started",
                            voice_turn_id=turn_id,
                            audio_stream_id=f"microphone-{turn_id}",
                        )
                    )
                )
                self.assertTrue(result.ok)

            database_path = next(state_dir.rglob("journal.sqlite3"))
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute("DELETE FROM voice_events WHERE ordinal = 2")
            missing = self._journal(state_dir).load_events()
            self.assertEqual(missing.status, "failed")
            self.assertEqual(missing.reason, "voice_journal_record_missing")
            self.assertEqual(missing.events, ())

            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute("UPDATE voice_journal_head SET record_count = 1 WHERE singleton_id = 1")
                connection.execute("UPDATE voice_events SET event_json = '{broken' WHERE ordinal = 1")
            corrupt = self._journal(state_dir).load_events()
            self.assertEqual(corrupt.status, "failed")
            self.assertEqual(corrupt.reason, "voice_journal_unreadable")
            self.assertEqual(corrupt.events, ())

    def test_text_artifact_is_immutable_reopenable_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            artifacts = FileVoiceTextArtifactPort(
                state_dir=state_dir,
                conversation_id="conversation-1",
                conversation_generation=1,
            )
            text = "第一句。\n哈啊？！\n？"
            written = artifacts.put_text(
                artifact_key="voice-text:response-1:0",
                text=text,
                media_type="text/plain; charset=utf-8",
            )
            self.assertTrue(written.ok, written)
            self.assertRegex(written.artifact_ref, r"^voice-text:[0-9a-f]{64}$")
            self.assertNotIn(str(state_dir), str(written))

            reopened = FileVoiceTextArtifactPort(
                state_dir=state_dir,
                conversation_id="conversation-1",
                conversation_generation=1,
            )
            read = reopened.read_text(written.artifact_ref)
            duplicate = reopened.put_text(
                artifact_key="voice-text:response-1:0",
                text=text,
                media_type="text/plain; charset=utf-8",
            )
            conflict = reopened.put_text(
                artifact_key="voice-text:response-1:0",
                text="被篡改的另一份正文",
                media_type="text/plain; charset=utf-8",
            )

            self.assertTrue(read.ok, read)
            self.assertEqual(read.text, text)
            self.assertEqual(read.media_type, "text/plain; charset=utf-8")
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(conflict.status, "failed")
            self.assertEqual(conflict.reason, "voice_text_artifact_conflict")
            stored_payload = json.loads(next(state_dir.rglob("text_artifacts/*.json")).read_text(encoding="utf-8"))
            self.assertNotIn("voice-text:response-1:0", stored_payload.values())
            self.assertNotIn(str(state_dir), json.dumps(stored_payload, ensure_ascii=False))
            self.assertFalse(list(state_dir.rglob("*.tmp")))

    def test_text_artifact_rejects_invalid_ref_and_detects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            artifacts = FileVoiceTextArtifactPort(
                state_dir=state_dir,
                conversation_id="conversation-1",
                conversation_generation=1,
            )
            written = artifacts.put_text(
                artifact_key="unit-1",
                text="不可静默损坏。",
                media_type="text/plain",
            )
            self.assertTrue(written.ok)
            invalid = artifacts.read_text("../../outside")
            self.assertEqual(invalid.reason, "voice_text_artifact_ref_invalid")

            artifact_path = next(state_dir.rglob("text_artifacts/*.json"))
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            payload["text"] = "内容已损坏"
            artifact_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            corrupt = artifacts.read_text(written.artifact_ref)
            self.assertEqual(corrupt.status, "failed")
            self.assertEqual(corrupt.reason, "voice_text_artifact_unreadable")


if __name__ == "__main__":
    unittest.main()
