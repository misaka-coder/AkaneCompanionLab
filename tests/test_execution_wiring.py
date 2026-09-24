from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from capcore import PermissionDecision

from companion_v01.capability_registry import CapabilitySelection, ExecutorBroker
from companion_v01.engine import AkaneMemoryEngine, FINAL_PROMPT_CACHE_LAYOUT_VERSION
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import (
    ExecutionAvailability,
    ExecutionRunOwner,
    ExecRunStart,
    execute_exec_run,
    make_cursor,
    new_run_id,
)
from companion_v01.execution_specs import EXEC_STATUS_RUNNING
from companion_v01.capcore_runtime import manual_permission_request, resolve_permission_for_profile
from companion_v01.local_capability_config import (
    get_approval_policy_config,
    load_capability_config,
    save_capability_approval_mode,
    save_capability_approval_modes,
)
from companion_v01.tool_handlers.catalog import build_builtin_tool_handlers
from companion_v01.tool_handlers.execution import (
    ExecCancelToolHandler,
    ExecRunToolHandler,
    ExecStatusToolHandler,
)
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import build_native_tool_decision_plan, execute_tool_invocation
from companion_v01.tool_runtime import ToolExecutionContext


def _context(
    *,
    profile_user_id: str = "alice",
    session_id: str = "s1",
    actor_stable_id: str = "",
    actor_profile_user_id: str = "",
) -> ToolExecutionContext:
    request_context = {}
    if actor_stable_id:
        request_context["actor_stable_id"] = actor_stable_id
    if actor_profile_user_id:
        request_context["actor_profile_user_id"] = actor_profile_user_id
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode="qq",
        request_context=request_context,
    )


def _python_command(code: str) -> str:
    if os.name == "nt":
        executable = str(sys.executable).replace("'", "''")
        script = str(code).replace("'", "''")
        return f"& '{executable}' -c '{script}'"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _sleep_command(seconds: int) -> str:
    return _python_command(f"import time; time.sleep({seconds})")


class ExecHandlerPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name)
        self.workspace = self.base_dir / "workspace"
        self.workspace.mkdir()
        self.run_log_dir = self.base_dir / "runlogs"
        self.provider = TrustedLocalExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            provider_id="local",
        )
        from tests.test_execution_local import _shutdown_executor
        self.addCleanup(_shutdown_executor, self.provider)

    def _handler(self, *, config_base_dir=None, provider=None):
        return ExecRunToolHandler(
            execution_provider=provider if provider is not None else self.provider,
            config_base_dir=config_base_dir if config_base_dir is not None else self.base_dir,
        )

    def _set_policy(self, mode: str) -> None:
        result = save_capability_approval_modes(
            base_dir=self.base_dir,
            profile_user_id="alice",
            modes={"ops": mode, "extensions": mode},
        )
        self.assertTrue(result.get("ok"), result)

    def test_exec_run_asks_by_default(self) -> None:
        handler = self._handler()
        result = handler.execute(call={"type": "exec_run", "command": "echo hi"}, context=_context())
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertIn("[approval required", result.followup_context)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "approval_required")

    def test_group_member_approval_explains_actor_scoped_host_permission(self) -> None:
        handler = self._handler()
        result = handler.execute(
            call={"type": "exec_run", "command": "Get-ChildItem -Recurse"},
            context=_context(
                profile_user_id="qq_group_shared_20001",
                session_id="qq_group_shared_20001",
                actor_stable_id="qq:10003",
                actor_profile_user_id="qq_user_10003",
            ),
        )

        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertEqual(
            result.stream_events[0]["approvalReason"],
            "group_actor_host_execution_requires_confirmation",
        )
        self.assertIn("当前群成员没有自动执行宿主命令的授权", result.followup_context)
        self.assertIn("设备主人", result.followup_context)

    def test_exec_run_blocked_when_policy_disabled(self) -> None:
        handler = self._handler()
        denied = PermissionDecision(
            allowed=False,
            requires_user_decision=False,
            mode="disabled",
            reason="capability_disabled_by_policy",
        )
        with patch.object(handler, "_permission_decision", return_value=denied):
            result = handler.execute(call={"type": "exec_run", "command": "echo hi"}, context=_context())
        self.assertEqual(result.stream_events[0]["type"], "capability_execution_result")
        self.assertEqual(result.stream_events[0]["status"], "blocked")
        self.assertIn("已被当前能力策略阻止", result.followup_context)

    def test_exec_run_executes_under_trusted_auto_allow(self) -> None:
        self._set_policy("trusted_auto_allow")
        handler = self._handler()
        result = handler.execute(
            call={"type": "exec_run", "command": "echo hi", "initial_wait_seconds": 3},
            context=_context(),
        )
        self.assertEqual(result.stream_events[0]["type"], "capability_execution_result")
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("hi", result.followup_context)
        self.assertIn("[exit code: 0", result.followup_context)
        self.assertIsNotNone(result.followup_envelope)
        self.assertTrue(result.followup_envelope.producer_bounded)
        self.assertTrue(result.state_updates["capability_execution"]["run_id"].startswith("execrun_"))
        self.assertEqual(result.state_updates["capability_execution"]["output_ref"].startswith("runlog:"), True)
        self.assertEqual(
            Path(result.state_updates["capability_execution"]["effective_cwd"]),
            self.workspace.resolve(),
        )
        self.assertEqual(result.state_updates["capability_execution"]["workspace_id"], "")

    def test_exec_run_uses_same_run_id_for_durable_lifecycle_tracking(self) -> None:
        self._set_policy("trusted_auto_allow")
        observed = {}

        class JobRuntime:
            @staticmethod
            def accepts(_context):
                return True

            @staticmethod
            def begin(**kwargs):
                observed["begin"] = kwargs
                return {"ok": True, "tracked": True}

            @staticmethod
            def observe_start(run_id, **kwargs):
                observed["start"] = {"run_id": run_id, **kwargs}
                return {"ok": True}

        handler = self._handler()
        handler.bind_job_runtime(JobRuntime())
        result = handler.execute(
            call={"type": "exec_run", "command": "echo hi", "initial_wait_seconds": 3},
            context=_context(),
        )

        run_id = result.state_updates["capability_execution"]["run_id"]
        self.assertEqual(observed["begin"]["run_id"], run_id)
        self.assertEqual(observed["start"]["run_id"], run_id)
        self.assertEqual(observed["start"]["status"], "completed")

    def test_exec_run_schema_and_handler_do_not_apply_a_private_command_length_limit(self) -> None:
        self._set_policy("trusted_auto_allow")
        handler = self._handler()
        command = "echo " + ("x" * 9000)
        normalized = handler.normalize_call({"type": "exec_run", "command": command})

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["command"], command)
        self.assertNotIn("maxLength", handler.tool_spec().input_schema["properties"]["command"])

        captured = {}

        class Provider:
            def availability(self):
                return ExecutionAvailability(enabled=True, status="ready")

            def run(self, **kwargs):
                captured.update(kwargs)
                return ExecRunStart(
                    status="completed",
                    run_id="execrun_" + "a" * 32,
                    exit_code=0,
                    stdout="ok",
                )

        mapped = execute_exec_run(
            Provider(),
            owner=ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local"),
            command=command,
        )
        self.assertEqual(mapped.event_status, "completed")
        self.assertEqual(captured["command"], command)

    def test_exec_prompt_is_compact_and_defers_exact_versions_to_real_probes(self) -> None:
        instruction = self._handler().build_prompt_instruction()

        self.assertIn("上方宿主事实", instruction)
        self.assertIn("Start-Process", instruction)
        self.assertIn("验证端口或进程", instruction)
        self.assertNotIn("toolchain=", instruction)
        self.assertNotIn("unavailable", instruction)
        self.assertNotIn(str(self.base_dir), instruction)
        self.assertLess(len(instruction), 600)

    def test_native_and_legacy_tool_context_share_one_execution_host_block(self) -> None:
        provider = SimpleNamespace(
            model_environment=lambda: {
                "platform": "linux",
                "command_shell": "/bin/sh",
                "preferred_script_shell": "/bin/bash",
                "toolchain": {"node": {"status": "unavailable", "version": ""}},
                "host_access": {
                    "filesystem": "host_user_permissions",
                    "absolute_cwd": "supported",
                },
                "dependency_storage": {"runtime": "host_path"},
            }
        )
        handler = ExecRunToolHandler(execution_provider=provider, config_base_dir=self.base_dir)
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("exec_run",),
            module_names=(),
            resolved_handlers={"exec_run": handler},
        )
        fake_engine = SimpleNamespace(
            _resolve_tool_handlers=lambda **_kwargs: {"exec_run": handler},
            _build_execution_host_context=AkaneMemoryEngine._build_execution_host_context,
            _build_loadable_capability_catalog=lambda **_kwargs: ("", []),
        )

        native = AkaneMemoryEngine._build_tool_prompt_context(
            fake_engine,
            allow_tool_call=True,
            exclude_tool_types={"exec_run"},
            capability_selection=selection,
            include_capability_status=False,
        )
        legacy = AkaneMemoryEngine._build_tool_prompt_context(
            fake_engine,
            allow_tool_call=True,
            capability_selection=selection,
            include_capability_status=False,
        )

        for value in (native, legacy):
            self.assertEqual(value.count("Execution environment:"), 1)
            self.assertEqual(value.count("working_directory:"), 1)
            self.assertIn("platform: linux", value)
            self.assertIn("shell: /bin/sh", value)
            self.assertIn("不依赖桌宠窗口在线", value)
            self.assertIn("停止自己启动的命令用 exec_cancel(run_id)", value)
            self.assertIn("不要把桌面设备的 PID 直接用于宿主命令", value)
            self.assertNotIn("effective_cwd", value)
            self.assertNotIn("toolchain=", value)
            self.assertNotIn("node=unavailable", value)
        self.assertNotIn("- exec_run：", native)
        self.assertIn("- exec_run：", legacy)

    def test_execution_host_context_ignores_volatile_diagnostics_and_cache_layout_is_unchanged(self) -> None:
        stable = {
            "platform": "windows",
            "command_shell": "pwsh",
            "preferred_script_shell": "pwsh",
            "host_access": {"filesystem": "host_user_permissions", "absolute_cwd": "supported"},
            "dependency_storage": {"runtime": "host_path"},
        }
        first = SimpleNamespace(
            execution_provider=SimpleNamespace(
                prompt_environment=lambda: {
                    **stable,
                    "toolchain": {"node": {"version": "22.1.0"}},
                    "diagnostic_timestamp": 1,
                }
            )
        )
        second = SimpleNamespace(
            execution_provider=SimpleNamespace(
                prompt_environment=lambda: {
                    **stable,
                    "toolchain": {"node": {"version": "99.0.0"}},
                    "diagnostic_timestamp": 999,
                }
            )
        )

        self.assertEqual(
            AkaneMemoryEngine._build_execution_host_context(first),
            AkaneMemoryEngine._build_execution_host_context(second),
        )
        self.assertNotIn("22.1.0", AkaneMemoryEngine._build_execution_host_context(first))
        self.assertEqual(FINAL_PROMPT_CACHE_LAYOUT_VERSION, "responses-unified-timeline-v3")

    def test_execution_host_context_discloses_only_configured_credential_references(self) -> None:
        handler = SimpleNamespace(
            execution_provider=SimpleNamespace(
                prompt_environment=lambda: {
                    "platform": "linux",
                    "command_shell": "/bin/sh",
                    "preferred_script_shell": "/bin/bash",
                    "host_access": {
                        "filesystem": "host_user_permissions",
                        "absolute_cwd": "supported",
                        "credential_env_refs": {
                            "GITHUB_TOKEN": "configured",
                            "OPTIONAL_TOKEN": "missing",
                        },
                    },
                    "dependency_storage": {"runtime": "host_path"},
                }
            )
        )

        context = AkaneMemoryEngine._build_execution_host_context(handler)

        self.assertIn("GITHUB_TOKEN=configured", context)
        self.assertIn("OPTIONAL_TOKEN=missing", context)
        self.assertIn("真实值由执行器注入并从输出中遮蔽", context)
        self.assertNotIn("secret-value", context)

    def test_execution_host_context_appends_prompt_safe_current_working_directory(self) -> None:
        handler = SimpleNamespace(
            execution_provider=SimpleNamespace(
                prompt_environment=lambda: {
                    "platform": "linux",
                    "command_shell": "/bin/sh",
                    "preferred_script_shell": "/bin/bash",
                    "host_access": {"filesystem": "host_user_permissions"},
                }
            ),
            working_directory_context=lambda **_kwargs: {
                "working_directory": "/workspace/compiler-lab",
                "project": "Compiler Lab",
                "workspace_id": "proj_" + "a" * 32,
            },
        )

        context = AkaneMemoryEngine._build_execution_host_context(
            handler,
            profile_user_id="alice",
            session_id="session-a",
            client_mode="qq_text",
        )

        self.assertIn("working_directory: \"/workspace/compiler-lab\"", context)
        self.assertIn('project: "Compiler Lab"', context)
        self.assertIn("workspace_id: proj_", context)
        self.assertNotIn("/var/lib", context)

    def test_exec_run_capability_override_does_not_unlock_other_high_risk_tools(self) -> None:
        saved = save_capability_approval_mode(
            base_dir=self.base_dir,
            profile_user_id="alice",
            capability_id="tool.exec_run",
            mode="trusted_auto_allow",
        )
        self.assertTrue(saved.get("ok"), saved)
        policy = get_approval_policy_config(base_dir=self.base_dir, profile_user_id="alice")["approvalPolicy"]
        self.assertNotIn("defaultMode", policy)
        self.assertNotIn("capabilityModes", policy)

        exec_result = self._handler().execute(
            call={"type": "exec_run", "command": "echo hi", "initial_wait_seconds": 3},
            context=_context(),
        )
        self.assertEqual(exec_result.stream_events[0]["status"], "completed")

        other_request = manual_permission_request(
            context=_context(),
            required=True,
            capability_id="another_high_risk_tool",
            display_name="Another high risk tool",
            risk="high",
            confirm="always",
            effects=("external_effect",),
            reason="test",
        )
        other_decision = resolve_permission_for_profile(
            other_request,
            base_dir=self.base_dir,
            profile_user_id="alice",
        )
        self.assertFalse(other_decision.allowed)
        self.assertTrue(other_decision.requires_user_decision)

    def test_family_policy_save_preserves_exact_override(self) -> None:
        save_capability_approval_mode(
            base_dir=self.base_dir,
            profile_user_id="alice",
            capability_id="exec_run",
            mode="disabled",
        )
        saved = save_capability_approval_modes(
            base_dir=self.base_dir,
            profile_user_id="alice",
            modes={"ops": "trusted_auto_allow", "extensions": "trusted_auto_allow"},
        )
        self.assertTrue(saved.get("ok"), saved)
        stored = load_capability_config(base_dir=self.base_dir, profile_user_id="alice")["approvalPolicy"]
        self.assertEqual(stored["capabilityModes"]["exec_run"], "disabled")

    def test_exec_run_unconfigured_provider_is_unavailable(self) -> None:
        handler = ExecRunToolHandler(execution_provider=None, config_base_dir=self.base_dir)
        result = handler.execute(call={"type": "exec_run", "command": "echo hi"}, context=_context())
        self.assertEqual(result.stream_events[0]["status"], "unavailable")
        self.assertIn("capability_unavailable", result.followup_context)

    def test_exec_run_normalize_call(self) -> None:
        handler = self._handler()
        self.assertIsNotNone(handler.normalize_call({"type": "exec_run", "command": "echo hi"}))
        self.assertEqual(
            handler.normalize_call({"type": "exec_run", "cmd": "echo hi"}),
            {"type": "exec_run", "command": "echo hi", "cwd": ""},
        )
        self.assertIsNone(
            handler.normalize_call({"type": "exec_run", "command": "echo safe", "cmd": "echo different"})
        )
        self.assertIsNone(handler.normalize_call({"type": "exec_run"}))
        self.assertIsNone(handler.normalize_call({"type": "other", "command": "echo hi"}))
        normalized = handler.normalize_call(
            {"type": "exec_run", "command": "echo hi", "cwd": "sub", "timeout_seconds": 30, "initial_wait_seconds": 3}
        )
        self.assertEqual(normalized["timeout_seconds"], 30)
        self.assertEqual(normalized["initial_wait_seconds"], 3)

    def test_exec_prompt_does_not_confuse_cwd_contract_with_shell_file_access(self) -> None:
        instruction = self._handler().build_prompt_instruction()

        self.assertIn("不是 Shell 沙箱", instruction)
        self.assertIn("真实宿主绝对目录", instruction)
        self.assertIn("真实输出发现路径", instruction)
        self.assertIn("input_resources", instruction)
        self.assertIn("不能与 cwd 同用", instruction)
        self.assertIn("output_globs", instruction)
        self.assertIn("alias:project", instruction)
        self.assertIn("上方宿主事实", instruction)
        self.assertIn("改变大量文件前先只读核对目标", instruction)
        self.assertNotIn("manage_project_workspace(open)", instruction)
        self.assertNotIn("TMPDIR", instruction)
        self.assertNotIn("send_file", instruction)

    def test_exec_status_and_cancel_are_owner_scoped_without_ask(self) -> None:
        status_handler = ExecStatusToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir)
        cancel_handler = ExecCancelToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir)
        unknown_run = "execrun_" + "0" * 32
        status_result = status_handler.execute(
            call={"type": "exec_status", "run_id": unknown_run},
            context=_context(),
        )
        self.assertEqual(status_result.stream_events[0]["status"], "unknown")
        cancel_result = cancel_handler.execute(
            call={"type": "exec_cancel", "run_id": unknown_run},
            context=_context(),
        )
        self.assertEqual(cancel_result.stream_events[0]["status"], "unknown")

    def test_group_exec_run_is_scoped_to_the_member_who_started_it(self) -> None:
        from companion_v01.execution_run import ExecutionRunOwner

        group_session = "qq_group_shared_872732158"
        alice_owner = ExecutionRunOwner(
            profile_user_id="alice",
            session_id=f"{group_session}\x1fqq:10001",
            provider_id="local",
        )
        start = self.provider.run(
            owner=alice_owner,
            command=_python_command("print('owned')"),
            initial_wait_seconds=3,
        )
        self.assertEqual(start.status, "completed")
        status_handler = ExecStatusToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir)

        other_member = status_handler.execute(
            call={"type": "exec_status", "run_id": start.run_id},
            context=_context(session_id=group_session, actor_stable_id="qq:10002"),
        )
        same_member = status_handler.execute(
            call={"type": "exec_status", "run_id": start.run_id},
            context=_context(session_id=group_session, actor_stable_id="qq:10001"),
        )

        self.assertEqual(other_member.stream_events[0]["status"], "unknown")
        self.assertEqual(same_member.stream_events[0]["status"], "completed")

    def test_exec_status_normalizes_bounded_wait(self) -> None:
        handler = ExecStatusToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir)
        self.assertEqual(
            handler.normalize_call(
                {
                    "type": "exec_status",
                    "run_id": "execrun_" + "0" * 32,
                    "cursor": "cursor",
                    "wait_seconds": 30,
                }
            ),
            {
                "type": "exec_status",
                "run_id": "execrun_" + "0" * 32,
                "cursor": "cursor",
                "wait_seconds": 30,
            },
        )
        self.assertIsNone(
            handler.normalize_call(
                {"type": "exec_status", "run_id": "execrun_" + "0" * 32, "wait_seconds": "later"}
            )
        )

    def test_exec_status_continuation_reads_rest(self) -> None:
        from companion_v01.execution_run import ExecutionRunOwner

        start = self.provider.run(
            owner=ExecutionRunOwner(profile_user_id="alice", session_id="s1", provider_id="local"),
            command=_python_command("import sys; sys.stdout.write('x' * 60000)"),
            initial_wait_seconds=3,
        )
        self.assertEqual(start.status, "completed")
        self.assertIsNotNone(start.next_cursor)
        handler = ExecStatusToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir)
        result = handler.execute(
            call={"type": "exec_status", "run_id": start.run_id, "cursor": start.next_cursor},
            context=_context(),
        )
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("x", result.followup_context)
        self.assertTrue(len(result.followup_context) > 8000)


