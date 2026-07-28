from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.tts_provider_runtime import (
    EDGE_TTS_PROVIDER_ID,
    GPT_SOVITS_PROVIDER_ID,
    resolve_character_tts_client,
)
from services.tts_client import GptSovitsTTSClient


class FakeTtsResponse:
    status_code = 200
    content = b"wav-audio"
    headers = {"content-type": "audio/wav"}


class FakeTtsSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, json: dict, timeout: float):  # noqa: A002
        self.calls.append({"url": url, "json": dict(json), "timeout": timeout})
        return FakeTtsResponse()


class GptSovitsTTSClientTests(unittest.TestCase):
    def test_gpt_sovits_client_sends_latency_tuning_parameters(self) -> None:
        session = FakeTtsSession()
        client = GptSovitsTTSClient(
            "http://127.0.0.1:9880",
            session=session,
            timeout_seconds=12,
            streaming_mode=True,
            parallel_infer=True,
            split_bucket=False,
            batch_size=64,
            speed_factor=1.15,
            fragment_interval=0.0,
            text_split_method="cut5",
        )

        result = asyncio.run(
            client.synthesize(
                "你好。",
                voice_profile_id="reimu_main",
                profile={
                    "textLang": "zh",
                    "promptLang": "zh",
                    "mediaType": "wav",
                    "streamingMode": False,
                    "speedFactor": 1.05,
                    "promptText": "主人，今天也要一起努力。",
                    "refAudioPath": r"C:\voices\reimu_ref.wav",
                },
            )
        )

        self.assertEqual(result.audio, b"wav-audio")
        self.assertEqual(result.media_type, "audio/wav")
        self.assertEqual(len(session.calls), 1)
        payload = session.calls[0]["json"]
        self.assertEqual(payload["text"], "你好。")
        self.assertEqual(payload["voice_profile_id"], "reimu_main")
        self.assertEqual(payload["streaming_mode"], False)
        self.assertEqual(payload["parallel_infer"], True)
        self.assertEqual(payload["split_bucket"], False)
        self.assertEqual(payload["batch_size"], 32)
        self.assertEqual(payload["speed_factor"], 1.05)
        self.assertEqual(payload["fragment_interval"], 0.0)
        self.assertEqual(payload["text_split_method"], "cut5")
        self.assertEqual(payload["prompt_text"], "主人，今天也要一起努力。")
        self.assertEqual(payload["ref_audio_path"], r"C:\voices\reimu_ref.wav")

    def test_character_runtime_binds_gpt_sovits_profile_without_edge_substitution(self) -> None:
        class CharacterClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def synthesize(
                self,
                text: str,
                *,
                voice_profile_id: str,
                profile: dict[str, object],
            ) -> bytes:
                self.calls.append(
                    {
                        "text": text,
                        "voice_profile_id": voice_profile_id,
                        "profile": dict(profile),
                    }
                )
                return b"character-voice"

        character_client = CharacterClient()
        with patch(
            "companion_v01.tts_provider_runtime.resolve_tts_runtime_provider",
            return_value={
                "status": "ready",
                "requestedProviderId": GPT_SOVITS_PROVIDER_ID,
                "activeProviderId": GPT_SOVITS_PROVIDER_ID,
                "voiceProfileId": "reimu_main",
                "voiceProfile": {"promptText": "灵梦参考音频"},
                "client": character_client,
            },
        ):
            resolved = resolve_character_tts_client(
                engine=object(),
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                base_dir=Path("."),
                config_module=None,
                settings=None,
                edge_tts_client=object(),
            )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.provider_id, GPT_SOVITS_PROVIDER_ID)
        self.assertEqual(asyncio.run(resolved.synthesize("你好。")), b"character-voice")
        self.assertEqual(
            character_client.calls,
            [
                {
                    "text": "你好。",
                    "voice_profile_id": "reimu_main",
                    "profile": {"promptText": "灵梦参考音频"},
                }
            ],
        )

    def test_character_runtime_refuses_edge_when_gpt_sovits_was_requested(self) -> None:
        with patch(
            "companion_v01.tts_provider_runtime.resolve_tts_runtime_provider",
            return_value={
                "status": "degraded",
                "reason": "requested_provider_unreachable",
                "requestedProviderId": GPT_SOVITS_PROVIDER_ID,
                "activeProviderId": EDGE_TTS_PROVIDER_ID,
                "voiceProfileId": "reimu_main",
                "client": None,
            },
        ):
            resolved = resolve_character_tts_client(
                engine=object(),
                profile_user_id="master",
                session_id="desktop",
                character_pack_id="reimu",
                base_dir=Path("."),
                config_module=None,
                settings=None,
                edge_tts_client=object(),
            )

        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
