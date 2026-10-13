from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.execution_run import (
    ExecutionAvailability,
    ExecCancelResult,
    ExecRunStart,
    ExecRunStatus,
    make_cursor,
)
from companion_v01.local_capability_config import save_capability_approval_modes
from companion_v01.task_work import TaskWork
from companion_v01.tool_handlers.core import TaskExecutionScope
from companion_v01.tool_handlers.execution import ExecCancelToolHandler, ExecRunToolHandler, ExecStatusToolHandler
from tests.test_execution_resources import _context, _Harness


class _FileProvider:
    """Controlled process facts around real staged inputs and output files."""

    provider_id = "local"

    def __init__(self, root, *, start_status="running", available=True):
        self.root = root
        self.start_status = start_status
        self.available = available
        self.run_calls = []
        self.status_value = "completed"
        self.query_failure = False
        self.cancel_status = "cancelled"
        self.ended_status = "completed"

    def availability(self):
        return ExecutionAvailability(self.available, "ready" if self.available else "offline", "provider_offline")

    def resolve_workdir(self, cwd):
        return (self.root / cwd).resolve()

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        run_id = kwargs["run_id"]
        if self.start_status != "unavailable":
            (self.resolve_workdir(kwargs["cwd"]) / "out.txt").write_text("actual output", encoding="utf-8")
        return ExecRunStart(
            self.start_status, run_id,
            exit_code=0 if self.start_status == "completed" else 1 if self.start_status == "failed" else None,
            next_cursor=make_cursor(run_id, 0) if self.start_status == "running" else None,
            reason="provider_offline" if self.start_status == "unavailable" else "termination_unconfirmed"
            if self.start_status == "execution_unknown" else "",
        )

    def status(self, *, run_id, **kwargs):
        if self.query_failure:
            raise RuntimeError("temporary_query_failure")
        return ExecRunStatus(
            self.status_value, run_id,
            exit_code=0 if self.status_value == "completed" else 1 if self.status_value == "failed" else None,
            next_cursor=make_cursor(run_id, 0) if self.status_value == "running" else None,
            reason="termination_unconfirmed" if self.status_value == "execution_unknown" else "",
        )

    def cancel(self, *, run_id, **kwargs):
        return ExecCancelResult(
            self.cancel_status != "cancel_failed", self.cancel_status, run_id,
            self.ended_status if self.cancel_status == "already_ended" else "",
        )


class ExecutionHandlerBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.h = _Harness(Path(directory.name))
        self.item = self.h.register_input(content="original input")
        self.policies = self.h.root / "policies"
        save_capability_approval_modes(
            base_dir=self.policies, profile_user_id="alice", modes={"ops": "trusted_auto_allow"}
        )

    def _handler(self, kind, provider):
        return kind(execution_provider=provider, config_base_dir=self.policies, resource_bridge=self.h.bridge)

    def _start(self, *, status="running", available=True, outputs=True, task=False):
        provider = _FileProvider(self.h.workspace, start_status=status, available=available)
        handler = self._handler(ExecRunToolHandler, provider)
        work = TaskWork() if task else None
        context = _context()
        if work is not None:
            context = replace(context, execution_scope=TaskExecutionScope(str(self.h.workspace), "child", work))
        call = handler.normalize_call({
            "type": "exec_run", "command": "controlled file operation",
            "input_resources": [{"handle": self.item["attachment_handle"], "as": "in.txt"}],
            "output_globs": ["out.txt"] if outputs else [],
        })
        with patch.object(self.h.bridge, "stage_inputs", wraps=self.h.bridge.stage_inputs) as staging:
            result = handler.execute(call=call, context=context)
        run_id = staging.call_args.kwargs["run_id"]
        run_dir = self.h.workspace / self.h.bridge.run_cwd_relpath(run_id)
        return provider, result, run_id, run_dir, context, work

    def _query(self, provider, run_id, context):
        return self._handler(ExecStatusToolHandler, provider).execute(
            call={"type": "exec_status", "run_id": run_id}, context=context
        )

    def test_execution_deadline_reaches_provider_after_admission(self):
        for kwargs, expected in (({}, 0), ({"timeout_seconds": 0}, 0), ({"timeout_seconds": 7200}, 7200)):
            with self.subTest(kwargs=kwargs):
                provider = _FileProvider(self.h.workspace, start_status="completed")
                handler = self._handler(ExecRunToolHandler, provider)
                call = handler.normalize_call({
                    "type": "exec_run", "command": "controlled file operation",
                    "input_resources": [{"handle": self.item["attachment_handle"], "as": "in.txt"}],
                    **kwargs,
                })
                result = handler.execute(call=call, context=_context())
                self.assertEqual(result.state_updates["capability_execution"]["status"], "completed")
                self.assertEqual(len(provider.run_calls), 1)
                self.assertEqual(provider.run_calls[0]["timeout_seconds"], expected)

    def _cancel(self, provider, run_id, context):
        return self._handler(ExecCancelToolHandler, provider).execute(
            call={"type": "exec_cancel", "run_id": run_id}, context=context
        )

    def _assert_not_started(self, result, run_dir, *, outputs=True):
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(state["reason"], "provider_offline")
        self.assertEqual(state.get("artifact_status"), "not_registered" if outputs else "not_requested")
        if outputs:
            self.assertEqual(state.get("artifact_reason"), "command_not_started")
        self.assertFalse(run_dir.exists())
        self.assertEqual((self.h.root / "source.txt").read_text(encoding="utf-8"), "original input")

    def test_unavailable_before_dispatch_releases_staged_inputs(self):
        provider, result, _, run_dir, _, _ = self._start(available=False)
        self.assertFalse(provider.run_calls)
        self._assert_not_started(result, run_dir)

    def test_unavailable_start_releases_staged_inputs(self):
        provider, result, _, run_dir, _, _ = self._start(status="unavailable")
        self.assertEqual(len(provider.run_calls), 1)
        self._assert_not_started(result, run_dir)

    def test_input_only_unavailable_start_releases_staged_inputs(self):
        _, result, _, run_dir, _, _ = self._start(available=False, outputs=False)
        self._assert_not_started(result, run_dir, outputs=False)

    def test_unavailable_start_preserves_persistent_workspace_files(self):
        persistent = self.h.workspace / "persistent"
        persistent.mkdir()
        original = persistent / "keep.txt"
        original.write_text("user work", encoding="utf-8")
        provider = _FileProvider(self.h.workspace, available=False)
        handler = self._handler(ExecRunToolHandler, provider)
        call = handler.normalize_call({
            "type": "exec_run", "command": "controlled file operation",
            "cwd": "persistent", "output_globs": ["*.txt"],
        })
        result = handler.execute(call=call, context=_context())
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(state["artifact_status"], "not_registered")
        self.assertEqual(state["artifact_reason"], "command_not_started")
        self.assertFalse(provider.run_calls)
        self.assertEqual(original.read_text(encoding="utf-8"), "user work")

    def test_unavailable_query_preserves_files_for_later_completion(self):
        provider, _, run_id, run_dir, context, _ = self._start()
        provider.status_value = "unavailable"
        result = self._query(provider, run_id, context)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "unavailable")
        self.assertTrue((run_dir / "in.txt").is_file())
        self.assertTrue((run_dir / "out.txt").is_file())
        provider.status_value = "completed"
        result = self._query(provider, run_id, context)
        self.assertEqual(result.state_updates["capability_execution"]["artifact_status"], "registered")

    def test_task_query_failure_preserves_files_for_later_completion(self):
        provider, _, run_id, run_dir, context, work = self._start(task=True)
        provider.query_failure = True
        ready = work.collect()
        self.assertEqual(ready[0]["status"], "execution_unknown")
        self.assertIn("status_query_failed", ready[0]["summary"])
        self.assertTrue((run_dir / "in.txt").is_file())
        self.assertTrue((run_dir / "out.txt").is_file())
        provider.query_failure = False
        result = self._query(provider, run_id, context)
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["artifact_status"], "registered")
        self.assertEqual(len(state["generated_resources"]), 1)

    def test_task_uncertain_results_preserve_unconfirmed_files(self):
        for status in ("unavailable", "unknown", "execution_unknown"):
            with self.subTest(status=status):
                provider, _, _, run_dir, _, work = self._start(task=True)
                provider.status_value = status
                ready = work.collect()
                self.assertEqual(ready[0]["status"], status)
                self.assertTrue((run_dir / "in.txt").is_file())
                self.assertTrue((run_dir / "out.txt").is_file())

    def test_completed_start_query_and_cancel_keep_the_same_registered_handle(self):
        provider, result, run_id, run_dir, context, _ = self._start(status="completed")
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["artifact_status"], "registered")
        handles = state["generated_resources"]
        self.assertEqual(len(handles), 1)
        self.assertFalse(run_dir.exists())
        queried = self._query(provider, run_id, context)
        provider.cancel_status = "already_ended"
        cancelled = self._cancel(provider, run_id, context)
        for repeated in (queried, cancelled):
            self.assertEqual(repeated.state_updates["capability_execution"]["generated_resources"], handles)

    def test_confirmed_failure_paths_cleanup_without_registering_partial_outputs(self):
        cases = [(source, status) for source in ("start", "query", "ended_cancel")
                 for status in ("failed", "timed_out", "cancelled")]
        cases.append(("cancel", "cancelled"))
        for source, status in cases:
            with self.subTest(source=source, status=status):
                provider, result, run_id, run_dir, context, _ = self._start(
                    status=status if source == "start" else "running"
                )
                if source == "query":
                    provider.status_value = status
                    result = self._query(provider, run_id, context)
                elif source in {"cancel", "ended_cancel"}:
                    provider.cancel_status = "already_ended" if source == "ended_cancel" else "cancelled"
                    provider.ended_status = status
                    result = self._cancel(provider, run_id, context)
                state = result.state_updates["capability_execution"]
                self.assertEqual(state["artifact_status"], "not_registered")
                self.assertEqual(state["artifact_reason"], f"command_{status}")
                self.assertFalse(state.get("generated_resources"))
                self.assertFalse(run_dir.exists())

    def test_unconfirmed_cancel_preserves_files_for_later_completion(self):
        provider, _, run_id, run_dir, context, work = self._start(task=True)
        provider.cancel_status = "cancel_failed"
        result = self._cancel(provider, run_id, context)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "cancel_failed")
        self.assertTrue(work.pending)
        self.assertTrue((run_dir / "out.txt").is_file())
        result = self._query(provider, run_id, context)
        self.assertEqual(result.state_updates["capability_execution"]["artifact_status"], "registered")
        self.assertFalse(work.pending)

    def test_unconfirmed_initial_result_preserves_staged_inputs(self):
        _, result, _, run_dir, _, _ = self._start(status="execution_unknown")
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], "execution_unknown")
        self.assertEqual(state["reason"], "termination_unconfirmed")
        self.assertTrue((run_dir / "in.txt").is_file())
        self.assertTrue((run_dir / "out.txt").is_file())


if __name__ == "__main__":
    unittest.main()
