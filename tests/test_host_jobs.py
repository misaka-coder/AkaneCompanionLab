from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.store import MemoryStore


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class HostJobStoreTests(unittest.TestCase):
    owner = HostJobOwner("profile", "session")

    def _create(self, store: HostJobStore, *, key: str = "call-1", payload: dict | None = None) -> dict:
        return store.create(
            owner=self.owner,
            capability_source="builtin",
            capability_id="generate_image",
            payload=payload or {"prompt": "一只猫"},
            idempotency_key=key,
            argument_fingerprint="sha256:abc",
            character_pack_id="reimu",
            channel="qq",
            delivery_target="group:87",
            turn_id="turn-1",
            tool_call_id="tool-1",
            completion_mode="agent",
            memory_mode="timeline",
        )

    def test_job_survives_reopen_in_existing_instance_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = MemoryStore(Path(temp_dir))
            first = HostJobStore(memory.db_path)
            created = self._create(first)

            reopened = HostJobStore(memory.db_path)
            job = reopened.get(created["job_id"], owner=self.owner)

            self.assertEqual(job.status, "queued")
            self.assertEqual(job.payload, {"prompt": "一只猫"})
            self.assertEqual(job.character_pack_id, "reimu")
            self.assertEqual(job.completion_event_id, created["completion_event_id"])
            self.assertEqual(memory.ensure_session(
                profile_user_id="profile",
                session_id="session",
                character_pack_id="reimu",
            )["session_id"], "session")

    def test_idempotency_returns_same_job_and_rejects_changed_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            first = self._create(store)
            duplicate = self._create(store)
            collision = self._create(store, payload={"prompt": "另一张图"})

            self.assertEqual(duplicate["status"], "duplicate")
            self.assertEqual(duplicate["job_id"], first["job_id"])
            self.assertEqual(collision["status"], "collision")
            self.assertEqual(collision["job_id"], first["job_id"])

    def test_expired_claim_is_not_replayed_and_stale_worker_is_fenced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = _Clock()
            store = HostJobStore(Path(temp_dir) / "jobs.db", clock=clock)
            created = self._create(store)
            first = store.claim_next(worker_id="worker-a", lease_seconds=10)

            clock.value = 111.0
            second = store.claim_next(worker_id="worker-b", lease_seconds=10)

            self.assertEqual(second["status"], "idle")
            self.assertEqual(
                store.succeed(created["job_id"], claim_token=first["claim_token"])["status"],
                "stale",
            )
            completed = store.get(created["job_id"], owner=self.owner)
            self.assertEqual(completed.status, "failed")
            self.assertEqual(completed.attempts, 1)
            self.assertEqual(completed.last_error, "lease_expired_outcome_unknown")
            self.assertIn("outcome is unknown", completed.result_summary)
            self.assertEqual(completed.completion_status, "pending")

            pending = store.pending_completions()
            self.assertEqual([item.job_id for item in pending], [created["job_id"]])
            delivered = store.mark_completion_delivered(
                created["job_id"],
                completion_event_id=completed.completion_event_id,
            )
            self.assertTrue(delivered["ok"])
            self.assertEqual(store.pending_completions(), [])

    def test_exact_claim_and_pending_list_do_not_take_another_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            first = self._create(store, key="call-1")
            second = self._create(store, key="call-2")

            claimed = store.claim(second["job_id"], worker_id="worker-b")

            self.assertEqual(claimed["job"].job_id, second["job_id"])
            self.assertEqual(store.pending_job_ids(), [first["job_id"]])

    def test_retry_delay_prevents_early_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = _Clock()
            store = HostJobStore(Path(temp_dir) / "jobs.db", clock=clock)
            created = self._create(store)
            claimed = store.claim_next(worker_id="worker-a")

            failed = store.fail(
                created["job_id"],
                claim_token=claimed["claim_token"],
                error="temporary",
                retryable=True,
                retry_delay_seconds=5,
            )
            self.assertEqual(failed["status"], "queued")
            self.assertEqual(store.claim_next(worker_id="worker-b")["status"], "idle")
            clock.value = 105.0
            self.assertTrue(store.claim_next(worker_id="worker-b")["ok"])

    def test_cancel_is_owner_scoped_and_cooperative_for_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = self._create(store)
            self.assertEqual(
                store.request_cancel(created["job_id"], owner=HostJobOwner("other", "session"))["status"],
                "unknown",
            )
            claimed = store.claim_next(worker_id="worker-a")
            self.assertEqual(store.request_cancel(created["job_id"], owner=self.owner)["status"], "cancelling")
            self.assertEqual(
                store.confirm_cancelled(created["job_id"], claim_token=claimed["claim_token"])["status"],
                "cancelled",
            )

    def test_queued_job_pause_resume_preserves_identity_and_stop_cannot_revive_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = self._create(store)
            paused = store.control(created["job_id"], owner=self.owner, action="pause")
            self.assertEqual(paused["status"], "paused")
            paused_job = store.get(created["job_id"], owner=self.owner)
            self.assertEqual(paused_job.status, "paused")
            self.assertEqual(store.pending_job_ids(), [])

            resumed = store.control(created["job_id"], owner=self.owner, action="resume")
            self.assertEqual(resumed["status"], "queued")
            self.assertEqual(store.get(created["job_id"], owner=self.owner).job_id, created["job_id"])
            self.assertEqual(store.pending_job_ids(), [created["job_id"]])

            store.control(created["job_id"], owner=self.owner, action="pause")
            stopped = store.control(created["job_id"], owner=self.owner, action="stop")
            self.assertEqual(stopped["status"], "cancelled")
            self.assertEqual(store.control(created["job_id"], owner=self.owner, action="resume")["status"], "cancelled")
            self.assertEqual(store.pending_job_ids(), [])

    def test_running_pause_requires_cooperative_boundary_and_fences_late_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = self._create(store)
            claimed = store.claim(created["job_id"], worker_id="cooperative-worker")
            token = claimed["claim_token"]

            unavailable = store.control(created["job_id"], owner=self.owner, action="pause")
            self.assertEqual(unavailable["status"], "pause_unavailable")
            self.assertEqual(store.get(created["job_id"], owner=self.owner).control_state, "running")

            paused = store.control(
                created["job_id"], owner=self.owner, action="pause", running_pause=True
            )
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(store.succeed(created["job_id"], claim_token=token)["status"], "stale")
            paused_job = store.get(created["job_id"], owner=self.owner)
            self.assertEqual(paused_job.status, "running")
            self.assertEqual(paused_job.control_state, "paused")

            resumed = store.control(created["job_id"], owner=self.owner, action="resume", running_pause=True)
            self.assertEqual(resumed["status"], "running")
            self.assertEqual(store.succeed(created["job_id"], claim_token=token)["status"], "succeeded")

    def test_restart_fails_unconfirmed_work_and_preserves_completion_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            first = HostJobStore(path)
            created = self._create(first)
            claimed = first.claim_next(worker_id="dead-worker")
            event_id = claimed["job"].completion_event_id

            restarted = HostJobStore(path)
            self.assertEqual(restarted.recover_abandoned_claims(), 1)
            recovered = restarted.claim_next(worker_id="new-worker")

            self.assertEqual(recovered["status"], "idle")
            job = restarted.get(created["job_id"], owner=self.owner)
            self.assertEqual(job.completion_event_id, event_id)
            self.assertEqual(job.attempts, 1)
            self.assertEqual(job.last_error, "host_restart_outcome_unknown")
            self.assertEqual(restarted.recover_abandoned_claims(), 0)

    def test_cancel_request_does_not_turn_failure_or_lost_worker_into_cancelled(self) -> None:
        for outcome in ("failed", "retryable", "restart", "expiry", "succeeded"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp_dir:
                clock = _Clock()
                store = HostJobStore(Path(temp_dir) / "jobs.db", clock=clock)
                created = self._create(store)
                claimed = store.claim_next(worker_id="worker", lease_seconds=10)
                store.request_cancel(created["job_id"], owner=self.owner)
                if outcome == "restart":
                    store.recover_abandoned_claims()
                elif outcome == "expiry":
                    clock.value += 11
                    store.pending_job_ids()
                elif outcome == "succeeded":
                    store.succeed(created["job_id"], claim_token=claimed["claim_token"])
                else:
                    store.fail(created["job_id"], claim_token=claimed["claim_token"],
                               error="worker_failed", retryable=outcome == "retryable")
                job = store.get(created["job_id"], owner=self.owner)
                self.assertEqual(job.status, "succeeded" if outcome == "succeeded" else "failed")
                self.assertEqual(job.completion_status, "pending")
                self.assertEqual(store.pending_job_ids(), [])

    def test_restart_preserves_unstarted_jobs_and_silent_policy_across_sources(self) -> None:
        for source in ("tool", "workflow", "execution", "subagent"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp_dir:
                store = HostJobStore(Path(temp_dir) / "jobs.db")
                queued = self._create(store, key="unstarted")
                running = store.create(
                    owner=self.owner, capability_source=source, capability_id="test",
                    payload={}, idempotency_key="running", argument_fingerprint="test",
                    completion_mode="silent",
                    memory_mode="current_turn",
                )
                store.claim(running["job_id"], worker_id="worker")
                store.recover_abandoned_claims()
                self.assertEqual(store.pending_job_ids(), [queued["job_id"]])
                job = store.get(running["job_id"], owner=self.owner)
                self.assertEqual(job.status, "failed")
                self.assertEqual(job.completion_status, "silent")

    def test_silent_running_job_can_arm_agent_completion_after_terminal_race(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = store.create(
                owner=self.owner,
                capability_source="execution",
                capability_id="exec_run",
                payload={"run_id": "execrun_test"},
                idempotency_key="execrun_test",
                argument_fingerprint="sha256:test",
                completion_mode="silent",
                memory_mode="current_turn",
            )
            claimed = store.claim(created["job_id"], worker_id="execution")
            store.succeed(
                created["job_id"],
                claim_token=claimed["claim_token"],
                result_summary="done",
            )
            silent = store.get(created["job_id"], owner=self.owner)
            self.assertEqual(silent.completion_status, "silent")

            armed = store.arm_agent_completion(created["job_id"], owner=self.owner)

            self.assertTrue(armed["ok"])
            pending = store.get(created["job_id"], owner=self.owner)
            self.assertEqual(pending.completion_mode, "agent")
            self.assertEqual(pending.memory_mode, "timeline")
            self.assertEqual(pending.completion_status, "pending")


if __name__ == "__main__":
    unittest.main()
