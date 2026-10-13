"""Capability HTTP voice payloads responsibilities."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Mapping
from ...local_capability_config import load_capability_config, normalize_voice_profile_config_payload
from services.tts_client import SynthesizedAudio
from .definitions import (
    GPT_SOVITS_PROVIDER_ID,
    PROVIDER_TTS_TEST_DEFAULT_TEXT,
    PROVIDER_TTS_TEST_TEXT_MAX_CHARS,
)


def _resolve_provider_tts_test_endpoint(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    payload: dict[str, Any],
    provider_id: str = GPT_SOVITS_PROVIDER_ID,
) -> dict[str, Any]:
    endpoint_value = str(payload.get("endpoint") or "").strip()
    if endpoint_value:
        from ...local_capability_config import normalize_local_http_endpoint

        normalized = normalize_local_http_endpoint(endpoint_value)
        if not normalized.get("ok"):
            return normalized
        return {"ok": True, "status": "valid", "endpoint": normalized["endpoint"]}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    saved = config.get("providers", {}).get(provider_id)
    endpoint = str((saved or {}).get("endpoint") or "").strip() if isinstance(saved, dict) else ""
    if not endpoint:
        return {"ok": False, "status": "missing_config", "reason": "provider_endpoint_missing"}
    return {"ok": True, "status": "valid", "endpoint": endpoint}

def _safe_provider_tts_test_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return PROVIDER_TTS_TEST_DEFAULT_TEXT
    return text[:PROVIDER_TTS_TEST_TEXT_MAX_CHARS]

def _provider_tts_test_profile_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    text_lang = _safe_provider_tts_profile_id(payload.get("textLang") or payload.get("text_lang"))
    prompt_lang = _safe_provider_tts_profile_id(payload.get("promptLang") or payload.get("prompt_lang"))
    media_type = _safe_provider_tts_profile_id(payload.get("mediaType") or payload.get("media_type"))
    ref_audio_path = _safe_provider_private_path(payload.get("refAudioPath") or payload.get("ref_audio_path"))
    prompt_text = _safe_provider_prompt_text(payload.get("promptText") or payload.get("prompt_text"))
    if text_lang:
        profile["textLang"] = text_lang
    if prompt_lang:
        profile["promptLang"] = prompt_lang
    if media_type:
        profile["mediaType"] = media_type
    if ref_audio_path:
        profile["refAudioPath"] = ref_audio_path
    if prompt_text:
        profile["promptText"] = prompt_text
    raw_emotion_map = payload.get("emotionVoiceMap") or payload.get("emotion_voice_map")
    if isinstance(raw_emotion_map, Mapping):
        normalized = normalize_voice_profile_config_payload(
            str(payload.get("voiceProfileId") or "preview"),
            {"emotionVoiceMap": raw_emotion_map},
        )
        if normalized.get("emotionVoiceMap"):
            profile["emotionVoiceMap"] = normalized["emotionVoiceMap"]
    streaming_mode = _safe_optional_bool(payload.get("streamingMode") if "streamingMode" in payload else payload.get("streaming_mode"))
    if streaming_mode is not None:
        profile["streamingMode"] = streaming_mode
    parallel_infer = _safe_optional_bool(payload.get("parallelInfer") if "parallelInfer" in payload else payload.get("parallel_infer"))
    if parallel_infer is not None:
        profile["parallelInfer"] = parallel_infer
    split_bucket = _safe_optional_bool(payload.get("splitBucket") if "splitBucket" in payload else payload.get("split_bucket"))
    if split_bucket is not None:
        profile["splitBucket"] = split_bucket
    batch_size = _safe_optional_int(payload.get("batchSize") if "batchSize" in payload else payload.get("batch_size"), minimum=1, maximum=32)
    if batch_size is not None:
        profile["batchSize"] = batch_size
    top_k = _safe_optional_int(payload.get("topK") if "topK" in payload else payload.get("top_k"), minimum=1, maximum=100)
    if top_k is not None:
        profile["topK"] = top_k
    top_p = _safe_optional_float(
        payload.get("topP") if "topP" in payload else payload.get("top_p"),
        minimum=0.0,
        maximum=1.0,
    )
    if top_p is not None:
        profile["topP"] = top_p
    temperature = _safe_optional_float(payload.get("temperature"), minimum=0.0, maximum=2.0)
    if temperature is not None:
        profile["temperature"] = temperature
    speed_factor = _safe_optional_float(
        payload.get("speedFactor") if "speedFactor" in payload else payload.get("speed_factor"),
        minimum=0.5,
        maximum=2.0,
    )
    if speed_factor is not None:
        profile["speedFactor"] = speed_factor
    fragment_interval = _safe_optional_float(
        payload.get("fragmentInterval") if "fragmentInterval" in payload else payload.get("fragment_interval"),
        minimum=0.0,
        maximum=2.0,
    )
    if fragment_interval is not None:
        profile["fragmentInterval"] = fragment_interval
    text_split_method = _safe_provider_tts_profile_id(
        payload.get("textSplitMethod") if "textSplitMethod" in payload else payload.get("text_split_method")
    )
    if text_split_method:
        profile["textSplitMethod"] = text_split_method
    return profile

def _provider_tts_test_profile_checks(
    *,
    endpoint: str,
    voice_profile_id: str,
    voice_profile: Mapping[str, Any],
    profile_source: str,
) -> dict[str, bool | str]:
    return {
        "endpoint": bool(str(endpoint or "").strip()),
        "voiceProfileId": bool(str(voice_profile_id or "").strip()),
        "profileApplied": bool(voice_profile),
        "profileSource": str(profile_source or "none")[:40],
        "refAudio": bool(str(voice_profile.get("refAudioPath") or "").strip()),
        "promptText": bool(str(voice_profile.get("promptText") or "").strip()),
    }

def _safe_provider_tts_profile_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    lowered = text.lower()
    if (
        "://" in text
        or "/" in text
        or "\\" in text
        or ":" in text
        or ".." in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return ""
    return text

def _safe_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return None

def _safe_optional_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))

def _safe_optional_float(value: Any, *, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))

def _safe_provider_prompt_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:300]

def _safe_provider_private_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:500]

def _coerce_provider_tts_test_audio(result: Any) -> tuple[bytes, str]:
    if isinstance(result, bytes):
        audio = result
        media_type = "audio/wav"
    elif isinstance(result, SynthesizedAudio):
        audio = result.audio
        media_type = result.media_type or "audio/wav"
    else:
        audio = bytes(getattr(result, "audio", b"") or b"")
        media_type = str(getattr(result, "media_type", "") or "audio/wav")
    if not audio:
        raise RuntimeError("provider_tts_test_empty_audio")
    media_type = str(media_type or "audio/wav").split(";", 1)[0].strip().lower()
    if "/" not in media_type or any(ch in media_type for ch in "\r\n"):
        media_type = "audio/wav"
    if not media_type.startswith("audio/"):
        media_type = "audio/wav"
    return audio, media_type[:80]
