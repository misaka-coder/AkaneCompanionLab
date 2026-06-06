from __future__ import annotations

import importlib.util
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .capability_registry import CapabilityRegistry
from .local_capability_config import (
    build_provider_config_entry,
    build_workflow_config_entry,
    CONFIGURABLE_PROVIDER_SPECS,
    CONFIGURABLE_WORKFLOW_SPECS,
)


SCHEMA_VERSION = 1
TOOL_RUNTIME_ADAPTER = "tool_runtime"
PROFILE_CONFIG_PATH_TEMPLATE = "users_data/<profile_user_id>/capabilities/capabilities.yaml"
LOCAL_DISCOVERY_PATH = "users_data/_local/capabilities/discovery.json"


TOOL_GROUPS: dict[str, str] = {
    "retrieve_memory": "memory",
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
    "read_attachment_section": "documents",
    "retry_attachment": "attachments",
    "clear_attachment_focus": "attachments",
    "compose_file": "documents",
    "revise_generated_file": "documents",
    "apply_style_to_existing_file": "documents",
    "inspect_media_info": "media",
    "separate_audio_stems": "audio",
    "clean_voice_track": "audio",
    "transcribe_media": "asr",
    "prepare_voice_dataset": "voice_dataset",
    "convert_media_file": "media",
    "inspect_generated_file": "generated_files",
    "send_file": "file_handoff",
    "send_generated_file": "file_handoff",
    "send_sticker": "stickers",
    "manage_generated_file": "generated_files",
}

TOOL_USED_BY: dict[str, list[str]] = {
    "call_npc": ["agent", "web_scene"],
    "check_inventory": ["agent", "web_scene"],
    "manage_gift": ["agent", "web_scene"],
    "manage_artifact": ["agent", "web_scene"],
    "send_sticker": ["agent", "qq_text"],
}

LOW_RISK_TOOLS = {
    "retrieve_memory",
    "list_reminders",
    "check_inventory",
    "inspect_attachment",
    "read_attachment_section",
    "inspect_media_info",
    "inspect_generated_file",
}

MEDIUM_RISK_TOOLS = {
    "fetch_media_from_url",
    "sync_attachment_workspace",
    "retry_attachment",
    "clear_attachment_focus",
    "compose_file",
    "revise_generated_file",
    "apply_style_to_existing_file",
    "separate_audio_stems",
    "clean_voice_track",
    "transcribe_media",
    "prepare_voice_dataset",
    "convert_media_file",
    "send_file",
    "send_generated_file",
    "manage_generated_file",
    "manage_task_workspace",
    "delegate_task",
    "manage_persona",
    "manage_gift",
    "manage_artifact",
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
)


def build_local_capability_catalog(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    profile_user_id: str = "",
    provider_configs: Mapping[str, Any] | None = None,
    workflow_configs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entries = []
    entries.extend(_build_backend_tool_entries(getattr(engine, "tool_handlers", {}) or {}))
    entries.extend(_build_provider_entries(config_module=config_module, tts_client=tts_client))
    configurable_provider_entries = _build_configurable_provider_entries(provider_configs or {})
    entries.extend(configurable_provider_entries)
    entries.extend(_build_workflow_entries(configurable_provider_entries, workflow_configs or {}))
    entries.extend(_build_prompt_module_entries())
    entries = sorted(entries, key=lambda item: (str(item.get("kind") or ""), str(item.get("id") or "")))

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
        "capabilities": entries,
    }


def build_local_workflow_catalog(
    *,
    profile_user_id: str = "",
    provider_configs: Mapping[str, Any] | None = None,
    workflow_configs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider_entries = _build_configurable_provider_entries(provider_configs or {})
    workflows = _build_workflow_entries(provider_entries, workflow_configs or {})
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
    services = [_probe_local_service(target, timeout_seconds=timeout_seconds) for target in KNOWN_LOCAL_SERVICE_PROBES]
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
        group = TOOL_GROUPS.get(tool_name, "backend")
        entries.append(
            {
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
                "enabled": True,
                "status": "ready",
                "risk": _tool_risk(tool_name),
                "requiresConfirmation": False,
                "usedBy": TOOL_USED_BY.get(tool_name, ["agent"]),
            }
        )
    return entries


def _build_provider_entries(*, config_module: Any = None, tts_client: Any = None) -> list[dict[str, Any]]:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    faster_whisper_available = importlib.util.find_spec("faster_whisper") is not None

    tts_status = "ready" if tts_client is not None else "disabled"
    tts_reason = "" if tts_client is not None else "tts_client_unavailable"
    entries = [
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
                "voice": _safe_config_value(config_module, "TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
                "streaming": bool(_safe_config_value(config_module, "STREAMING_TTS_ENABLED", True)),
            },
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
                "model": _safe_config_value(
                    config_module,
                    "ASR_WHISPER_MODEL_SIZE",
                    _safe_config_value(config_module, "WHISPER_MODEL_SIZE", "small"),
                ),
                "language": _safe_config_value(config_module, "ASR_LANGUAGE", "zh"),
            },
        },
    ]
    return entries


def _build_configurable_provider_entries(provider_configs: Mapping[str, Any]) -> list[dict[str, Any]]:
    provider_map = provider_configs if isinstance(provider_configs, Mapping) else {}
    return [
        build_provider_config_entry(spec, provider_map.get(spec.id))
        for spec in CONFIGURABLE_PROVIDER_SPECS
    ]


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


def _build_prompt_module_entries() -> list[dict[str, Any]]:
    entries = []
    registry = CapabilityRegistry()
    for module in registry.modules:
        entries.append(
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
            }
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

    return {
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
        "autoEnabled": False,
    }


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


def _tool_risk(tool_name: str) -> str:
    if tool_name in LOW_RISK_TOOLS:
        return "low"
    if tool_name in MEDIUM_RISK_TOOLS:
        return "medium"
    return "low"


def _tool_description(tool_name: str, group: str) -> str:
    return f"Built-in Akane backend tool `{tool_name}` in the `{group}` capability group."


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


def _incr(mapping: dict[str, int], key: str) -> None:
    mapping[key] = int(mapping.get(key, 0)) + 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
