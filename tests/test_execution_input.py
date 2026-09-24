import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from companion_v01.execution_input import InputChannel
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner
from companion_v01.tool_handlers.execution import ExecInputToolHandler
from tests.test_execution_local import _python_command
from tests.test_execution_wiring import _context


class ExecutionInputTests(unittest.TestCase):
    def wait_written(self, channel):
        deadline = time.monotonic() + 3
        while channel.status()["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(.01)
        return channel.status()

    def test_write_is_exact_and_deduplicated(self):
        pipe = io.BytesIO()
        channel = InputChannel(pipe)
        text = "  中文\n\tlast "
        channel.submit(sequence=1, text=text, close=False)
        self.assertEqual(self.wait_written(channel)["status"], "written")
        channel.submit(sequence=1, text=text, close=False)
        self.assertEqual(pipe.getvalue(), text.encode())
        self.assertEqual(channel.submit(sequence=1, text="different", close=False)["reason"], "input_sequence_conflict")
        self.assertEqual(channel.submit(sequence=3, text="later", close=False)["reason"], "input_sequence_out_of_order")

    def test_slow_write_is_observable_and_does_not_block_control(self):
        started, release = threading.Event(), threading.Event()
        class SlowPipe(io.BytesIO):
            def write(self, value):
                started.set()
                release.wait(3)
                return super().write(value)
        channel = InputChannel(SlowPipe())
        try:
            channel.submit(sequence=1, text="a", close=False)
            self.assertTrue(started.wait(1))
            self.assertEqual(channel.status()["status"], "pending")
            self.assertEqual(channel.submit(sequence=2, text="b", close=False)["reason"], "input_write_pending")
            channel.finish()
        finally:
            release.set()
        self.assertEqual(self.wait_written(channel)["status"], "written")
        self.assertTrue(channel.pipe.closed)

    def test_partial_write_failure_is_not_replayed(self):
        class PartialPipe(io.BytesIO):
            calls = 0
            def write(self, value):
                self.calls += 1
                if self.calls > 1:
                    raise BrokenPipeError()
                return super().write(value[:2])
        pipe = PartialPipe()
        channel = InputChannel(pipe)
        channel.submit(sequence=1, text="abcd", close=False)
        receipt = self.wait_written(channel)
        self.assertEqual(receipt["status"], "input_unknown")
        self.assertEqual(receipt["written_bytes"], 2)
        self.assertEqual(channel.submit(sequence=1, text="abcd", close=False)["status"], "input_unknown")
        self.assertEqual(pipe.getvalue(), b"ab")

    def test_live_input_eof_output_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = TrustedLocalExecutor(workspace_root=root, run_log_dir=root / "logs")
            owner = ExecutionRunOwner("alice", "s1", "local")
            start = provider.run(owner=owner, command=_python_command("import sys; print('ready',flush=True); print('received='+sys.stdin.readline().rstrip(),flush=True); print('eof='+str(sys.stdin.read()==''),flush=True)"),
                                 interactive=True, initial_wait_seconds=3, timeout_seconds=12)
            try:
                self.assertEqual(start.status, "running")
                self.assertIn("ready", start.stdout)
                denied = provider.input(owner=ExecutionRunOwner("other", "s1", "local"), run_id=start.run_id)
                self.assertEqual(denied["reason"], "run_not_found")
                result = provider.input(owner=owner, run_id=start.run_id, action="write", sequence=1, text="synthetic-audit\n", close=True)
                self.assertIn(result["status"], {"pending", "written"})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    status = provider.status(owner=owner, run_id=start.run_id)
                    if status.status != "running": break
                    time.sleep(.05)
                self.assertEqual(status.status, "completed", status)
                self.assertIn("received=synthetic-audit", status.tail)
                self.assertIn("eof=True", status.tail)
                self.assertEqual(provider.input(owner=owner, run_id=start.run_id)["status"], "written")
                duplicate = provider.input(owner=owner, run_id=start.run_id, action="write", sequence=1, text="synthetic-audit\n", close=True)
                self.assertEqual(duplicate["status"], "written")
            finally:
                provider.cancel(owner=owner, run_id=start.run_id)

    def test_write_uses_existing_command_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            class Provider:
                provider_id = "local"
                def input(self, **kwargs):
                    raise AssertionError("must not execute before authorization")
            handler = ExecInputToolHandler(execution_provider=Provider(), config_base_dir=directory)
            call = handler.normalize_call({"type": "exec_input", "run_id": "execrun_" + "a"*32,
                "action": "write", "sequence": 1, "text": "print(1)\n"})
            result = handler.execute(call=call, context=_context())
            self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
            self.assertNotEqual(handler._fingerprint(call), handler._fingerprint({**call, "text": "print(2)\n"}))
