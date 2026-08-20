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
from companion_v01.execution_run import ExecutionAvailability, ExecRunStart, make_cursor, new_run_id
from companion_v01.execution_specs import EXEC_COMMAND_MAX_CHARS, EXEC_STATUS_RUNNING
from companion_v01.capcore_runtime import manual_permission_request, resolve_permission_for_profile
from companion_v01.local_capability_config import (
    get_approval_policy_config,
    save_approval_policy_config,
    save_capability_approval_mode,
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
) -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode="qq",
        request_context={"actor_stable_id": actor_stable_id} if actor_stable_id else {},
    )


def _python_command(code: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, "-c", code])
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

    def _handler(self, *, config_base_dir=None, provider=None):
        return ExecRunToolHandler(
            execution_provider=provider if provider is not None else self.provider,
            config_base_dir=config_base_dir if config_base_dir is not None else self.base_dir,
        )

    def _set_policy(self, mode: str) -> None:
        result = save_approval_policy_config(
            base_dir=self.base_dir,
            profile_user_id="alice",
            payload={"defaultMode": mode},
        )
        self.assertTrue(result.get("ok"), result)

    def test_exec_run_asks_by_default(self) -> None:
        handler = self._handler()
        result = handler.execute(call={"type": "exec_run", "command": "echo hi"}, context=_context())
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertIn("需要用户确认", result.followup_context)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "approval_required")

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
            call={"type": "exec_run", "command": "echo hi", "initial_wait_seconds": 1},
            context=_context(),
        )
        self.assertEqual(result.stream_events[0]["type"], "capability_execution_result")
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("完成", result.followup_context)
        self.assertIn("stdout", result.followup_context)
        self.assertIsNotNone(result.followup_envelope)
        self.assertTrue(result.followup_envelope.producer_bounded)
        self.assertTrue(result.state_updates["capability_execution"]["run_id"].startswith("execrun_"))
        self.assertEqual(result.state_updates["capability_execution"]["output_ref"].startswith("runlog:"), True)

    def test_exec_run_rejects_long_command_with_actionable_limits(self) -> None:
        self._set_policy("trusted_auto_allow")
        command = "x" * (EXEC_COMMAND_MAX_CHARS + 17)

        result = self._handler().execute(
            call={"type": "exec_run", "command": command},
            context=_context(),
        )

        event = result.stream_events[0]
        state = result.state_updates["capability_execution"]
        self.assertEqual(event["status"], "rejected")
        self.assertEqual(event["reason"], "command_too_long")
        self.assertEqual(state["status"], "rejected")
        self.assertEqual(state["max_chars"], EXEC_COMMAND_MAX_CHARS)
        self.assertEqual(state["actual_chars"], len(command))
        self.assertEqual(state["recommended_action"], "workspace_write_or_patch")
        self.assertIn("workspace_write", result.followup_context)
        self.assertNotIn("无法确认", result.followup_context)

    def test_exec_prompt_is_compact_and_defers_exact_versions_to_real_probes(self) -> None:
        instruction = self._handler().build_prompt_instruction()

        self.assertIn("上方宿主事实", instruction)
        self.assertIn("精确运行时版本需要时先用命令探测", instruction)
        self.assertNotIn("toolchain=", instruction)
        self.assertNotIn("unavailable", instruction)
        self.assertNotIn(str(self.base_dir), instruction)

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
            self.assertEqual(value.count("【执行宿主】"), 1)
            self.assertIn("platform=linux", value)
            self.assertIn("command_shell=/bin/sh", value)
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

    def test_exec_run_capability_override_does_not_unlock_other_high_risk_tools(self) -> None:
        saved = save_capability_approval_mode(
            base_dir=self.base_dir,
            profile_user_id="alice",
            capability_id="tool.exec_run",
            mode="trusted_auto_allow",
        )
        self.assertTrue(saved.get("ok"), saved)
        policy = get_approval_policy_config(base_dir=self.base_dir, profile_user_id="alice")["approvalPolicy"]
        self.assertEqual(policy["defaultMode"], "ask_each_time")
        self.assertEqual(policy["capabilityModes"], {"exec_run": "trusted_auto_allow"})

        exec_result = self._handler().execute(
            call={"type": "exec_run", "command": "echo hi", "initial_wait_seconds": 1},
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

    def test_global_policy_save_preserves_shell_override(self) -> None:
        save_capability_approval_mode(
            base_dir=self.base_dir,
            profile_user_id="alice",
            capability_id="exec_run",
            mode="disabled",
        )
        saved = save_approval_policy_config(
            base_dir=self.base_dir,
            profile_user_id="alice",
            payload={"defaultMode": "trusted_auto_allow"},
        )
        self.assertTrue(saved.get("ok"), saved)
        self.assertEqual(saved["approvalPolicy"]["capabilityModes"], {"exec_run": "disabled"})

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
        self.assertIn("input_resources 与 cwd 互斥", instruction)
        self.assertIn("output_globs 可以与 cwd=alias:project 一起使用", instruction)
        self.assertIn("本次新建或变更的产物", instruction)
        self.assertIn("上方宿主事实", instruction)
        self.assertIn("先用只读命令核对真实目标", instruction)
        self.assertIn("与自己向用户说明的范围完全一致", instruction)
        self.assertIn("timed_out/failed", instruction)

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
            initial_wait_seconds=1,
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
            initial_wait_seconds=1,
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
    def test_catalog_registers_exec_handlers_when_provider_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            provider = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=Path(tmp) / "runlogs")
            handlers = build_builtin_tool_handlers(
                store=None,
                npc_runtime=None,
                gift_service=None,
                artifact_service=None,
                persona_card_service=None,
                sticker_assets=None,
                capability_offer_source=None,
                capability_config_base_dir=None,
                memory_timeline_service=None,
                context_libraries=None,
                attachment_service=None,
                image_material_resolver=None,
                task_workspace_service=None,
                workspace_file_service=None,
                attachment_ingest_service=None,
                generated_file_service=None,
                image_generation_service=None,
                cover_song_service=None,
                task_worker_service=None,
                retrieve_fn=None,
                describe_scene=None,
                build_npc_followup_context=None,
                observe_gift_image_fn=None,
                execution_provider=provider,
            )
            self.assertIn("exec_run", handlers)
            self.assertIn("exec_status", handlers)
            self.assertIn("exec_cancel", handlers)
            self.assertEqual(handlers["exec_run"].tool_metadata().family, "execution")
            self.assertEqual(handlers["exec_run"].tool_metadata().risk, "high")
            self.assertTrue(handlers["exec_run"].tool_metadata().requires_confirmation)
            self.assertTrue(handlers["exec_status"].tool_metadata().is_read_only)
            self.assertFalse(handlers["exec_cancel"].tool_metadata().is_read_only)
            self.assertTrue(handlers["exec_run"].policy_accepted_native_tool)
            self.assertTrue(handlers["exec_status"].policy_accepted_native_tool)
            self.assertTrue(handlers["exec_cancel"].policy_accepted_native_tool)

    def test_catalog_omits_exec_handlers_without_provider(self) -> None:
        handlers = build_builtin_tool_handlers(
            store=None,
            npc_runtime=None,
            gift_service=None,
            artifact_service=None,
            persona_card_service=None,
            sticker_assets=None,
            capability_offer_source=None,
            capability_config_base_dir=None,
            memory_timeline_service=None,
            context_libraries=None,
            attachment_service=None,
            image_material_resolver=None,
            task_workspace_service=None,
            workspace_file_service=None,
            attachment_ingest_service=None,
            generated_file_service=None,
            image_generation_service=None,
            cover_song_service=None,
            task_worker_service=None,
            retrieve_fn=None,
            describe_scene=None,
            build_npc_followup_context=None,
            observe_gift_image_fn=None,
        )
        self.assertNotIn("exec_run", handlers)
        self.assertNotIn("exec_status", handlers)
        self.assertNotIn("exec_cancel", handlers)

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
        save_approval_policy_config(
            base_dir=self.base_dir,
            profile_user_id="alice",
            payload={"defaultMode": "trusted_auto_allow"},
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
        result, envelope = self._dispatch(self._invocation("exec_run", {"command": "echo hi", "initial_wait_seconds": 1}))
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertIn("run_id", envelope.data["state_updates"]["capability_execution"])

    def test_envelope_preserves_bounded_output_continuation(self) -> None:
        result, envelope = self._dispatch(
            self._invocation(
                "exec_run",
                {
                    "command": _python_command("import sys; sys.stdout.write('x' * 60000)"),
                    "initial_wait_seconds": 1,
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
        result, envelope = self._dispatch(self._invocation("exec_run", {"command": "exit 2", "initial_wait_seconds": 1}))
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["status"], "failed")

    def test_envelope_ask_for_approval_required(self) -> None:
        save_approval_policy_config(
            base_dir=self.base_dir,
            profile_user_id="alice",
            payload={"defaultMode": "ask_each_time"},
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
