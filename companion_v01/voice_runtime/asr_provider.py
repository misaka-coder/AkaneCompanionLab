from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from capcore_adapter_speech import AliyunFunASRRealtimeClient, OpenAICompatASRAdapter


FUN_ASR_REALTIME_PROVIDER_ID = "provider.asr.aliyun_fun_realtime"


@dataclass(frozen=True)
class VoiceASRProviderResolution:
    status: str
    reason: str
    provider_id: str = FUN_ASR_REALTIME_PROVIDER_ID
    adapter: OpenAICompatASRAdapter | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.adapter is not None


def build_voice_asr_provider(
    settings: Any,
    *,
    client_factory: Any = AliyunFunASRRealtimeClient,
) -> VoiceASRProviderResolution:
    """Build the default-off streaming ASR provider from one Bot snapshot."""

    if not bool(getattr(settings, "fun_asr_realtime_enabled", False)):
        return VoiceASRProviderResolution(status="disabled", reason="fun_asr_realtime_disabled")

    api_key = str(getattr(settings, "dashscope_api_key", "") or "").strip()
    api_host = str(getattr(settings, "dashscope_api_host", "") or "").strip()
    if not api_key:
        return VoiceASRProviderResolution(status="missing_config", reason="dashscope_api_key_missing")
    if not api_host:
        return VoiceASRProviderResolution(status="missing_config", reason="dashscope_api_host_missing")

    try:
        client = client_factory(
            api_key=api_key,
            api_host=api_host,
            model=str(getattr(settings, "fun_asr_realtime_model", "fun-asr-realtime") or "fun-asr-realtime"),
            audio_format=str(getattr(settings, "fun_asr_audio_format", "pcm") or "pcm"),
            sample_rate=int(getattr(settings, "fun_asr_sample_rate", 16000) or 16000),
            vocabulary_id=str(getattr(settings, "fun_asr_vocabulary_id", "") or ""),
            semantic_punctuation_enabled=False,
            max_sentence_silence=int(getattr(settings, "fun_asr_max_sentence_silence", 800) or 800),
            heartbeat=False,
        )
    except (TypeError, ValueError):
        return VoiceASRProviderResolution(status="invalid_config", reason="fun_asr_realtime_config_invalid")

    return VoiceASRProviderResolution(
        status="ready",
        reason="",
        adapter=OpenAICompatASRAdapter(
            provider_id=FUN_ASR_REALTIME_PROVIDER_ID,
            client=client,
            display_name="Alibaba Cloud Fun-ASR Realtime",
        ),
    )
