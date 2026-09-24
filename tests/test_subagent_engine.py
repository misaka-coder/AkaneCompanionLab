from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.capability_registry import ExecutorBroker
from companion_v01.llm_runtime import ChatJSONResult, ModelBundle, ModelExecutionTarget, LLMRuntime
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
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.project_workspace import ProjectInspectToolHandler, WorkspaceWriteToolHandler
from companion_v01.tool_handlers.project_workspace import ManageProjectWorkspaceToolHandler
from companion_v01.tool_handlers.subagent import SpawnSubagentToolHandler
from companion_v01.tool_runtime import ToolExecutionResult
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
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

    def _configured_reasoning_effort(self, _bundle):
        return self.settings.llm_chat_reasoning_effort or self.settings.llm_reasoning_effort

    def chat_supports_native_tools(self, **_kwargs):
        return True

    def resolve_turn_execution_target(self, **_kwargs):
        return self.target

    def call_chat_json_result(self, **kwargs):
        self.requests.append({**kwargs, "post_user_turns": list(kwargs.get("post_user_turns") or []), "settings": self.settings})
        result = self.responses.pop(0)
        return result(kwargs) if callable(result) else result


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
        from companion_v01.tool_execution_policy import tool_parallel_prompt
        self.assertIn(tool_parallel_prompt(), first["system_prompt"])
        parent = self.engine.memcore_manager.build_context_projection(
            provider_profile="openai_chat", profile_user_id="alice", session_id="parent", character_pack_id="",
        )
        self.assertNotIn("Search returned", str(parent))

    def test_changed_route_fails_before_model_or_tools(self):
        request = replace(self.request, execution_context={**self.request.execution_context, "route_fingerprint": "changed"})
        result = self.driver(request, cancelled=lambda: False)
        self.assertEqual(result.reason, "subagent_model_route_changed")
        self.assertEqual(self.engine.llm.requests, [])

    def test_tool_optional_followup_does_not_replace_child_final_report(self):
        handler = self.engine.tool_handlers["web_search"]
        original = handler.execute
        def execute(**kwargs):
            result = original(**kwargs)
            from companion_v01.tool_continuation import resolve_followup
            result.followup = resolve_followup(default="none")
            return result
        with patch.object(handler, "execute", side_effect=execute):
            self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD: {
                "type": "web_search", "query": "test", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "final-action",
            }}), response({"status": "succeeded", "summary": "Parent still gets the final report."})]
            result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertIn("final report", result.summary)
        self.assertEqual(len(self.engine.llm.requests), 2)

    def test_fifty_ordinary_rounds_keep_prompt_schema_and_paired_history_stable(self):
        self.engine._max_tool_rounds = lambda: 60
        self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD: {
            "type": "web_search", "query": str(index), TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: "search_" + str(index)}}) for index in range(50)]
        self.engine.llm.responses.append(response({"status": "succeeded", "summary": "All completed."}))
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        first = self.engine.llm.requests[0]
        for item in self.engine.llm.requests[1:]:
            for key in ("system_prompt", "user_prompt", "native_tools"):
                self.assertEqual(first[key], item[key])
        history = self.engine.llm.requests[-1]["post_user_turns"]
        self.assertEqual(len(history), 100)
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"],
                         ["search_" + str(index) for index in range(50)])

    def test_child_deferred_mcp_load_and_invoke_keep_native_tools_stable(self):
        from tests.test_capability_adapter_mcp_orchestration import write_profile_config
        write_profile_config(self.root, "alice", prompt_exposed=False, allowlist=["echo"])
        request = replace(self.request, allowed_tools=("capability_list", "capability_load", "capability_invoke", "mcp.demo.echo"))
        def invoke_after_load(kwargs):
            result = kwargs["post_user_turns"][-1]["content"]
            contract = json.loads(result.split("\n",1)[1])["capabilities"][0]
            self.assertEqual(contract["capability_id"], "mcp.demo.echo")
            return response({NATIVE_TOOL_CALL_FIELD:{"type":"capability_invoke",
                "capability_id":"mcp.demo.echo", "contract_ref":contract["contract_ref"], "arguments":{"text":"child"},
                TOOL_SOURCE_FIELD:NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD:"invoke"}})
        self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD:{"type":"capability_load",
            "capability_ids":["mcp.demo.echo"],TOOL_SOURCE_FIELD:NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD:"load"}}),
            invoke_after_load, response({"status":"succeeded","summary":"Called MCP."})]
        observed = []
        async def caller(_self, *, server, tool_name, arguments):
            observed.append((tool_name,arguments))
            return {"content":[{"type":"text","text":"child complete"}],"isError":False}
        with patch("companion_v01.mcp_stdio_discoverer.McpToolCaller.__call__",caller):
            result = self.driver(request,cancelled=lambda:False)
        self.assertEqual(result.status,"succeeded",result)
        self.assertEqual(observed,[("echo",{"text":"child"})])
        first,loaded,later = self.engine.llm.requests
        self.assertIn("mcp.demo.echo",first["user_prompt"])
        self.assertNotIn("web_search",first["user_prompt"])
        self.assertNotIn("mcp_demo_echo",str(first["native_tools"]))
        self.assertEqual(first["native_tools"],loaded["native_tools"])
        self.assertEqual(first["native_tools"],later["native_tools"])
        self.assertEqual(first["user_prompt"],later["user_prompt"])
        self.assertIn("child complete",str(later["post_user_turns"]))

    def test_long_tool_returns_id_and_resumes_child_without_parent_delivery(self):
        import threading
        from companion_v01.capability_registry import WEB_SEARCH_TOOL_SPEC
        from companion_v01.tool_handlers.core import ToolMetadata
        handler = self.engine.tool_handlers["web_search"]
        handler.tool_spec = lambda: replace(WEB_SEARCH_TOOL_SPEC, execution_class="long_task")
        handler.tool_metadata = lambda: ToolMetadata(operation="write")
        release, started = threading.Event(), threading.Event()
        background = BackgroundTaskRunner({"host-jobs": 2})
        self.addCleanup(background.close)
        self.addCleanup(release.set)
        store = HostJobStore(self.root / "nested-jobs.db")
        parent_deliveries = []
        self.engine.host_tool_jobs = HostToolJobRuntime(engine=self.engine, store=store, background_tasks=background,
            conversation_ref_issuer=lambda ctx: "parent-ref", terminal_callback=lambda job: parent_deliveries.append(job) or True)
        def execute(*, call, context):
            self.assertEqual(context.execution_scope.task_id, self.request.child_session_id)
            self.assertEqual(context.execution_scope.working_directory, self.request.working_directory)
            if call["query"] == "slow":
                started.set()
                if not release.wait(4):
                    raise RuntimeError("independent_work_never_started")
            else:
                self.assertTrue(started.wait(2))
                release.set()
            return ToolExecutionResult(tool_type="web_search", followup_context="finished:" + call["query"])
        handler.execute = execute
        self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD: {
            "type": "web_search", "query": word, TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: word}}) for word in ("slow", "independent")]
        self.engine.llm.responses.extend([response({"status": "succeeded", "summary": "Both verified."}) for _ in range(4)])
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertTrue(release.is_set())
        self.assertIn("job_id=", self.engine.llm.requests[1]["post_user_turns"][-1]["content"])
        self.assertIn("finished:slow", str(self.engine.llm.requests[-1]["post_user_turns"]))
        self.assertIn("task.work.completed", str(self.engine.llm.requests[-1]["post_user_turns"]))
        self.assertEqual(parent_deliveries, [])
        self.assertEqual(store.pending_completions(), [])
        self.assertEqual(self.engine.llm.requests[0]["system_prompt"], self.engine.llm.requests[-1]["system_prompt"])
        self.assertEqual(self.engine.llm.requests[0]["native_tools"], self.engine.llm.requests[-1]["native_tools"])

    def test_child_parallel_reads_use_shared_scheduler_and_paired_history(self):
        import threading
        from companion_v01.tool_handlers.core import ToolMetadata
        from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD
        barrier = threading.Barrier(2)
        handler = self.engine.tool_handlers["web_search"]
        handler.tool_metadata = lambda: ToolMetadata(operation="read")
        def execute(*, call, context):
            barrier.wait(timeout=3)
            return ToolExecutionResult(tool_type="web_search", followup_context=call["query"])
        handler.execute = execute
        self.engine.llm.responses = [response({NATIVE_TOOL_CALLS_FIELD: [
            {"type": "web_search", "query": word, TOOL_SOURCE_FIELD: NATIVE_OPENAI,
             TOOL_INVOCATION_ID_FIELD: "search_" + word} for word in ("one", "two")
        ]}), response({"status": "succeeded", "summary": "Both searches completed."})]
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        history = self.engine.llm.requests[-1]["post_user_turns"]
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"], ["search_one", "search_two"])
        self.assertEqual([m["content"] for m in history if m["role"] == "tool"], ["one", "two"])

    def test_invalid_tool_request_gets_host_feedback_then_can_be_corrected(self):
        self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD: {
            "type": "not_a_real_tool", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: "bad_tool",
        }}), response({NATIVE_TOOL_CALL_FIELD: {
            "type": "web_search", "query": "corrected", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: "good_tool",
        }}), response({"status": "succeeded", "summary": "Corrected request, search completed."})]
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        feedback = self.engine.llm.requests[1]["post_user_turns"][-1]["content"]
        self.assertIn("not_a_real_tool", feedback)
        self.assertIn("没有执行", feedback)
        history = self.engine.llm.requests[2]["post_user_turns"]
        self.assertEqual([m["role"] for m in history], ["assistant", "tool", "assistant", "tool"])
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"], ["bad_tool", "good_tool"])

    def test_tool_images_and_verified_artifact_receipts_reach_child_and_parent(self):
        image = {"attachment_id": "sample-image", "attachment_handle": "img_sample",
                 "mime_type": "image/png", "data_url": "data:image/png;base64,AAAA"}
        handler = self.engine.tool_handlers["web_search"]
        handler.execute = lambda **kwargs: ToolExecutionResult(tool_type="web_search",
            followup_context="Image loaded.", model_image_inputs=[image],
            state_updates={"generated_handles": ["gen_actual"]})
        self.engine.llm.responses = [response({NATIVE_TOOL_CALL_FIELD: {
            "type": "web_search", "query": "sample", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: "image_search",
        }}), response({"status": "succeeded", "summary": "Image inspected.",
                       "artifact_handles": ["gen_model_invented"]})]
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual([item["handle"] for item in result.artifacts], ["gen_actual"])
        second = self.engine.llm.requests[1]
        self.assertEqual(second["user_images"], [image])
        history = second["post_user_turns"]
        image_messages = [m for m in history if isinstance(m.get("content"), list)]
        self.assertTrue(image_messages, history)
        self.assertIn("img_sample", str(image_messages))

    def test_real_llm_adapter_preserves_multi_round_native_calls_and_reasoning(self):
        import httpx
        from openai import OpenAI
        requests = []
        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            index = len(requests)
            if index <= 2:
                message = {"role": "assistant", "content": None, "reasoning_content": f"reasoning-{index}",
                    "tool_calls": [{"id": f"call_{index}", "type": "function", "function": {
                        "name": "web_search", "arguments": json.dumps({"query": f"query-{index}"})}}]}
            else:
                message = {"role": "assistant", "content": '{"status":"succeeded","summary":"Two searches verified."}'}
            return httpx.Response(200, json={"id": f"response-{index}", "object": "chat.completion", "created": 1,
                "model": "test", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if index <= 2 else "stop"}]})
        client = OpenAI(api_key="test-key-not-a-secret", base_url="http://test.invalid/v1",
                        http_client=httpx.Client(transport=httpx.MockTransport(respond)))
        self.addCleanup(client.close)
        client._akane_protocol = "openai"
        bundle = ModelBundle(client, "deepseek-test")
        with patch.object(LLMRuntime, "_build_chat_bundle", return_value=bundle), \
             patch.object(LLMRuntime, "_build_aux_bundle", return_value=bundle), \
             patch.object(LLMRuntime, "_build_memcore_summary_bundle", return_value=bundle), \
             patch.object(LLMRuntime, "_build_vision_bundle", return_value=None):
            self.engine.llm = LLMRuntime(settings=BotSettingsView(), log_dir=self.root / "llm-logs")
        target = self.engine.llm.resolve_turn_execution_target(has_real_images=False)
        request = replace(self.request, model=target.model, execution_context={**self.request.execution_context,
            "route_fingerprint": model_route_fingerprint(target)})
        result = self.driver(request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(len(requests), 3)
        tool_messages = [m for m in requests[-1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["call_1", "call_2"])
        assistants = [m for m in requests[-1]["messages"] if m["role"] == "assistant"]
        self.assertEqual([m["reasoning_content"] for m in assistants], ["reasoning-1", "reasoning-2"])
        self.assertEqual(requests[0]["tools"], requests[-1]["tools"])
        self.assertEqual(requests[0]["messages"][0], requests[-1]["messages"][0])

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
        self.assertIn(str(project), delivered[0].message)
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

    def test_parent_native_spawn_uses_frozen_turn_context_and_returns_before_child(self):
        import threading
        from companion_v01.tool_handlers.mcp_management import InvokeMcpToolHandler
        from tests.test_capability_adapter_mcp_orchestration import write_profile_config
        write_profile_config(self.root, "alice", prompt_exposed=False, allowlist=["echo"])
        self.engine.tool_handlers["invoke_mcp"] = InvokeMcpToolHandler()
        store = MemoryStore(self.root / "parent-store")
        service = ProjectWorkspaceService(store=store, execution_workspace_root=self.root / "execution")
        provider = TrustedLocalExecutor(workspace_root=self.root / "execution", run_log_dir=self.root / "runlogs")
        project_scope = service.scope_for(profile_user_id="alice", session_id="parent", client_mode="desktop_pet")
        project = service.create(scope=project_scope, display_name="Parent project")
        self.engine.store = store
        self.engine.execution_provider = provider
        self.engine.tool_handlers["manage_project_workspace"] = ManageProjectWorkspaceToolHandler(service=service, execution_provider=provider)
        background = BackgroundTaskRunner({"subagents": 1})
        release = threading.Event()
        self.addCleanup(background.close)
        self.addCleanup(release.set)
        background.submit(lane="subagents", name="hold", fn=release.wait, args=(10,))
        jobs = HostJobStore(self.root / "spawn-jobs.db")
        registry = SubagentProviderRegistry()
        registry.register(InProcessSubagentProvider(self.driver))
        runtime = HostSubagentJobRuntime(store=jobs, providers=registry, provider_name="in_process", background_tasks=background)
        authority = PluginConversationReferenceAuthority(self.root / "conversation.key", instance_id="test")
        self.engine.tool_handlers["spawn_subagent"] = SpawnSubagentToolHandler(
            engine=self.engine, runtime=runtime, conversation_ref_issuer=authority.issue)
        client = self.engine._resolve_client_protocol_context({"client_mode": "desktop_pet"})
        selection = self.engine._resolve_capability_selection(client_context=client, profile_user_id="alice", session_id="parent")
        self.assertIn("spawn_subagent", selection.schema_tool_names)
        output, calls, rejected = self.engine._prepare_tool_round_decisions(
            final_output={NATIVE_TOOL_CALL_FIELD: {"type": "spawn_subagent", "task": "Summarize the task scope.",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD: "parent_spawn"},
                TOOL_CAPABILITY_SELECTION_FIELD: selection},
            user_message="delegate", client_context=client, profile_user_id="alice", session_id="parent")
        self.assertEqual(rejected, [])
        record = {"source_id": "parent-input", "role": "user", "content": "delegate", "timestamp": 1}
        opened = self.engine.memcore_manager.begin_input_turn(record, profile_user_id="alice", session_id="parent",
                                                              character_pack_id="reimu")
        self.engine.llm.responses = [response({"status": "succeeded", "summary": "Task scope reviewed."})]
        results, _events = self.engine._execute_and_record_tool_batch(
            tool_calls=calls, final_output=output, tool_results=[], tool_events=[], tool_followups=[], tool_turns=[],
            recent_raw_for_turn=[], profile_user_id="alice", session_id="parent", character_pack_id="reimu", now_ts=1,
            current_user_source_id="parent-input", client_context=client, memory_exclude_source_ids=[], request_context={},
            tool_history_turns=[], memcore_turn_id=opened["turn_id"], execution_target=self.engine.llm.target)
        accepted = results[0].stream_events[0]
        self.assertEqual(accepted["status"], "accepted", results[0])
        job = jobs.get(accepted["job_id"], owner=HostJobOwner("alice", "parent"))
        self.assertEqual(job.status, "queued")
        self.assertEqual(authority.resolve(job.delivery_target)["character"], "reimu")
        self.assertEqual(job.payload["working_directory"], project["working_directory"])
        self.assertNotIn("spawn_subagent", job.payload["allowed_tools"])
        self.assertNotIn("manage_project_workspace", job.payload["allowed_tools"])
        self.assertIn("mcp.demo.echo", job.payload["allowed_tools"])
        self.assertNotIn("mcp.demo.echo", selection.schema_tool_names)
        self.assertEqual(job.payload["execution_context"]["route_fingerprint"], model_route_fingerprint(self.engine.llm.target))
        self.assertEqual(self.engine.llm.requests, [])
        release.set()
        self.assertTrue(background.wait_idle(lane="subagents", timeout=5))
        self.assertEqual(jobs.get(job.job_id, owner=job.owner).status, "succeeded")

    def test_cancel_mid_batch_records_all_pairs_and_preserves_actual_artifacts(self):
        import threading
        from companion_v01.tool_handlers.core import ToolMetadata
        from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD
        cancelled = threading.Event()
        handler = self.engine.tool_handlers["web_search"]
        handler.tool_metadata = lambda: ToolMetadata(operation="write")
        def execute(**kwargs):
            cancelled.set()
            return ToolExecutionResult(tool_type="web_search", followup_context="Created report.",
                                       state_updates={"generated_handles": ["gen_verified"]})
        handler.execute = execute
        self.engine.llm.responses = [response({NATIVE_TOOL_CALLS_FIELD: [
            {"type": "web_search", "query": str(n), TOOL_SOURCE_FIELD: NATIVE_OPENAI,
             TOOL_INVOCATION_ID_FIELD: "call_" + str(n)} for n in range(2)
        ]})]
        with patch.object(self.engine, "_record_memcore_tool_batch", wraps=self.engine._record_memcore_tool_batch) as record:
            result = self.driver(self.request, cancelled=cancelled.is_set)
        self.assertEqual(result.status, "cancelled", result)
        self.assertEqual([item["handle"] for item in result.artifacts], ["gen_verified"])
        items = record.call_args.kwargs["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1][1].stream_events[0]["status"], "cancelled")

    def test_cancel_before_request_never_calls_model(self):
        result = self.driver(self.request, cancelled=lambda: True)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.engine.llm.requests, [])


if __name__ == "__main__":
    unittest.main()
