from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.gemini_native_client import (
    GeminiNativeCompatClient,
    _GeminiStream,
    _build_gemini_payload,
    _build_openai_style_response,
)
from services.llm_client import build_llm_client, normalize_api_protocol
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.runtime_settings import BotSettingsView


class _FakeStreamResponse:
    def __init__(self, payloads: list[dict]) -> None:
        self._lines: list[bytes] = []
        for payload in payloads:
            self._lines.extend(
                [
                    f"data: {json.dumps(payload)}".encode("utf-8"),
                    b"",
                ]
            )
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        del decode_unicode
        yield from self._lines

    def close(self) -> None:
        self.closed = True


class GeminiNativeClientTests(unittest.TestCase):
    def test_protocol_builds_native_client_without_openai_compatibility(self) -> None:
        self.assertEqual(normalize_api_protocol("gemini", "https://api.pinaic.com/"), "gemini")
        self.assertEqual(
            normalize_api_protocol("auto", "https://generativelanguage.googleapis.com"),
            "gemini",
        )

        client = build_llm_client(
            api_key="test-key",
            base_url="https://api.pinaic.com/",
            protocol="gemini",
            timeout=12,
            max_retries=0,
        )

        self.assertIsInstance(client, GeminiNativeCompatClient)
        self.assertEqual(client._akane_protocol, "gemini")
        self.assertEqual(client.base_url, "https://api.pinaic.com")

    def test_payload_preserves_images_parallel_calls_and_grouped_results(self) -> None:
        payload = _build_gemini_payload(
            {
                "messages": [
                    {"role": "system", "content": "stable system"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "look"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,QUJD"},
                            },
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": "我一起查一下。",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "lookup_city",
                                    "arguments": '{"city":"Beijing"}',
                                },
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "lookup_city",
                                    "arguments": '{"city":"Shanghai"}',
                                },
                            },
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
                    {"role": "tool", "tool_call_id": "call_2", "content": "Shanghai result"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup_city",
                            "description": "Look up one city.",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                "tool_choice": "required",
                "temperature": 0.1,
                "max_tokens": 1024,
            }
        )

        self.assertEqual(payload["systemInstruction"]["parts"], [{"text": "stable system"}])
        self.assertEqual(payload["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(
            [part["functionCall"]["id"] for part in payload["contents"][1]["parts"][1:]],
            ["call_1", "call_2"],
        )
        self.assertEqual(payload["contents"][2]["role"], "user")
        self.assertEqual(
            [part["functionResponse"]["id"] for part in payload["contents"][2]["parts"]],
            ["call_1", "call_2"],
        )
        declaration = payload["tools"][0]["functionDeclarations"][0]
        self.assertEqual(declaration["parameters"]["type"], "OBJECT")
        self.assertEqual(declaration["parameters"]["properties"]["city"]["type"], "STRING")
        self.assertNotIn("additionalProperties", declaration["parameters"])
        self.assertEqual(payload["toolConfig"]["functionCallingConfig"], {"mode": "ANY"})
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 1024)

    def test_nonstream_response_adapts_text_tools_and_cache_usage(self) -> None:
        response = _build_openai_style_response(
            {
                "modelVersion": "gemini-3.5-flash",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "我查一下。"},
                                {
                                    "functionCall": {
                                        "id": "call_native_1",
                                        "name": "web_search",
                                        "args": {"query": "Akane"},
                                    }
                                },
                            ],
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": 120,
                    "cachedContentTokenCount": 80,
                },
            },
            model="gemini-3.5-flash",
        )

        self.assertEqual(response.choices[0].message.content, "我查一下。")
        self.assertEqual(response.choices[0].finish_reason, "tool_calls")
        self.assertEqual(response.choices[0].message.tool_calls[0]["id"], "call_native_1")
        self.assertEqual(
            json.loads(response.choices[0].message.tool_calls[0]["function"]["arguments"]),
            {"query": "Akane"},
        )
        self.assertEqual(response.usage.prompt_tokens, 100)
        self.assertEqual(response.usage.prompt_tokens_details.cached_tokens, 80)

    def test_stream_adapts_text_and_multiple_function_calls(self) -> None:
        response = _FakeStreamResponse(
            [
                {
                    "modelVersion": "gemini-3.5-flash",
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "id": "call_1",
                                            "name": "lookup_city",
                                            "args": {"city": "Beijing"},
                                        }
                                    },
                                    {
                                        "functionCall": {
                                            "id": "call_2",
                                            "name": "lookup_city",
                                            "args": {"city": "Shanghai"},
                                        }
                                    },
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 60},
                },
                {
                    "modelVersion": "gemini-3.5-flash",
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": "稍等一下。"}]},
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 60,
                        "candidatesTokenCount": 10,
                        "cachedContentTokenCount": 40,
                    },
                },
            ]
        )
        stream = _GeminiStream(response=response, model="gemini-3.5-flash")

        chunks = list(stream)

        calls = chunks[0].choices[0].delta.tool_calls
        self.assertEqual([call["id"] for call in calls], ["call_1", "call_2"])
        self.assertEqual(chunks[1].choices[0].delta.content, "稍等一下。")
        self.assertEqual(chunks[1].choices[0].finish_reason, "stop")
        self.assertEqual(stream.usage.prompt_tokens_details.cached_tokens, 40)
        self.assertTrue(response.closed)

    def test_completion_uses_native_generate_content_endpoint(self) -> None:
        client = GeminiNativeCompatClient(
            api_key="test-key",
            base_url="https://api.pinaic.com/",
            timeout=10,
            max_retries=0,
        )
        fake_response = SimpleNamespace(
            ok=True,
            encoding="",
            json=lambda: {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "OK"}]},
                    }
                ]
            },
        )
        with patch("services.gemini_native_client._request_with_retries", return_value=fake_response) as request:
            response = client.chat.completions.create(
                model="gemini-3.5-flash",
                messages=[{"role": "user", "content": "Reply OK"}],
            )

        self.assertEqual(response.choices[0].message.content, "OK")
        self.assertEqual(
            request.call_args.kwargs["url"],
            "https://api.pinaic.com/v1beta/models/gemini-3.5-flash:generateContent",
        )

    def test_runtime_sends_native_tools_without_forcing_json_wire_mode(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime.settings = BotSettingsView(prompt_cache_hints_enabled=False)
        runtime._metrics = {}
        runtime._metrics_lock = __import__("threading").RLock()
        bundle = SimpleNamespace(
            client=SimpleNamespace(
                _akane_protocol="gemini",
                _akane_bundle_role="chat",
                base_url="https://api.pinaic.com",
            ),
            model="gemini-3.5-flash",
        )

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="Return JSON when answering directly.",
            user_prompt="Search if needed.",
            temperature=0.1,
            json_mode=True,
            native_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search.",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ],
            native_tool_choice="auto",
        )

        self.assertEqual(runtime._native_tool_profile(bundle).native_call_shape, "gemini_function_call")
        self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")
        self.assertTrue(payload["parallel_tool_calls"])
        self.assertNotIn("response_format", payload)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_decision_sent"], 1)


if __name__ == "__main__":
    unittest.main()
