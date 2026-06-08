from __future__ import annotations

import asyncio
import unittest

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


if __name__ == "__main__":
    unittest.main()
