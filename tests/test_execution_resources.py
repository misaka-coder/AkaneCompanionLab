from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from capcore import PermissionDecision
from unittest.mock import patch

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.execution_resources import ExecutionResourceBridge
from companion_v01.execution_run import ExecRunStatus, ExecutionRunOwner
from companion_v01.generated_files import GeneratedFileService
from companion_v01.local_capability_config import save_approval_policy_config
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.execution import ExecRunToolHandler
from companion_v01.tool_runtime import ToolExecutionContext


OWNER = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local")


def _python_command(code: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, "-c", code])
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _context(*, profile_user_id: str = "alice", session_id: str = "s1", client_mode: str = "qq") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode=client_mode,
    )


class _Harness:
    def __init__(self, tmp: Path) -> None:
        self.root = Path(tmp)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.managed = self.root / "managed"
        self.store = MemoryStore(self.root / "db")
        self.attachment_service = AttachmentInboxService(store=self.store, base_dir=self.root)
        self.generated_service = GeneratedFileService(
            base_dir=self.managed,
            store=self.store,
            attachment_service=self.attachment_service,
        )
        self.bridge = ExecutionResourceBridge(
            generated_file_service=self.generated_service,
            workspace_root=self.workspace,
        )

    def register_input(self, *, name: str = "source.txt", content: str = "hello") -> dict:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        item = self.store.add_attachment_inbox_item(
            profile_user_id="alice",
            session_id="s1",
            kind="file",
            source="test",
            status="ready",
            origin_name=name,
            mime_type="text/plain",
            file_ext=".txt",
            file_size=path.stat().st_size,
            storage_relpath=str(path.name),
            timestamp=100,
        )
        return item


class ExecutionResourceBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = _Harness(Path(self._tmp.name))

    def test_stage_inputs_copies_into_run_workspace(self) -> None:
        item = self.harness.register_input()
        result = self.harness.bridge.stage_inputs(
            run_id="execrun_" + "a" * 32,
            owner=OWNER,
            input_resources=[{"handle": item["attachment_handle"], "as": "inputs/source.txt"}],
            output_globs=[],
        )
        self.assertTrue(result["ok"], result)
        staged_path = self.harness.workspace / result["cwd_relpath"] / "inputs/source.txt"
        self.assertTrue(staged_path.is_file())
        self.assertEqual(staged_path.read_text(encoding="utf-8"), "hello")

    def test_stage_inputs_requires_valid_run_id_and_exact_handle(self) -> None:
        self.harness.register_input()
        invalid_run = self.harness.bridge.stage_inputs(
            run_id="../../escape",
            owner=OWNER,
            input_resources=[],
            output_globs=[],
        )
        self.assertFalse(invalid_run["ok"])
        self.assertEqual(invalid_run["reason"], "invalid_execution_run_id")
        alias = self.harness.bridge.stage_inputs(
            run_id="execrun_" + "0" * 32,
            owner=OWNER,
            input_resources=[{"handle": "latest", "as": "input.txt"}],
            output_globs=[],
        )
        self.assertFalse(alias["ok"])
        self.assertIn("input_handle_not_found", alias["reason"])

    def test_stage_inputs_rejects_unsafe_as(self) -> None:
        item = self.harness.register_input()
        for unsafe in ["../escape.txt", "/abs/path.txt", "C:/drive.txt", "a:b.txt", "sub/../up.txt"]:
            result = self.harness.bridge.stage_inputs(
                run_id="execrun_" + "b" * 32,
                owner=OWNER,
                input_resources=[{"handle": item["attachment_handle"], "as": unsafe}],
                output_globs=[],
            )
            self.assertFalse(result["ok"], unsafe)
            self.assertIn("input_resource", str(result["reason"]))

    def test_stage_inputs_rejects_unknown_handle_and_duplicate_targets(self) -> None:
        item = self.harness.register_input()
        handle = item["attachment_handle"]
        missing = self.harness.bridge.stage_inputs(
            run_id="execrun_" + "c" * 32,
            owner=OWNER,
            input_resources=[{"handle": "file_999", "as": "x.txt"}],
            output_globs=[],
        )
        self.assertFalse(missing["ok"])
        self.assertIn("input_handle_not_found", missing["reason"])
        duplicate = self.harness.bridge.stage_inputs(
            run_id="execrun_" + "d" * 32,
            owner=OWNER,
            input_resources=[
                {"handle": handle, "as": "same.txt"},
                {"handle": handle, "as": "same.txt"},
            ],
            output_globs=[],
        )
        self.assertFalse(duplicate["ok"])
        self.assertIn("duplicate_input_resource_target", duplicate["reason"])

    def test_register_outputs_registers_multi_artifacts_sorted_and_deduped(self) -> None:
        run_id = "execrun_" + "e" * 32
        staged = self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["outputs/*.txt"],
        )
        self.assertTrue(staged["ok"])
        run_dir = self.harness.workspace / staged["cwd_relpath"]
        (run_dir / "outputs").mkdir(parents=True)
        (run_dir / "outputs" / "b.txt").write_text("bbb", encoding="utf-8")
        (run_dir / "outputs" / "a.txt").write_text("aaa", encoding="utf-8")
        (run_dir / "outputs" / "a.txt").write_text("aaa", encoding="utf-8")

        result = self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "registered")
        resources = result["generated_resources"]
        self.assertEqual(len(resources), 2)
        names = [item["name"] for item in resources]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(str(item["handle"]).startswith("gen_") for item in resources))
        for item in resources:
            self.assertNotIn("\\", item["handle"])
            self.assertNotIn(":", item["handle"])

    def test_register_outputs_is_idempotent_and_returns_same_handles(self) -> None:
        run_id = "execrun_" + "f" * 32
        self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["out.txt"],
        )
        run_dir = self.harness.workspace / self.harness.bridge.run_cwd_relpath(run_id)
        (run_dir / "out.txt").write_text("x", encoding="utf-8")
        first = self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER)
        second = self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(first["generated_resources"], second["generated_resources"])
        self.assertEqual(len(first["generated_resources"]), 1)

    def test_register_outputs_is_concurrently_idempotent(self) -> None:
        run_id = "execrun_" + "1" * 32
        self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["out.txt"],
        )
        run_dir = self.harness.workspace / self.harness.bridge.run_cwd_relpath(run_id)
        (run_dir / "out.txt").write_text("x", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _index: self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER),
                    range(4),
                )
            )
        handles = [result["generated_resources"][0]["handle"] for result in results]
        self.assertEqual(len(set(handles)), 1)
        stored = self.harness.store.list_generated_files(
            profile_user_id="alice",
            session_id="s1",
            statuses=["ready"],
            limit=20,
        )
        self.assertEqual(len(stored), 1)

    def test_register_outputs_supports_generic_file_types(self) -> None:
        run_id = "execrun_" + "2" * 32
        self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["photo.jpg", "clip.mp4", "slides.pptx", "Main.java", "script.py"],
        )
        run_dir = self.harness.workspace / self.harness.bridge.run_cwd_relpath(run_id)
        for name in ("photo.jpg", "clip.mp4", "slides.pptx", "Main.java", "script.py"):
            (run_dir / name).write_bytes(b"not-empty")
        result = self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "registered", result)
        self.assertEqual(
            {item["name"] for item in result["generated_resources"]},
            {"photo.jpg", "clip.mp4", "slides.pptx", "Main.java", "script.py"},
        )
        stored = self.harness.store.list_generated_files(
            profile_user_id="alice",
            session_id="s1",
            statuses=["ready"],
            limit=20,
        )
        by_ext = {str(item.get("file_ext")): item for item in stored}
        self.assertEqual(by_ext["java"]["output_format"], "java")
        self.assertEqual(by_ext["py"]["output_format"], "py")
        delivery = self.harness.generated_service.send_file(
            profile_user_id="alice",
            session_id="s1",
            target=str(by_ext["java"]["generated_handle"]),
        )
        self.assertTrue(delivery["ok"], delivery)
        self.assertEqual(delivery["files"][0]["name"], "Main.java")
        self.assertEqual(delivery["files"][0]["file_ext"], "java")

    def test_output_limit_is_not_silently_partial_and_workspace_is_cleaned(self) -> None:
        bridge = ExecutionResourceBridge(
            generated_file_service=self.harness.generated_service,
            workspace_root=self.harness.workspace,
            max_outputs=1,
        )
        run_id = "execrun_" + "3" * 32
        staged = bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["*.txt"],
        )
        run_dir = self.harness.workspace / staged["cwd_relpath"]
        (run_dir / "a.txt").write_text("a", encoding="utf-8")
        (run_dir / "b.txt").write_text("b", encoding="utf-8")
        result = bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "registration_failed")
        self.assertEqual(result["reason"], "output_file_limit_exceeded")
        self.assertEqual(result["generated_resources"], [])
        self.assertFalse(run_dir.exists())

    def test_no_outputs_and_failed_runs_cleanup_staging(self) -> None:
        no_output_run = "execrun_" + "4" * 32
        staged = self.harness.bridge.stage_inputs(
            run_id=no_output_run,
            owner=OWNER,
            input_resources=[],
            output_globs=[],
        )
        run_dir = self.harness.workspace / staged["cwd_relpath"]
        self.harness.bridge.register_outputs(run_id=no_output_run, owner=OWNER)
        self.assertFalse(run_dir.exists())

        failed_run = "execrun_" + "5" * 32
        staged = self.harness.bridge.stage_inputs(
            run_id=failed_run,
            owner=OWNER,
            input_resources=[],
            output_globs=["out.txt"],
        )
        run_dir = self.harness.workspace / staged["cwd_relpath"]
        result = self.harness.bridge.finalize_without_outputs(
            run_id=failed_run,
            owner=OWNER,
            reason="command_failed",
        )
        self.assertEqual(result["artifact_status"], "not_registered")
        self.assertEqual(result["reason"], "command_failed")
        self.assertFalse(run_dir.exists())

    def test_register_outputs_rejects_output_glob_escape(self) -> None:
        run_id = "execrun_" + "7" * 32
        staged = self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["../outside.txt"],
        )
        self.assertFalse(staged["ok"])
        result = self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "not_registered")
        self.assertNotEqual(result["ok"], True)

    def test_register_outputs_failure_when_command_never_created_output(self) -> None:
        run_id = "execrun_" + "8" * 32
        self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["outputs/result.wav"],
        )
        result = self.harness.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "registration_failed")
        self.assertEqual(result["reason"], "output_not_found")

    def test_register_outputs_owner_scoped(self) -> None:
        run_id = "execrun_" + "9" * 32
        self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["out.txt"],
        )
        other = ExecutionRunOwner(profile_user_id="bob", session_id="s9", provider_id="local")
        result = self.harness.bridge.register_outputs(run_id=run_id, owner=other)
        self.assertEqual(result["artifact_status"], "not_registered")
        self.assertEqual(result["reason"], "execution_resources_owner_mismatch")


class ExecRunResourceWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = _Harness(Path(self._tmp.name))
        save_approval_policy_config(
            base_dir=self.harness.root,
            profile_user_id="alice",
            payload={"defaultMode": "trusted_auto_allow"},
        )
        from companion_v01.execution_local import TrustedLocalExecutor

        self.provider = TrustedLocalExecutor(
            workspace_root=self.harness.workspace,
            run_log_dir=self.harness.root / "runlogs",
            provider_id="local",
        )
        self.handler = ExecRunToolHandler(
            execution_provider=self.provider,
            config_base_dir=self.harness.root,
            approval_store=None,
            resource_bridge=self.harness.bridge,
        )

    def test_exec_run_with_output_globs_registers_gen(self) -> None:
        call = {
            "type": "exec_run",
            "command": _python_command("open('result.txt','w').write('done')"),
            "initial_wait_seconds": 2,
            "output_globs": ["result.txt"],
        }
        result = self.handler.execute(call=call, context=_context())
        self.assertEqual(result.stream_events[0]["type"], "capability_execution_result")
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("生成资源", result.followup_context)
        self.assertIn("send_file", result.followup_context)
        state = result.state_updates.get("capability_execution", {})
        self.assertTrue(state.get("generated_resources"))
        self.assertEqual(state.get("artifact_status"), "registered")

    def test_resource_mode_rejects_cwd_instead_of_silently_ignoring_it(self) -> None:
        normalized = self.handler.normalize_call(
            {
                "type": "exec_run",
                "command": "echo hi",
                "cwd": "subdir",
                "output_globs": ["out.txt"],
            }
        )
        self.assertIsNone(normalized)

    def test_failed_command_finalizes_resource_workspace(self) -> None:
        result = self.handler.execute(
            call={
                "type": "exec_run",
                "command": "exit 2",
                "initial_wait_seconds": 2,
                "output_globs": ["out.txt"],
            },
            context=_context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["artifact_status"], "not_registered")
        run_id = str(state["run_id"])
        run_dir = self.harness.workspace / self.harness.bridge.run_cwd_relpath(run_id)
        self.assertFalse(run_dir.exists())

    def test_exec_run_stages_input_and_command_reads_it(self) -> None:
        item = self.harness.register_input(content="PING")
        call = {
            "type": "exec_run",
            "command": _python_command("import pathlib;print(pathlib.Path('inputs/source.txt').read_text())"),
            "initial_wait_seconds": 2,
            "input_resources": [{"handle": item["attachment_handle"], "as": "inputs/source.txt"}],
        }
        result = self.handler.execute(call=call, context=_context())
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("PING", result.followup_context)

    def test_exec_run_staging_rejection_never_runs_command(self) -> None:
        call = {
            "type": "exec_run",
            "command": "echo should-not-run",
            "input_resources": [{"handle": "file_404", "as": "x.txt"}],
        }
        result = self.handler.execute(call=call, context=_context())
        self.assertEqual(result.stream_events[0]["type"], "capability_execution_result")
        self.assertEqual(result.stream_events[0]["status"], "blocked")
        self.assertIn("资源暂存被拒绝", result.followup_context)

    def test_exec_run_without_bridge_is_unavailable(self) -> None:
        handler = ExecRunToolHandler(
            execution_provider=self.provider,
            config_base_dir=self.harness.root,
            resource_bridge=None,
        )
        item = self.harness.register_input()
        call = {
            "type": "exec_run",
            "command": "echo hi",
            "input_resources": [{"handle": item["attachment_handle"], "as": "x.txt"}],
        }
        result = handler.execute(call=call, context=_context())
        self.assertEqual(result.stream_events[0]["status"], "blocked")
        self.assertIn("execution_resources_unconfigured", result.stream_events[0]["reason"])


class ExecStatusResourceRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = _Harness(Path(self._tmp.name))
        from companion_v01.execution_local import TrustedLocalExecutor
        from companion_v01.tool_handlers.execution import ExecStatusToolHandler

        self.provider = TrustedLocalExecutor(
            workspace_root=self.harness.workspace,
            run_log_dir=self.harness.root / "runlogs",
            provider_id="local",
        )
        self.status_handler = ExecStatusToolHandler(
            execution_provider=self.provider,
            config_base_dir=self.harness.root,
            approval_store=None,
            resource_bridge=self.harness.bridge,
        )

    def test_exec_status_registers_outputs_once_for_long_task(self) -> None:
        run_id = "execrun_" + "abcdef0123456789abcdef0123456789"
        staged = self.harness.bridge.stage_inputs(
            run_id=run_id,
            owner=OWNER,
            input_resources=[],
            output_globs=["out.txt"],
        )
        self.assertTrue(staged["ok"])
        run_dir = self.harness.workspace / staged["cwd_relpath"]
        (run_dir / "out.txt").write_text("slow-done", encoding="utf-8")
        from companion_v01.execution_run import execute_exec_run

        command = _python_command(
            "import pathlib,time;time.sleep(0.3);"
            "pathlib.Path('out.txt').write_text('slow-done')"
        )
        mapped = execute_exec_run(
            self.provider,
            owner=OWNER,
            command=command,
            cwd=staged["cwd_relpath"],
            initial_wait_seconds=1,
            run_id=run_id,
        )
        self.assertIn(mapped.event_status, {"completed", "running"})

        from companion_v01.execution_specs import EXEC_STATUS_COMPLETED

        if mapped.event_status != EXEC_STATUS_COMPLETED:
            cursor = mapped.data.get("next_cursor")
            for _ in range(20):
                status = self.status_handler.execute(
                    call={"type": "exec_status", "run_id": run_id, "cursor": cursor},
                    context=_context(),
                )
                event_status = status.stream_events[0]["status"]
                if event_status == EXEC_STATUS_COMPLETED:
                    break
                cursor = status.state_updates["capability_execution"].get("next_cursor")
                import time as _time

                _time.sleep(0.1)

        first = self.status_handler.execute(
            call={"type": "exec_status", "run_id": run_id},
            context=_context(),
        )
        self.assertEqual(first.stream_events[0]["status"], EXEC_STATUS_COMPLETED)
        self.assertIn("已登记生成资源", first.followup_context)
        first_handles = [
            item.get("handle")
            for item in first.state_updates["capability_execution"].get("generated_resources", [])
        ]
        second = self.status_handler.execute(
            call={"type": "exec_status", "run_id": run_id},
            context=_context(),
        )
        second_handles = [
            item.get("handle")
            for item in second.state_updates["capability_execution"].get("generated_resources", [])
        ]
        self.assertEqual(first_handles, second_handles)
        self.assertEqual(len(first_handles), 1)

    def test_plain_run_status_does_not_claim_unknown_artifact_registration(self) -> None:
        class _CompletedProvider:
            provider_id = "local"

            def status(self, *, owner, run_id, cursor=None):
                return ExecRunStatus(status="completed", run_id=run_id, exit_code=0)

        from companion_v01.tool_handlers.execution import ExecStatusToolHandler

        handler = ExecStatusToolHandler(
            execution_provider=_CompletedProvider(),
            resource_bridge=self.harness.bridge,
        )
        result = handler.execute(
            call={"type": "exec_status", "run_id": "execrun_" + "6" * 32},
            context=_context(),
        )
        state = result.state_updates["capability_execution"]
        self.assertNotIn("artifact_status", state)
        self.assertNotIn("generated_resources", state)


if __name__ == "__main__":
    unittest.main()
