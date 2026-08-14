from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from companion_v01.capability_registry import (
    COMPOSE_FILE_TOOL_SPEC,
    REVISE_GENERATED_FILE_TOOL_SPEC,
    SEND_FILE_TOOL_SPEC,
)
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner
from companion_v01.execution_specs import (
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
)
from companion_v01.skill_runtime import SkillRegistry

ROOT = Path(__file__).resolve().parents[1]
SKILL_SOURCE = ROOT / "skills" / "coding-project"


def _python_command(code: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, "-c", code])
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


class CodingProjectSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.bundled = root / "bundled"
        managed = root / "managed"
        workspace = root / "workspace"
        for path in (self.bundled, managed, workspace):
            path.mkdir(parents=True, exist_ok=True)
        target = self.bundled / "coding-project"
        target.mkdir()
        (target / "SKILL.md").write_text(
            (SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.registry = SkillRegistry(
            bundled_root=self.bundled,
            managed_root=managed,
            execution_workspace_root=workspace,
        )

    def test_catalog_first_round_exposes_only_routing_metadata(self) -> None:
        catalog = self.registry.prompt_catalog()
        self.assertIn("coding-project", catalog)
        self.assertIn("executable program", catalog)
        self.assertIn("Skip for one-off short text", catalog)
        self.assertNotIn("Verification levels", catalog)
        self.assertNotIn("Honest ability check first", catalog)
        entry = self.registry.snapshot().by_name()["coding-project"]
        self.assertEqual(entry.source, "bundled")
        self.assertEqual(entry.execution_cwd, "alias:bundled_skills")

    def test_load_skill_returns_the_full_closed_loop(self) -> None:
        content = self.registry.load("coding-project").content
        for marker in (
            "Honest ability check first",
            "Scope an acceptable MVP and keep going",
            "Check the environment, never assume",
            "Read before editing; preserve real failure status",
            "Build incrementally, edit locally",
            "Keep long tasks observable",
            "Verification levels",
            "Web deliverables must match the deployment reality",
            "Delivery: queued is not received",
            "Failures drive the next step",
        ):
            self.assertIn(marker, content)
        self.assertIn("未运行验证", content)
        self.assertIn("$LASTEXITCODE", content)
        self.assertIn('$ErrorActionPreference = "Stop"', content)
        self.assertIn("node --check file.js", content)
        self.assertIn("python -m py_compile", content)
        self.assertIn("Pointer Lock", content)
        self.assertIn("已进入发送队列", content)
        self.assertIn("does not expose a\n  JavaScript console", content)
        self.assertIn("One failed command is evidence for the next step", content)
        self.assertIn("briefly tell the user the concrete next step", content)
        self.assertIn("Fix the root cause with the smallest coherent change", content)
        self.assertIn("Run the narrowest relevant check", content)

    def test_skill_declares_no_permission_upgrade_or_new_tools(self) -> None:
        content = self.registry.load("coding-project").content
        self.assertIn("not new tools and not a permission upgrade", content)
        self.assertIn("existing `exec_run`", content)
        from companion_v01.execution_specs import EXEC_TOOL_SPECS

        before = [spec.capability_id for spec in EXEC_TOOL_SPECS]
        self.registry.snapshot()
        after = [spec.capability_id for spec in EXEC_TOOL_SPECS]
        self.assertEqual(before, after)

    def test_hot_edit_takes_effect_next_snapshot(self) -> None:
        skill_dir = self.bundled / "coding-project"
        skill_path = skill_dir / "SKILL.md"
        original = skill_path.read_text(encoding="utf-8")
        try:
            skill_path.write_text(
                original.replace("Verification levels", "Verification tiers"),
                encoding="utf-8",
            )
            self.assertIn("Verification tiers", self.registry.load("coding-project").content)
        finally:
            skill_path.write_text(original, encoding="utf-8")
        self.assertIn("Verification levels", self.registry.load("coding-project").content)


class ToolDescriptionBoundaryTests(unittest.TestCase):
    def test_compose_file_spec_names_generation_vs_run_boundary(self) -> None:
        description = COMPOSE_FILE_TOOL_SPEC.description
        self.assertIn("返回成功只证明文件已生成，不证明内容可运行", description)
        self.assertIn("静态展示页", description)
        self.assertIn("coding-project Skill", description)

    def test_revise_spec_names_verification_boundary_and_local_edits(self) -> None:
        description = REVISE_GENERATED_FILE_TOOL_SPEC.description
        self.assertIn("不代表修改解决了运行问题", description)
        self.assertIn("局部修改", description)

    def test_exec_run_spec_guides_long_tasks_and_failure_propagation(self) -> None:
        description = EXEC_RUN_TOOL_SPEC.description
        self.assertIn("running 是命令仍在执行的正常状态", description)
        self.assertIn("抑制或缓冲实时输出", description)
        self.assertIn("按当前 Shell 显式保留并检查每一步失败状态", description)
        self.assertIn("不是 Shell 沙箱", description)
        self.assertIn("当前宿主用户权限", description)

    def test_send_file_spec_keeps_queue_vs_receipt_distinction(self) -> None:
        description = SEND_FILE_TOOL_SPEC.description
        self.assertIn("客户端投递队列", description)
        self.assertIn("不会生成、修改或转码文件", description)


class CodingLoopSmokeTests(unittest.TestCase):
    """Real subprocess loop in a throwaway workspace, never the user project."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()
        self.run_log_dir = Path(self._tmp.name) / "runlogs"
        self.owner = ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local")
        self.executor = TrustedLocalExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            provider_id="local",
        )

    def _run(self, command: str, *, cwd: str = "", initial_wait_seconds: int = 3, timeout_seconds: int = 30) -> object:
        return self.executor.run(
            owner=self.owner,
            command=command,
            cwd=cwd,
            initial_wait_seconds=initial_wait_seconds,
            timeout_seconds=timeout_seconds,
        )

    def test_smoke_a_syntax_error_loop_ends_with_real_success(self) -> None:
        project = self.workspace / "tiny"
        project.mkdir()
        (project / "hello.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        check = _python_command("import py_compile; py_compile.compile('hello.py', doraise=True)")
        first = self._run(check, cwd="tiny", initial_wait_seconds=5)
        self.assertEqual(first.status, EXEC_STATUS_FAILED, first)
        self.assertIn("SyntaxError", first.stdout + first.stderr)
        (project / "hello.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
        second = self._run(check, cwd="tiny", initial_wait_seconds=5)
        self.assertEqual(second.status, EXEC_STATUS_COMPLETED, second)
        self.assertEqual(second.exit_code, 0)

    def test_smoke_b_long_task_reports_running_then_terminal_with_full_output(self) -> None:
        from companion_v01.execution_run import execute_exec_status

        command = _python_command(
            "import time,sys;"
            "[(sys.stdout.write(f'line {i}\\n'), sys.stdout.flush(), time.sleep(0.15)) for i in range(30)]"
        )
        start = self._run(command, initial_wait_seconds=1, timeout_seconds=120)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING, start)
        self.assertTrue(start.run_id)
        waited = execute_exec_status(
            self.executor,
            owner=self.owner,
            run_id=start.run_id,
            cursor=start.next_cursor,
            wait_seconds=30,
        )
        self.assertEqual(waited.event_status, EXEC_STATUS_COMPLETED, waited.data)
        self.assertIn("line 29", str(waited.model_feedback or "") + str(waited.data or {}))
        self.assertIn("line ", str(waited.data.get("tail") or ""))

    def test_smoke_b_cancel_returns_real_cancelled_confirmation(self) -> None:
        command = _python_command(
            "import time,sys;"
            "[(sys.stdout.write(f'line {i}\\n'), sys.stdout.flush(), time.sleep(0.15)) for i in range(400)]"
        )
        start = self._run(command, initial_wait_seconds=1, timeout_seconds=120)
        self.assertEqual(start.status, EXEC_STATUS_RUNNING, start)
        cancelled = self.executor.cancel(owner=self.owner, run_id=start.run_id)
        self.assertEqual(cancelled.status, EXEC_STATUS_CANCELLED, cancelled)
        final = self.executor.status(owner=self.owner, run_id=start.run_id)
        self.assertEqual(final.status, EXEC_STATUS_CANCELLED)

    def test_smoke_c_compound_command_masks_failure_then_propagates_it(self) -> None:
        fail = _python_command("import sys;sys.exit(3)")
        ok = _python_command("print('last-step-ok')")
        if os.name == "nt":
            masked = self._run(f"{fail} & {ok}", initial_wait_seconds=5)
            propagated = self._run(f"{fail} && {ok}", initial_wait_seconds=5)
        else:
            masked = self._run(f"{fail}; {ok}", initial_wait_seconds=5)
            propagated = self._run(f"set -e; {fail}; {ok}", initial_wait_seconds=5)
        self.assertEqual(masked.status, EXEC_STATUS_COMPLETED, masked)
        self.assertEqual(masked.exit_code, 0)
        self.assertIn("last-step-ok", masked.stdout)
        self.assertEqual(propagated.status, EXEC_STATUS_FAILED, propagated)
        self.assertEqual(propagated.exit_code, 3)

    @unittest.skipUnless(shutil.which("node"), "node is not installed on this host")
    def test_smoke_d_web_file_javascript_syntax_check_loop(self) -> None:
        project = self.workspace / "web"
        project.mkdir()
        (project / "app.js").write_text("const broken = (;\n", encoding="utf-8")
        node = shutil.which("node") or "node"
        if os.name == "nt":
            check = subprocess.list2cmdline([node, "--check", "app.js"])
        else:
            check = f"{shlex.quote(node)} --check app.js"
        first = self._run(check, cwd="web", initial_wait_seconds=5)
        self.assertEqual(first.status, EXEC_STATUS_FAILED, first)
        self.assertIn("SyntaxError", first.stdout + first.stderr)
        (project / "app.js").write_text("const ok = () => 1;\n", encoding="utf-8")
        second = self._run(check, cwd="web", initial_wait_seconds=5)
        self.assertEqual(second.status, EXEC_STATUS_COMPLETED, second)

    def test_smoke_e_output_globs_register_gen_then_enter_delivery_queue(self) -> None:
        from companion_v01.attachment_inbox import AttachmentInboxService
        from companion_v01.execution_resources import ExecutionResourceBridge
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01.local_capability_config import save_approval_policy_config
        from companion_v01.store import MemoryStore
        from companion_v01.tool_handlers.execution import ExecRunToolHandler
        from companion_v01.tool_runtime import ToolExecutionContext

        root = Path(self._tmp.name)
        managed = root / "managed"
        store = MemoryStore(root / "db")
        attachment_service = AttachmentInboxService(store=store, base_dir=root)
        generated_service = GeneratedFileService(
            base_dir=managed,
            store=store,
            attachment_service=attachment_service,
        )
        bridge = ExecutionResourceBridge(
            generated_file_service=generated_service,
            workspace_root=self.workspace,
        )
        save_approval_policy_config(
            base_dir=root,
            profile_user_id="alice",
            payload={"defaultMode": "trusted_auto_allow"},
        )
        handler = ExecRunToolHandler(
            execution_provider=self.executor,
            config_base_dir=root,
            approval_store=None,
            resource_bridge=bridge,
        )
        context = ToolExecutionContext(
            profile_user_id="alice",
            session_id="s1",
            now_ts=0,
            visual_payload={},
            client_mode="qq",
        )
        result = handler.execute(
            call={
                "type": "exec_run",
                "command": _python_command("open('snake.py','w').write('print(1)')"),
                "initial_wait_seconds": 2,
                "output_globs": ["snake.py"],
            },
            context=context,
        )
        state = result.state_updates["capability_execution"]
        self.assertEqual(state["status"], "completed", result.followup_context)
        self.assertEqual(state["artifact_status"], "registered")
        handle = state["generated_resources"][0]["handle"]
        self.assertTrue(handle.startswith("gen_"))
        delivery = generated_service.send_file(
            profile_user_id="alice",
            session_id="s1",
            target=handle,
            targets=[handle],
            timestamp=1,
        )
        self.assertTrue(delivery["ok"], delivery)
        self.assertTrue(delivery["send_to_user"])
        self.assertIn("投递队列", delivery["followup_context"])
        self.assertIn("不能说用户已经收到", delivery["followup_context"])


if __name__ == "__main__":
    unittest.main()
