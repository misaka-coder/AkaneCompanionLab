from __future__ import annotations

import threading
from types import SimpleNamespace
import unittest

from companion_v01.capability_registry import SEND_MUSIC_CARD_TOOL_SPEC
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.skill_specs import LOAD_SKILL_TOOL_SPEC
from services.gemini_native_client import _build_gemini_payload
from services.llm_client import _build_anthropic_payload


class ProviderToolCatalogTests(unittest.TestCase):
    """Large published catalogs must match what the provider actually receives."""

    def setUp(self):
        self.runtime = LLMRuntime.__new__(LLMRuntime)
        self.runtime.settings = BotSettingsView(prompt_cache_hints_enabled=False)
        self.runtime._metrics = {}
        self.runtime._metrics_lock = threading.RLock()
        self.description = "Read the contract.\n" * 70 + "Preserve this final constraint."
        self.tools = [
            {"type": "function", "function": {
                "name": f"a_plugin_{i:03}", "description": self.description,
                "parameters": {"type": "object", "properties": {}}}}
            for i in range(105)
        ] + [build_openai_native_tool_from_spec(LOAD_SKILL_TOOL_SPEC),
             build_openai_native_tool_from_spec(SEND_MUSIC_CARD_TOOL_SPEC)]
        self.expected = [tool["function"]["name"] for tool in self.tools]

    def test_full_catalog_reaches_each_provider_wire(self):
        for protocol in ("openai", "gemini", "anthropic", "responses"):
            with self.subTest(protocol=protocol):
                bundle = SimpleNamespace(client=SimpleNamespace(
                    _akane_protocol=protocol, base_url="https://example.test/v1"),
                    model="gemini-2.5-pro" if protocol in {"openai", "gemini"} else "test-model")
                payload = self.runtime._build_completion_kwargs(
                    bundle=bundle, system_prompt="system", user_prompt="music card",
                    temperature=0.1, native_tools=self.tools)
                if protocol == "gemini":
                    wire_tools = _build_gemini_payload(payload)["tools"][0]["functionDeclarations"]
                elif protocol == "anthropic":
                    wire_tools = _build_anthropic_payload(payload)["tools"]
                elif protocol == "responses":
                    wire_tools = self.runtime._responses_payload_from_chat(payload)["tools"]
                else:
                    wire_tools = [tool["function"] for tool in payload["tools"]]
                self.assertEqual([tool["name"] for tool in wire_tools], self.expected)
                self.assertEqual(wire_tools[0]["description"], self.description)

    def test_large_catalog_still_filters_invalid_and_duplicate_entries(self):
        dirty = [None, {"type": "invalid"}, *self.tools, self.tools[-1],
                 {"type": "function", "function": {"name": "invalid name"}}]
        normalized = self.runtime._normalize_native_tools(dirty)
        self.assertEqual([tool["function"]["name"] for tool in normalized], self.expected)
        self.assertTrue(all("_akane_capability_id" not in tool for tool in normalized))

    def test_audit_lists_every_sent_tool_without_recording_private_prompt(self):
        records = []
        self.runtime._should_record_prompt_audit = lambda _: True
        self.runtime._append_prompt_audit_record = records.append
        bundle = SimpleNamespace(client=SimpleNamespace(
            _akane_protocol="openai", base_url="https://example.test/v1"), model="test-model")
        self.runtime._build_completion_kwargs(
            bundle=bundle, system_prompt="private-system-marker", user_prompt="music",
            temperature=0.1, native_tools=self.tools, prompt_cache_key="chat:final")
        self.assertEqual(records[0]["native_tool_count"], len(self.expected))
        self.assertEqual(records[0]["native_tool_names"], self.expected)
        self.assertNotIn("private-system-marker", str(records))


if __name__ == "__main__":
    unittest.main()
