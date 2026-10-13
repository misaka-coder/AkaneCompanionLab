"""Numeric reuse must preserve budgets and the real request's audit evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.model_runtime.models import ModelBundle
from companion_v01 import text_utils


class PromptCountCacheTests(unittest.TestCase):
    def setUp(self):
        text_utils._estimate_cached_prompt_tokens.cache_clear()

    def tearDown(self):
        text_utils._estimate_cached_prompt_tokens.cache_clear()

    def test_coercion_happens_before_reuse_and_observes_mutable_inputs(self):
        class Text:
            value = "abcde"

            def __str__(self):
                return self.value

        value = Text()
        self.assertEqual(text_utils.estimate_prompt_tokens(value), 2)
        value.value = "你好abcde"
        self.assertEqual(text_utils.estimate_prompt_tokens(value), 4)
        self.assertEqual(text_utils.estimate_prompt_tokens(None), 0)
        self.assertEqual(text_utils.estimate_prompt_tokens([]), 0)
        value.value = "abcde"
        self.assertEqual(text_utils.estimate_prompt_tokens(value), 2)

    def test_long_inputs_are_not_retained_and_short_entries_can_be_evicted(self):
        boundary = "a" * 4096
        self.assertEqual(text_utils.estimate_prompt_tokens(boundary), 1024)
        self.assertEqual(text_utils._estimate_cached_prompt_tokens.cache_info().currsize, 1)
        for index in range(1100):
            text_utils.estimate_prompt_tokens("a" * 4100 + str(index))
        self.assertEqual(text_utils._estimate_cached_prompt_tokens.cache_info().currsize, 1)
        for index in range(1100):
            text_utils.estimate_prompt_tokens(str(index))
        self.assertLessEqual(text_utils._estimate_cached_prompt_tokens.cache_info().currsize, 1024)
        self.assertEqual(text_utils.estimate_prompt_tokens(boundary), 1024)

    def test_shared_counter_remains_exact_during_parallel_requests(self):
        cases = [("hello", 2), ("你好abc", 3), ("あア😀", 1), ("\ud800\udfff\x00\n", 1)] * 64
        with ThreadPoolExecutor(max_workers=8) as executor:
            observed = list(executor.map(text_utils.estimate_prompt_tokens, (text for text, _ in cases)))
        self.assertEqual(observed, [expected for _, expected in cases])


class PromptAuditReuseTests(unittest.TestCase):
    @staticmethod
    def record(tools, payload_tools=None, *, enabled=True):
        runtime = LLMRuntime.__new__(LLMRuntime)
        records = []
        runtime.instance_id = "test"
        runtime._should_record_prompt_audit = lambda _key: enabled
        runtime._append_prompt_audit_record = records.append
        client = SimpleNamespace(_akane_bundle_role="chat", _akane_protocol="openai")
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}]
        runtime._record_prompt_audit_if_enabled(
            bundle=ModelBundle(client=client, model="test"), prompt_cache_key="chat:final:test",
            request_payload={"model": "test", "messages": messages,
                             "tools": tools if payload_tools is None else payload_tools},
            messages=messages, system_extra_blocks=[], history_turns=[], ephemeral_turns=[],
            post_user_turns=[], user_prompt="hello", user_image_count=0, prompt_audit_sections=[],
            stream=True, json_mode=False, native_tools=tools,
        )
        return records

    @staticmethod
    def digest(value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(raw.encode("utf-8")).hexdigest()[:16]

    def test_shared_schema_keeps_distinct_labels_and_detached_evidence(self):
        tools = [{"type": "function", "function": {"name": "demo", "description": "你好"}}]
        first = self.record(tools)[0]
        field = next(item for item in first["request_field_fingerprints"] if item["name"] == "payload.field.tools")
        schema = first["native_tool_schema"]
        self.assertEqual(schema["name"], "payload.native_tools")
        self.assertEqual(schema["sha256_16"], self.digest(tools))
        self.assertEqual(field, {**schema, "name": "payload.field.tools"})
        original = deepcopy(first)
        tools[0]["function"]["description"] = "changed schema"
        second = self.record(tools)[0]
        self.assertEqual(first, original)
        self.assertNotEqual(schema["sha256_16"], second["native_tool_schema"]["sha256_16"])
        field["chars"] = -1
        self.assertEqual(schema["chars"], original["native_tool_schema"]["chars"])

    def test_distinct_wire_schema_is_audited_separately(self):
        original = [{"function": {"name": "demo", "parameters": {"type": "object"}}}]
        wire = deepcopy(original)
        wire[0]["function"]["parameters"]["description"] = "provider projection"
        record = self.record(original, wire)[0]
        field = next(item for item in record["request_field_fingerprints"] if item["name"] == "payload.field.tools")
        self.assertEqual(record["native_tool_schema"]["sha256_16"], self.digest(original))
        self.assertEqual(field["sha256_16"], self.digest(wire))
        self.assertNotEqual(field["sha256_16"], record["native_tool_schema"]["sha256_16"])

    def test_disabled_audit_does_not_record_or_count_request_material(self):
        text_utils._estimate_cached_prompt_tokens.cache_clear()
        self.assertEqual(self.record([{"function": {"name": "demo"}}], enabled=False), [])
        self.assertEqual(text_utils._estimate_cached_prompt_tokens.cache_info().currsize, 0)


if __name__ == "__main__":
    unittest.main()
