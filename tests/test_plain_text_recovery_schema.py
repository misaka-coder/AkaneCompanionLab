"""Terminal recovery keeps provider schemas without reopening tool execution."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.model_request_control import ModelRequestCancelled
from services.gemini_native_client import _build_gemini_payload
from services.llm_client import _build_anthropic_payload
from tests.test_final_response_contract import build_engine, drain, generation_args


TOOLS = [
    {"type": "function", "function": {
        "name": "web_search", "description": "Search the web.",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    }},
]


def response(text="这是模型的最终答复。", *, tool_call=False, truncated=False, protocol="openai"):
    call = {"id": "call_1", "type": "function",
            "function": {"name": "web_search", "arguments": '{"q":"TTS"}'}}
    if protocol == "anthropic":
        content = [{"type": "text", "text": text}]
        if tool_call:
            content.append({"type": "tool_use", "id": "call_1", "name": "web_search", "input": {"q": "TTS"}})
        message = SimpleNamespace(content=content)
    else:
        message = SimpleNamespace(content=text, tool_calls=[call] if tool_call else [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="length" if truncated else "stop")])


class PlainTextRecoverySchemaTests(unittest.TestCase):
    def setUp(self):
        self.audit = patch("config.LLM_PROMPT_AUDIT_ENABLED", False)
        self.audit.start()
        self.addCleanup(self.audit.stop)

    def runtime(self, protocol="openai", provider_response=None):
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol=protocol, base_url="https://example.test/v1"), model="test")
        runtime._request_bundle = lambda **_kwargs: bundle
        runtime._record_metric = lambda *_args, **_kwargs: None
        runtime._record_cache_metrics = lambda *_args, **_kwargs: None
        runtime._capture_runtime_error = lambda *_args, **_kwargs: None
        requests = []

        def create(**kwargs):
            requests.append(kwargs["payload"])
            return provider_response or response(protocol=protocol)

        runtime._create_completion = create
        return runtime, bundle, requests

    def test_all_provider_wires_keep_schema_and_prefix_with_calls_disabled(self):
        for protocol in ("openai", "responses", "anthropic", "gemini"):
            with self.subTest(protocol=protocol):
                runtime, bundle, requests = self.runtime(protocol)
                inputs = {
                    "system_prompt": "stable client contract", "system_extra_blocks": ["stable tools catalog"],
                    "history_turns": [{"role": "user", "content": "previous input"},
                                      {"role": "assistant", "content": "历史回复（显示记录）\n之前的答复。"}],
                    "user_prompt": "current input", "temperature": 0.7, "prompt_cache_key": "chat:final:scope",
                    "native_tools": deepcopy(TOOLS), "ephemeral_turns": [{"role": "user", "content": "energy: 80"}],
                }
                original = deepcopy(inputs)
                structured = runtime._build_completion_kwargs(bundle=bundle, native_tool_choice="auto", **inputs)
                inputs["ephemeral_turns"] = [*inputs["ephemeral_turns"], {"role": "user", "content": "Please finish as plain text."}]
                observations = []
                result = runtime.call_chat_text(**inputs, request_observer=observations.append)
                self.assertEqual(result.text, "这是模型的最终答复。")
                self.assertEqual(len(requests), 1)
                recovery = requests[0]
                self.assertEqual(recovery["tools"], structured["tools"])
                self.assertEqual(recovery["messages"][:-1], structured["messages"])
                self.assertEqual(recovery["tool_choice"], "none")
                self.assertEqual(observations[0]["tool_schema"], runtime._responses_tools_from_chat(structured["tools"])
                                 if protocol == "responses" else structured["tools"])
                for key in ("prompt_cache_key", "prompt_cache_retention", "system_extra_blocks"):
                    self.assertEqual(recovery.get(key), structured.get(key))
                self.assertNotIn("response_format", recovery)
                self.assertEqual(original["native_tools"], inputs["native_tools"])
                if protocol == "responses":
                    before = runtime._responses_payload_from_chat(structured)
                    after = runtime._responses_payload_from_chat(recovery)
                    self.assertEqual(after["instructions"], before["instructions"])
                    self.assertEqual(after["input"][:-1], before["input"])
                    self.assertEqual(after["tools"], before["tools"])
                    self.assertEqual(after["tool_choice"], "none")
                elif protocol == "anthropic":
                    before, after = map(_build_anthropic_payload, (structured, recovery))
                    self.assertEqual(after["system"], before["system"])
                    self.assertEqual(after["tools"], before["tools"])
                    self.assertEqual(after["tool_choice"], {"type": "none"})
                elif protocol == "gemini":
                    before, after = map(_build_gemini_payload, (structured, recovery))
                    self.assertEqual(after["systemInstruction"], before["systemInstruction"])
                    self.assertEqual(after["tools"], before["tools"])
                    self.assertEqual(after["toolConfig"]["functionCallingConfig"], {"mode": "NONE"})

    def test_unexpected_provider_tool_call_is_not_delivered_as_final_text(self):
        for protocol in ("openai", "anthropic"):
            with self.subTest(protocol=protocol):
                runtime, _, requests = self.runtime(protocol, response("我先查一下。", tool_call=True, protocol=protocol))
                result = runtime.call_chat_text(system_prompt="system", user_prompt="user", native_tools=TOOLS)
                self.assertEqual(result.text, "")
                self.assertEqual(result.error, "unexpected_tool_call")
                self.assertEqual(result.raw_text, "我先查一下。")
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["tool_choice"], "none")

    def test_no_tools_and_truncation_keep_the_existing_failure_contract(self):
        runtime, _, requests = self.runtime(provider_response=response("未完成的答复", truncated=True))
        result = runtime.call_chat_text(system_prompt="system", user_prompt="user")
        self.assertEqual(result.text, "")
        self.assertEqual(result.error, "response_truncated")
        self.assertEqual(result.raw_text, "未完成的答复")
        self.assertNotIn("tools", requests[0])
        self.assertNotIn("tool_choice", requests[0])

    def test_observer_rejection_and_cancellation_stop_recovery(self):
        runtime, _, requests = self.runtime()
        result = runtime.call_chat_text(system_prompt="system", user_prompt="user", native_tools=TOOLS,
            request_observer=lambda _request: {"ok": False, "reason": "projection_write_failed"})
        self.assertEqual(requests, [])
        self.assertEqual(result.text, "")
        self.assertIn("projection_write_failed", result.error)
        runtime._create_completion = lambda **_kwargs: (_ for _ in ()).throw(ModelRequestCancelled("cancelled"))
        with self.assertRaises(ModelRequestCancelled):
            runtime.call_chat_text(system_prompt="system", user_prompt="user", native_tools=TOOLS)

    def test_sync_and_stream_recovery_use_the_original_context_schemas(self):
        for mode in ("sync", "stream"):
            with self.subTest(mode=mode):
                calls = []

                class LLM:
                    def call_chat_json_result(self, **kwargs):
                        calls.append(("json", kwargs))
                        return SimpleNamespace(parsed={}, raw_text='{"speech":', fallback_used=True)

                    def stream_chat_json(self, **kwargs):
                        yield from ()
                        return self.call_chat_json_result(**kwargs)

                    def call_chat_text(self, **kwargs):
                        calls.append(("text", kwargs))
                        return SimpleNamespace(text="完整答复。", raw_text="完整答复。", error="")

                context = {"system_prompt": "stable system", "user_prompt": "current input",
                    "fallback": {"speech": "", "tool_call": None}, "visual_defaults": {},
                    "debug_enabled": False, "allow_tool_call": True,
                    "native_tools": deepcopy(TOOLS), "native_tool_choice": "auto",
                    "history_turns": [{"role": "assistant", "content": "old reply"}],
                    "ephemeral_turns": [{"role": "user", "content": "energy: 80"}],
                    "post_user_turns": [
                        {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "web_search", "arguments": '{"q":"TTS"}'}}]},
                        {"role": "tool", "tool_call_id": "call_1", "content": "Search finished."},
                    ]}
                engine = build_engine(LLM(), context=context)
                engine._build_memcore_request_observer = lambda **_kwargs: lambda _request: None
                result = drain(engine._stream_final_response(**generation_args()))[1] if mode == "stream" else engine._build_final_response(**generation_args())
                self.assertEqual(result["speech"], "完整答复。")
                self.assertEqual([kind for kind, _ in calls], ["json", "text"])
                before, after = [kwargs for _, kwargs in calls]
                self.assertIs(after["native_tools"], context["native_tools"])
                for key in ("system_prompt", "user_prompt", "history_turns", "prompt_cache_key", "request_observer"):
                    self.assertEqual(after[key], before[key])
                self.assertEqual(after["ephemeral_turns"][:-1], before["ephemeral_turns"])
                self.assertNotIn("native_tool_choice", after, "Text transport owns the unconditional none policy")
                self.assertFalse(result["_emotion_model_authored"])
                runtime, bundle, requests = self.runtime()
                wire_keys = ("system_prompt", "user_prompt", "temperature", "prompt_cache_key", "user_images",
                    "native_tools", "native_tool_choice", "system_extra_blocks", "history_turns", "ephemeral_turns", "post_user_turns")
                structured = runtime._build_completion_kwargs(bundle=bundle, **{key: before[key] for key in wire_keys if key in before})
                runtime.call_chat_text(**after)
                self.assertEqual(requests[0]["messages"][:-1], structured["messages"], "Recovery feedback follows completed tool records")
                self.assertEqual(requests[0]["tools"], structured["tools"])


if __name__ == "__main__":
    unittest.main()
