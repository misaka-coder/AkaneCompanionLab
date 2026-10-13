"""Real request assembly/ledger integration without APIs or production databases."""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

from memcore import ChatContextSnapshot, LLMRequest, TaskType
from memcore.chat_compaction import ChatCompactionPrefix
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.response_catalog import _overlay_ephemeral_provider_evidence
from companion_v01.host_completion_work import HostCompletionWork
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.llm_runtime import LLMRuntime, ModelBundle, ModelExecutionTarget
from companion_v01.memcore_integration.adapters import build_akane_llm_client
from companion_v01.memcore_integration.chat_prefix import _refresh_terminal_context
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.model_runtime.memory_prefix import CapturedCompletionRequest
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.subagent_memory import SubagentMemory
from services import provider_continuation as continuation
from services.gemini_native_client import _build_gemini_payload
from services.llm_client import _build_anthropic_payload


TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]


def runtime(protocol="openai"):
    llm = LLMRuntime.__new__(LLMRuntime)
    llm.settings = BotSettingsView(
        llm_context_window=300000,
        llm_auto_compact_token_limit=10,
        llm_chat_max_output_tokens=1024,
        prompt_cache_hints_enabled=True,
    )
    llm._metrics, llm._metrics_lock, llm._bundle_lock = {}, threading.RLock(), threading.RLock()
    client = NS(_akane_protocol=protocol, _akane_bundle_role="chat", base_url="https://example.test/v1")
    llm.chat = ModelBundle(client, "test-model")
    llm._record_prompt_audit_if_enabled = Mock()
    llm._record_responses_request_audit_if_enabled = Mock()
    llm._create_completion = Mock(
        return_value=NS(
            choices=[NS(finish_reason="stop", message=NS(content='{"summary":"A completed; continue with B."}'))]
        )
    )
    return llm


def prefix(captured, *, start=1, end=None, purpose="working"):
    messages = tuple(captured.public_messages())
    boundary = end or len(messages)
    groups = tuple((f"fixture-source-{i}",) if start <= i < boundary else () for i in range(len(messages)))
    snapshot = ChatContextSnapshot("openai_chat", messages, groups, captured)
    return ChatCompactionPrefix(snapshot, start, boundary, purpose)


def call(identifier="a", content="x"):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"q":"' + content + '"}'},
    }


