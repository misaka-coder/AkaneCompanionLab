"""Host failure propagation and safe retries across actual durable receipts."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.voice_runtime.durable_ports import SqliteVoiceRuntimeJournal
from companion_v01.voice_runtime.host import (
    AkaneVoiceRuntimeHost,
    VoiceCommandExecutionResult,
    VoiceHostPortResult,
)
from tests.test_voice_runtime_host import _FakeAudioCommandExecutor, _FakeProjectionPort
from voicecore import initial_snapshot
from voicecore.testing import EventFactory


class VoiceHostBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.factory = EventFactory()
        self.journal = SqliteVoiceRuntimeJournal(
            state_dir=Path(folder.name),
            conversation_id=self.factory.conversation_id,
            conversation_generation=1,
        )
        self.executor = _FakeAudioCommandExecutor(self.factory)
        self.projections = _FakeProjectionPort()
        self.host = self._host()

    def _host(self, restored_snapshot=None):
        return AkaneVoiceRuntimeHost(
            conversation_id=self.factory.conversation_id,
            conversation_generation=1,
            journal=self.journal,
            projection_port=self.projections,
            command_executor=self.executor,
            capability_snapshot={"playback_interrupt": True},
            restored_snapshot=restored_snapshot,
        )

    def _restart(self) -> None:
        replayed = self.journal.replay(initial_snapshot(
            self.factory.conversation_id,
            conversation_generation=1,
            capability_snapshot={"playback_interrupt": True},
        ))
        self.assertTrue(replayed.ok)
        self.host = self._host(replayed.replay.snapshot)

    def _accept(self, kind, **fields):
        result = self.host.accept_event(self.factory.make(kind, **fields))
        self.assertTrue(result.accepted, (result.status, result.reason))
        return result

    def _commit_input(self):
        self._accept("voice.input.activity_started", voice_turn_id="turn-1", audio_stream_id="microphone-1")
        self._accept("voice.asr.partial", voice_turn_id="turn-1", turn_revision=1, payload={"stable_text": "测试"})
        self._accept("voice.asr.finalized", voice_turn_id="turn-1", turn_revision=2,
                     payload={"stable_text": "测试回执", "supersedes_revision": 1})
        self._accept("voice.turn.commit_requested", voice_turn_id="turn-1", turn_revision=2,
                     payload={"disposition": "message"})
        return next(iter(self.host.snapshot.pending_commands.values()))

    def _prepare_tts(self):
        self._commit_input()
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self._accept("voice.speech_unit.declared", voice_turn_id="turn-1", response_id="response-1",
                     speech_unit_id="speech-unit-1", turn_revision=2, response_generation=1,
                     payload={"ordinal": 0, "purpose": "content", "text_artifact_ref": "text:boundary-1"})
        return next(c for c in self.host.snapshot.pending_commands.values() if c.command_kind == "start_tts")

    def _unfinished(self):
        loaded = self.journal.load_unfinished_commands()
        self.assertTrue(loaded.ok)
        return loaded.records

    def test_hard_completion_failure_fails_drive_and_restart_only_settles_receipt(self):
        command = self._commit_input()
        with patch.object(self.journal, "mark_command_completed",
                          return_value=VoiceHostPortResult.failed("completion_denied")):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.reason, "completion_denied")
        self.assertFalse(driven.quiescent)
        self.assertEqual(self._unfinished()[0].phase, "observations_pending")
        self.assertNotIn(command.command_id, self.host.snapshot.pending_commands)
        calls = tuple(self.executor.command_kinds)
        self._restart()
        self.assertTrue(self.host.drain_command_receipts().quiescent)
        self.assertEqual(tuple(self.executor.command_kinds), calls)
        self.assertEqual(self._unfinished(), ())

    def test_invalid_completion_result_fails_outer_drive(self):
        self._commit_input()
        with patch.object(self.journal, "mark_command_completed", return_value=True):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.reason, "invalid_command_receipt_completion_result")
        self.assertEqual(len(self._unfinished()), 1)

    def test_transient_observation_append_defers_and_restart_reuses_saved_result(self):
        command = self._commit_input()
        before = self.host.snapshot
        with patch.object(self.journal, "append_transition",
                          return_value=VoiceHostPortResult.failed("journal_busy", retryable=True)):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "deferred")
        self.assertEqual(driven.reason, "journal_busy")
        self.assertIs(self.host.snapshot, before)
        self.assertEqual(driven.command_receipt_result.pending_receipt_command_ids, (command.command_id,))
        self.assertTrue(driven.dispatch_results[0].journal_result.retryable)
        calls = tuple(self.executor.command_kinds)
        self._restart()
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(tuple(self.executor.command_kinds), calls)
        self.assertNotIn(command.command_id, self.host.snapshot.pending_commands)
        self.assertEqual(self._unfinished(), ())

    def test_hard_observation_append_failure_remains_failed(self):
        self._commit_input()
        with patch.object(self.journal, "append_transition",
                          return_value=VoiceHostPortResult.failed("journal_corrupt")):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.reason, "journal_corrupt")
        self.assertEqual(len(self._unfinished()), 1)

    def _assert_committed_observations_survive_exception(self, error):
        self._commit_input()
        original = self.journal.store_command_observations

        def commit_then_raise(*args):
            result = original(*args)
            self.assertTrue(result.ok)
            raise error("private storage detail")

        with patch.object(self.journal, "store_command_observations", side_effect=commit_then_raise):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "deferred")
        self.assertEqual(driven.reason, "command_observation_store_failed")
        self.assertNotIn("private storage detail", repr(driven))
        self.assertEqual(self._unfinished()[0].phase, "observations_pending")
        calls = tuple(self.executor.command_kinds)
        self._restart()
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(tuple(self.executor.command_kinds), calls)

    def test_type_error_after_observation_commit_does_not_reexecute(self):
        self._assert_committed_observations_survive_exception(TypeError)

    def test_value_error_after_observation_commit_does_not_reexecute(self):
        self._assert_committed_observations_survive_exception(ValueError)

    def test_unknown_not_started_recovery_never_releases_or_reexecutes_effect(self):
        command = self._commit_input()
        self.assertTrue(self.host._begin_command(command).ok)
        with patch.object(self.executor, "recover", return_value=VoiceCommandExecutionResult(
            status="not_started", outcome_known=False,
        )):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "deferred")
        self.assertFalse(driven.quiescent)
        self.assertEqual(self.executor.command_kinds, [])
        self.assertEqual(self._unfinished()[0].phase, "executing")
        self.assertIn(command.command_id, self.host.snapshot.pending_commands)

    def test_success_with_unknown_outcome_retains_executing_receipt(self):
        command = self._commit_input()
        original = self.executor.execute

        def unknown_success(*args):
            return replace(original(*args), outcome_known=False)

        with patch.object(self.executor, "execute", side_effect=unknown_success):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "failed")
        self.assertFalse(driven.command_results[0].outcome_known)
        self.assertEqual(self._unfinished()[0].phase, "executing")
        self.assertIn(command.command_id, self.host.snapshot.pending_commands)
        self.assertEqual(self.host.snapshot.responses, {})

    def test_invalid_observation_payload_returns_structured_unknown(self):
        command = self._commit_input()
        bad = self.factory.make("voice.response.created", payload=None)
        with patch.object(self.executor, "execute", return_value=VoiceCommandExecutionResult.succeeded(bad)):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.command_results[0].reason, "invalid_command_observation")
        self.assertFalse(driven.command_results[0].outcome_known)
        self.assertEqual(self._unfinished()[0].phase, "executing")
        self.assertIn(command.command_id, self.host.snapshot.pending_commands)

    def test_repeated_intermediate_observation_does_not_fail_successful_tts(self):
        self._prepare_tts()
        original = self.executor.execute

        def deliver_first_observation(*args):
            result = original(*args)
            self.assertTrue(self.host.accept_event(result.observations[0]).accepted)
            return result

        with patch.object(self.executor, "execute", side_effect=deliver_first_observation):
            driven = self.host.drive_once()
        self.assertEqual([d.status for d in driven.dispatch_results], ["duplicate", "accepted"])
        self.assertTrue(driven.command_receipt_result.quiescent)
        self.assertEqual(driven.status, "succeeded")
        self.assertEqual(self.executor.command_kinds.count("start_tts"), 1)
        self.assertEqual(self._unfinished(), ())

    def test_transient_completion_failure_defers_until_confirmation(self):
        self._commit_input()
        with patch.object(self.journal, "mark_command_completed",
                          return_value=VoiceHostPortResult.failed("completion_busy", retryable=True)):
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "deferred")
        self.assertEqual(driven.reason, "completion_busy")
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self._unfinished(), ())

    def test_confirmed_not_started_recovery_allows_one_safe_execution(self):
        command = self._commit_input()
        self.assertTrue(self.host._begin_command(command).ok)
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self.executor.command_kinds, ["start_response_generation"])
        self.assertEqual(self._unfinished(), ())

    def test_serialization_failure_does_not_invoke_observation_store(self):
        self._commit_input()
        original = self.executor.execute

        def unencodable_result(*args):
            result = original(*args)
            observation = result.observations[0]
            return replace(result, observations=(replace(observation, payload={
                **observation.payload, "private_value": object(),
            }),))

        with patch.object(self.executor, "execute", side_effect=unencodable_result), \
                patch.object(self.journal, "store_command_observations") as store:
            driven = self.host.drive_once()
        self.assertEqual(driven.status, "failed")
        self.assertEqual(driven.reason, "command_observation_not_serializable")
        store.assert_not_called()
        self.assertEqual(self._unfinished()[0].phase, "executing")
        self.assertEqual(self.host.snapshot.responses, {})

    def test_invalid_journal_results_never_advance_snapshot(self):
        event = self.factory.make("voice.input.activity_started", voice_turn_id="turn-1", audio_stream_id="microphone-1")
        before = self.host.snapshot
        for value, reason in ((True, "invalid_journal_result"),
                              (VoiceHostPortResult(status="invalid"), "invalid_journal_status")):
            with self.subTest(reason=reason), patch.object(self.journal, "append_transition", return_value=value):
                dispatched = self.host.accept_event(event)
                self.assertEqual(dispatched.status, "failed")
                self.assertEqual(dispatched.reason, reason)
                self.assertFalse(dispatched.snapshot_advanced)
                self.assertIs(self.host.snapshot, before)
                self.assertEqual(self.executor.command_kinds, [])
                self.assertEqual(self.projections.records, [])

    def _hold_input_projection(self):
        with patch.object(self.projections, "emit", return_value=VoiceHostPortResult.failed("held", retryable=True)):
            self._commit_input()
        pending = self.journal.load_pending_projections()
        self.assertTrue(pending.ok)
        self.assertTrue(pending.records)
        return tuple(r["projection_id"] for r in pending.records)

    def test_invalid_projection_results_keep_delivery_pending_and_block_effects(self):
        pending = self._hold_input_projection()
        for value, reason in ((True, "invalid_projection_result"),
                              (VoiceHostPortResult(status="invalid"), "invalid_projection_status")):
            with self.subTest(reason=reason), patch.object(self.projections, "emit", return_value=value), \
                    patch.object(self.journal, "mark_projection_delivered") as ack:
                driven = self.host.drive_once()
                self.assertEqual(driven.status, "failed")
                self.assertEqual(driven.reason, reason)
                self.assertEqual(driven.pending_projection_ids, pending)
                ack.assert_not_called()
                self.assertEqual(self.executor.command_kinds, [])

    def test_invalid_projection_acknowledgements_keep_stable_retry_identity(self):
        pending = self._hold_input_projection()
        for value, reason in ((True, "invalid_projection_acknowledgement_result"),
                              (VoiceHostPortResult(status="invalid"), "invalid_projection_acknowledgement_status")):
            with self.subTest(reason=reason), patch.object(self.journal, "mark_projection_delivered", return_value=value):
                driven = self.host.drive_once()
                self.assertEqual(driven.status, "failed")
                self.assertEqual(driven.reason, reason)
                self.assertEqual(driven.pending_projection_ids, pending)
                self.assertEqual(self.executor.command_kinds, [])
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self.journal.load_pending_projections().records, ())
        delivered_ids = [r["projection_id"] for r in self.projections.records]
        self.assertEqual(len(delivered_ids), len(set(delivered_ids)))


if __name__ == "__main__":
    unittest.main()
