from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.llm_client import _build_anthropic_payload, build_llm_client, normalize_api_protocol, normalize_base_url
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.tool_invocation import NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD


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

    def test_llm_runtime_error_detail_redacts_secrets(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._last_error_lock = threading.RLock()
        runtime._last_error = {}

        runtime._record_error_detail(
            RuntimeError("Authorization: Bearer sk-testsecret123456 api_key=sk-othersecret123456"),
            phase="call_json",
        )
        detail = runtime.snapshot_last_error()

        self.assertEqual(detail["phase"], "call_json")
        self.assertEqual(detail["type"], "RuntimeError")
        self.assertNotIn("sk-testsecret", detail["message"])
        self.assertNotIn("sk-othersecret", detail["message"])
        self.assertIn("[redacted]", detail["message"])

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

    def test_llm_runtime_writes_prompt_audit_without_prompt_text(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-flash",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("config.LLM_PROMPT_AUDIT_ENABLED", True), patch("config.LLM_PROMPT_AUDIT_INCLUDE_AUX", False):
                with patch("config.LOG_DIR", temp_dir):
                    runtime._build_completion_kwargs(
                        bundle=bundle,
                        system_prompt="system private prompt",
                        user_prompt="user private prompt",
                        temperature=0.1,
                        stream=True,
                        json_mode=True,
                        prompt_cache_key="chat:final",
                        system_extra_blocks=["semantic private block"],
                        history_turns=[
                            {"role": "user", "content": "history private turn"},
                            {"role": "assistant", "content": "assistant private turn"},
                        ],
                        prompt_audit_sections=[
                            {"name": "user.current_message", "text": "current private message"},
                            {"name": "user.raw_recent_timeline", "text": "raw private timeline"},
                        ],
                    )

            files = list((Path(temp_dir) / "llm_prompt_audit").glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8").strip())

        self.assertEqual(record["prompt_cache_key"], "chat:final")
        self.assertEqual(record["model"], "deepseek-v4-flash")
        self.assertTrue(record["stream"])
        self.assertEqual(record["history_turn_count"], 2)
        source_by_name = {section["name"]: section for section in record["source_sections"]}
        self.assertEqual(source_by_name["user.current_message"]["chars"], len("current private message"))
        self.assertIn("sha256_16", source_by_name["user.raw_recent_timeline"])
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("current private message", serialized)
        self.assertNotIn("raw private timeline", serialized)
        self.assertNotIn("history private turn", serialized)
        self.assertNotIn("semantic private block", serialized)
        self.assertNotIn("system private prompt", serialized)
        self.assertGreater(record["payload_totals"]["estimated_tokens"], 0)

    def test_llm_runtime_prompt_audit_defaults_to_chat_final_only(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-flash",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("config.LLM_PROMPT_AUDIT_ENABLED", True), patch("config.LLM_PROMPT_AUDIT_INCLUDE_AUX", False):
                with patch("config.LOG_DIR", temp_dir):
                    runtime._build_completion_kwargs(
                        bundle=bundle,
                        system_prompt="system",
                        user_prompt="user",
                        temperature=0.1,
                        prompt_cache_key="aux:summary",
                    )

            self.assertFalse((Path(temp_dir) / "llm_prompt_audit").exists())

    def test_llm_runtime_adds_native_tools_for_verified_profile(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-pro",
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

    def test_llm_runtime_suppresses_forced_json_when_verified_profile_cannot_coexist(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-flash",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            json_mode=True,
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
        self.assertNotIn("response_format", payload)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_forced_json_suppressed"], 1)

    def test_llm_runtime_keeps_forced_json_when_verified_profile_can_coexist(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1"),
            model="deepseek-v4-pro",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            json_mode=True,
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

        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")

    def test_llm_runtime_skips_native_tools_for_unverified_openai_compatible_model(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://api.openai.com/v1"),
            model="gpt-5",
        )

        with patch("config.NATIVE_TOOL_PROVIDER_ALLOWLIST", ""):
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

    def test_llm_runtime_can_allow_configured_openai_compatible_native_profile(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://opencode.ai/zen/go/v1"),
            model="deepseek-v4-pro",
        )

        with patch("config.NATIVE_TOOL_PROVIDER_ALLOWLIST", "opencode.ai:deepseek-v4-pro"):
            payload = runtime._build_completion_kwargs(
                bundle=bundle,
                system_prompt="system",
                user_prompt="user",
                temperature=0.1,
                json_mode=True,
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
        self.assertNotIn("response_format", payload)

    def test_llm_runtime_configured_native_profile_supports_wildcard_and_json_mode(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://opencode.ai/zen/go/v1"),
            model="deepseek-v4-pro",
        )

        with patch("config.NATIVE_TOOL_PROVIDER_ALLOWLIST", "opencode.ai:*:json"):
            payload = runtime._build_completion_kwargs(
                bundle=bundle,
                system_prompt="system",
                user_prompt="user",
                temperature=0.1,
                json_mode=True,
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

        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")

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
            {"type": "web_search", "query": "Akane", "max_results": 3, TOOL_SOURCE_FIELD: NATIVE_OPENAI},
        )

    def test_llm_runtime_returns_native_tool_call_on_internal_carrier(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        runtime._build_completion_kwargs = lambda **_kwargs: {}
        runtime._create_completion = lambda **_kwargs: object()
        runtime._record_cache_metrics = lambda _response: None
        runtime._extract_native_tool_call = lambda _response: {
            "type": "web_search",
            "query": "Akane",
            TOOL_SOURCE_FIELD: NATIVE_OPENAI,
            TOOL_INVOCATION_ID_FIELD: "call_native_1",
        }

        result = runtime._call_json(
            bundle=SimpleNamespace(),
            system_prompt="system",
            user_prompt="user",
            fallback={"speech": "", "tool_call": None},
            temperature=0.0,
            prompt_cache_key="test:native_tool",
            native_tools=[{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}],
            native_tool_choice="auto",
        )

        self.assertIsNone(result["tool_call"])
        self.assertEqual(result[NATIVE_TOOL_CALL_FIELD]["type"], "web_search")
        self.assertEqual(result[NATIVE_TOOL_CALL_FIELD][TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_call_extracted"], 1)

    def test_llm_runtime_stream_returns_native_tool_call_on_internal_carrier(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        runtime._build_completion_kwargs = lambda **_kwargs: {}
        runtime._record_cache_metrics = lambda _response: None
        runtime._close_stream = lambda _response: None
        runtime._extract_stream_text = lambda _chunk: ""
        runtime._create_completion = lambda **_kwargs: [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_stream_1",
                                    function=SimpleNamespace(
                                        name="web_search",
                                        arguments='{"query":"Akane"}',
                                    ),
                                )
                            ]
                        )
                    )
                ]
            )
        ]

        generator = runtime._stream_chat_json(
            bundle=SimpleNamespace(),
            system_prompt="system",
            user_prompt="user",
            fallback={"speech": "", "tool_call": None},
            temperature=0.0,
            early_tool_call_validator=None,
            prompt_cache_key="test:native_tool_stream",
            native_tools=[{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}],
            native_tool_choice="auto",
        )

        while True:
            try:
                next(generator)
            except StopIteration as exc:
                result = exc.value
                break

        self.assertIsNone(result.parsed["tool_call"])
        self.assertEqual(result.parsed[NATIVE_TOOL_CALL_FIELD]["type"], "web_search")
        self.assertEqual(result.parsed[NATIVE_TOOL_CALL_FIELD][TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_call_extracted"], 1)

    def test_llm_runtime_collects_stream_native_tool_call_to_akane_shape(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        parts: dict[int, dict[str, object]] = {}
        first_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_stream_1",
                                function=SimpleNamespace(name="web_search", arguments='{"query":"A'),
                            )
                        ]
                    )
                )
            ]
        )
        second_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name="", arguments='kane","max_results":3}'),
                            )
                        ]
                    )
                )
            ]
        )

        runtime._collect_stream_native_tool_call_parts(first_chunk, parts)
        runtime._collect_stream_native_tool_call_parts(second_chunk, parts)

        self.assertEqual(
            runtime._stream_native_tool_call_from_parts(parts),
            {
                "type": "web_search",
                "query": "Akane",
                "max_results": 3,
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_stream_1",
            },
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


class ResponseTruncationDetectionTests(unittest.TestCase):
    """D1: the non-stream payload sends no max_tokens, so a provider cap (the
    Anthropic shim defaults to 1024) can silently cut a reply mid-JSON. These
    lock in that finish_reason=length is surfaced (metric + last_error) instead
    of passing as a normal short answer."""

    def _runtime(self) -> LLMRuntime:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics = {}
        runtime._metrics_lock = threading.Lock()
        runtime._last_error = {}
        runtime._last_error_lock = threading.Lock()
        return runtime

    @staticmethod
    def _response(finish_reason: str, content: str) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))]
        )

    def test_length_finish_reason_is_surfaced(self) -> None:
        runtime = self._runtime()
        runtime._note_truncation(self._response("length", '{"speech":"长长的回答被切'), phase="call_json")
        self.assertEqual(runtime.snapshot_metrics().get("response_truncated"), 1)
        error = runtime.snapshot_last_error()
        self.assertEqual(error.get("type"), "ResponseTruncated")
        self.assertIn("finish_reason=length", error.get("message", ""))
        self.assertEqual(error.get("phase"), "call_json")

    def test_normal_finish_reason_is_ignored(self) -> None:
        runtime = self._runtime()
        runtime._note_truncation(self._response("stop", '{"speech":"ok"}'), phase="call_json")
        self.assertIsNone(runtime.snapshot_metrics().get("response_truncated"))
        self.assertEqual(runtime.snapshot_last_error(), {})

    def test_malformed_response_does_not_raise(self) -> None:
        runtime = self._runtime()
        runtime._note_truncation(SimpleNamespace(choices=[]), phase="call_json")
        self.assertIsNone(runtime.snapshot_metrics().get("response_truncated"))


class ChatJSONFallbackSampleTests(unittest.TestCase):
    """D3: a reply that doesn't parse as JSON falls back. The fallback is
    counted (chat_json_fallbacks) but without a sample it can't be reproduced;
    _note_parse_fallback records a sanitized head sample into last_error."""

    def _runtime(self) -> LLMRuntime:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics = {}
        runtime._metrics_lock = threading.Lock()
        runtime._last_error = {}
        runtime._last_error_lock = threading.Lock()
        return runtime

    def test_fallback_sample_is_recorded_and_redacted(self) -> None:
        runtime = self._runtime()
        runtime._note_parse_fallback(
            "sorry I cannot, sk-secret123456789 not json", phase="stream_chat_json"
        )
        error = runtime.snapshot_last_error()
        self.assertEqual(error.get("type"), "ChatJSONFallback")
        self.assertEqual(error.get("phase"), "stream_chat_json")
        self.assertIn("not valid JSON", error.get("message", ""))
        # Secret in the sampled content must be redacted (reuses SECRET_PATTERNS).
        self.assertNotIn("sk-secret123456789", error.get("message", ""))

    def test_missing_lock_is_safe(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._note_parse_fallback("x", phase="call_json")  # must not raise


if __name__ == "__main__":
    unittest.main()
