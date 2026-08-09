from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner, execute_exec_status
from companion_v01.execution_specs import EXEC_STATUS_EXECUTION_UNKNOWN, EXEC_STATUS_RUNNING


def _sleep_command(seconds: int) -> str:
    return f"python -c \"import time; time.sleep({seconds})\""


class _RefusingKillExecutor(TrustedLocalExecutor):
    """Refuses every kill until ``refuse`` is cleared, so the watcher must give up."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.refuse = True
        self.spawned_proc = None

    def _kill_process_group(self, proc):
        if self.refuse:
            return False
        return super()._kill_process_group(proc)

    def _spawn(self, command, workdir, env):
        proc = super()._spawn(command, workdir, env)
        self.spawned_proc = proc
        return proc


class ExecutionKillRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()
        self.run_log_dir = Path(self._tmp.name) / "runlogs"
        self.owner = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local")
        self.executor = None

    def tearDown(self) -> None:
        # Reap any leaked process after the watcher gave up.
        if self.executor is not None and getattr(self.executor, "spawned_proc", None) is not None:
            self.executor.refuse = False
            self.executor._kill_process_group(self.executor.spawned_proc)

    def _wait_for(self, predicate, *, deadline_seconds: float = 6.0) -> None:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("timed out waiting for condition")

    def _run_running(self) -> str:
        start = self.executor.run(owner=self.owner, command=_sleep_command(30), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING)
        return start.run_id

    def test_kill_retry_gives_up_to_execution_unknown_and_cleans_handles(self) -> None:
        self.executor = _RefusingKillExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            max_kill_attempts=2,
            cancel_confirm_grace_seconds=0.5,
        )
        run_id = self._run_running()
        cancel = self.executor.cancel(owner=self.owner, run_id=run_id)
        self.assertFalse(cancel.ok)
        self.assertEqual(cancel.status, "cancel_failed")

        self._wait_for(
            lambda: self.executor.status(owner=self.owner, run_id=run_id).status == EXEC_STATUS_EXECUTION_UNKNOWN
        )
        status = self.executor.status(owner=self.owner, run_id=run_id)
        self.assertEqual(status.status, EXEC_STATUS_EXECUTION_UNKNOWN)
        self.assertEqual(status.reason, "termination_unconfirmed")
        mapped = execute_exec_status(self.executor, owner=self.owner, run_id=run_id)
        self.assertEqual(mapped.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)
        self.assertEqual(mapped.envelope_status, "error")
        self.assertIn("无法确认", mapped.model_feedback)

        self._wait_for(lambda: run_id not in self.executor._logs and run_id not in self.executor._procs)
        self.assertNotIn(run_id, self.executor._logs)
        self.assertNotIn(run_id, self.executor._procs)

    def test_cancel_after_give_up_reports_cancel_failed_not_cancelled(self) -> None:
        self.executor = _RefusingKillExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            max_kill_attempts=2,
            cancel_confirm_grace_seconds=0.5,
        )
        run_id = self._run_running()

        # First cancel triggers the give-up (executor_unknown terminal).
        first = self.executor.cancel(owner=self.owner, run_id=run_id)
        self.assertFalse(first.ok)
        self.assertEqual(first.status, "cancel_failed")
        self._wait_for(
            lambda: self.executor.status(owner=self.owner, run_id=run_id).status == EXEC_STATUS_EXECUTION_UNKNOWN
        )

        # A later cancel must not claim the run was cancelled or already ended.
        again = self.executor.cancel(owner=self.owner, run_id=run_id)
        self.assertFalse(again.ok)
        self.assertEqual(again.status, "cancel_failed")
        self.assertEqual(again.reason, "termination_unconfirmed")


if __name__ == "__main__":
    unittest.main()