class ExecCatalogRegistrationTests(unittest.TestCase):
    def test_catalog_registers_complete_project_tool_surface_with_shared_service(self) -> None:
        from companion_v01.project_workspace import ProjectWorkspaceService
        from companion_v01.store import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            workspace.mkdir()
            provider = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=base / "runlogs")
            service = ProjectWorkspaceService(
                store=MemoryStore(base / "data"),
                execution_workspace_root=workspace,
            )
            handlers = build_builtin_tool_handlers(
                store=None,
                sticker_assets=None,
                capability_offer_source=None,
                capability_config_base_dir=None,
                memory_timeline_service=None,
                context_libraries=None,
                attachment_service=None,
                image_material_resolver=None,
                workspace_file_service=None,
                attachment_ingest_service=None,
                generated_file_service=None,
                retrieve_fn=None,
                execution_provider=provider,
                project_workspace_service=service,
            )

            self.assertEqual(
                [name for name in handlers if name in {"manage_project_workspace", "project_inspect", "workspace_write", "workspace_patch"}],
                ["manage_project_workspace", "project_inspect", "workspace_write", "workspace_patch"],
            )
            self.assertTrue(handlers["project_inspect"].tool_metadata().is_read_only)
            self.assertIs(handlers["project_inspect"].service, service)
            self.assertIs(handlers["workspace_patch"].service, service)

    def test_catalog_registers_exec_handlers_when_provider_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            capability_config_base_dir = Path(tmp) / "users_data"
            provider = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=Path(tmp) / "runlogs")
            handlers = build_builtin_tool_handlers(
                store=None,
                sticker_assets=None,
                capability_offer_source=None,
                capability_config_base_dir=capability_config_base_dir,
                memory_timeline_service=None,
                context_libraries=None,
                attachment_service=None,
                image_material_resolver=None,
                workspace_file_service=None,
                attachment_ingest_service=None,
                generated_file_service=None,
                retrieve_fn=None,
                execution_provider=provider,
            )
            self.assertIn("exec_run", handlers)
            self.assertIn("exec_status", handlers)
            self.assertIn("exec_cancel", handlers)
            self.assertIn("exec_input", handlers)
            self.assertFalse(handlers["exec_input"].tool_metadata().is_read_only)
            self.assertEqual(handlers["exec_input"].tool_spec().capability_id, "exec_input")
            self.assertEqual(handlers["exec_run"].capability_status()["status"], "ready")
            self.assertEqual(handlers["open_browser"].capability_status()["status"], "unavailable")
            self.assertEqual(handlers["exec_run"].tool_metadata().family, "execution")
            self.assertEqual(handlers["exec_run"].tool_metadata().risk, "high")
            self.assertTrue(handlers["exec_run"].tool_metadata().requires_confirmation)
            self.assertTrue(handlers["exec_status"].tool_metadata().is_read_only)
            self.assertFalse(handlers["exec_cancel"].tool_metadata().is_read_only)
            self.assertTrue(handlers["exec_run"].policy_accepted_native_tool)
            self.assertTrue(handlers["exec_status"].policy_accepted_native_tool)
            self.assertTrue(handlers["exec_cancel"].policy_accepted_native_tool)
            self.assertEqual(handlers["browser_page"].config_base_dir, capability_config_base_dir)

    def test_catalog_omits_exec_handlers_without_provider(self) -> None:
        handlers = build_builtin_tool_handlers(
            store=None,
            sticker_assets=None,
            capability_offer_source=None,
            capability_config_base_dir=None,
            memory_timeline_service=None,
            context_libraries=None,
            attachment_service=None,
            image_material_resolver=None,
            workspace_file_service=None,
            attachment_ingest_service=None,
            generated_file_service=None,
            retrieve_fn=None,
        )
        self.assertNotIn("exec_run", handlers)
        self.assertNotIn("exec_status", handlers)
        self.assertNotIn("exec_cancel", handlers)
        self.assertNotIn("exec_input", handlers)

    def test_exec_handlers_are_advertised_through_the_native_schema(self) -> None:
        handlers = {
            "exec_run": ExecRunToolHandler(execution_provider=object()),
            "exec_status": ExecStatusToolHandler(execution_provider=object()),
            "exec_cancel": ExecCancelToolHandler(execution_provider=object()),
        }
        with patch("companion_v01.tool_orchestration_engine.config.ENABLE_NATIVE_TOOL_DECISION", True):
            plan = build_native_tool_decision_plan(
                handlers,
                allow_tool_call=True,
                provider_supports_native_tools=True,
                allowed_tool_names=handlers,
            )

        self.assertEqual(plan.status, "enabled")
        schemas = {item["function"]["name"]: item["function"] for item in plan.tools}
        self.assertEqual(set(schemas), set(handlers))
        self.assertEqual(schemas["exec_run"]["parameters"]["required"], ["command"])
        self.assertIn("不是 cmd", schemas["exec_run"]["parameters"]["properties"]["command"]["description"])


class ExecDispatchEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name)
        self.workspace = self.base_dir / "workspace"
        self.workspace.mkdir()
        self.run_log_dir = self.base_dir / "runlogs"
        self.provider = TrustedLocalExecutor(
            workspace_root=self.workspace,
            run_log_dir=self.run_log_dir,
            provider_id="local",
        )
        from tests.test_execution_local import _shutdown_executor
        self.addCleanup(_shutdown_executor, self.provider)
        save_capability_approval_modes(
            base_dir=self.base_dir,
            profile_user_id="alice",
            modes={"ops": "trusted_auto_allow", "extensions": "trusted_auto_allow"},
        )
        self.handlers = {
            "exec_run": ExecRunToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir),
            "exec_status": ExecStatusToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir),
            "exec_cancel": ExecCancelToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir),
        }
        self.engine = SimpleNamespace(
            _resolve_tool_handlers=lambda **kw: self.handlers,
            executor_broker=ExecutorBroker(None),
        )
        self.selection = CapabilitySelection(
            light_hints=(),
            tool_names=("exec_run", "exec_status", "exec_cancel"),
            module_names=("exec",),
            schema_tool_names=("exec_run", "exec_status", "exec_cancel"),
        )

    def _invocation(self, name: str, arguments: dict) -> ToolInvocation:
        return ToolInvocation(
            name=name,
            arguments=arguments,
            source="native_openai",
            id="inv_1",
            capability_selection=self.selection,
        )

    def _dispatch(self, invocation: ToolInvocation):
        return execute_tool_invocation(
            self.engine,
            invocation=invocation,
            profile_user_id="alice",
            session_id="s1",
            character_pack_id="",
            visual_payload={},
            now_ts=0,
        )

    def test_envelope_ok_for_completed_command(self) -> None:
        result, envelope = self._dispatch(self._invocation("exec_run", {"command": "echo hi", "initial_wait_seconds": 3}))
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("run_id", envelope.data["state_updates"]["capability_execution"])

    def test_exec_input_uses_normal_dispatch_and_owner_scope(self) -> None:
        from dataclasses import replace
        from companion_v01.execution_run import ExecutionRunOwner, execute_exec_status
        from companion_v01.tool_handlers.execution import ExecInputToolHandler
        self.handlers["exec_input"] = ExecInputToolHandler(execution_provider=self.provider, config_base_dir=self.base_dir)
        self.selection = replace(self.selection, tool_names=(*self.selection.tool_names, "exec_input"),
                                 schema_tool_names=(*self.selection.schema_tool_names, "exec_input"))
        owner = ExecutionRunOwner("alice", "s1", "local")
        started = self.provider.run(owner=owner, command=_python_command("print(input(),flush=True)"), interactive=True,
                                    initial_wait_seconds=3, timeout_seconds=15)
        try:
            result, envelope = self._dispatch(self._invocation("exec_input", {"run_id":started.run_id, "action":"write",
                "sequence":1, "text":"through-dispatch\n", "close":True}))
            self.assertEqual(envelope.status, "ok", envelope.model_feedback)
            status = execute_exec_status(self.provider, owner=owner, run_id=started.run_id, wait_seconds=5)
            self.assertEqual(status.event_status, "completed", status.model_feedback)
            self.assertIn("through-dispatch", status.model_feedback)
        finally:
            self.provider.cancel(owner=owner, run_id=started.run_id)

    def test_envelope_preserves_bounded_output_continuation(self) -> None:
        result, envelope = self._dispatch(
            self._invocation(
                "exec_run",
                {
                    "command": _python_command("import sys; sys.stdout.write('x' * 60000)"),
                    "initial_wait_seconds": 3,
                },
            )
        )
        self.assertIsNotNone(result.followup_envelope)
        self.assertEqual(envelope.status, "ok")
        followup = envelope.data["followup"]
        self.assertTrue(followup["producer_bounded"])
        self.assertFalse(followup["complete"])
        self.assertEqual(followup["continuation"]["type"], "exec_status")
        self.assertTrue(followup["continuation"]["cursor"])

    def test_running_continuation_requests_one_bounded_wait(self) -> None:
        class RunningProvider:
            provider_id = "local"

            def availability(self):
                return ExecutionAvailability(enabled=True, status="ready")

            def run(self, **kwargs):
                del kwargs
                run_id = new_run_id()
                return ExecRunStart(
                    status=EXEC_STATUS_RUNNING,
                    run_id=run_id,
                    next_cursor=make_cursor(run_id, 0),
                )

        self.handlers["exec_run"] = ExecRunToolHandler(
            execution_provider=RunningProvider(),
            config_base_dir=self.base_dir,
        )
        result, envelope = self._dispatch(
            self._invocation("exec_run", {"command": "long task", "initial_wait_seconds": 1})
        )
        self.assertEqual(result.stream_events[0]["status"], "running")
        continuation = envelope.data["followup"]["continuation"]
        self.assertEqual(continuation["type"], "exec_status")
        self.assertEqual(continuation["wait_seconds"], 30)

    def test_envelope_error_for_failed_command(self) -> None:
        result, envelope = self._dispatch(self._invocation("exec_run", {"command": "exit 2", "initial_wait_seconds": 3}))
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["status"], "failed")

    def test_envelope_ask_for_approval_required(self) -> None:
        save_capability_approval_modes(
            base_dir=self.base_dir,
            profile_user_id="alice",
            modes={"ops": "ask_each_time", "extensions": "ask_each_time"},
        )
        result, envelope = self._dispatch(self._invocation("exec_run", {"command": "echo hi", "initial_wait_seconds": 1}))
        self.assertEqual(envelope.status, "ask")
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")

    def test_envelope_error_for_unknown_status_query(self) -> None:
        result, envelope = self._dispatch(
            self._invocation("exec_status", {"run_id": "execrun_" + "0" * 32})
        )
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["status"], "unknown")


