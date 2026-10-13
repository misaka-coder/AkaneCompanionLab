"""Real memory/tool paths for child routing, recovery, continuation and budgets."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_subagent_jobs import HostSubagentJobRuntime
from companion_v01.llm_runtime import LLMRuntime, ModelBundle
from companion_v01.model_service_config import SubagentModelSettings
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.subagent_models import resolve_subagent_route, model_route_fingerprint
from companion_v01.tool_handlers.core import ToolExecutionResult
from companion_v01.tool_invocation import NATIVE_TOOL_CALL_FIELD, NATIVE_OPENAI, TOOL_SOURCE_FIELD, TOOL_INVOCATION_ID_FIELD
from tests import test_subagent_engine as fixtures
from tests import test_host_subagent_jobs as job_fixtures


def tool_call(identifier="search", query="check"):
    return fixtures.response({NATIVE_TOOL_CALL_FIELD: {"type": "web_search", "query": query,
        TOOL_SOURCE_FIELD: NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD: identifier}})


class SubagentContinuationTests(unittest.TestCase):
    setUp = fixtures.SubagentEngineTests.setUp

    def projection(self, agent=None):
        agent = agent or self.request.child_session_id
        return self.engine.memcore_manager.build_context_projection(provider_profile="openai_chat",
            profile_user_id=agent, session_id=agent, character_pack_id="")

    def test_related_rework_loads_only_same_child_and_keeps_distinct_sources(self):
        self.engine.llm.responses = [tool_call(), fixtures.response({"status": "succeeded", "summary": "First audit verified alpha."})]
        first = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(first.status, "succeeded", first)
        self.engine.llm.responses = [fixtures.response({"status": "succeeded", "summary": "Rechecked alpha and beta."})]
        second = self.driver(replace(self.request, task="Recheck the previous audit for beta."), cancelled=lambda: False)
        self.assertEqual(second.status, "succeeded", second)
        request = self.engine.llm.requests[-1]
        self.assertIn("First audit verified alpha", str(request["history_turns"]))
        self.assertEqual(request["post_user_turns"], [])
        sources = self.projection()["source_ids"]
        self.assertEqual(len([sid for sid in sources if ":result" in sid]), 2)
        other = "subagent_" + "c" * 32
        self.engine.llm.responses = [fixtures.response({"status": "succeeded", "summary": "Unrelated audit."})]
        self.driver(replace(self.request, child_session_id=other), cancelled=lambda: False)
        self.assertNotIn("First audit", str(self.engine.llm.requests[-1]["history_turns"]))

    def test_transient_retry_reuses_prefix_without_replaying_successful_tool(self):
        handler = self.engine.tool_handlers["web_search"]
        self.engine.llm.responses = [tool_call(), fixtures.response({}, error="503 overloaded"),
                                     fixtures.response({"status": "succeeded", "summary": "Search verified."})]
        with patch.object(handler, "execute", wraps=handler.execute) as execute:
            result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result.usage["recoveries"], 1)
        self.assertEqual(self.engine.llm.requests[1]["post_user_turns"], self.engine.llm.requests[2]["post_user_turns"])

    def test_timeout_auth_and_adapter_recovery_do_not_stack_retries(self):
        for error, attempts in [("request timed out", 1), ("401 invalid API key", 1), ("503 server error", 2)]:
            with self.subTest(error=error):
                response = fixtures.response({}, error=error)
                response.request_attempts = attempts
                self.engine.llm.responses = [response]
                before = len(self.engine.llm.requests)
                result = self.driver(self.request, cancelled=lambda: False)
                self.assertEqual(result.status, "failed")
                self.assertEqual(len(self.engine.llm.requests) - before, 1)
                self.assertNotIn("API key", result.reason)

    def test_one_final_report_repair_has_no_tool_dispatch(self):
        self.engine.llm.responses = [fixtures.response({"speech": "Done."}),
                                   fixtures.response({"status": "succeeded", "summary": "Actual report."})]
        handler = self.engine.tool_handlers["web_search"]
        with patch.object(handler, "execute", wraps=handler.execute) as execute:
            result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(execute.call_count, 0)
        self.assertEqual(self.engine.llm.requests[-1]["native_tool_choice"], "none")

    def test_failure_preserves_artifacts_and_rework_sees_failed_step(self):
        handler = self.engine.tool_handlers["web_search"]
        handler.execute = lambda **kwargs: ToolExecutionResult(tool_type="web_search", followup_context="verified partial work",
                                                              state_updates={"generated_handles": ["gen_partial"]})
        self.engine.llm.responses = [tool_call(), fixtures.response({}, error="request timed out")]
        failed = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(failed.status, "failed", failed)
        self.assertEqual(failed.artifacts[0]["handle"], "gen_partial")
        self.assertIn("Executed 1 tool", failed.summary)
        self.engine.llm.responses = [fixtures.response({"status": "succeeded", "summary": "Continued from partial work."})]
        result = self.driver(replace(self.request, task="Finish the remaining checks."), cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertIn("request_timeout", str(self.engine.llm.requests[-1]["history_turns"]))

    def test_instruction_arriving_with_final_response_runs_at_next_boundary_once(self):
        jobs = HostJobStore(self.root / "jobs.db")
        runtime = HostSubagentJobRuntime(store=jobs, providers=job_fixtures._providers(self.driver), provider_name="in_process",
            background_tasks=job_fixtures._HoldingBackgroundTasks())
        self.engine.host_subagent_jobs = runtime
        owner = HostJobOwner("alice", "parent")
        kwargs = dict(owner=owner, task=self.request.task, working_directory=str(self.root), allowed_tools=("web_search",),
                      model=self.request.model, reasoning_effort="high", execution_context=dict(self.request.execution_context),
                      conversation_ref="ref", channel="desktop_pet", tool_call_id="first")
        admitted = runtime.submit(**kwargs)
        def during_response(_kwargs):
            instruction = runtime.submit(**{**kwargs, "tool_call_id": "steer", "task": "Also check the error state.", "agent_id": admitted["agent_id"]})
            self.assertEqual(instruction["status"], "steered")
            return fixtures.response({"status": "succeeded", "summary": "Initial result."})
        self.engine.llm.responses = [during_response, fixtures.response({"status": "succeeded", "summary": "Error state checked too."})]
        runtime._run(admitted["job_id"])
        job = jobs.get(admitted["job_id"], owner=owner)
        self.assertEqual(job.status, "succeeded", job)
        self.assertEqual(len(self.engine.llm.requests), 2)
        self.assertEqual(str(self.engine.llm.requests[-1]["post_user_turns"]).count("Also check the error state"), 1)
        again = runtime.submit(**{**kwargs, "tool_call_id": "steer", "task": "Also check the error state.", "agent_id": admitted["agent_id"]})
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["job_id"], admitted["job_id"])
        self.assertEqual(jobs.pending_subagent_inputs(job.job_id, owner=owner), [])

    def test_signed_tool_replay_survives_instruction_history_refresh(self):
        from services import provider_continuation as continuation

        jobs = HostJobStore(self.root / "signed-jobs.db")
        runtime = HostSubagentJobRuntime(store=jobs, providers=job_fixtures._providers(self.driver), provider_name="in_process",
            background_tasks=job_fixtures._HoldingBackgroundTasks())
        self.engine.host_subagent_jobs = runtime
        owner = HostJobOwner("alice", "parent")
        kwargs = dict(owner=owner, task=self.request.task, working_directory=str(self.root), allowed_tools=("web_search",),
                      model=self.request.model, reasoning_effort="high", execution_context=dict(self.request.execution_context),
                      conversation_ref="ref", channel="desktop_pet", tool_call_id="first")
        admitted = runtime.submit(**kwargs)
        query = "check Authorization: Bearer fixture-query-secret"

        def first_response(_kwargs):
            runtime.submit(**{**kwargs, "tool_call_id": "steer-signed", "task": "Also check failures.",
                              "agent_id": admitted["agent_id"]})
            result = tool_call("signed-search", query)
            result.parsed[continuation.RESULT_FIELD] = {
                "route": continuation.route(self.engine.llm.target.bundle),
                "calls": [{"id": "signed-search", "name": "web_search", "arguments": {"query": query}}],
                "data": {"openai": {"signed-search": {"google": {"thought_signature": "fixture-signature"}}}},
            }
            return result

        self.engine.llm.responses = [first_response,
            fixtures.response({"status": "succeeded", "summary": "Checked failures too."})]
        runtime._run(admitted["job_id"])
        job = jobs.get(admitted["job_id"], owner=owner)
        self.assertEqual(job.status, "succeeded", job)
        history = self.engine.llm.requests[-1]["post_user_turns"]
        self.assertIn(continuation.FIELD, history[0])
        self.assertEqual(str(history).count("Also check failures."), 1)
        self.assertNotIn("fixture-query-secret", str(self.projection(admitted["agent_id"])))

    def test_oversized_open_task_compacts_closed_slices_and_preserves_readback(self):
        manager = self.engine.memcore_manager
        manager._memory_config = replace(manager._memory_config, raw_token_trigger=1000)
        ids = []
        original = self.engine._record_memcore_tool_batch
        def record(**kwargs):
            result = original(**kwargs)
            ids.extend(result[0])
            return result
        self.engine._record_memcore_tool_batch = record
        self.engine.llm.call_memcore_json = lambda **kwargs: {
            "diary_summary": "The task checked source data and should continue from verified tool results.",
            "importance": .5, "key_events": ["Checked source data"], "core_facts": [], "memory_title": "Source audit"}
        self.engine.llm.responses = [tool_call("large_" + str(i), "原始资料" * 2000) for i in range(3)]
        self.engine.llm.responses.append(fixtures.response({"status": "succeeded", "summary": "All source data checked."}))
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertGreater(self.projection()["compaction_generation"], 0)
        first = manager.open_memory(profile_user_id=self.request.child_session_id, session_id=self.request.child_session_id,
                                    character_pack_id="", arguments={"memory_id": ids[0], "view": "content"})
        self.assertTrue(first.get("ok"), first)
        self.assertIn("原始资料", str(first))
        for request in self.engine.llm.requests:
            history = request["post_user_turns"]
            for index, message in enumerate(history):
                if message.get("role") == "tool":
                    self.assertTrue(any(call.get("id") == message["tool_call_id"] for call in history[index - 1].get("tool_calls", [])))

    def test_independent_route_uses_real_child_client_and_frozen_settings(self):
        import httpx
        from openai import OpenAI
        parent_requests, child_requests = [], []
        def child_reply(request):
            payload = json.loads(request.content)
            child_requests.append(payload)
            if len(child_requests) == 1:
                self.engine.llm.settings = replace(self.engine.llm.settings, subagent=SubagentModelSettings())
                message = {"role": "assistant", "content": None, "tool_calls": [{"id": "child-search", "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query":"audit"}'}}]}
            else:
                message = {"role": "assistant", "content": '{"status":"succeeded","summary":"Child route verified."}'}
            return httpx.Response(200, json={"id": "response", "object": "chat.completion", "created": 1,
                "model": payload["model"], "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}})
        def parent_reply(request):
            parent_requests.append(request)
            return httpx.Response(500)
        def client(url, reply):
            value = OpenAI(api_key="fixture-only", base_url=url, max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(reply)))
            value._akane_protocol = "openai"
            self.addCleanup(value.close)
            return value
        parent = client("http://parent.invalid/v1", parent_reply)
        child = client("http://child.invalid/v1", child_reply)
        settings = BotSettingsView(chat_base_url="http://parent.invalid/v1", chat_api_key="fixture-only", chat_model_name="parent",
            subagent=SubagentModelSettings(mode="independent", protocol="openai", base_url="http://child.invalid/v1",
                api_key="child-fixture-key", model="deepseek-child", thinking_mode="enabled", reasoning_effort="high", max_output_tokens=2300))
        bundle = ModelBundle(parent, "parent")
        with patch.object(LLMRuntime, "_build_llm_client", return_value=parent), \
             patch.object(LLMRuntime, "_build_memcore_summary_bundle", return_value=None):
            self.engine.llm = LLMRuntime(settings=settings, log_dir=self.root / "logs")
        with patch.object(self.engine.llm, "_build_llm_client", return_value=child):
            route = resolve_subagent_route(self.engine)
            request = replace(self.request, model=route.target.model, reasoning_effort=route.reasoning_effort,
                              execution_context={"client_mode": "desktop_pet", **route.metadata()})
            result = self.driver(request, cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(parent_requests, [])
        self.assertEqual([value["model"] for value in child_requests], ["deepseek-child", "deepseek-child"])
        self.assertTrue(all(value.get("reasoning_effort") == "high" and value.get("max_tokens") == 2300 for value in child_requests))
        self.assertEqual(result.usage["input_tokens"], 2000)
        self.assertEqual(self.engine.llm.snapshot_metrics()["reported_input_tokens"], 0)
        self.assertNotIn("child-fixture-key", repr(self.engine.llm.requests) if hasattr(self.engine.llm, "requests") else str(child_requests))

    def test_independent_incomplete_route_never_uses_parent(self):
        self.engine.llm.settings = replace(self.engine.llm.settings, subagent=SubagentModelSettings(mode="independent", model="cheap"))
        result = self.driver(self.request, cancelled=lambda: False)
        self.assertEqual(result.reason, "subagent_model_service_incomplete")
        self.assertEqual(self.engine.llm.requests, [])

    def test_new_run_budget_applies_to_existing_child_without_changing_parent(self):
        from companion_v01.subagent_memory import SubagentMemory
        manager = self.engine.memcore_manager
        scope = dict(profile_user_id=self.request.child_session_id, session_id=self.request.child_session_id, character_pack_id="")
        manager.bind_subagent_runtime(self.request.child_session_id, self.engine.llm, token_limit=5000)
        child = manager._get_system(**scope)
        self.assertEqual(child.config.raw_token_trigger, 5000)
        manager.bind_subagent_runtime(self.request.child_session_id, self.engine.llm, token_limit=1500)
        self.assertIs(manager._get_system(**scope), child)
        self.assertEqual(child.config.raw_token_trigger, 1500)
        self.assertGreater(manager._memory_config.raw_token_trigger, 1500)
        self.engine.llm.settings = replace(self.engine.llm.settings, llm_context_window=1300, llm_chat_max_output_tokens=500)
        with self.assertRaisesRegex(RuntimeError, "subagent_prompt_exceeds_context_window"):
            SubagentMemory(self.engine, self.request, self.engine.llm, self.engine.llm.target, fixed_tokens=1000)

    def test_backend_restart_retains_history_and_closes_only_the_previous_orphan_slice(self):
        from tests.test_memcore_retention_anchor_slice import _manager
        manager = self.engine.memcore_manager
        scope = dict(profile_user_id=self.request.child_session_id, session_id=self.request.child_session_id, character_pack_id="")
        opened = manager.begin_input_turn({"source_id": "interrupted-input", "content": "Check the already changed file.",
                                         "timestamp": 1}, **scope)
        artifact = self.root / "changed.md"
        artifact.write_text("Already written.", encoding="utf-8")
        manager.close()
        self.engine.memcore_manager = _manager(self.temp.name)
        self.addCleanup(self.engine.memcore_manager.close)
        self.engine.llm.responses = [fixtures.response({"status": "succeeded", "summary": "Verified the existing file."})]
        result = self.driver(replace(self.request, task="Continue verification after restart."), cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        system = self.engine.memcore_manager._get_system(**scope)
        self.assertEqual(system.store.get_turn(namespace=system.namespace, turn_id=opened["turn_id"]).status, "aborted")
        self.assertIn("already changed file", str(self.engine.llm.requests[-1]["history_turns"]))
        self.assertEqual(artifact.read_text(encoding="utf-8"), "Already written.")

    def test_final_report_does_not_wait_for_background_summary(self):
        import threading
        import time
        manager = self.engine.memcore_manager
        manager._memory_config = replace(manager._memory_config, raw_token_trigger=1000)
        scope = dict(profile_user_id=self.request.child_session_id, session_id=self.request.child_session_id, character_pack_id="")
        old = manager.begin_input_turn({"source_id": "old-task", "content": "Verified source data: " + "资料" * 4000,
                                       "timestamp": 1}, **scope)
        manager.complete_input_turn(turn_id=old["turn_id"], assistant_record={"source_id": "old-report", "content": "Checked."},
                                    memory_metadata=None, provider_profile="openai_chat", **scope)
        entered, release = threading.Event(), threading.Event()
        def summary(**kwargs):
            entered.set()
            release.wait(3)
            return {"diary_summary": "Verified data retained for the task.", "importance": .5,
                    "key_events": ["Checked data"], "core_facts": [], "memory_title": "Task data"}
        self.engine.llm.call_memcore_json = summary
        self.addCleanup(release.set)
        self.engine.llm.responses = [fixtures.response({"status": "succeeded", "summary": "New report."})]
        started = time.perf_counter()
        result = self.driver(self.request, cancelled=lambda: False)
        elapsed = time.perf_counter() - started
        self.assertEqual(result.status, "succeeded", result)
        self.assertLess(elapsed, 1)
        self.assertTrue(entered.wait(1))
        release.set()

    def test_settings_save_handoff_resolves_the_actual_runtime_route(self):
        from companion_v01.model_service_config import settings_from_mapping, ModelServiceConfigStore
        settings = settings_from_mapping({"providerId": "openai_compatible", "baseUrl": "http://parent.invalid/v1",
            "apiKey": "parent-fixture-key", "chatModel": "parent", "subagent": {"mode": "independent",
                "providerId": "openai_compatible", "protocol": "openai", "baseUrl": "http://child.invalid/v1",
                "apiKey": "child-fixture-key", "model": "cheap-child", "reasoningEffort": "low"}})
        store = ModelServiceConfigStore(self.root / "model.json")
        store.save(settings)
        loaded = store.load()
        self.engine.llm.settings = self.engine.llm.settings.with_model_service(loaded)
        client = SimpleNamespace(base_url="http://child.invalid/v1", _akane_protocol="openai_chat")
        with patch.object(self.engine.llm, "_build_llm_client", create=True, return_value=client):
            route = resolve_subagent_route(self.engine)
        self.assertEqual(route.target.bundle.client, client)
        self.assertEqual(route.target.model, "cheap-child")
        self.assertEqual(route.reasoning_effort, "low")
        self.assertNotIn("child-fixture-key", str(route.metadata()))
        self.assertNotIn("child-fixture-key", str(self.engine.llm.settings.public_snapshot()))
