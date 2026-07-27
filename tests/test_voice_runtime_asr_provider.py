from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from companion_v01.runtime_settings import BotSettingsView
from companion_v01.voice_runtime import (
    FUN_ASR_REALTIME_PROVIDER_ID,
    build_voice_asr_provider,
)


class _CapturingClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)


class VoiceRuntimeASRProviderTests(unittest.TestCase):
    def test_provider_is_default_off_and_missing_config_is_explicit(self) -> None:
        disabled = build_voice_asr_provider(BotSettingsView())
        missing_key = build_voice_asr_provider(
            BotSettingsView(
                fun_asr_realtime_enabled=True,
                dashscope_api_host="llm-example.cn-beijing.maas.aliyuncs.com",
            )
        )
        missing_host = build_voice_asr_provider(
            BotSettingsView(
                fun_asr_realtime_enabled=True,
                dashscope_api_key="test-api-key",
            )
        )

        self.assertEqual((disabled.status, disabled.reason), ("disabled", "fun_asr_realtime_disabled"))
        self.assertEqual((missing_key.status, missing_key.reason), ("missing_config", "dashscope_api_key_missing"))
        self.assertEqual((missing_host.status, missing_host.reason), ("missing_config", "dashscope_api_host_missing"))
        self.assertIsNone(disabled.adapter)

    def test_ready_provider_uses_one_bot_snapshot_without_exposing_secret(self) -> None:
        settings = BotSettingsView(
            fun_asr_realtime_enabled=True,
            dashscope_api_key="test-api-key",
            dashscope_api_host="llm-example.cn-beijing.maas.aliyuncs.com",
            fun_asr_realtime_model="fun-asr-realtime",
            fun_asr_audio_format="pcm",
            fun_asr_sample_rate=16000,
            fun_asr_max_sentence_silence=800,
            fun_asr_vocabulary_id="vocabulary-example",
        )

        resolution = build_voice_asr_provider(settings, client_factory=_CapturingClient)

        self.assertTrue(resolution.ready)
        self.assertEqual(resolution.provider_id, FUN_ASR_REALTIME_PROVIDER_ID)
        assert resolution.adapter is not None
        self.assertEqual(resolution.adapter.provider_id, FUN_ASR_REALTIME_PROVIDER_ID)
        client = resolution.adapter.client
        self.assertEqual(client.kwargs["api_key"], "test-api-key")
        self.assertEqual(client.kwargs["audio_format"], "pcm")
        self.assertEqual(client.kwargs["sample_rate"], 16000)
        self.assertEqual(client.kwargs["max_sentence_silence"], 800)
        self.assertEqual(client.kwargs["vocabulary_id"], "vocabulary-example")
        self.assertNotIn("test-api-key", repr(settings))
        self.assertNotIn("test-api-key", repr(resolution))

    def test_invalid_remote_host_is_structured_as_config_failure(self) -> None:
        resolution = build_voice_asr_provider(
            BotSettingsView(
                fun_asr_realtime_enabled=True,
                dashscope_api_key="test-api-key",
                dashscope_api_host="example.com",
            )
        )

        self.assertEqual(resolution.status, "invalid_config")
        self.assertEqual(resolution.reason, "fun_asr_realtime_config_invalid")
        self.assertIsNone(resolution.adapter)

    def test_runtime_snapshot_redacts_key_and_endpoint(self) -> None:
        settings = BotSettingsView.from_config(
            SimpleNamespace(
                FUN_ASR_REALTIME_ENABLED=True,
                DASHSCOPE_API_KEY="snapshot-secret-key",
                DASHSCOPE_API_HOST="llm-example.cn-beijing.maas.aliyuncs.com",
                FUN_ASR_REALTIME_MODEL="fun-asr-realtime",
                FUN_ASR_AUDIO_FORMAT="pcm",
                FUN_ASR_SAMPLE_RATE=16000,
                FUN_ASR_MAX_SENTENCE_SILENCE=900,
            )
        )

        public = settings.public_snapshot()["voice"]["asr"]["realtime"]

        self.assertTrue(public["enabled"])
        self.assertTrue(public["configured"])
        self.assertEqual(public["provider"], FUN_ASR_REALTIME_PROVIDER_ID)
        self.assertEqual(public["sample_rate"], 16000)
        self.assertNotIn("snapshot-secret-key", repr(settings))
        self.assertNotIn("api_key", public)
        self.assertNotIn("host", public)


if __name__ == "__main__":
    unittest.main()
