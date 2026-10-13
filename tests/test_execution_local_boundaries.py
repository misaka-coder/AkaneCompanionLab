"""Rejected or competing runs must preserve the original task's output."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner, ExecutionRunStore
from tests.test_execution_local import _python_command, _shutdown_executor


class LocalExecutionBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.owner = ExecutionRunOwner('alice', 'log-boundary', 'local')

    def executor(self, **kwargs):
        executor = TrustedLocalExecutor(workspace_root=self.root, run_log_dir=self.root / 'logs', **kwargs)
        self.addCleanup(_shutdown_executor, executor)
        return executor

    def reject_without_spawning(self, executor, run_id):
        with patch.object(executor, '_spawn', wraps=executor._spawn) as spawn:
            result = executor.run(owner=self.owner, command='echo unexpected', run_id=run_id)
            self.assertEqual(result.status, 'failed')
            spawn.assert_not_called()
        return result

    def test_duplicate_completed_run_keeps_original_log_and_record(self):
        executor = self.executor()
        start = executor.run(owner=self.owner, command=_python_command("print('retained-output')"),
                             initial_wait_seconds=5)
        self.assertEqual(start.status, 'completed')
        path = executor.run_log_dir / f'{start.run_id}.log'
        before = path.read_bytes()
        self.assertEqual(self.reject_without_spawning(executor, start.run_id).reason, 'duplicate_run_id')
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), before)
        status = executor.status(owner=self.owner, run_id=start.run_id)
        self.assertEqual(status.status, 'completed')
        self.assertEqual(status.output_ref, start.output_ref)

    def test_duplicate_active_run_keeps_handle_and_later_output(self):
        executor = self.executor()
        start = executor.run(owner=self.owner, interactive=True, initial_wait_seconds=1,
                             command=_python_command("import sys;print('head',flush=True);sys.stdin.read();print('tail',flush=True)"))
        self.assertEqual(start.status, 'running')
        handle = executor._logs[start.run_id]
        path = executor.run_log_dir / f'{start.run_id}.log'
        self.assertEqual(self.reject_without_spawning(executor, start.run_id).reason, 'duplicate_run_id')
        self.assertIs(executor._logs.get(start.run_id), handle)
        self.assertFalse(handle.closed)
        self.assertTrue(path.exists())
        submitted = executor.input(owner=self.owner, run_id=start.run_id, action='write',
                                   sequence=1, text='continue\n', close=True)
        self.assertIn(submitted['status'], ('pending', 'written'))
        deadline = time.monotonic() + 5
        while (status := executor.status(owner=self.owner, run_id=start.run_id)).status == 'running':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        self.assertEqual(status.status, 'completed')
        self.assertIn(b'head', path.read_bytes())
        self.assertIn(b'tail', path.read_bytes())

    def test_capacity_rejection_keeps_a_log_not_opened_by_this_attempt(self):
        store = ExecutionRunStore(max_runs=1)
        store.register('execrun_' + '1' * 32, owner=self.owner)
        executor = self.executor(store=store)
        run_id = 'execrun_' + '2' * 32
        path = executor.run_log_dir / f'{run_id}.log'
        path.write_bytes(b'existing-private-output\n')
        result = self.reject_without_spawning(executor, run_id)
        self.assertEqual(result.reason, 'execution_run_capacity_reached')
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), b'existing-private-output\n')
        self.assertIsNone(store.get(run_id, owner=self.owner))

    def test_capacity_rejection_removes_only_its_new_log(self):
        store = ExecutionRunStore(max_runs=1)
        store.register('execrun_' + '1' * 32, owner=self.owner)
        executor = self.executor(store=store)
        run_id = 'execrun_' + '2' * 32
        result = self.reject_without_spawning(executor, run_id)
        self.assertEqual(result.reason, 'execution_run_capacity_reached')
        self.assertFalse((executor.run_log_dir / f'{run_id}.log').exists())
        self.assertEqual(executor._logs, {})

    def test_log_creation_and_registration_admit_one_competing_run(self):
        executor = self.executor()
        run_id = 'execrun_' + '3' * 32
        first_opened = threading.Event()
        release_first = threading.Event()
        second_opened = threading.Event()
        real_open = executor._open_run_log

        def gated_open(value):
            output_ref = real_open(value)
            if output_ref:
                first_opened.set()
                if not release_first.wait(5):
                    raise RuntimeError('test_registration_gate_timeout')
            else:
                second_opened.set()
            return output_ref

        with patch.object(executor, '_open_run_log', side_effect=gated_open), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(executor.run, owner=self.owner, run_id=run_id, initial_wait_seconds=5,
                                command=_python_command("print('first-admitted')"))
            try:
                self.assertTrue(first_opened.wait(3))
                second = pool.submit(executor.run, owner=self.owner, run_id=run_id, command='echo unexpected')
                self.assertFalse(second_opened.wait(.2))
            finally:
                release_first.set()
            self.assertEqual(first.result(timeout=10).status, 'completed')
            rejected = second.result(timeout=10)
            self.assertEqual(rejected.status, 'failed')
            self.assertEqual(rejected.reason, 'duplicate_run_id')
        self.assertIn(b'first-admitted', (executor.run_log_dir / f'{run_id}.log').read_bytes())


if __name__ == '__main__':
    unittest.main()
