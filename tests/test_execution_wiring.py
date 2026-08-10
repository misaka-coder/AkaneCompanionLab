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
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.execution_local import TrustedLocalExecutor
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


def _context(*, profile_user_id: str = "alice", session_id: str = "s1") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode="qq",
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


if __name__ == "__main__":
    unittest.main()
