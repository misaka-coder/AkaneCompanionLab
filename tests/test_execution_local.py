from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from companion_v01.execution_local import ExecutionPathError, TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner, make_cursor
from companion_v01.execution_specs import (
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNKNOWN,
)


def _python_command(code: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, "-c", code])
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _sleep_command(seconds: int) -> str:
    return _python_command(f"import time; time.sleep({seconds})")


def _echo_env(name: str) -> str:
    if os.name == "nt":
        return f"echo %{name}%"
    return f"echo ${name}"


def _current_dir_command() -> str:
    return "cd" if os.name == "nt" else "pwd"


class TrustedLocalExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()
        self.run_log_dir = Path(self._tmp.name) / "runlogs"
        self.owner = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local")

    def _executor(self, **kwargs) -> TrustedLocalExecutor:
        defaults = {"workspace_root": self.workspace, "run_log_dir": self.run_log_dir}
        defaults.update(kwargs)
        return TrustedLocalExecutor(**defaults)

    # -- completed / failed ----------------------------------------------------------

    def test_completed_command_returns_full_output_and_output_ref(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="echo hello", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertEqual(start.exit_code, 0)
        self.assertIn("hello", start.stdout)
        self.assertEqual(start.output_ref, f"runlog:{start.run_id}")
        log_path = self.run_log_dir / f"{start.run_id}.log"
        self.assertTrue(log_path.exists())
        self.assertIn("hello", log_path.read_text(encoding="utf-8"))

    def test_failed_command_returns_exit_code(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="exit 2", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertEqual(start.exit_code, 2)

    def test_empty_command_is_rejected(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="   ", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertIn("invalid_execution_command", start.reason)

    # -- long task / status / cancel / timeout ---------------------------------------

    def test_long_command_returns_running_then_status(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command=_sleep_command(30), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING)
        self.assertTrue(start.next_cursor)
        status = executor.status(owner=self.owner, run_id=start.run_id)
        self.assertEqual(status.status, EXEC_STATUS_RUNNING)
        executor.cancel(owner=self.owner, run_id=start.run_id)

    def test_cancel_confirms_process_group_termination(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command=_sleep_command(30), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING)
        cancel = executor.cancel(owner=self.owner, run_id=start.run_id)
        self.assertTrue(cancel.ok)
        self.assertEqual(cancel.status, EXEC_STATUS_CANCELLED)
        status = executor.status(owner=self.owner, run_id=start.run_id)
        self.assertEqual(status.status, EXEC_STATUS_CANCELLED)

    def test_timeout_marks_timed_out(self) -> None:
        executor = self._executor()
        start = executor.run(
            owner=self.owner,
            command=_sleep_command(30),
            timeout_seconds=1,
            initial_wait_seconds=2,
        )
        self.assertEqual(start.status, EXEC_STATUS_TIMED_OUT)
        self.assertIn("execution_timeout", start.reason)

    def test_cancel_after_terminal_is_already_ended(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="echo done", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        cancel = executor.cancel(owner=self.owner, run_id=start.run_id)
        self.assertTrue(cancel.ok)
        self.assertEqual(cancel.status, "already_ended")

    # -- path semantics --------------------------------------------------------------

    def test_cwd_resolves_inside_workspace(self) -> None:
        subdir = self.workspace / "subdir"
        subdir.mkdir()
        executor = self._executor()
        start = executor.run(owner=self.owner, command=_current_dir_command(), cwd="subdir", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIn(str(subdir.resolve()), start.stdout)

    def test_absolute_cwd_rejected(self) -> None:
        executor = self._executor()
        target = str(Path(tempfile.gettempdir()))
        start = executor.run(owner=self.owner, command="echo hi", cwd=target, initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertIn("invalid_execution_cwd", start.reason)
        self.assertIn("absolute_path_not_allowed", start.reason)

    def test_traversal_cwd_rejected(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="echo hi", cwd="../../outside", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertIn("path_traversal_not_allowed", start.reason)

    def test_missing_cwd_rejected(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="echo hi", cwd="does_not_exist", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertIn("cwd_not_found", start.reason)

    def test_mount_alias_resolves_outside_workspace(self) -> None:
        mount = Path(self._tmp.name) / "mounted"
        mount.mkdir()
        executor = self._executor(mounts={"shared": mount})
        start = executor.run(owner=self.owner, command=_current_dir_command(), cwd="alias:shared", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIn(str(mount.resolve()), start.stdout)

    def test_unknown_mount_alias_rejected(self) -> None:
        executor = self._executor(mounts={"shared": self.workspace})
        start = executor.run(owner=self.owner, command="echo hi", cwd="alias:nope", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertIn("unknown_mount_alias", start.reason)

    def test_resolve_workdir_symlink_escape_rejected(self) -> None:
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        link = self.workspace / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted on this platform")
        executor = self._executor()
        with self.assertRaises(ExecutionPathError):
            executor._resolve_workdir("escape")

    # -- environment allowlist --------------------------------------------------------

    def test_env_allowlist_blocks_unspecified_vars(self) -> None:
        os.environ["AKANE_EXEC_TEST_SECRET"] = "s3cr3t_value"
        self.addCleanup(lambda: os.environ.pop("AKANE_EXEC_TEST_SECRET", None))
        executor = self._executor(allowed_env_names={"PATH", "SystemRoot", "COMSPEC"})
        start = executor.run(owner=self.owner, command=_echo_env("AKANE_EXEC_TEST_SECRET"), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertNotIn("s3cr3t_value", start.stdout)

    def test_env_allowlist_passes_allowed_names(self) -> None:
        os.environ["AKANE_EXEC_TEST_SECRET"] = "s3cr3t_value"
        self.addCleanup(lambda: os.environ.pop("AKANE_EXEC_TEST_SECRET", None))
        executor = self._executor(
            allowed_env_names={"PATH", "SystemRoot", "COMSPEC", "AKANE_EXEC_TEST_SECRET"},
        )
        start = executor.run(owner=self.owner, command=_echo_env("AKANE_EXEC_TEST_SECRET"), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIn("s3cr3t_value", start.stdout)

    def test_host_env_is_not_inherited_wholesale(self) -> None:
        os.environ["AKANE_EXEC_TEST_SECRET"] = "s3cr3t_value"
        self.addCleanup(lambda: os.environ.pop("AKANE_EXEC_TEST_SECRET", None))
        executor = self._executor(allowed_env_names={"PATH"})
        start = executor.run(owner=self.owner, command=_echo_env("AKANE_EXEC_TEST_SECRET"), initial_wait_seconds=1)
        self.assertNotIn("s3cr3t_value", start.stdout)

    # -- ownership / provider scope ---------------------------------------------------

    def test_cross_session_query_is_unknown(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="echo hi", initial_wait_seconds=1)
        other = ExecutionRunOwner(profile_user_id="alice", session_id="s2", provider_id="local")
        status = executor.status(owner=other, run_id=start.run_id)
        self.assertEqual(status.status, EXEC_STATUS_UNKNOWN)
        cancel = executor.cancel(owner=other, run_id=start.run_id)
        self.assertEqual(cancel.status, EXEC_STATUS_UNKNOWN)

    def test_provider_id_mismatch_is_rejected(self) -> None:
        executor = self._executor()
        cloud_owner = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="cloud")
        start = executor.run(owner=cloud_owner, command="echo hi", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_EXECUTION_UNKNOWN)
        status = executor.status(owner=cloud_owner, run_id="execrun_" + "0" * 32)
        self.assertEqual(status.status, EXEC_STATUS_UNKNOWN)
        cancel = executor.cancel(owner=cloud_owner, run_id="execrun_" + "0" * 32)
        self.assertEqual(cancel.status, EXEC_STATUS_UNKNOWN)
        self.assertEqual(cancel.reason, "run_not_found")

    # -- output paging -----------------------------------------------------------------

    def test_long_output_truncates_initial_result_then_continuation_reads_rest(self) -> None:
        executor = self._executor()
        command = _python_command("import sys; sys.stdout.write('x' * 60000)")
        start = executor.run(owner=self.owner, command=command, initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIsNotNone(start.next_cursor)
        self.assertLess(len(start.stdout.encode("utf-8")), 60000)
        first = executor.status(owner=self.owner, run_id=start.run_id, cursor=start.next_cursor)
        self.assertEqual(first.status, EXEC_STATUS_COMPLETED)
        self.assertIn("x", first.tail)
        self.assertIsNone(first.next_cursor)

    def test_output_ref_log_contains_full_output(self) -> None:
        executor = self._executor()
        start = executor.run(owner=self.owner, command="echo line1 && echo line2", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIn("line1", start.stdout)
        self.assertIn("line2", start.stdout)
        log_path = self.run_log_dir / f"{start.run_id}.log"
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("line1", content)
        self.assertIn("line2", content)

    def test_terminal_waits_for_reader_drain_before_publishing_result(self) -> None:
        class DelayedReaderExecutor(TrustedLocalExecutor):
            def _read_pipe(self, run_id, owner, pipe, stream):
                time.sleep(0.15)
                return super()._read_pipe(run_id, owner, pipe, stream)

        executor = DelayedReaderExecutor(workspace_root=self.workspace, run_log_dir=self.run_log_dir)
        start = executor.run(owner=self.owner, command="echo drained", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIn("drained", start.stdout)
        self.assertIn("drained", (self.run_log_dir / f"{start.run_id}.log").read_text(encoding="utf-8"))

    def test_compacted_output_can_be_reloaded_from_the_same_status_tool(self) -> None:
        executor = self._executor()
        command = _python_command("import sys; sys.stdout.write('BEGIN|' + 'x' * 90000 + '|END')")
        start = executor.run(owner=self.owner, command=command, initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertTrue(start.stdout.startswith("BEGIN|"))
        self.assertIsNotNone(start.next_cursor)

        reloaded = executor.status(owner=self.owner, run_id=start.run_id, cursor=make_cursor(start.run_id, 0))
        self.assertEqual(reloaded.reason, "output_reloaded")
        self.assertTrue(reloaded.tail.startswith("BEGIN|"))

        recovered = start.stdout
        cursor = start.next_cursor
        while cursor is not None:
            page = executor.status(owner=self.owner, run_id=start.run_id, cursor=cursor)
            self.assertNotEqual(page.reason, "output_compacted_without_persistence")
            recovered += page.tail
            cursor = page.next_cursor
        self.assertEqual(recovered, "BEGIN|" + "x" * 90000 + "|END")

    def test_spawn_failure_closes_and_removes_log_handle(self) -> None:
        class FailingSpawnExecutor(TrustedLocalExecutor):
            def _spawn(self, command, workdir, env):
                raise OSError("synthetic spawn failure")

        executor = FailingSpawnExecutor(workspace_root=self.workspace, run_log_dir=self.run_log_dir)
        start = executor.run(owner=self.owner, command="echo never", initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_FAILED)
        self.assertFalse(executor._logs)
        log_paths = list(self.run_log_dir.glob("*.log"))
        self.assertEqual(len(log_paths), 1)
        self.assertEqual(log_paths[0].read_bytes(), b"")

    def test_timeout_does_not_claim_termination_before_kill_confirmation(self) -> None:
        allow_kill = threading.Event()
        first_attempt = threading.Event()

        class DelayedKillExecutor(TrustedLocalExecutor):
            def _kill_process_group(self, proc):
                first_attempt.set()
                if not allow_kill.is_set():
                    return False
                return super()._kill_process_group(proc)

        executor = DelayedKillExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            cancel_confirm_grace_seconds=0.2,
        )
        start = executor.run(
            owner=self.owner,
            command=_sleep_command(30),
            timeout_seconds=1,
            initial_wait_seconds=2,
        )
        self.assertTrue(first_attempt.is_set())
        self.assertEqual(start.status, EXEC_STATUS_RUNNING)
        self.assertEqual(start.reason, "timeout_termination_pending")
        allow_kill.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = executor.status(owner=self.owner, run_id=start.run_id)
            if status.status == EXEC_STATUS_TIMED_OUT:
                break
            time.sleep(0.02)
        self.assertEqual(status.status, EXEC_STATUS_TIMED_OUT)

    def test_cancel_failure_remains_running_until_kill_confirmation(self) -> None:
        allow_kill = threading.Event()

        class DelayedKillExecutor(TrustedLocalExecutor):
            def _kill_process_group(self, proc):
                if not allow_kill.is_set():
                    return False
                return super()._kill_process_group(proc)

        executor = DelayedKillExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            cancel_confirm_grace_seconds=0.2,
        )
        start = executor.run(owner=self.owner, command=_sleep_command(30), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING)
        cancel = executor.cancel(owner=self.owner, run_id=start.run_id)
        self.assertFalse(cancel.ok)
        self.assertEqual(cancel.status, "cancel_failed")
        status = executor.status(owner=self.owner, run_id=start.run_id)
        self.assertEqual(status.status, EXEC_STATUS_RUNNING)
        self.assertEqual(status.reason, "cancel_termination_pending")
        allow_kill.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = executor.status(owner=self.owner, run_id=start.run_id)
            if status.status == EXEC_STATUS_CANCELLED:
                break
            time.sleep(0.02)
        self.assertEqual(status.status, EXEC_STATUS_CANCELLED)

    def test_utf8_multibyte_output_survives_read_chunk_boundaries(self) -> None:
        executor = self._executor()
        unit = "中文你好-😀"
        command = _python_command("import sys; sys.stdout.write('中文你好-😀' * 5000)")
        start = executor.run(owner=self.owner, command=command, initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        self.assertIsNotNone(start.next_cursor)
        recovered = start.stdout
        cursor = start.next_cursor
        while cursor is not None:
            page = executor.status(owner=self.owner, run_id=start.run_id, cursor=cursor)
            recovered += page.tail
            cursor = page.next_cursor
        self.assertEqual(recovered, unit * 5000)
        log_path = self.run_log_dir / f"{start.run_id}.log"
        raw = log_path.read_bytes()
        self.assertEqual(raw, (unit * 5000).encode("utf-8"))
        self.assertNotIn(b"\xef\xbf\xbd", raw)

    def test_secret_and_path_are_redacted_for_model_but_kept_in_private_log(self) -> None:
        executor = self._executor()
        command = _python_command(
            "import sys; print('api_key=supersecret123'); "
            "print('C:\\\\Users\\\\alice\\\\private.txt')"
        )
        start = executor.run(owner=self.owner, command=command, initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_COMPLETED)
        from companion_v01.execution_run import map_exec_run_outcome

        mapped = map_exec_run_outcome(start)
        self.assertNotIn("supersecret123", mapped.model_feedback)
        self.assertNotIn("C:\\Users\\alice", mapped.model_feedback)
        self.assertNotIn("supersecret123", str(mapped.data))
        log = (self.run_log_dir / f"{start.run_id}.log").read_text(encoding="utf-8", errors="replace")
        self.assertIn("supersecret123", log)
        self.assertIn("private.txt", log)
        # The run is terminal; the watcher closes its log handle shortly after.
        # Wait so the tempdir cleanup never races an open handle on Windows.
        deadline = time.monotonic() + 5
        while start.run_id in executor._logs and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_log_pruning_never_removes_an_active_run_log(self) -> None:
        executor = self._executor(run_log_retention_seconds=1)
        start = executor.run(owner=self.owner, command=_sleep_command(30), initial_wait_seconds=1)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING)
        path = self.run_log_dir / f"{start.run_id}.log"
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertEqual(executor.prune_run_logs(), 0)
        self.assertTrue(path.exists())
        executor.cancel(owner=self.owner, run_id=start.run_id)


if __name__ == "__main__":
    unittest.main()
