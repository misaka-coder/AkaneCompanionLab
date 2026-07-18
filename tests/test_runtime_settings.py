from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.runtime_settings import BotSettingsView


class BotSettingsViewTests(unittest.TestCase):
    def test_config_snapshot_applies_effective_values_without_exposing_secrets(self) -> None:
        config_module = SimpleNamespace(
            TEXT_API_KEY="text-secret",
            TEXT_BASE_URL="https://text.example/v1",
            TEXT_MODEL_NAME="text-model",
            TEXT_API_PROTOCOL="openai",
            AUX_API_KEY="aux-secret",
            AUX_BASE_URL="https://aux.example/v1",
            AUX_MODEL_NAME="aux-model",
            AUX_API_PROTOCOL="responses",
            CHAT_API_KEY="chat-secret",
            CHAT_BASE_URL="https://chat.example/v1",
            CHAT_MODEL_NAME="chat-model",
            CHAT_API_PROTOCOL="openai",
        )

        view = BotSettingsView.from_config(config_module)
        public = view.public_snapshot()

        self.assertEqual(view.chat_model_name, "chat-model")
        self.assertEqual(view.aux_api_protocol, "responses")
        self.assertNotIn("secret", repr(view))
        self.assertNotIn("api_key", public["chat"])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            view.chat_model_name = "other"  # type: ignore[misc]

    def test_overlay_creates_an_independent_snapshot_and_rejects_unknown_fields(self) -> None:
        base = BotSettingsView(
            chat_api_key="base-key",
            chat_base_url="https://base.example/v1",
            chat_model_name="base-model",
            chat_api_protocol="openai",
        )

        overridden = base.overlay(
            {
                "chat_api_key": "bot-key",
                "chat_model_name": "bot-model",
            }
        )

        self.assertEqual(base.chat_model_name, "base-model")
        self.assertEqual(overridden.chat_model_name, "bot-model")
        self.assertEqual(overridden.chat_base_url, base.chat_base_url)
        with self.assertRaises(ValueError) as raised:
            base.overlay({"absolute_path": "not-allowed"})
        self.assertEqual(str(raised.exception), "bot_settings_unknown_field:absolute_path")


class LLMRuntimeSettingsIsolationTests(unittest.TestCase):
    def test_two_runtime_instances_build_clients_from_their_own_settings(self) -> None:
        settings_a = BotSettingsView(
            aux_api_key="aux-a",
            aux_base_url="https://aux-a.example/v1",
            aux_model_name="aux-a-model",
            aux_api_protocol="openai",
            chat_api_key="chat-a",
            chat_base_url="https://chat-a.example/v1",
            chat_model_name="chat-a-model",
            chat_api_protocol="responses",
        )
        settings_b = BotSettingsView(
            aux_api_key="aux-b",
            aux_base_url="https://aux-b.example/v1",
            aux_model_name="aux-b-model",
            aux_api_protocol="anthropic",
            chat_api_key="chat-b",
            chat_base_url="https://chat-b.example/v1",
            chat_model_name="chat-b-model",
            chat_api_protocol="openai",
        )
        calls: list[dict[str, object]] = []

        def fake_build_llm_client(**kwargs: object) -> SimpleNamespace:
            calls.append(dict(kwargs))
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("companion_v01.llm_runtime.build_llm_client", side_effect=fake_build_llm_client):
                runtime_a = LLMRuntime(
                    settings=settings_a,
                    log_dir=Path(temp_dir) / "a",
                    instance_id="bot-a",
                )
                runtime_b = LLMRuntime(
                    settings=settings_b,
                    log_dir=Path(temp_dir) / "b",
                    instance_id="bot-b",
                )

        self.assertEqual(
            [(call["api_key"], call["base_url"], call["protocol"]) for call in calls],
            [
                ("aux-a", "https://aux-a.example/v1", "openai"),
                ("chat-a", "https://chat-a.example/v1", "responses"),
                ("aux-b", "https://aux-b.example/v1", "anthropic"),
                ("chat-b", "https://chat-b.example/v1", "openai"),
            ],
        )
        self.assertEqual(runtime_a.chat.model, "chat-a-model")
        self.assertEqual(runtime_b.chat.model, "chat-b-model")
        self.assertEqual(runtime_a.aux.model, "aux-a-model")
        self.assertEqual(runtime_b.aux.model, "aux-b-model")


if __name__ == "__main__":
    unittest.main()
