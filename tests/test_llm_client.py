from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.llm_client import _build_anthropic_payload, build_llm_client, normalize_api_protocol, normalize_base_url
from companion_v01.llm_runtime import LLMRuntime


class LLMClientConfigTests(unittest.TestCase):
    def test_ollama_protocol_normalizes_to_openai_compatible_v1_endpoint(self) -> None:
        self.assertEqual(normalize_api_protocol(protocol="ollama", base_url=""), "ollama")
        self.assertEqual(normalize_api_protocol(protocol="auto", base_url="http://127.0.0.1:11434"), "ollama")
        self.assertEqual(
            normalize_base_url(protocol="ollama", base_url="http://127.0.0.1:11434"),
            "http://127.0.0.1:11434/v1",
        )
        self.assertEqual(
            normalize_base_url(protocol="ollama", base_url="http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1",
        )

    def test_ollama_client_can_be_built_without_api_key(self) -> None:
        client = build_llm_client(
            api_key="",
            base_url="http://127.0.0.1:11434",
            protocol="ollama",
            timeout=1.0,
            max_retries=0,
        )

        self.assertEqual(str(client.base_url).rstrip("/"), "http://127.0.0.1:11434/v1")
        self.assertEqual(client.api_key, "ollama")

    def test_chat_bundle_uses_chat_config_instead_of_aux_config(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_build_llm_client(**kwargs):
            calls.append(dict(kwargs))
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))

        runtime = LLMRuntime.__new__(LLMRuntime)
        with patch("companion_v01.llm_runtime.build_llm_client", side_effect=fake_build_llm_client):
            with patch("config.CHAT_API_KEY", "chat-key"), patch("config.CHAT_BASE_URL", "http://chat.example/v1"):
                with patch("config.CHAT_API_PROTOCOL", "openai"), patch("config.CHAT_MODEL_NAME", "chat-model"):
                    bundle = runtime._build_chat_bundle()

        self.assertEqual(bundle.model, "chat-model")
        self.assertEqual(calls[0]["api_key"], "chat-key")
        self.assertEqual(calls[0]["base_url"], "http://chat.example/v1")
        self.assertEqual(calls[0]["protocol"], "openai")

    def test_llm_runtime_uses_json_mode_for_ollama_json_calls(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="ollama"),
            model="qwen2.5:7b",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            stream=True,
            json_mode=True,
        )

        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIs(payload["stream"], True)

    def test_llm_runtime_can_attach_user_images_to_chat_prompt(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.example.test/v1"),
            model="vision-chat",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user text",
            temperature=0.1,
            user_images=[
                {"data_url": "data:image/jpeg;base64,abc"},
                {"data_url": "https://example.test/not-inline.jpg"},
            ],
        )

        content = payload["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "user text"})
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}})
        self.assertEqual(len(content), 2)

    def test_llm_runtime_preserves_system_extra_blocks_for_openai_compatible_payloads(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-flash",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            system_extra_blocks=["resource block", "semantic block"],
        )

        self.assertIn("system", payload["messages"][0]["content"])
        self.assertIn("resource block", payload["messages"][0]["content"])
        self.assertIn("semantic block", payload["messages"][0]["content"])
        self.assertNotIn("system_extra_blocks", payload)

    def test_llm_runtime_keeps_system_extra_blocks_separate_for_anthropic(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="anthropic"),
            model="claude-test",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            system_extra_blocks=["resource block", "semantic block"],
        )

        self.assertEqual(payload["messages"][0]["content"], "system")
        self.assertEqual(payload["system_extra_blocks"], ["resource block", "semantic block"])

    def test_llm_runtime_adds_prompt_cache_hints_for_official_openai(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.openai.com/v1"),
            model="gpt-5",
        )

        with patch("config.PROMPT_CACHE_HINTS_ENABLED", True), patch("config.PROMPT_CACHE_HINTS_FORCE", False):
            with patch("config.PROMPT_CACHE_NAMESPACE", "akane"), patch("config.PROMPT_CACHE_RETENTION", "24h"):
                payload = runtime._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt="system",
                    user_prompt="user",
                    temperature=0.1,
                    prompt_cache_key="chat:final",
                )

        self.assertEqual(payload["prompt_cache_key"], "akane:chat:final")
        self.assertEqual(payload["prompt_cache_retention"], "24h")

    def test_llm_runtime_adds_native_tools_only_when_explicit_for_openai(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.openai.com/v1"),
            model="gpt-5",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            native_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the public web.",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            native_tool_choice="auto",
        )

        self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")
        self.assertEqual(payload["tool_choice"], "auto")

    def test_llm_runtime_skips_native_tools_for_non_openai_protocol(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="ollama", base_url="http://127.0.0.1:11434/v1"),
            model="qwen2.5:7b",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            native_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the public web.",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            native_tool_choice="auto",
        )

        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

    def test_llm_runtime_extracts_native_tool_call_to_akane_shape(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="web_search",
                                    arguments='{"query":"Akane","max_results":3,"type":"ignored"}',
                                )
                            )
                        ]
                    )
                )
            ]
        )

        self.assertEqual(
            runtime._extract_native_tool_call(response),
            {"type": "web_search", "query": "Akane", "max_results": 3},
        )

    def test_llm_runtime_skips_prompt_cache_hints_for_non_openai_base_url_by_default(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-chat",
        )

        with patch("config.PROMPT_CACHE_HINTS_ENABLED", True), patch("config.PROMPT_CACHE_HINTS_FORCE", False):
            with patch("config.PROMPT_CACHE_NAMESPACE", "akane"), patch("config.PROMPT_CACHE_RETENTION", "24h"):
                payload = runtime._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt="system",
                    user_prompt="user",
                    temperature=0.1,
                    prompt_cache_key="chat:final",
                )

        self.assertNotIn("prompt_cache_key", payload)
        self.assertNotIn("prompt_cache_retention", payload)

    def test_llm_runtime_disables_deepseek_thinking_by_default(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-flash",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            stream=True,
            json_mode=True,
        )

        self.assertEqual(payload["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_anthropic_system_extra_blocks_are_preserved_beyond_cache_limit(self) -> None:
        payload = _build_anthropic_payload(
            {
                "model": "claude-test",
                "messages": [
                    {"role": "system", "content": "base system"},
                    {"role": "user", "content": "hello"},
                ],
                "system_extra_blocks": ["extra-1", "extra-2", "extra-3", "extra-4", "extra-5"],
            }
        )

        system_blocks = payload["system"]
        self.assertEqual([block["text"] for block in system_blocks], ["base system", "extra-1", "extra-2", "extra-3", "extra-4", "extra-5"])
        self.assertEqual(sum(1 for block in system_blocks if "cache_control" in block), 4)
        self.assertNotIn("cache_control", system_blocks[-1])

    def test_llm_runtime_records_deepseek_cache_usage_fields(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        recorded: list[tuple[str, int]] = []
        runtime._record_metric = lambda key, amount=1: recorded.append((key, amount))

        runtime._record_cache_metrics(
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_cache_hit_tokens=12,
                    prompt_cache_miss_tokens=34,
                )
            )
        )

        self.assertIn(("cache_read_tokens", 12), recorded)
        self.assertIn(("cache_creation_tokens", 34), recorded)

    def test_llm_runtime_does_not_send_deepseek_thinking_control_to_other_hosts(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.example.test/v1"),
            model="chat-model",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            stream=True,
            json_mode=True,
        )

        self.assertNotIn("extra_body", payload)
        self.assertNotIn("stream_options", payload)

    def test_llm_runtime_retries_without_prompt_cache_hints_when_client_rejects_them(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        calls: list[dict[str, object]] = []

        def fake_create(**kwargs):
            calls.append(dict(kwargs))
            if "prompt_cache_key" in kwargs or "prompt_cache_retention" in kwargs:
                raise TypeError("unexpected keyword argument 'prompt_cache_key'")
            return {"ok": True}

        bundle = SimpleNamespace(
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))),
            model="gpt-5",
        )

        result = runtime._create_completion(
            bundle=bundle,
            payload={
                "model": "gpt-5",
                "messages": [],
                "prompt_cache_key": "akane:chat:final",
                "prompt_cache_retention": "24h",
            },
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIn("prompt_cache_key", calls[0])
        self.assertNotIn("prompt_cache_key", calls[1])
        self.assertNotIn("prompt_cache_retention", calls[1])


if __name__ == "__main__":
    unittest.main()
