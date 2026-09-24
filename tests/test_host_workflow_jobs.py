from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_workflow_jobs import HostWorkflowJobRuntime, WorkflowJobAssetStore
from companion_v01.local_workflow_execution import WorkflowExecutionAsset


class _HoldingBackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def submit(self, **kwargs):
        self.calls.append(dict(kwargs))
        return object()


class _RejectingBackgroundTasks:
    def submit(self, **_kwargs):
        raise RuntimeError("closed")


class _WorkflowRunner:
    def __init__(self) -> None:
        self.requests = []

    def execute_workflow(self, request):
        self.requests.append(request)
        return {"ok": True, "status": "completed", "reason": "done", "outputs": []}


def _preflight() -> dict:
    return {
        "workflowId": "workflow.test",
        "capabilityId": "image.generate",
        "acceptedInputs": {
            "inputImageHandle": "source",
            "outputImageHandle": "result",
        },
        "checks": {},
        "workflow": {"nodes": {}},
    }


class HostWorkflowJobRuntimeTests(unittest.TestCase):
    owner = HostJobOwner("profile", "session")

    def test_resource_limit_prevents_runner_and_reaches_public_job(self):
        from companion_v01.execution_resource_policy import ExecutionResourcePolicy
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            assets = WorkflowJobAssetStore(Path(temp_dir) / "assets")
            holding = _HoldingBackgroundTasks()
            runner = _WorkflowRunner()
            runtime = HostWorkflowJobRuntime(store=store, asset_store=assets, workflow_runner=runner,
                background_tasks=holding, executor_broker=ExecutorBroker(None,
                    resource_policy=ExecutionResourcePolicy.from_config({"max_input_bytes": 8})))
            source = WorkflowExecutionAsset("source", b"source-bytes", "image/png")
            started = runtime.start(preflight=_preflight(), profile_user_id=self.owner.profile_user_id,
                session_id=self.owner.session_id, input_assets={source.handle: source})
            scheduled, = holding.calls
            scheduled["fn"](*scheduled["args"])
            job = runtime.get(started["jobId"], owner=self.owner)
            public = runtime.public(job)
            self.assertEqual(job.status, "failed")
            self.assertEqual(public["reason"], "execution_resource_limit_exceeded")
            self.assertEqual(public["resourceLimit"]["budget"], "max_input_bytes")
            self.assertEqual(public["resourceLimit"]["execution_status"], "not_started")
            self.assertEqual(runner.requests, [])
            self.assertIsNone(assets.read(job.job_id, "input", "source"))

    def test_scheduler_failure_settles_job_and_removes_persisted_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            assets = WorkflowJobAssetStore(Path(temp_dir) / "assets")
            runtime = HostWorkflowJobRuntime(
                store=store,
                asset_store=assets,
                workflow_runner=_WorkflowRunner(),
                background_tasks=_RejectingBackgroundTasks(),
                executor_broker=ExecutorBroker(None),
            )
            source = WorkflowExecutionAsset("source", b"source-bytes", "image/png")

            started = runtime.start(
                preflight=_preflight(),
                profile_user_id=self.owner.profile_user_id,
                session_id=self.owner.session_id,
                input_assets={source.handle: source},
            )

            self.assertFalse(started["ok"])
            self.assertEqual(started["status"], "failed")
            self.assertEqual(started["reason"], "workflow_scheduler_failed")
            job = store.get(started["jobId"], owner=self.owner)
            self.assertEqual(job.status, "failed")
            self.assertIsNone(assets.read(job.job_id, "input", "source"))

    def test_queued_job_and_input_resume_with_a_new_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            assets = WorkflowJobAssetStore(Path(temp_dir) / "assets")
            holding = _HoldingBackgroundTasks()
            source = WorkflowExecutionAsset("source", b"source-bytes", "image/png")
            first = HostWorkflowJobRuntime(
                store=store,
                asset_store=assets,
                workflow_runner=_WorkflowRunner(),
                background_tasks=holding,
                executor_broker=ExecutorBroker(None),
            )
            started = first.start(
                preflight=_preflight(),
                profile_user_id=self.owner.profile_user_id,
                session_id=self.owner.session_id,
                input_assets={source.handle: source},
            )
            self.assertEqual(started["jobStatus"], "queued")
            self.assertEqual(len(holding.calls), 1)

            runner = _WorkflowRunner()
            background = BackgroundTaskRunner({"workflow": 1})
            self.addCleanup(background.close)
            restarted = HostWorkflowJobRuntime(
                store=HostJobStore(Path(temp_dir) / "jobs.db"),
                asset_store=WorkflowJobAssetStore(Path(temp_dir) / "assets"),
                workflow_runner=runner,
                background_tasks=background,
                executor_broker=ExecutorBroker(None),
            )

            self.assertEqual(restarted.recover(), 1)
            self.assertTrue(background.wait_idle(lane="workflow", timeout=2.0))
            job = restarted.get(started["jobId"], owner=self.owner)
            self.assertEqual(job.status, "succeeded")
            self.assertEqual(len(runner.requests), 1)
            self.assertEqual(runner.requests[0].input_assets["source"].data, b"source-bytes")
            self.assertIsNone(assets.read(job.job_id, "input", "source"))

    def test_asset_store_rejects_noncanonical_job_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets = WorkflowJobAssetStore(Path(temp_dir) / "assets")
            asset = WorkflowExecutionAsset("source", b"data", "image/png")

            with self.assertRaisesRegex(ValueError, "workflow_job_asset_identity_invalid"):
                assets.write("job_../../escape", "input", asset)


if __name__ == "__main__":
    unittest.main()
