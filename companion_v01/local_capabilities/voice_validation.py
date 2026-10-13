"""Private voice fields and safe public emotion-voice projection."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .common import (
    _safe_optional_bool,
    _safe_optional_float,
    _safe_optional_int,
    _safe_path_basename,
    _safe_short_text,
    _safe_short_token,
)
from .definitions import (
    EMOTION_VOICE_ALIAS_MAX_COUNT,
    EMOTION_VOICE_ID_RE,
    PROFILE_ID_SAFE_CHARS,
    VOICE_PROFILE_PATH_MAX_LENGTH,
    VOICE_PROFILE_TEXT_MAX_LENGTH,
)


def normalize_voice_profile_config_payload(profile_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    provider_id = str(
        payload.get("providerId") or payload.get("provider_id") or "provider.tts.gpt_sovits.local"
    ).strip()
    if provider_id == "provider.tts.irodori.local":
        raw_voice = str(payload.get("irodoriVoiceId") or "").strip()
        if raw_voice and (not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", raw_voice)
                          or raw_voice.lower() in {"none", "no_ref", "noref"}):
            return {"ok": False, "status": "invalid_config", "reason": "irodori_voice_id_invalid"}
        return {"ok": True, "status": "valid", "providerId": provider_id,
                "enabled": bool(payload.get("enabled", True)),
                "displayName": (_safe_short_text(payload.get("displayName") or profile_id) or profile_id)[:80],
                "textLang": "ja", "promptLang": "", "mediaType": "wav", "irodoriVoiceId": raw_voice}
    if provider_id != "provider.tts.gpt_sovits.local":
        return {"ok": False, "status": "unsupported_provider", "reason": "voice_profile_provider_not_supported"}
    ref_audio_path = _safe_private_local_path(payload.get("refAudioPath") or payload.get("ref_audio_path"))
    prompt_text = _safe_private_prompt_text(payload.get("promptText") or payload.get("prompt_text"))
    display_name = _safe_short_text(payload.get("displayName") or payload.get("name") or profile_id) or profile_id
    return {
        "ok": True,
        "status": "valid",
        "providerId": provider_id,
        "enabled": bool(payload.get("enabled", True)),
        "displayName": display_name[:80],
        "textLang": _safe_short_token(payload.get("textLang") or payload.get("text_lang") or "zh", default="zh"),
        "promptLang": _safe_short_token(payload.get("promptLang") or payload.get("prompt_lang") or "zh", default="zh"),
        "mediaType": _safe_short_token(payload.get("mediaType") or payload.get("media_type") or "wav", default="wav"),
        "refAudioPath": ref_audio_path,
        "promptText": prompt_text,
        "emotionVoiceMap": _safe_emotion_voice_map(payload.get("emotionVoiceMap") or payload.get("emotion_voice_map")),
        "streamingMode": _safe_optional_bool(
            payload.get("streamingMode") if "streamingMode" in payload else payload.get("streaming_mode"),
        ),
        "parallelInfer": _safe_optional_bool(
            payload.get("parallelInfer") if "parallelInfer" in payload else payload.get("parallel_infer")
        ),
        "splitBucket": _safe_optional_bool(
            payload.get("splitBucket") if "splitBucket" in payload else payload.get("split_bucket")
        ),
        "batchSize": _safe_optional_int(
            payload.get("batchSize") if "batchSize" in payload else payload.get("batch_size"),
            minimum=1,
            maximum=32,
        ),
        "topK": _safe_optional_int(
            payload.get("topK") if "topK" in payload else payload.get("top_k"),
            minimum=1,
            maximum=100,
        ),
        "topP": _safe_optional_float(
            payload.get("topP") if "topP" in payload else payload.get("top_p"),
            minimum=0.0,
            maximum=1.0,
        ),
        "temperature": _safe_optional_float(payload.get("temperature"), minimum=0.0, maximum=2.0),
        "speedFactor": _safe_optional_float(
            payload.get("speedFactor") if "speedFactor" in payload else payload.get("speed_factor"),
            minimum=0.5,
            maximum=2.0,
        ),
        "fragmentInterval": _safe_optional_float(
            payload.get("fragmentInterval") if "fragmentInterval" in payload else payload.get("fragment_interval"),
            minimum=0.0,
            maximum=2.0,
        ),
        "textSplitMethod": _safe_short_token(
            payload.get("textSplitMethod") if "textSplitMethod" in payload else payload.get("text_split_method"),
            default="",
        ),
    }


def _sanitize_voice_profile_configs(raw_profiles: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    profiles: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_profiles in (None, ""):
        return profiles, warnings
    if not isinstance(raw_profiles, Mapping):
        return profiles, [{"status": "invalid_config", "reason": "voice_profiles_must_be_object"}]

    for raw_profile_id, raw_config in raw_profiles.items():
        profile_id = _safe_voice_profile_id(raw_profile_id)
        if not profile_id:
            warnings.append({"status": "invalid_config", "reason": "voice_profile_id_invalid"})
            continue
        normalized = normalize_voice_profile_config_payload(
            profile_id, raw_config if isinstance(raw_config, Mapping) else {}
        )
        if not normalized.get("ok"):
            warnings.append(
                {
                    "voiceProfileId": profile_id,
                    "status": normalized.get("status") or "invalid_config",
                    "reason": normalized.get("reason") or "invalid_voice_profile_config",
                }
            )
            continue
        if normalized["providerId"] == "provider.tts.irodori.local":
            profiles[profile_id] = {key: value for key, value in normalized.items() if key not in {"ok", "status"}}
            updated_at = _safe_short_text(raw_config.get("updatedAt")) if isinstance(raw_config, Mapping) else ""
            if updated_at:
                profiles[profile_id]["updatedAt"] = updated_at
            continue
        profiles[profile_id] = {
            "providerId": normalized["providerId"],
            "enabled": bool(normalized["enabled"]),
            "displayName": normalized["displayName"],
            "textLang": normalized["textLang"],
            "promptLang": normalized["promptLang"],
            "mediaType": normalized["mediaType"],
            "refAudioPath": normalized["refAudioPath"],
            "promptText": normalized["promptText"],
            "emotionVoiceMap": normalized["emotionVoiceMap"],
            "streamingMode": normalized["streamingMode"],
            "parallelInfer": normalized["parallelInfer"],
            "splitBucket": normalized["splitBucket"],
            "batchSize": normalized["batchSize"],
            "topK": normalized["topK"],
            "topP": normalized["topP"],
            "temperature": normalized["temperature"],
            "speedFactor": normalized["speedFactor"],
            "fragmentInterval": normalized["fragmentInterval"],
            "textSplitMethod": normalized["textSplitMethod"],
        }
        updated_at = _safe_short_text((raw_config or {}).get("updatedAt")) if isinstance(raw_config, Mapping) else ""
        if updated_at:
            profiles[profile_id]["updatedAt"] = updated_at
    return profiles, warnings


def _safe_voice_profile_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-")
    return safe[:120] if safe else ""


def _safe_private_prompt_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:VOICE_PROFILE_TEXT_MAX_LENGTH]


def _safe_private_local_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:VOICE_PROFILE_PATH_MAX_LENGTH]


def _safe_emotion_voice_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_emotion_id, raw_entry in list(value.items())[:32]:
        emotion_id = _safe_emotion_voice_id(raw_emotion_id)
        if not emotion_id:
            continue
        entry: dict[str, Any] = {}
        if isinstance(raw_entry, Mapping):
            ref_audio_path = _safe_private_local_path(
                raw_entry.get("refAudioPath")
                or raw_entry.get("ref_audio_path")
                or raw_entry.get("referenceAudioPath")
                or raw_entry.get("reference_audio_path")
            )
            if ref_audio_path:
                entry["refAudioPath"] = ref_audio_path
            prompt_text = _safe_private_prompt_text(raw_entry.get("promptText") or raw_entry.get("prompt_text"))
            if prompt_text:
                entry["promptText"] = prompt_text
            text_lang = _safe_short_token(raw_entry.get("textLang") or raw_entry.get("text_lang"), default="")
            if text_lang:
                entry["textLang"] = text_lang
            prompt_lang = _safe_short_token(raw_entry.get("promptLang") or raw_entry.get("prompt_lang"), default="")
            if prompt_lang:
                entry["promptLang"] = prompt_lang
            media_type = _safe_short_token(raw_entry.get("mediaType") or raw_entry.get("media_type"), default="")
            if media_type:
                entry["mediaType"] = media_type
            aliases = _safe_emotion_voice_aliases(
                raw_entry.get("aliases")
                or raw_entry.get("emotionAliases")
                or raw_entry.get("emotion_aliases")
            )
            if aliases:
                entry["aliases"] = aliases
        if entry:
            result[emotion_id] = entry
    return result


def _safe_emotion_voice_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if EMOTION_VOICE_ID_RE.fullmatch(text) else ""


def _safe_emotion_voice_aliases(value: Any) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"[,，;；\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        alias = _safe_emotion_voice_id(raw_value)
        folded = alias.casefold()
        if not alias or folded in seen:
            continue
        seen.add(folded)
        result.append(alias)
        if len(result) >= EMOTION_VOICE_ALIAS_MAX_COUNT:
            break
    return result


def _merge_submitted_emotion_voice_map(
    existing_value: Any,
    submitted_value: Any,
) -> tuple[dict[str, dict[str, Any]], str]:
    existing = _safe_emotion_voice_map(existing_value)
    submitted = _safe_emotion_voice_map(submitted_value)
    merged: dict[str, dict[str, Any]] = {}
    for emotion_id, submitted_entry in submitted.items():
        entry = dict(submitted_entry)
        previous = existing.get(emotion_id) if isinstance(existing.get(emotion_id), Mapping) else {}
        for key in ("refAudioPath", "promptText", "textLang", "promptLang", "mediaType"):
            if not entry.get(key) and previous.get(key):
                entry[key] = previous[key]
        has_reference = bool(entry.get("refAudioPath"))
        has_prompt = bool(entry.get("promptText"))
        if has_reference != has_prompt:
            return {}, "emotion_voice_reference_pair_required"
        if not has_reference:
            return {}, "emotion_voice_reference_required"
        merged[emotion_id] = entry
    return merged, ""


def _public_emotion_voice_samples(value: Any) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for emotion_id, entry in _safe_emotion_voice_map(value).items():
        ref_audio_path = str(entry.get("refAudioPath") or "")
        prompt_text = str(entry.get("promptText") or "")
        if not ref_audio_path or not prompt_text:
            continue
        samples.append(
            {
                "emotionId": emotion_id,
                "aliases": list(entry.get("aliases") or []),
                "referenceAudioName": _safe_path_basename(ref_audio_path),
                "promptTextLength": len(prompt_text),
            }
        )
    return samples
