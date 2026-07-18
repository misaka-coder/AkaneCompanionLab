from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.routes.voice import _default_gpt_sovits_client_factory
from companion_v01.store import MemoryStore
from companion_v01.vision_service import VisionObservationService


class BotSettingsViewTests(unittest.TestCase):
    def test_voice_snapshot_isolated_in_gpt_sovits_client_factory(self) -> None:
        settings_a = BotSettingsView(
            gpt_sovits_tts_timeout_seconds=11.0,
            gpt_sovits_text_lang="zh",
            gpt_sovits_media_type="wav",
            gpt_sovits_streaming_mode=False,
            gpt_sovits_speed_factor=0.8,
            gpt_sovits_text_split_method="cut5",
        )
        settings_b = BotSettingsView(
            gpt_sovits_tts_timeout_seconds=23.0,
            gpt_sovits_text_lang="en",
            gpt_sovits_media_type="mp3",
            gpt_sovits_streaming_mode=True,
            gpt_sovits_speed_factor=1.2,
            gpt_sovits_text_split_method="cut0",
        )

        client_a = _default_gpt_sovits_client_factory(None, settings=settings_a)("http://127.0.0.1:18001")
        client_b = _default_gpt_sovits_client_factory(None, settings=settings_b)("http://127.0.0.1:18002")

        self.assertEqual(
            (client_a.timeout_seconds, client_a.text_lang, client_a.media_type, client_a.streaming_mode, client_a.speed_factor),
            (11.0, "zh", "wav", False, 0.8),
        )
        self.assertEqual(
            (client_b.timeout_seconds, client_b.text_lang, client_b.media_type, client_b.streaming_mode, client_b.speed_factor),
            (23.0, "en", "mp3", True, 1.2),
        )
        self.assertNotEqual(client_a.text_split_method, client_b.text_split_method)

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
            VISION_API_KEY="vision-secret",
            VISION_BASE_URL="https://vision.example/v1",
            VISION_MODEL_NAME="vision-model",
            VISION_API_PROTOCOL="responses",
            VISION_ENABLED=True,
            VISION_REQUEST_TIMEOUT=45,
            VISION_PROMPT_VERSION="v2",
            VISION_AUTO_SCENE_OBSERVE=False,
            VISION_AUTO_GIFT_OBSERVE=True,
            VISION_AUTO_OUTFIT_OBSERVE=False,
            VISION_MAX_IMAGE_BYTES=1024 * 1024,
            PROMPT_CACHE_HINTS_ENABLED=True,
            PROMPT_CACHE_HINTS_FORCE=True,
            PROMPT_CACHE_NAMESPACE="bot-a",
            PROMPT_CACHE_RETENTION="24h",
            LLM_CONTEXT_WINDOW=12000,
            LLM_AUTO_COMPACT_TOKEN_LIMIT=9000,
        )

        view = BotSettingsView.from_config(config_module)
        public = view.public_snapshot()

        self.assertEqual(view.chat_model_name, "chat-model")
        self.assertEqual(view.aux_api_protocol, "responses")
        self.assertEqual(view.vision_model_name, "vision-model")
        self.assertFalse(view.vision_auto_scene_observe)
        self.assertEqual(view.prompt_cache_namespace, "bot-a")
        self.assertEqual(view.llm_context_window, 12000)
        self.assertNotIn("secret", repr(view))
        self.assertNotIn("api_key", public["chat"])
        self.assertNotIn("vision-secret", repr(view))
        self.assertTrue(public["vision"]["configured"])
        self.assertEqual(public["context"]["auto_compact_token_limit"], 9000)
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
        vision_override = base.overlay(
            {
                "vision_enabled": False,
                "prompt_cache_namespace": "bot-b",
                "llm_context_window": 4096,
            }
        )
        self.assertFalse(vision_override.vision_enabled)
        self.assertEqual(vision_override.prompt_cache_namespace, "bot-b")
        self.assertEqual(vision_override.llm_context_window, 4096)
        with self.assertRaises(ValueError) as raised:
            base.overlay({"absolute_path": "not-allowed"})
        self.assertEqual(str(raised.exception), "bot_settings_unknown_field:absolute_path")

    def test_model_service_overlay_updates_chat_aux_and_vision_together(self) -> None:
        base = BotSettingsView(
            vision_api_key="old-vision-key",
            vision_base_url="https://old.example/v1",
            vision_model_name="old-vision",
        )
        updated = base.with_model_service(
            SimpleNamespace(
                api_key="provider-key",
                base_url="https://provider.example/v1",
                chat_model="provider-chat",
                protocol="responses",
                use_for_vision=True,
                vision_model="provider-vision",
            )
        )

        self.assertEqual(updated.chat_api_key, "provider-key")
        self.assertEqual(updated.aux_model_name, "provider-chat")
        self.assertEqual(updated.vision_model_name, "provider-vision")
        self.assertEqual(updated.chat_api_protocol, "responses")


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


class VisionSettingsIsolationTests(unittest.TestCase):
    def test_two_vision_services_build_clients_from_their_own_settings(self) -> None:
        settings_a = BotSettingsView(
            vision_api_key="vision-a",
            vision_base_url="https://vision-a.example/v1",
            vision_model_name="vision-a-model",
            vision_api_protocol="responses",
        )
        settings_b = BotSettingsView(
            vision_api_key="vision-b",
            vision_base_url="https://vision-b.example/v1",
            vision_model_name="vision-b-model",
            vision_api_protocol="openai",
        )
        calls: list[dict[str, object]] = []

        def fake_build_llm_client(**kwargs: object) -> SimpleNamespace:
            calls.append(dict(kwargs))
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("companion_v01.vision_service.build_llm_client", side_effect=fake_build_llm_client):
                service_a = VisionObservationService(
                    Path(temp_dir) / "a",
                    store=MemoryStore(Path(temp_dir) / "db-a"),
                    settings=settings_a,
                )
                service_b = VisionObservationService(
                    Path(temp_dir) / "b",
                    store=MemoryStore(Path(temp_dir) / "db-b"),
                    settings=settings_b,
                )

        self.assertEqual(
            calls,
            [
                {
                    "api_key": "vision-a",
                    "base_url": "https://vision-a.example/v1",
                    "protocol": "responses",
                    "timeout": 60.0,
                    "max_retries": 0,
                },
                {
                    "api_key": "vision-b",
                    "base_url": "https://vision-b.example/v1",
                    "protocol": "openai",
                    "timeout": 60.0,
                    "max_retries": 0,
                },
            ],
        )
        self.assertIsNotNone(service_a._client)
        self.assertIsNotNone(service_b._client)


if __name__ == "__main__":
    unittest.main()
