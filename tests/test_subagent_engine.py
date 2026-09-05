from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.capability_registry import ExecutorBroker
from companion_v01.llm_runtime import ChatJSONResult, ModelBundle, ModelExecutionTarget
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.subagent_engine import EngineSubagentDriver, model_route_fingerprint
from companion_v01.subagent_runtime import SubagentStartRequest
from companion_v01.subagent_runtime import InProcessSubagentProvider, SubagentProviderRegistry
from companion_v01.host_subagent_jobs import HostSubagentJobRuntime
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.bot_runtime import _host_job_completion_request
from companion_v01.project_workspace import ProjectWorkspaceService
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.project_workspace import ProjectInspectToolHandler, WorkspaceWriteToolHandler
from companion_v01.tool_invocation import NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_SOURCE_FIELD, TOOL_INVOCATION_ID_FIELD
from tests.test_capability_adapter_mcp_orchestration import build_engine
from tests.test_memcore_retention_anchor_slice import _manager


class ScriptedModel:
    def __init__(self):
        self.settings = BotSettingsView(llm_reasoning_effort="low")
        self.target = ModelExecutionTarget(
            role="chat", bundle=ModelBundle(SimpleNamespace(base_url="http://test", _akane_protocol="openai_chat"), "test"),
            model="test", reason="test",
        )
        self.requests = []
        self.responses = []

    def _settings_view(self):
        return self.settings

    def resolve_turn_execution_target(self, **_kwargs):
        return self.target

    def call_chat_json_result(self, **kwargs):
        self.requests.append({**kwargs, "post_user_turns": list(kwargs.get("post_user_turns") or []), "settings": self.settings})
        return self.responses.pop(0)


def response(output, *, error="", fallback=False):
    return ChatJSONResult(parsed=output, raw_text=json.dumps(output), error=error, fallback_used=fallback)


class SubagentEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        configuration = patch("config.DATA_DIR", self.temp.name)
        configuration.start()
        self.addCleanup(configuration.stop)
        self.engine = build_engine(self.root)
        self.engine.memcore_manager = _manager(self.temp.name)
        self.addCleanup(self.engine.memcore_manager.close)
        self.engine.executor_broker = ExecutorBroker(None)
        self.engine.llm = ScriptedModel()
        self.driver = EngineSubagentDriver(self.engine)
        self.request = SubagentStartRequest(
            task="Run a search and report the result.", child_session_id="subagent_" + "b" * 32,
            parent_profile_user_id="alice", parent_session_id="parent",
            working_directory=str(self.root), allowed_tools=("web_search",), model="test", reasoning_effort="high",
            execution_context={"client_mode": "desktop_pet", "model_role": "chat", "thinking_mode": "enabled",
                               "route_fingerprint": model_route_fingerprint(self.engine.llm.target)},
        )

    def test_native_tool_runs_through_host_broker_and_real_memcore_projection(self):
        self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD: {
            "type": "web_search", "query": "test", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: "call_child_search",
        }}), response({"status": "succeeded", "summary": "Search returned ok."})]
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(len(self.engine.llm.requests), 2)
        first, second = self.engine.llm.requests
        self.assertEqual(first["post_user_turns"], [])
        self.assertEqual(first["settings"].llm_chat_reasoning_effort, "high")
        self.assertEqual(self.engine.llm.settings.llm_reasoning_effort, "low")
        history = second["post_user_turns"]
        self.assertEqual([item["role"] for item in history], ["assistant", "tool"])
        self.assertEqual(history[1]["tool_call_id"], "call_child_search")
        self.assertIn("ok", history[1]["content"])
        self.assertEqual(first["system_prompt"], second["system_prompt"])
        parent = self.engine.memcore_manager.build_context_projection(
            provider_profile="openai_chat", profile_user_id="alice", session_id="parent", character_pack_id="",
        )
        self.assertNotIn("Search returned", str(parent))

    def test_changed_route_fails_before_model_or_tools(self):
        request = replace(self.request, execution_context={**self.request.execution_context, "route_fingerprint": "changed"})
        result = self.driver(request, cancelled=lambda: False)
        self.assertEqual(result.reason, "subagent_model_route_changed")
        self.assertEqual(self.engine.llm.requests, [])

    def test_real_file_task_finishes_job_and_returns_only_summary_to_parent(self):
        store = MemoryStore(self.root / "legacy")
        service = ProjectWorkspaceService(store=store, execution_workspace_root=self.root / "execution")
        provider = TrustedLocalExecutor(workspace_root=self.root / "execution", run_log_dir=self.root / "runlogs")
        project = self.root / "project"
        project.mkdir()
        (project / "source.py").write_text("answer = 42\n", encoding="utf-8")
        self.engine.store = store
        self.engine.execution_provider = provider
        self.engine.tool_handlers = {
            "project_inspect": ProjectInspectToolHandler(service=service, execution_provider=provider),
            "workspace_write": WorkspaceWriteToolHandler(service=service, execution_provider=provider),
        }
        self.engine.llm.responses = [
            response({NATIVE_TOOL_CALL_FIELD: {"type": "project_inspect", "action": "read", "path": "source.py",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD: "read_source"}}),
            response({NATIVE_TOOL_CALL_FIELD: {"type": "workspace_write", "path": "audit.md", "content": "Verified answer = 42.\n",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD: "write_report"}}),
            response({"status": "succeeded", "summary": "Read source.py and wrote audit.md in the shared project."}),
        ]
        jobs = HostJobStore(self.root / "jobs.db")
        background = BackgroundTaskRunner({"subagents": 1})
        self.addCleanup(background.close)
        delivered = []
        completions = HostToolJobRuntime(engine=self.engine, store=jobs, background_tasks=background,
            terminal_callback=lambda job: delivered.append(_host_job_completion_request(job)) or True)
        registry = SubagentProviderRegistry()
        registry.register(InProcessSubagentProvider(self.driver))
        runtime = HostSubagentJobRuntime(store=jobs, background_tasks=background, providers=registry,
            provider_name="in_process", completion_publisher=completions.publish_completion)
        owner = HostJobOwner("alice", "parent")
        result = runtime.submit(owner=owner, task="Read source.py and write an audit report.",
            working_directory=str(project), allowed_tools=("project_inspect", "workspace_write"),
            model="test", reasoning_effort="high", execution_context=dict(self.request.execution_context),
            conversation_ref="parent-ref", channel="desktop_pet", character_pack_id="reimu", tool_call_id="spawn-test")
        self.assertTrue(result["ok"], result)
        self.assertTrue(background.wait_idle(lane="subagents", timeout=5))
        self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=5))
        job = jobs.get(result["job_id"], owner=owner)
        self.assertEqual(job.status, "succeeded", job)
        self.assertTrue((project / "audit.md").exists(), self.engine.llm.requests[-1]["post_user_turns"])
        self.assertEqual((project / "audit.md").read_text(), "Verified answer = 42.\n")
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].conversation_ref, "parent-ref")
        self.assertNotIn("Verified answer = 42", delivered[0].message)
        self.assertIn("audit.md", delivered[0].message)
        self.assertEqual(job.completion_status, "delivered")

    def test_fallback_truncation_and_empty_final_are_not_success(self):
        for payload in (response({}, fallback=True), response({}, error="response_truncated"), response({"status": "succeeded"})):
            with self.subTest(payload=payload):
                # Each child has a distinct audit stream.
                import uuid
                request = replace(self.request, child_session_id="subagent_" + uuid.uuid4().hex)
                self.engine.llm.responses = [payload]
                result = self.driver(request, cancelled=lambda: False)
                self.assertEqual(result.status, "failed")

    def test_cancel_before_request_never_calls_model(self):
        result = self.driver(self.request, cancelled=lambda: True)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.engine.llm.requests, [])


if __name__ == "__main__":
    unittest.main()
