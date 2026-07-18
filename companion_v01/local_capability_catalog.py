from __future__ import annotations

import importlib.util
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from capcore import project_mapping_fields as capcore_project_mapping_fields

from .capability_registry import CapabilityRegistry
from .local_capability_config import (
    apply_approval_policy_to_entry,
    build_mcp_server_config_entry,
    build_mcp_tool_config_entry,
    build_provider_config_entry,
    build_workflow_config_entry,
    CONFIGURABLE_PROVIDER_SPECS,
    CONFIGURABLE_WORKFLOW_SPECS,
    project_capcore_catalog_fields,
)
from .music_lyrics import build_music_lyrics_provider_status
from .capability_adapters import AkanePythonCapabilityAdapter


SCHEMA_VERSION = 1
TOOL_RUNTIME_ADAPTER = "tool_runtime"
PROFILE_CONFIG_PATH_TEMPLATE = "users_data/<profile_user_id>/capabilities/capabilities.yaml"
LOCAL_DISCOVERY_PATH = "users_data/_local/capabilities/discovery.json"


TOOL_GROUPS: dict[str, str] = {
    "retrieve_memory": "memory",
    "read_memory_timeline": "memory",
    "load_character_context": "character_context",
    "set_reminder": "reminders",
    "list_reminders": "reminders",
    "cancel_reminder": "reminders",
    "manage_persona": "persona",
    "manage_task_workspace": "workspace",
    "delegate_task": "workspace",
    "call_npc": "scene",
    "check_inventory": "scene",
    "manage_gift": "scene",
    "manage_artifact": "scene",
    "fetch_media_from_url": "media",
    "sync_attachment_workspace": "attachments",
    "inspect_attachment": "attachments",
    "load_material": "attachments",
    "read_attachment_section": "documents",
    "retry_attachment": "attachments",
    "clear_attachment_focus": "attachments",
    "compose_file": "documents",
    "generate_image": "image_generation",
    "revise_generated_file": "documents",
    "apply_style_to_existing_file": "documents",
    "inspect_media_info": "media",
    "separate_audio_stems": "audio",
    "cover_song": "audio",
    "clean_voice_track": "audio",
    "transcribe_media": "asr",
    "prepare_voice_dataset": "voice_dataset",
    "convert_media_file": "media",
    "inspect_generated_file": "generated_files",
    "send_file": "file_handoff",
    "send_generated_file": "file_handoff",
    "send_sticker": "stickers",
    "manage_generated_file": "generated_files",
    "web_search": "web",
    "open_browser": "desktop_browser",
    "browser_page": "desktop_browser",
    "open_music_search": "music",
}

TOOL_USED_BY: dict[str, list[str]] = {
    "load_character_context": ["agent", "desktop_pet", "qq_text"],
    "load_material": ["agent", "desktop_pet", "qq_text"],
    "generate_image": ["agent", "desktop_pet", "qq_text"],
    "cover_song": ["agent", "desktop_pet", "qq_text"],
    "call_npc": ["agent", "web_scene"],
    "check_inventory": ["agent", "web_scene"],
    "manage_gift": ["agent", "web_scene"],
    "manage_artifact": ["agent", "web_scene"],
    "send_sticker": ["agent", "qq_text"],
    "web_search": ["agent", "desktop_pet", "qq_text", "web_scene"],
    "open_browser": ["agent", "desktop_pet"],
    "browser_page": ["agent", "desktop_pet"],
    "open_music_search": ["agent", "desktop_pet"],
}


@dataclass(frozen=True)
class LocalServiceProbe:
    id: str
    name: str
    type: str
    adapter: str
    endpoint: str
    host: str
    port: int


