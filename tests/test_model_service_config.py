from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.model_service_config import (
    ModelServiceConfigStore,
    apply_model_service_settings,
    public_model_service_snapshot,
    probe_model_ids,
    normalize_provider_model_id,
    settings_from_mapping,
    resolve_vision_candidate_settings,
    test_model_service as run_model_service_test,
    test_vision_model_service as run_vision_service_test,
)
from companion_v01.runtime_settings import BotSettingsView
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
    def test_chat_api_probe_inherits_actual_defaults_without_saving_or_affecting_independent_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ModelServiceConfigStore(Path(directory) / "models.json")
            store.save(settings_from_mapping({"providerId": "deepseek", "apiKey": "main-key",
                "chatModel": "deepseek-flash", "chatThinkingMode": "", "chatReasoningEffort": ""}))
            before = store.path.read_bytes()
            config = build_config()
            config.LLM_THINKING_MODE = "enabled"
            config.LLM_REASONING_EFFORT = "high"
            observed = []
            def test_target(settings):
                observed.append(settings)
                return "OK"
            app = FastAPI()
            app.include_router(build_model_services_router(store=store, config_module=config,
                engine=StubEngine(), connection_tester=test_target))
            client = TestClient(app)
            cases = (
                ({}, "enabled", "high"),
                ({"chatThinkingMode": "disabled", "chatReasoningEffort": "max"}, "disabled", "max"),
                ({"target": "subagent", "subagent": {"mode": "inherit"}}, "enabled", "high"),
                ({"target": "subagent", "subagent": {"mode": "independent", "providerId": "deepseek",
                    "apiKey": "child-key", "model": "deepseek-flash"}}, "", ""),
            )
            for payload, mode, effort in cases:
                with self.subTest(payload=payload):
                    result = client.post("/control-center/model-service/test", json=payload).json()
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(observed[-1].chat_thinking_mode, mode)
                    self.assertEqual(observed[-1].chat_reasoning_effort, effort)
                    self.assertEqual(store.path.read_bytes(), before)

    def test_child_only_save_keeps_main_reasoning_and_independent_vision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ModelServiceConfigStore(Path(directory) / "models.json")
            original = settings_from_mapping({
                "providerId": "pinai", "apiKey": "main-secret", "chatModel": "gpt-main",
                "chatThinkingMode": "enabled", "chatReasoningEffort": "max", "chatMaxOutputTokens": 8192,
                "standaloneVision": True, "visionModel": "vision-model", "visionApiKey": "vision-secret",
                "visionBaseUrl": "https://vision.example/v1", "visionApiProtocol": "openai",
            })
            store.save(original)
            engine = StubEngine()
            app = FastAPI()
            app.include_router(build_model_services_router(store=store, config_module=build_config(),
                engine=engine, reload_model_services=engine.reload_model_services))
            client = TestClient(app)
            child = {"mode": "independent", "providerId": "openai_compatible", "baseUrl": "https://child.example/v1",
                "apiKey": "child-secret", "model": "child-model", "reasoningEffort": "high"}
            saved = client.post("/control-center/model-service", json={"subagent": child}).json()
            self.assertTrue(saved["ok"], saved)
            self.assertEqual(saved["chatReasoningEffort"], "max")
            self.assertEqual(saved["chatThinkingMode"], "enabled")
            self.assertEqual(saved["chatMaxOutputTokens"], 8192)
            self.assertEqual(saved["subagent"]["reasoningEffort"], "high")
            self.assertTrue(saved["standaloneVision"])
            self.assertEqual(store.load().vision_api_key, "vision-secret")
            runtime = BotSettingsView(llm_reasoning_effort="high").with_model_service(store.load())
            self.assertEqual(runtime.llm_chat_reasoning_effort, "max")
            self.assertEqual(runtime.llm_thinking_mode, "enabled")
            self.assertEqual(runtime.subagent.reasoning_effort, "high")
            changed = client.post("/control-center/model-service", json={"chatReasoningEffort": "low"}).json()
            self.assertEqual(changed["chatReasoningEffort"], "low")
            self.assertEqual(changed["subagent"]["reasoningEffort"], "high")
            for secret in ("main-secret", "vision-secret", "child-secret"):
                self.assertNotIn(secret, json.dumps(saved))

    def test_explicit_shared_vision_never_reactivates_saved_standalone_route(self) -> None:
        settings = settings_from_mapping({
            "providerId": "pinai", "apiKey": "main-key", "chatModel": "gpt-main",
            "chatReasoningEffort": "max", "standaloneVision": False,
            "visionModel": "gpt-main", "visionApiKey": "old-key", "visionBaseUrl": "http://127.0.0.1:8317/v1",
            "visionApiProtocol": "openai",
        })
        with tempfile.TemporaryDirectory() as directory:
            store = ModelServiceConfigStore(Path(directory) / "models.json")
            store.save(settings)
            settings = store.load()
        config = build_config()
        apply_model_service_settings(config, settings)
        runtime = BotSettingsView().with_model_service(settings)
        self.assertEqual(config.VISION_BASE_URL, settings.base_url)
        self.assertEqual(runtime.vision_base_url, settings.base_url)
        self.assertEqual(runtime.vision_api_protocol, "responses")
        candidate = resolve_vision_candidate_settings(settings)
        self.assertEqual(candidate.api_key, "main-key")
        self.assertEqual(candidate.chat_model, "gpt-main")
        self.assertEqual(candidate.chat_reasoning_effort, "max")
        self.assertFalse(public_model_service_snapshot(settings, source="local_file")["standaloneVision"])

    def test_vision_probe_and_test_use_the_selected_route_and_do_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ModelServiceConfigStore(Path(directory) / "models.json")
            store.save(settings_from_mapping({"providerId": "pinai", "apiKey": "main-key", "chatModel": "gpt-main"}))
            before = store.path.read_bytes()
            observed = []
            def probe(settings):
                observed.append(settings)
                return ["vision-only"]
            def test_vision(settings):
                observed.append(settings)
                return "red"
            app = FastAPI()
            app.include_router(build_model_services_router(store=store, config_module=build_config(), engine=StubEngine(),
                model_probe=probe, vision_tester=test_vision))
            client = TestClient(app)
            payload = {"target": "vision", "standaloneVision": True, "visionModel": "vision-only",
                "visionBaseUrl": "https://vision.example/v1", "visionApiProtocol": "openai", "visionApiKey": "vision-key"}
            self.assertEqual(client.post("/control-center/model-service/models", json=payload).json()["models"], ["vision-only"])
            result = client.post("/control-center/model-service/test", json=payload).json()
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["message"], "red")
            self.assertEqual(observed[-1].chat_model, "vision-only")
            self.assertEqual(observed[-1].api_key, "vision-key")
            self.assertEqual(observed[-1].base_url, "https://vision.example/v1")
            self.assertEqual(store.path.read_bytes(), before)
            # An explicitly independent route with missing credentials must
            # neither test the main provider nor overwrite the saved route.
            payload["visionApiKey"] = ""
            rejected = client.post("/control-center/model-service", json=payload).json()
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(store.path.read_bytes(), before)

    def test_independent_targets_can_test_without_main_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = build_config()
            config.CHAT_API_KEY = ""
            observed = []
            def test_target(settings):
                observed.append(settings)
                return "OK"
            app = FastAPI()
            app.include_router(build_model_services_router(store=ModelServiceConfigStore(Path(directory) / "models.json"),
                config_module=config, engine=StubEngine(), connection_tester=test_target, vision_tester=test_target))
            client = TestClient(app)
            for payload in (
                {"target": "subagent", "subagent": {"mode": "independent", "providerId": "openai_compatible",
                    "baseUrl": "https://child.example/v1", "apiKey": "child-key", "model": "child-only"}},
                {"target": "vision", "useForVision": True, "standaloneVision": True, "visionModel": "vision-only",
                    "visionBaseUrl": "https://vision.example/v1", "visionApiProtocol": "openai", "visionApiKey": "vision-key"},
            ):
                result = client.post("/control-center/model-service/test", json=payload).json()
                self.assertTrue(result["ok"], result)
                self.assertTrue(observed[-1].api_key.endswith("-key"))

    def test_api_tests_send_reasoning_and_real_image_and_reject_empty_output(self) -> None:
        settings = settings_from_mapping({"providerId": "pinai", "apiKey": "main-key", "chatModel": "gpt-main",
            "chatReasoningEffort": "max"})
        sent = []
        client = SimpleNamespace(_akane_protocol="responses", base_url=settings.base_url,
            responses=SimpleNamespace(create=lambda **kwargs: sent.append(kwargs) or SimpleNamespace(output_text="red")))
        with patch("companion_v01.model_service_config.build_llm_client", return_value=client):
            self.assertEqual(run_vision_service_test(settings), "red")
        self.assertEqual(sent[0]["reasoning"], {"effort": "max"})
        self.assertGreater(sent[0]["max_output_tokens"], 8)
        self.assertTrue(sent[0]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,"))
        child = settings_from_mapping({"providerId": "deepseek", "apiKey": "child-key", "chatModel": "deepseek-chat",
            "chatThinkingMode": "enabled", "chatReasoningEffort": "high"})
        client = SimpleNamespace(_akane_protocol="openai", base_url=child.base_url,
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: sent.append(kwargs) or
                SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]))))
        with patch("companion_v01.model_service_config.build_llm_client", return_value=client):
            self.assertEqual(run_model_service_test(child), "OK")
        self.assertEqual(sent[-1]["extra_body"], {"thinking": {"type": "enabled"}, "reasoning_effort": "high"})
        client.chat.completions.create = lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))])
        with patch("companion_v01.model_service_config.build_llm_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "response_empty"):
                run_model_service_test(child)

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

    def test_subagent_settings_modes_persistence_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "model_service.json"
            store = ModelServiceConfigStore(store_path)

            # 1. Old config without subagent defaults to inherit
            old_raw = {
                "providerId": "deepseek",
                "apiKey": "sk-main",
                "chatModel": "deepseek-chat",
            }
            settings_old = settings_from_mapping(old_raw)
            self.assertEqual(settings_old.subagent.mode, "inherit")
            self.assertEqual(settings_old.subagent.model, "deepseek-chat")
            self.assertEqual(settings_old.subagent.api_key, "sk-main")
            self.assertTrue(settings_old.subagent.configured)
            store.save(settings_old)

            loaded = store.load()
            self.assertEqual(loaded.subagent.mode, "inherit")
            self.assertEqual(loaded.subagent.api_key, "sk-main")

            # 2. Shared service mode: reuses main service, specifies separate subagent model
            shared_payload = {
                "providerId": "deepseek",
                "apiKey": "sk-main",
                "chatModel": "deepseek-chat",
                "subagent": {
                    "mode": "shared_service",
                    "model": "deepseek-reasoner",
                    "thinkingMode": "enabled",
                    "reasoningEffort": "high",
                    "maxOutputTokens": 8192,
                    "timeoutSeconds": 180,
                },
            }
            settings_shared = settings_from_mapping(shared_payload)
            self.assertEqual(settings_shared.subagent.mode, "shared_service")
            self.assertEqual(settings_shared.subagent.model, "deepseek-reasoner")
            self.assertEqual(settings_shared.subagent.thinking_mode, "enabled")
            self.assertEqual(settings_shared.subagent.reasoning_effort, "high")
            self.assertEqual(settings_shared.subagent.api_key, "sk-main")
            self.assertEqual(settings_shared.subagent.base_url, "https://api.deepseek.com/v1")
            self.assertTrue(settings_shared.subagent.configured)

            # Save and verify disk JSON does NOT duplicate main API key in subagent
            store.save(settings_shared)
            disk_json = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(disk_json["subagent"]["api_key"], "")
            self.assertEqual(disk_json["subagent"]["model"], "deepseek-reasoner")

            # Reload re-derives key from main service
            loaded_shared = store.load()
            self.assertEqual(loaded_shared.subagent.api_key, "sk-main")
            self.assertEqual(loaded_shared.subagent.model, "deepseek-reasoner")

            # Changing main model does NOT overwrite subagent model
            update_main_only = {
                "providerId": "deepseek",
                "apiKey": "sk-main-2",
                "chatModel": "deepseek-chat-v3",
            }
            updated = settings_from_mapping(update_main_only, existing_subagent=loaded_shared.subagent)
            self.assertEqual(updated.chat_model, "deepseek-chat-v3")
            self.assertEqual(updated.subagent.model, "deepseek-reasoner")
            self.assertEqual(updated.subagent.api_key, "sk-main-2")

            # 3. Independent mode: independent provider, url, and key
            indep_payload = {
                "providerId": "deepseek",
                "apiKey": "sk-main",
                "chatModel": "deepseek-chat",
                "subagent": {
                    "mode": "independent",
                    "providerId": "anthropic",
                    "baseUrl": "https://api.anthropic.com",
                    "apiKey": "sk-sub-ant-key",
                    "model": "claude-haiku-4-5-20251001",
                    "timeoutSeconds": 90,
                },
            }
            settings_indep = settings_from_mapping(indep_payload)
            self.assertEqual(settings_indep.subagent.mode, "independent")
            self.assertEqual(settings_indep.subagent.provider_id, "anthropic")
            self.assertEqual(settings_indep.subagent.protocol, "anthropic")
            self.assertEqual(settings_indep.subagent.api_key, "sk-sub-ant-key")
            self.assertEqual(settings_indep.subagent.model, "claude-haiku-4-5-20251001")
            self.assertTrue(settings_indep.subagent.configured)
            store.save(settings_indep)

            # Public snapshot & repr never expose subagent secret
            snapshot = public_model_service_snapshot(settings_indep, source="local_file")
            self.assertTrue(snapshot["subagent"]["hasApiKey"])
            self.assertTrue(snapshot["subagent"]["configured"])
            self.assertNotIn("sk-sub-ant-key", json.dumps(snapshot, ensure_ascii=False))
            self.assertNotIn("sk-sub-ant-key", repr(settings_indep))

            # Blank key preserves existing independent key on same route
            blank_key_payload = {
                "providerId": "deepseek",
                "apiKey": "sk-main",
                "chatModel": "deepseek-chat",
                "subagent": {
                    "mode": "independent",
                    "providerId": "anthropic",
                    "baseUrl": "https://api.anthropic.com",
                    "apiKey": "",
                    "model": "claude-sonnet-5",
                },
            }
            preserved_indep = settings_from_mapping(blank_key_payload, existing_subagent=settings_indep.subagent)
            self.assertEqual(preserved_indep.subagent.api_key, "sk-sub-ant-key")
            self.assertEqual(preserved_indep.subagent.model, "claude-sonnet-5")

            # Changing provider with blank key drops old key
            change_provider_payload = {
                "providerId": "deepseek",
                "apiKey": "sk-main",
                "chatModel": "deepseek-chat",
                "subagent": {
                    "mode": "independent",
                    "providerId": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "",
                    "model": "gpt-5-mini",
                },
            }
            changed_prov = settings_from_mapping(change_provider_payload, existing_subagent=settings_indep.subagent)
            self.assertEqual(changed_prov.subagent.api_key, "")
            self.assertFalse(changed_prov.subagent.configured)

            # Explicit clearApiKey drops key
            clear_key_payload = {
                "providerId": "deepseek",
                "apiKey": "sk-main",
                "chatModel": "deepseek-chat",
                "subagent": {
                    "mode": "independent",
                    "providerId": "anthropic",
                    "baseUrl": "https://api.anthropic.com",
                    "clearApiKey": True,
                    "model": "claude-sonnet-5",
                },
            }
            cleared_indep = settings_from_mapping(clear_key_payload, existing_subagent=settings_indep.subagent)
            self.assertEqual(cleared_indep.subagent.api_key, "")

    def test_subagent_route_actions_target_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ModelServiceConfigStore(Path(temp_dir) / "model_service.json")
            config = build_config()
            engine = StubEngine()
            observed = []

            def fake_probe(settings):
                observed.append(("probe", settings.base_url, settings.api_key, settings.chat_model))
                return ["sub-model-1", "sub-model-2"]

            def fake_test(settings):
                observed.append(("test", settings.base_url, settings.api_key, settings.chat_model))
                if settings.chat_model == "fail-model":
                    raise RuntimeError(f"error from provider with key {settings.api_key}")
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

            # 1. Main model test without target tests main chat model
            resp_main = client.post(
                "/control-center/model-service/test",
                json={
                    "providerId": "deepseek",
                    "apiKey": "main-secret",
                    "chatModel": "deepseek-chat",
                },
            )
            self.assertEqual(resp_main.json()["status"], "connected")
            self.assertEqual(observed[-1], ("test", "https://api.deepseek.com/v1", "main-secret", "deepseek-chat"))

            # 2. Subagent test with target="subagent" in shared_service mode tests subagent model
            resp_sub_shared = client.post(
                "/control-center/model-service/test",
                json={
                    "target": "subagent",
                    "providerId": "deepseek",
                    "apiKey": "main-secret",
                    "chatModel": "deepseek-chat",
                    "subagent": {
                        "mode": "shared_service",
                        "model": "deepseek-reasoner",
                    },
                },
            )
            self.assertEqual(resp_sub_shared.json()["status"], "connected")
            self.assertEqual(observed[-1], ("test", "https://api.deepseek.com/v1", "main-secret", "deepseek-reasoner"))

            # 3. Subagent test with target="subagent" in independent mode tests independent route & key
            resp_sub_indep = client.post(
                "/control-center/model-service/test",
                json={
                    "target": "subagent",
                    "providerId": "deepseek",
                    "apiKey": "main-secret",
                    "chatModel": "deepseek-chat",
                    "subagent": {
                        "mode": "independent",
                        "providerId": "anthropic",
                        "baseUrl": "https://api.anthropic.com",
                        "apiKey": "anthropic-secret-xyz",
                        "model": "claude-haiku-4-5-20251001",
                    },
                },
            )
            self.assertEqual(resp_sub_indep.json()["status"], "connected")
            self.assertEqual(observed[-1], ("test", "https://api.anthropic.com", "anthropic-secret-xyz", "claude-haiku-4-5-20251001"))

            # 4. Error message redacts the subagent api key
            resp_sub_fail = client.post(
                "/control-center/model-service/test",
                json={
                    "target": "subagent",
                    "providerId": "deepseek",
                    "apiKey": "main-secret",
                    "chatModel": "deepseek-chat",
                    "subagent": {
                        "mode": "independent",
                        "providerId": "anthropic",
                        "baseUrl": "https://api.anthropic.com",
                        "apiKey": "anthropic-secret-xyz",
                        "model": "fail-model",
                    },
                },
            )
            self.assertEqual(resp_sub_fail.json()["status"], "request_failed")
            self.assertIn("<redacted>", resp_sub_fail.json()["reason"])
            self.assertNotIn("anthropic-secret-xyz", resp_sub_fail.text)

            # 5. Probe subagent models with target="subagent"
            resp_sub_probe = client.post(
                "/control-center/model-service/models",
                json={
                    "target": "subagent",
                    "providerId": "deepseek",
                    "apiKey": "main-secret",
                    "chatModel": "deepseek-chat",
                    "subagent": {
                        "mode": "independent",
                        "providerId": "anthropic",
                        "baseUrl": "https://api.anthropic.com",
                        "apiKey": "anthropic-secret-xyz",
                    },
                },
            )
            self.assertEqual(resp_sub_probe.json()["models"], ["sub-model-1", "sub-model-2"])
            self.assertEqual(observed[-1], ("probe", "https://api.anthropic.com", "anthropic-secret-xyz", ""))


if __name__ == "__main__":
    unittest.main()