class PrefixRuntimeTests(unittest.TestCase):
    def test_frozen_route_controls_tools_and_cache_key_survive_live_settings_change(self):
        llm = runtime()
        payload = {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "A"},
                {"role": "assistant", "content": "answer A"},
                {"role": "user", "content": "new B"},
            ],
            "tools": deepcopy(TOOLS),
            "tool_choice": "auto",
            "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            "prompt_cache_key": "stable-user-key",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        captured = CapturedCompletionRequest.capture(llm.chat, payload, context_window=300000)
        original = deepcopy(captured.payload)
        llm.chat.model = "changed-model"
        llm.settings = replace(llm.settings, llm_context_window=1, llm_chat_reasoning_effort="max")
        payload["tools"].clear()
        value = llm.call_memcore_prefix_json(
            chat_prefix=prefix(captured, end=3), contract='JSON {"summary":"..."}', fallback={}
        )
        self.assertIn("summary", value)
        sent = llm._create_completion.call_args.kwargs
        self.assertEqual(sent["bundle"].model, "test-model")
        self.assertEqual(sent["payload"]["messages"][:-1], original["messages"][:3])
        self.assertEqual(sent["payload"]["tools"], TOOLS)
        self.assertEqual(sent["payload"]["extra_body"], original["extra_body"])
        self.assertEqual(sent["payload"]["prompt_cache_key"], original["prompt_cache_key"])
        self.assertEqual(sent["payload"]["tool_choice"], "none")
        self.assertNotIn("stream", sent["payload"])
        self.assertEqual(captured.payload, original)
        self.assertEqual(llm._metrics, {"memcore_prefix_calls": 1})

    def test_adapter_never_switches_to_dedicated_summary_route_after_prefix_failure(self):
        llm = NS(call_memcore_prefix_json=Mock(side_effect=ValueError("503")), call_memcore_json=Mock())
        result = build_akane_llm_client(llm).call(
            LLMRequest(
                TaskType.SUMMARY,
                "contract",
                "independent source",
                max_retries=0,
                fallback={},
                chat_prefix=prefix(
                    CapturedCompletionRequest.capture(
                        runtime().chat,
                        {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "A"}]},
                    )
                ),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "503")
        llm.call_memcore_json.assert_not_called()

    def test_capture_only_does_not_send_request_or_duplicate_prompt_audits(self):
        llm = runtime()
        capture = llm.capture_memcore_generation_request(
            system_prompt="stable", user_prompt="A", temperature=0.1, native_tools=TOOLS, prompt_cache_key="same-key"
        )
        self.assertTrue(capture.payload["tools"])
        llm._create_completion.assert_not_called()
        llm._record_prompt_audit_if_enabled.assert_not_called()
        self.assertEqual(llm._metrics, {})

    def test_physical_limit_and_invalid_json_preserve_failure(self):
        llm = runtime()
        captured = CapturedCompletionRequest.capture(
            llm.chat, {"messages": [{"role": "user", "content": "long " * 100}]}, context_window=1
        )
        with self.assertRaisesRegex(ValueError, "context_window"):
            llm.call_memcore_prefix_json(chat_prefix=prefix(captured, start=0), contract="JSON", fallback={})
        llm._create_completion.assert_not_called()
        captured = CapturedCompletionRequest.capture(llm.chat, {"messages": [{"role": "user", "content": "A"}]})
        for content, reason in (("not JSON", "invalid_json"), ('{"summary":"x"}', "truncated")):
            llm._create_completion.return_value = NS(
                choices=[NS(message=NS(content=content), finish_reason="length" if reason == "truncated" else "stop")]
            )
            with self.assertRaisesRegex(ValueError, reason):
                llm.call_memcore_prefix_json(chat_prefix=prefix(captured, start=0), contract="JSON", fallback={})

    def test_signed_surviving_batch_is_replayed_after_old_batch_folds(self):
        for protocol in ("gemini", "responses", "anthropic"):
            with self.subTest(protocol=protocol):
                llm = runtime(protocol)
                if protocol == "anthropic":
                    durable = {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "b", "name": "lookup", "input": {"q": "x"}}],
                    }
                    result = {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "b", "content": "done"}],
                    }
                    wire_data = [
                        {"type": "thinking", "thinking": "private", "signature": "signature-b"},
                        *durable["content"],
                    ]
                else:
                    durable = {"role": "assistant", "tool_calls": [call("b")]}
                    result = {"role": "tool", "tool_call_id": "b", "content": "done"}
                    wire_data = (
                        [
                            {
                                "functionCall": {"id": "b", "name": "lookup", "args": {"q": "x"}},
                                "thoughtSignature": "signature-b",
                            }
                        ]
                        if protocol == "gemini"
                        else [
                            {"type": "reasoning", "id": "reason-b", "encrypted_content": "signature-b"},
                            {"type": "function_call", "call_id": "b", "name": "lookup", "arguments": '{"q":"x"}'},
                        ]
                    )
                carrier = {
                    "route": continuation.route(llm.chat),
                    "calls": continuation.batch(durable),
                    "data": {protocol: wire_data},
                }
                prepared = [
                    {"role": "assistant", "tool_calls": [call("a")]},
                    {"role": "tool", "tool_call_id": "a", "content": "old body"},
                    continuation.overlay(durable, {"b": carrier}),
                    result,
                ]
                authoritative = [{"role": "user", "content": "[working summary] A is completed."}, durable, result]
                overlaid = _overlay_ephemeral_provider_evidence(authoritative, prepared, allow_folded_history=True)
                payload = llm._build_completion_kwargs(
                    bundle=llm.chat,
                    system_prompt="stable",
                    user_prompt="task",
                    temperature=0.1,
                    native_tools=TOOLS,
                    post_user_turns=overlaid,
                    validate_budget=False,
                )
                captured = CapturedCompletionRequest.capture(llm.chat, payload)
                public = tuple(captured.public_messages())
                groups = ((), *((f"signed-source-{i}",) for i in range(1, len(public))))
                snapshot = ChatContextSnapshot("openai_chat", public, groups, captured)
                projections = [
                    NS(payload=deepcopy(message), source_ids=ids, provider_profile="openai_chat")
                    for message, ids in zip(public[1:], groups[1:])
                ]
                # Terminal settlement changes the result while the signed
                # call itself survives unchanged, then appends the final.
                if protocol == "anthropic":
                    projections[-1].payload["content"][0]["content"] = "compact_reloadable result b"
                else:
                    projections[-1].payload["content"] = "compact_reloadable result b"
                projections.append(
                    NS(
                        payload={"role": "assistant", "content": "final verified"},
                        source_ids=("signed-final",),
                        provider_profile="openai_chat",
                    )
                )
                refreshed = _refresh_terminal_context(snapshot, tuple(projections))
                self.assertIsNotNone(refreshed)
                terminal_prefix = refreshed.select(projections, purpose="raw")
                self.assertIsNotNone(terminal_prefix)
                llm.call_memcore_prefix_json(chat_prefix=terminal_prefix, contract="JSON summary", fallback={})
                sent = llm._create_completion.call_args.kwargs["payload"]
                wire = (
                    _build_gemini_payload(sent)
                    if protocol == "gemini"
                    else _build_anthropic_payload(sent)
                    if protocol == "anthropic"
                    else llm._responses_payload_from_chat(sent)
                )
                self.assertIn("signature-b", str(wire))
                self.assertNotIn("old body", str(wire))
                self.assertNotIn("signature-b", str(captured.public_messages()))
                self.assertIn("final verified", str(sent))
                self.assertIn("compact_reloadable result b", str(sent))
                changed = deepcopy(projections)
                signed_call = next(item for item in changed if continuation.batch(item.payload))
                if protocol == "anthropic":
                    signed_call.payload["content"][0]["input"]["q"] = "changed argument"
                else:
                    signed_call.payload["tool_calls"][0]["function"]["arguments"] = '{"q":"changed argument"}'
                self.assertIsNone(_refresh_terminal_context(snapshot, tuple(changed)))


