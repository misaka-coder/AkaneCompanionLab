from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.llm_client import build_llm_client, normalize_api_protocol, normalize_base_url
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


if __name__ == "__main__":
    unittest.main()
