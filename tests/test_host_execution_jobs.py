from __future__ import annotations

import tempfile
import threading
import os
import shlex
import sys
import unittest
from pathlib import Path

from companion_v01.client_protocol import ClientMode
from companion_v01.execution_run import (
    ExecutionRunOwner,
    ExecutionRunTerminalEvent,
    new_run_id,
)
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.host_execution_jobs import HostExecutionJobRuntime
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.execution import ExecRunToolHandler


def _sleep_command(seconds: float) -> str:
    code = f"import time; time.sleep({seconds})"
    if os.name == "nt":
        executable = str(sys.executable).replace("'", "''")
        return f"& '{executable}' -c '{code}'"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="profile-a",
        session_id="session-a",
        now_ts=123,
        visual_payload={},
        character_pack_id="reimu",
        current_user_source_id="message-a",
        client_mode=ClientMode.QQ_TEXT.value,
    )


class HostExecutionJobRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = HostJobStore(Path(self.temp.name) / "jobs.db")
        self.owner = ExecutionRunOwner("profile-a", "session-a", "local")
        self.job_owner = HostJobOwner("profile-a", "session-a")
        self.published = []
        self.runtime = HostExecutionJobRuntime(
            store=self.store,
            conversation_ref_issuer=lambda _context: "signed-conversation-ref",
            completion_publisher=lambda job: self.published.append(job),
        )

    def _begin(self, run_id: str) -> dict:
        return self.runtime.begin(
            run_id=run_id,
            run_owner=self.owner,
            context=_context(),
            argument_fingerprint="sha256:command",
        )

    def _terminal(self, run_id: str, *, status: str = "completed") -> ExecutionRunTerminalEvent:
        return ExecutionRunTerminalEvent(
            run_id=run_id,
            owner=self.owner,
            status=status,
            exit_code=0 if status == "completed" else 1,
            reason="",
            finished_at=130.0,
        )

    def test_short_command_is_durable_but_does_not_publish_duplicate_completion(self) -> None:
        run_id = new_run_id()
        started = self._begin(run_id)

        self.runtime.observe_terminal(self._terminal(run_id))
        self.runtime.observe_start(run_id, status="completed", exit_code=0, reason="")

        job = self.store.get(started["job_id"], owner=self.job_owner)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.completion_status, "silent")
        self.assertEqual(job.payload, {"run_id": run_id})
        self.assertEqual(self.published, [])

    def test_running_command_publishes_one_agent_completion(self) -> None:
        run_id = new_run_id()
        started = self._begin(run_id)
        self.runtime.observe_start(run_id, status="running", exit_code=None, reason="")

        self.runtime.observe_terminal(self._terminal(run_id))
        self.runtime.observe_terminal(self._terminal(run_id))

        job = self.store.get(started["job_id"], owner=self.job_owner)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.completion_status, "pending")
        self.assertEqual([item.job_id for item in self.published], [started["job_id"]])
        self.assertIn(run_id, job.result_summary)
        self.assertIn("exec_status", job.result_summary)

    def test_terminal_race_is_armed_after_exec_run_observes_running(self) -> None:
        run_id = new_run_id()
        started = self._begin(run_id)

        self.runtime.observe_terminal(self._terminal(run_id))
        self.runtime.observe_start(run_id, status="running", exit_code=None, reason="")

        job = self.store.get(started["job_id"], owner=self.job_owner)
        self.assertEqual(job.completion_status, "pending")
        self.assertEqual([item.job_id for item in self.published], [started["job_id"]])

    def test_restart_fails_orphaned_process_instead_of_faking_recovery(self) -> None:
        run_id = new_run_id()
        started = self._begin(run_id)
        self.runtime.observe_start(run_id, status="running", exit_code=None, reason="")
        self.assertEqual(self.store.recover_abandoned_claims(), 1)

        recovered = HostExecutionJobRuntime(
            store=self.store,
            conversation_ref_issuer=lambda _context: "signed-conversation-ref",
        ).recover()

        self.assertEqual(recovered, 0)
        job = self.store.get(started["job_id"], owner=self.job_owner)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.completion_status, "pending")
        self.assertEqual(job.last_error, "host_restart_outcome_unknown")

    def test_missing_conversation_context_prevents_process_admission(self) -> None:
        runtime = HostExecutionJobRuntime(
            store=self.store,
            conversation_ref_issuer=lambda _context: "",
        )
        result = runtime.begin(
            run_id=new_run_id(),
            run_owner=self.owner,
            context=_context(),
            argument_fingerprint="sha256:command",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "job_conversation_context_unavailable")

    def test_real_executor_running_result_reaches_shared_completion_publisher(self) -> None:
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        provider = TrustedLocalExecutor(
            workspace_root=workspace,
            run_log_dir=Path(self.temp.name) / "runlogs",
        )
        published = []
        completed = threading.Event()
        runtime = HostExecutionJobRuntime(
            store=self.store,
            conversation_ref_issuer=lambda _context: "signed-conversation-ref",
            completion_publisher=lambda job: (published.append(job), completed.set()),
        )
        runtime.bind_provider(provider)
        handler = ExecRunToolHandler(execution_provider=provider)
        handler.bind_job_runtime(runtime)

        result = handler._execute_command(
            provider,
            {
                "type": "exec_run",
                "command": _sleep_command(1.5),
                "initial_wait_seconds": 1,
            },
            _context(),
        )

        self.assertEqual(result.stream_events[0]["status"], "running")
        self.assertTrue(completed.wait(timeout=4.0))
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].status, "succeeded")
        self.assertEqual(
            published[0].payload["run_id"],
            result.state_updates["capability_execution"]["run_id"],
        )


if __name__ == "__main__":
    unittest.main()
