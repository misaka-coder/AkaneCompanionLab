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
    settings_from_mapping,
    test_model_service as run_model_service_test,
)
from companion_v01.routes.model_services import build_model_services_router


class StubEngine:
    def __init__(self) -> None:
        self.reload_count = 0

    def reload_model_services(self) -> dict:
        self.reload_count += 1
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
    def test_store_preserves_secret_without_public_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ModelServiceConfigStore(Path(temp_dir) / "model_service.json")
            settings = settings_from_mapping(
                {
                    "providerId": "deepseek",
                    "apiKey": "sk-private",
                    "chatModel": "deepseek-chat",
                }
            )
            store.save(settings)

            loaded = store.load()
            self.assertEqual(loaded.api_key, "sk-private")
            public = public_model_service_snapshot(loaded, source="local_file")
            self.assertTrue(public["hasApiKey"])
            self.assertNotIn("sk-private", json.dumps(public, ensure_ascii=False))
            self.assertNotIn("apiKey", public)

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
                },
            )
            self.assertEqual(saved.json()["status"], "configured")
            self.assertEqual(engine.reload_count, 1)
            self.assertEqual(config.CHAT_API_PROTOCOL, "ollama")
            self.assertNotIn("env-secret", saved.text)


if __name__ == "__main__":
    unittest.main()