class PrefixManagerTests(unittest.TestCase):
    scope = {"profile_user_id": "fixture-user", "session_id": "fixture-session", "character_pack_id": "fixture"}

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="akane-prefix-")
        self.addCleanup(self.temp.cleanup)
        self.mode = patch("config.MEMCORE_COMPACTION_REQUEST_MODE", "chat_prefix")
        self.mode.start()
        self.addCleanup(self.mode.stop)
        policy = patch("config.MEMCORE_OPERATION_PROJECTION_POLICY", "compact_arguments_after_terminal")
        policy.start()
        self.addCleanup(policy.stop)
        self.llm = runtime()
        self.manager = MemcoreManager(
            backend="memcore",
            storage_path=Path(self.temp.name) / "memory.db",
            visible_scope="conversation",
            enable_flavor=True,
            shadow_compare=False,
            llm=self.llm,
            embedding_provider=None,
            retrieval_mode="navigation",
        )
        self.addCleanup(self.manager.close)
        self.assertTrue(self.manager.available, self.manager._reason)
        self.manager._memory_config.raw_token_trigger = 999999
        self.manager._memory_config.working_token_trigger = 1000
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        self.engine.memcore_manager, self.engine.llm = self.manager, self.llm
        self.llm.supports_request_observer = True
        opened = self.manager.begin_input_turn(
            {"source_id": "input", "content": "Please develop fixture", "timestamp": 100}, **self.scope
        )
        self.turn_id = opened["turn_id"]
        self.system = self.manager._get_system(**self.scope)

    def tools(self, identifier="a", body=None, scope=None, turn_id=None):
        result = self.manager.record_tool_batch(
            turn_id=turn_id or self.turn_id,
            exchanges=[
                {
                    "tool_name": "lookup",
                    "tool_call_id": identifier,
                    "tool_input": {"q": identifier},
                    "result": body or ("verified output " * 800),
                    "source": "fixture",
                    "timestamp": 101,
                    "source_id_prefix": "tool-" + identifier,
                    "result_status": "success",
                }
            ],
            **(scope or self.scope),
        )
        self.assertTrue(result["ok"], result)

    def context(self, current_source_id="input"):
        surface = self.manager.build_context_surface(
            provider_profile="openai_chat", current_source_id=current_source_id, **self.scope
        )
        self.assertTrue(surface["ok"], surface)
        metadata = surface["message_projection_metadata"]
        history = surface["history_messages"]
        payloads = [surface["current_message"], *surface["active_turn_messages"]]
        context = {
            "history_turns": history,
            "memcore_history_start_index": 0,
            "memcore_projection_read": {
                "provider_profile": "openai_chat",
                "current_turn_id": self.turn_id,
                "history_message_source_ids": [meta["source_ids"] for meta in metadata[: len(history)]],
                "current_turn_messages": [
                    {**meta, "payload": payload} for meta, payload in zip(metadata[len(history) :], payloads)
                ],
            },
        }
        return context, payloads

    def capture(self, context, payloads, **kwargs):
        observer = self.engine._build_memcore_request_observer(generation_context=context, **self.scope)
        with patch.object(
            self.manager, "compact_due_background", return_value={"ok": True, "status": "fixture_manual"}
        ):
            return self.llm.capture_memcore_generation_request(
                system_prompt="stable persona",
                user_prompt="actual rendered task",
                temperature=0.1,
                native_tools=TOOLS,
                history_turns=context["history_turns"],
                post_user_turns=payloads[1:],
                request_observer=observer,
                prompt_cache_key="fixture:key",
                **kwargs,
            )

    def terminal_summary_response(self):
        return NS(choices=[NS(finish_reason="stop", message=NS(content='{"diary_summary":"fixture verified"}'))])

    def test_open_trace_never_triggers_raw_summary_before_terminal_projection(self):
        prior_context, prior_payloads = self.context()
        self.capture(prior_context, prior_payloads)
        completed = self.manager.complete_input_turn(
            turn_id=self.turn_id,
            assistant_record={"source_id": "prior-final", "content": "prior answer", "timestamp": 102},
            memory_metadata=None,
            provider_output_raw="prior answer",
            provider_profile="openai_chat",
            provider_projection={"role": "assistant", "content": "prior answer"},
            **self.scope,
        )
        self.assertTrue(completed["ok"], completed)
        opened = self.manager.begin_input_turn(
            {"source_id": "active-input", "content": "develop next task", "timestamp": 103}, **self.scope
        )
        self.assertTrue(opened["ok"], opened)
        self.turn_id = opened["turn_id"]
        self.system.config.raw_token_trigger = 3000
        self.system.config.working_token_trigger = 200000
        context_before, _ = self.context(current_source_id="active-input")
        generation = self.system.build_context_projection(provider_profile="openai_chat").compaction_generation
        for identifier in ("a", "b"):
            self.tools(identifier=identifier, body="large verified tool output " * 1000)
            context, payloads = self.context(current_source_id="active-input")
            self.capture(context, payloads)
            self.assertTrue(self.engine._schedule_memcore_compaction(**self.scope)["ok"])
            self.assertEqual(self.wait_for_compaction(), [])
            self.assertEqual(context["history_turns"], context_before["history_turns"])
            self.assertEqual(
                self.system.build_context_projection(provider_profile="openai_chat").compaction_generation, generation
            )
            self.llm._create_completion.assert_not_called()

        completed = self.manager.complete_input_turn(
            turn_id=self.turn_id,
            assistant_record={"source_id": "active-final", "content": "task verified", "timestamp": 104},
            memory_metadata=None,
            provider_output_raw="task verified",
            provider_profile="openai_chat",
            provider_projection={"role": "assistant", "content": "task verified"},
            **self.scope,
        )
        self.assertTrue(completed["ok"], completed)
        metrics = self.system.settlement_metrics()
        self.assertGreater(metrics[-1]["full_projected_tokens"], self.system.config.raw_token_trigger)
        self.assertLess(metrics[-1]["settled_projected_tokens"], self.system.config.raw_token_trigger)
        self.assertTrue(self.engine._schedule_memcore_compaction(**self.scope)["ok"])
        self.assertEqual(self.wait_for_compaction(), [])
        self.llm._create_completion.assert_not_called()

    def wait_for_compaction(self, system=None):
        target = system or self.system
        key = self.manager._compaction_namespace_key(target)
        # Follow coalesced maintenance to quiescence, never ask for another
        # sync compaction merely to make an immediate-terminal test pass.
        with self.manager._jobs_condition:
            ready = self.manager._jobs_condition.wait_for(
                lambda: key not in self.manager._background_compactions,
                timeout=5,
            )
        self.assertTrue(ready, "fixture compaction did not finish")
        return target.store.get_visible_episodic_summaries(
            namespace=target.namespace, limit=10, cross_conversation=False
        )

    def test_immediate_terminal_schedule_forks_settled_history_and_working_note_without_recapture(self):
        self.tools()
        context, payloads = self.context()
        captured = self.capture(
            context,
            payloads,
            ephemeral_turns=[{"role": "user", "content": "fixture transient render evidence"}],
            user_images=[{"url": "data:image/png;base64,fixture-terminal-image"}],
        )
        self.assertEqual(
            self.manager.compact_due_sync(provider_profile="openai_chat", **self.scope)["stats"]["working_status"],
            "committed",
        )
        # The next ordinary request adopts A's summary and sees the new B.
        self.tools("b")
        context, payloads = self.context()
        captured = self.capture(
            context,
            payloads,
            ephemeral_turns=[{"role": "user", "content": "fixture transient render evidence"}],
            user_images=[{"url": "data:image/png;base64,fixture-terminal-image"}],
        )
        self.assertIn("[working summary;", str(captured.public_messages()))
        self.llm._create_completion.reset_mock()
        self.llm._create_completion.return_value = self.terminal_summary_response()
        self.system.config.raw_token_trigger = 100
        completed = self.engine._complete_memcore_input_turn(
            turn_id=self.turn_id,
            assistant_record={"source_id": "fixture-final", "content": "Completed fixture", "timestamp": 102},
            memory_metadata=None,
            provider_output_raw="Completed fixture",
            **self.scope,
        )
        self.assertTrue(completed["ok"], completed)
        self.llm.chat.model = "new-live-model"
        self.llm.settings = replace(self.llm.settings, llm_context_window=1, llm_chat_reasoning_effort="max")
        # Use the real production scheduler; no manually refreshed snapshot.
        scheduled = self.engine._schedule_memcore_compaction(**self.scope)
        self.assertTrue(scheduled["ok"], scheduled)
        self.assertTrue(self.wait_for_compaction())
        self.assertEqual(self.llm._create_completion.call_count, 1)
        sent = self.llm._create_completion.call_args.kwargs
        body = str(sent["payload"]["messages"])
        self.assertIn("[working summary;", body)
        self.assertEqual(body.count("A completed; continue with B."), 1)
        self.assertIn("Completed fixture", body)
        self.assertIn("compact_reloadable", body)
        self.assertNotIn("verified output " * 800, body)
        self.assertEqual(sent["payload"]["messages"][0], captured.payload["messages"][0])
        for key in ("model", "tools", "prompt_cache_key", "extra_body"):
            self.assertEqual(sent["payload"].get(key), captured.payload.get(key))
        self.assertIn("Internal memory maintenance", body)
        self.assertEqual(body.count("fixture transient render evidence"), 1)
        transient_index = next(
            i for i, message in enumerate(sent["payload"]["messages"])
            if message.get("content") == "fixture transient render evidence"
        )
        instruction = sent["payload"]["messages"][-1]["content"]
        self.assertIn(f"request messages {transient_index + 1} are background, not memory sources", instruction)
        self.assertIn("memory_metadata 标注本次选中的历史内容", instruction)
        self.assertIn("fixture-terminal-image", body)
        self.assertFalse(
            self.system.store.get_working_segments(namespace=self.system.namespace, provider_profile="openai_chat")
        )
        self.assertNotIn("Completed fixture", str(captured.public_messages()), "capture was mutated in place")

    def test_terminal_provider_wait_does_not_block_scheduler_or_new_input(self):
        self.tools()
        context, payloads = self.context()
        self.capture(context, payloads)
        completed = self.manager.complete_input_turn(
            turn_id=self.turn_id,
            assistant_record={"source_id": "final-nonblocking", "content": "finished", "timestamp": 102},
            memory_metadata=None,
            provider_output_raw="finished",
            provider_profile="openai_chat",
            provider_projection={"role": "assistant", "content": "finished"},
            **self.scope,
        )
        self.assertTrue(completed["ok"], completed)
        self.system.config.raw_token_trigger = 100
        entered, release = threading.Event(), threading.Event()

        def provider_wait(**kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture provider timed out")
            return self.terminal_summary_response()

        self.llm._create_completion.side_effect = provider_wait
        try:
            scheduled = self.engine._schedule_memcore_compaction(**self.scope)
            self.assertTrue(scheduled["ok"], scheduled)
            self.assertTrue(entered.wait(2))
            opened = self.manager.begin_input_turn(
                {"source_id": "new-input", "content": "new request during summary", "timestamp": 103},
                **self.scope,
            )
            self.assertTrue(opened["ok"], opened)
            self.tools(
                identifier="during-prior-summary",
                body="new task tool result during prior summary",
                turn_id=opened["turn_id"],
            )
            projection = self.manager.build_context_projection(provider_profile="openai_chat", **self.scope)
            self.assertIn("new request during summary", str(projection))
            self.assertNotIn("new request during summary", str(self.llm._create_completion.call_args))
        finally:
            release.set()
        self.assertTrue(self.wait_for_compaction())
        self.assertEqual(self.llm._create_completion.call_count, 1)
        projection = self.manager.build_context_projection(provider_profile="openai_chat", **self.scope)
        self.assertIn("new task tool result during prior summary", str(projection))

    def test_child_terminal_compaction_keeps_its_own_route_and_working_note(self):
        request = NS(child_session_id="subagent_terminal_fixture", run_id="run-terminal-child")
        child_llm = runtime("responses")
        child_llm.settings = replace(child_llm.settings, llm_auto_compact_token_limit=0)
        child_llm.supports_request_observer = True
        target = ModelExecutionTarget("chat", child_llm.chat, child_llm.chat.model, "fixture")
        memory = SubagentMemory(self.engine, request, child_llm, target)
        memory.open("child task")
        self.tools("child", scope=memory.scope, turn_id=memory.turn_id)
        history = memory.current()
        with patch.object(self.manager, "compact_due_background", return_value={"ok": True}):
            child_llm.capture_memcore_generation_request(
                system_prompt="child stable system",
                user_prompt=memory.input_content,
                temperature=0.1,
                history_turns=memory.past,
                post_user_turns=history,
                native_tools=TOOLS,
                request_observer=memory.observer(),
            )
        self.assertEqual(
            self.manager.compact_due_sync(provider_profile="responses", **memory.scope)["stats"]["working_status"],
            "committed",
        )
        system = self.manager._get_system(**memory.scope)
        system.config.raw_token_trigger = 100
        child_llm._create_completion.reset_mock()
        child_llm._create_completion.return_value = self.terminal_summary_response()
        memory.close(NS(status="completed", summary="child verified result", reason="", artifacts=[]))
        self.assertTrue(self.wait_for_compaction(system))
        self.assertEqual(child_llm._create_completion.call_count, 1)
        self.llm._create_completion.assert_not_called()
        body = str(child_llm._create_completion.call_args.kwargs["payload"]["messages"])
        self.assertIn("child stable system", body)
        self.assertIn("child verified result", body)
        self.assertIn("[working summary;", body)
        self.assertNotIn("fixture-key", body)

    def test_actual_current_payload_is_bound_then_cards_summary_and_b_are_read_at_next_boundary(self):
        self.tools()
        context, payloads = self.context()
        original_action = next(
            item["payload"]
            for item in self.manager.build_context_projection(provider_profile="openai_chat", **self.scope)["messages"]
            if item["payload"].get("tool_calls")
        )
        self.assertIn("content", original_action)  # canonical None disappears during actual assembly
        captured = self.capture(context, payloads)
        self.assertEqual(self.system.config.working_token_trigger, 1000)
        self.assertEqual(self.system._compaction._chat_context.transport, captured)
        result = self.manager.compact_due_sync(provider_profile="openai_chat", **self.scope)
        self.assertEqual(result["stats"]["working_status"], "committed", result)
        self.assertEqual(self.llm._create_completion.call_count, 1)
        self.tools("b", "B result")
        context2, payloads2 = self.context()
        self.assertIn("[working summary;", str(payloads2))
        self.assertLess(str(payloads2).index("[working summary;"), str(payloads2).index("B result"))
        self.capture(context2, payloads2)
        self.assertNotIn("memcore_request_projection_failure", context2)
        frozen = self.system.store.get_turn_projections(
            namespace=self.system.namespace, turn_id=self.turn_id, provider_profile="openai_chat"
        )
        self.assertTrue(
            ("verified output " * 800).strip() in str([m.payload for m in frozen]), "raw ledger was overwritten"
        )
        self.assertNotIn("[working summary;", str([m.payload for m in frozen]))
        self.assertEqual(self.llm._create_completion.call_count, 1)

    def test_hard_capture_does_not_consume_completion_receipts_or_change_configured_soft_limit(self):
        self.tools()
        store = HostJobStore(Path(self.temp.name) / "jobs.db")
        registry = HostCompletionWork(store, Mock())
        owner = HostJobOwner(self.scope["profile_user_id"], self.scope["session_id"])
        work = registry.begin(owner=owner, character_pack_id=self.scope["character_pack_id"],
                              channel="qq_text", turn_id=self.turn_id, request_bound=True)
        self.addCleanup(registry.close, work)
        self.engine.host_tool_jobs = NS(completion_work=registry)
        for has_job in (False, True):
            with self.subTest(has_job=has_job):
                if has_job:
                    created = store.create(owner=owner, capability_source="tool", capability_id="lookup",
                        payload={}, idempotency_key="running-call", argument_fingerprint="fixture",
                        character_pack_id=self.scope["character_pack_id"], channel="qq_text", turn_id=self.turn_id,
                        tool_call_id="running-call", completion_mode="agent", memory_mode="timeline")
                    self.assertTrue(created["ok"], created)
                    work.track(store.get(created["job_id"], owner=owner))
                context, payloads = self.context()
                with patch.object(work, "observe_request", wraps=work.observe_request) as observe:
                    self.capture(context, payloads, working_budget_tokens=500)
                observe.assert_not_called()
                expected = (created["job_id"],) if has_job else ()
                self.assertEqual(self.system._compaction._chat_context.pending_task_ids, expected)
                self.assertEqual(work.work.pending_ids, expected)
                self.assertFalse(work.queried)
        self.assertEqual(self.system._compaction._chat_context.working_token_limit, 500)
        self.assertEqual(self.system.config.working_token_trigger, 1000)

    def test_normal_transports_reach_provider_with_real_completion_work(self):
        self.llm.settings = replace(self.llm.settings, llm_auto_compact_token_limit=0)
        store = HostJobStore(Path(self.temp.name) / "normal-jobs.db")
        registry = HostCompletionWork(store, Mock())
        owner = HostJobOwner(self.scope["profile_user_id"], self.scope["session_id"])
        work = registry.begin(owner=owner, character_pack_id=self.scope["character_pack_id"],
                              channel="qq_text", turn_id=self.turn_id, request_bound=True)
        self.addCleanup(registry.close, work)
        self.engine.host_tool_jobs = NS(completion_work=registry)
        self.engine._resolve_turn_speaker_identity = lambda *_: {"assistant_name": "fixture"}
        self.engine._normalize_final_output = lambda *, result, **_: dict(result)
        self.engine._attach_memory_annotation_truth = lambda *_, **__: None
        self.engine._final_response_max_attempts = lambda _: 1

        def complete(*, payload, **_):
            body = '{"speech":"fixture valid reply"}'
            if payload.get("stream"):
                return [NS(choices=[NS(finish_reason="stop", delta=NS(content=body))])]
            return NS(choices=[NS(finish_reason="stop", message=NS(content=body))])

        self.llm._create_completion.side_effect = complete
        for transport in ("sync", "stream"):
            with self.subTest(transport=transport):
                context, _ = self.context()
                context.update(system_prompt="fixture system", user_prompt="Please develop fixture",
                               fallback={"speech": "", "tool_call": None}, visual_defaults={},
                               debug_enabled=False, allow_tool_call=True)
                args = {**self.scope, "user_message": "Please develop fixture", "now_ts": 100,
                        "recent_raw": [], "recent_episodic_summaries": [], "recent_semantic_summaries": [],
                        "confirmed_snippets": [], "request_projection_state": {"current_user_source_id": "input"}}
                with (
                    patch("companion_v01.engine_services.response_builder.prepare_context",
                          autospec=True, return_value=context),
                    patch.object(self.manager, "compact_due_background", return_value={"ok": True}),
                ):
                    if transport == "sync":
                        output = self.engine._build_final_response(**args)
                    else:
                        stream = self.engine._stream_final_response(**args)
                        while True:
                            try:
                                next(stream)
                            except StopIteration as stop:
                                output = stop.value
                                break
                self.assertEqual(output["speech"], "fixture valid reply")
                self.assertEqual(context["memcore_request_projection"]["status"], "recorded")
                self.assertEqual(self.system._compaction._chat_context.pending_task_ids, ())
                self.assertEqual(bool(self.system._compaction._chat_context.transport.payload.get("stream")),
                                 transport == "stream")
        self.assertEqual(self.llm._create_completion.call_count, 2)

    def test_same_background_future_is_waited_without_a_second_provider_call(self):
        future = Future()
        key = self.manager._compaction_namespace_key(self.system)
        self.manager._background_compactions[key] = future
        with patch.object(self.system, "compact_due_sync") as duplicate:
            timer = threading.Timer(
                0.05, lambda: future.set_result({"status": "compacted", "compaction_generation": 0})
            )
            timer.start()
            try:
                result = self.manager.compact_due_sync(provider_profile="openai_chat", **self.scope)
                self.assertTrue(result["ok"], result)
                duplicate.assert_not_called()
            finally:
                timer.join()
                self.manager._background_compactions.pop(key, None)

    def test_current_media_is_kept_only_in_in_memory_wire_snapshot(self):
        self.tools()
        context, payloads = self.context()
        self.capture(context, payloads, user_images=[{"url": "data:image/png;base64,fixture-image"}])
        result = self.manager.compact_due_sync(provider_profile="openai_chat", **self.scope)
        self.assertEqual(result["stats"].get("working_status"), "committed", result)
        sent = self.llm._create_completion.call_args.kwargs["payload"]
        self.assertTrue("fixture-image" in str(sent), "internal fork lost current image")
        self.assertNotIn(
            "fixture-image", str(self.manager.build_context_projection(provider_profile="openai_chat", **self.scope))
        )

    def test_child_uses_same_open_turn_and_its_own_runtime_and_pending_ids(self):
        request = NS(child_session_id="subagent_fixture", run_id="run-child")
        child_llm = runtime("responses")
        child_llm.settings = replace(child_llm.settings, llm_auto_compact_token_limit=0)
        child_llm.supports_request_observer = True
        target = ModelExecutionTarget("chat", child_llm.chat, child_llm.chat.model, "fixture")
        memory = SubagentMemory(self.engine, request, child_llm, target)
        memory.open("child task")
        self.tools(
            "child",
            body="child-pending: queued. " + "verified output " * 800,
            scope=memory.scope,
            turn_id=memory.turn_id,
        )
        history = memory.current()
        before_turn = memory.turn_id
        with patch.object(self.manager, "compact_due_background", return_value={"ok": True}):
            child_llm.capture_memcore_generation_request(
                system_prompt="child system",
                user_prompt=memory.input_content,
                temperature=0.1,
                history_turns=memory.past,
                post_user_turns=history,
                native_tools=TOOLS,
                request_observer=memory.observer(pending_task_ids=["child-pending"]),
            )
        result = self.manager.compact_due_sync(provider_profile="responses", **memory.scope)
        self.assertEqual(result["stats"].get("working_status"), "committed", result)
        memory.checkpoint_if_due(history, pending_ids=["child-pending"])
        self.assertEqual(memory.turn_id, before_turn)
        self.assertIn("child-pending", str(history))
        self.assertEqual(child_llm._create_completion.call_count, 1)
        self.llm._create_completion.assert_not_called()
        parent = self.manager.build_context_projection(provider_profile="openai_chat", **self.scope)
        self.assertNotIn("child-pending", str(parent))


class PrefixChildProtocolTests(unittest.TestCase):
    """Exercise existing child delivery paths under the opt-in request mode."""

    def setUp(self):
        from tests.test_subagent_engine import SubagentEngineTests

        SubagentEngineTests.setUp(self)
        mode = patch("config.MEMCORE_COMPACTION_REQUEST_MODE", "chat_prefix")
        mode.start()
        self.addCleanup(mode.stop)
        manager = self.engine.memcore_manager
        manager._memory_config = replace(manager._memory_config, compaction_request_mode="chat_prefix")

    def projection(self, agent=None):
        from tests.test_subagent_continuation import SubagentContinuationTests

        return SubagentContinuationTests.projection(self, agent)

    def normalize_scripted_requests(self):
        # Verify what the provider receives, rather than counting duplicated
        # text/content bookkeeping fields in the unnormalized ContextSurface.
        llm = runtime()
        original = self.engine.llm.call_chat_json_result

        def call(**kwargs):
            kwargs["post_user_turns"] = llm._normalize_history_turns_for_payload(
                kwargs.get("post_user_turns"), bundle=self.engine.llm.target.bundle
            )
            return original(**kwargs)

        self.engine.llm.call_chat_json_result = call

    def test_pending_tool_image_survives_context_refresh(self):
        from tests.test_subagent_engine import SubagentEngineTests

        SubagentEngineTests.test_tool_images_and_verified_artifact_receipts_reach_child_and_parent(self)

    def test_real_adapter_keeps_native_calls_reasoning_and_prefix_capture(self):
        from tests.test_subagent_engine import SubagentEngineTests

        SubagentEngineTests.test_real_llm_adapter_preserves_multi_round_native_calls_and_reasoning(self)

    def test_final_boundary_instruction_appears_once_on_provider_wire(self):
        from tests.test_subagent_continuation import SubagentContinuationTests

        self.normalize_scripted_requests()
        SubagentContinuationTests.test_instruction_arriving_with_final_response_runs_at_next_boundary_once(self)

    def test_signed_continuation_and_instruction_survive_refresh(self):
        from tests.test_subagent_continuation import SubagentContinuationTests

        self.normalize_scripted_requests()
        SubagentContinuationTests.test_signed_tool_replay_survives_instruction_history_refresh(self)


if __name__ == "__main__":
    unittest.main()
