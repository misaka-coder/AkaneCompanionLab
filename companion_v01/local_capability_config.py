from __future__ import annotations

import json
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlparse, urlunparse

import yaml


CONFIG_SCHEMA_VERSION = 1
PROFILE_CONFIG_PATH_TEMPLATE = "users_data/<profile_user_id>/capabilities/capabilities.yaml"
PROFILE_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
APPROVAL_MODE_TRUSTED_AUTO_ALLOW = "trusted_auto_allow"
APPROVAL_MODE_ASK_EACH_TIME = "ask_each_time"
APPROVAL_MODE_DISABLED = "disabled"
APPROVAL_POLICY_DEFAULT_MODE = APPROVAL_MODE_ASK_EACH_TIME
APPROVAL_POLICY_MODES = {
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
}
APPROVAL_MODES = {
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_DISABLED,
}
APPROVAL_DISABLED_STATUSES = {
    "configured",
    "disabled",
    "error",
    "invalid_config",
    "misconfigured",
    "missing_config",
    "missing_executor",
    "missing_model",
    "missing_slot_mapping",
    "missing_workflow",
    "unavailable",
    "unreachable",
    "unsupported_platform",
}
PUBLIC_PROVIDER_FIELDS = {"enabled", "endpoint", "updatedAt", "lastHealth"}
PUBLIC_WORKFLOW_FIELDS = {"enabled", "workflowPath", "slotMapping", "updatedAt"}
PRIVATE_VOICE_PROFILE_FIELDS = {
    "providerId",
    "enabled",
    "displayName",
    "textLang",
    "promptLang",
    "mediaType",
    "refAudioPath",
    "promptText",
    "emotionVoiceMap",
    "streamingMode",
    "parallelInfer",
    "splitBucket",
    "batchSize",
    "speedFactor",
    "fragmentInterval",
    "textSplitMethod",
    "updatedAt",
}
PRIVATE_MCP_SERVER_FIELDS = {
    "enabled",
    "displayName",
    "transport",
    "command",
    "args",
    "cwd",
    "env",
    "tools",
    "lowRiskAllowlist",
    "lastDiscovery",
    "updatedAt",
}
PUBLIC_APPROVAL_POLICY_FIELDS = {"defaultMode", "updatedAt"}
WORKFLOW_PATH_MAX_LENGTH = 220
WORKFLOW_SLOT_MAX_LENGTH = 80
VOICE_PROFILE_TEXT_MAX_LENGTH = 300
VOICE_PROFILE_PATH_MAX_LENGTH = 500
VOICE_MODEL_FOLDER_MAX_LENGTH = 500
VOICE_MODEL_FOLDER_MAX_SCAN_FILES = 240
VOICE_MODEL_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}
VOICE_MODEL_CONFIG_FILENAMES = ("tts_infer.yaml", "tts_infer.yml")
VOICE_MODEL_GPT_EXTENSIONS = {".ckpt"}
VOICE_MODEL_SOVITS_EXTENSIONS = {".pth"}
MCP_SERVER_ID_MAX_LENGTH = 80
MCP_SERVER_TEXT_MAX_LENGTH = 240
MCP_SERVER_PATH_MAX_LENGTH = 500
MCP_SERVER_ARG_MAX_LENGTH = 240
MCP_SERVER_ARG_MAX_COUNT = 24
MCP_SERVER_ENV_MAX_COUNT = 12
MCP_TOOL_NAME_MAX_LENGTH = 80
MCP_TOOL_DESCRIPTION_MAX_LENGTH = 240
MCP_TOOL_MAX_COUNT = 64
MCP_SCHEMA_PROPERTY_MAX_COUNT = 24
MCP_TOOL_CONFIRM_POLICIES = {"never", "first_time", "always"}
MCP_ENV_PLACEHOLDER_RE = re.compile(r"\$\{[A-Z_][A-Z0-9_]{0,79}\}")
MCP_SECRET_MARKERS = ("api_key", "password", "secret", "token")
WORKFLOW_SLOT_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
WORKFLOW_COMFYUI_SLOT_PATH_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}\.inputs\.[A-Za-z0-9_.-]{1,120}$")
WORKFLOW_ASSET_HANDLE_MAX_LENGTH = 120
WORKFLOW_ASSET_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
MCP_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
MCP_SAFE_TYPE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
WORKFLOW_CONFIG_FILE_MAX_BYTES = 4 * 1024 * 1024


HealthChecker = Callable[[str, int, float], tuple[bool, str]]


def capability_approval_mode(
    *,
    enabled: bool = True,
    status: str = "",
    risk: str = "",
    requires_confirmation: bool = False,
) -> str:
    normalized_status = str(status or "").strip().lower()
    normalized_risk = str(risk or "").strip().lower()
    if not enabled or normalized_status in APPROVAL_DISABLED_STATUSES:
        return APPROVAL_MODE_DISABLED
    if requires_confirmation or normalized_risk == "high":
        return APPROVAL_MODE_ASK_EACH_TIME
    return APPROVAL_MODE_TRUSTED_AUTO_ALLOW


def with_capability_approval_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    public_entry = dict(entry)
    raw_mode = str(public_entry.get("approvalMode") or "").strip()
    mode = (
        raw_mode
        if raw_mode in APPROVAL_MODES
        else capability_approval_mode(
            enabled=public_entry.get("enabled") is not False,
            status=str(public_entry.get("status") or ""),
            risk=str(public_entry.get("risk") or ""),
            requires_confirmation=bool(public_entry.get("requiresConfirmation")),
        )
    )
    public_entry["approvalMode"] = mode
    if not str(public_entry.get("approvalReason") or "").strip():
        public_entry["approvalReason"] = _approval_reason(public_entry, mode)
    return public_entry


def apply_approval_policy_to_entry(entry: Mapping[str, Any], approval_policy: Mapping[str, Any] | None) -> dict[str, Any]:
    public_entry = with_capability_approval_metadata(entry)
    policy = normalize_approval_policy_config(approval_policy)
    if policy["defaultMode"] != APPROVAL_MODE_TRUSTED_AUTO_ALLOW:
        return public_entry
    if public_entry.get("approvalMode") == APPROVAL_MODE_DISABLED:
        return public_entry
    if str(public_entry.get("risk") or "").strip().lower() != "high" and not public_entry.get("requiresConfirmation"):
        return public_entry
    return {
        **public_entry,
        "approvalMode": APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
        "approvalReason": "user_policy_trusted_auto_allow",
        "requiresConfirmation": False,
    }


def normalize_approval_policy_config(raw_policy: Any) -> dict[str, Any]:
    raw = raw_policy if isinstance(raw_policy, Mapping) else {}
    default_mode = str(raw.get("defaultMode") or raw.get("default_mode") or APPROVAL_POLICY_DEFAULT_MODE).strip()
    if default_mode not in APPROVAL_POLICY_MODES:
        default_mode = APPROVAL_POLICY_DEFAULT_MODE
    updated_at = _safe_short_text(raw.get("updatedAt") or raw.get("updated_at"))
    return {
        "defaultMode": default_mode,
        "updatedAt": updated_at,
    }


def build_approval_policy_entry(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_approval_policy_config(policy)
    default_mode = normalized["defaultMode"]
    return {
        "defaultMode": default_mode,
        "label": "完全访问" if default_mode == APPROVAL_MODE_TRUSTED_AUTO_ALLOW else "请求批准",
        "summary": (
            "高风险能力在执行前自动允许；仍保留 URL、路径、密钥和本地边界校验。"
            if default_mode == APPROVAL_MODE_TRUSTED_AUTO_ALLOW
            else "高风险能力在执行前创建审批请求，由用户允许或拒绝。"
        ),
        "requiresConfirmationByDefault": default_mode == APPROVAL_MODE_ASK_EACH_TIME,
        "trustedAutoAllowHighRisk": default_mode == APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
        "availableModes": [
            {
                "id": APPROVAL_MODE_ASK_EACH_TIME,
                "label": "请求批准",
                "summary": "高风险动作先进入审批队列。",
            },
            {
                "id": APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
                "label": "完全访问",
                "summary": "跳过高风险动作的逐次确认，但不跳过硬安全校验。",
            },
        ],
        "updatedAt": normalized["updatedAt"],
    }


def _approval_reason(entry: Mapping[str, Any], mode: str) -> str:
    if mode == APPROVAL_MODE_DISABLED:
        if entry.get("enabled") is False:
            return "capability_disabled"
        status = re.sub(r"[^a-z0-9_.-]+", "_", str(entry.get("status") or "").strip().lower())[:80]
        return f"status_{status}" if status else "capability_not_ready"
    if mode == APPROVAL_MODE_ASK_EACH_TIME:
        return "requires_confirmation"
    return "trusted_runtime_boundary"


@dataclass(frozen=True)
class ProviderConfigSpec:
    id: str
    name: str
    type: str
    adapter: str
    default_endpoint: str
    used_by: tuple[str, ...]
    risk: str = "medium"


@dataclass(frozen=True)
class WorkflowConfigSpec:
    id: str
    capability_id: str
    workflow_id: str
    name: str
    description: str
    type: str
    adapter: str
    provider_id: str
    group: str
    used_by: tuple[str, ...]
    risk: str
    target: str
    output: str
    default_workflow_path: str
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]


CONFIGURABLE_PROVIDER_SPECS: tuple[ProviderConfigSpec, ...] = (
    ProviderConfigSpec(
        id="provider.comfyui.local",
        name="本地 ComfyUI",
        type="asset_processor",
        adapter="comfyui",
        default_endpoint="http://127.0.0.1:8188",
        used_by=("workshop", "image", "desktop_pet"),
    ),
    ProviderConfigSpec(
        id="provider.tts.gpt_sovits.local",
        name="本地 GPT-SoVITS",
        type="tts_provider",
        adapter="gpt_sovits",
        default_endpoint="http://127.0.0.1:9880",
        used_by=("voice", "desktop_pet"),
    ),
    ProviderConfigSpec(
        id="provider.asr.openai_compat.local",
        name="本地 OpenAI 兼容 ASR",
        type="asr_provider",
        adapter="openai_compat_asr",
        default_endpoint="http://127.0.0.1:8000",
        used_by=("voice", "desktop_pet"),
    ),
)

