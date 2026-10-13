"""Real SQLite checks for host-job authority, controls and transaction boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from companion_v01.host_jobs import HostJobOwner, HostJobStore


class HostJobBoundaryTests(unittest.TestCase):
    owner = HostJobOwner("profile", "session")

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "jobs.db"
        self.store = HostJobStore(self.path, clock=lambda: 100.0)

    def create(self, *, source="tool", key="call", completion="agent", memory="timeline"):
        created = self.store.create(
            owner=self.owner,
            capability_source=source,
            capability_id="plugin.test.work",
            payload={"plugin_id": "plugin.test", "generation_id": "generation-1",
                     "parent_turn_token": "turn-1"},
            idempotency_key=key,
            argument_fingerprint="sha256:test",
            completion_mode=completion,
            memory_mode=memory,
        )
        self.assertTrue(created["ok"])
        return created["job_id"]

    def get(self, job_id):
        job = self.store.get(job_id, owner=self.owner)
        self.assertIsNotNone(job)
        return job

    def settle(self, outcome, job_id, token, **kwargs):
        if outcome == "failed":
            return self.store.fail(job_id, claim_token=token, error="worker_failed",
                                   retryable=False, **kwargs)
        if outcome == "retry":
            return self.store.fail(job_id, claim_token=token, error="worker_failed",
                                   retryable=True, **kwargs)
        if outcome == "cancelled":
            return self.store.confirm_cancelled(job_id, claim_token=token, **kwargs)
        return self.store.succeed(job_id, claim_token=token, **kwargs)

    def test_paused_withdrawal_commits_terminal_metadata_and_notification_together(self):
        for operation in ("turn", "capabilities", "job", "generation"):
            for completion, memory, expected in (("agent", "timeline", "pending"),
                                                 ("silent", "current_turn", "silent"),
                                                 ("silent", "timeline", "pending")):
                with self.subTest(operation=operation, completion=completion, memory=memory):
                    source = "plugin_task" if operation == "generation" else "tool"
                    job_id = self.create(source=source, key=f"{operation}-{completion}-{memory}",
                                         completion=completion, memory=memory)
                    event_id = self.get(job_id).completion_event_id
                    self.store.control(job_id, owner=self.owner, action="pause")
                    if operation == "turn":
                        self.store.revoke_turn(owner=self.owner, turn_token="turn-1")
                    elif operation == "capabilities":
                        self.store.revoke_capabilities(("plugin.test.work",))
                    elif operation == "job":
                        self.store.revoke_job_capability(job_id, owner=self.owner)
                    else:
                        self.store.revoke_plugin_tasks("plugin.test", "generation-1")
                    job = self.get(job_id)
                    self.assertEqual(job.status, "cancelled")
                    self.assertEqual(job.finished_at, 100.0)
                    self.assertTrue(job.cancel_requested)
                    self.assertEqual(job.control_state, "stopped")
                    self.assertEqual(job.completion_status, expected)
                    self.assertEqual(job.completion_event_id, event_id)
                    self.assertEqual(job.claim_token, "")
                    self.assertFalse(self.store.claim(job_id, worker_id="late-worker")["ok"])
                    pending = {item.job_id for item in self.store.pending_completions()}
                    self.assertEqual(job_id in pending, expected == "pending")

    def revoked_plugin_job(self):
        job_id = self.create(source="plugin_task")
        token = self.store.claim(job_id, worker_id="plugin-worker")["claim_token"]
        self.store.revoke_plugin_tasks("plugin.test", "generation-1")
        self.store.request_cancel(job_id, owner=self.owner)
        return job_id, token

    def assert_plugin_settlement_refused(self, outcome):
        job_id, token = self.revoked_plugin_job()
        before = self.get(job_id)
        result = self.settle(outcome, job_id, token, result={"late": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(self.get(job_id), before)
        self.assertEqual(self.store.pending_job_ids(), [])
        self.assertEqual(self.store.recover_abandoned_claims(), 1)
        recovered = self.get(job_id)
        self.assertEqual(recovered.status, "failed")
        self.assertEqual(recovered.recovery_state, "unknown")
        self.assertEqual(recovered.claim_token, "")
        self.assertEqual(recovered.result, {})
        self.assertEqual(recovered.completion_event_id, before.completion_event_id)

    def test_revoked_plugin_cannot_publish_success_with_its_old_claim(self):
        self.assert_plugin_settlement_refused("succeeded")

    def test_revoked_plugin_cannot_publish_failure_with_its_old_claim(self):
        self.assert_plugin_settlement_refused("failed")

    def test_revoked_plugin_cannot_requeue_with_its_old_claim(self):
        self.assert_plugin_settlement_refused("retry")

    def test_revoked_plugin_cannot_confirm_cancellation_with_its_old_claim(self):
        self.assert_plugin_settlement_refused("cancelled")

    def test_host_worker_can_report_real_outcome_after_parent_turn_withdrawal(self):
        for source in ("tool", "execution", "workflow", "subagent"):
            for outcome in ("succeeded", "failed", "cancelled", "retry"):
                with self.subTest(source=source, outcome=outcome):
                    job_id = self.create(source=source, key=f"{source}-{outcome}")
                    token = self.store.claim(job_id, worker_id="host-worker")["claim_token"]
                    self.store.revoke_turn(owner=self.owner, turn_token="turn-1")
                    before = self.get(job_id)
                    self.assertEqual(before.status, "running")
                    self.assertTrue(before.cancel_requested)
                    result = self.settle(outcome, job_id, token, result={"confirmed": True})
                    expected = "failed" if outcome == "retry" else outcome
                    self.assertTrue(result["ok"])
                    self.assertEqual(self.get(job_id).status, expected)
                    self.assertEqual(self.get(job_id).result, {"confirmed": True})

    def test_cancel_request_cannot_be_overwritten_by_pause_or_resume(self):
        job_id = self.create(source="plugin_task")
        token = self.store.claim(job_id, worker_id="worker")["claim_token"]
        self.store.request_cancel(job_id, owner=self.owner)
        before = self.get(job_id)
        for action in ("pause", "resume"):
            for cooperative in (True, False):
                with self.subTest(action=action, cooperative=cooperative):
                    result = self.store.control(job_id, owner=self.owner, action=action,
                                                running_pause=cooperative)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["status"], "cancelling")
                    self.assertEqual(self.get(job_id), before)
        confirmed = self.store.confirm_cancelled(job_id, claim_token=token)
        self.assertTrue(confirmed["ok"])
        self.assertEqual(self.get(job_id).status, "cancelled")

    def test_withdrawn_running_job_cannot_be_paused_while_cancel_is_requested(self):
        job_id = self.create()
        self.store.claim(job_id, worker_id="worker")
        self.store.revoke_turn(owner=self.owner, turn_token="turn-1")
        before = self.get(job_id)
        result = self.store.control(job_id, owner=self.owner, action="pause", running_pause=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "cancelling")
        self.assertEqual(self.get(job_id), before)

    def test_revoked_unknown_outcome_cannot_be_resumed_into_ready_work(self):
        for source in ("plugin_task", "tool"):
            with self.subTest(source=source):
                job_id = self.create(source=source, key=f"recovery-{source}")
                self.store.claim(job_id, worker_id="dead-worker")
                self.store.recover_abandoned_claims()
                if source == "plugin_task":
                    self.store.revoke_plugin_tasks("plugin.test", "generation-1")
                else:
                    self.store.revoke_job_capability(job_id, owner=self.owner)
                before = self.get(job_id)
                result = self.store.control(job_id, owner=self.owner, action="resume", running_pause=True)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "host_job_scope_revoked")
                self.assertEqual(self.get(job_id), before)
                reopened = HostJobStore(self.path, clock=lambda: 100.0)
                self.assertEqual(reopened.pending_job_ids(), [])
                self.assertEqual(reopened.get(job_id, owner=self.owner), before)

    def test_nonfinite_artifact_values_are_rejected_without_consuming_claim(self):
        for outcome in ("succeeded", "failed", "cancelled", "retry"):
            for index, value in enumerate((float("nan"), float("inf"), float("-inf"))):
                with self.subTest(outcome=outcome, number=index):
                    job_id = self.create(key=f"nonfinite-{outcome}-{index}")
                    token = self.store.claim(job_id, worker_id="worker")["claim_token"]
                    before = self.get(job_id)
                    result = self.settle(outcome, job_id, token,
                                         artifacts=({"metadata": {"number": value}},))
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["reason"], "host_job_artifacts_not_json_safe")
                    self.assertEqual(self.get(job_id), before)
                    valid = self.settle(outcome, job_id, token,
                                        artifacts=({"label": "可用产物", "number": 0.5},))
                    self.assertTrue(valid["ok"])
                    self.assertEqual(self.get(job_id).artifacts,
                                     ({"label": "可用产物", "number": 0.5},))

    def test_two_store_instances_create_one_identity_for_a_concurrent_request(self):
        second = HostJobStore(self.path, clock=lambda: 100.0)
        barrier = threading.Barrier(2, timeout=5)

        def create(store):
            barrier.wait()
            return store.create(owner=self.owner, capability_source="tool", capability_id="test",
                                payload={"query": "same"}, idempotency_key="same",
                                argument_fingerprint="sha256:same", completion_mode="agent",
                                memory_mode="timeline")

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(create, (self.store, second)))
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual({result["status"] for result in results}, {"queued", "duplicate"})
        self.assertEqual(len({result["job_id"] for result in results}), 1)
        self.assertEqual(len({self.get(result["job_id"]).completion_event_id for result in results}), 1)
        self.assertEqual(len(self.store.pending_job_ids()), 1)

    def test_two_store_instances_cannot_claim_the_same_work_twice(self):
        job_id = self.create()
        second = HostJobStore(self.path, clock=lambda: 100.0)
        barrier = threading.Barrier(2, timeout=5)

        def claim(pair):
            worker, store = pair
            barrier.wait()
            return store.claim(job_id, worker_id=worker)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(claim, (("first", self.store), ("second", second))))
        successes = [result for result in results if result["ok"]]
        self.assertEqual(len(successes), 1)
        job = self.get(job_id)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.claim_token, successes[0]["claim_token"])
        self.assertEqual(self.store.pending_job_ids(), [])

    def test_connection_setup_failure_closes_the_opened_database_handle(self):
        connection = Mock()
        connection.execute.side_effect = sqlite3.OperationalError("setup_failed")
        # The compatibility entry keeps the same sqlite3 module object.
        with patch("companion_v01.host_jobs.sqlite3.connect", return_value=connection):
            with self.assertRaises(sqlite3.OperationalError):
                with self.store._connect():
                    self.fail("setup failure must not expose a usable connection")
        connection.close.assert_called_once()

    def test_withdrawal_failure_rolls_back_all_task_state_changes(self):
        job_id = self.create()
        self.store.control(job_id, owner=self.owner, action="pause")
        before = self.get(job_id)
        with patch.object(self.store, "_row_to_job", side_effect=ValueError("row_mapping_failed")):
            with self.assertRaises(ValueError):
                self.store.revoke_job_capability(job_id, owner=self.owner)
        self.assertEqual(self.get(job_id), before)
        self.assertEqual(self.store.pending_completions(), [])


if __name__ == "__main__":
    unittest.main()
