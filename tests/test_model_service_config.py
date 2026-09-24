from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.model_service_config import (
    ModelServiceConfigStore,
    apply_model_service_settings,
    public_model_service_snapshot,
    probe_model_ids,
    normalize_provider_model_id,
    settings_from_mapping,
    test_model_service as run_model_service_test,
)
from companion_v01.routes.model_services import build_model_services_router


class StubEngine:
    def __init__(self) -> None:
        self.reload_count = 0
        self.last_settings = None

    def reload_model_services(self, settings=None) -> dict:
        self.reload_count += 1
        self.last_settings = settings
        return {"status": "reloaded"}


def build_config() -> SimpleNamespace:
    return SimpleNamespace(
        TEXT_API_KEY="env-secret",
        TEXT_BASE_URL="https://api.deepseek.com/v1",
        TEXT_MODEL_NAME="deepseek-chat",
        TEXT_API_PROTOCOL="openai",
        AUX_API_KEY="env-secret",
        AUX_BASE_URL="https://api.deepseek.com/v1",
        AUX_MODEL_NAME="deepseek-chat",
        AUX_API_PROTOCOL="openai",
        CHAT_API_KEY="env-secret",
        CHAT_BASE_URL="https://api.deepseek.com/v1",
        CHAT_MODEL_NAME="deepseek-chat",
        CHAT_API_PROTOCOL="openai",
        VISION_API_KEY="",
        VISION_BASE_URL="",
        VISION_MODEL_NAME="",
        VISION_API_PROTOCOL="openai",
    )


