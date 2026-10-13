"""TTS lifecycle boundaries against real audio artifacts and command receipts."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.tts_service import TTSServiceError
from companion_v01.voice_runtime.durable_ports import (
    FileVoiceAudioArtifactPort,
    FileVoiceTextArtifactPort,
    SqliteVoiceRuntimeJournal,
    VoiceAudioArtifactReadResult,
)
from companion_v01.voice_runtime.host import AkaneVoiceRuntimeHost, VoiceHostPortResult
from companion_v01.voice_runtime.production_runtime.assembly import _SerializedVoiceRuntimeHost
from companion_v01.voice_runtime.production_runtime.lifecycle import _RuntimeResources
from companion_v01.voice_runtime.tts_executor import AkaneVoiceTTSCommandExecutor, VoiceCommandRouterExecutor
from tests.test_voice_runtime_host import _FakeAudioCommandExecutor, _FakeProjectionPort
from voicecore import snapshot_to_dict, voice_command_to_dict
from voicecore.testing import EventFactory


class _HeldRunner:
    def __init__(self):
        self.tasks = []
        self.submits = 0
        self.fail = False

    def submit(self, **task):
        self.submits += 1
        if self.fail:
            raise RuntimeError("private scheduler detail")
        self.tasks.append(task)

    def run(self):
        task = self.tasks.pop(0)
        task["fn"](*task["args"])


class _Client:
    provider_id = "fixture-provider"

    def __init__(self):
        self.calls = []
        self.failure = None

    def synthesize(self, text):
        self.calls.append(text)
        if self.failure is not None:
            raise self.failure
        return b"ID3-fixture-audio"


class VoiceTTSBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.factory = EventFactory()
        ports = dict(state_dir=Path(directory.name), conversation_id=self.factory.conversation_id,
                     conversation_generation=1)
        self.journal = SqliteVoiceRuntimeJournal(**ports)
        self.text = FileVoiceTextArtifactPort(**ports)
        self.audio = FileVoiceAudioArtifactPort(**ports)
        self.runner = _HeldRunner()
        self.client = _Client()
        self.notifications = []
        self.executor = AkaneVoiceTTSCommandExecutor(
            tts_client=self.client, text_artifacts=self.text, audio_artifacts=self.audio,
            conversation_id=self.factory.conversation_id, conversation_generation=1,
            background_tasks=self.runner,
        )
        router = VoiceCommandRouterExecutor({
            "start_response_generation": _FakeAudioCommandExecutor(self.factory),
            "start_tts": self.executor,
        })
        self.host = _SerializedVoiceRuntimeHost(AkaneVoiceRuntimeHost(
            conversation_id=self.factory.conversation_id, conversation_generation=1,
            journal=self.journal, projection_port=_FakeProjectionPort(), command_executor=router,
        ))
        self.executor.bind_host(self.host, notify_delivery=self.notifications.append)
        self.executor.enable_live_commands()

    def _prepare(self):
        events = [
            ("voice.input.activity_started", {"audio_stream_id": "audio-1"}),
            ("voice.asr.partial", {"turn_revision": 1, "payload": {"stable_text": "测试"}}),
            ("voice.asr.finalized", {"turn_revision": 2, "payload": {
                "stable_text": "测试输入", "supersedes_revision": 1}}),
            ("voice.turn.commit_requested", {"turn_revision": 2, "payload": {"disposition": "message"}}),
        ]
        for kind, fields in events:
            self.assertTrue(self.host.accept_event(self.factory.make(kind, voice_turn_id="turn-1", **fields)).accepted)
        self.assertEqual(self.host.drive_once().status, "succeeded")
        self.assertEqual(self.host.drive_once().status, "succeeded")
        written = self.text.put_text(artifact_key="tts-boundary:0", text="测试语音。", media_type="text/plain")
        event = self.factory.make(
            "voice.speech_unit.declared", voice_turn_id="turn-1", response_id="response-1",
            speech_unit_id="speech-1", turn_revision=2, response_generation=1,
            payload={"ordinal": 0, "purpose": "content", "text_artifact_ref": written.artifact_ref},
        )
        self.assertTrue(self.host.accept_event(event).accepted)
        self.command = next(c for c in self.host.snapshot.pending_commands.values() if c.command_kind == "start_tts")
        self.record = voice_command_to_dict(self.command)
        return self.record

    def _start(self):
        result = self.host.drive_once()
        self.assertEqual(result.status, "deferred")
        self.assertEqual(len(self.runner.tasks), 1)

    def _recover(self):
        return self.executor.recover(self.record, snapshot_to_dict(self.host.snapshot))

    def _events(self):
        loaded = self.journal.load_events()
        self.assertTrue(loaded.ok)
        return [event for event in loaded.events if event.event_kind.startswith("voice.tts.")]

    def _close_result(self):
        try:
            self.executor.close()
        except RuntimeError as exc:
            self.assertEqual(str(exc), "voice_tts_shutdown_settlement_pending")
            return "pending"
        return "closed"

    def _ack_lost(self, **fields):
        self._put_audio(**fields)
        raise RuntimeError("private audio publication acknowledgement lost")

    def _leave_audio_unconfirmed(self):
        self._put_audio = self.audio.put_audio
        missing = VoiceAudioArtifactReadResult(status="failed", reason="voice_audio_artifact_missing")
        self._start()
        with patch.object(self.audio, "put_audio", side_effect=self._ack_lost), \
                patch.object(self.audio, "read_audio", return_value=missing):
            self.runner.run()
        self.assertEqual(self._events(), [])

    def test_normal_background_synthesis_is_durable_once_without_playback_facts(self):
        self._prepare()
        self._start()
        self.runner.run()
        events = self._events()
        self.assertEqual([e.event_kind for e in events], ["voice.tts.started", "voice.tts.ready"])
        self.assertTrue(self.audio.read_audio(events[-1].payload["audio_artifact_ref"]).ok)
        self.assertEqual(self.client.calls, ["测试语音。"])
        self.assertNotIn(self.command.command_id, self.host.snapshot.pending_commands)
        self.assertEqual(self.notifications, ["turn-1"])

    def test_close_of_queued_job_prevents_provider_entry_and_settles_receipt(self):
        self._prepare()
        self._start()
        self.assertEqual(self._close_result(), "closed")
        self.assertNotIn(self.command.command_id, self.host.snapshot.pending_commands)
        self.runner.run()
        self.assertEqual(self.client.calls, [])
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.failed"])
        self.executor.close()

    def test_close_discards_late_audio_from_real_background_worker(self):
        self._prepare()
        entered, release = threading.Event(), threading.Event()
        runner = BackgroundTaskRunner()
        self.executor.background_tasks = runner
        original = self.client.synthesize

        def blocked(text):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture_wait_timeout")
            return original(text)

        try:
            with patch.object(self.client, "synthesize", side_effect=blocked):
                self.host.drive_once()
                self.assertTrue(entered.wait(2))
                self.assertEqual(self._close_result(), "pending")
                release.set()
                self.assertTrue(runner.wait_idle(timeout=5))
            self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.started", "voice.tts.failed"])
            self.assertEqual(self.client.calls, ["测试语音。"])
            self.executor.close()
        finally:
            release.set()
            runner.close(timeout=5)

    def test_close_during_audio_publication_does_not_emit_ready(self):
        self._prepare()
        self._start()
        original = self.audio.put_audio
        closing = []

        def put(**fields):
            stored = original(**fields)
            closing.append(self._close_result())
            return stored

        with patch.object(self.audio, "put_audio", side_effect=put):
            self.runner.run()
        self.assertEqual(closing, ["pending"])
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.started", "voice.tts.failed"])
        self.executor.close()

    def test_close_during_text_decode_blocks_provider_entry(self):
        from companion_v01.voice_runtime import speech_artifact
        self._prepare()
        self._start()
        original = speech_artifact.decode_speech_artifact

        def decode(text):
            self._close_result()
            return original(text)

        with patch.object(speech_artifact, "decode_speech_artifact", side_effect=decode):
            self.runner.run()
        self.assertEqual(self.client.calls, [])
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.failed"])
        self.executor.close()

    def test_closed_synchronous_executor_skips_text_artifact_read(self):
        self._prepare()
        self.executor.background_tasks = None
        self.executor.close()
        with patch.object(self.text, "read_text", wraps=self.text.read_text) as read:
            result = self.executor.execute(self.record, snapshot_to_dict(self.host.snapshot))
        self.assertEqual(result.observations[-1].payload["reason_code"], "voice_tts_executor_closed")
        read.assert_not_called()
        self.assertEqual(self.client.calls, [])

    def test_synchronous_audio_ack_loss_is_unknown_and_recovers_without_provider_retry(self):
        self._prepare()
        self.executor.background_tasks = None
        self._put_audio = self.audio.put_audio
        with patch.object(self.audio, "put_audio", side_effect=self._ack_lost):
            result = self.executor.execute(self.record, snapshot_to_dict(self.host.snapshot))
        self.assertFalse(result.outcome_known)
        self.assertEqual(result.observations, ())
        self.assertNotIn("private", str(result))
        recovered = self._recover()
        self.assertTrue(recovered.outcome_known)
        self.assertEqual(recovered.observations[-1].event_kind, "voice.tts.ready")
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_background_audio_ack_loss_recovers_actual_artifact(self):
        self._prepare()
        self._start()
        self._put_audio = self.audio.put_audio
        with patch.object(self.audio, "put_audio", side_effect=self._ack_lost):
            self.runner.run()
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.started", "voice.tts.ready"])
        self.assertEqual(self.client.calls, ["测试语音。"])
        self.assertNotIn(self.command.command_id, self.host.snapshot.pending_commands)

    def test_temporarily_unreadable_audio_is_recovered_when_storage_returns(self):
        self._prepare()
        self._leave_audio_unconfirmed()
        recovered = self._recover()
        self.assertTrue(recovered.outcome_known)
        self.assertEqual(recovered.observations[-1].event_kind, "voice.tts.ready")
        self.host.drive_once()
        self.assertEqual(len(self._events()), 2)
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_recovery_read_exception_remains_structured_and_retryable(self):
        self._prepare()
        self.executor.background_tasks = None
        self.executor.execute(self.record, snapshot_to_dict(self.host.snapshot))
        with patch.object(self.audio, "read_audio", side_effect=RuntimeError("private storage path")):
            recovered = self._recover()
        self.assertFalse(recovered.outcome_known)
        self.assertTrue(recovered.retryable)
        self.assertEqual(recovered.observations, ())
        self.assertNotIn("private", str(recovered))
        self.assertEqual(self._recover().observations[-1].event_kind, "voice.tts.ready")
        self.assertEqual(len(self.client.calls), 1)

    def test_concurrent_audio_recovery_reuses_the_same_completion_events(self):
        self._prepare()
        self._leave_audio_unconfirmed()
        barrier = threading.Barrier(2)
        original = self.audio.read_audio
        results, errors = [], []

        def read(ref):
            barrier.wait(timeout=2)
            return original(ref)

        def recover():
            try:
                results.append(self._recover())
            except Exception as exc:
                errors.append(type(exc).__name__)

        with patch.object(self.audio, "read_audio", side_effect=read):
            threads = [threading.Thread(target=recover) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.outcome_known for result in results))
        self.assertEqual(results[0].observations, results[1].observations)
        self.assertEqual(len(self.client.calls), 1)

    def test_unknown_provider_outcome_never_repeats_synthesis(self):
        self._prepare()

        def unknown(text, **_context):
            self.client.calls.append(text)
            raise TTSServiceError("fixture_provider_unknown", outcome_known=False)

        self.client.synthesize_command = unknown
        self._start()
        self.runner.run()
        for _ in range(2):
            result = self._recover()
            self.assertFalse(result.outcome_known)
            self.assertEqual(result.observations, ())
        self.assertEqual(self.client.calls, ["测试语音。"])
        self.assertEqual(self._events(), [])

    def test_schedule_failure_survives_a_failed_observation_write(self):
        self._prepare()
        self.runner.fail = True
        failed = VoiceHostPortResult.failed("fixture_receipt_write_unavailable", retryable=True)
        with patch.object(self.journal, "store_command_observations", return_value=failed):
            self.assertEqual(self.host.drive_once().status, "deferred")
        self.host.drive_once()
        events = self._events()
        self.assertEqual([e.event_kind for e in events], ["voice.tts.failed"])
        self.assertEqual(events[0].payload["reason_code"], "voice_tts_schedule_failed")
        self.assertEqual(self.runner.submits, 1)
        self.assertEqual(self.client.calls, [])

    def test_close_preserves_failed_confirmation_until_receipt_retry_succeeds(self):
        self._prepare()
        self.runner.fail = True
        failed = VoiceHostPortResult.failed("fixture_receipt_write_unavailable", retryable=True)
        owned = _RuntimeResources(executors=[self.executor])
        with patch.object(self.journal, "store_command_observations", return_value=failed):
            self.host.drive_once()
            self.assertEqual(owned.close_executors(), ["voice_runtime_executor_close_failed"])
            self.assertEqual(owned.executors, [self.executor])
        self.assertEqual(owned.close_executors(), [])
        self.assertEqual(owned.executors, [])
        self.assertNotIn(self.command.command_id, self.host.snapshot.pending_commands)
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.failed"])
        self.assertEqual(self.client.calls, [])

    def test_concurrent_close_cannot_report_settled_during_receipt_confirmation(self):
        self._prepare()
        self._start()
        entered, release = threading.Event(), threading.Event()
        original = self.host.drain_command_receipts
        outcomes = []

        def drain():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture_wait_timeout")
            return original()

        with patch.object(self.host, "drain_command_receipts", side_effect=drain):
            thread = threading.Thread(target=lambda: outcomes.append(self._close_result()))
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self._close_result(), "pending")
            finally:
                release.set()
                thread.join(timeout=3)
        self.assertEqual(outcomes, ["closed"])
        self.runner.run()
        self.assertEqual(self.client.calls, [])

    def test_reentrant_close_keeps_the_original_confirmation_owner(self):
        self._prepare()
        self._start()
        original = self.host.drain_command_receipts
        nested = []

        def drain():
            nested.append(self._close_result())
            return original()

        with patch.object(self.host, "drain_command_receipts", side_effect=drain):
            self.assertEqual(self._close_result(), "closed")
        self.assertEqual(nested, ["pending"])
        self.runner.run()
        self.assertEqual(self.client.calls, [])

    def test_concurrent_duplicate_commands_submit_once(self):
        self._prepare()
        barrier = threading.Barrier(2)
        results = []

        def start():
            barrier.wait(timeout=2)
            results.append(self.executor.execute(self.record, snapshot_to_dict(self.host.snapshot)))

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.in_progress for result in results))
        self.assertEqual(self.runner.submits, 1)
        self.runner.run()
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_completion_receipt_ack_loss_keeps_original_audio_and_events(self):
        self._prepare()
        self._start()
        original = self.journal.store_command_observations
        lost = []

        def store(command_id, observations):
            result = original(command_id, observations)
            if command_id == self.command.command_id and not lost:
                lost.append(tuple(observations))
                raise RuntimeError("private receipt acknowledgement lost")
            return result

        with patch.object(self.journal, "store_command_observations", side_effect=store):
            self.runner.run()
        self.host.drive_once()
        events = self._events()
        self.assertEqual([e.event_kind for e in events], ["voice.tts.started", "voice.tts.ready"])
        self.assertEqual([e.event_id for e in events], [r["event_id"] for r in lost[0]])
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_generic_provider_failure_is_terminal_without_private_exception_detail(self):
        self._prepare()
        self.client.failure = RuntimeError("private provider secret")
        self._start()
        self.runner.run()
        events = self._events()
        self.assertEqual([e.event_kind for e in events], ["voice.tts.started", "voice.tts.failed"])
        self.assertEqual(events[-1].payload["reason_code"], "voice_tts_synthesis_failed")
        self.assertNotIn("private", repr(events))
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_failed_completion_marker_keeps_cleanup_owned_until_retry(self):
        self._prepare()
        self.client.failure = RuntimeError("private provider failure")
        self._start()
        failed = VoiceHostPortResult.failed("fixture_completion_marker_unavailable", retryable=True)
        owned = _RuntimeResources(executors=[self.executor])
        with patch.object(self.journal, "mark_command_completed", return_value=failed):
            self.runner.run()
            self.assertNotIn(self.command.command_id, self.host.snapshot.pending_commands)
            self.assertEqual(owned.close_executors(), ["voice_runtime_executor_close_failed"])
            self.assertEqual(owned.executors, [self.executor])
        self.assertEqual(owned.close_executors(), [])
        self.assertEqual(owned.executors, [])
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.started", "voice.tts.failed"])
        self.assertEqual(self.journal.load_unfinished_commands().records, ())
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_removed_command_keeps_unconfirmed_receipt_release_owned(self):
        self._prepare()
        self._start()
        removed = self.factory.make(
            "voice.response.failed", voice_turn_id="turn-1", response_id="response-1",
            turn_revision=2, response_generation=1,
            payload={"stage": "thinking_agent", "reason_code": "fixture_response_removed"},
        )
        self.assertTrue(self.host.accept_event(removed).accepted)
        self.assertNotIn(self.command.command_id, self.host.snapshot.pending_commands)
        failed = VoiceHostPortResult.failed("fixture_receipt_release_unavailable", retryable=True)
        with patch.object(self.journal, "release_command", return_value=failed):
            self.assertEqual(self._close_result(), "pending")
        self.assertEqual(self._close_result(), "closed")
        self.runner.run()
        self.assertEqual(self.journal.load_unfinished_commands().records, ())
        self.assertEqual(self.client.calls, [])

    def test_completed_marker_ack_loss_is_confirmed_from_durable_receipts(self):
        self._prepare()
        self._start()
        original = self.journal.mark_command_completed

        def mark(command_id):
            result = original(command_id)
            if command_id == self.command.command_id:
                raise RuntimeError("private completion marker acknowledgement lost")
            return result

        with patch.object(self.journal, "mark_command_completed", side_effect=mark):
            self.runner.run()
            self.assertEqual(self._close_result(), "closed")
        self.assertEqual(self.journal.load_unfinished_commands().records, ())
        self.assertEqual([e.event_kind for e in self._events()], ["voice.tts.started", "voice.tts.ready"])
        self.assertEqual(self.client.calls, ["测试语音。"])

    def test_closed_preflight_failure_survives_receipt_write_failure(self):
        self._prepare()
        self.executor.close()
        failed = VoiceHostPortResult.failed("fixture_receipt_write_unavailable", retryable=True)
        with patch.object(self.journal, "store_command_observations", return_value=failed):
            self.assertEqual(self.host.drive_once().status, "deferred")
        self.host.drive_once()
        events = self._events()
        self.assertEqual([e.event_kind for e in events], ["voice.tts.failed"])
        self.assertEqual(events[0].payload["reason_code"], "voice_tts_executor_closed")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.runner.submits, 0)

    def test_restart_preflight_failure_survives_receipt_write_failure(self):
        self._prepare()
        self.executor._live_commands = False
        failed = VoiceHostPortResult.failed("fixture_receipt_write_unavailable", retryable=True)
        with patch.object(self.journal, "store_command_observations", return_value=failed):
            self.assertEqual(self.host.drive_once().status, "deferred")
        self.host.drive_once()
        events = self._events()
        self.assertEqual([e.event_kind for e in events], ["voice.tts.failed"])
        self.assertEqual(events[0].payload["reason_code"], "voice_tts_runtime_restarted")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.runner.submits, 0)
