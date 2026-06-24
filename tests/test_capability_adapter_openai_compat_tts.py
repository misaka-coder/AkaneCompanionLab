from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.capability_adapters import (
    CapabilityProtocolError,
    InvocationContext,
    OpenAICompatTTSAdapter,
)
from companion_v01.local_capability_config import (
    get_voice_profile_runtime_config,
    save_provider_config,
    save_voice_profile_config,
)
from companion_v01.routes.voice import build_voice_router


class FakeRuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))


class OpenAICompatTTSAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_synthesizes_audio_with_profile(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any]]] = []

            async def synthesize(self, text: str, *, voice_profile_id: str = "", profile=None) -> SimpleNamespace:
                self.calls.append((text, voice_profile_id, dict(profile or {})))
                return SimpleNamespace(audio=b"voice", media_type="audio/wav")

        client = FakeClient()
        adapter = OpenAICompatTTSAdapter(
            provider_id="provider.tts.gpt_sovits.local",
            client=client,
            default_media_type="audio/wav",
        )

        result = await adapter.invoke(
            "tts.synthesize",
            {
                "text": "hello",
                "voice_profile_id": "akane",
                "profile": {"refAudioPath": r"C:\voices\base.wav", "promptText": "base voice"},
            },
            InvocationContext(profile_user_id="master"),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.content["audio"], b"voice")
        self.assertEqual(result.content["mediaType"], "audio/wav")
        self.assertEqual(client.calls[0][1], "akane")

    async def test_emotion_voice_map_overlays_reference_audio(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.profile: dict[str, Any] = {}

            async def synthesize(self, text: str, *, voice_profile_id: str = "", profile=None) -> bytes:
                self.profile = dict(profile or {})
                return b"emotion-audio"

        client = FakeClient()
        adapter = OpenAICompatTTSAdapter(
            provider_id="provider.tts.gpt_sovits.local",
            client=client,
            default_media_type="audio/wav",
        )

        result = await adapter.invoke(
            "tts.synthesize",
            {
                "text": "sound happy",
                "emotion": "joy",
                "voice_profile_id": "akane",
                "profile": {
                    "refAudioPath": r"C:\voices\base.wav",
                    "promptText": "base voice",
                    "emotionVoiceMap": {
                        "joy": {
                            "refAudioPath": r"C:\voices\joy.wav",
                            "promptText": "bright and cheerful",
                            "promptLang": "zh",
                        }
                    },
                },
            },
            InvocationContext(profile_user_id="master"),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(client.profile["refAudioPath"], r"C:\voices\joy.wav")
        self.assertEqual(client.profile["promptText"], "bright and cheerful")
        self.assertEqual(client.profile["promptLang"], "zh")

    async def test_unknown_capability_raises_protocol_error(self) -> None:
        adapter = OpenAICompatTTSAdapter(provider_id="provider.tts.edge", client=object())
        with self.assertRaises(CapabilityProtocolError):
            await adapter.invoke("tts.missing", {"text": "hi"}, InvocationContext())


class OpenAICompatTTSRouteTests(unittest.TestCase):
    def test_voice_profile_config_preserves_private_emotion_map_without_public_path_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_voice_profile_config(
                base_dir=temp_dir,
                profile_user_id="master",
                voice_profile_id="akane",
                payload={
                    "enabled": True,
                    "refAudioPath": r"C:\voices\base.wav",
                    "promptText": "base voice",
                    "emotionVoiceMap": {
                        "joy": {
                            "refAudioPath": r"C:\voices\joy.wav",
                            "promptText": "bright and cheerful",
                        },
                        "bad": {"refAudioPath": "https://example.com/leak.wav", "promptText": "token=bad"},
                    },
                },
            )
            runtime = get_voice_profile_runtime_config(
                base_dir=temp_dir,
                profile_user_id="master",
                voice_profile_id="akane",
            )

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["voiceProfile"]["emotionVoiceIds"], ["joy"])
        self.assertEqual(runtime["emotionVoiceMap"]["joy"]["refAudioPath"], r"C:\voices\joy.wav")
        public_text = json.dumps(saved["voiceProfile"], ensure_ascii=False).lower()
        self.assertNotIn(r"c:\voices", public_text)
        self.assertNotIn("token", public_text)

    def test_tts_route_applies_emotion_voice_map_before_gpt_sovits_call(self) -> None:
        class FakeGptSovitsClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any]]] = []

            async def synthesize(
                self,
                text: str,
                *,
                voice_profile_id: str = "",
                profile: dict[str, Any] | None = None,
            ) -> SimpleNamespace:
                self.calls.append((text, voice_profile_id, dict(profile or {})))
                return SimpleNamespace(audio=b"joy-audio", media_type="audio/wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.tts.gpt_sovits.local",
                payload={"enabled": True, "endpoint": "http://127.0.0.1:9880"},
            )
            save_voice_profile_config(
                base_dir=temp_dir,
                profile_user_id="master",
                voice_profile_id="akane",
                payload={
                    "enabled": True,
                    "displayName": "Akane",
                    "refAudioPath": r"C:\voices\base.wav",
                    "promptText": "base voice",
                    "emotionVoiceMap": {
                        "joy": {
                            "refAudioPath": r"C:\voices\joy.wav",
                            "promptText": "bright and cheerful",
                        }
                    },
                },
            )
            gpt_client = FakeGptSovitsClient()
            app = FastAPI()
            app.include_router(
                build_voice_router(
                    engine=SimpleNamespace(),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    tts_client=object(),
                    runtime_metrics=FakeRuntimeMetrics(),
                    log_event=lambda *_args, **_kwargs: None,
                    gpt_sovits_client_factory=lambda _endpoint: gpt_client,
                )
            )

            response = TestClient(app).post(
                "/tts",
                json={
                    "text": "I am happy today",
                    "real_user_id": "master",
                    "voiceProfileId": "akane",
                    "emotion": "joy",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"joy-audio")
        self.assertEqual(response.headers.get("x-akane-tts-provider"), "provider.tts.gpt_sovits.local")
        self.assertEqual(gpt_client.calls[0][1], "akane")
        self.assertEqual(gpt_client.calls[0][2]["refAudioPath"], r"C:\voices\joy.wav")
        self.assertEqual(gpt_client.calls[0][2]["promptText"], "bright and cheerful")


if __name__ == "__main__":
    unittest.main()
