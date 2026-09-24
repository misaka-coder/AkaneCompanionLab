from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_subagent_jobs import HostSubagentJobRuntime
from companion_v01.subagent_runtime import (
    InProcessSubagentProvider,
    SubagentProviderRegistry,
    SubagentRunResult,
)


class _HoldingBackgroundTasks:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, **kwargs):
        self.calls.append(dict(kwargs))
        return object()


class _RejectingBackgroundTasks:
    def submit(self, **_kwargs):
        raise RuntimeError("closed")


def _providers(runner) -> SubagentProviderRegistry:
    providers = SubagentProviderRegistry()
    providers.register(InProcessSubagentProvider(runner))
    return providers


class HostSubagentJobRuntimeTests(unittest.TestCase):
    owner = HostJobOwner("master", "qq_group_shared_87")

    def test_shutdown_keeps_unstarted_work_queued_for_next_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            holding = _HoldingBackgroundTasks()
            calls = []
            runtime = HostSubagentJobRuntime(store=store, background_tasks=holding,
                providers=_providers(lambda *_args, **_kwargs: calls.append(True)), provider_name="in_process")
            started = self._submit(runtime)
            runtime.request_shutdown()
            holding.calls[0]["fn"](*holding.calls[0]["args"])
            self.assertEqual(store.get(started["job_id"], owner=self.owner).status, "queued")
            self.assertEqual(calls, [])

    def test_lost_claim_stops_child_before_its_next_step(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            runtime = HostSubagentJobRuntime(store=store, background_tasks=_HoldingBackgroundTasks(),
                providers=_providers(lambda *_args, **_kwargs: None), provider_name="in_process")
            started = self._submit(runtime)
            claim = store.claim(started["job_id"], worker_id="child")
            self.assertFalse(runtime._cancel_requested(claim["job"]))
            store.recover_abandoned_claims()
            self.assertTrue(runtime._cancel_requested(claim["job"]))

    def test_failed_child_keeps_actionable_report_and_partial_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            holding = _HoldingBackgroundTasks()
            def runner(request, **kwargs):
                return SubagentRunResult(status="failed", child_session_id=request.child_session_id,
                    summary="Read the source; verification still needs the unavailable test runner.",
                    reason="test_runner_missing", artifacts=({"handle": "gen_partial", "source": "generated_file"},))
            runtime = HostSubagentJobRuntime(store=store, background_tasks=holding,
                providers=_providers(runner), provider_name="in_process")
            started = self._submit(runtime)
            holding.calls[0]["fn"](*holding.calls[0]["args"])
            job = store.get(started["job_id"], owner=self.owner)
            self.assertEqual(job.status, "failed")
            self.assertIn("verification still needs", job.result_summary)
            self.assertEqual(job.artifacts[0]["handle"], "gen_partial")
            self.assertEqual(job.last_error, "test_runner_missing")

    def _submit(self, runtime: HostSubagentJobRuntime, *, key: str = "call-1") -> dict:
        return runtime.submit(
            owner=self.owner,
            task="审计项目并返回结论。",
            label="audit",
            working_directory=str(Path(__file__).resolve().parent.parent),
            allowed_tools=("project_inspect",),
            conversation_ref="signed-parent-reference",
            character_pack_id="reimu",
            channel="qq_text",
            tool_call_id=key,
            idempotency_key=key,
        )

    def test_job_runs_once_and_duplicate_submit_keeps_child_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            calls = []
            completions = []

            def runner(request, *, cancelled):
                calls.append((request, cancelled()))
                return SubagentRunResult(
                    status="succeeded",
                    child_session_id=request.child_session_id,
                    summary="审计完成。",
                    artifacts=({"handle": "gen_audit", "source": "generated_file"},),
                )

            def publish(job):
                completions.append(job)
                return store.mark_completion_delivered(
                    job.job_id,
                    completion_event_id=job.completion_event_id,
                )

            background = BackgroundTaskRunner({"subagents": 1})
            self.addCleanup(background.close)
            runtime = HostSubagentJobRuntime(
                store=store,
                providers=_providers(runner),
                provider_name="in_process",
                background_tasks=background,
                completion_publisher=publish,
            )

            first = self._submit(runtime)
            self.assertTrue(background.wait_idle(lane="subagents", timeout=2.0))
            duplicate = self._submit(runtime)

            self.assertTrue(first["ok"])
            self.assertTrue(duplicate["ok"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["job_id"], first["job_id"])
            self.assertEqual(duplicate["child_session_id"], first["child_session_id"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(completions), 1)
            job = store.get(first["job_id"], owner=self.owner)
            self.assertEqual(job.status, "succeeded")
            self.assertEqual(job.result_summary, "审计完成。")
            self.assertEqual(job.completion_status, "delivered")

    def test_provider_validation_happens_before_job_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            runtime = HostSubagentJobRuntime(
                store=store,
                providers=SubagentProviderRegistry(),
                provider_name="in_process",
                background_tasks=_HoldingBackgroundTasks(),
            )

            result = self._submit(runtime)

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "subagent_provider_unavailable")
            self.assertEqual(store.pending_job_ids(capability_source="subagent"), [])

    def test_scheduler_failure_is_terminal_without_later_duplicate_notification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            runtime = HostSubagentJobRuntime(
                store=store,
                providers=_providers(lambda *_args, **_kwargs: None),
                provider_name="in_process",
                background_tasks=_RejectingBackgroundTasks(),
            )

            result = self._submit(runtime)

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "subagent_scheduler_failed")
            job = store.get(result["job_id"], owner=self.owner)
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.completion_status, "delivered")

    def test_recovered_started_child_fails_without_rerunning_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            holding = _HoldingBackgroundTasks()
            first = HostSubagentJobRuntime(
                store=store,
                providers=_providers(lambda *_args, **_kwargs: None),
                provider_name="in_process",
                background_tasks=holding,
            )
            started = self._submit(first)
            store.claim(started["job_id"], worker_id="dead-worker")
            self.assertEqual(store.recover_abandoned_claims(), 1)

            calls = []
            completions = []

            def runner(*_args, **_kwargs):
                calls.append(True)
                raise AssertionError("recovered child must not restart")

            def publish(job):
                completions.append(job)
                return store.mark_completion_delivered(
                    job.job_id,
                    completion_event_id=job.completion_event_id,
                )

            background = BackgroundTaskRunner({"subagents": 1})
            self.addCleanup(background.close)
            restarted = HostSubagentJobRuntime(
                store=store,
                providers=_providers(runner),
                provider_name="in_process",
                background_tasks=background,
                completion_publisher=publish,
            )

            self.assertEqual(restarted.recover(), 0)
            self.assertTrue(background.wait_idle(lane="subagents", timeout=2.0))
            job = store.get(started["job_id"], owner=self.owner)
            self.assertEqual(calls, [])
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.last_error, "host_restart_outcome_unknown")
            self.assertEqual(completions, [])
            # Shared host completion dispatch owns recovery for every source.
            self.assertEqual([item.job_id for item in store.pending_completions()], [job.job_id])

    def test_running_child_observes_owner_scoped_cancel_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            entered = threading.Event()

            def runner(request, *, cancelled):
                entered.set()
                deadline = time.monotonic() + 2.0
                while not cancelled() and time.monotonic() < deadline:
                    time.sleep(0.01)
                return SubagentRunResult(
                    status="cancelled",
                    child_session_id=request.child_session_id,
                    reason="subagent_cancelled",
                )

            background = BackgroundTaskRunner({"subagents": 1})
            self.addCleanup(background.close)
            runtime = HostSubagentJobRuntime(
                store=store,
                providers=_providers(runner),
                provider_name="in_process",
                background_tasks=background,
            )
            started = self._submit(runtime)
            self.assertTrue(entered.wait(timeout=1.0))

            cancelled = store.request_cancel(started["job_id"], owner=self.owner)

            self.assertEqual(cancelled["status"], "cancelling")
            self.assertTrue(background.wait_idle(lane="subagents", timeout=2.0))
            job = store.get(started["job_id"], owner=self.owner)
            self.assertEqual(job.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
