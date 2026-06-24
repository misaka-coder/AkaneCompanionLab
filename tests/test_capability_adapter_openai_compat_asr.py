from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.capability_adapters import (
    CapabilityProtocolError,
    CapabilityResult,
    InvocationContext,
    OpenAICompatASRAdapter,
)
from companion_v01.local_capability_config import save_provider_config
from companion_v01.local_capability_catalog import build_local_capability_catalog
from companion_v01.routes.capabilities import build_capabilities_router
from companion_v01.routes.voice import build_voice_router


class FakeRuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))


class FakeASRResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeASRSession:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs) -> FakeASRResponse:
        self.posts.append({"url": url, **kwargs})
        return FakeASRResponse({"text": " hello   akane ", "language": "en", "duration": 1.25})


class OpenAICompatASRAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_transcribes_with_fake_client(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def transcribe(self, **kwargs) -> dict[str, Any]:
                self.calls.append(kwargs)
                return {"text": " hi   there ", "language": "en"}

        client = FakeClient()
        adapter = OpenAICompatASRAdapter(provider_id="provider.asr.openai_compat.local", client=client)

        result = await adapter.invoke(
            "asr.transcribe",
            {
                "audio": b"0" * 600,
                "filename": "voice.webm",
                "content_type": "audio/webm",
                "language": "en",
            },
            InvocationContext(profile_user_id="master"),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.content["text"], "hi there")
        self.assertEqual(client.calls[0]["filename"], "voice.webm")
        self.assertEqual(client.calls[0]["content_type"], "audio/webm")

    async def test_http_adapter_uses_openai_transcriptions_path(self) -> None:
        session = FakeASRSession()
        adapter = OpenAICompatASRAdapter(
            provider_id="provider.asr.openai_compat.local",
            endpoint="http://localhost:8000",
            session=session,
            model="whisper-1",
        )

        result = await adapter.invoke(
            "asr.transcribe",
            {"audio": b"1" * 600, "filename": "sample.wav", "content_type": "audio/wav", "language": "en"},
            InvocationContext(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.content["text"], "hello akane")
        self.assertEqual(session.posts[0]["url"], "http://127.0.0.1:8000/v1/audio/transcriptions")
        self.assertEqual(session.posts[0]["data"], {"model": "whisper-1", "language": "en"})
        self.assertEqual(session.posts[0]["files"]["file"][0], "sample.wav")

    async def test_unknown_capability_raises_protocol_error(self) -> None:
        adapter = OpenAICompatASRAdapter(provider_id="provider.asr.openai_compat.local", client=object())
        with self.assertRaises(CapabilityProtocolError):
            await adapter.invoke("asr.missing", {"audio": b"x" * 600}, InvocationContext())


class OpenAICompatASRRouteTests(unittest.TestCase):
    def test_asr_route_uses_configured_openai_compat_provider(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def invoke(self, capability_id: str, args: dict[str, Any], ctx: InvocationContext) -> CapabilityResult:
                self.calls.append({"capability_id": capability_id, "args": args, "ctx": ctx})
                return CapabilityResult(is_error=False, status="ok", content={"text": "voice text", "language": "en"})

        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.asr.openai_compat.local",
                payload={"enabled": True, "endpoint": "http://127.0.0.1:8000"},
            )
            adapter = FakeAdapter()
            app = FastAPI()
            app.include_router(
                build_voice_router(
                    engine=SimpleNamespace(),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, ASR_MAX_UPLOAD_MB=1),
                    tts_client=None,
                    runtime_metrics=FakeRuntimeMetrics(),
                    log_event=lambda *_args, **_kwargs: None,
                    asr_adapter_factory=lambda _endpoint: adapter,
                )
            )

            with TestClient(app) as client:
                response = client.post(
                    "/asr",
                    data={"real_user_id": "master", "language": "en"},
                    files={"file": ("voice.webm", b"2" * 600, "audio/webm")},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["text"], "voice text")
        self.assertEqual(payload["providerId"], "provider.asr.openai_compat.local")
        self.assertEqual(adapter.calls[0]["capability_id"], "asr.transcribe")
        self.assertEqual(adapter.calls[0]["args"]["filename"], "voice.webm")

    def test_catalog_prefers_configured_openai_compat_asr_provider(self) -> None:
        provider_configs = {
            "provider.asr.openai_compat.local": {
                "enabled": True,
                "endpoint": "http://127.0.0.1:8000",
            }
        }

        catalog = build_local_capability_catalog(
            engine=SimpleNamespace(tool_handlers={}),
            config_module=SimpleNamespace(),
            provider_configs=provider_configs,
        )

        by_id = {item["id"]: item for item in catalog["capabilities"]}
        self.assertIn("provider.asr.openai_compat.local", by_id)
        self.assertEqual(by_id["provider.asr.openai_compat.local"]["adapter"], "openai_compat_asr")
        self.assertEqual(catalog["resolutions"]["voice.input.asr"]["activeProviderId"], "provider.asr.openai_compat.local")


class CapabilityAdapterRegistryReloadRouteTests(unittest.TestCase):
    def test_reload_endpoint_returns_redacted_registry_summary(self) -> None:
        class FakeRegistry:
            def __init__(self) -> None:
                self.reload_calls: list[str] = []

            def reload(self, provider_id: str) -> None:
                self.reload_calls.append(provider_id)

            def list_manifests(self):
                return (SimpleNamespace(provider_id="provider.demo"),)

            def list_invalid(self):
                return (
                    SimpleNamespace(
                        source_path=Path(r"C:\Users\ExampleUser\secret\bad.yaml"),
                        source_layer="profile",
                        provider_id="provider.bad",
                        reason="invalid_config",
                        detail=r"token=real-secret C:\Users\ExampleUser\secret\bad.yaml",
                    ),
                )

        registry = FakeRegistry()
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=SimpleNamespace(tool_handlers={}, capability_adapter_registry=registry),
                config_module=SimpleNamespace(),
                runtime_metrics=FakeRuntimeMetrics(),
            )
        )

        with TestClient(app) as client:
            response = client.post(
                "/capabilities/adapter-registry/reload",
                json={"providerId": "provider.demo"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(registry.reload_calls, ["provider.demo"])
        self.assertEqual(payload["validCount"], 1)
        self.assertEqual(payload["invalidCount"], 1)
        self.assertEqual(payload["invalid"][0]["source"], "bad.yaml")
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("exampleuser", serialized)
        self.assertNotIn("real-secret", serialized)
        self.assertNotIn(r"c:\users", serialized)


if __name__ == "__main__":
    unittest.main()