class ModelServiceConfigTests(unittest.TestCase):
    def test_provider_model_id_preserves_gateway_routing_prefixes(self) -> None:
        self.assertEqual(
            normalize_provider_model_id("[ruru20]gemini-2.5-flash"),
            "[ruru20]gemini-2.5-flash",
        )
        self.assertEqual(
            normalize_provider_model_id("[渠道一-量-t3]gemini-3.5-flash"),
            "[渠道一-量-t3]gemini-3.5-flash",
        )
        self.assertEqual(normalize_provider_model_id("bad model"), "")
        self.assertEqual(normalize_provider_model_id("bad\nmodel"), "")

    def test_gemini_preset_uses_native_generate_content_protocol(self) -> None:
        settings = settings_from_mapping(
            {
                "providerId": "gemini",
                "baseUrl": "https://api.pinaic.com/",
                "apiKey": "gemini-secret",
                "chatModel": "gemini-3.5-flash",
            }
        )

        self.assertEqual(settings.protocol, "gemini")
        self.assertEqual(settings.base_url, "https://api.pinaic.com")

    def test_pinai_preset_selects_responses_protocol(self) -> None:
        settings = settings_from_mapping(
            {
                "providerId": "pinai",
                "apiKey": "pinai-secret",
                "chatModel": "gpt-5.6-sol",
                "chatReasoningEffort": "high",
                "chatMaxOutputTokens": 4096,
            }
        )
        self.assertEqual(settings.protocol, "responses")
        self.assertEqual(settings.base_url, "https://api.pinaic.com/v1")
        self.assertEqual(settings.chat_reasoning_effort, "high")
        self.assertEqual(settings.chat_max_output_tokens, 4096)

    def test_store_preserves_secret_without_public_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ModelServiceConfigStore(Path(temp_dir) / "model_service.json")
            settings = settings_from_mapping(
                {
                    "providerId": "deepseek",
                    "apiKey": "sk-private",
                    "chatModel": "deepseek-chat",
                    "useForImageGeneration": True,
                    "imageGenerationApiKey": "image-private",
                    "imageGenerationModel": "gpt-image-2",
                }
            )
            store.save(settings)

            loaded = store.load()
            self.assertEqual(loaded.api_key, "sk-private")
            public = public_model_service_snapshot(loaded, source="local_file")
            self.assertTrue(public["hasApiKey"])
            self.assertTrue(public["useForImageGeneration"])
            self.assertTrue(public["hasImageGenerationApiKey"])
            self.assertNotIn("sk-private", json.dumps(public, ensure_ascii=False))
            self.assertNotIn("image-private", json.dumps(public, ensure_ascii=False))
            self.assertNotIn("apiKey", public)
            self.assertEqual(public["chatReasoningEffort"], "")
            self.assertEqual(public["chatMaxOutputTokens"], 0)

    def test_invalid_chat_reasoning_effort_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "model_service_chat_reasoning_effort_invalid"):
            settings_from_mapping(
                {
                    "providerId": "pinai",
                    "apiKey": "pinai-secret",
                    "chatModel": "gpt-5.6-sol",
                    "chatReasoningEffort": "ultra",
                }
            )

    def test_apply_one_visible_service_to_chat_aux_and_vision(self) -> None:
        config = build_config()
        settings = settings_from_mapping(
            {
                "providerId": "ollama",
                "chatModel": "qwen2.5:7b",
                "visionModel": "qwen2.5vl:7b",
                "useForVision": True,
            }
        )
        apply_model_service_settings(config, settings)

        self.assertEqual(config.CHAT_BASE_URL, "http://127.0.0.1:11434")
        self.assertEqual(config.TEXT_MODEL_NAME, "qwen2.5:7b")
        self.assertEqual(config.AUX_API_PROTOCOL, "ollama")
        self.assertEqual(config.VISION_MODEL_NAME, "qwen2.5vl:7b")

    def test_disabling_visible_vision_clears_stale_runtime_provider(self) -> None:
        config = build_config()
        config.VISION_API_KEY = "stale-dashscope-key"
        config.VISION_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        config.VISION_MODEL_NAME = "qwen-vl-max"
        settings = settings_from_mapping(
            {
                "providerId": "anthropic",
                "baseUrl": "https://api.pinaic.com",
                "chatModel": "claude-sonnet-5",
                "apiKey": "current-key",
                "useForVision": False,
            }
        )

        apply_model_service_settings(config, settings)

        self.assertEqual(config.CHAT_MODEL_NAME, "claude-sonnet-5")
        self.assertEqual(config.VISION_API_KEY, "")
        self.assertEqual(config.VISION_BASE_URL, "")
        self.assertEqual(config.VISION_MODEL_NAME, "")
        self.assertEqual(config.VISION_API_PROTOCOL, "anthropic")

    def test_standalone_vision_provider_routes_vision_to_its_own_service(self) -> None:
        config = build_config()
        settings = settings_from_mapping(
            {
                "providerId": "openai_compatible",
                "baseUrl": "https://chat.example/v1",
                "apiKey": "chat-key",
                "chatModel": "deepseek-v4-flash",
                "useForVision": True,
                "visionModel": "gemini-3.5-flash",
                "visionApiKey": "vision-key",
                "visionBaseUrl": "https://api.akane.win/v1",
                "visionApiProtocol": "openai",
            }
        )

        self.assertTrue(settings.vision_configured)
        self.assertEqual(settings.vision_api_key, "vision-key")
        self.assertEqual(settings.vision_base_url, "https://api.akane.win/v1")
        self.assertEqual(settings.vision_api_protocol, "openai")

        apply_model_service_settings(config, settings)

        self.assertEqual(config.CHAT_API_KEY, "chat-key")
        self.assertEqual(config.CHAT_BASE_URL, "https://chat.example/v1")
        self.assertEqual(config.VISION_API_KEY, "vision-key")
        self.assertEqual(config.VISION_BASE_URL, "https://api.akane.win/v1")
        self.assertEqual(config.VISION_MODEL_NAME, "gemini-3.5-flash")
        self.assertEqual(config.VISION_API_PROTOCOL, "openai")

    def test_standalone_vision_provider_preserves_existing_key_on_save(self) -> None:
        settings = settings_from_mapping(
            {
                "providerId": "openai_compatible",
                "baseUrl": "https://chat.example/v1",
                "apiKey": "chat-key",
                "chatModel": "deepseek-v4-flash",
                "useForVision": True,
                "visionModel": "gemini-3.5-flash",
                "visionBaseUrl": "https://api.akane.win/v1",
                "visionApiProtocol": "openai",
            },
            existing_vision_api_key="saved-vision-key",
        )

        self.assertEqual(settings.vision_api_key, "saved-vision-key")

    def test_standalone_vision_ignored_when_incomplete(self) -> None:
        config = build_config()
        settings = settings_from_mapping(
            {
                "providerId": "openai_compatible",
                "baseUrl": "https://chat.example/v1",
                "apiKey": "chat-key",
                "chatModel": "deepseek-v4-flash",
                "useForVision": True,
                "visionModel": "gemini-3.5-flash",
                "visionBaseUrl": "",
                "visionApiKey": "",
                "visionApiProtocol": "",
            }
        )

        self.assertFalse(settings.vision_configured)
        self.assertEqual(settings.vision_model, "gemini-3.5-flash")

        apply_model_service_settings(config, settings)

        self.assertEqual(config.VISION_API_KEY, "chat-key")
        self.assertEqual(config.VISION_BASE_URL, "https://chat.example/v1")
        self.assertEqual(config.VISION_MODEL_NAME, "gemini-3.5-flash")
        self.assertEqual(config.VISION_API_PROTOCOL, "openai")

    def test_openai_compatible_probe_and_test_use_real_http_contract(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "local-chat"}]}).encode("utf-8"))

            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(content_length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "id": "chatcmpl-test",
                            "object": "chat.completion",
                            "created": 0,
                            "model": "local-chat",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "OK"},
                                }
                            ],
                        }
                    ).encode("utf-8")
                )

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = settings_from_mapping(
                {
                    "providerId": "openai_compatible",
                    "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                    "apiKey": "local-test-key",
                    "chatModel": "local-chat",
                    "timeoutSeconds": 5,
                }
            )
            self.assertEqual(probe_model_ids(settings), ["local-chat"])
            self.assertEqual(run_model_service_test(settings), "OK")
        finally:
            server.shutdown()
            server.server_close()

    def test_route_save_probe_and_test_are_real_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ModelServiceConfigStore(Path(temp_dir) / "model_service.json")
            config = build_config()
            engine = StubEngine()
            observed = []

            def fake_probe(settings):
                observed.append(("probe", settings.api_key, settings.chat_model))
                return ["model-a", "model-b"]

            def fake_test(settings):
                observed.append(("test", settings.api_key, settings.chat_model))
                return "OK"

            app = FastAPI()
            app.include_router(
                build_model_services_router(
                    store=store,
                    config_module=config,
                    engine=engine,
                    reload_model_services=engine.reload_model_services,
                    model_probe=fake_probe,
                    connection_tester=fake_test,
                )
            )
            client = TestClient(app)

            initial = client.get("/control-center/model-service")
            self.assertEqual(initial.status_code, 200)
            self.assertTrue(initial.json()["hasApiKey"])
            self.assertNotIn("env-secret", initial.text)

            probed = client.post(
                "/control-center/model-service/models",
                json={
                    "providerId": "deepseek",
                    "baseUrl": "https://api.deepseek.com/v1",
                    "apiKey": "",
                    "chatModel": "",
                },
            )
            self.assertEqual(probed.json()["models"], ["model-a", "model-b"])
            self.assertEqual(observed[-1], ("probe", "env-secret", ""))

            tested = client.post(
                "/control-center/model-service/test",
                json={
                    "providerId": "deepseek",
                    "apiKey": "",
                    "chatModel": "deepseek-chat",
                },
            )
            self.assertEqual(tested.json()["status"], "connected")
            self.assertEqual(observed[-1], ("test", "env-secret", "deepseek-chat"))

            saved = client.post(
                "/control-center/model-service",
                json={
                    "providerId": "ollama",
                    "chatModel": "qwen2.5:7b",
                    "useForVision": True,
                    "useForImageGeneration": True,
                    "imageGenerationApiKey": "image-private",
                    "imageGenerationBaseUrl": "https://images.example.com/v1",
                    "imageGenerationModel": "gpt-image-2",
                },
            )
            self.assertEqual(saved.json()["status"], "configured")
            self.assertEqual(engine.reload_count, 1)
            self.assertEqual(config.CHAT_API_PROTOCOL, "openai")
            self.assertEqual(engine.last_settings.protocol, "ollama")
            self.assertTrue(engine.last_settings.use_for_image_generation)
            self.assertEqual(engine.last_settings.image_generation_api_key, "image-private")
            self.assertNotIn("env-secret", saved.text)
            self.assertNotIn("image-private", saved.text)

            preserved = client.post(
                "/control-center/model-service",
                json={
                    "providerId": "ollama",
                    "chatModel": "qwen2.5:7b",
                    "useForVision": True,
                },
            )
            self.assertTrue(preserved.json()["useForImageGeneration"])
            self.assertTrue(preserved.json()["hasImageGenerationApiKey"])
            self.assertTrue(engine.last_settings.use_for_image_generation)
            self.assertEqual(engine.last_settings.image_generation_api_key, "image-private")


if __name__ == "__main__":
    unittest.main()