class ExecTraceStatusTests(unittest.TestCase):
    def test_approval_required_is_not_recorded_as_success(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        pending = SimpleNamespace(
            followup_context="能力需要主人批准。",
            stream_events=[{"type": "capability_approval_required"}],
        )
        self.assertEqual(engine._tool_result_trace_status(pending), "approval_required")

    def test_running_execution_result_is_not_recorded_as_tool_error(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        running = SimpleNamespace(
            followup_context="命令仍在执行中，请稍后查询。",
            stream_events=[{"type": "capability_execution_result", "status": "running"}],
        )
        self.assertFalse(engine._tool_result_is_error(running))
        self.assertEqual(engine._tool_result_trace_status(running), "success")

    def test_confirmed_cancel_is_not_overwritten_to_error(self) -> None:
        from companion_v01.tool_orchestration_engine import _final_exec_envelope

        result = SimpleNamespace(
            tool_type="exec_cancel",
            capability_result=None,
            followup_context="执行器已确认命令停止。",
            followup_envelope=None,
            state_updates={},
            stream_events=[
                {
                    "type": "capability_execution_result",
                    "tool_type": "exec_cancel",
                    "status": "cancelled",
                }
            ],
        )
        envelope = _final_exec_envelope(
            invocation=ToolInvocation(
                name="exec_cancel",
                arguments={"run_id": "execrun_" + "0" * 32},
                source="native_openai",
                id="cancel_1",
            ),
            result=result,
        )
        self.assertEqual(envelope.status, "ok")

    def test_satellite_execution_unknown_keeps_real_state_in_trace_status(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        result = SimpleNamespace(
            tool_type="system_media_control",
            followup_context="已向用户绑定电脑发送指令，但结果未确认。",
            stream_events=[
                {
                    "type": "capability_execution_result",
                    "tool_type": "system_media_control",
                    "status": "execution_unknown",
                    "reason": "media_state_not_confirmed",
                }
            ],
        )
        self.assertTrue(engine._tool_result_is_error(result))
        self.assertEqual(engine._tool_result_trace_status(result), "execution_unknown")


if __name__ == "__main__":
    unittest.main()
