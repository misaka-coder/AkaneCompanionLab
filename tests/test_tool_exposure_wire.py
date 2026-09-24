"""Controlled provider-wire evidence using production context, recorder and clients.

Only unrelated persona/retrieval/workspace inputs and provider responses are fixed.
No provider messages, tools or catalog updates are normalized out of comparisons.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
import requests

import config
from companion_v01.client_protocol import ClientProtocolContext, ClientMode, default_capabilities_for_mode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.llm_runtime import LLMRuntime, ModelBundle
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from services.llm_client import AnthropicCompatClient
from tests.test_capability_exposure import CountingTool
from tests.test_memcore_integration import _FakeLLM, _FakeEmbeddingProvider, _PromptContextEngine


class WireEngine(_PromptContextEngine, AkaneMemoryEngine):
    def _resolve_capability_selection(self, **kwargs):
        # Static controlled host tools use the real admin selection path.
        kwargs.pop("client_context", None)
        return resolve_capability_selection(self, client_context=None, **kwargs)

    def _get_prompt_profile_registry(self):
        return PromptProfileRegistry()

    def _build_current_visual_context(self, **kwargs):
        return ""

    _build_tool_prompt_context = AkaneMemoryEngine._build_tool_prompt_context


class ControlledSummary:
    fail = False
    calls = 0

    def call_memcore_json(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("controlled_summary_failure")
        return {"diary_summary": "Controlled conversation completed.", "importance": 0.6,
                "key_events": ["Controlled tool use"], "core_facts": ["Controlled result"],
                "memory_metadata": {"entity_anchors": ["controlled"], "memory_facets": ["plan"],
                                    "about_roles": ["user"]}}


class ToolExposureWireTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        if getattr(self, "work_root", None) is not None:
            self.root = Path(self.work_root).resolve()
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, value in {"DATA_DIR": self.root, "MEMORY_BACKEND": "memcore",
                           "MEMCORE_RAW_TOKEN_TRIGGER": 1000,
                           "ENABLE_NATIVE_TOOL_DECISION": True}.items():
            self.stack.enter_context(patch.object(config, key, value))
        self.scope = dict(profile_user_id="wire-user", session_id="wire-session", character_pack_id="wire-character")
        self.summary = ControlledSummary()
        self.manager = MemcoreManager(backend="memcore", storage_path=self.root / "memory.db",
            visible_scope="conversation", enable_flavor=True, shadow_compare=False,
            llm=self.summary, embedding_provider=_FakeEmbeddingProvider())
        self.stack.callback(self.manager.close)
        self.engine = WireEngine(memcore_manager=self.manager)
        self.engine.capability_config_base_dir = self.root
        self.engine.tool_handlers = {"demo.alpha": CountingTool("demo.alpha")}
        self.blocks = ["Controlled stable plugin rule."]
        self.engine.prompt_builder = PromptBuilder(load_persona_config(),
            stable_system_blocks_provider=lambda: tuple(self.blocks))
        self.client_context = ClientProtocolContext(effective_mode=ClientMode.DESKTOP_PET,
            capabilities=default_capabilities_for_mode(ClientMode.DESKTOP_PET))
        self.requests = []
        self.round = 0
        self.next_call = None
        self.evidence = []

    def provider(self, protocol):
        self.protocol = protocol
        def content(payload):
            self.requests.append(deepcopy(payload))
            call = self.next_call
            if protocol == "openai":
                message = {"role": "assistant", "content": '{"speech":"Done"}'}
                if call:
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": call[2],
                        "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}
                return {"id": "recorded", "object": "chat.completion", "created": 1, "model": "wire-test",
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if call else "stop"}]}
            blocks = [{"type": "text", "text": '{"speech":"Done"}'}]
            if call:
                blocks = [{"type": "tool_use", "id": call[2], "name": call[0], "input": call[1]}]
            return {"id": "recorded", "type": "message", "role": "assistant", "model": "wire-test",
                "content": blocks, "stop_reason": "tool_use" if call else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1}}
        def body(payload):
            value = content(payload)
            if not payload.get("stream"):
                return json.dumps(value).encode()
            if protocol == "openai":
                message = value["choices"][0]["message"]
                delta = {"role": "assistant", "content": message["content"]}
                if message.get("tool_calls"):
                    delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]
                chunk = {"id":"recorded", "object":"chat.completion.chunk", "created":1, "model":"wire-test",
                    "choices":[{"index":0, "delta":delta, "finish_reason":None}]}
                end = deepcopy(chunk)
                end["choices"] = [{"index":0, "delta":{}, "finish_reason":value["choices"][0]["finish_reason"]}]
                return ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode()
            block = value["content"][0]
            events = [("message_start", {"type":"message_start", "message":{**value,"content":[]}})]
            if block["type"] == "tool_use":
                events.extend([("content_block_start", {"type":"content_block_start", "index":0,
                    "content_block":{**block,"input":{}}}),
                    ("content_block_delta", {"type":"content_block_delta", "index":0,
                    "delta":{"type":"input_json_delta", "partial_json":json.dumps(block["input"])}})])
            else:
                events.append(("content_block_start", {"type":"content_block_start", "index":0,"content_block":block}))
            events.extend([("content_block_stop", {"type":"content_block_stop","index":0}),
                ("message_delta", {"type":"message_delta","delta":{"stop_reason":value["stop_reason"]}, "usage":{"output_tokens":1}}),
                ("message_stop", {"type":"message_stop"})])
            return "".join("event: " + name + "\ndata: " + json.dumps(data) + "\n\n" for name,data in events).encode()
        if protocol == "openai":
            client = OpenAI(api_key="controlled-placeholder", base_url="http://test.invalid/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=body(json.loads(request.content)),
                        headers={"content-type":"text/event-stream" if json.loads(request.content).get("stream") else "application/json"}))))
            client._akane_protocol = protocol
            self.stack.callback(client.close)
        else:
            client = AnthropicCompatClient(api_key="controlled-placeholder", base_url="http://test.invalid")
            def post(_url, **kwargs):
                response = requests.Response()
                response.status_code = 200
                response._content = body(kwargs["json"])
                response._content_consumed = True
                return response
            self.stack.enter_context(patch("services.llm_client.requests.post", side_effect=post))
        bundle = ModelBundle(client, "wire-test")
        with ExitStack() as setup:
            for name in ("_build_chat_bundle", "_build_aux_bundle", "_build_memcore_summary_bundle"):
                setup.enter_context(patch.object(LLMRuntime, name, return_value=bundle))
            setup.enter_context(patch.object(LLMRuntime, "_build_vision_bundle", return_value=None))
            self.engine.llm = LLMRuntime(settings=BotSettingsView(), log_dir=self.root / "llm")

    def begin(self, index, text=""):
        self.source = f"{self.scope['session_id']}-user-{index}"
        self.text = text or f"Controlled user turn {index}."
        opened = self.manager.begin_input_turn({"source_id": self.source, "content": self.text, "timestamp": 100 + index}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.turn = opened["turn_id"]
        self.state = {"current_user_source_id": self.source}

    def request(self, action=None, stream=False):
        prepared = response_builder.prepare_context(self.engine, **self.scope,
            user_message=self.text, current_user_source_id=self.source, recent_raw=[],
            recent_episodic_summaries=[], recent_semantic_summaries=[], confirmed_snippets=[],
            now_ts=100, client_context=self.client_context, enable_native_tools=True,
            request_projection_state=self.state)
        self.assertNotIn("memcore_projection_failure", prepared, prepared)
        observer = self.engine._build_memcore_request_observer(generation_context=prepared,
            request_projection_state=self.state, **self.scope)
        self.assertIsNotNone(observer)
        kwargs = self.engine._build_final_response_request_kwargs(generation_context=prepared,
            request_projection_state=self.state, user_images=None, chat_model_override="", execution_target=None,
            prompt_cache_key="", retry_note="", request_observer=observer, allow_tool_call=True)
        before = len(self.requests)
        self.next_call = None
        if action is not None:
            selection = prepared[TOOL_CAPABILITY_SELECTION_FIELD]
            aliases = selection.native_tool_aliases
            name = next((alias for alias, target in aliases.items() if target == action[0]), action[0])
            self.round += 1
            self.next_call = (name, action[1], f"provider-call-{self.round}")
        if stream:
            generator = self.engine.llm.stream_chat_json(**kwargs)
            self.stream_events = []
            while True:
                try:
                    self.stream_events.append(next(generator))
                except StopIteration as stop:
                    result = stop.value
                    break
        else:
            result = self.engine.llm.call_chat_json_result(**kwargs)
        self.last_result, self.last_prepared = result, prepared
        self.assertFalse(result.error, result.error)
        self.assertEqual(len(self.requests), before + 1)
        return prepared, deepcopy(self.requests[-1])

    def tool(self, name="capability_load", arguments=None):
        self.round += 1
        selected = self.engine._resolve_capability_selection(**self.scope)
        args = arguments or {"capability_ids": ["demo.alpha"]}
        call = self.engine._normalize_tool_call({"type": name, **args}, capability_selection=selected, **{k:v for k,v in self.scope.items() if k != "character_pack_id"})
        from companion_v01.tool_orchestration_engine import execute_tool_invocation, normalize_tool_invocation
        invocation = normalize_tool_invocation(self.engine, call, capability_selection=selected,
            profile_user_id=self.scope["profile_user_id"], session_id=self.scope["session_id"])
        result, _ = execute_tool_invocation(self.engine, invocation=invocation, **self.scope, visual_payload={}, now_ts=100)
        recorded = self.manager.record_tool_batch(exchanges=[{
            "tool_name": name, "tool_call_id": f"call-{self.round}", "tool_input": args,
            "result": result.followup_context, "source": name, "timestamp": 100,
            "source_id_prefix": f"batch-{self.round}", "result_status": "success"}], turn_id=self.turn, **self.scope)
        self.assertTrue(recorded["ok"], recorded)
        return result

    def execute_response(self):
        output = {**self.last_result.parsed,
                  TOOL_CAPABILITY_SELECTION_FIELD: self.last_prepared[TOOL_CAPABILITY_SELECTION_FIELD]}
        final, calls, rejected = self.engine._prepare_tool_round_decisions(final_output=output,
            user_message=self.text, client_context=self.client_context,
            profile_user_id=self.scope["profile_user_id"], session_id=self.scope["session_id"])
        self.assertFalse(rejected, rejected)
        self.assertEqual(len(calls), 1, output)
        from companion_v01.tool_orchestration_engine import execute_tool_invocation, normalize_tool_invocation
        invocation = normalize_tool_invocation(self.engine, calls[0],
            capability_selection=calls[0].get(TOOL_CAPABILITY_SELECTION_FIELD),
            profile_user_id=self.scope["profile_user_id"], session_id=self.scope["session_id"])
        result, envelope = execute_tool_invocation(self.engine, invocation=invocation,
            **self.scope, visual_payload={}, now_ts=100)
        sources, failure = self.engine._record_memcore_tool_batch(items=[(calls[0],result,result.followup_context)],
            **self.scope, now_ts=100, current_user_source_id=self.source, memcore_turn_id=self.turn,
            recorded_tool_call_ids=set())
        self.assertIsNone(failure, failure)
        self.assertTrue(sources)
        return result, envelope

    @staticmethod
    def partitions(payload):
        system = payload.get("system", [m for m in payload["messages"] if m["role"] == "system"])
        history = [(m["role"], b) for m in payload["messages"] if m["role"] != "system"
                   for b in (m["content"] if isinstance(m.get("content"), list) else [m])]
        return payload.get("tools",[]), system, history

    def compare(self, group, scenario, left, right, *, history_prefix=False, tools_equal=True, system_equal=True):
        def digest(value):
            return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        def first_path(a,b,path="$"):
            if a == b:
                return None
            if isinstance(a,dict) and isinstance(b,dict):
                for key in sorted(set(a)|set(b)):
                    if key not in a or key not in b:
                        return path+"."+key
                    found = first_path(a[key],b[key],path+"."+key)
                    if found:
                        return found
            if isinstance(a,list) and isinstance(b,list):
                for i,(x,y) in enumerate(zip(a,b)):
                    found = first_path(x,y,f"{path}[{i}]")
                    if found:
                        return found
                return f"{path}[{min(len(a),len(b))}]"
            return path
        lt,ls,lh = self.partitions(left)
        rt,rs,rh = self.partitions(right)
        for payload in (left,right):
            calls,results = [],[]
            for message in payload["messages"]:
                calls.extend(call["id"] for call in message.get("tool_calls",[]))
                if message["role"] == "tool":
                    results.append(message["tool_call_id"])
                for block in message.get("content") or []:
                    if isinstance(block,dict) and block.get("type") == "tool_use":
                        calls.append(block["id"])
                    if isinstance(block,dict) and block.get("type") == "tool_result":
                        results.append(block["tool_use_id"])
            self.assertEqual(calls,results)
            self.assertEqual(len(calls),len(set(calls)))
        prefix = next((i for i,(a,b) in enumerate(zip(lh,rh)) if a != b), min(len(lh),len(rh)))
        if tools_equal:
            self.assertEqual(lt,rt)
        if system_equal:
            self.assertEqual(ls,rs)
        if history_prefix:
            self.assertEqual(lh, rh[:len(lh)])
        record = {"group":group,"scenario":scenario,"protocol":self.protocol,
            "tools_sha256":[digest(lt),digest(rt)],"system_sha256":[digest(ls),digest(rs)],
            "history_blocks":[len(lh),len(rh)],"history_common_prefix_blocks":prefix,
            "first_tools_difference":first_path(lt,rt),"first_system_difference":first_path(ls,rs),
            "first_raw_messages_difference":first_path(left["messages"],right["messages"]),
            "raw_message_common_prefix":next((i for i,(a,b) in enumerate(zip(left["messages"],right["messages"])) if a != b),min(len(left["messages"]),len(right["messages"]))),
            "complete_tool_pairs":len(calls),
            "first_history_difference":prefix if lh != rh else None,
            "expected_change": "tail_append" if history_prefix else "real_compaction_or_explicit_system_revocation",
            "online_cache_metrics":"not_measured"}
        directory = os.environ.get("AKANE_EXPOSURE_WIRE_REPORT_DIR")
        if directory:
            path = Path(directory)
            path.mkdir(parents=True,exist_ok=True)
            (path / f"{self.protocol}-{group}-{scenario}.json").write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        self.evidence.append(record)

    def finish(self):
        finished = self.manager.complete_input_turn(turn_id=self.turn,
            assistant_record={"source_id": self.source + "-final", "content": "Done", "timestamp": 101},
            memory_metadata={}, provider_output_raw='{"speech":"Done"}', provider_profile=self.protocol,
            provider_projection={"role": "assistant", "content": (
                [{"type": "text", "text": '{"speech":"Done"}'}] if self.protocol == "anthropic"
                else '{"speech":"Done"}')}, **self.scope)
        self.assertTrue(finished["ok"], finished)

    def test_openai_updates_and_tool_pairs_stay_in_published_order(self):
        self.provider("openai")
        self.begin(1)
        _, first = self.request()
        self.tool()
        _, loaded = self.request()
        self.assertEqual(first["tools"], loaded["tools"])
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        _, changed = self.request()
        self.tool(arguments={"capability_ids": ["demo.beta"]})
        _, after = self.request()
        self.engine.tool_handlers.pop("demo.beta")
        _, removed = self.request()
        self.assertEqual(first["tools"], removed["tools"])
        updates = [m for m in removed["messages"] if "【能力目录更新】" in str(m.get("content", ""))]
        self.assertEqual(len(updates), 2)
        self.assertIn("当前可加载", updates[0]["content"])
        self.assertIn("当前不可用", updates[1]["content"])
        for left, right in zip((first, loaded, changed, after), (loaded, changed, after, removed)):
            self.assertEqual(left["messages"], right["messages"][:len(left["messages"])])
        self.finish()
        self.begin(2)
        _, next_turn = self.request()
        self.assertEqual(removed["messages"], next_turn["messages"][:len(removed["messages"])])
        tool_results = [m for m in next_turn["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_results], ["call-1", "call-2"])

    def test_anthropic_updates_and_tool_pairs_stay_in_published_order(self):
        self.provider("anthropic")
        self.begin(1)
        _, first = self.request()
        self.tool()
        _, loaded = self.request()
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        _, changed = self.request()
        self.tool(arguments={"capability_ids": ["demo.beta"]})
        _, after = self.request()
        self.engine.tool_handlers.pop("demo.beta")
        _, removed = self.request()
        self.assertEqual(first["tools"], removed["tools"])
        self.assertEqual(first["system"], removed["system"])
        # Anthropic legitimately coalesces adjacent same-role messages. Check
        # the exact ordered blocks too, without discarding any payload fields.
        def blocks(request):
            return [(m["role"], block) for m in request["messages"]
                    for block in (m["content"] if isinstance(m["content"], list) else [m["content"]])]
        for left, right in zip((first, loaded, changed, after), (loaded, changed, after, removed)):
            self.assertEqual(blocks(left), blocks(right)[:len(blocks(left))])
        self.finish()
        self.begin(2)
        _, next_turn = self.request()
        self.assertEqual(blocks(removed), blocks(next_turn)[:len(blocks(removed))])
        results = [b["tool_use_id"] for _, b in blocks(next_turn) if isinstance(b, dict) and b["type"] == "tool_result"]
        self.assertEqual(results, ["call-1", "call-2"])

    def boundary(self, scenario, protocol):
        self.provider(protocol)
        self.stack.enter_context(patch.object(config, "MEMCORE_RAW_TOKEN_TRIGGER", 1000))
        self.scope["session_id"] = f"boundary-{scenario}-{protocol}"
        self.begin(1, "Controlled history material. " * 1300)
        before_context, before = self.request()
        generation = before_context["tool_exposure_lifecycle"]["compaction_generation"]
        if scenario in {"pending", "netzero", "failure"}:
            self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
            pending, during = self.request()
            self.assertTrue(pending["tool_exposure_lifecycle"]["pending"])
            self.assertEqual(before["tools"], during["tools"])
        if scenario == "netzero":
            self.engine.tool_handlers.pop("demo.beta")
            self.request()
        self.finish()
        if scenario == "failure":
            self.summary.fail = True
        compacted = self.manager.compact_due_sync(provider_profile=protocol, **self.scope)
        self.begin(2)
        after_context, after = self.request()
        next_generation = after_context["tool_exposure_lifecycle"]["compaction_generation"]
        if scenario == "failure":
            self.assertGreater(self.summary.calls, 0, compacted)
            self.assertEqual(generation, next_generation, compacted)
            self.assertTrue(after_context["tool_exposure_lifecycle"]["pending"])
            self.assertEqual(before["tools"], after["tools"])
        else:
            self.assertGreater(next_generation, generation, compacted)
            self.assertFalse(after_context["tool_exposure_lifecycle"]["pending"])
            if scenario == "pending":
                self.assertNotEqual(before["tools"], after["tools"])
            else:
                self.assertEqual(before["tools"], after["tools"])
        def system(payload):
            return payload.get("system", [m for m in payload["messages"] if m["role"] == "system"])
        self.assertEqual(system(before), system(after))
        self.compare("boundary", scenario, before, after, tools_equal=scenario != "pending")

    def test_compaction_in_prepare_reads_final_provider_and_preference_state(self):
        from companion_v01.capability_exposure_config import save_preferences
        self.provider("openai")
        self.begin(1, "Controlled completed material. " * 1300)
        initial, before = self.request()
        generation = initial["tool_exposure_lifecycle"]["compaction_generation"]
        self.finish()
        self.begin(2)
        original = self.summary.call_memcore_json
        def change_during_summary(**kwargs):
            self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
            self.blocks.append("New rule published during summary.")
            saved = save_preferences(profile_user_id=self.scope["profile_user_id"], base_dir=self.root,
                                     payload={"revision":0,"toolModes":{"demo.alpha":"on_demand"}})
            self.assertTrue(saved["ok"],saved)
            return original(**kwargs)
        with patch.object(self.summary, "call_memcore_json", side_effect=change_during_summary), \
                patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 100):
            final, after = self.request()
        self.assertTrue(final["prompt_budget"]["compact_attempted"])
        self.assertGreater(final["tool_exposure_lifecycle"]["compaction_generation"],generation)
        selected = final[TOOL_CAPABILITY_SELECTION_FIELD]
        self.assertIn("demo.beta",selected.schema_tool_names)
        self.assertNotIn("demo.alpha",selected.schema_tool_names)
        self.assertFalse(final["tool_exposure_lifecycle"]["pending"])
        self.assertIn("New rule published during summary.",repr(after))
        self.compare("boundary", "change_during_summary", before,after,tools_equal=False,system_equal=False)

    def test_openai_real_compaction_without_changes_preserves_tools_system(self):
        self.boundary("unchanged", "openai")

    def test_openai_real_compaction_merges_pending_final_state(self):
        self.boundary("pending", "openai")

    def test_openai_real_compaction_net_zero_preserves_tools_system(self):
        self.boundary("netzero", "openai")

    def test_openai_failed_compaction_keeps_old_generation_and_pending_state(self):
        self.boundary("failure", "openai")

    def test_anthropic_real_compaction_without_changes_preserves_tools_system(self):
        self.boundary("unchanged", "anthropic")

    def test_anthropic_real_compaction_merges_pending_final_state(self):
        self.boundary("pending", "anthropic")

    def test_anthropic_real_compaction_net_zero_preserves_tools_system(self):
        self.boundary("netzero", "anthropic")

    def test_anthropic_failed_compaction_keeps_old_generation_and_pending_state(self):
        self.boundary("failure", "anthropic")


    def no_changes(self, protocol, stream):
        self.provider(protocol)
        previous = None
        for index in range(1,4):
            self.begin(index)
            _, request = self.request(action=("demo.alpha", {"text":str(index)}), stream=stream)
            if previous:
                self.compare("unchanged", f"{stream}-user-{index}", previous, request, history_prefix=True)
            result, _ = self.execute_response()
            self.assertEqual(result.followup_context,str(index))
            _, after = self.request(stream=stream)
            self.compare("unchanged", f"{stream}-tool-{index}", request, after, history_prefix=True)
            previous = after
            self.finish()
        self.assertEqual(len(self.engine.tool_handlers["demo.alpha"].calls),3)
        self.assertNotIn("【能力目录更新】",str(previous))

    def in_era_recovery(self, protocol, stream):
        from companion_v01.capability_exposure_config import save_preferences
        self.provider(protocol)
        self.begin(1, "Controlled material for real compaction. " * 1300)
        original, first = self.request()
        initial_generation = original["tool_exposure_lifecycle"]["compaction_generation"]
        self.request(action=("capability_load", {"capability_ids":["demo.alpha"]}),stream=stream)
        loaded, _ = self.execute_response()
        _, after_load = self.request()
        self.compare("in-era",f"{stream}-load",first,after_load,history_prefix=True)
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        _, installed = self.request(action=("capability_load", {"capability_ids":["demo.beta"]}),stream=stream)
        self.compare("in-era",f"{stream}-install",after_load,installed,history_prefix=True)
        loaded, _ = self.execute_response()
        contract = json.loads(loaded.followup_context.split("\n",1)[1])["capabilities"][0]
        self.request(action=("capability_invoke",{"capability_id":"demo.beta","contract_ref":contract["contract_ref"],
                                               "arguments":{"text":"new tool"}}),stream=stream)
        called,_ = self.execute_response()
        self.assertEqual(called.followup_context,"new tool")
        self.assertEqual(len(self.engine.tool_handlers["demo.beta"].calls),1)
        _, invoked = self.request()
        self.compare("in-era",f"{stream}-new-tool-invoke",installed,invoked,history_prefix=True)
        self.engine.tool_handlers.pop("demo.beta")
        _, uninstalled = self.request()
        self.compare("in-era",f"{stream}-uninstall",invoked,uninstalled,history_prefix=True)
        saved = save_preferences(base_dir=self.root,profile_user_id=self.scope["profile_user_id"],payload={
            "revision":0,"toolModes":{"demo.alpha":"on_demand"}})
        self.assertTrue(saved["ok"],saved)
        _, preference = self.request()
        self.compare("in-era",f"{stream}-preference",uninstalled,preference,history_prefix=True,tools_equal=False)
        self.assertNotIn("demo.alpha", self.last_prepared[TOOL_CAPABILITY_SELECTION_FIELD].schema_tool_names)
        # Returning to resident also publishes immediately, without compression.
        saved = save_preferences(base_dir=self.root,profile_user_id=self.scope["profile_user_id"],payload={
            "revision":saved["preferences"]["revision"],"toolModes":{"demo.alpha":"resident"}})
        self.assertTrue(saved["ok"], saved)
        self.request(action=("demo.alpha",{"text":"10"}),stream=stream)
        result,_ = self.execute_response()
        self.assertEqual(result.followup_context,"10")
        old = self.engine.tool_handlers["demo.alpha"]
        updated = CountingTool("demo.alpha",revision="amount-now-cents")
        self.assertEqual(old.tool_spec(),updated.tool_spec())
        self.engine.tool_handlers["demo.alpha"] = updated
        _, stale_request = self.request(action=("demo.alpha",{"text":"10"}),stream=stream)
        rejected,_ = self.execute_response()
        self.assertFalse(json.loads(rejected.followup_context)["executed"])
        self.assertEqual(updated.calls,[])
        _, rejection_result = self.request(action=("capability_load",{"capability_ids":["demo.alpha"]}),stream=stream)
        self.compare("in-era",f"{stream}-stale-native",stale_request,rejection_result,history_prefix=True)
        loaded,_ = self.execute_response()
        current = json.loads(loaded.followup_context.split("\n",1)[1])["capabilities"][0]
        self.request(action=("capability_invoke",{"capability_id":"demo.alpha","contract_ref":current["contract_ref"],
                                               "arguments":{"text":"10"}}),stream=stream)
        result,_ = self.execute_response()
        self.assertEqual(result.followup_context,"10")
        self.assertEqual(len(updated.calls),1)
        _, recovered = self.request()
        self.compare("in-era",f"{stream}-recover",rejection_result,recovered,history_prefix=True)
        self.request(action=("demo.alpha",{"text":"10"}),stream=stream)
        rejected,_ = self.execute_response()
        self.assertFalse(json.loads(rejected.followup_context)["executed"])
        self.assertEqual(len(updated.calls),1)
        # An explicit exposure change republishes the current native contracts.
        saved = save_preferences(base_dir=self.root,profile_user_id=self.scope["profile_user_id"],payload={
            "revision":saved["preferences"]["revision"],"searchEnabled":False})
        self.assertTrue(saved["ok"],saved)
        self.request()
        self.finish()
        self.begin(2)
        final, wire = self.request(action=("demo.alpha",{"text":"10"}),stream=stream)
        self.assertEqual(final["tool_exposure_lifecycle"]["compaction_generation"],initial_generation)
        self.assertNotIn("capability_search",final[TOOL_CAPABILITY_SELECTION_FIELD].schema_tool_names)
        result,_ = self.execute_response()
        self.assertEqual(result.followup_context,"10")
        self.assertEqual(len(updated.calls),2)
        self.compare("preference",f"{stream}-contract-recovery",recovered,wire,tools_equal=False,history_prefix=True)

    def plugin_rules(self, protocol, text_only):
        from dataclasses import replace
        from companion_v01.client_protocol import ClientCapability
        self.provider(protocol)
        if text_only:
            self.client_context = self.client_context.model_copy(update={"capabilities":frozenset(
                c for c in self.client_context.capabilities if c != ClientCapability.TOOL_ACTIONS)})
        self.begin(1)
        prepared, first = self.request()
        self.assertTrue(prepared["tool_exposure_lifecycle"]["ok"])
        if text_only:
            self.assertFalse(first.get("tools"))
        self.blocks.append("Controlled newly installed rule.")
        prepared, installed = self.request()
        self.assertTrue(prepared["tool_exposure_lifecycle"]["pending"])
        self.compare("system",f"{text_only}-install",first,installed,history_prefix=True)
        self.blocks.pop(0)
        prepared, revoked = self.request()
        self.assertTrue(prepared["tool_exposure_lifecycle"]["system_revocation_exception"])
        self.assertNotIn("Controlled stable plugin rule.",str(self.partitions(revoked)[1]))
        self.assertNotIn("Controlled newly installed rule.",str(self.partitions(revoked)[1]))
        self.compare("system",f"{text_only}-revoke",installed,revoked,system_equal=False)


def _scenario(method, *arguments):
    def test(self):
        return getattr(self,method)(*arguments)
    return test

for _protocol in ("openai","anthropic"):
    for _stream in (False,True):
        setattr(ToolExposureWireTests,f"test_{_protocol}_unchanged_stream_{_stream}",_scenario("no_changes",_protocol,_stream))
        setattr(ToolExposureWireTests,f"test_{_protocol}_recovery_stream_{_stream}",_scenario("in_era_recovery",_protocol,_stream))
    for _text_only in (False,True):
        setattr(ToolExposureWireTests,f"test_{_protocol}_plugin_rules_text_only_{_text_only}",_scenario("plugin_rules",_protocol,_text_only))
