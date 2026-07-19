from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.llm_client import _build_anthropic_payload, build_llm_client, normalize_api_protocol, normalize_base_url
from companion_v01.llm_runtime import LLMRuntime, ModelBundle
from companion_v01.native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.tool_invocation import (
    NATIVE_ANTHROPIC,
    NATIVE_OPENAI,
    NATIVE_TOOL_CALL_FIELD,
    NATIVE_TOOL_CALLS_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
)


class LLMClientConfigTests(unittest.TestCase):
    def test_openai_payload_preserves_stable_context_and_append_only_history_message_boundaries(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="openai", base_url="https://gateway.example/v1"),
            model="model",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="stable system",
            user_prompt="dynamic current tail",
            temperature=0.1,
            history_turns=[
                {"role": "user", "content": "stable tool context"},
                {"role": "user", "content": "stable summaries"},
                {"role": "user", "content": "first user turn"},
                {"role": "assistant", "content": "first assistant turn"},
            ],
        )

        self.assertEqual(
            [(item["role"], item["content"]) for item in payload["messages"]],
            [
                ("system", "stable system"),
                ("user", "stable tool context"),
                ("user", "stable summaries"),
                ("user", "first user turn"),
                ("assistant", "first assistant turn"),
                ("user", "dynamic current tail"),
            ],
        )

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

    def test_responses_protocol_normalizes_base_url(self) -> None:
        self.assertEqual(
            normalize_base_url(protocol="responses", base_url="https://api.pinaic.com"),
            "https://api.pinaic.com/v1",
        )
        client = build_llm_client(
            api_key="test-key",
            base_url="https://api.pinaic.com",
            protocol="responses",
            timeout=1.0,
            max_retries=0,
        )
        self.assertEqual(str(client.base_url).rstrip("/"), "https://api.pinaic.com/v1")
        self.assertEqual(client._akane_protocol, "responses")

    def test_responses_payload_preserves_tools_history_and_privacy_controls(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="responses", base_url="https://api.pinaic.com/v1"),
            model="gpt-5.6-sol",
        )
        with (
            patch("config.LLM_REASONING_EFFORT", "max"),
            patch("config.LLM_DISABLE_RESPONSE_STORAGE", True),
            patch("config.PROMPT_CACHE_NAMESPACE", "akane"),
            patch("config.PROMPT_CACHE_RETENTION", "24h"),
        ):
            chat_payload = runtime._build_completion_kwargs(
                bundle=bundle,
                system_prompt="stable instructions",
                user_prompt="current question",
                temperature=0.7,
                json_mode=True,
                prompt_cache_key="chat:final:reimu",
                native_tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "Search related evidence.",
                            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                        },
                    }
                ],
                post_user_turns=[
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "web_search", "arguments": '{"q":"Nikkei"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "search result"},
                ],
            )
            request = runtime._responses_payload_from_chat(chat_payload)

        self.assertEqual(request["instructions"], "stable instructions")
        self.assertEqual(request["reasoning"], {"effort": "max"})
        self.assertFalse(request["store"])
        self.assertNotIn("temperature", request)
        self.assertEqual(request["prompt_cache_key"], "akane:chat:final:reimu")
        self.assertEqual(request["prompt_cache_retention"], "in-memory")
        # Native tool rounds keep JSON mode prompt-only so a function call can
        # coexist with the eventual structured Akane answer.
        self.assertNotIn("text", request)
        self.assertTrue(request["parallel_tool_calls"])
        self.assertEqual(request["tools"][0]["name"], "web_search")
        self.assertIn("function_call", [item.get("type") for item in request["input"]])
        self.assertIn("function_call_output", [item.get("type") for item in request["input"]])

    def test_responses_reasoning_effort_can_differ_between_aux_and_chat(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        aux = SimpleNamespace(client=SimpleNamespace(_akane_protocol="responses", _akane_bundle_role="aux"))
        chat = SimpleNamespace(client=SimpleNamespace(_akane_protocol="responses", _akane_bundle_role="chat"))
        with (
            patch("config.LLM_REASONING_EFFORT", "medium"),
            patch("config.LLM_AUX_REASONING_EFFORT", "low"),
            patch("config.LLM_CHAT_REASONING_EFFORT", "max"),
        ):
            self.assertEqual(runtime._build_reasoning_control_kwargs(bundle=aux), {"reasoning": {"effort": "low"}})
            self.assertEqual(runtime._build_reasoning_control_kwargs(bundle=chat), {"reasoning": {"effort": "max"}})

    def test_responses_reasoning_effort_uses_per_bot_snapshot(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime.settings = BotSettingsView(
            llm_reasoning_effort="medium",
            llm_aux_reasoning_effort="low",
            llm_chat_reasoning_effort="high",
        )
        aux = SimpleNamespace(client=SimpleNamespace(_akane_protocol="responses", _akane_bundle_role="aux"))
        chat = SimpleNamespace(client=SimpleNamespace(_akane_protocol="responses", _akane_bundle_role="chat"))

        self.assertEqual(runtime._build_reasoning_control_kwargs(bundle=aux), {"reasoning": {"effort": "low"}})
        self.assertEqual(runtime._build_reasoning_control_kwargs(bundle=chat), {"reasoning": {"effort": "high"}})

    def test_pinai_responses_omits_unsupported_forced_json_wire_hint(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        pinai = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="responses", base_url="https://api.pinaic.com/v1"),
            model="gpt-5.6-sol",
        )
        official = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="responses", base_url="https://api.openai.com/v1"),
            model="gpt-5.4",
        )
        with patch("config.LLM_REASONING_EFFORT", "low"):
            pinai_payload = runtime._build_completion_kwargs(
                bundle=pinai,
                system_prompt="Return JSON.",
                user_prompt="Return one object.",
                temperature=0.2,
                json_mode=True,
            )
            official_payload = runtime._build_completion_kwargs(
                bundle=official,
                system_prompt="Return JSON.",
                user_prompt="Return one object.",
                temperature=0.2,
                json_mode=True,
            )

        self.assertNotIn("response_format", pinai_payload)
        self.assertEqual(official_payload["response_format"], {"type": "json_object"})

    def test_responses_result_and_stream_adapt_to_existing_tool_pipeline(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        response = SimpleNamespace(
            status="completed",
            output_text="",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_2",
                    name="web_search",
                    arguments='{"q":"rates"}',
                )
            ],
            usage=SimpleNamespace(input_tokens=40, output_tokens=5),
        )
        adapted = runtime._adapt_responses_result(response)
        self.assertEqual(adapted.choices[0].message.tool_calls[0]["id"], "call_2")
        self.assertEqual(adapted.choices[0].message.tool_calls[0]["function"]["name"], "web_search")

        from companion_v01.llm_runtime import _ResponsesStreamAdapter

        usage = SimpleNamespace(input_tokens=100, input_tokens_details=SimpleNamespace(cached_tokens=64))
        events = [
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(type="function_call", call_id="call_3", name="web_search"),
            ),
            SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='{"q":'),
            SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='"oil"}'),
            SimpleNamespace(type="response.output_text.delta", delta='{"speech":"checking"}'),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage)),
        ]
        stream_adapter = _ResponsesStreamAdapter(events)
        chunks = list(stream_adapter)
        self.assertIsNone(stream_adapter.usage)
        parts: dict[object, dict[str, object]] = {}
        for chunk in chunks:
            runtime._collect_stream_native_tool_call_parts(chunk, parts)
        calls = runtime._stream_native_tool_calls_from_parts(
            parts,
            native_tools=[
                {
                    "type": "function",
                    "function": {"name": "web_search", "parameters": {"type": "object"}},
                }
            ],
        )
        self.assertEqual(calls[0]["type"], "web_search")
        self.assertEqual(calls[0]["q"], "oil")
        self.assertEqual(calls[0][TOOL_INVOCATION_ID_FIELD], "call_3")
        self.assertEqual("".join(runtime._extract_stream_text(chunk) for chunk in chunks), '{"speech":"checking"}')

    def test_responses_input_coalesces_adjacent_plain_messages_for_stable_stream_cache_prefix(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        messages = [
            {"role": "user", "content": "stable tool context"},
            {"role": "user", "content": "stable memory context"},
            {"role": "assistant", "content": "first assistant fragment"},
            {"role": "assistant", "content": "second assistant fragment"},
            {"role": "user", "content": [{"type": "text", "text": "multimodal text"}]},
            {"role": "user", "content": "plain text after structured content"},
        ]

        result = runtime._responses_input_from_messages(messages)

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "stable tool context\n\nstable memory context"},
                {"role": "assistant", "content": "first assistant fragment\n\nsecond assistant fragment"},
                {"role": "user", "content": [{"type": "input_text", "text": "multimodal text"}]},
                {"role": "user", "content": "plain text after structured content"},
            ],
        )

    def test_responses_failures_surface_bounded_structured_reasons(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        failed = SimpleNamespace(
            status="failed",
            error=SimpleNamespace(type="server_error", code="upstream_failed", message="do not echo this"),
            output=[],
        )
        with self.assertRaisesRegex(RuntimeError, "responses_failed type=server_error code=upstream_failed"):
            runtime._adapt_responses_result(failed)

        from companion_v01.llm_runtime import _ResponsesStreamAdapter

        events = [
            SimpleNamespace(
                type="response.failed",
                response=SimpleNamespace(error=SimpleNamespace(type="invalid_request_error", code="bad_input")),
            )
        ]
        with self.assertRaisesRegex(
            RuntimeError,
            "responses_stream_failed type=invalid_request_error code=bad_input",
        ):
            list(_ResponsesStreamAdapter(events))

    def test_openai_nested_cached_tokens_are_recorded(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=9,
                input_tokens_details=SimpleNamespace(cached_tokens=96),
            )
        )

        runtime._record_cache_metrics(response)

        self.assertEqual(runtime._metrics["cache_read_tokens"], 96)
        self.assertEqual(runtime._metrics["reported_input_tokens"], 120)
        self.assertEqual(runtime._metrics["reported_output_tokens"], 9)

    def test_final_cache_usage_is_recorded_separately(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=200,
                completion_tokens=11,
                prompt_tokens_details=SimpleNamespace(cached_tokens=150),
            )
        )

        runtime._record_cache_metrics(response, prompt_cache_key="chat:final:conversation")

        self.assertEqual(runtime._metrics["final_cache_read_tokens"], 150)
        self.assertEqual(runtime._metrics["final_reported_input_tokens"], 200)
        self.assertEqual(runtime._metrics["final_reported_output_tokens"], 11)
        self.assertEqual(runtime._metrics["final_cache_usage_calls"], 1)
        self.assertEqual(runtime._metrics["final_cache_hit_calls"], 1)

    def test_plugin_proactive_cache_usage_is_recorded_separately(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=9,
                input_tokens_details=SimpleNamespace(cached_tokens=96),
            )
        )

        runtime._record_cache_metrics(response, prompt_cache_key="chat:plugin_proactive:stable")

        self.assertEqual(runtime._metrics["plugin_proactive_cache_read_tokens"], 96)
        self.assertEqual(runtime._metrics["plugin_proactive_reported_input_tokens"], 120)
        self.assertEqual(runtime._metrics["plugin_proactive_reported_output_tokens"], 9)

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

    def test_chat_model_override_reuses_current_chat_client(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._bundle_lock = threading.RLock()
        client = SimpleNamespace(_akane_protocol="openai", base_url="https://api.deepseek.com/v1")
        runtime.chat = ModelBundle(client=client, model="deepseek-chat")

        override_bundle = runtime._chat_bundle_for_override("deepseek-v4-flash")
        default_bundle = runtime._chat_bundle_for_override("")
        invalid_bundle = runtime._chat_bundle_for_override("坏模型")

        self.assertIs(override_bundle.client, client)
        self.assertEqual(override_bundle.model, "deepseek-v4-flash")
        self.assertIs(default_bundle, runtime.chat)
        self.assertIs(invalid_bundle, runtime.chat)

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

    def test_runtime_error_capture_logs_only_redacted_detail(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._last_error_lock = threading.RLock()
        runtime._last_error = {}

        with patch("companion_v01.llm_runtime.logger.warning") as warning:
            runtime._capture_runtime_error(
                RuntimeError("Authorization: Bearer sk-testsecret123456"),
                phase="stream_chat_json",
            )

        rendered = " ".join(str(value) for value in warning.call_args.args)
        self.assertNotIn("sk-testsecret123456", rendered)
        self.assertIn("[redacted]", rendered)
        self.assertEqual(runtime.snapshot_last_error()["phase"], "stream_chat_json")

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
            model="deepseek-v4-pro",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime.log_dir = Path(temp_dir)
            runtime.instance_id = "finance-prod"
            with patch("config.LLM_PROMPT_AUDIT_ENABLED", True), patch("config.LLM_PROMPT_AUDIT_INCLUDE_AUX", False):
                with patch("config.LOG_DIR", str(Path(temp_dir) / "wrong-global-log-root")):
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
                        native_tools=[
                            {
                                "type": "function",
                                "function": {
                                    "name": "private_tool_name",
                                    "description": "private tool description",
                                    "parameters": {"type": "object"},
                                },
                            }
                        ],
                    )

            files = list((Path(temp_dir) / "llm_prompt_audit").glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8").strip())

        self.assertEqual(record["prompt_cache_key"], "chat:final")
        self.assertEqual(record["record_type"], "prompt")
        self.assertEqual(record["instance_id"], "finance-prod")
        self.assertTrue(record["runtime_object_id"])
        self.assertTrue(record["client_object_id"])
        self.assertEqual(record["bundle_role"], "")
        self.assertEqual(record["model"], "deepseek-v4-pro")
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
        self.assertNotIn("private tool description", serialized)
        self.assertEqual(record["native_tool_count"], 1)
        self.assertEqual(record["native_tool_names"], ["private_tool_name"])
        self.assertTrue(record["native_tool_schema"]["sha256_16"])
        self.assertGreater(record["payload_totals"]["estimated_tokens"], 0)
        self.assertIn("messages", record["request_field_order"])
        self.assertIn("tools", record["request_field_order"])
        self.assertEqual(
            [item["role"] for item in record["message_fingerprints"]],
            ["system", "user", "assistant", "user"],
        )
        self.assertTrue(all(item["sha256_16"] for item in record["message_fingerprints"]))
        field_names = {item["name"] for item in record["request_field_fingerprints"]}
        self.assertIn("payload.field.model", field_names)
        self.assertIn("payload.field.tools", field_names)

    def test_llm_runtime_writes_per_call_cache_usage_audit(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        runtime.instance_id = "personal-prod"

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime.log_dir = Path(temp_dir)
            response = SimpleNamespace(
                model="pin-model",
                usage=SimpleNamespace(
                    prompt_tokens=1_000,
                    completion_tokens=25,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=800),
                ),
            )
            with patch("config.LLM_PROMPT_AUDIT_ENABLED", True), patch("config.LLM_PROMPT_AUDIT_INCLUDE_AUX", False):
                runtime._record_cache_metrics(response, prompt_cache_key="chat:final:conversation")

            path = next((Path(temp_dir) / "llm_prompt_audit").glob("*.jsonl"))
            record = json.loads(path.read_text(encoding="utf-8").strip())

        self.assertEqual(record["record_type"], "usage")
        self.assertEqual(record["reported_input_tokens"], 1_000)
        self.assertEqual(record["cache_read_tokens"], 800)
        self.assertEqual(record["cache_hit_ratio"], 0.8)
        self.assertEqual(record["model"], "pin-model")

    def test_llm_runtime_writes_order_sensitive_responses_request_audit_without_prompt_text(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="responses", _akane_bundle_role="chat"),
            model="pin-model",
        )
        request = {
            "model": "pin-model",
            "instructions": "private system prompt",
            "input": [
                {"role": "user", "content": "private stable input"},
                {"type": "function_call_output", "call_id": "call-private", "output": "private output"},
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "private_tool",
                    "description": "private description",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            "prompt_cache_key": "akane:chat:final:conversation",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime.log_dir = Path(temp_dir)
            runtime.instance_id = "personal-prod"
            with patch("config.LLM_PROMPT_AUDIT_ENABLED", True), patch("config.LLM_PROMPT_AUDIT_INCLUDE_AUX", False):
                with patch("config.PROMPT_CACHE_NAMESPACE", "akane"):
                    runtime._record_responses_request_audit_if_enabled(bundle=bundle, request=request)

            path = next((Path(temp_dir) / "llm_prompt_audit").glob("*.jsonl"))
            record = json.loads(path.read_text(encoding="utf-8").strip())

        self.assertEqual(record["record_type"], "responses_request")
        self.assertEqual(record["prompt_cache_key"], "akane:chat:final:conversation")
        self.assertEqual(record["request_field_order"], list(request))
        self.assertEqual([item["role"] for item in record["input_item_fingerprints"]], ["user", ""])
        self.assertEqual(record["tool_fingerprints"][0]["name"], "private_tool")
        self.assertEqual(record["tool_fingerprints"][0]["field_order"], list(request["tools"][0]))
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("private system prompt", serialized)
        self.assertNotIn("private stable input", serialized)
        self.assertNotIn("private output", serialized)
        self.assertNotIn("private description", serialized)

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

        with patch("config.LLM_PROMPT_AUDIT_ENABLED", True), patch("config.LLM_PROMPT_AUDIT_INCLUDE_AUX", False):
            self.assertFalse(runtime._should_record_prompt_audit("chat:finance_push"))

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

    def test_llm_runtime_strips_internal_native_tool_mapping_from_payload(self) -> None:
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
                    NATIVE_TOOL_CAPABILITY_ID_FIELD: "mcp.demo.echo",
                    "function": {
                        "name": "mcp_demo_echo_abcd123456",
                        "description": "Echo.",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            native_tool_choice="auto",
        )

        self.assertEqual(payload["tools"][0]["function"]["name"], "mcp_demo_echo_abcd123456")
        self.assertNotIn(NATIVE_TOOL_CAPABILITY_ID_FIELD, payload["tools"][0])

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

    def test_llm_runtime_adds_native_tools_for_anthropic_protocol(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(
            client=SimpleNamespace(_akane_protocol="anthropic", base_url="https://api.anthropic.com"),
            model="claude-sonnet-5",
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
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertNotIn("response_format", payload)

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

    def test_anthropic_payload_converts_native_tools_to_messages_shape(self) -> None:
        payload = _build_anthropic_payload(
            {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [
                    {
                        "type": "function",
                        NATIVE_TOOL_CAPABILITY_ID_FIELD: "internal.should_not_leak",
                        "function": {
                            "name": "web_search",
                            "description": "Search the public web.",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
            }
        )

        self.assertEqual(
            payload["tools"],
            [
                {
                    "name": "web_search",
                    "description": "Search the public web.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
        )
        self.assertEqual(payload["tool_choice"], {"type": "auto"})
        self.assertNotIn(NATIVE_TOOL_CAPABILITY_ID_FIELD, json.dumps(payload, ensure_ascii=False))

    def test_llm_runtime_appends_post_user_turns_after_current_user_prompt_for_anthropic(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="anthropic"), model="claude-test")

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="current user prompt",
            temperature=0.1,
            post_user_turns=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "我先查一下。"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "web_search",
                            "input": {"query": "Akane"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "result text",
                        }
                    ],
                },
            ],
        )

        self.assertEqual([message["role"] for message in payload["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(payload["messages"][1]["content"], "current user prompt")
        self.assertEqual(payload["messages"][2]["content"][0], {"type": "text", "text": "我先查一下。"})
        self.assertEqual(payload["messages"][2]["content"][1]["type"], "tool_use")
        self.assertEqual(payload["messages"][3]["content"][0]["type"], "tool_result")

        anthropic_payload = _build_anthropic_payload(payload)
        self.assertEqual([message["role"] for message in anthropic_payload["messages"]], ["user", "assistant", "user"])
        self.assertEqual(anthropic_payload["messages"][1]["content"][0], {"type": "text", "text": "我先查一下。"})
        self.assertEqual(anthropic_payload["messages"][1]["content"][1]["type"], "tool_use")
        self.assertEqual(anthropic_payload["messages"][2]["content"][0]["type"], "tool_result")

    def test_llm_runtime_appends_standard_openai_parallel_tool_history(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="openai"), model="gpt-test")

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="current user prompt",
            temperature=0.1,
            post_user_turns=[
                {
                    "role": "assistant",
                    "content": "我一起查一下。",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": {"query": "日经指数"}},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "retrieve_memory", "arguments": '{"query":"风险偏好"}'},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "market result"},
                {"role": "tool", "tool_call_id": "call_2", "content": "memory result"},
            ],
        )

        self.assertEqual(
            [message["role"] for message in payload["messages"]],
            ["system", "user", "assistant", "tool", "tool"],
        )
        assistant_message = payload["messages"][2]
        self.assertEqual(assistant_message["content"], "我一起查一下。")
        self.assertEqual(
            [call["id"] for call in assistant_message["tool_calls"]],
            ["call_1", "call_2"],
        )
        self.assertEqual(
            json.loads(assistant_message["tool_calls"][0]["function"]["arguments"]),
            {"query": "日经指数"},
        )
        self.assertEqual(
            [message["tool_call_id"] for message in payload["messages"][3:]],
            ["call_1", "call_2"],
        )

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

    def test_llm_runtime_maps_provider_safe_native_tool_name_to_capability_id(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        native_tools = [
            {
                "type": "function",
                NATIVE_TOOL_CAPABILITY_ID_FIELD: "mcp.demo.echo",
                "function": {
                    "name": "mcp_demo_echo_abcd123456",
                    "parameters": {"type": "object"},
                },
            }
        ]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_mapped_1",
                                function=SimpleNamespace(
                                    name="mcp_demo_echo_abcd123456",
                                    arguments='{"text":"hi","type":"ignored"}',
                                ),
                            )
                        ]
                    )
                )
            ]
        )

        self.assertEqual(
            runtime._extract_native_tool_call(response, native_tools=native_tools),
            {
                "type": "mcp.demo.echo",
                "text": "hi",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_mapped_1",
                TOOL_MODEL_NAME_FIELD: "mcp_demo_echo_abcd123456",
            },
        )

    def test_llm_runtime_extracts_anthropic_tool_use_to_akane_shape(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="anthropic"), model="claude-test")
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "web_search",
                                "input": {"query": "Akane", "max_results": 3, "type": "ignored"},
                            }
                        ]
                    )
                )
            ]
        )

        self.assertEqual(
            runtime._extract_native_tool_call(response, native_tools=None, bundle=bundle),
            {
                "type": "web_search",
                "query": "Akane",
                "max_results": 3,
                TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
                TOOL_INVOCATION_ID_FIELD: "toolu_1",
            },
        )

    def test_llm_runtime_preserves_multiple_anthropic_tool_uses(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="anthropic"), model="claude-test")
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "web_search",
                                "input": {"query": "日经指数"},
                            },
                            {
                                "type": "tool_use",
                                "id": "toolu_2",
                                "name": "retrieve_memory",
                                "input": {"query": "风险偏好"},
                            },
                        ]
                    )
                )
            ]
        )

        calls = runtime._extract_native_tool_calls(response, native_tools=None, bundle=bundle)

        self.assertEqual([call["type"] for call in calls], ["web_search", "retrieve_memory"])
        self.assertEqual(
            [call[TOOL_INVOCATION_ID_FIELD] for call in calls],
            ["toolu_1", "toolu_2"],
        )

    def test_llm_runtime_keeps_anthropic_model_tool_name_for_safe_name_mapping(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="anthropic"), model="claude-test")
        native_tools = [
            {
                "type": "function",
                NATIVE_TOOL_CAPABILITY_ID_FIELD: "mcp.demo.echo",
                "function": {
                    "name": "mcp_demo_echo_abcd123456",
                    "parameters": {"type": "object"},
                },
            }
        ]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=[
                            {
                                "type": "tool_use",
                                "id": "toolu_safe",
                                "name": "mcp_demo_echo_abcd123456",
                                "input": {"text": "hi"},
                            }
                        ]
                    )
                )
            ]
        )

        self.assertEqual(
            runtime._extract_native_tool_call(response, native_tools=native_tools, bundle=bundle),
            {
                "type": "mcp.demo.echo",
                "text": "hi",
                TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
                TOOL_INVOCATION_ID_FIELD: "toolu_safe",
                TOOL_MODEL_NAME_FIELD: "mcp_demo_echo_abcd123456",
            },
        )

    def test_llm_runtime_keeps_openai_model_tool_name_for_safe_name_mapping(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="openai"), model="gpt-test")
        native_tools = [
            {
                "type": "function",
                NATIVE_TOOL_CAPABILITY_ID_FIELD: "mcp.demo.echo",
                "function": {
                    "name": "mcp_demo_echo_abcd123456",
                    "parameters": {"type": "object"},
                },
            }
        ]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_safe",
                                function=SimpleNamespace(
                                    name="mcp_demo_echo_abcd123456",
                                    arguments='{"text":"hi"}',
                                ),
                            )
                        ]
                    )
                )
            ]
        )

        self.assertEqual(
            runtime._extract_native_tool_call(response, native_tools=native_tools, bundle=bundle),
            {
                "type": "mcp.demo.echo",
                "text": "hi",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_safe",
                TOOL_MODEL_NAME_FIELD: "mcp_demo_echo_abcd123456",
            },
        )

    def test_llm_runtime_returns_native_tool_call_on_internal_carrier(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        runtime._build_completion_kwargs = lambda **_kwargs: {}
        runtime._create_completion = lambda **_kwargs: object()
        runtime._record_cache_metrics = lambda _response, **_kwargs: None
        runtime._extract_text = lambda _response: "我先查一下。"
        runtime._extract_native_tool_calls = lambda _response, **_kwargs: [
            {
                "type": "web_search",
                "query": "Akane",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_native_1",
            }
        ]

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
        self.assertEqual(len(result[NATIVE_TOOL_CALLS_FIELD]), 1)
        self.assertEqual(result[NATIVE_TOOL_CALL_FIELD]["type"], "web_search")
        self.assertEqual(result[NATIVE_TOOL_CALL_FIELD][TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(result["speech"], "我先查一下。")
        self.assertEqual(result["speech_segments"], ["我先查一下。"])
        self.assertEqual(runtime.snapshot_metrics()["native_tool_call_extracted"], 1)

    def test_llm_runtime_preserves_all_native_tool_calls_in_provider_order(self) -> None:
        runtime = LLMRuntime()
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                function=SimpleNamespace(
                                    name="web_search",
                                    arguments='{"action":"search","query":"日经指数"}',
                                ),
                            ),
                            SimpleNamespace(
                                id="call_2",
                                function=SimpleNamespace(
                                    name="retrieve_memory",
                                    arguments='{"query":"风险偏好"}',
                                ),
                            ),
                        ]
                    )
                )
            ]
        )

        calls = runtime._extract_native_tool_calls(response)

        self.assertEqual([call["type"] for call in calls], ["web_search", "retrieve_memory"])
        self.assertEqual(
            [call[TOOL_INVOCATION_ID_FIELD] for call in calls],
            ["call_1", "call_2"],
        )
        self.assertEqual(runtime.snapshot_metrics()["native_tool_calls_extra"], 1)

    def test_llm_runtime_stream_returns_native_tool_call_on_internal_carrier(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        runtime._build_completion_kwargs = lambda **_kwargs: {}
        runtime._record_cache_metrics = lambda _response, **_kwargs: None
        runtime._close_stream = lambda _response: None
        runtime._extract_stream_text = lambda _chunk: "我先查一下。"
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
        self.assertEqual(len(result.parsed[NATIVE_TOOL_CALLS_FIELD]), 1)
        self.assertEqual(result.parsed[NATIVE_TOOL_CALL_FIELD]["type"], "web_search")
        self.assertEqual(result.parsed[NATIVE_TOOL_CALL_FIELD][TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(result.parsed["speech"], "我先查一下。")
        self.assertEqual(result.native_preface_text, "我先查一下。")
        self.assertEqual(runtime.snapshot_metrics()["native_tool_call_extracted"], 1)

    def test_llm_runtime_collects_stream_native_tool_call_to_akane_shape(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        parts: dict[object, dict[str, object]] = {}
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

    def test_llm_runtime_maps_stream_provider_safe_native_tool_name_to_capability_id(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        native_tools = [
            {
                "type": "function",
                NATIVE_TOOL_CAPABILITY_ID_FIELD: "mcp.demo.echo",
                "function": {
                    "name": "mcp_demo_echo_abcd123456",
                    "parameters": {"type": "object"},
                },
            }
        ]
        parts: dict[object, dict[str, object]] = {}
        first_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    index=0,
                    delta=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_stream_mapped",
                                function=SimpleNamespace(name="mcp_demo_echo_abcd123456", arguments='{"text":"'),
                            )
                        ]
                    ),
                )
            ]
        )
        second_chunk = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": 'hi"}'},
                            }
                        ]
                    },
                }
            ]
        }

        runtime._collect_stream_native_tool_call_parts(first_chunk, parts)
        runtime._collect_stream_native_tool_call_parts(second_chunk, parts)

        self.assertEqual(
            runtime._stream_native_tool_call_from_parts(parts, native_tools=native_tools),
            {
                "type": "mcp.demo.echo",
                "text": "hi",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_stream_mapped",
                TOOL_MODEL_NAME_FIELD: "mcp_demo_echo_abcd123456",
            },
        )

    def test_llm_runtime_collects_anthropic_stream_tool_use_to_akane_shape(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="anthropic"), model="claude-test")
        parts: dict[object, dict[str, object]] = {}
        first_chunk = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_stream_1",
                "name": "web_search",
                "input": {},
            },
        }
        second_chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"query":"A'},
        }
        third_chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": 'kane","max_results":3}'},
        }

        runtime._collect_stream_native_tool_call_parts(first_chunk, parts, bundle=bundle)
        runtime._collect_stream_native_tool_call_parts(second_chunk, parts, bundle=bundle)
        runtime._collect_stream_native_tool_call_parts(third_chunk, parts, bundle=bundle)

        self.assertEqual(
            runtime._stream_native_tool_call_from_parts(parts, native_tools=None, bundle=bundle),
            {
                "type": "web_search",
                "query": "Akane",
                "max_results": 3,
                TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
                TOOL_INVOCATION_ID_FIELD: "toolu_stream_1",
            },
        )

    def test_llm_runtime_collects_multiple_anthropic_stream_tool_uses(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="anthropic"), model="claude-test")
        parts: dict[object, dict[str, object]] = {}
        chunks = [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "web_search", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"query":"日经指数"}'},
            },
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "retrieve_memory",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"query":"风险偏好"}'},
            },
        ]
        for chunk in chunks:
            runtime._collect_stream_native_tool_call_parts(chunk, parts, bundle=bundle)

        calls = runtime._stream_native_tool_calls_from_parts(parts, native_tools=None, bundle=bundle)

        self.assertEqual([call["type"] for call in calls], ["web_search", "retrieve_memory"])
        self.assertEqual(
            [call[TOOL_INVOCATION_ID_FIELD] for call in calls],
            ["toolu_1", "toolu_2"],
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
        self.assertEqual(
            [block["text"] for block in system_blocks],
            ["base system", "extra-1", "extra-2", "extra-3", "extra-4", "extra-5"],
        )
        self.assertEqual(sum(1 for block in system_blocks if "cache_control" in block), 4)
        self.assertNotIn("cache_control", system_blocks[-1])
        self.assertEqual(payload["max_tokens"], 4096)

    def test_llm_runtime_records_deepseek_cache_usage_fields(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        recorded: list[tuple[str, int]] = []
        runtime._record_metric = lambda key, amount=1: recorded.append((key, amount))

        runtime._record_cache_metrics(
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_cache_hit_tokens=12,
                    prompt_cache_miss_tokens=34,
                    prompt_tokens=56,
                    completion_tokens=7,
                )
            )
        )

        self.assertIn(("cache_read_tokens", 12), recorded)
        self.assertIn(("cache_creation_tokens", 34), recorded)
        self.assertIn(("reported_input_tokens", 56), recorded)
        self.assertIn(("reported_output_tokens", 7), recorded)

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
    """Provider output limits can still cut a reply mid-JSON. These lock in
    that finish_reason=length is surfaced (metric + last_error) instead of
    passing as a normal short answer."""

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
        runtime._note_parse_fallback("sorry I cannot, sk-secret123456789 not json", phase="stream_chat_json")
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