CONFIGURABLE_PROVIDER_BY_ID = {spec.id: spec for spec in CONFIGURABLE_PROVIDER_SPECS}

CONFIGURABLE_WORKFLOW_SPECS: tuple[WorkflowConfigSpec, ...] = (
    WorkflowConfigSpec(
        id="workflow.workshop.portrait.cutout",
        capability_id="workshop.portrait.cutout",
        workflow_id="workflow.comfyui.portrait_cutout",
        name="透明背景处理",
        description="角色工坊的立绘透明背景处理流程，绑定本地 ComfyUI 工作流后可用于角色素材整理。",
        type="asset_processor",
        adapter="comfyui",
        provider_id="provider.comfyui.local",
        group="workshop",
        used_by=("workshop", "desktop_pet"),
        risk="medium",
        target="character_pack_assets",
        output="transparent_png",
        default_workflow_path="workflows/comfyui/portrait_cutout.json",
        required_slots=("input_image_handle", "output_image_handle"),
        optional_slots=("mask_output_handle", "background_color", "padding", "alpha_threshold"),
    ),
)

CONFIGURABLE_WORKFLOW_BY_ID = {spec.id: spec for spec in CONFIGURABLE_WORKFLOW_SPECS}


def list_provider_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    providers = [
        build_provider_config_entry(spec, config.get("providers", {}).get(spec.id))
        for spec in CONFIGURABLE_PROVIDER_SPECS
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
        "providers": providers,
        "summary": _summarize_provider_entries(providers),
    }


def get_provider_config_entries(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> list[dict[str, Any]]:
    return list_provider_configs(base_dir=base_dir, profile_user_id=profile_user_id)["providers"]


def get_approval_policy_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "configStatus": config.get("configStatus") or "available",
        "warnings": list(config.get("warnings") or []),
        "configScope": _public_config_scope(profile_user_id),
        "approvalPolicy": build_approval_policy_entry(config.get("approvalPolicy")),
    }


def save_approval_policy_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = normalize_approval_policy_config(payload)
    if str(payload.get("defaultMode") or payload.get("default_mode") or "").strip() not in APPROVAL_POLICY_MODES:
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": "approval_policy_mode_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }
    approval_policy = {
        "defaultMode": normalized["defaultMode"],
        "updatedAt": _now_iso(),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "approvalPolicy": approval_policy,
            "providers": config.get("providers", {}),
            "workflows": config.get("workflows", {}),
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": config.get("mcpServers", {}),
        },
    )
    return {
        "ok": True,
        "status": "saved",
        "configScope": _public_config_scope(profile_user_id),
        "approvalPolicy": build_approval_policy_entry(approval_policy),
        "refresh": True,
    }


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
        _first_voice_model_yaml_value(yaml_values, "voice_profile_id", "profile_id", "id")
        or folder_name
    )
    display_name = _safe_short_text(
        _first_voice_model_yaml_value(yaml_values, "display_name", "voice_name", "name")
        or folder_name,
        limit=80,
    ) or profile_id or "GPT-SoVITS Voice"

    yaml_ref_audio = _resolve_voice_model_file_from_yaml(
        folder,
        _first_voice_model_yaml_value(yaml_values, "ref_audio_path", "reference_audio_path", "prompt_audio_path"),
        VOICE_MODEL_AUDIO_EXTENSIONS,
    )
    ref_audio_path = yaml_ref_audio or _best_voice_model_file(files, VOICE_MODEL_AUDIO_EXTENSIONS, ("ref", "prompt", "sample", "output"))
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
    ref_audio_submitted = "refAudioPath" in payload or "ref_audio_path" in payload
    prompt_text_submitted = "promptText" in payload or "prompt_text" in payload
    if not ref_audio_submitted and not normalized["refAudioPath"]:
        normalized["refAudioPath"] = str(existing_profile.get("refAudioPath") or "")
    if not prompt_text_submitted and not normalized["promptText"]:
        normalized["promptText"] = str(existing_profile.get("promptText") or "")
    emotion_map_submitted = "emotionVoiceMap" in payload or "emotion_voice_map" in payload
    if not emotion_map_submitted and not normalized["emotionVoiceMap"]:
        normalized["emotionVoiceMap"] = dict(existing_profile.get("emotionVoiceMap") or {})
    optional_voice_fields = {
        "streamingMode": ("streamingMode", "streaming_mode"),
        "parallelInfer": ("parallelInfer", "parallel_infer"),
        "splitBucket": ("splitBucket", "split_bucket"),
        "batchSize": ("batchSize", "batch_size"),
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
) -> dict[str, Any]:
    profile_id = _safe_voice_profile_id(voice_profile_id)
    if not profile_id:
        return {}
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    profile = config.get("voiceProfiles", {}).get(profile_id)
    if not isinstance(profile, Mapping) or profile.get("enabled") is False:
        return {}
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
    for key in ("streamingMode", "parallelInfer", "splitBucket", "batchSize", "speedFactor", "fragmentInterval"):
        value = profile.get(key)
        if value is not None:
            result[key] = value
    text_split_method = str(profile.get("textSplitMethod") or "")
    if text_split_method:
        result["textSplitMethod"] = text_split_method
    return result


def list_mcp_server_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    servers = [
        build_mcp_server_config_entry(server_id, server_config)
        for server_id, server_config in sorted((config.get("mcpServers") or {}).items())
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
        "mcpServers": servers,
        "summary": _summarize_mcp_server_entries(servers),
    }


def save_mcp_server_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {"ok": False, "status": "invalid_mcp_server", "reason": "mcp_server_id_invalid"}
    normalized = normalize_mcp_server_config_payload(safe_server_id, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "serverId": safe_server_id,
            "reason": normalized.get("reason") or "invalid_mcp_server_config",
        }

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "serverId": safe_server_id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }

    servers = dict(config.get("mcpServers") or {})
    existing = servers.get(safe_server_id) if isinstance(servers.get(safe_server_id), Mapping) else {}
    next_server = {
        "enabled": bool(normalized["enabled"]),
        "displayName": normalized["displayName"],
        "transport": normalized["transport"],
        "command": normalized["command"],
        "args": normalized["args"],
        "cwd": normalized["cwd"],
        "env": normalized["env"],
        "updatedAt": _now_iso(),
    }
    if existing.get("command") == normalized["command"] and isinstance(existing.get("tools"), list):
        next_server["tools"] = list(existing.get("tools") or [])
    if existing.get("command") == normalized["command"] and isinstance(existing.get("lastDiscovery"), Mapping):
        next_server["lastDiscovery"] = dict(existing.get("lastDiscovery") or {})
    servers[safe_server_id] = next_server
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "providers": config.get("providers", {}),
            "workflows": config.get("workflows", {}),
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": servers,
        },
    )
    return {
        "ok": True,
        "status": "saved",
        "serverId": safe_server_id,
        "autoEnable": False,
        "configScope": _public_config_scope(profile_user_id),
        "mcpServer": build_mcp_server_config_entry(safe_server_id, next_server),
    }


def get_mcp_server_runtime_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {}
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    server = config.get("mcpServers", {}).get(safe_server_id)
    if not isinstance(server, Mapping):
        return {}
    return {
        "serverId": safe_server_id,
        "enabled": bool(server.get("enabled")),
        "displayName": str(server.get("displayName") or safe_server_id),
        "transport": str(server.get("transport") or "stdio"),
        "command": str(server.get("command") or ""),
        "args": list(server.get("args") or []) if isinstance(server.get("args"), list) else [],
        "cwd": str(server.get("cwd") or ""),
        "env": dict(server.get("env") or {}) if isinstance(server.get("env"), Mapping) else {},
        "tools": list(server.get("tools") or []) if isinstance(server.get("tools"), list) else [],
        "lowRiskAllowlist": list(server.get("lowRiskAllowlist") or []) if isinstance(server.get("lowRiskAllowlist"), list) else [],
    }


def save_mcp_server_discovery(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {"ok": False, "status": "invalid_mcp_server", "reason": "mcp_server_id_invalid"}
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    server = config.get("mcpServers", {}).get(safe_server_id)
    if not isinstance(server, Mapping):
        return {
            "ok": False,
            "status": "missing_config",
            "serverId": safe_server_id,
            "reason": "mcp_server_config_missing",
        }
    if server.get("enabled") is False:
        return {
            "ok": False,
            "status": "disabled",
            "serverId": safe_server_id,
            "reason": "mcp_server_disabled",
        }
    normalized = normalize_mcp_tool_discovery_payload(safe_server_id, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "serverId": safe_server_id,
            "reason": normalized.get("reason") or "invalid_mcp_discovery_payload",
        }

    last_discovery = {
        "status": "ready",
        "discoveredAt": _now_iso(),
        "toolCount": len(normalized["tools"]),
    }
    servers = dict(config.get("mcpServers") or {})
    servers[safe_server_id] = {
        **server,
        "tools": normalized["tools"],
        "lastDiscovery": last_discovery,
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "providers": config.get("providers", {}),
            "workflows": config.get("workflows", {}),
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": servers,
        },
    )
    return {
        "ok": True,
        "status": "discovered",
        "serverId": safe_server_id,
        "toolCount": len(normalized["tools"]),
        "mcpServer": build_mcp_server_config_entry(safe_server_id, servers[safe_server_id]),
        "tools": [
            build_mcp_tool_config_entry(safe_server_id, tool)
            for tool in normalized["tools"]
        ],
        "refresh": True,
    }


def list_workflow_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    workflows = [
        build_workflow_config_entry(
            spec,
            config.get("workflows", {}).get(spec.id),
            providers_by_id.get(spec.provider_id),
        )
        for spec in CONFIGURABLE_WORKFLOW_SPECS
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
        "workflows": workflows,
        "summary": _summarize_workflow_entries(workflows),
    }