KNOWN_LOCAL_SERVICE_PROBES = (
    LocalServiceProbe(
        id="provider.comfyui.local",
        name="本地 ComfyUI",
        type="asset_processor",
        adapter="comfyui",
        endpoint="http://127.0.0.1:8188",
        host="127.0.0.1",
        port=8188,
    ),
    LocalServiceProbe(
        id="provider.tts.gpt_sovits.local",
        name="本地 GPT-SoVITS",
        type="tts_provider",
        adapter="gpt_sovits",
        endpoint="http://127.0.0.1:9880",
        host="127.0.0.1",
        port=9880,
    ),
    LocalServiceProbe(
        id="provider.voice_conversion.rvc.local",
        name="本地 RVC WebUI",
        type="voice_conversion_provider",
        adapter="rvc_webui",
        endpoint="http://127.0.0.1:7899",
        host="127.0.0.1",
        port=7899,
    ),
)


def build_local_capability_catalog(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    settings: Any = None,
    profile_user_id: str = "",
    provider_configs: Mapping[str, Any] | None = None,
    workflow_configs: Mapping[str, Any] | None = None,
    mcp_server_configs: Mapping[str, Any] | None = None,
    approval_policy: Mapping[str, Any] | None = None,
    character_voice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entries = []
    entries.extend(_build_backend_tool_entries(getattr(engine, "tool_handlers", {}) or {}))
    entries.extend(_build_python_adapter_entries())
    entries.extend(_build_provider_entries(config_module=config_module, tts_client=tts_client, settings=settings))
    configurable_provider_entries = _build_configurable_provider_entries(provider_configs or {})
    entries.extend(configurable_provider_entries)
    entries.extend(_build_workflow_entries(configurable_provider_entries, workflow_configs or {}))
    entries.extend(_build_mcp_entries(mcp_server_configs or {}))
    entries.extend(_build_prompt_module_entries())
    entries = [apply_approval_policy_to_entry(entry, approval_policy) for entry in entries]
    entries = sorted(entries, key=lambda item: (str(item.get("kind") or ""), str(item.get("id") or "")))
    resolutions = _build_voice_provider_resolutions(entries, character_voice=character_voice)

    return {
        "ok": True,
        "status": "available",
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "execution": "read-only",
        "configScope": {
            "profileUserId": str(profile_user_id or ""),
            "explicitConfigPath": PROFILE_CONFIG_PATH_TEMPLATE,
            "localDiscoveryPath": LOCAL_DISCOVERY_PATH,
        },
        "summary": _summarize_entries(entries),
        "resolutions": resolutions,
        "capabilities": entries,
    }


def build_local_workflow_catalog(
    *,
    profile_user_id: str = "",
    provider_configs: Mapping[str, Any] | None = None,
    workflow_configs: Mapping[str, Any] | None = None,
    approval_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider_entries = _build_configurable_provider_entries(provider_configs or {})
    workflows = _build_workflow_entries(provider_entries, workflow_configs or {})
    workflows = [apply_approval_policy_to_entry(entry, approval_policy) for entry in workflows]
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "execution": "read-only",
        "configScope": {
            "profileUserId": str(profile_user_id or ""),
            "explicitConfigPath": PROFILE_CONFIG_PATH_TEMPLATE,
            "localDiscoveryPath": LOCAL_DISCOVERY_PATH,
        },
        "summary": _summarize_entries(workflows),
        "workflows": workflows,
    }


def probe_known_local_services(*, timeout_seconds: float = 0.35) -> dict[str, Any]:
    services = [
        apply_approval_policy_to_entry(_probe_local_service(target, timeout_seconds=timeout_seconds), None)
        for target in KNOWN_LOCAL_SERVICE_PROBES
    ]
    return {
        "ok": True,
        "status": "checked",
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "discoveryPath": LOCAL_DISCOVERY_PATH,
        "scope": "localhost-known-services",
        "autoEnable": False,
        "services": services,
        "summary": _summarize_entries(services),
    }


def _build_backend_tool_entries(tool_handlers: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool_name in sorted(str(name or "").strip() for name in tool_handlers.keys()):
        if not tool_name:
            continue
        handler = tool_handlers.get(tool_name)
        status = _tool_runtime_status(handler)
        group = TOOL_GROUPS.get(tool_name, "backend")
        capcore_projection = _tool_capcore_catalog_projection(tool_name, handler)
        entry = {
            "id": f"tool.{tool_name}",
            "kind": "tool",
            "type": "tool",
            "source": "backend_tool",
            "adapter": TOOL_RUNTIME_ADAPTER,
            "executionMode": "internal",
            "toolType": tool_name,
            "name": _humanize_tool_name(tool_name),
            "description": _tool_description(tool_name, group),
            "group": group,
            "enabled": bool(status.get("enabled")),
            "status": str(status.get("status") or "ready"),
            "risk": capcore_projection["risk"],
            "requiresConfirmation": capcore_projection["requiresConfirmation"],
            "usedBy": TOOL_USED_BY.get(tool_name, ["agent"]),
        }
        reason = str(status.get("reason") or "").strip()
        if reason:
            entry["reason"] = reason
        entries.append(entry)
    return [_project_catalog_entry(entry) for entry in entries]


def _build_python_adapter_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        descriptors = AkanePythonCapabilityAdapter().list_capabilities_sync()
    except Exception:
        return []
    for descriptor in descriptors:
        capability_id = str(getattr(descriptor, "id", "") or "").strip()
        if not capability_id:
            continue
        confirm = str(getattr(descriptor, "confirm", "") or "first_time").strip()
        entry = {
            "id": capability_id,
            "kind": "python_tool",
            "type": "tool",
            "source": "python_adapter",
            "adapter": "python",
            "executionMode": "internal",
            "toolType": capability_id,
            "name": str(getattr(descriptor, "display_name", "") or capability_id).strip(),
            "description": str(getattr(descriptor, "short_hint", "") or "").strip()[:240],
            "group": "python_local",
            "enabled": True,
            "status": "ready",
            "risk": str(getattr(descriptor, "risk", "") or "medium").strip(),
            "confirm": confirm,
            "requiresConfirmation": confirm in {"first_time", "always"},
            "effects": list(getattr(descriptor, "effects", ()) or ()),
            "usedBy": ["agent"],
            "exposedToPrompt": bool(getattr(descriptor, "prompt_exposed", False)),
        }
        entries.append(entry)
    return [_project_catalog_entry(entry) for entry in entries]


def _build_provider_entries(
    *,
    config_module: Any = None,
    tts_client: Any = None,
    settings: Any = None,
) -> list[dict[str, Any]]:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    faster_whisper_available = importlib.util.find_spec("faster_whisper") is not None

    tts_status = "ready" if tts_client is not None else "disabled"
    tts_reason = "" if tts_client is not None else "tts_client_unavailable"
    system_media_ready = sys.platform == "win32"
    lyrics_status = build_music_lyrics_provider_status(config_module)
    entries = [
        {
            "id": "provider.music.system_media",
            "kind": "provider",
            "type": "music_perception_provider",
            "source": "tauri_bridge",
            "adapter": "winrt_smtc",
            "executionMode": "internal",
            "name": "系统媒体感知",
            "enabled": system_media_ready,
            "status": "ready" if system_media_ready else "unavailable",
            "reason": "" if system_media_ready else "unsupported_platform",
            "risk": "low",
            "requiresConfirmation": False,
            "usedBy": ["desktop_pet"],
            "summary": "读取系统正在播放的歌曲和进度",
        },
        {
            "id": "provider.music.system_media_control",
            "kind": "provider",
            "type": "music_playback_provider",
            "source": "tauri_bridge",
            "adapter": "winrt_smtc",
            "executionMode": "internal",
            "name": "系统媒体控制",
            "enabled": system_media_ready,
            "status": "ready" if system_media_ready else "unavailable",
            "reason": "" if system_media_ready else "unsupported_platform",
            "risk": "medium",
            "requiresConfirmation": False,
            "usedBy": ["desktop_pet"],
            "summary": "请求当前系统播放器播放、暂停、停止、上一首或下一首",
        },
        {
            "id": "provider.music.lyrics.online",
            "kind": "provider",
            "type": "music_lyrics_provider",
            "source": "builtin",
            "adapter": "syncedlyrics",
            "executionMode": "internal",
            "name": "在线歌词检索",
            "enabled": bool(lyrics_status.get("enabled")),
            "status": str(lyrics_status.get("status") or "unavailable"),
            "reason": str(lyrics_status.get("reason") or ""),
            "risk": "medium",
            "requiresConfirmation": False,
            "usedBy": ["desktop_pet"],
            "summary": "根据歌名和歌手搜索同步歌词",
        },
        {
            "id": "provider.tts.edge",
            "kind": "provider",
            "type": "tts_provider",
            "source": "builtin",
            "adapter": "edge_tts",
            "executionMode": "internal",
            "name": "Edge TTS",
            "enabled": tts_client is not None,
            "status": tts_status,
            "reason": tts_reason,
            "risk": "low",
            "requiresConfirmation": False,
            "usedBy": ["voice", "desktop_pet"],
            "config": {
                "voice": _runtime_value(settings, config_module, "tts_voice", "TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
                "streaming": bool(
                    _runtime_value(settings, config_module, "streaming_tts_enabled", "STREAMING_TTS_ENABLED", True)
                ),
            },
        },
        {
            "id": "provider.voice.text_only",
            "kind": "provider",
            "type": "tts_provider",
            "source": "builtin",
            "adapter": "text_only",
            "executionMode": "internal",
            "name": "文字气泡兜底",
            "enabled": True,
            "status": "ready",
            "reason": "",
            "risk": "low",
            "requiresConfirmation": False,
            "usedBy": ["voice", "desktop_pet"],
            "fallbackOnly": True,
            "summary": "语音不可用时仍显示文本回复",
        },
        {
            "id": "provider.media.ffmpeg",
            "kind": "provider",
            "type": "asset_processor",
            "source": "external_executor",
            "adapter": "ffmpeg",
            "executionMode": "external",
            "name": "FFmpeg",
            "enabled": bool(ffmpeg_path),
            "status": "ready" if ffmpeg_path else "missing_executor",
            "reason": "" if ffmpeg_path else "ffmpeg_not_found",
            "risk": "medium",
            "requiresConfirmation": False,
            "usedBy": ["workspace", "media"],
        },
        {
            "id": "provider.media.ffprobe",
            "kind": "provider",
            "type": "asset_processor",
            "source": "external_executor",
            "adapter": "ffprobe",
            "executionMode": "external",
            "name": "FFprobe",
            "enabled": bool(ffprobe_path),
            "status": "ready" if ffprobe_path else "missing_executor",
            "reason": "" if ffprobe_path else "ffprobe_not_found",
            "risk": "low",
            "requiresConfirmation": False,
            "usedBy": ["workspace", "media"],
        },
        {
            "id": "provider.asr.faster_whisper",
            "kind": "provider",
            "type": "asr_provider",
            "source": "builtin",
            "adapter": "faster_whisper",
            "executionMode": "internal",
            "name": "faster-whisper ASR",
            "enabled": bool(faster_whisper_available and ffmpeg_path),
            "status": _asr_status(faster_whisper_available=faster_whisper_available, ffmpeg_path=ffmpeg_path),
            "reason": _asr_reason(faster_whisper_available=faster_whisper_available, ffmpeg_path=ffmpeg_path),
            "risk": "medium",
            "requiresConfirmation": False,
            "usedBy": ["voice", "workspace", "desktop_pet"],
            "config": {
                "model": _runtime_value(
                    settings,
                    config_module,
                    "asr_whisper_model_size",
                    "ASR_WHISPER_MODEL_SIZE",
                    _safe_config_value(config_module, "WHISPER_MODEL_SIZE", "small"),
                ),
                "language": _runtime_value(settings, config_module, "asr_language", "ASR_LANGUAGE", "zh"),
            },
        },
        {
            "id": "provider.asr.text_input",
            "kind": "provider",
            "type": "asr_provider",
            "source": "builtin",
            "adapter": "text_input",
            "executionMode": "internal",
            "name": "文本输入兜底",
            "enabled": True,
            "status": "ready",
            "reason": "",
            "risk": "low",
            "requiresConfirmation": False,
            "usedBy": ["voice", "desktop_pet"],
            "fallbackOnly": True,
            "summary": "语音输入不可用时继续使用文本输入",
        },
    ]
    return [_project_catalog_entry(entry) for entry in entries]


def _build_voice_provider_resolutions(
    entries: list[dict[str, Any]],
    *,
    character_voice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    by_id = {str(entry.get("id") or ""): entry for entry in entries}
    character_voice = character_voice if isinstance(character_voice, Mapping) else {}
    requested_tts = _normalize_voice_provider_id(character_voice.get("provider")) or "provider.tts.edge"
    voice_profile_id = _safe_public_text(character_voice.get("profileId") or character_voice.get("profile_id"))
    request_source = "character_pack" if character_voice.get("provider") else "default"

    tts_resolution = _resolve_first_ready_provider(
        capability_id="voice.tts.character",
        requested_provider_id=requested_tts,
        candidates=[requested_tts, "provider.tts.edge", "provider.voice.text_only"],
        entries_by_id=by_id,
        request_source=request_source,
        extra_reason=_voice_request_blocker(requested_tts, voice_profile_id),
        voice_profile_id=voice_profile_id,
    )
    configured_asr_id = "provider.asr.openai_compat.local"
    configured_asr_entry = by_id.get(configured_asr_id)
    configured_asr_status = str((configured_asr_entry or {}).get("status") or "").strip()
    requested_asr = (
        configured_asr_id
        if isinstance(configured_asr_entry, Mapping)
        and configured_asr_entry.get("enabled") is not False
        and configured_asr_status in {"configured", "ready", "available", "ok"}
        else "provider.asr.faster_whisper"
    )
    asr_resolution = _resolve_first_ready_provider(
        capability_id="voice.input.asr",
        requested_provider_id=requested_asr,
        candidates=[requested_asr, "provider.asr.faster_whisper", "provider.asr.text_input"],
        entries_by_id=by_id,
        request_source="default",
        accepted_statuses={"configured", "ready", "available", "ok"},
    )
    return {
        "voice.tts.character": tts_resolution,
        "voice.input.asr": asr_resolution,
    }


def _resolve_first_ready_provider(
    *,
    capability_id: str,
    requested_provider_id: str,
    candidates: list[str],
    entries_by_id: Mapping[str, Mapping[str, Any]],
    request_source: str,
    extra_reason: str = "",
    voice_profile_id: str = "",
    accepted_statuses: set[str] | None = None,
) -> dict[str, Any]:
    unique_candidates = []
    for candidate in candidates:
        candidate_id = str(candidate or "").strip()
        if candidate_id and candidate_id not in unique_candidates:
            unique_candidates.append(candidate_id)

    active_provider_id = ""
    if not extra_reason:
        for candidate_id in unique_candidates:
            entry = entries_by_id.get(candidate_id)
            if _is_provider_ready(entry, accepted_statuses=accepted_statuses):
                active_provider_id = candidate_id
                break
    if not active_provider_id:
        for fallback_id in unique_candidates:
            if _is_provider_ready(entries_by_id.get(fallback_id), accepted_statuses=accepted_statuses):
                active_provider_id = fallback_id
                break

    requested_entry = entries_by_id.get(requested_provider_id)
    active_entry = entries_by_id.get(active_provider_id)
    status = (
        "ready"
        if active_provider_id and active_provider_id == requested_provider_id and not extra_reason
        else "degraded"
    )
    if not active_provider_id:
        status = "unavailable"
    reason = extra_reason or _resolution_reason(
        requested_provider_id=requested_provider_id,
        active_provider_id=active_provider_id,
        requested_entry=requested_entry,
    )
    return {
        "capabilityId": capability_id,
        "strategy": "first_ready",
        "status": status,
        "reason": reason if status != "ready" else "",
        "requestSource": request_source,
        "requestedProviderId": requested_provider_id,
        "requestedProviderName": _provider_name(requested_entry, requested_provider_id),
        "activeProviderId": active_provider_id,
        "activeProviderName": _provider_name(active_entry, active_provider_id),
        "fallbackProviderId": active_provider_id if active_provider_id != requested_provider_id else "",
        "voiceProfileId": voice_profile_id,
        "candidates": [
            _candidate_summary(candidate_id, entries_by_id.get(candidate_id)) for candidate_id in unique_candidates
        ],
    }


def _normalize_voice_provider_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    allowed_provider_ids = {
        "provider.tts.edge",
        "provider.tts.gpt_sovits.local",
        "provider.voice.text_only",
    }
    aliases = {
        "edge": "provider.tts.edge",
        "edge_tts": "provider.tts.edge",
        "edge-tts": "provider.tts.edge",
        "provider.tts.edge": "provider.tts.edge",
        "gpt_sovits": "provider.tts.gpt_sovits.local",
        "gpt-sovits": "provider.tts.gpt_sovits.local",
        "gptsovits": "provider.tts.gpt_sovits.local",
        "provider.tts.gpt_sovits.local": "provider.tts.gpt_sovits.local",
        "text": "provider.voice.text_only",
        "text_only": "provider.voice.text_only",
        "none": "provider.voice.text_only",
        "provider.voice.text_only": "provider.voice.text_only",
    }
    normalized = aliases.get(raw.lower(), raw)
    return normalized if normalized in allowed_provider_ids else ""


def _voice_request_blocker(requested_provider_id: str, voice_profile_id: str) -> str:
    if requested_provider_id == "provider.tts.gpt_sovits.local" and not voice_profile_id:
        return "requested_voice_profile_missing"
    return ""


def _is_provider_ready(entry: Mapping[str, Any] | None, *, accepted_statuses: set[str] | None = None) -> bool:
    if not isinstance(entry, Mapping):
        return False
    status = str(entry.get("status") or "").strip()
    statuses = accepted_statuses or {"ready", "available", "ok"}
    return entry.get("enabled") is not False and status in statuses


def _resolution_reason(
    *,
    requested_provider_id: str,
    active_provider_id: str,
    requested_entry: Mapping[str, Any] | None,
) -> str:
    if not active_provider_id:
        return "no_ready_provider"
    if requested_provider_id == active_provider_id:
        return ""
    if not isinstance(requested_entry, Mapping):
        return "requested_provider_unknown"
    status = str(requested_entry.get("status") or "").strip()
    reason = str(requested_entry.get("reason") or "").strip()
    if status == "missing_config":
        return "requested_provider_missing_config"
    if status == "missing_executor":
        return "requested_provider_missing_executor"
    if status == "missing_model":
        return "requested_provider_missing_model"
    if status == "unreachable":
        return "requested_provider_unreachable"
    if status == "disabled":
        return "requested_provider_disabled"
    if status == "configured":
        return "requested_provider_pending_health_check"
    if reason:
        return reason[:120]
    return "requested_provider_not_ready"


def _provider_name(entry: Mapping[str, Any] | None, provider_id: str) -> str:
    if isinstance(entry, Mapping):
        return str(entry.get("name") or entry.get("id") or provider_id or "").strip()
    return str(provider_id or "").strip()


def _candidate_summary(provider_id: str, entry: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "name": _provider_name(entry, provider_id),
        "status": str((entry or {}).get("status") or "unavailable"),
        "ready": _is_provider_ready(entry),
    }


def _safe_public_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\\", "/")
    lowered = text.lower()
    if (
        "://" in text
        or "/" in text
        or ":" in text
        or ".." in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return ""
    return text[:120]


def _build_configurable_provider_entries(provider_configs: Mapping[str, Any]) -> list[dict[str, Any]]:
    provider_map = provider_configs if isinstance(provider_configs, Mapping) else {}
    return [build_provider_config_entry(spec, provider_map.get(spec.id)) for spec in CONFIGURABLE_PROVIDER_SPECS]


def _build_workflow_entries(
    provider_entries: list[dict[str, Any]],
    workflow_configs: Mapping[str, Any],
) -> list[dict[str, Any]]:
    providers_by_id = {str(entry.get("id") or ""): entry for entry in provider_entries}
    workflow_map = workflow_configs if isinstance(workflow_configs, Mapping) else {}
    return [
        build_workflow_config_entry(spec, workflow_map.get(spec.id), providers_by_id.get(spec.provider_id))
        for spec in CONFIGURABLE_WORKFLOW_SPECS
    ]


def _build_mcp_entries(mcp_server_configs: Mapping[str, Any]) -> list[dict[str, Any]]:
    server_map = mcp_server_configs if isinstance(mcp_server_configs, Mapping) else {}
    entries: list[dict[str, Any]] = []
    for server_id, server_config in sorted(server_map.items(), key=lambda item: str(item[0])):
        if not isinstance(server_config, Mapping):
            continue
        server_entry = build_mcp_server_config_entry(str(server_id), server_config)
        if not server_entry.get("serverId"):
            continue
        entries.append(server_entry)
        if server_entry.get("status") != "ready":
            continue
        for tool in server_config.get("tools") or []:
            tool_entry = build_mcp_tool_config_entry(str(server_id), tool if isinstance(tool, Mapping) else {})
            if tool_entry.get("id"):
                entries.append(tool_entry)
    return entries


def _build_prompt_module_entries() -> list[dict[str, Any]]:
    entries = []
    registry = CapabilityRegistry()
    for module in registry.modules:
        entries.append(
            _project_catalog_entry(
                {
                    "id": f"prompt_module.{module.name}",
                    "kind": "prompt_module",
                    "type": "tool",
                    "source": "builtin",
                    "adapter": "prompt_capability_registry",
                    "executionMode": "internal",
                    "name": module.name,
                    "description": module.light_hint,
                    "group": module.layer,
                    "enabled": True,
                    "status": "ready",
                    "risk": "low",
                    "requiresConfirmation": False,
                    "usedBy": ["agent_prompt"],
                    "toolTypes": list(module.tools),
                    "clientModes": [mode.value for mode in module.modes],
                },
                default_risk="low",
            )
        )
    return entries


def _probe_local_service(target: LocalServiceProbe, *, timeout_seconds: float) -> dict[str, Any]:
    status = "unreachable"
    reason = "connection_failed"
    try:
        with socket.create_connection((target.host, target.port), timeout=max(0.05, float(timeout_seconds))):
            status = "ready"
            reason = ""
    except OSError as exc:
        reason = str(exc)[:120] or "connection_failed"

    entry = {
        "id": target.id,
        "kind": "provider",
        "type": target.type,
        "source": "external_executor",
        "adapter": target.adapter,
        "executionMode": "external",
        "name": target.name,
        "enabled": False,
        "status": status,
        "reason": reason,
        "risk": "medium",
        "requiresConfirmation": False,
        "usedBy": ["discovery"],
        "endpoint": target.endpoint,
        "discovered": status == "ready",
        "bindable": status == "ready",
    }
    return _project_catalog_entry(entry)


def _project_catalog_entry(
    entry: Mapping[str, Any],
    *,
    default_risk: str = "medium",
    default_confirm: str = "never",
) -> dict[str, Any]:
    return project_capcore_catalog_fields(
        entry,
        default_risk=str(entry.get("risk") or default_risk),
        default_confirm=default_confirm,
    )


def _summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for entry in entries:
        _incr(by_kind, str(entry.get("kind") or "unknown"))
        _incr(by_status, str(entry.get("status") or "unknown"))
        _incr(by_source, str(entry.get("source") or "unknown"))
        _incr(by_type, str(entry.get("type") or "unknown"))
    return {
        "total": len(entries),
        "byKind": by_kind,
        "byStatus": by_status,
        "bySource": by_source,
        "byType": by_type,
    }


def _tool_capcore_catalog_projection(tool_name: str, handler: Any) -> dict[str, Any]:
    raw_risk = _handler_tool_risk(handler)
    capability_id = f"tool.{tool_name}"
    projected = capcore_project_mapping_fields(
        {
            "id": capability_id,
            "name": _humanize_tool_name(tool_name),
            "risk": raw_risk,
            # Static backend tool catalog approval stays capability-level.
            # Fine-grained gates such as browser click/fill/press are enforced
            # by their execution handlers with action-specific capcore requests.
            "requiresConfirmation": raw_risk == "high",
        },
        default_id=capability_id,
        default_risk="low",
        default_confirm="never",
    )
    return {
        "risk": projected["risk"],
        "requiresConfirmation": bool(projected["requiresConfirmation"]),
    }


def _handler_tool_risk(handler: Any) -> str:
    metadata_fn = getattr(handler, "tool_metadata", None)
    if callable(metadata_fn):
        try:
            metadata = metadata_fn()
        except Exception:
            metadata = None
        risk = str(getattr(metadata, "risk", "") or "").strip().lower()
        if risk in {"low", "medium", "high"}:
            return risk
    return "low"


def _tool_description(tool_name: str, group: str) -> str:
    if tool_name == "browser_page":
        return "Visible Akane-managed browser window with accessibility snapshots, visible link/video candidates, element refs, scrolling, and approval-gated click/fill/press actions."
    if tool_name == "open_music_search":
        return "Open a public music-platform search page for a requested song; it does not guarantee playback or control the player."
    return f"Built-in Akane backend tool `{tool_name}` in the `{group}` capability group."


def _tool_runtime_status(handler: Any) -> dict[str, Any]:
    status_fn = getattr(handler, "capability_status", None)
    if callable(status_fn):
        try:
            status = status_fn()
        except Exception:
            return {"enabled": False, "status": "unavailable", "reason": "tool_status_failed"}
        if isinstance(status, Mapping):
            normalized_status = str(status.get("status") or "ready").strip() or "ready"
            return {
                "enabled": bool(
                    status.get("enabled", normalized_status not in {"disabled", "unavailable", "missing_executor"})
                ),
                "status": normalized_status,
                "reason": str(status.get("reason") or "").strip()[:160],
            }
    return {"enabled": True, "status": "ready", "reason": ""}


def _humanize_tool_name(tool_name: str) -> str:
    return tool_name.replace("_", " ").strip().title()


def _asr_status(*, faster_whisper_available: bool, ffmpeg_path: str | None) -> str:
    if not faster_whisper_available:
        return "missing_executor"
    if not ffmpeg_path:
        return "missing_executor"
    return "ready"


def _asr_reason(*, faster_whisper_available: bool, ffmpeg_path: str | None) -> str:
    if not faster_whisper_available:
        return "faster_whisper_not_found"
    if not ffmpeg_path:
        return "ffmpeg_not_found"
    return ""


def _safe_config_value(config_module: Any, name: str, default: Any) -> Any:
    if config_module is None:
        return default
    value = getattr(config_module, name, default)
    if value is None:
        return default
    return value


def _runtime_value(settings: Any, config_module: Any, field_name: str, config_name: str, default: Any) -> Any:
    if settings is not None and hasattr(settings, field_name):
        value = getattr(settings, field_name)
        return default if value is None else value
    return _safe_config_value(config_module, config_name, default)


def _incr(mapping: dict[str, int], key: str) -> None:
    mapping[key] = int(mapping.get(key, 0)) + 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
