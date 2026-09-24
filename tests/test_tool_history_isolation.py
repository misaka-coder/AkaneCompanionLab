import copy
from types import SimpleNamespace
import unittest

from services.tool_history import isolate_tool_history
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.memcore_integration.manager import MemcoreManager


def call(identifier="live"):
    return {"id": identifier, "type": "function", "function": {"name": "lookup", "arguments": '{ "q": "test" }'}}


def pair(identifier="live"):
    return [{"role": "assistant", "tool_calls": [call(identifier)], "reasoning_content": "private reasoning"},
            {"role": "tool", "tool_call_id": identifier, "content": "result"}]


class ToolHistoryIsolationTests(unittest.TestCase):
    def test_broken_history_keeps_complete_siblings_text_and_input_unchanged(self):
        messages = [{"role": "tool", "tool_call_id": "missing", "content": "PRIVATE ORPHAN"},
                    {"role": "user", "content": "old request"},
                    {"role": "assistant", "content": "checking", "tool_calls": [call("done"), call("interrupted")]},
                    {"role": "tool", "tool_call_id": "done", "content": "real result"},
                    {"role": "user", "content": "new request"}, *pair()]
        before = copy.deepcopy(messages)
        result = isolate_tool_history(messages)
        self.assertEqual(result.source_indexes, [1, 2, 3, 4, 5, 6])
        self.assertEqual(result.messages[1]["tool_calls"], [call("done")])
        self.assertEqual(result.messages[-2:], pair())
        self.assertEqual(messages, before)
        self.assertNotIn("PRIVATE", str(result.issues))
        self.assertEqual(isolate_tool_history(result.messages).issues, [])
        self.assertEqual(isolate_tool_history(result.messages).messages, result.messages)

    def test_duplicate_results_wrong_order_and_cross_turn_pairs_are_isolated(self):
        valid = pair()
        cases = [
            [valid[1], valid[0]],
            [valid[0], {"role": "user", "content": "boundary"}, valid[1]],
            [valid[0], valid[1], valid[1]],
            [*valid, *valid],
            [{"role": "assistant", "tool_calls": [call(), call()]}, valid[1]],
        ]
        for messages in cases:
            with self.subTest(messages=messages):
                result = isolate_tool_history(messages)
                self.assertTrue(result.issues)
                self.assertEqual(isolate_tool_history(result.messages).issues, [])
                self.assertLessEqual(sum(row["role"] == "tool" for row in result.messages), 1)

    def test_malformed_items_cannot_throw_or_form_protocol_shells(self):
        for bad in (None, "bad", 3, [], {}, {"id": []}, {"id": "x", "function": []}):
            result = isolate_tool_history([
                {"role": "assistant", "tool_calls": [bad]},
                {"role": "tool", "tool_call_id": [], "content": "orphan"},
                {"role": "user", "content": "still usable"}])
            self.assertEqual(result.messages, [{"role": "user", "content": "still usable"}])
        self.assertEqual(isolate_tool_history([None, "bad", *pair()]).messages, pair())

    def test_anthropic_blocks_preserve_complete_pairs_and_multimodal_content(self):
        text = {"type": "text", "text": "ordinary text"}
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "fixture"}}
        use = {"type": "tool_use", "id": "a", "name": "lookup", "input": {"q": "test"}}
        output = {"type": "tool_result", "tool_use_id": "a", "content": "real result"}
        good = [{"role": "assistant", "content": [text, use]},
                {"role": "user", "content": [output, image, text]}]
        self.assertEqual(isolate_tool_history(good).messages, good)
        bad = [{"role": "user", "content": [output]},
               {"role": "assistant", "content": [text, use]},
               {"role": "user", "content": [text, output]}]
        cleaned = isolate_tool_history(bad)
        self.assertEqual(cleaned.messages, [{"role": "assistant", "content": [text]},
                                            {"role": "user", "content": [text]}])
        self.assertEqual(isolate_tool_history(cleaned.messages).issues, [])

    def test_invalid_tool_shells_and_unrenderable_calls_do_not_survive_conversion(self):
        runtime = LLMRuntime.__new__(LLMRuntime)
        invalid = call()
        invalid["function"]["name"] = "not.a.native.name"
        messages = [{"role": "assistant", "tool_calls": [invalid]}, pair()[1]]
        self.assertEqual(runtime._responses_input_from_messages(messages), [])
        for messages in (
            [{"role": "assistant", "tool_calls": [], "content": None}],
            [pair()[0], {"role": "tool", "tool_call_id": "live", "content": None}],
            [{"role": "assistant", "content": [
                {"type": "thinking", "thinking": "private", "signature": "test"},
                {"type": "tool_use", "id": "missing", "name": "lookup", "input": {}}]}],
        ):
            self.assertEqual(isolate_tool_history(messages).messages, [])

    def test_all_runtime_protocols_isolate_before_wire_and_observer_keeps_current_source(self):
        for protocol in ("openai", "responses", "ollama", "gemini", "anthropic"):
            with self.subTest(protocol=protocol):
                runtime = LLMRuntime.__new__(LLMRuntime)
                seen = []
                def create(**kwargs):
                    seen.append(kwargs)
                    return SimpleNamespace(status="completed", output_text="ok", output=[], usage=None)
                client = SimpleNamespace(_akane_protocol=protocol, protocol=protocol,
                    base_url="https://example.invalid/v1", chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                    responses=SimpleNamespace(create=create))
                bundle = SimpleNamespace(client=client, model="test-model")
                history = [{"role": "tool", "tool_call_id": "legacy", "content": "PRIVATE BAD HISTORY"},
                           {"role": "assistant", "content": "ordinary history"}]
                payload = runtime._build_completion_kwargs(bundle=bundle, system_prompt="system", user_prompt="CURRENT",
                    temperature=0.2, history_turns=history, ephemeral_turns=[{"role": "user", "content": "ephemeral"}],
                    post_user_turns=[{"role": "assistant", "content": "current reply"}], stream=True)
                persistent = runtime._persistent_turn_messages_from_payload(payload=payload, bundle=bundle,
                    history_turns=history, ephemeral_turns=[{"role": "user", "content": "ephemeral"}],
                    post_user_turns=[{"role": "assistant", "content": "current reply"}])
                self.assertEqual(persistent, [{"role": "user", "content": "CURRENT"},
                                               {"role": "assistant", "content": "current reply"}])
                observed = []
                runtime._observe_completion_request(bundle=bundle, payload=payload,
                    observer=lambda event: observed.append(event), persistent_turn_messages=persistent)
                runtime._create_completion(bundle=bundle, payload=payload)
                self.assertEqual(len(seen), 1)
                self.assertNotIn("PRIVATE BAD HISTORY", str(seen))
                self.assertNotIn("persistent_message_indexes", str(seen))
                self.assertEqual(observed[0]["persistent_turn_messages"], persistent)

    def test_direct_chat_and_compatibility_retry_cannot_replay_bad_history(self):
        runtime = LLMRuntime.__new__(LLMRuntime)
        seen = []
        def create(**kwargs):
            seen.append(kwargs)
            if "prompt_cache_key" in kwargs:
                raise TypeError("unsupported prompt_cache_key")
            return "ok"
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="openai",
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        runtime._create_completion(bundle=bundle, payload={"messages": [
            {"role": "tool", "tool_call_id": "old", "content": "bad"}, *pair()], "prompt_cache_key": "cache"})
        self.assertEqual(len(seen), 2)
        for wire in seen:
            self.assertEqual(wire["messages"], pair())

    def test_memcore_surface_reindexes_sources_without_touching_durable_projection(self):
        from memcore import stable_projection_hash
        from companion_v01.memcore_integration.context_validation import isolate_context_surface
        history = [{"role": "tool", "tool_call_id": "orphan", "content": "PRIVATE"},
                   {"role": "assistant", "content": "kept", "tool_calls": [call("dangling")]}, *pair()]
        current = {"role": "user", "content": "CURRENT"}
        messages = [*history, current]
        surface = {"version": "context_surface_v1", "provider_profile": "openai_chat", "history_messages": history,
            "current_message": current, "active_turn_messages": [], "messages": messages,
            "message_source_ids": [[f"source-{i}"] for i in range(len(messages))],
            "message_projection_metadata": [{"turn_id": "old" if i < len(history) else "current",
                "source_ids": [f"source-{i}"], "projection_index": i, "projection_status": "canonical_fallback",
                "projection_version": 1} for i in range(len(messages))], "projection_hash": "old", "diagnostics": []}
        original = copy.deepcopy(surface)
        system = SimpleNamespace(build_context_surface=lambda **kwargs: SimpleNamespace(
            as_dict=lambda: copy.deepcopy(surface), messages=messages))
        manager = MemcoreManager.__new__(MemcoreManager)
        manager._get_system_or_none = lambda **kwargs: system
        result = manager.build_context_surface(provider_profile="deepseek", current_source_id="source-4",
            profile_user_id="alice", session_id="private")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["history_messages"]), 3)
        self.assertEqual(result["message_source_ids"][3], ["source-4"])
        self.assertEqual(result["message_projection_metadata"][3]["turn_id"], "current")
        self.assertNotIn("PRIVATE", str(result))
        self.assertEqual(surface, original)
        self.assertEqual(isolate_context_surface(result), result)
        self.assertEqual(result["projection_hash"], stable_projection_hash({key: result[key] for key in (
            "version", "provider_profile", "history_messages", "current_message", "active_turn_messages")}))


if __name__ == "__main__":
    unittest.main()
