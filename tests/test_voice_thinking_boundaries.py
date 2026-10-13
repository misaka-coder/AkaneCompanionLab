"""Real Host/journal generation boundaries with controlled streams and barriers."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.voice_runtime.durable_ports import FileVoiceTextArtifactPort, SqliteVoiceRuntimeJournal
from companion_v01.voice_runtime.host import AkaneVoiceRuntimeHost, VoiceHostPortResult
from companion_v01.voice_runtime.production_runtime.assembly import _SerializedVoiceRuntimeHost
from companion_v01.voice_runtime.thinking_agent import AkaneThinkingAgentCommandExecutor
from tests.test_voice_runtime_host import _FakeProjectionPort
from voicecore import snapshot_to_dict
from voicecore.testing import EventFactory


class _HeldRunner:
    def __init__(self):
        self.tasks = []

    def submit(self, **task):
        self.tasks.append(task)

    def run(self):
        task = self.tasks.pop(0)
        task["fn"](*task["args"])


class _Engine:
    def __init__(self):
        self.calls = []
        self.closed = threading.Event()
        self.fail = False

    def process_voice_turn_stream(self, **context):
        self.calls.append(context)
        try:
            if self.fail:
                raise RuntimeError("private model detail")
            yield {"type": "final", "payload": {"speech": "回复内容"}}
        finally:
            self.closed.set()


class _SnapshotHook:
    def __init__(self, host, hook):
        self.host, self.hook = host, hook

    @property
    def snapshot(self):
        self.hook()
        return self.host.snapshot

    def __getattr__(self, name):
        return getattr(self.host, name)


class VoiceThinkingBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.factory = EventFactory()
        ports = dict(state_dir=Path(directory.name), conversation_id=self.factory.conversation_id,
                     conversation_generation=1)
        self.journal = SqliteVoiceRuntimeJournal(**ports)
        self.artifacts = FileVoiceTextArtifactPort(**ports)
        self.projections = _FakeProjectionPort()
        self.engine = _Engine()
        self.runner = _HeldRunner()
        self.manager = SimpleNamespace(resolve_voice_projection_turn=lambda **fields: {
            "ok": True, "status": "ready", "source_id": fields["projection_id"],
            "turn_id": "memory-turn-1", "text": "测试输入", "timestamp": 1,
        })
        self.executor = self._executor()
        self.executor.register_turn(voice_turn_id="turn-1", event_factory=self.factory)
        self.host = _SerializedVoiceRuntimeHost(AkaneVoiceRuntimeHost(
            conversation_id=self.factory.conversation_id, conversation_generation=1,
            journal=self.journal, projection_port=self.projections, command_executor=self.executor,
        ))

    def _executor(self, runner=None):
        return AkaneThinkingAgentCommandExecutor(
            engine=self.engine, memcore_manager=self.manager, profile_user_id="profile-1",
            session_id="session-1", character_pack_id="character-1",
            conversation_id=self.factory.conversation_id, conversation_generation=1,
            text_artifacts=self.artifacts, background_tasks=runner or self.runner,
        )

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
        self.assertEqual(self.engine.calls, [])
        return next(iter(self.executor._jobs.values()))

    def _start(self):
        result = self.executor.start_ready_generations(self.host, voice_turn_id="turn-1")
        self.assertTrue(result.ok, (result.status, result.reason))
        return result

    def _assistant_records(self):
        return [p for p in self.projections.records if p["kind"] == "message.assistant.voice"]

    def test_close_blocks_an_already_queued_model_job(self):
        self._prepare()
        self._start()
        self.executor.close()
        self.runner.run()
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self._assistant_records(), [])
        self.assertFalse(self.executor.has_running_turn("turn-1"))
        self.assertEqual(self.executor._jobs, {})

    def test_close_discards_late_final_from_real_background_worker(self):
        job = self._prepare()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def blocked_stream(**context):
            calls.append(context)
            entered.set()
            try:
                if not release.wait(15):
                    raise RuntimeError("test stream barrier timeout")
                yield {"type": "final", "payload": {"speech": "晚返回的回复"}}
            finally:
                closed.set()

        worker = BackgroundTaskRunner()
        self.addCleanup(lambda: worker.close(timeout=5))
        self.addCleanup(release.set)
        self.engine.process_voice_turn_stream = blocked_stream
        self.executor.background_tasks = worker
        self._start()
        self.assertTrue(entered.wait(5))
        self.executor.close()
        release.set()
        self.assertTrue(worker.wait_idle(timeout=5))
        self.assertTrue(closed.is_set())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self._assistant_records(), [])
        self.assertNotEqual(self.host.snapshot.responses[job.response_id].state.value, "completed")
        self.assertEqual(self.executor._jobs, {})

    def test_close_during_presentation_staging_does_not_commit_final(self):
        sink = Mock(side_effect=lambda *_: self.executor.close())
        self.executor.register_turn(voice_turn_id="turn-1", event_factory=self.factory, presentation_sink=sink)
        self._prepare()
        self._start()
        self.runner.run()
        sink.assert_called_once()
        self.assertEqual(self._assistant_records(), [])
        self.assertTrue(self.engine.closed.is_set())

    def test_close_while_reading_playback_context_prevents_model_entry(self):
        self._prepare()
        self._start()
        module = importlib.import_module(self.executor._run_generation.__module__)
        with patch.object(module, "render_unsettled_playback_context",
                          side_effect=lambda *_args, **_fields: (self.executor.close(), "")[1]):
            self.runner.run()
        self.assertEqual(self.engine.calls, [])

    def test_reregister_without_callbacks_clears_previous_channel(self):
        old_delivery, old_presentation = Mock(), Mock()
        self.executor.register_turn(voice_turn_id="turn-1", event_factory=self.factory,
                                    delivery_notifier=old_delivery, presentation_sink=old_presentation)
        self.executor.register_turn(voice_turn_id="turn-1", event_factory=self.factory)
        self._prepare()
        self._start()
        self.runner.run()
        old_delivery.assert_not_called()
        old_presentation.assert_not_called()

    def test_old_job_cleanup_preserves_new_turn_registration(self):
        self._prepare()
        self._start()
        new_factory = EventFactory()
        new_delivery, new_presentation = Mock(), Mock()
        self.executor.register_turn(voice_turn_id="turn-1", event_factory=new_factory,
                                    delivery_notifier=new_delivery, presentation_sink=new_presentation)
        self.runner.run()
        self.assertIs(self.executor._event_factory_for_turn("turn-1"), new_factory)
        self.assertIs(self.executor._delivery_notifier("turn-1"), new_delivery)
        self.assertIs(self.executor._presentation_sinks["turn-1"], new_presentation)
        self.assertEqual(self.executor._jobs, {})

    def test_closed_executor_rejects_generation_commands(self):
        self._prepare()
        command = {
            "command_id": "new-command", "command_kind": "start_response_generation", "idempotency_key": "new-key",
            "payload": {"voice_turn_id": "turn-1", "turn_revision": 2},
        }
        self.executor.close()
        result = self.executor.execute(command, snapshot_to_dict(self.host.snapshot))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "voice_thinking_executor_closed")
        self.assertEqual(self.executor._jobs, {})

    def test_late_context_resolution_does_not_publish_job_after_close(self):
        self._prepare()
        job = next(iter(self.executor._jobs.values()))
        resolver = self.manager.resolve_voice_projection_turn

        def close_then_resolve(**fields):
            self.executor.close()
            return resolver(**fields)

        command = {"command_id": "start-again", "command_kind": "start_response_generation", "payload": {
            "voice_turn_id": "turn-1", "turn_revision": 2, "response_id": job.response_id,
            "response_generation": 1,
        }}
        with patch.object(self.manager, "resolve_voice_projection_turn", side_effect=close_then_resolve):
            result = self.executor.execute(command, snapshot_to_dict(self.host.snapshot))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "voice_thinking_executor_closed")
        self.assertEqual(self.executor._jobs, {})

    def test_closed_executor_rejects_restore_without_recreating_jobs(self):
        self._prepare()
        fresh = self._executor()
        fresh.close()
        result = fresh.restore_generating_jobs(snapshot_to_dict(self.host.snapshot))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "voice_thinking_executor_closed")
        self.assertEqual(fresh._jobs, {})

    def test_late_restore_context_cannot_repopulate_closed_executor(self):
        self._prepare()
        fresh = self._executor()
        resolver = self.manager.resolve_voice_projection_turn

        def close_then_resolve(**fields):
            fresh.close()
            return resolver(**fields)

        with patch.object(self.manager, "resolve_voice_projection_turn", side_effect=close_then_resolve):
            result = fresh.restore_generating_jobs(snapshot_to_dict(self.host.snapshot))
        self.assertEqual(result.status, "failed")
        self.assertEqual(fresh._jobs, {})

    def test_close_between_ready_snapshot_and_submission_rejects_start(self):
        self._prepare()
        view = _SnapshotHook(self.host, self.executor.close)
        result = self.executor.start_ready_generations(view, voice_turn_id="turn-1")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "voice_thinking_executor_closed")
        self.assertEqual(self.runner.tasks, [])

    def test_concurrent_starts_submit_once_without_false_failure(self):
        self._prepare()
        barrier, guard = threading.Barrier(2), threading.Lock()
        visits = 0

        def rendezvous():
            nonlocal visits
            with guard:
                visits += 1
                should_wait = visits <= 2
            if should_wait:
                barrier.wait(timeout=5)

        view = _SnapshotHook(self.host, rendezvous)
        results, errors = [], []

        def start():
            try:
                results.append(self.executor.start_ready_generations(view, voice_turn_id="turn-1"))
            except Exception as error:
                errors.append(type(error).__name__)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(6)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.ok for r in results), [r.status for r in results])
        self.assertEqual(len(self.runner.tasks), 1)
        self.runner.run()
        self.assertEqual(len(self.engine.calls), 1)

    def test_schedule_failure_releases_unused_job_and_callbacks(self):
        job = self._prepare()
        with patch.object(self.runner, "submit", side_effect=RuntimeError("private queue detail")):
            result = self.executor.start_ready_generations(self.host, voice_turn_id="turn-1")
        self.assertEqual(result.reason, "voice_thinking_schedule_failed")
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertFalse(self.executor.has_running_turn("turn-1"))
        self.assertEqual(self.executor._jobs, {})
        self.assertEqual(self.executor._event_factories, {})
        self.assertEqual(self.engine.calls, [])

    def test_unconfirmed_failure_retains_cleanup_and_does_not_restart_model(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        accept = self.host.accept_event

        def reject_failure(event):
            if event.event_kind == "voice.response.failed":
                raise RuntimeError("private journal detail")
            return accept(event)

        with patch.object(self.host, "accept_event", side_effect=reject_failure):
            self.runner.run()
            self.assertFalse(self.executor.has_running_turn("turn-1"))
            retry = self.executor.start_ready_generations(self.host, voice_turn_id="turn-1")
            self.assertFalse(retry.ok)
            self.assertEqual(self.runner.tasks, [])
            self.assertNotIn("private journal detail", repr(retry))
        self.assertTrue(self.executor.start_ready_generations(self.host, voice_turn_id="turn-1").ok)
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.executor._jobs, {})

    def test_failure_commit_ack_loss_retries_same_event_without_new_model_call(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        append = self.journal.append_transition

        def commit_then_raise(event, projections):
            result = append(event, projections)
            self.assertTrue(result.ok)
            if event["event_kind"] == "voice.response.failed":
                raise RuntimeError("private commit acknowledgement detail")
            return result

        with patch.object(self.journal, "append_transition", side_effect=commit_then_raise):
            self.runner.run()
            self.assertFalse(self.executor.start_ready_generations(self.host, voice_turn_id="turn-1").ok)
        self.assertTrue(self.executor.start_ready_generations(self.host, voice_turn_id="turn-1").ok)
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        loaded = self.journal.load_events()
        self.assertTrue(loaded.ok)
        self.assertEqual(sum(e.event_kind == "voice.response.failed" for e in loaded.events), 1)
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.runner.tasks, [])

    def test_close_reports_pending_failure_until_retry_is_confirmed(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        with patch.object(self.journal, "append_transition", return_value=VoiceHostPortResult.failed("journal_busy", retryable=True)):
            self.runner.run()
            with self.assertRaisesRegex(RuntimeError, "voice_thinking_shutdown_settlement_pending"):
                self.executor.close()
        self.executor.close()
        self.executor.close()
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(self.executor._jobs, {})
        self.assertEqual(len(self.engine.calls), 1)

    def test_failure_event_factory_error_remains_retryable_cleanup(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        make = self.factory.make

        def fail_build(kind, **fields):
            if kind == "voice.response.failed":
                raise RuntimeError("private factory detail")
            return make(kind, **fields)

        with patch.object(self.factory, "make", side_effect=fail_build):
            self.runner.run()
            result = self.executor.start_ready_generations(self.host, voice_turn_id="turn-1")
            self.assertFalse(result.ok)
            self.assertNotIn("private factory detail", repr(result))
        self.assertTrue(self.executor.start_ready_generations(self.host, voice_turn_id="turn-1").ok)
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(len(self.engine.calls), 1)

    def test_close_descriptor_error_still_settles_and_releases_job(self):
        job = self._prepare()

        class BadCloseIterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

            @property
            def close(self):
                raise RuntimeError("private iterator detail")

        self.engine.process_voice_turn_stream = lambda **_: BadCloseIterator()
        self._start()
        self.runner.run()
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(self.executor._jobs, {})
        self.assertFalse(self.executor.has_running_turn("turn-1"))

    def test_normal_generation_is_delivered_once_and_cleans_registration(self):
        self._prepare()
        self._start()
        self._start()
        self.assertEqual(len(self.runner.tasks), 1)
        self.runner.run()
        self.assertEqual(len(self.engine.calls), 1)
        self.assertTrue(self.engine.closed.is_set())
        self.assertEqual(len(self._assistant_records()), 1)
        self.assertEqual(self.executor._jobs, {})
        self.assertEqual(self.executor._event_factories, {})

    def test_model_failure_is_persisted_without_private_exception_detail(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        self.runner.run()
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        loaded = self.journal.load_events()
        failed = next(e for e in loaded.events if e.event_kind == "voice.response.failed")
        self.assertEqual(failed.payload["reason_code"], "voice_thinking_generation_failed")
        self.assertNotIn("private model detail", repr(failed))
        self.assertEqual(self.executor._jobs, {})

    def test_restart_failure_never_requests_another_model_stream(self):
        job = self._prepare()
        fresh = self._executor()
        result = fresh.fail_restarted_generations(self.host, response_ids=(job.response_id,))
        self.assertTrue(result.ok)
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.runner.tasks, [])

    def test_concurrent_close_cannot_report_settled_during_failure_confirmation(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        with patch.object(self.journal, "append_transition", return_value=VoiceHostPortResult.failed("journal_busy", retryable=True)):
            self.runner.run()
        entered, release = threading.Event(), threading.Event()
        accept = self.host.accept_event
        results, errors = [], []

        def blocked_accept(event):
            entered.set()
            if not release.wait(15):
                raise RuntimeError("test settlement barrier timeout")
            return accept(event)

        def retry():
            try:
                results.append(self.executor.start_ready_generations(self.host, voice_turn_id="turn-1"))
            except Exception as error:
                errors.append(type(error).__name__)

        with patch.object(self.host, "accept_event", side_effect=blocked_accept):
            thread = threading.Thread(target=retry, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(RuntimeError, "voice_thinking_shutdown_settlement_pending"):
                    self.executor.close()
            finally:
                release.set()
                thread.join(6)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.executor.close()
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.executor._jobs, {})
        self.assertEqual(self.runner.tasks, [])

    def test_nested_close_during_failure_dispatch_preserves_confirmation_owner(self):
        job = self._prepare()
        self.engine.fail = True
        self._start()
        accept = self.host.accept_event

        def close_during_accept(event):
            if event.event_kind == "voice.response.failed":
                with self.assertRaisesRegex(RuntimeError, "voice_thinking_shutdown_settlement_pending"):
                    self.executor.close()
            return accept(event)

        with patch.object(self.host, "accept_event", side_effect=close_during_accept):
            self.runner.run()
        self.executor.close()
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        self.assertEqual(self.executor._jobs, {})
        self.assertFalse(self.executor.has_running_turn("turn-1"))
        self.assertEqual(len(self.engine.calls), 1)

    def test_failure_confirmation_retry_wakes_original_delivery_channel(self):
        notify = Mock()
        self.executor.register_turn(voice_turn_id="turn-1", event_factory=self.factory, delivery_notifier=notify)
        job = self._prepare()
        self.engine.fail = True
        self._start()
        with patch.object(self.journal, "append_transition", return_value=VoiceHostPortResult.failed("journal_busy", retryable=True)):
            self.runner.run()
        notify.reset_mock()
        self.assertTrue(self.executor.start_ready_generations(self.host, voice_turn_id="turn-1").ok)
        self.assertEqual(self.host.snapshot.responses[job.response_id].state.value, "failed")
        notify.assert_called_once()
        self.assertEqual(len(self.engine.calls), 1)


if __name__ == "__main__":
    unittest.main()
