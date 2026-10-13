"""Voice profile operations and bounded local model-folder inspection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .approval_policy import (
    project_capcore_catalog_fields,
    with_capability_approval_metadata,
)
from .common import (
    _now_iso,
    _public_config_scope,
    _safe_optional_bool,
    _safe_optional_float,
    _safe_optional_int,
    _safe_path_basename,
    _safe_short_text,
    _safe_short_token,
)
from .definitions import (
    CONFIG_SCHEMA_VERSION,
    VOICE_MODEL_AUDIO_EXTENSIONS,
    VOICE_MODEL_CONFIG_FILENAMES,
    VOICE_MODEL_FOLDER_MAX_LENGTH,
    VOICE_MODEL_FOLDER_MAX_SCAN_FILES,
    VOICE_MODEL_GPT_EXTENSIONS,
    VOICE_MODEL_SOVITS_EXTENSIONS,
)
from .storage import (
    load_capability_config,
    write_capability_config,
)
from .voice_validation import (
    _merge_submitted_emotion_voice_map,
    _public_emotion_voice_samples,
    _safe_private_local_path,
    _safe_private_prompt_text,
    _safe_voice_profile_id,
    normalize_voice_profile_config_payload,
)


def list_voice_profile_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    profiles = [
        build_voice_profile_config_entry(profile_id, profile_config)
        for profile_id, profile_config in sorted((config.get("voiceProfiles") or {}).items())
    ]
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "execution": "config-skeleton",
        "configStatus": config.get("configStatus") or "available",
        "warnings": list(config.get("warnings") or []),
        "configScope": _public_config_scope(profile_user_id),
        "voiceProfiles": profiles,
        "summary": _summarize_voice_profile_entries(profiles),
    }


def inspect_gpt_sovits_voice_model_folder(
    *,
    provider_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    provider_id = str(provider_id or "").strip()
    if provider_id != "provider.tts.gpt_sovits.local":
        return {
            "ok": False,
            "status": "unsupported_provider",
            "providerId": provider_id,
            "reason": "voice_model_folder_inspect_not_supported",
            "refresh": False,
        }

    payload = payload if isinstance(payload, Mapping) else {}
    folder_value = (
        payload.get("folderPath")
        or payload.get("folder_path")
        or payload.get("modelFolderPath")
        or payload.get("model_folder_path")
        or payload.get("path")
    )
    normalized_folder = _safe_private_local_path(folder_value)
    if not normalized_folder:
        return {
            "ok": False,
            "status": "invalid_request",
            "providerId": provider_id,
            "reason": "model_folder_path_invalid",
            "refresh": False,
        }
    if len(normalized_folder) > VOICE_MODEL_FOLDER_MAX_LENGTH:
        return {
            "ok": False,
            "status": "invalid_request",
            "providerId": provider_id,
            "reason": "model_folder_path_too_long",
            "refresh": False,
        }

    folder = Path(normalized_folder).expanduser()
    if not folder.is_absolute():
        return {
            "ok": False,
            "status": "invalid_request",
            "providerId": provider_id,
            "reason": "model_folder_must_be_absolute",
            "refresh": False,
        }
    try:
        folder = folder.resolve()
    except OSError:
        return {
            "ok": False,
            "status": "invalid_request",
            "providerId": provider_id,
            "reason": "model_folder_path_invalid",
            "refresh": False,
        }
    if not folder.is_dir():
        return {
            "ok": False,
            "status": "missing_model_folder",
            "providerId": provider_id,
            "reason": "model_folder_not_found",
            "refresh": False,
        }

    files = _scan_voice_model_folder_files(folder)
    yaml_path = _find_voice_model_yaml(folder, files)
    yaml_values = _parse_voice_model_yaml_scalars(yaml_path) if yaml_path else {}

    folder_name = _safe_short_text(folder.name, limit=80) or "gpt_sovits_voice"
    profile_id = _safe_voice_profile_id(
        _first_voice_model_yaml_value(yaml_values, "voice_profile_id", "profile_id", "id") or folder_name
    )
    display_name = (
        _safe_short_text(
            _first_voice_model_yaml_value(yaml_values, "display_name", "voice_name", "name") or folder_name,
            limit=80,
        )
        or profile_id
        or "GPT-SoVITS Voice"
    )

    yaml_ref_audio = _resolve_voice_model_file_from_yaml(
        folder,
        _first_voice_model_yaml_value(yaml_values, "ref_audio_path", "reference_audio_path", "prompt_audio_path"),
        VOICE_MODEL_AUDIO_EXTENSIONS,
    )
    ref_audio_path = yaml_ref_audio or _best_voice_model_file(
        files, VOICE_MODEL_AUDIO_EXTENSIONS, ("ref", "prompt", "sample", "output")
    )
    prompt_text = _safe_private_prompt_text(
        _first_voice_model_yaml_value(yaml_values, "prompt_text", "reference_text", "ref_text")
    )
    text_lang = _safe_short_token(
        _first_voice_model_yaml_value(yaml_values, "text_lang", "text_language") or "zh",
        default="zh",
    )
    prompt_lang = _safe_short_token(
        _first_voice_model_yaml_value(yaml_values, "prompt_lang", "prompt_language", "ref_lang") or text_lang,
        default="zh",
    )
    media_type = _safe_short_token(
        _first_voice_model_yaml_value(yaml_values, "media_type", "return_media_type") or "wav",
        default="wav",
    )
    parallel_infer = _safe_optional_bool(_first_voice_model_yaml_value(yaml_values, "parallel_infer"))
    split_bucket = _safe_optional_bool(_first_voice_model_yaml_value(yaml_values, "split_bucket"))
    batch_size = _safe_optional_int(
        _first_voice_model_yaml_value(yaml_values, "batch_size"), minimum=1, maximum=32
    )
    top_k = _safe_optional_int(_first_voice_model_yaml_value(yaml_values, "top_k"), minimum=1, maximum=100)
    top_p = _safe_optional_float(
        _first_voice_model_yaml_value(yaml_values, "top_p"), minimum=0.0, maximum=1.0
    )
    temperature = _safe_optional_float(
        _first_voice_model_yaml_value(yaml_values, "temperature"), minimum=0.0, maximum=2.0
    )
    speed_factor = _safe_optional_float(
        _first_voice_model_yaml_value(yaml_values, "speed_factor"), minimum=0.5, maximum=2.0
    )
    fragment_interval = _safe_optional_float(
        _first_voice_model_yaml_value(yaml_values, "fragment_interval"), minimum=0.0, maximum=2.0
    )
    text_split_method = _safe_short_token(
        _first_voice_model_yaml_value(yaml_values, "text_split_method"), default=""
    )

    gpt_weight = _resolve_voice_model_file_from_yaml(
        folder,
        _first_voice_model_yaml_value(yaml_values, "gpt_path", "t2s_weights_path", "t2s_weights"),
        VOICE_MODEL_GPT_EXTENSIONS,
    ) or _best_voice_model_file(files, VOICE_MODEL_GPT_EXTENSIONS, ("gpt", "t2s"))
    sovits_weight = _resolve_voice_model_file_from_yaml(
        folder,
        _first_voice_model_yaml_value(yaml_values, "sovits_path", "vits_weights_path", "vits_weights"),
        VOICE_MODEL_SOVITS_EXTENSIONS,
    ) or _best_voice_model_file(files, VOICE_MODEL_SOVITS_EXTENSIONS, ("sovits", "vits"))

    if yaml_path is None and not ref_audio_path and not gpt_weight and not sovits_weight:
        return {
            "ok": False,
            "status": "missing_model_files",
            "providerId": provider_id,
            "reason": "model_folder_has_no_supported_files",
            "refresh": False,
        }

    warnings: list[str] = []
    if yaml_path is None:
        warnings.append("tts_infer_yaml_missing")
    if not ref_audio_path:
        warnings.append("reference_audio_missing")
    if not prompt_text:
        warnings.append("prompt_text_missing")
    if not gpt_weight:
        warnings.append("gpt_weight_missing")
    if not sovits_weight:
        warnings.append("sovits_weight_missing")

    return {
        "ok": True,
        "status": "inspected",
        "providerId": provider_id,
        "suggestedProfile": {
            "voiceProfileId": profile_id or "gpt_sovits_voice",
            "displayName": display_name,
            "enabled": True,
            "textLang": text_lang,
            "promptLang": prompt_lang,
            "mediaType": media_type,
            "refAudioPath": str(ref_audio_path) if ref_audio_path else "",
            "promptText": prompt_text,
            "parallelInfer": parallel_infer,
            "splitBucket": split_bucket,
            "batchSize": batch_size,
            "topK": top_k,
            "topP": top_p,
            "temperature": temperature,
            "speedFactor": speed_factor,
            "fragmentInterval": fragment_interval,
            "textSplitMethod": text_split_method,
        },
        "detected": {
            "folderName": folder_name,
            "configFileName": _safe_path_basename(yaml_path) if yaml_path else "",
            "referenceAudioName": _safe_path_basename(ref_audio_path),
            "gptWeightName": _safe_path_basename(gpt_weight),
            "sovitsWeightName": _safe_path_basename(sovits_weight),
            "audioCandidateCount": sum(1 for item in files if item.suffix.lower() in VOICE_MODEL_AUDIO_EXTENSIONS),
            "scannedFileCount": len(files),
        },
        "warnings": warnings,
        "autoEnable": False,
        "refresh": False,
    }


def save_voice_profile_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    voice_profile_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    profile_id = _safe_voice_profile_id(voice_profile_id)
    if not profile_id:
        return {"ok": False, "status": "invalid_voice_profile", "reason": "voice_profile_id_invalid"}
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "voiceProfileId": profile_id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }

    voice_profiles = dict(config.get("voiceProfiles") or {})
    existing_profile = voice_profiles.get(profile_id)
    existing_profile = existing_profile if isinstance(existing_profile, Mapping) else {}
    normalized = normalize_voice_profile_config_payload(profile_id, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "voiceProfileId": profile_id,
            "reason": normalized.get("reason") or "invalid_voice_profile_config",
        }
    if normalized["providerId"] == "provider.tts.irodori.local":
        if "irodoriVoiceId" not in payload and existing_profile.get("providerId") == normalized["providerId"]:
            normalized["irodoriVoiceId"] = str(existing_profile.get("irodoriVoiceId") or "")
        voice_profiles[profile_id] = {key: value for key, value in normalized.items() if key not in {"ok", "status"}}
        voice_profiles[profile_id]["updatedAt"] = _now_iso()
        write_capability_config(base_dir=base_dir, profile_user_id=profile_user_id,
            config={"schemaVersion": CONFIG_SCHEMA_VERSION, "providers": config.get("providers", {}),
                    "workflows": config.get("workflows", {}), "voiceProfiles": voice_profiles,
                    "mcpServers": config.get("mcpServers", {})})
        return {"ok": True, "status": "saved", "voiceProfileId": profile_id,
                "configScope": _public_config_scope(profile_user_id),
                "voiceProfile": build_voice_profile_config_entry(profile_id, voice_profiles[profile_id])}
    ref_audio_submitted = "refAudioPath" in payload or "ref_audio_path" in payload
    prompt_text_submitted = "promptText" in payload or "prompt_text" in payload
    if not ref_audio_submitted and not normalized["refAudioPath"]:
        normalized["refAudioPath"] = str(existing_profile.get("refAudioPath") or "")
    if not prompt_text_submitted and not normalized["promptText"]:
        normalized["promptText"] = str(existing_profile.get("promptText") or "")
    emotion_map_submitted = "emotionVoiceMap" in payload or "emotion_voice_map" in payload
    if emotion_map_submitted:
        merged_emotion_map, emotion_map_error = _merge_submitted_emotion_voice_map(
            existing_profile.get("emotionVoiceMap"),
            normalized["emotionVoiceMap"],
        )
        if emotion_map_error:
            return {
                "ok": False,
                "status": "invalid_config",
                "voiceProfileId": profile_id,
                "reason": emotion_map_error,
            }
        normalized["emotionVoiceMap"] = merged_emotion_map
    elif not normalized["emotionVoiceMap"]:
        normalized["emotionVoiceMap"] = dict(existing_profile.get("emotionVoiceMap") or {})
    optional_voice_fields = {
        "streamingMode": ("streamingMode", "streaming_mode"),
        "parallelInfer": ("parallelInfer", "parallel_infer"),
        "splitBucket": ("splitBucket", "split_bucket"),
        "batchSize": ("batchSize", "batch_size"),
        "topK": ("topK", "top_k"),
        "topP": ("topP", "top_p"),
        "temperature": ("temperature",),
        "speedFactor": ("speedFactor", "speed_factor"),
        "fragmentInterval": ("fragmentInterval", "fragment_interval"),
        "textSplitMethod": ("textSplitMethod", "text_split_method"),
    }
    for canonical_key, aliases in optional_voice_fields.items():
        if not any(alias in payload for alias in aliases) and normalized.get(canonical_key) in (None, ""):
            normalized[canonical_key] = existing_profile.get(canonical_key)
    voice_profiles[profile_id] = {
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
        "updatedAt": _now_iso(),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "providers": config.get("providers", {}),
            "workflows": config.get("workflows", {}),
            "voiceProfiles": voice_profiles,
            "mcpServers": config.get("mcpServers", {}),
        },
    )
    return {
        "ok": True,
        "status": "saved",
        "voiceProfileId": profile_id,
        "configScope": _public_config_scope(profile_user_id),
        "voiceProfile": build_voice_profile_config_entry(profile_id, voice_profiles[profile_id]),
    }


def get_voice_profile_runtime_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    voice_profile_id: str,
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile_id = _safe_voice_profile_id(voice_profile_id)
    if not profile_id:
        return {}
    config = (config_snapshot if config_snapshot is not None else
              load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id))
    profile = config.get("voiceProfiles", {}).get(profile_id)
    if not isinstance(profile, Mapping) or profile.get("enabled") is False:
        return {}
    if profile.get("providerId") == "provider.tts.irodori.local":
        return {"id": profile_id, "providerId": profile["providerId"], "textLang": "ja", "mediaType": "wav",
                "irodoriVoiceId": str(profile.get("irodoriVoiceId") or "")}
    result: dict[str, Any] = {
        "id": profile_id,
        "providerId": str(profile.get("providerId") or "provider.tts.gpt_sovits.local"),
        "textLang": str(profile.get("textLang") or ""),
        "promptLang": str(profile.get("promptLang") or ""),
        "mediaType": str(profile.get("mediaType") or ""),
        "refAudioPath": str(profile.get("refAudioPath") or ""),
        "promptText": str(profile.get("promptText") or ""),
    }
    emotion_voice_map = profile.get("emotionVoiceMap") if isinstance(profile.get("emotionVoiceMap"), Mapping) else {}
    if emotion_voice_map:
        result["emotionVoiceMap"] = dict(emotion_voice_map)
    for key in (
        "streamingMode",
        "parallelInfer",
        "splitBucket",
        "batchSize",
        "topK",
        "topP",
        "temperature",
        "speedFactor",
        "fragmentInterval",
    ):
        value = profile.get(key)
        if value is not None:
            result[key] = value
    text_split_method = str(profile.get("textSplitMethod") or "")
    if text_split_method:
        result["textSplitMethod"] = text_split_method
    return result


def build_voice_profile_config_entry(profile_id: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    safe_id = _safe_voice_profile_id(profile_id)
    provider_id = str(config.get("providerId") or "provider.tts.gpt_sovits.local").strip()
    if provider_id == "provider.tts.irodori.local":
        enabled = bool(config.get("enabled"))
        voice_id = str(config.get("irodoriVoiceId") or "")
        configured = bool(voice_id)
        status = "ready" if enabled and configured else "missing_config" if enabled else "disabled"
        entry = {"id": safe_id, "voiceProfileId": safe_id, "kind": "voice_profile", "type": "tts_voice_profile",
                 "source": "profile_config", "adapter": "irodori", "executionMode": "external",
                 "providerId": provider_id, "name": str(config.get("displayName") or safe_id)[:80],
                 "enabled": enabled, "configured": configured, "status": status,
                 "reason": "" if status == "ready" else "voice_profile_disabled" if not enabled else "irodori_voice_id_missing",
                 "irodoriVoiceId": voice_id, "textLang": "ja", "promptLang": "", "mediaType": "wav",
                 "emotionSampleCount": 0, "emotionSamples": [], "updatedAt": str(config.get("updatedAt") or "")[:80],
                 "risk": "medium", "requiresConfirmation": False, "usedBy": ["voice", "desktop_pet", "qq"]}
        return with_capability_approval_metadata(project_capcore_catalog_fields(entry, default_risk="medium", default_confirm="never"))
    enabled = bool(config.get("enabled"))
    ref_audio_path = str(config.get("refAudioPath") or "").strip()
    prompt_text = str(config.get("promptText") or "").strip()
    configured = bool(ref_audio_path and prompt_text)
    emotion_samples = _public_emotion_voice_samples(config.get("emotionVoiceMap"))
    status = "ready" if enabled and configured else "missing_config" if enabled else "disabled"
    entry = {
        "id": safe_id,
        "voiceProfileId": safe_id,
        "kind": "voice_profile",
        "type": "tts_voice_profile",
        "source": "profile_config",
        "adapter": "gpt_sovits",
        "executionMode": "external",
        "providerId": provider_id,
        "name": str(config.get("displayName") or safe_id or "GPT-SoVITS 声线").strip()[:80],
        "enabled": enabled,
        "configured": configured,
        "status": status,
        "reason": ""
        if status == "ready"
        else ("voice_profile_disabled" if not enabled else "voice_profile_reference_missing"),
        "textLang": str(config.get("textLang") or "zh")[:20],
        "promptLang": str(config.get("promptLang") or "zh")[:20],
        "mediaType": str(config.get("mediaType") or "wav")[:20],
        "streamingMode": bool(config.get("streamingMode")),
        "parallelInfer": config.get("parallelInfer") if isinstance(config.get("parallelInfer"), bool) else None,
        "splitBucket": config.get("splitBucket") if isinstance(config.get("splitBucket"), bool) else None,
        "batchSize": config.get("batchSize") if isinstance(config.get("batchSize"), int) else None,
        "topK": config.get("topK") if isinstance(config.get("topK"), int) else None,
        "topP": config.get("topP") if isinstance(config.get("topP"), (int, float)) else None,
        "temperature": config.get("temperature")
        if isinstance(config.get("temperature"), (int, float))
        else None,
        "speedFactor": config.get("speedFactor") if isinstance(config.get("speedFactor"), (int, float)) else None,
        "fragmentInterval": config.get("fragmentInterval")
        if isinstance(config.get("fragmentInterval"), (int, float))
        else None,
        "textSplitMethod": str(config.get("textSplitMethod") or "")[:40],
        "referenceAudioName": _safe_path_basename(ref_audio_path),
        "promptTextLength": len(prompt_text),
        "emotionSampleCount": len(emotion_samples),
        "emotionSamples": emotion_samples,
        "updatedAt": str(config.get("updatedAt") or "")[:80],
        "risk": "medium",
        "requiresConfirmation": False,
        "usedBy": ["voice", "desktop_pet"],
    }
    projected = project_capcore_catalog_fields(entry, default_risk="medium", default_confirm="never")
    return with_capability_approval_metadata(projected)


def _summarize_voice_profile_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = int(by_status.get(status, 0)) + 1
    return {
        "total": len(entries),
        "configured": sum(1 for entry in entries if entry.get("configured")),
        "enabled": sum(1 for entry in entries if entry.get("enabled")),
        "byStatus": by_status,
    }


def _scan_voice_model_folder_files(folder: Path) -> list[Path]:
    files: list[Path] = []

    def append_file(candidate: Path) -> None:
        if len(files) >= VOICE_MODEL_FOLDER_MAX_SCAN_FILES:
            return
        try:
            if candidate.is_file():
                files.append(candidate)
        except OSError:
            return

    try:
        children = sorted(folder.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []

    for child in children:
        if len(files) >= VOICE_MODEL_FOLDER_MAX_SCAN_FILES:
            break
        if child.name.startswith("."):
            continue
        append_file(child)
        try:
            is_nested_dir = child.is_dir() and not child.is_symlink()
        except OSError:
            is_nested_dir = False
        if not is_nested_dir:
            continue
        try:
            nested_children = sorted(child.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for nested in nested_children:
            if len(files) >= VOICE_MODEL_FOLDER_MAX_SCAN_FILES:
                break
            if not nested.name.startswith("."):
                append_file(nested)
    return files


def _find_voice_model_yaml(folder: Path, files: list[Path]) -> Path | None:
    for filename in VOICE_MODEL_CONFIG_FILENAMES:
        candidate = folder / filename
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    yaml_files = [item for item in files if item.suffix.lower() in {".yaml", ".yml"}]
    for item in yaml_files:
        name = item.name.lower()
        if "tts" in name or "infer" in name or "sovits" in name:
            return item
    return yaml_files[0] if yaml_files else None


def _parse_voice_model_yaml_scalars(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    scalars: dict[str, str] = {}
    for raw_line in text[:65536].splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        raw_key, raw_value = line.split(":", 1)
        key = raw_key.strip().strip("'\"").lower().replace("-", "_")
        if not key or not re.fullmatch(r"[a-z0-9_.]+", key):
            continue
        value = raw_value.strip()
        if not value or value in {"|", ">", "-", "[]", "{}"}:
            continue
        if value.startswith("#"):
            continue
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = value.strip()
        if value:
            scalars[key] = value
    return scalars


def _first_voice_model_yaml_value(values: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        normalized_key = key.strip().lower().replace("-", "_")
        value = str(values.get(normalized_key) or "").strip()
        if value:
            return value
    return ""


def _resolve_voice_model_file_from_yaml(folder: Path, value: Any, allowed_suffixes: set[str]) -> Path | None:
    text = _safe_private_local_path(value)
    if not text:
        return None
    candidate = Path(text).expanduser()
    try:
        candidate = candidate.resolve() if candidate.is_absolute() else (folder / text).resolve()
    except OSError:
        return None
    if candidate.suffix.lower() not in allowed_suffixes:
        return None
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _best_voice_model_file(
    files: list[Path], allowed_suffixes: set[str], preferred_markers: tuple[str, ...]
) -> Path | None:
    candidates = [item for item in files if item.suffix.lower() in allowed_suffixes]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int, int, str]:
        name = path.name.lower()
        marker_index = next(
            (index for index, marker in enumerate(preferred_markers) if marker in name), len(preferred_markers)
        )
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        empty_penalty = 1 if size <= 0 else 0
        return (marker_index, empty_penalty, size, name)

    return sorted(candidates, key=score)[0]
