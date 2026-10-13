from __future__ import annotations

from copy import deepcopy
import json
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from services import provider_continuation as state
from services.gemini_native_client import _build_openai_style_response, _build_gemini_payload, _GeminiStream, gemini_thinking_config
from services.llm_client import _build_anthropic_payload, _AnthropicStream
from companion_v01.llm_runtime import LLMRuntime, _ResponsesStreamAdapter
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.response_builder import _overlay_ephemeral_provider_evidence
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.tool_runtime import ToolExecutionResult


def call(identity="c1"):
    return {"id": identity, "type": "function", "function": {"name": "lookup", "arguments": '{"q":"x"}'}}


class WireResponse:
    def __init__(self, events):
        self.events, self.closed = events, False

    def iter_lines(self, **kwargs):
        for event, payload in self.events:
            if event:
                yield ("event: " + event).encode()
            yield ("data: " + json.dumps(payload)).encode()
            yield b""

    def close(self):
        self.closed = True


class ProviderContinuationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = LLMRuntime.__new__(LLMRuntime)
        self.runtime.settings = BotSettingsView(prompt_cache_hints_enabled=False)
        self.runtime._metrics = {}
        self.runtime._metrics_lock = threading.RLock()
        self.tools = [{"type": "function", "function": {"name": "lookup", "parameters": {
            "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}}]

    def bundle(self, protocol):
        return NS(client=NS(_akane_protocol=protocol, base_url="https://example.test/v1"), model="test-model")

    def projected(self, response, bundle, *, extras=None):
        calls = self.runtime._extract_native_tool_calls(response, native_tools=self.tools, bundle=bundle)
        carrier = state.capture(response, bundle=bundle, calls=calls, extras=extras)
        assistant = {"role": "assistant", "tool_calls": [call()]}
        if bundle.client._akane_protocol == "anthropic":
            assistant = {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "lookup", "input": {"q": "x"}}]}
        return state.overlay(assistant, {"c1": carrier}), carrier

    def test_gemini_complete_round_survives_host_projection_and_wire_conversion(self):
        parts = [{"text": "checking"}, {"functionCall": {"id": "c1", "name": "lookup", "args": {"q": "x"}},
                                         "thoughtSignature": "private-signature"}]
        response = _build_openai_style_response({"candidates": [{"content": {"parts": parts}}]}, model="test-model")
        bundle = self.bundle("gemini")
        self.runtime._create_completion = lambda **kwargs: response
        parsed = self.runtime._call_json(bundle=bundle, system_prompt="system", user_prompt="lookup",
                                        fallback={}, temperature=0.1, prompt_cache_key="test:continuation",
                                        native_tools=self.tools)
        normalized = {}
        AkaneMemoryEngine._attach_native_reasoning_content(normalized, raw_result=parsed)
        carrier = normalized[state.RESULT_FIELD]
        durable = [{"role": "assistant", "tool_calls": [call()]},
                   {"role": "tool", "tool_call_id": "c1", "content": "done"}]
        prepared = [state.overlay(durable[0], {"c1": carrier}), durable[1]]
        turns = _overlay_ephemeral_provider_evidence(durable, prepared)
        payload = self.runtime._build_completion_kwargs(bundle=bundle, system_prompt="system", user_prompt="lookup",
                                                        temperature=0.1, post_user_turns=turns, native_tools=self.tools)
        wire = _build_gemini_payload(payload)
        self.assertEqual(wire["contents"][1]["parts"], parts)
        self.assertEqual(wire["contents"][2]["parts"][0]["functionResponse"]["id"], "c1")
        persistent = self.runtime._persistent_turn_messages_from_payload(payload=payload, bundle=bundle,
                                     history_turns=None, ephemeral_turns=None, post_user_turns=turns)
        observed = []
        self.runtime._observe_completion_request(bundle=bundle, payload=payload, observer=observed.append,
                                                 persistent_turn_messages=persistent)
        self.assertNotIn("private-signature", json.dumps(observed))
        self.assertNotIn("private-signature", json.dumps(durable))

    def test_gemini_stream_retains_late_signature_on_same_call_part(self):
        part = {"functionCall": {"id": "c1", "name": "lookup", "args": {"q": "x"}}}
        stream = _GeminiStream(response=WireResponse([
            ("", {"candidates": [{"content": {"parts": [part]}}]}),
            ("", {"candidates": [{"content": {"parts": [{**part, "thoughtSignature": "late"}]}, "finishReason": "STOP"}]})
        ]), model="test-model")
        chunks = list(stream)
        emitted = [c for chunk in chunks for c in (chunk.choices[0].delta.tool_calls or [])]
        self.assertEqual(len(emitted), 1)
        self.assertEqual(stream.provider_content[0]["thoughtSignature"], "late")

    def test_openai_compatibility_late_extension_is_replayed_only_on_wire(self):
        collected = {}
        state.collect_chat_extras({"choices": [{"delta": {"tool_calls": [{"index": 0, **call()}]}}]}, collected)
        extra = {"google": {"thought_signature": "opaque"}}
        state.collect_chat_extras({"choices": [{"delta": {"tool_calls": [{"index": 0, "extra_content": extra}]}}]}, collected)
        response = NS(choices=[NS(message=NS(tool_calls=[call()]))])
        message, carrier = self.projected(response, self.bundle("openai"), extras=state.chat_extras(collected))
        sent = []
        bundle = self.bundle("openai")
        bundle.client.chat = NS(completions=NS(create=lambda **kwargs: sent.append(kwargs)))
        self.runtime._create_completion(bundle=bundle, payload={"messages": [message,
            {"role": "tool", "tool_call_id": "c1", "content": "done"}]})
        self.assertEqual(sent[0]["messages"][0]["tool_calls"][0]["extra_content"], extra)
        self.assertNotIn(state.FIELD, sent[0]["messages"][0])
        self.assertNotIn("opaque", json.dumps(state.public_message(message)))

    def test_changed_model_route_or_arguments_cannot_reuse_signed_state(self):
        response = NS(provider_content=[{"thoughtSignature": "private"}], choices=[NS(message=NS(tool_calls=[call()]))])
        bundle = self.bundle("gemini")
        message, _ = self.projected(response, bundle)
        for changed in (NS(client=bundle.client, model="other"), self.bundle("openai")):
            with self.assertRaisesRegex(ValueError, "route_changed"):
                self.runtime._normalize_post_user_turn_for_payload(message, bundle=changed)
        changed = deepcopy(message)
        changed["tool_calls"][0]["function"]["arguments"] = '{"q":"new"}'
        with self.assertRaisesRegex(ValueError, "batch_changed"):
            state.wire_data(changed, "gemini")

    def test_responses_preserves_encrypted_reasoning_item_order(self):
        output = [{"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque"},
                  {"type": "function_call", "id": "f1", "call_id": "c1", "name": "lookup", "arguments": '{"q":"x"}'}]
        response = self.runtime._adapt_responses_result({"status": "completed", "output": output})
        message, _ = self.projected(response, self.bundle("responses"))
        wire = self.runtime._responses_input_from_messages([message, {"role": "tool", "tool_call_id": "c1", "content": "done"}])
        self.assertEqual(wire[:2], output)
        self.assertEqual(wire[2]["type"], "function_call_output")
        stream = _ResponsesStreamAdapter(iter([
            {"type": "response.output_item.done", "output_index": i, "item": item} for i, item in enumerate(output)]))
        list(stream)
        self.assertEqual(stream.provider_content, output)

    def test_anthropic_stream_retains_signed_and_redacted_thinking(self):
        events = [
            ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "signed"}}),
            ("content_block_start", {"index": 1, "content_block": {"type": "redacted_thinking", "data": "redacted"}}),
            ("content_block_start", {"index": 2, "content_block": {"type": "tool_use", "id": "c1", "name": "lookup", "input": {}}}),
            ("content_block_delta", {"index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"q":"x"}'}}),
            ("content_block_stop", {"index": 2}),
        ]
        stream = _AnthropicStream(response=WireResponse(events), model="test-model")
        list(stream)
        response = NS(provider_content=stream.provider_content, choices=[NS(message=NS(content=stream.provider_content))])
        message, _ = self.projected(response, self.bundle("anthropic"))
        normalized = self.runtime._normalize_post_user_turn_for_payload(message, bundle=self.bundle("anthropic"))
        wire = _build_anthropic_payload({"model": "test-model", "messages": [normalized]})
        self.assertEqual(wire["messages"][0]["content"], stream.provider_content)
        self.assertEqual(wire["messages"][0]["content"][0]["signature"], "signed")
        self.assertNotIn("signed", json.dumps(state.public_message(normalized)))

    def test_host_rebuilds_two_tool_batches_without_persisting_continuation(self):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        payloads = [{"role": "assistant", "tool_calls": [call()]},
                    {"role": "tool", "tool_call_id": "c1", "content": "done"},
                    {"role": "assistant", "tool_calls": [call("c2")]},
                    {"role": "tool", "tool_call_id": "c2", "content": "done"}]
        projection = {"ok": True, "messages": [{"turn_id": "turn", "payload": m, "source_ids": [str(i)]}
                                               for i, m in enumerate(payloads)]}
        engine.memcore_manager = NS(build_open_turn_projection=lambda **kwargs: deepcopy(projection))
        carrier = {"route": state.route(self.bundle("gemini")), "calls": state.batch(payloads[0]),
                   "data": {"gemini": [{"thoughtSignature": "private"}]}}
        carrier2 = {**carrier, "calls": state.batch(payloads[2])}
        history = []
        result = engine._append_tool_history_batch(tool_history_turns=history,
            items=[({"type": "lookup", "_tool_source": "native_openai"}, ToolExecutionResult(tool_type="lookup"), "done")],
            trace_source_ids=["2", "3"], provider_profile="gemini", profile_user_id="u", session_id="s", character_pack_id="p",
            memcore_turn_id="turn", provider_continuations={"c1": carrier, "c2": carrier2})
        self.assertTrue(result["ok"], result)
        self.assertIn(state.FIELD, history[0])
        self.assertIn(state.FIELD, history[2])
        self.assertNotIn("private", json.dumps(projection))

    def test_gemini_reasoning_selection_reaches_actual_wire_and_rejects_unknown_levels(self):
        self.runtime.settings = BotSettingsView(llm_chat_reasoning_effort="high", prompt_cache_hints_enabled=False)
        bundle = self.bundle("gemini")
        bundle.model = "gemini-3.1-pro-preview"
        bundle.client._akane_bundle_role = "chat"
        payload = self.runtime._build_completion_kwargs(bundle=bundle, system_prompt="system", user_prompt="user", temperature=1)
        self.assertEqual(_build_gemini_payload(payload)["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})
        self.assertEqual(gemini_thinking_config("gemini-2.5-flash", "low"), {"thinkingBudget": 1024})
        self.assertEqual(gemini_thinking_config("gemini-2.5-flash", "none"), {"thinkingBudget": 0})
        self.assertEqual(gemini_thinking_config("unknown", ""), {})
        with self.assertRaisesRegex(ValueError, "cannot_be_disabled"):
            gemini_thinking_config("gemini-2.5-pro", "none")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            gemini_thinking_config("gemini-3.1-pro", "xhigh")

    def test_projection_mismatch_does_not_silently_drop_signed_state(self):
        response = NS(provider_content=[{"thoughtSignature": "private"}], choices=[NS(message=NS(tool_calls=[call()]))])
        message, _ = self.projected(response, self.bundle("gemini"))
        with self.assertRaisesRegex(ValueError, "projection_changed"):
            _overlay_ephemeral_provider_evidence([], [message])

    def test_sanitized_tool_arguments_are_aligned_before_the_signed_batch_gate(self):
        """Host scrubbing of stored arguments must not break signed replay."""

        carrier = {
            "route": state.route(self.bundle("openai")),
            "calls": [{"id": "c1", "name": "exec_run",
                       "arguments": {"command": "echo hello", "apiKey": "opaque-value"}}],
            "data": {"openai": {"c1": {"google": {"thought_signature": "private"}}}},
        }
        durable = {"role": "assistant", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "exec_run", "arguments": '{"command":"echo hello"}'}}]}
        with self.assertRaisesRegex(ValueError, "batch_changed"):
            state.overlay(durable, {"c1": carrier})
        aligned = state.align(carrier, lambda value: {k: v for k, v in value.items() if k.lower() != "apikey"})
        self.assertNotIn("apiKey", aligned["calls"][0]["arguments"])
        self.assertEqual(aligned["wire_calls"], carrier["calls"])
        overlaid = state.overlay(durable, {"c1": aligned})
        self.assertIn(state.FIELD, overlaid)
        self.assertEqual(overlaid[state.FIELD]["data"], carrier["data"])

    def test_real_argument_drift_is_still_rejected_after_alignment(self):
        carrier = {
            "route": state.route(self.bundle("openai")),
            "calls": [{"id": "c1", "name": "exec_run",
                       "arguments": {"command": "echo hello", "apiKey": "opaque-value"}}],
            "data": {"openai": {"c1": {"google": {"thought_signature": "private"}}}},
        }
        aligned = state.align(carrier, lambda value: {k: v for k, v in value.items() if k.lower() != "apikey"})
        drifted = {"role": "assistant", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "exec_run", "arguments": '{"command":"echo other"}'}}]}
        with self.assertRaisesRegex(ValueError, "batch_changed"):
            state.overlay(drifted, {"c1": aligned})

    def test_scrubbed_openai_replay_restores_original_arguments_only_on_wire(self):
        original = {"q": "x", "apiKey": "fixture-call-secret"}
        extra = {"google": {"thought_signature": "fixture-signature"}}
        bundle = self.bundle("openai")
        carrier = {"route": state.route(bundle),
                   "calls": [{"id": "c1", "name": "lookup", "arguments": original}],
                   "data": {"openai": {"c1": extra}}}
        aligned = state.align(carrier, lambda value: {k: v for k, v in value.items() if k != "apiKey"})
        message = state.overlay({"role": "assistant", "tool_calls": [call()]}, {"c1": aligned})
        sent = []
        bundle.client.chat = NS(completions=NS(create=lambda **kwargs: sent.append(kwargs)))
        turns = [message, {"role": "tool", "tool_call_id": "c1", "content": "done"}]
        payload = self.runtime._build_completion_kwargs(bundle=bundle, system_prompt="system", user_prompt="lookup",
            temperature=1, post_user_turns=turns, native_tools=self.tools)
        persistent = self.runtime._persistent_turn_messages_from_payload(payload=payload, bundle=bundle,
            history_turns=None, ephemeral_turns=None, post_user_turns=turns)
        observed = []
        self.runtime._observe_completion_request(bundle=bundle, payload=payload, observer=observed.append,
            persistent_turn_messages=persistent)
        self.runtime._create_completion(bundle=bundle, payload=payload)
        wire_message = next(item for item in sent[0]["messages"] if item.get("tool_calls"))
        wire_call = wire_message["tool_calls"][0]
        self.assertEqual(json.loads(wire_call["function"]["arguments"]), original)
        self.assertEqual(wire_call["extra_content"], extra)
        self.assertNotIn(state.FIELD, wire_message)
        self.assertNotIn("fixture-call-secret", json.dumps(state.public_message(message)))
        self.assertNotIn("fixture-call-secret", json.dumps(observed))
        self.assertNotIn("fixture-signature", json.dumps(observed))
        self.assertEqual(carrier["calls"][0]["arguments"], original)

    def test_scrubbed_native_replay_keeps_original_blocks_for_each_adapter(self):
        arguments = {"q": "x", "apiKey": "fixture-call-secret"}
        parts = [{"functionCall": {"id": "c1", "name": "lookup", "args": arguments},
                  "thoughtSignature": "fixture-signature"}]
        blocks = [{"type": "thinking", "thinking": "checking", "signature": "fixture-signature"},
                  {"type": "tool_use", "id": "c1", "name": "lookup", "input": arguments}]
        output = [{"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "fixture-signature"},
                  {"type": "function_call", "id": "f1", "call_id": "c1", "name": "lookup",
                   "arguments": json.dumps(arguments)}]
        for protocol, data in (("gemini", parts), ("anthropic", blocks), ("responses", output)):
            with self.subTest(protocol=protocol):
                bundle = self.bundle(protocol)
                carrier = {"route": state.route(bundle),
                           "calls": [{"id": "c1", "name": "lookup", "arguments": arguments}],
                           "data": {protocol: data}}
                message = {"role": "assistant", "tool_calls": [call()]}
                if protocol == "anthropic":
                    message = {"role": "assistant", "content": [{"type": "tool_use", "id": "c1",
                        "name": "lookup", "input": {"q": "x"}}]}
                message = state.overlay(message, {"c1": carrier},
                    sanitize=lambda value: {k: v for k, v in value.items() if k != "apiKey"})
                self.assertNotIn("wire_calls", message[state.FIELD])
                normalized = self.runtime._normalize_post_user_turn_for_payload(message, bundle=bundle)
                if protocol == "gemini":
                    self.assertEqual(_build_gemini_payload({"model": "test-model", "messages": [normalized]})
                                     ["contents"][0]["parts"], parts)
                elif protocol == "anthropic":
                    self.assertEqual(_build_anthropic_payload({"model": "test-model", "messages": [normalized]})
                                     ["messages"][0]["content"], blocks)
                else:
                    wire = self.runtime._responses_input_from_messages([normalized,
                        {"role": "tool", "tool_call_id": "c1", "content": "done"}])
                    self.assertEqual(wire[:2], output)
                    self.assertEqual(wire[2]["type"], "function_call_output")
                self.assertNotIn("fixture-call-secret", json.dumps(state.public_message(normalized)))

    def test_parallel_replay_aligns_shared_carrier_once_across_history_refreshes(self):
        calls = [{"id": identity, "name": "lookup", "arguments": {"q": identity, "apiKey": "fixture-secret"}}
                 for identity in ("c1", "c2")]
        carrier = {"route": state.route(self.bundle("openai")), "calls": calls,
                   "data": {"openai": {"c1": {"google": {"thought_signature": "fixture-signature"}}}}}
        states = {item["id"]: carrier for item in calls}
        message = {"role": "assistant", "tool_calls": [{"id": item["id"], "type": "function",
            "function": {"name": "lookup", "arguments": json.dumps({"q": item["id"]})}} for item in calls]}
        sanitized = []
        def scrub(value):
            sanitized.append(value)
            return {k: v for k, v in value.items() if k != "apiKey"}

        for _ in range(3):
            overlaid = state.overlay(message, states, sanitize=scrub)
            wire = state.chat_wire_message(overlaid)
            self.assertEqual([json.loads(item["function"]["arguments"]) for item in wire["tool_calls"]],
                             [item["arguments"] for item in calls])
        self.assertEqual(len(sanitized), 2)
        self.assertIs(states["c1"], states["c2"])
        self.assertNotIn("fixture-secret", json.dumps(state.public_message(overlaid)))

    def test_host_tool_trace_projection_survives_credential_scrubbing(self):
        """The real durable path must reproduce a scrubbed signed batch."""

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        projection = {"ok": True, "messages": [
            {"turn_id": "turn",
             "payload": {"role": "assistant", "tool_calls": [{
                 "id": "c1", "type": "function",
                 "function": {"name": "exec_run", "arguments": '{"command":"echo hello"}'}}]},
             "source_ids": ["0"]},
            {"turn_id": "turn",
             "payload": {"role": "tool", "tool_call_id": "c1", "content": "done"},
             "source_ids": ["1"]},
        ]}
        engine.memcore_manager = NS(build_open_turn_projection=lambda **kwargs: deepcopy(projection))
        carrier = {
            "route": state.route(self.bundle("openai")),
            "calls": [{"id": "c1", "name": "exec_run",
                       "arguments": {"command": "echo hello", "apiKey": "opaque-value"}}],
            "data": {"openai": {"c1": {"google": {"thought_signature": "private"}}}},
        }
        history = []
        result = engine._append_tool_history_batch(
            tool_history_turns=history,
            items=[({"type": "exec_run", "_tool_source": "native_openai"}, ToolExecutionResult(tool_type="exec_run"), "done")],
            trace_source_ids=["0", "1"], provider_profile="openai", profile_user_id="u", session_id="s",
            character_pack_id="p", memcore_turn_id="turn", provider_continuations={"c1": carrier})
        self.assertTrue(result["ok"], result)
        self.assertIn(state.FIELD, history[0])
        self.assertNotIn("apikey", json.dumps(state.public_message(history[0])).lower())

    def test_parallel_batch_through_real_memcore_retains_only_private_wire_state(self):
        from tests.test_memcore_integration import _FakeLLM, _FakeEmbeddingProvider
        from companion_v01.memcore_integration.manager import MemcoreManager
        with tempfile.TemporaryDirectory() as directory:
            manager = MemcoreManager(backend="memcore", storage_path=Path(directory) / "memory.db",
                visible_scope="conversation", enable_flavor=True, shadow_compare=False,
                llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
            try:
                scope = dict(profile_user_id="u", session_id="s", character_pack_id="p")
                opened = manager.begin_input_turn({"source_id": "input", "role": "user", "content": "lookup", "timestamp": 100}, **scope)
                batch = manager.record_tool_batch(exchanges=[
                    {"tool_name": "lookup", "tool_call_id": c, "tool_input": {"q": "x"}, "result": "done",
                     "source": "test", "timestamp": 101, "source_id_prefix": c, "result_status": "success"}
                    for c in ("c1", "c2")], turn_id=opened["turn_id"], **scope)
                self.assertTrue(batch["ok"], batch)
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.memcore_manager = manager
                parts = [{"functionCall": {"id": c, "name": "lookup", "args": {"q": "x"}},
                          **({"thoughtSignature": "private-signature"} if c == "c1" else {})} for c in ("c1", "c2")]
                carrier = {"route": state.route(self.bundle("gemini")),
                           "calls": state.batch({"tool_calls": [call(), call("c2")]}), "data": {"gemini": parts}}
                history = []
                result = engine._append_tool_history_batch(tool_history_turns=history,
                    items=[({"type": "lookup", "_tool_source": "native_openai"}, ToolExecutionResult(tool_type="lookup"), "done")],
                    trace_source_ids=[s for e in batch["exchanges"] for s in (e["tool_use_source_id"], e["tool_result_source_id"])],
                    provider_profile="gemini", memcore_turn_id=opened["turn_id"], current_user_source_id="input",
                    provider_continuations={"c1": carrier, "c2": carrier}, **scope)
                self.assertTrue(result["ok"], result)
                wire = _build_gemini_payload({"messages": history})
                self.assertEqual(wire["contents"][0]["parts"], parts)
                self.assertEqual(len(wire["contents"][1]["parts"]), 2)
                durable = manager.build_open_turn_projection(turn_id=opened["turn_id"], provider_profile="gemini", **scope)
                self.assertNotIn("private-signature", json.dumps(durable))
            finally:
                manager.close()

    def test_broken_stream_does_not_execute_a_partial_tool_batch(self):
        def broken():
            yield NS(choices=[NS(delta=NS(tool_calls=[{"index": 0, **call()}], content=None), finish_reason=None)])
            raise TimeoutError("synthetic_stream_interrupted")
        self.runtime._create_completion = lambda **kwargs: broken()
        generator = self.runtime._stream_chat_json(bundle=self.bundle("gemini"), system_prompt="s", user_prompt="u",
            fallback={}, temperature=0.1, early_tool_call_validator=None, prompt_cache_key="test:partial", native_tools=self.tools)
        while True:
            try:
                next(generator)
            except StopIteration as stopped:
                result = stopped.value
                break
        self.assertTrue(result.error)
        self.assertFalse(result.parsed.get("_native_tool_calls"))
        self.assertNotIn(state.RESULT_FIELD, result.parsed)

    def test_native_schema_keeps_references_and_unions_without_duplicate_sanitizing(self):
        schema = {"type": "object", "x-capcore-kind": "object", "additionalProperties": False,
                  "$defs": {"V": {"type": "integer"}},
                  "properties": {"x-user-name": {"oneOf": [{"$ref": "#/$defs/V"}, {"type": "string"}],
                                                "default": {"x-capcore-literal": "keep"}}}}
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": schema}}]
        payload = self.runtime._build_completion_kwargs(bundle=self.bundle("gemini"), system_prompt="s", user_prompt="u",
                                                        temperature=1, native_tools=tools)
        projected = _build_gemini_payload(payload)["tools"][0]["functionDeclarations"][0]
        self.assertNotIn("parameters", projected)
        self.assertEqual(projected["parametersJsonSchema"], {k: v for k, v in schema.items() if k != "x-capcore-kind"})
        self.assertIn("x-capcore-kind", schema)


if __name__ == "__main__":
    unittest.main()