def save_provider_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    provider_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_PROVIDER_BY_ID.get(str(provider_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_provider", "providerId": str(provider_id or "").strip()}

    normalized = normalize_provider_config_payload(spec, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "providerId": spec.id,
            "reason": normalized.get("reason") or "invalid_provider_config",
        }

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": spec.id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }

    providers = dict(config.get("providers") or {})
    existing = providers.get(spec.id) if isinstance(providers.get(spec.id), dict) else {}
    next_provider = {
        "enabled": bool(normalized["enabled"]),
        "endpoint": normalized["endpoint"],
        "updatedAt": _now_iso(),
    }
    if (
        existing.get("endpoint") == normalized["endpoint"]
        and isinstance(existing.get("lastHealth"), Mapping)
    ):
        next_provider["lastHealth"] = existing["lastHealth"]
    providers[spec.id] = next_provider
    config = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "providers": providers,
        "workflows": config.get("workflows", {}),
        "voiceProfiles": config.get("voiceProfiles", {}),
        "mcpServers": config.get("mcpServers", {}),
    }
    write_capability_config(base_dir=base_dir, profile_user_id=profile_user_id, config=config)
    return {
        "ok": True,
        "status": "saved",
        "providerId": spec.id,
        "autoEnable": False,
        "configScope": _public_config_scope(profile_user_id),
        "provider": build_provider_config_entry(spec, next_provider),
    }


def check_provider_health(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    provider_id: str,
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: float = 0.35,
    health_checker: HealthChecker | None = None,
) -> dict[str, Any]:
    spec = CONFIGURABLE_PROVIDER_BY_ID.get(str(provider_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_provider", "providerId": str(provider_id or "").strip()}

    payload = payload or {}
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": spec.id,
            "autoEnable": False,
            "enabled": False,
            "reason": config.get("reason") or "provider_config_file_invalid",
        }

    saved = config.get("providers", {}).get(spec.id)
    endpoint_value = payload.get("endpoint") if "endpoint" in payload else None
    endpoint = str(endpoint_value or (saved or {}).get("endpoint") or "").strip()
    if not endpoint:
        return {
            "ok": False,
            "status": "missing_config",
            "providerId": spec.id,
            "autoEnable": False,
            "enabled": bool((saved or {}).get("enabled")),
            "reason": "provider_endpoint_missing",
        }

    normalized_endpoint = normalize_local_http_endpoint(endpoint)
    if not normalized_endpoint["ok"]:
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": spec.id,
            "autoEnable": False,
            "enabled": bool((saved or {}).get("enabled")),
            "reason": normalized_endpoint.get("reason") or "invalid_endpoint",
        }

    parsed = urlparse(normalized_endpoint["endpoint"])
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    checker = health_checker or _socket_health_check
    try:
        ready, reason = checker(parsed.hostname or "", port, max(0.05, float(timeout_seconds)))
    except Exception as exc:
        ready = False
        reason = _safe_reason(str(exc) or "health_check_failed")

    status = "ready" if ready else "unreachable"
    last_health = {
        "status": status,
        "checkedAt": _now_iso(),
        "endpoint": normalized_endpoint["endpoint"],
        "reason": "" if ready else _safe_reason(reason or "connection_failed"),
    }
    if saved and endpoint_value is None:
        providers = dict(config.get("providers") or {})
        providers[spec.id] = {**saved, "lastHealth": last_health}
        write_capability_config(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            config={
                "schemaVersion": CONFIG_SCHEMA_VERSION,
                "providers": providers,
                "workflows": config.get("workflows", {}),
                "voiceProfiles": config.get("voiceProfiles", {}),
                "mcpServers": config.get("mcpServers", {}),
            },
        )

    return {
        "ok": ready,
        "status": status,
        "providerId": spec.id,
        "autoEnable": False,
        "enabled": bool((saved or {}).get("enabled")),
        "endpoint": normalized_endpoint["endpoint"],
        "reason": "" if ready else last_health["reason"],
    }


def save_workflow_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    normalized = normalize_workflow_config_payload(spec, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "workflowId": spec.id,
            "reason": normalized.get("reason") or "invalid_workflow_config",
        }

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "workflowId": spec.id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }

    workflows = dict(config.get("workflows") or {})
    workflows[spec.id] = {
        "enabled": bool(normalized["enabled"]),
        "workflowPath": normalized["workflowPath"],
        "slotMapping": normalized["slotMapping"],
        "updatedAt": _now_iso(),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "providers": config.get("providers", {}),
            "workflows": workflows,
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": config.get("mcpServers", {}),
        },
    )
    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    workflow = build_workflow_config_entry(spec, workflows[spec.id], providers_by_id.get(spec.provider_id))
    return {
        "ok": True,
        "status": "saved",
        "workflowId": spec.id,
        "executionReady": False,
        "autoEnable": False,
        "configScope": _public_config_scope(profile_user_id),
        "workflow": workflow,
    }


def save_workflow_file(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    workflow_path_value = payload.get("workflowPath")
    if workflow_path_value is None:
        workflow_path_value = payload.get("workflowRef")
    if workflow_path_value is None:
        workflow_path_value = spec.default_workflow_path
    normalized_path = normalize_workflow_path(str(workflow_path_value or "").strip())
    if not normalized_path["ok"]:
        return {
            "ok": False,
            "status": normalized_path["status"],
            "workflowId": spec.id,
            "reason": normalized_path.get("reason") or "invalid_workflow_config",
        }

    workflow_text = payload.get("workflowJson")
    if workflow_text is None:
        workflow_text = payload.get("workflowText")
    workflow_text = str(workflow_text or "")
    if not workflow_text.strip():
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_file_required",
        }
    if len(workflow_text.encode("utf-8")) > WORKFLOW_CONFIG_FILE_MAX_BYTES:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_file_too_large",
        }

    try:
        workflow_json = json.loads(workflow_text)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_file_invalid_json",
        }
    if not isinstance(workflow_json, Mapping) or not workflow_json:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_json_invalid",
        }

    target_path = resolve_workflow_config_file_path(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_path=str(normalized_path["workflowPath"]),
    )
    if target_path is None:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_path_must_be_safe_relative_json",
        }

    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(workflow_json, ensure_ascii=False, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target_path.parent), delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(payload_text)
        handle.write("\n")
    tmp_path.replace(target_path)

    return {
        "ok": True,
        "status": "workflow_file_saved",
        "workflowId": spec.id,
        "workflowPath": str(normalized_path["workflowPath"]),
        "configScope": _public_config_scope(profile_user_id),
        "executionReady": False,
        "autoEnable": False,
    }


def validate_workflow_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "workflowId": spec.id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "executionReady": False,
        }

    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    workflow = build_workflow_config_entry(
        spec,
        config.get("workflows", {}).get(spec.id),
        providers_by_id.get(spec.provider_id),
    )
    checks = {
        "providerConfigured": bool(providers_by_id.get(spec.provider_id, {}).get("configured")),
        "workflowConfigured": bool(workflow.get("configured")),
        "requiredSlots": _required_slots_present(spec, workflow.get("slotMapping")),
        "workflowFile": False,
        "slotPaths": False,
        "executionReady": False,
    }
    if not checks["workflowConfigured"]:
        return {
            "ok": False,
            "status": workflow.get("status") or "missing_workflow",
            "workflowId": spec.id,
            "reason": workflow.get("reason") or "workflow_binding_missing",
            "executionReady": False,
            "checks": checks,
            "workflow": workflow,
        }
    runtime = validate_workflow_runtime_binding(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_id=spec.id,
    )
    runtime_checks = runtime.get("checks") if isinstance(runtime.get("checks"), Mapping) else {}
    checks["workflowFile"] = bool(runtime_checks.get("workflowFile"))
    checks["slotPaths"] = bool(runtime_checks.get("slotPaths"))
    if not runtime.get("ok"):
        return {
            "ok": False,
            "status": runtime.get("status") or "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
            "executionReady": False,
            "checks": checks,
            "workflow": {
                **workflow,
                "status": runtime.get("status") or "invalid_workflow_config",
                "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
                "executionReady": False,
            },
        }
    return {
        "ok": True,
        "status": "validated_config",
        "workflowId": spec.id,
        "reason": "workflow_runtime_not_bound",
        "executionReady": False,
        "checks": checks,
        "workflow": workflow,
    }


def preflight_workflow_execution(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "executionReady": False,
            "canRun": False,
        }

    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    provider = providers_by_id.get(spec.provider_id, {})
    workflow = build_workflow_config_entry(
        spec,
        config.get("workflows", {}).get(spec.id),
        provider,
    )
    checks = {
        "providerConfigured": bool(provider.get("configured")),
        "providerEnabled": bool(provider.get("enabled")),
        "providerAvailable": str(provider.get("status") or "") in {"configured", "ready"},
        "workflowConfigured": bool(workflow.get("configured")),
        "workflowEnabled": bool(workflow.get("enabled")),
        "requiredSlots": _required_slots_present(spec, workflow.get("slotMapping")),
        "workflowFile": False,
        "slotPaths": False,
        "inputImageHandle": False,
        "outputImageHandle": False,
        "runnerBound": False,
    }
    if not (checks["providerConfigured"] and checks["workflowConfigured"] and checks["workflowEnabled"]):
        return {
            "ok": False,
            "status": workflow.get("status") or "missing_workflow",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": workflow.get("reason") or "workflow_binding_missing",
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": workflow,
        }
    if not checks["providerAvailable"]:
        return {
            "ok": False,
            "status": workflow.get("status") or provider.get("status") or "unavailable",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": workflow.get("reason") or provider.get("reason") or "provider_unavailable",
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": workflow,
        }

    input_handle = normalize_workflow_asset_handle(
        payload.get("inputImageHandle") or payload.get("inputAssetHandle") or payload.get("sourceAssetHandle")
    )
    output_handle = normalize_workflow_asset_handle(
        payload.get("outputImageHandle") or payload.get("outputAssetHandle") or payload.get("targetAssetHandle")
    )
    checks["inputImageHandle"] = bool(input_handle.get("ok"))
    checks["outputImageHandle"] = bool(output_handle.get("ok"))
    if not input_handle.get("ok") or not output_handle.get("ok"):
        return {
            "ok": False,
            "status": "invalid_request",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": input_handle.get("reason") if not input_handle.get("ok") else output_handle.get("reason"),
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": workflow,
        }

    runtime = validate_workflow_runtime_binding(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_id=spec.id,
    )
    runtime_checks = runtime.get("checks") if isinstance(runtime.get("checks"), Mapping) else {}
    checks["workflowFile"] = bool(runtime_checks.get("workflowFile"))
    checks["slotPaths"] = bool(runtime_checks.get("slotPaths"))
    if not runtime.get("ok"):
        return {
            "ok": False,
            "status": runtime.get("status") or "invalid_workflow_config",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": {
                **workflow,
                "status": runtime.get("status") or "invalid_workflow_config",
                "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
                "executionReady": False,
            },
        }

    return {
        "ok": False,
        "status": "not-implemented",
        "workflowId": spec.id,
        "capabilityId": spec.capability_id,
        "reason": "workflow_runner_not_bound",
        "executionReady": False,
        "canRun": False,
        "checks": checks,
        "acceptedInputs": {
            "inputImageHandle": input_handle["handle"],
            "outputImageHandle": output_handle["handle"],
        },
        "workflow": workflow,
    }


