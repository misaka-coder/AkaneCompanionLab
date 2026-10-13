"""Failure boundaries exercised through real job storage and worker queues."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.tool_handlers.core import ToolExecutionResult
from tests.test_host_tool_jobs import _Engine, _context


class _Handler:
    tool_type = "generate_image"

    def __init__(self, factory, *, lease=0):
        self.factory = factory
        self.lease = lease

    def retain_invocation(self):
        self.factory.retains += 1
        if self.factory.retains == self.factory.fail_retain:
            raise RuntimeError("private_retention_details")
        return _Handler(self.factory, lease=self.factory.retains)

    def release_invocation(self):
        self.factory.releases.append(self.lease)
        if self.lease in self.factory.fail_release:
            raise RuntimeError("private_release_details")

    def execute(self, *, call, context):
        self.factory.executions.append(dict(call))
        self.factory.started.set()
        self.factory.allow_finish.wait(3)
        return ToolExecutionResult(tool_type=self.tool_type, followup_context="work finished")


class _Factory:
    def __init__(self):
        self.retains = 0
        self.releases = []
        self.executions = []
        self.fail_retain = 0
        self.fail_release = set()
        self.started = threading.Event()
        self.allow_finish = threading.Event()
        self.allow_finish.set()
        self.handler = _Handler(self)


class HostToolJobBoundaryTests(unittest.TestCase):
    owner = HostJobOwner("profile-a", "session-a")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = HostJobStore(Path(temporary.name) / "jobs.db")
        self.factory = _Factory()
        self.runner = BackgroundTaskRunner({"host-jobs": 1, "host-job-completions": 1})
        self.addCleanup(self.runner.close, timeout=3)
        self.addCleanup(self.factory.allow_finish.set)
        self.completions = []
        self.runtime = HostToolJobRuntime(
            engine=_Engine(self.factory.handler), store=self.store, background_tasks=self.runner,
            conversation_ref_issuer=lambda context: "conversation-ref",
            terminal_callback=lambda job: self.completions.append(job) or True,
        )

    def submit(self, *, call=None, key="call-a"):
        return self.runtime.submit(capability_id="generate_image", invocation_id=key,
                                   call=call if call is not None else {"type": "generate_image", "prompt": "moon"},
                                   context=_context())

    def drain(self):
        self.assertTrue(self.runner.wait_idle(lane="host-jobs", timeout=3))
        self.assertTrue(self.runner.wait_idle(lane="host-job-completions", timeout=3))

    def get(self, job_id):
        job = self.store.get(job_id, owner=self.owner)
        self.assertIsNotNone(job)
        return job

    def assert_registered_failure(self, result, *, status):
        event = result.stream_events[0]
        self.assertEqual(event["type"], "background_job_rejected")
        job_id = event["job_id"]
        self.assertEqual(event["job_status"], status)
        self.assertEqual(result.state_updates["capability_execution"]["job_id"], job_id)
        self.assertEqual(result.state_updates["capability_execution"]["job_status"], status)
        self.assertIn(job_id, result.followup_context)
        self.assertNotIn("没有成功登记", result.followup_context)
        self.assertNotIn("因此尚未开始", result.followup_context)
        return job_id

    def test_caller_release_failure_does_not_hide_an_accepted_job(self):
        self.factory.fail_release = {1}
        with self.assertLogs("akane.host_tool_jobs", level="ERROR") as captured:
            result = self.submit()
            self.drain()
        self.assertEqual(result.stream_events[0]["type"], "background_job_accepted")
        job_id = result.stream_events[0]["job_id"]
        self.assertEqual(self.get(job_id).status, "succeeded")
        self.assertEqual(self.get(job_id).completion_status, "delivered")
        self.assertEqual(len(self.factory.executions), 1)
        self.assertEqual(sorted(self.factory.releases), [1, 2])
        self.assertNotIn("private_release_details", " ".join(captured.output))

    def test_worker_release_failure_does_not_skip_terminal_notification(self):
        self.factory.fail_release = {2}
        with self.assertLogs("akane", level="ERROR") as captured:
            result = self.submit()
            self.drain()
        job_id = result.stream_events[0]["job_id"]
        self.assertEqual(self.get(job_id).status, "succeeded")
        self.assertEqual(self.get(job_id).completion_status, "delivered")
        self.assertEqual([job.job_id for job in self.completions], [job_id])
        self.assertEqual(self.runtime._scheduled, set())
        self.assertEqual(self.runtime._admitted_handlers, {})
        self.assertEqual(self.runtime._completion_scheduled, set())
        self.assertNotIn("private_release_details", " ".join(captured.output))

    def test_scheduler_rejection_reports_the_persisted_failure_and_does_not_replay(self):
        self.runner.close()
        result = self.submit()
        job_id = self.assert_registered_failure(result, status="failed")
        job = self.get(job_id)
        self.assertEqual(job.last_error, "host_job_schedule_RuntimeError")
        self.assertEqual(job.completion_status, "delivered")
        self.assertEqual(self.factory.executions, [])
        self.assertEqual(self.runtime._scheduled, set())
        self.assertEqual(self.runtime._admitted_handlers, {})
        self.runtime.background_tasks = BackgroundTaskRunner()
        self.addCleanup(self.runtime.background_tasks.close, timeout=3)
        self.assertEqual(self.runtime.recover(), 0)
        self.assertEqual(self.completions, [])
        self.assertEqual(self.store.pending_job_ids(), [])

    def test_release_failure_during_scheduler_rejection_still_settles_the_job(self):
        self.runner.close()
        self.factory.fail_release = {1, 2}
        with self.assertLogs("akane.host_tool_jobs", level="ERROR"):
            result = self.submit()
        job_id = self.assert_registered_failure(result, status="failed")
        self.assertEqual(self.get(job_id).completion_status, "delivered")
        self.assertEqual(sorted(self.factory.releases), [1, 2])
        self.assertEqual(self.store.pending_job_ids(), [])

    def test_worker_retention_failure_after_registration_is_terminal_and_observable(self):
        self.factory.fail_retain = 2
        result = self.submit()
        job_id = self.assert_registered_failure(result, status="failed")
        self.assertEqual(self.get(job_id).last_error, "long_tool_handler_retention_failed")
        self.assertEqual(self.get(job_id).completion_status, "delivered")
        self.assertEqual(self.factory.releases, [1])
        self.assertEqual(self.factory.executions, [])
        self.assertEqual(self.runtime._admitted_handlers, {})
        self.assertEqual(self.store.pending_job_ids(), [])
        self.assertNotIn("private_retention_details", result.followup_context)

    def test_initial_retention_failure_creates_no_job(self):
        self.factory.fail_retain = 1
        with self.assertLogs("akane.host_tool_jobs", level="ERROR") as captured:
            result = self.submit()
        self.assertEqual(result.stream_events[0]["type"], "background_job_rejected")
        self.assertEqual(result.stream_events[0]["reason"], "long_tool_handler_resolution_failed")
        self.assertEqual(self.store.pending_job_ids(), [])
        self.assertEqual(self.factory.executions, [])
        self.assertNotIn("private_retention_details", " ".join(captured.output))

    def test_duplicate_retention_failure_does_not_stop_the_existing_queued_worker(self):
        gate_entered, gate_release = threading.Event(), threading.Event()
        self.addCleanup(gate_release.set)

        def gate():
            gate_entered.set()
            gate_release.wait(3)

        self.runner.submit(lane="host-jobs", name="gate", fn=gate)
        self.assertTrue(gate_entered.wait(2))
        first = self.submit()
        job_id = first.stream_events[0]["job_id"]
        before = self.get(job_id)
        self.assertEqual(before.status, "queued")
        admitted = self.runtime._admitted_handlers[job_id]
        self.factory.fail_retain = 4
        duplicate = self.submit()
        self.assertEqual(self.assert_registered_failure(duplicate, status="queued"), job_id)
        self.assertEqual(self.get(job_id), before)
        self.assertIs(self.runtime._admitted_handlers[job_id], admitted)
        gate_release.set()
        self.drain()
        self.assertEqual(self.get(job_id).status, "succeeded")
        self.assertEqual(self.get(job_id).completion_status, "delivered")
        self.assertEqual(len(self.factory.executions), 1)
        self.assertEqual(sorted(self.factory.releases), [1, 2, 3])

    def test_scheduler_failure_does_not_rewrite_another_claimants_running_job(self):
        claimed = []
        store = self.store

        class RacingScheduler:
            def submit(self, *, lane, name, fn, args):
                claimed.append(store.claim(args[0], worker_id="other-worker"))
                raise RuntimeError("queue_acknowledgement_failed")

        self.runtime.background_tasks = RacingScheduler()
        result = self.submit()
        job_id = self.assert_registered_failure(result, status="running")
        job = self.get(job_id)
        self.assertEqual(job.claim_token, claimed[0]["claim_token"])
        self.assertEqual(job.claimed_by, "other-worker")
        self.assertEqual(job.completion_status, "waiting")
        self.assertEqual(job.last_error, "")
        self.assertTrue(self.store.succeed(job_id, claim_token=claimed[0]["claim_token"])["ok"])
        self.assertEqual(self.get(job_id).status, "succeeded")

    def test_non_json_call_is_rejected_before_persisting_or_dispatching(self):
        for value in (object(), {"nested": object()}):
            with self.subTest(container=isinstance(value, dict)):
                result = self.submit(call={"type": "generate_image", "input": value})
                self.assertEqual(result.stream_events[0]["type"], "background_job_rejected")
                self.assertEqual(result.stream_events[0]["reason"], "host_job_payload_not_json_safe")
                self.assertEqual(self.store.pending_job_ids(), [])
        self.assertEqual(self.factory.executions, [])
        self.assertEqual(self.factory.releases, [1, 2])

    def test_cyclic_call_is_rejected_before_persisting_or_dispatching(self):
        value = {}
        value["self"] = value
        result = self.submit(call={"type": "generate_image", "input": value})
        self.assertEqual(result.stream_events[0]["type"], "background_job_rejected")
        self.assertEqual(result.stream_events[0]["reason"], "host_job_payload_not_json_safe")
        self.assertEqual(self.store.pending_job_ids(), [])
        self.assertEqual(self.factory.releases, [1])
        self.assertEqual(self.factory.executions, [])

    def test_failed_claim_during_rejection_preserves_the_existing_queued_state(self):
        self.runner.close()
        with patch.object(self.store, "claim", side_effect=sqlite3.OperationalError("private_database_details")):
            with self.assertLogs("akane.host_tool_jobs", level="ERROR") as captured:
                result = self.submit()
        job_id = self.assert_registered_failure(result, status="queued")
        self.assertEqual(self.get(job_id).completion_status, "waiting")
        self.assertEqual(self.get(job_id).last_error, "")
        self.assertEqual(self.factory.executions, [])
        self.assertEqual(sorted(self.factory.releases), [1, 2])
        self.assertNotIn("private_database_details", " ".join(captured.output))

    def test_failed_terminal_write_does_not_claim_that_the_job_was_settled(self):
        self.runner.close()
        with patch.object(self.store, "fail", side_effect=sqlite3.OperationalError("private_database_details")):
            with self.assertLogs("akane.host_tool_jobs", level="ERROR") as captured:
                result = self.submit()
        job_id = self.assert_registered_failure(result, status="running")
        self.assertEqual(self.get(job_id).completion_status, "waiting")
        self.assertEqual(self.get(job_id).claimed_by, "scheduler-failure")
        self.assertEqual(self.factory.executions, [])
        self.assertEqual(self.store.recover_abandoned_claims(), 1)
        self.assertEqual(self.get(job_id).recovery_state, "unknown")
        self.assertEqual(self.store.pending_job_ids(), [])
        self.assertNotIn("private_database_details", " ".join(captured.output))

    def test_failed_state_read_reports_unknown_and_keeps_the_registration_identity(self):
        self.runner.close()
        with patch.object(self.store, "get", side_effect=sqlite3.OperationalError("private_database_details")):
            with self.assertLogs("akane.host_tool_jobs", level="ERROR") as captured:
                result = self.submit()
        job_id = self.assert_registered_failure(result, status="unknown")
        self.assertEqual(self.get(job_id).status, "failed")
        self.assertEqual(self.get(job_id).completion_status, "pending")
        self.assertEqual(self.factory.executions, [])
        self.assertEqual(sorted(self.factory.releases), [1, 2])
        self.assertNotIn("private_database_details", " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()