def build_provider_config_entry(spec: ProviderConfigSpec, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    config_status = str(config.get("status") or "").strip()
    endpoint = _safe_endpoint_for_output(config.get("endpoint"))
    enabled = bool(config.get("enabled")) if config_status != "invalid_config" else False
    last_health = config.get("lastHealth") if isinstance(config.get("lastHealth"), Mapping) else {}
    configured = bool(endpoint)
    status = "invalid_config" if config_status == "invalid_config" else _provider_status(
        configured=configured,
        enabled=enabled,
        last_health=last_health,
    )
    return with_capability_approval_metadata({
        "id": spec.id,
        "kind": "provider",
        "type": spec.type,
        "source": "external_executor",
        "adapter": spec.adapter,
        "executionMode": "external",
        "name": spec.name,
        "enabled": enabled,
        "configured": configured,
        "status": status,
        "reason": _provider_reason(status, last_health, config),
        "risk": spec.risk,
        "requiresConfirmation": False,
        "usedBy": list(spec.used_by),
        "endpoint": endpoint,
        "defaultEndpoint": spec.default_endpoint,
        "autoEnabled": False,
        "configurable": True,
    })


def build_workflow_config_entry(
    spec: WorkflowConfigSpec,
    config: Mapping[str, Any] | None,
    provider_entry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    config_status = str(config.get("status") or "").strip()
    workflow_path = _safe_workflow_path_for_output(config.get("workflowPath"))
    slot_mapping = _safe_slot_mapping_for_output(spec, config.get("slotMapping"))
    enabled = bool(config.get("enabled")) if config_status != "invalid_config" else False
    configured = bool(workflow_path and _required_slots_present(spec, slot_mapping))
    status = _workflow_status(
        spec=spec,
        config=config,
        config_status=config_status,
        provider_entry=provider_entry,
        workflow_path=workflow_path,
        slot_mapping=slot_mapping,
        enabled=enabled,
        configured=configured,
    )
    return with_capability_approval_metadata({
        "id": spec.id,
        "kind": "workflow",
        "type": spec.type,
        "source": "external_executor",
        "adapter": spec.adapter,
        "executionMode": "external",
        "capabilityId": spec.capability_id,
        "workflowId": spec.workflow_id,
        "providerId": spec.provider_id,
        "name": spec.name,
        "description": spec.description,
        "group": spec.group,
        "enabled": enabled,
        "configured": configured,
        "configurable": True,
        "executionReady": False,
        "status": status,
        "reason": _workflow_reason(status, config, provider_entry),
        "risk": spec.risk,
        "requiresConfirmation": False,
        "usedBy": list(spec.used_by),
        "target": spec.target,
        "output": spec.output,
        "workflowPath": workflow_path,
        "defaultWorkflowPath": spec.default_workflow_path,
        "slotMapping": slot_mapping,
        "slots": {
            "required": list(spec.required_slots),
            "optional": list(spec.optional_slots),
        },
        "inputSchema": {
            "inputImage": "asset_handle",
            "outputImage": "asset_handle",
            "pathPolicy": "safe-handle-only",
        },
    })


def build_voice_profile_config_entry(profile_id: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    safe_id = _safe_voice_profile_id(profile_id)
    provider_id = str(config.get("providerId") or "provider.tts.gpt_sovits.local").strip()
    enabled = bool(config.get("enabled"))
    ref_audio_path = str(config.get("refAudioPath") or "").strip()
    prompt_text = str(config.get("promptText") or "").strip()
    emotion_voice_map = config.get("emotionVoiceMap") if isinstance(config.get("emotionVoiceMap"), Mapping) else {}
    emotion_voice_ids = sorted(
        emotion_id
        for emotion_id in (_safe_voice_profile_id(key) for key in emotion_voice_map.keys())
        if emotion_id
    )
    configured = bool(ref_audio_path and prompt_text)
    status = "ready" if enabled and configured else "missing_config" if enabled else "disabled"
    return with_capability_approval_metadata({
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
        "reason": "" if status == "ready" else ("voice_profile_disabled" if not enabled else "voice_profile_reference_missing"),
        "textLang": str(config.get("textLang") or "zh")[:20],
        "promptLang": str(config.get("promptLang") or "zh")[:20],
        "mediaType": str(config.get("mediaType") or "wav")[:20],
        "streamingMode": bool(config.get("streamingMode")),
        "parallelInfer": config.get("parallelInfer") if isinstance(config.get("parallelInfer"), bool) else None,
        "splitBucket": config.get("splitBucket") if isinstance(config.get("splitBucket"), bool) else None,
        "batchSize": config.get("batchSize") if isinstance(config.get("batchSize"), int) else None,
        "speedFactor": config.get("speedFactor") if isinstance(config.get("speedFactor"), (int, float)) else None,
        "fragmentInterval": config.get("fragmentInterval") if isinstance(config.get("fragmentInterval"), (int, float)) else None,
        "textSplitMethod": str(config.get("textSplitMethod") or "")[:40],
        "hasReferenceAudio": bool(ref_audio_path),
        "referenceAudioName": _safe_path_basename(ref_audio_path),
        "promptTextLength": len(prompt_text),
        "emotionVoiceIds": emotion_voice_ids,
        "emotionVoiceCount": len(emotion_voice_map),
        "updatedAt": str(config.get("updatedAt") or "")[:80],
        "risk": "medium",
        "requiresConfirmation": False,
        "usedBy": ["voice", "desktop_pet"],
    })


def build_mcp_server_config_entry(server_id: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    safe_id = _safe_mcp_server_id(server_id)
    enabled = bool(config.get("enabled"))
    command = str(config.get("command") or "").strip()
    transport = str(config.get("transport") or "stdio").strip() or "stdio"
    tools = config.get("tools") if isinstance(config.get("tools"), list) else []
    last_discovery = config.get("lastDiscovery") if isinstance(config.get("lastDiscovery"), Mapping) else {}
    configured = bool(command and transport == "stdio")
    discovered = bool(last_discovery.get("status") == "ready")
    status = (
        "ready"
        if enabled and configured and discovered
        else "configured"
        if enabled and configured
        else "missing_config"
        if enabled
        else "disabled"
    )
    reason = ""
    if status == "missing_config":
        reason = "mcp_server_command_missing"
    elif status == "configured":
        reason = "mcp_tools_not_discovered"
    elif status == "disabled":
        reason = "mcp_server_disabled"
    return with_capability_approval_metadata({
        "id": f"provider.mcp.{safe_id}",
        "serverId": safe_id,
        "kind": "provider",
        "type": "mcp_provider",
        "source": "mcp",
        "adapter": "mcp_stdio",
        "executionMode": "external",
        "name": str(config.get("displayName") or safe_id or "MCP Server").strip()[:80],
        "enabled": enabled,
        "configured": configured,
        "status": status,
        "reason": reason,
        "transport": transport,
        "commandName": _safe_path_basename(command),
        "argsCount": len(config.get("args") or []) if isinstance(config.get("args"), list) else 0,
        "envCount": len(config.get("env") or {}) if isinstance(config.get("env"), Mapping) else 0,
        "toolCount": len(tools),
        "lastDiscovery": {
            "status": str(last_discovery.get("status") or "")[:80],
            "discoveredAt": str(last_discovery.get("discoveredAt") or "")[:80],
            "toolCount": int(last_discovery.get("toolCount") or 0),
        } if last_discovery else {},
        "risk": "medium",
        "requiresConfirmation": False,
        "usedBy": ["agent_prompt", "external_tools"],
        "configurable": True,
    })


def build_mcp_tool_config_entry(server_id: str, tool: Mapping[str, Any] | None) -> dict[str, Any]:
    tool = tool if isinstance(tool, Mapping) else {}
    safe_server_id = _safe_mcp_server_id(server_id)
    tool_name = _safe_mcp_tool_name(tool.get("name"))
    public_id = f"mcp.{safe_server_id}.{tool_name}" if safe_server_id and tool_name else ""
    risk = str(tool.get("risk") or _infer_mcp_tool_risk(tool_name, str(tool.get("description") or ""))).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    confirm = str(tool.get("confirm") or "first_time").strip().lower()
    if confirm not in MCP_TOOL_CONFIRM_POLICIES:
        confirm = "first_time"
    if risk == "high":
        confirm = "always"
    return with_capability_approval_metadata({
        "id": public_id,
        "serverId": safe_server_id,
        "kind": "mcp_tool",
        "type": "tool",
        "source": "mcp",
        "adapter": "mcp_stdio",
        "executionMode": "external",
        "toolType": tool_name,
        "name": tool_name,
        "description": _safe_public_mcp_text(tool.get("description"), limit=MCP_TOOL_DESCRIPTION_MAX_LENGTH),
        "group": "mcp",
        "enabled": True,
        "status": "available",
        "reason": "",
        "risk": risk,
        "confirm": confirm,
        "requiresConfirmation": risk == "high" or confirm in {"first_time", "always"},
        "usedBy": ["agent_prompt"],
        "providerId": f"provider.mcp.{safe_server_id}",
        "inputSchema": _normalize_mcp_input_schema(tool.get("inputSchema") or tool.get("input_schema")),
        "exposedToPrompt": bool(tool.get("promptExposed") or tool.get("prompt_exposed")),
    })


def normalize_provider_config_payload(spec: ProviderConfigSpec, payload: Mapping[str, Any]) -> dict[str, Any]:
    endpoint_value = payload.get("endpoint")
    if endpoint_value is None:
        endpoint_value = spec.default_endpoint
    normalized_endpoint = normalize_local_http_endpoint(str(endpoint_value or "").strip())
    if not normalized_endpoint["ok"]:
        return normalized_endpoint
    return {
        "ok": True,
        "status": "valid",
        "enabled": bool(payload.get("enabled")),
        "endpoint": normalized_endpoint["endpoint"],
    }


def normalize_workflow_config_payload(spec: WorkflowConfigSpec, payload: Mapping[str, Any]) -> dict[str, Any]:
    workflow_path_value = payload.get("workflowPath")
    if workflow_path_value is None:
        workflow_path_value = payload.get("workflowRef")
    if workflow_path_value is None:
        workflow_path_value = spec.default_workflow_path
    normalized_path = normalize_workflow_path(str(workflow_path_value or "").strip())
    if not normalized_path["ok"]:
        return normalized_path

    slot_payload = payload.get("slotMapping")
    if slot_payload is None:
        slot_payload = payload.get("slots")
    normalized_slots = normalize_workflow_slot_mapping(spec, slot_payload)
    if not normalized_slots["ok"]:
        return normalized_slots
    return {
        "ok": True,
        "status": "valid",
        "enabled": bool(payload.get("enabled")),
        "workflowPath": normalized_path["workflowPath"],
        "slotMapping": normalized_slots["slotMapping"],
    }


def normalize_voice_profile_config_payload(profile_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    provider_id = str(payload.get("providerId") or payload.get("provider_id") or "provider.tts.gpt_sovits.local").strip()
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


def normalize_mcp_server_config_payload(server_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    transport = str(payload.get("transport") or "stdio").strip().lower()
    if transport != "stdio":
        return {"ok": False, "status": "unsupported_transport", "reason": "mcp_transport_not_supported"}
    command = _safe_private_mcp_path(payload.get("command"))
    if not command:
        return {"ok": False, "status": "missing_config", "reason": "mcp_server_command_required"}
    args = _safe_mcp_args(payload.get("args"))
    if args is None:
        return {"ok": False, "status": "invalid_config", "reason": "mcp_server_args_invalid"}
    env = _safe_mcp_env(payload.get("env"))
    if env is None:
        return {"ok": False, "status": "invalid_config", "reason": "mcp_server_env_invalid"}
    cwd = _safe_private_mcp_path(payload.get("cwd"))
    return {
        "ok": True,
        "status": "valid",
        "enabled": bool(payload.get("enabled", True)),
        "displayName": _safe_short_text(payload.get("displayName") or payload.get("name") or server_id, limit=80) or server_id,
        "transport": transport,
        "command": command,
        "args": args,
        "cwd": cwd,
        "env": env,
    }


def normalize_mcp_tool_discovery_payload(server_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    raw_tools = payload.get("tools")
    if raw_tools is None and isinstance(payload.get("capabilities"), Mapping):
        raw_tools = payload.get("capabilities", {}).get("tools")
    if not isinstance(raw_tools, list):
        return {"ok": False, "status": "invalid_discovery", "reason": "mcp_tools_must_be_list"}
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_tool in raw_tools[:MCP_TOOL_MAX_COUNT]:
        tool = _normalize_mcp_tool_config(server_id, raw_tool)
        if not tool:
            continue
        name = tool["name"]
        if name in seen:
            suffix = 2
            unique = f"{name}_{suffix}"
            while unique in seen:
                suffix += 1
                unique = f"{name}_{suffix}"
            tool["name"] = unique[:MCP_TOOL_NAME_MAX_LENGTH]
            name = tool["name"]
        seen.add(name)
        tools.append(tool)
    return {"ok": True, "status": "valid", "tools": tools}


def normalize_local_http_endpoint(endpoint: str) -> dict[str, Any]:
    raw = str(endpoint or "").strip()
    if not raw:
        return {"ok": False, "status": "missing_config", "reason": "endpoint_required"}
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return {"ok": False, "status": "invalid_config", "reason": "endpoint_must_be_http_localhost"}
    if parsed.username or parsed.password:
        return {"ok": False, "status": "invalid_config", "reason": "endpoint_credentials_not_allowed"}
    host = (parsed.hostname or "").strip().lower()
    if host not in LOOPBACK_HOSTS:
        return {"ok": False, "status": "invalid_config", "reason": "endpoint_must_be_loopback"}
    try:
        port = parsed.port
    except ValueError:
        return {"ok": False, "status": "invalid_config", "reason": "invalid_endpoint_port"}
    netloc_host = "127.0.0.1" if host in {"127.0.0.1", "localhost"} else "[::1]"
    netloc = f"{netloc_host}:{port}" if port else netloc_host
    return {
        "ok": True,
        "status": "valid",
        "endpoint": urlunparse((parsed.scheme, netloc, "", "", "", "")),
    }


def normalize_workflow_path(workflow_path: str) -> dict[str, Any]:
    raw = str(workflow_path or "").strip().replace("\\", "/")
    if not raw:
        return {"ok": False, "status": "missing_workflow", "reason": "workflow_path_required"}
    if len(raw) > WORKFLOW_PATH_MAX_LENGTH:
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_too_long"}
    lowered = raw.lower()
    if "://" in raw or "token" in lowered or "secret" in lowered or "password" in lowered or "api_key" in lowered:
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_be_safe_relative_json"}
    if raw.startswith("/") or raw.startswith("//") or re.match(r"^[A-Za-z]:/", raw):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_be_relative"}
    path = PurePosixPath(raw)
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_not_escape_scope"}
    if any(part.strip() != part or not part.strip() for part in parts):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_segment_invalid"}
    if not parts[-1].lower().endswith(".json"):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_be_json"}
    return {
        "ok": True,
        "status": "valid",
        "workflowPath": "/".join(parts),
    }


def resolve_workflow_config_file_path(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_path: str,
) -> Path | None:
    config_path = _profile_config_path(base_dir, profile_user_id)
    if config_path is None:
        return None
    normalized = normalize_workflow_path(workflow_path)
    if not normalized.get("ok"):
        return None
    root = config_path.parent.resolve()
    target = (root / str(normalized["workflowPath"])).resolve()
    if root == target or root not in target.parents:
        return None
    return target


def validate_workflow_runtime_binding(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    workflow = config.get("workflows", {}).get(spec.id)
    if not isinstance(workflow, Mapping) or not workflow.get("enabled"):
        return _runtime_binding_result(spec, "missing_workflow", "workflow_binding_missing")

    workflow_path = str(workflow.get("workflowPath") or "").strip()
    slot_mapping = workflow.get("slotMapping") if isinstance(workflow.get("slotMapping"), Mapping) else {}
    if not workflow_path:
        return _runtime_binding_result(spec, "missing_workflow", "workflow_path_required")
    if not _required_slots_present(spec, slot_mapping):
        return _runtime_binding_result(spec, "missing_slot_mapping", "required_slot_mapping_missing")

    resolved_path = resolve_workflow_config_file_path(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_path=workflow_path,
    )
    if resolved_path is None:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_path_must_be_safe_relative_json")
    if not resolved_path.is_file():
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_missing")
    try:
        if resolved_path.stat().st_size > WORKFLOW_CONFIG_FILE_MAX_BYTES:
            return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_too_large")
        workflow_json = json.loads(resolved_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_invalid_encoding")
    except json.JSONDecodeError:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_invalid_json")
    except OSError:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_unreadable")

    if not isinstance(workflow_json, Mapping) or not workflow_json:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_json_invalid")

    for slot_name in spec.required_slots:
        slot_path = str(slot_mapping.get(slot_name) or "").strip()
        slot_check = validate_comfyui_input_slot_path(workflow_json, slot_path)
        if not slot_check.get("ok"):
            return _runtime_binding_result(
                spec,
                "invalid_workflow_config",
                str(slot_check.get("reason") or "slot_mapping_target_missing"),
                workflow_file=True,
            )

    return {
        "ok": True,
        "status": "ready",
        "workflowId": spec.id,
        "reason": "",
        "executionReady": True,
        "checks": {
            "workflowFile": True,
            "slotPaths": True,
        },
    }


def validate_comfyui_input_slot_path(workflow_json: Mapping[str, Any], slot_path: str) -> dict[str, Any]:
    path = str(slot_path or "").strip()
    if not WORKFLOW_COMFYUI_SLOT_PATH_RE.match(path):
        return {"ok": False, "reason": "slot_mapping_path_invalid"}
    parts = path.split(".")
    node_id = parts[0]
    node = workflow_json.get(node_id)
    if not isinstance(node, Mapping):
        return {"ok": False, "reason": "slot_mapping_node_missing"}
    current: Any = node.get("inputs")
    if not isinstance(current, Mapping):
        return {"ok": False, "reason": "slot_mapping_inputs_missing"}
    for part in parts[2:]:
        if not isinstance(current, Mapping) or part not in current:
            return {"ok": False, "reason": "slot_mapping_target_missing"}
        current = current[part]
    return {"ok": True, "reason": ""}


def _runtime_binding_result(
    spec: WorkflowConfigSpec,
    status: str,
    reason: str,
    *,
    workflow_file: bool = False,
    slot_paths: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "workflowId": spec.id,
        "reason": _safe_reason(reason),
        "executionReady": False,
        "checks": {
            "workflowFile": workflow_file,
            "slotPaths": slot_paths,
        },
    }


def normalize_workflow_slot_mapping(spec: WorkflowConfigSpec, slot_mapping: Any) -> dict[str, Any]:
    if slot_mapping in (None, ""):
        slot_mapping = {slot: slot for slot in spec.required_slots}
    if not isinstance(slot_mapping, Mapping):
        return {"ok": False, "status": "missing_slot_mapping", "reason": "slot_mapping_must_be_object"}
    allowed = set(spec.required_slots) | set(spec.optional_slots)
    normalized: dict[str, str] = {}
    for slot_name, raw_value in slot_mapping.items():
        slot = str(slot_name or "").strip()
        if slot not in allowed:
            continue
        value = str(raw_value or "").strip()
        if not value:
            continue
        if not WORKFLOW_SLOT_VALUE_RE.match(value):
            return {"ok": False, "status": "invalid_workflow_config", "reason": "slot_mapping_value_invalid"}
        normalized[slot] = value
    missing = [slot for slot in spec.required_slots if not normalized.get(slot)]
    if missing:
        return {
            "ok": False,
            "status": "missing_slot_mapping",
            "reason": "required_slot_mapping_missing",
            "missingSlots": missing,
        }
    return {"ok": True, "status": "valid", "slotMapping": normalized}


def normalize_workflow_asset_handle(value: Any) -> dict[str, Any]:
    handle = str(value or "").strip()
    if not handle:
        return {"ok": False, "status": "invalid_request", "reason": "asset_handle_required"}
    lowered = handle.lower()
    if (
        len(handle) > WORKFLOW_ASSET_HANDLE_MAX_LENGTH
        or "://" in handle
        or "/" in handle
        or "\\" in handle
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return {"ok": False, "status": "invalid_request", "reason": "asset_handle_must_be_safe_opaque_id"}
    if not WORKFLOW_ASSET_HANDLE_RE.match(handle):
        return {"ok": False, "status": "invalid_request", "reason": "asset_handle_must_be_safe_opaque_id"}
    return {"ok": True, "status": "valid", "handle": handle}


def load_capability_config(*, base_dir: Path | str | None, profile_user_id: str) -> dict[str, Any]:
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None or not path.exists():
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "missing",
            "providers": {},
            "workflows": {},
            "voiceProfiles": {},
            "mcpServers": {},
            "approvalPolicy": normalize_approval_policy_config({}),
            "warnings": [],
        }
    try:
        raw = path.read_text(encoding="utf-8")
        data = _parse_capability_config_payload(raw)
    except Exception:
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_file_invalid_json",
            "providers": {},
            "workflows": {},
            "voiceProfiles": {},
            "mcpServers": {},
            "approvalPolicy": normalize_approval_policy_config({}),
            "warnings": [{"status": "invalid_config", "reason": "provider_config_file_invalid_json"}],
        }
    if not isinstance(data, dict):
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_root_must_be_object",
            "providers": {},
            "workflows": {},
            "voiceProfiles": {},
            "mcpServers": {},
            "approvalPolicy": normalize_approval_policy_config({}),
            "warnings": [{"status": "invalid_config", "reason": "provider_config_root_must_be_object"}],
        }
    providers, provider_warnings = _sanitize_provider_configs(data.get("providers"))
    workflows, workflow_warnings = _sanitize_workflow_configs(data.get("workflows"))
    voice_profiles, voice_profile_warnings = _sanitize_voice_profile_configs(data.get("voiceProfiles"))
    mcp_servers, mcp_server_warnings = _sanitize_mcp_server_configs(data.get("mcpServers"))
    approval_policy = normalize_approval_policy_config(data.get("approvalPolicy"))
    warnings = [*provider_warnings, *workflow_warnings, *voice_profile_warnings, *mcp_server_warnings]
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "configStatus": "partial_invalid_config" if warnings else "available",
        "providers": providers,
        "workflows": workflows,
        "voiceProfiles": voice_profiles,
        "mcpServers": mcp_servers,
        "approvalPolicy": approval_policy,
        "warnings": warnings,
    }


def write_capability_config(*, base_dir: Path | str | None, profile_user_id: str, config: Mapping[str, Any]) -> None:
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    writable_config = dict(config)
    if "approvalPolicy" not in writable_config and path.exists():
        try:
            raw_existing = _parse_capability_config_payload(path.read_text(encoding="utf-8"))
        except Exception:
            raw_existing = {}
        if isinstance(raw_existing, Mapping):
            writable_config["approvalPolicy"] = raw_existing.get("approvalPolicy")
    payload = json.dumps(_config_for_write(writable_config), ensure_ascii=False, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(payload)
        handle.write("\n")
    tmp_path.replace(path)


def _parse_capability_config_payload(raw: str) -> Any:
    text = str(raw or "")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except Exception:
        return yaml.safe_load(text)


def _profile_config_path(base_dir: Path | str | None, profile_user_id: str) -> Path | None:
    if base_dir is None:
        return None
    root = Path(base_dir).resolve()
    profile = _safe_profile_id(profile_user_id)
    path = (root / profile / "capabilities" / "capabilities.yaml").resolve()
    if root not in path.parents:
        return None
    return path


def _safe_profile_id(profile_user_id: str) -> str:
    raw = str(profile_user_id or "").strip() or "default"
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-") or "default"
    return safe[:120]


def _public_config_scope(profile_user_id: str) -> dict[str, Any]:
    return {
        "profileUserId": _safe_profile_id(profile_user_id),
        "explicitConfigPath": PROFILE_CONFIG_PATH_TEMPLATE,
    }


def _provider_status(*, configured: bool, enabled: bool, last_health: Mapping[str, Any]) -> str:
    if not configured:
        return "missing_config"
    if not enabled:
        return "disabled"
    health_status = str(last_health.get("status") or "").strip()
    if health_status in {"ready", "unreachable"}:
        return health_status
    return "configured"


def _provider_reason(status: str, last_health: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    config = config if isinstance(config, Mapping) else {}
    if status == "invalid_config":
        return _safe_reason(config.get("reason") or "invalid_provider_config")
    if status == "missing_config":
        return "provider_endpoint_missing"
    if status == "disabled":
        return "provider_disabled"
    if status == "unreachable":
        return _safe_reason(last_health.get("reason") or "connection_failed")
    return ""


def _summarize_provider_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
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


def _summarize_workflow_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = int(by_status.get(status, 0)) + 1
    return {
        "total": len(entries),
        "configured": sum(1 for entry in entries if entry.get("configured")),
        "enabled": sum(1 for entry in entries if entry.get("enabled")),
        "executionReady": sum(1 for entry in entries if entry.get("executionReady")),
        "byStatus": by_status,
    }


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


def _summarize_mcp_server_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = int(by_status.get(status, 0)) + 1
    return {
        "total": len(entries),
        "configured": sum(1 for entry in entries if entry.get("configured")),
        "enabled": sum(1 for entry in entries if entry.get("enabled")),
        "discoveredTools": sum(int(entry.get("toolCount") or 0) for entry in entries),
        "byStatus": by_status,
    }


def _socket_health_check(host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, ""
    except OSError as exc:
        return False, _safe_reason(str(exc) or "connection_failed")


def _sanitize_provider_configs(raw_providers: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    providers: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_providers in (None, ""):
        return providers, warnings
    if not isinstance(raw_providers, Mapping):
        return providers, [{"status": "invalid_config", "reason": "providers_must_be_object"}]

    for spec in CONFIGURABLE_PROVIDER_SPECS:
        if spec.id not in raw_providers:
            continue
        sanitized, warning = _sanitize_provider_config_entry(spec, raw_providers.get(spec.id))
        if sanitized:
            providers[spec.id] = sanitized
        if warning:
            warnings.append(warning)
    return providers, warnings


def _sanitize_workflow_configs(raw_workflows: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    workflows: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_workflows in (None, ""):
        return workflows, warnings
    if not isinstance(raw_workflows, Mapping):
        return workflows, [{"status": "invalid_config", "reason": "workflows_must_be_object"}]

    for spec in CONFIGURABLE_WORKFLOW_SPECS:
        if spec.id not in raw_workflows:
            continue
        sanitized, warning = _sanitize_workflow_config_entry(spec, raw_workflows.get(spec.id))
        if sanitized:
            workflows[spec.id] = sanitized
        if warning:
            warnings.append(warning)
    return workflows, warnings


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
        normalized = normalize_voice_profile_config_payload(profile_id, raw_config if isinstance(raw_config, Mapping) else {})
        if not normalized.get("ok"):
            warnings.append(
                {
                    "voiceProfileId": profile_id,
                    "status": normalized.get("status") or "invalid_config",
                    "reason": normalized.get("reason") or "invalid_voice_profile_config",
                }
            )
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
            "speedFactor": normalized["speedFactor"],
            "fragmentInterval": normalized["fragmentInterval"],
            "textSplitMethod": normalized["textSplitMethod"],
        }
        updated_at = _safe_short_text((raw_config or {}).get("updatedAt")) if isinstance(raw_config, Mapping) else ""
        if updated_at:
            profiles[profile_id]["updatedAt"] = updated_at
    return profiles, warnings


def _sanitize_mcp_server_configs(raw_servers: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    servers: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_servers in (None, ""):
        return servers, warnings
    if not isinstance(raw_servers, Mapping):
        return servers, [{"status": "invalid_config", "reason": "mcp_servers_must_be_object"}]

    for raw_server_id, raw_config in raw_servers.items():
        server_id = _safe_mcp_server_id(raw_server_id)
        if not server_id:
            warnings.append({"status": "invalid_config", "reason": "mcp_server_id_invalid"})
            continue
        normalized = normalize_mcp_server_config_payload(server_id, raw_config if isinstance(raw_config, Mapping) else {})
        if not normalized.get("ok"):
            warnings.append(
                {
                    "serverId": server_id,
                    "status": normalized.get("status") or "invalid_config",
                    "reason": normalized.get("reason") or "invalid_mcp_server_config",
                }
            )
            continue
        server: dict[str, Any] = {
            "enabled": bool(normalized["enabled"]),
            "displayName": normalized["displayName"],
            "transport": normalized["transport"],
            "command": normalized["command"],
            "args": normalized["args"],
            "cwd": normalized["cwd"],
            "env": normalized["env"],
        }
        low_risk_allowlist = _safe_mcp_tool_name_list(
            (raw_config or {}).get("lowRiskAllowlist")
            or (raw_config or {}).get("low_risk_allowlist")
        )
        if low_risk_allowlist:
            server["lowRiskAllowlist"] = low_risk_allowlist
        updated_at = _safe_short_text((raw_config or {}).get("updatedAt")) if isinstance(raw_config, Mapping) else ""
        if updated_at:
            server["updatedAt"] = updated_at
        raw_tools = (raw_config or {}).get("tools") if isinstance(raw_config, Mapping) else None
        normalized_tools = normalize_mcp_tool_discovery_payload(server_id, {"tools": raw_tools or []})
        if normalized_tools.get("ok") and normalized_tools.get("tools"):
            server["tools"] = [
                _apply_mcp_low_risk_allowlist(tool, low_risk_allowlist)
                for tool in normalized_tools["tools"]
            ]
        last_discovery = _sanitize_mcp_last_discovery((raw_config or {}).get("lastDiscovery") if isinstance(raw_config, Mapping) else None)
        if last_discovery:
            server["lastDiscovery"] = last_discovery
        servers[server_id] = server
    return servers, warnings


def _sanitize_provider_config_entry(
    spec: ProviderConfigSpec,
    raw_config: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(raw_config, Mapping):
        return _invalid_provider_config(spec, "provider_config_must_be_object"), {
            "providerId": spec.id,
            "status": "invalid_config",
            "reason": "provider_config_must_be_object",
        }

    sanitized: dict[str, Any] = {"enabled": bool(raw_config.get("enabled"))}
    endpoint_value = str(raw_config.get("endpoint") or "").strip()
    if endpoint_value:
        normalized = normalize_local_http_endpoint(endpoint_value)
        if not normalized["ok"]:
            reason = normalized.get("reason") or "invalid_endpoint"
            return _invalid_provider_config(spec, reason), {
                "providerId": spec.id,
                "status": "invalid_config",
                "reason": reason,
            }
        sanitized["endpoint"] = normalized["endpoint"]

    updated_at = _safe_short_text(raw_config.get("updatedAt"))
    if updated_at:
        sanitized["updatedAt"] = updated_at

    last_health = _sanitize_last_health(raw_config.get("lastHealth"))
    if last_health:
        sanitized["lastHealth"] = last_health

    return sanitized, None


def _sanitize_workflow_config_entry(
    spec: WorkflowConfigSpec,
    raw_config: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(raw_config, Mapping):
        return _invalid_workflow_config(spec, "workflow_config_must_be_object"), {
            "workflowId": spec.id,
            "status": "invalid_config",
            "reason": "workflow_config_must_be_object",
        }

    sanitized: dict[str, Any] = {"enabled": bool(raw_config.get("enabled"))}
    workflow_path_value = str(raw_config.get("workflowPath") or "").strip()
    if workflow_path_value:
        normalized_path = normalize_workflow_path(workflow_path_value)
        if not normalized_path["ok"]:
            reason = normalized_path.get("reason") or "invalid_workflow_path"
            return _invalid_workflow_config(spec, reason), {
                "workflowId": spec.id,
                "status": "invalid_config",
                "reason": reason,
            }
        sanitized["workflowPath"] = normalized_path["workflowPath"]

    normalized_slots = normalize_workflow_slot_mapping(spec, raw_config.get("slotMapping"))
    if not normalized_slots["ok"] and workflow_path_value:
        reason = normalized_slots.get("reason") or "invalid_slot_mapping"
        return _invalid_workflow_config(spec, reason), {
            "workflowId": spec.id,
            "status": normalized_slots.get("status") or "invalid_config",
            "reason": reason,
        }
    if normalized_slots.get("ok"):
        sanitized["slotMapping"] = normalized_slots["slotMapping"]

    updated_at = _safe_short_text(raw_config.get("updatedAt"))
    if updated_at:
        sanitized["updatedAt"] = updated_at

    return sanitized, None


def _invalid_provider_config(spec: ProviderConfigSpec, reason: str) -> dict[str, Any]:
    return {"enabled": False, "status": "invalid_config", "reason": _safe_reason(reason), "providerId": spec.id}


def _invalid_workflow_config(spec: WorkflowConfigSpec, reason: str) -> dict[str, Any]:
    return {"enabled": False, "status": "invalid_config", "reason": _safe_reason(reason), "workflowId": spec.id}


def _sanitize_last_health(raw_last_health: Any) -> dict[str, Any]:
    if not isinstance(raw_last_health, Mapping):
        return {}
    status = str(raw_last_health.get("status") or "").strip()
    if status not in {"ready", "unreachable"}:
        return {}
    sanitized: dict[str, Any] = {"status": status}
    checked_at = _safe_short_text(raw_last_health.get("checkedAt"))
    if checked_at:
        sanitized["checkedAt"] = checked_at
    endpoint = _safe_endpoint_for_output(raw_last_health.get("endpoint"))
    if endpoint:
        sanitized["endpoint"] = endpoint
    if status == "unreachable":
        sanitized["reason"] = _safe_reason(raw_last_health.get("reason") or "connection_failed")
    else:
        sanitized["reason"] = ""
    return sanitized


def _sanitize_mcp_last_discovery(raw_last_discovery: Any) -> dict[str, Any]:
    if not isinstance(raw_last_discovery, Mapping):
        return {}
    status = str(raw_last_discovery.get("status") or "").strip()
    if status not in {"ready", "unavailable", "failed"}:
        return {}
    result: dict[str, Any] = {"status": status}
    discovered_at = _safe_short_text(raw_last_discovery.get("discoveredAt"))
    if discovered_at:
        result["discoveredAt"] = discovered_at
    try:
        result["toolCount"] = max(0, min(MCP_TOOL_MAX_COUNT, int(raw_last_discovery.get("toolCount") or 0)))
    except Exception:
        result["toolCount"] = 0
    if status != "ready":
        result["reason"] = _safe_reason(raw_last_discovery.get("reason") or "mcp_discovery_failed")
    return result


def _config_for_write(config: Mapping[str, Any]) -> dict[str, Any]:
    approval_policy = normalize_approval_policy_config(config.get("approvalPolicy"))
    raw_providers = config.get("providers") if isinstance(config.get("providers"), Mapping) else {}
    providers, _warnings = _sanitize_provider_configs(raw_providers)
    write_providers: dict[str, dict[str, Any]] = {}
    for provider_id, provider_config in providers.items():
        if provider_config.get("status") == "invalid_config":
            continue
        write_providers[provider_id] = {
            key: value
            for key, value in provider_config.items()
            if key in PUBLIC_PROVIDER_FIELDS and value not in (None, "")
        }
    raw_workflows = config.get("workflows") if isinstance(config.get("workflows"), Mapping) else {}
    workflows, _workflow_warnings = _sanitize_workflow_configs(raw_workflows)
    write_workflows: dict[str, dict[str, Any]] = {}
    for workflow_id, workflow_config in workflows.items():
        if workflow_config.get("status") == "invalid_config":
            continue
        write_workflows[workflow_id] = {
            key: value
            for key, value in workflow_config.items()
            if key in PUBLIC_WORKFLOW_FIELDS and value not in (None, "")
        }
    raw_voice_profiles = config.get("voiceProfiles") if isinstance(config.get("voiceProfiles"), Mapping) else {}
    voice_profiles, _voice_profile_warnings = _sanitize_voice_profile_configs(raw_voice_profiles)
    write_voice_profiles: dict[str, dict[str, Any]] = {}
    for profile_id, profile_config in voice_profiles.items():
        write_voice_profiles[profile_id] = {
            key: value
            for key, value in profile_config.items()
            if key in PRIVATE_VOICE_PROFILE_FIELDS and value not in (None, "", [], {})
        }
    raw_mcp_servers = config.get("mcpServers") if isinstance(config.get("mcpServers"), Mapping) else {}
    mcp_servers, _mcp_server_warnings = _sanitize_mcp_server_configs(raw_mcp_servers)
    write_mcp_servers: dict[str, dict[str, Any]] = {}
    for server_id, server_config in mcp_servers.items():
        write_mcp_servers[server_id] = {
            key: value
            for key, value in server_config.items()
            if key in PRIVATE_MCP_SERVER_FIELDS and value not in (None, "", [], {})
        }
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "approvalPolicy": {
            key: value
            for key, value in approval_policy.items()
            if key in PUBLIC_APPROVAL_POLICY_FIELDS and value not in (None, "")
        },
        "providers": write_providers,
        "workflows": write_workflows,
        "voiceProfiles": write_voice_profiles,
        "mcpServers": write_mcp_servers,
    }


def _safe_endpoint_for_output(value: Any) -> str:
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    normalized = normalize_local_http_endpoint(endpoint)
    return str(normalized.get("endpoint") or "") if normalized.get("ok") else ""


def _safe_workflow_path_for_output(value: Any) -> str:
    workflow_path = str(value or "").strip()
    if not workflow_path:
        return ""
    normalized = normalize_workflow_path(workflow_path)
    return str(normalized.get("workflowPath") or "") if normalized.get("ok") else ""


def _safe_slot_mapping_for_output(spec: WorkflowConfigSpec, value: Any) -> dict[str, str]:
    normalized = normalize_workflow_slot_mapping(spec, value)
    return dict(normalized.get("slotMapping") or {}) if normalized.get("ok") else {}


def _normalize_mcp_tool_config(server_id: str, raw_tool: Any) -> dict[str, Any]:
    if not isinstance(raw_tool, Mapping):
        return {}
    tool_name = _safe_mcp_tool_name(raw_tool.get("name"))
    if not tool_name:
        return {}
    description = _safe_public_mcp_text(raw_tool.get("description"), limit=MCP_TOOL_DESCRIPTION_MAX_LENGTH)
    inferred_risk = _infer_mcp_tool_risk(tool_name, description)
    raw_risk = str(raw_tool.get("risk") or "").strip().lower()
    risk = raw_risk if raw_risk in {"low", "medium", "high"} else inferred_risk
    raw_confirm = str(raw_tool.get("confirm") or "").strip().lower()
    confirm = raw_confirm if raw_confirm in MCP_TOOL_CONFIRM_POLICIES else "first_time"
    prompt_exposed = _safe_optional_bool(
        raw_tool.get("promptExposed")
        if "promptExposed" in raw_tool
        else raw_tool.get("prompt_exposed"),
        default=False,
    )
    if risk == "high":
        confirm = "always"
    return {
        "name": tool_name,
        "description": description,
        "inputSchema": _normalize_mcp_input_schema(raw_tool.get("inputSchema") or raw_tool.get("input_schema")),
        "risk": risk,
        "confirm": confirm,
        "promptExposed": bool(prompt_exposed),
    }


def _apply_mcp_low_risk_allowlist(tool: Mapping[str, Any], allowlist: list[str]) -> dict[str, Any]:
    normalized = dict(tool)
    if str(normalized.get("risk") or "").strip().lower() == "low":
        tool_name = str(normalized.get("name") or "").strip()
        if tool_name not in set(allowlist):
            normalized["risk"] = "medium"
    return normalized


def _safe_mcp_tool_name_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:MCP_TOOL_MAX_COUNT]:
        tool_name = _safe_mcp_tool_name(item)
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        result.append(tool_name)
    return result


def _normalize_mcp_input_schema(raw_schema: Any) -> dict[str, Any]:
    if not isinstance(raw_schema, Mapping):
        return {"type": "object", "properties": {}, "required": []}
    schema_type = str(raw_schema.get("type") or "object").strip().lower()
    if schema_type != "object":
        schema_type = "object"
    raw_properties = raw_schema.get("properties") if isinstance(raw_schema.get("properties"), Mapping) else {}
    properties: dict[str, dict[str, str]] = {}
    for raw_name, raw_property in list(raw_properties.items())[:MCP_SCHEMA_PROPERTY_MAX_COUNT]:
        prop_name = _safe_mcp_property_name(raw_name)
        if not prop_name:
            continue
        prop = raw_property if isinstance(raw_property, Mapping) else {}
        prop_type = _safe_mcp_schema_type(prop.get("type"))
        properties[prop_name] = {
            "type": prop_type or "string",
            "description": _safe_public_mcp_text(prop.get("description"), limit=120),
        }
    raw_required = raw_schema.get("required") if isinstance(raw_schema.get("required"), list) else []
    required = []
    for item in raw_required[:MCP_SCHEMA_PROPERTY_MAX_COUNT]:
        prop_name = _safe_mcp_property_name(item)
        if prop_name and prop_name in properties and prop_name not in required:
            required.append(prop_name)
    return {
        "type": schema_type,
        "properties": properties,
        "required": required,
    }


def _safe_mcp_server_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-")
    return safe[:MCP_SERVER_ID_MAX_LENGTH] if safe else ""


def _safe_mcp_tool_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-")
    return safe[:MCP_TOOL_NAME_MAX_LENGTH] if safe else ""


def _safe_mcp_property_name(value: Any) -> str:
    return _safe_mcp_tool_name(value)


def _safe_mcp_schema_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or not MCP_SAFE_TYPE_RE.fullmatch(text):
        return "string"
    if text not in {"string", "number", "integer", "boolean", "array", "object", "null"}:
        return "string"
    return text


def _safe_public_mcp_text(value: Any, *, limit: int = MCP_SERVER_TEXT_MAX_LENGTH) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text[:limit]


def _safe_private_mcp_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in MCP_SECRET_MARKERS):
        return ""
    return text[:MCP_SERVER_PATH_MAX_LENGTH]


def _mcp_text_has_secret_literal(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    without_placeholders = MCP_ENV_PLACEHOLDER_RE.sub("", text)
    lowered = without_placeholders.lower()
    if any(marker in lowered for marker in MCP_SECRET_MARKERS):
        return True
    if re.search(r"(?i)\bbearer\s+[^\s$][^\s]*", without_placeholders):
        return True
    if re.search(r"(?i)\b(sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|xox[baprs]-[A-Za-z0-9])", without_placeholders):
        return True
    return False


def _safe_mcp_args(value: Any) -> list[str] | None:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        return None
    args: list[str] = []
    for item in value[:MCP_SERVER_ARG_MAX_COUNT]:
        text = str(item or "").strip().replace("\r", "").replace("\n", "")
        lowered = text.lower()
        if not text:
            continue
        if any(marker in lowered for marker in MCP_SECRET_MARKERS) and not MCP_ENV_PLACEHOLDER_RE.search(text):
            return None
        if _mcp_text_has_secret_literal(text):
            return None
        args.append(text[:MCP_SERVER_ARG_MAX_LENGTH])
    return args


def _safe_mcp_env(value: Any) -> dict[str, str] | None:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        return None
    env: dict[str, str] = {}
    for raw_key, raw_value in list(value.items())[:MCP_SERVER_ENV_MAX_COUNT]:
        key = str(raw_key or "").strip()
        lowered_key = key.lower()
        if not key or not MCP_ENV_KEY_RE.fullmatch(key):
            return None
        if any(marker in lowered_key for marker in MCP_SECRET_MARKERS):
            return None
        text = str(raw_value or "").strip().replace("\r", "").replace("\n", "")
        lowered_value = text.lower()
        if any(marker in lowered_value for marker in MCP_SECRET_MARKERS) or _mcp_text_has_secret_literal(text):
            return None
        env[key] = text[:MCP_SERVER_TEXT_MAX_LENGTH]
    return env


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
    yaml_files = [
        item for item in files
        if item.suffix.lower() in {".yaml", ".yml"}
    ]
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


def _best_voice_model_file(files: list[Path], allowed_suffixes: set[str], preferred_markers: tuple[str, ...]) -> Path | None:
    candidates = [item for item in files if item.suffix.lower() in allowed_suffixes]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int, int, str]:
        name = path.name.lower()
        marker_index = next((index for index, marker in enumerate(preferred_markers) if marker in name), len(preferred_markers))
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        empty_penalty = 1 if size <= 0 else 0
        return (marker_index, empty_penalty, size, name)

    return sorted(candidates, key=score)[0]


def _infer_mcp_tool_risk(tool_name: str, description: str) -> str:
    text = f"{tool_name} {description}".lower()
    if re.search(r"\b(delete|remove|write|edit|shell|terminal|exec|command|click|open_url|navigate|download|upload)\b", text):
        return "high"
    if re.search(r"\b(file|read|http|fetch|search|web|workspace|local)\b", text):
        return "medium"
    return "medium"


def _safe_voice_profile_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-")
    return safe[:120] if safe else ""


def _safe_short_token(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text):
        return default
    return text


def _safe_optional_bool(value: Any, *, default: bool | None = None) -> bool | None:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


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
        emotion_id = _safe_voice_profile_id(raw_emotion_id)
        if not emotion_id:
            continue
        entry: dict[str, Any] = {}
        if isinstance(raw_entry, str):
            ref_audio_path = _safe_private_local_path(raw_entry)
            if ref_audio_path:
                entry["refAudioPath"] = ref_audio_path
        elif isinstance(raw_entry, Mapping):
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
        if entry:
            result[emotion_id] = entry
    return result


def _safe_path_basename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    name = text.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        return ""
    return name[:120]


def _required_slots_present(spec: WorkflowConfigSpec, slot_mapping: Any) -> bool:
    mapping = slot_mapping if isinstance(slot_mapping, Mapping) else {}
    return all(bool(mapping.get(slot)) for slot in spec.required_slots)


def _workflow_status(
    *,
    spec: WorkflowConfigSpec,
    config: Mapping[str, Any],
    config_status: str,
    provider_entry: Mapping[str, Any] | None,
    workflow_path: str,
    slot_mapping: Mapping[str, Any],
    enabled: bool,
    configured: bool,
) -> str:
    if config_status == "invalid_config":
        return "invalid_workflow_config"
    provider_status = str((provider_entry or {}).get("status") or "").strip()
    if provider_status in {"missing_config", "invalid_config", "unreachable", "disabled"}:
        return provider_status
    if not workflow_path:
        return "missing_workflow"
    if not _required_slots_present(spec, slot_mapping):
        return "missing_slot_mapping"
    if not enabled:
        return "disabled"
    if not configured:
        return "missing_workflow"
    return "configured"


def _workflow_reason(status: str, config: Mapping[str, Any], provider_entry: Mapping[str, Any] | None) -> str:
    if status == "invalid_workflow_config":
        return _safe_reason(config.get("reason") or "invalid_workflow_config")
    if status == "missing_workflow":
        return "workflow_binding_missing"
    if status == "missing_slot_mapping":
        return "required_slot_mapping_missing"
    if status == "disabled":
        return "workflow_disabled"
    if status == "configured":
        return "workflow_runtime_not_bound"
    if status == "missing_config":
        return "provider_endpoint_missing"
    if status == "invalid_config":
        return _safe_reason((provider_entry or {}).get("reason") or "invalid_provider_config")
    if status == "unreachable":
        return _safe_reason((provider_entry or {}).get("reason") or "connection_failed")
    return ""


def _safe_reason(value: Any) -> str:
    text = _safe_short_text(value, limit=160)
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)=([^\s&]+)", r"\1=redacted", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text


def _safe_short_text(value: Any, *, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
