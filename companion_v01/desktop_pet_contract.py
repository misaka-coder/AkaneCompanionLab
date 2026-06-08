from __future__ import annotations

import copy
import os
import sys
import time
from typing import Any

from .capability_registry import CapabilityRegistry, CapabilitySnapshot
from .client_protocol import ClientMode, ClientProtocolContext


DESKTOP_PET_CONTRACT_VERSION = "desktop_pet.v0.1"
DESKTOP_PET_RESOURCE_CONTRACT_VERSION = "desktop_pet_resource.v0.1"
DESKTOP_PET_CLIENT_MODE = "desktop_pet"
DESKTOP_PET_DEFAULT_OUTFIT = "猫娘"
DESKTOP_PET_DEFAULT_EMOTION = "正常"

DESKTOP_PET_CAPABILITIES = (
    "speech_segments",
    "desktop_context",
    "screen_vision",
    "tts",
    "audio_playback",
    "static_sprite",
    "resource_manifest",
    "sessions",
    "asr",
    "workspace_summary",
)

DESKTOP_PET_ENDPOINTS = {
    "health": "/desktop-pet/health",
    "resource_manifest": "/resource-manifest",
    "session_ensure": "/sessions/ensure",
    "think": "/think",
    "think_once": "/think_once",
    "tts": "/tts",
    "asr": "/asr",
    "workspace_summary": "/desktop-pet/workspace/summary",
    "screen_vision_clip": "/desktop-pet/vision/clip",
    "screen_vision_latest": "/desktop-pet/vision/latest",
    "screen_vision_reaction": "/desktop-pet/vision/reaction",
    "screen_vision_clear": "/desktop-pet/vision/clear",
    "music_lyrics": "/capabilities/music/lyrics",
}


def normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def entry_lookup_values(entry: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("id", "name"):
        value = str(entry.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    for alias in entry.get("aliases") or []:
        value = str(alias or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def find_manifest_entry(entries: list[dict[str, Any]], value: Any) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lookup = normalize_key(raw)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for option in entry_lookup_values(entry):
            if raw == option or lookup == normalize_key(option):
                return entry
    return None


def decorate_resource_manifest_for_desktop_pet(
    manifest: dict[str, Any],
    *,
    profile_user_id: str = "",
    session_id: str = "",
    preferred_outfit: str = DESKTOP_PET_DEFAULT_OUTFIT,
    preferred_emotion: str = DESKTOP_PET_DEFAULT_EMOTION,
) -> dict[str, Any]:
    payload = copy.deepcopy(manifest if isinstance(manifest, dict) else {})
    payload.setdefault("schema_version", 2)
    payload.setdefault("characters", {})
    payload.setdefault("defaults", {})

    outfits = payload.get("characters", {}).get("outfits")
    if not isinstance(outfits, list):
        outfits = []
        payload["characters"]["outfits"] = outfits

    defaults = payload["defaults"]
    outfit = (
        find_manifest_entry(outfits, preferred_outfit)
        or find_manifest_entry(outfits, defaults.get("outfit"))
        or (outfits[0] if outfits and isinstance(outfits[0], dict) else None)
    )
    emotions = outfit.get("emotions") if isinstance(outfit, dict) else []
    if not isinstance(emotions, list):
        emotions = []

    emotion = (
        find_manifest_entry(emotions, preferred_emotion)
        or find_manifest_entry(emotions, defaults.get("emotion"))
        or (emotions[0] if emotions and isinstance(emotions[0], dict) else None)
    )

    desktop_outfit = str((outfit or {}).get("id") or defaults.get("outfit") or "").strip()
    desktop_emotion = str((emotion or {}).get("id") or defaults.get("emotion") or "").strip()

    defaults["desktop_pet_outfit"] = desktop_outfit
    defaults["desktop_pet_emotion"] = desktop_emotion
    payload.setdefault("clients", {})
    payload["clients"]["desktop_pet"] = {
        "contract_version": DESKTOP_PET_RESOURCE_CONTRACT_VERSION,
        "profile_user_id": str(profile_user_id or ""),
        "session_id": str(session_id or ""),
        "client_mode": DESKTOP_PET_CLIENT_MODE,
        "default_outfit": desktop_outfit,
        "default_emotion": desktop_emotion,
        "fallback_emotion": desktop_emotion,
        "preferred_outfit": preferred_outfit,
        "preferred_emotion": preferred_emotion,
        "outfit_match_fields": ["id", "name", "aliases"],
        "emotion_match_fields": ["id", "name", "aliases"],
        "supports": {
            "aliases": True,
            "allowed_emotions": True,
            "runtime_user_assets": True,
        },
    }
    return payload


def build_desktop_pet_health_payload(
    *,
    profile_user_id: str = "",
    session_id: str = "",
    streaming_tts_enabled: bool = True,
    yt_dlp_available: bool = False,
    resource_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resource_summary: dict[str, Any] = {
        "endpoint": DESKTOP_PET_ENDPOINTS["resource_manifest"],
        "schema_version": 2,
        "desktop_contract_version": DESKTOP_PET_RESOURCE_CONTRACT_VERSION,
    }
    if isinstance(resource_manifest, dict):
        defaults = resource_manifest.get("defaults") if isinstance(resource_manifest.get("defaults"), dict) else {}
        desktop = (
            resource_manifest.get("clients", {}).get("desktop_pet")
            if isinstance(resource_manifest.get("clients"), dict)
            else {}
        )
        resource_summary.update(
            {
                "default_outfit": str(desktop.get("default_outfit") or defaults.get("outfit") or ""),
                "default_emotion": str(desktop.get("default_emotion") or defaults.get("emotion") or ""),
            }
        )

    return {
        "status": "ok",
        "contract_version": DESKTOP_PET_CONTRACT_VERSION,
        "server_time": int(time.time()),
        "pid": os.getpid(),
        "python": sys.executable,
        "client_mode": DESKTOP_PET_CLIENT_MODE,
        "profile_user_id": str(profile_user_id or ""),
        "session_id": str(session_id or ""),
        "capabilities": list(DESKTOP_PET_CAPABILITIES),
        "endpoints": dict(DESKTOP_PET_ENDPOINTS),
        "resource_manifest": resource_summary,
        "tts": {
            "enabled": bool(streaming_tts_enabled),
            "endpoint": DESKTOP_PET_ENDPOINTS["tts"],
            "response_media_type": "audio/mpeg",
        },
        "asr": {
            "endpoint": DESKTOP_PET_ENDPOINTS["asr"],
            "upload_field": "file",
        },
        "desktop_context": {
            "accepted": True,
            "required": False,
        },
        "screen_vision": {
            "accepted": True,
            "required": False,
            "storage": "short_term_only",
        },
        "dependencies": {
            "yt_dlp": bool(yt_dlp_available),
        },
    }


def build_desktop_pet_error_payload(
    *,
    error: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "status": "error",
        "contract_version": DESKTOP_PET_CONTRACT_VERSION,
        "error": str(error or "unknown_error"),
        "message": str(message or "请求失败。"),
        "retryable": bool(retryable),
    }
    if details:
        payload["details"] = details
    return payload


def _build_resource_summary(
    engine: Any,
    *,
    profile_user_id: str = "",
    character_pack_id: str = "",
    preferred_outfit: str = "",
    preferred_emotion: str = "",
) -> dict[str, Any]:
    """Safe resource info focused on desktop-pet character needs."""
    try:
        if hasattr(engine, "build_resource_manifest"):
            manifest = engine.build_resource_manifest(
                profile_user_id=profile_user_id,
                client_mode=DESKTOP_PET_CLIENT_MODE,
                character_pack_id=character_pack_id,
            )
        else:
            resource_manifest = getattr(engine, "resource_manifest", None)
            manifest = resource_manifest.get_manifest() if hasattr(resource_manifest, "get_manifest") else {}
        manifest = manifest if isinstance(manifest, dict) else {}
    except Exception:
        manifest = {}

    try:
        decorated = decorate_resource_manifest_for_desktop_pet(
            manifest,
            profile_user_id=profile_user_id,
            preferred_outfit=preferred_outfit or DESKTOP_PET_DEFAULT_OUTFIT,
            preferred_emotion=preferred_emotion or DESKTOP_PET_DEFAULT_EMOTION,
        )
    except Exception:
        decorated = manifest

    defaults = decorated.get("defaults", {}) if isinstance(decorated.get("defaults"), dict) else {}
    clients = decorated.get("clients", {}) if isinstance(decorated.get("clients"), dict) else {}
    desktop = clients.get(DESKTOP_PET_CLIENT_MODE, {}) if isinstance(clients.get(DESKTOP_PET_CLIENT_MODE), dict) else {}
    default_outfit_id = str(
        desktop.get("default_outfit")
        or defaults.get("desktop_pet_outfit")
        or defaults.get("outfit")
        or ""
    )
    default_emotion_id = str(
        desktop.get("default_emotion")
        or defaults.get("desktop_pet_emotion")
        or defaults.get("emotion")
        or ""
    )

    emotion_count = 0
    characters = decorated.get("characters", {}) if isinstance(decorated.get("characters"), dict) else {}
    outfits = characters.get("outfits")
    if isinstance(outfits, list):
        fallback_emotions: list[Any] = []
        for outfit in outfits:
            if not isinstance(outfit, dict):
                continue
            emotions = outfit.get("emotions")
            if not fallback_emotions and isinstance(emotions, list):
                fallback_emotions = emotions
            if str(outfit.get("id") or "") == default_outfit_id:
                emotion_count = len(emotions) if isinstance(emotions, list) else 0
                break
        if emotion_count <= 0 and fallback_emotions:
            emotion_count = len(fallback_emotions)

    return {
        "resource_manifest_ok": bool(decorated.get("schema_version")),
        "character_pack_id": _safe_character_pack_id(character_pack_id),
        "outfit": default_outfit_id,
        "default_emotion": default_emotion_id,
        "emotion_count": emotion_count,
    }


def _build_workspace_counts(engine: Any, *, profile_user_id: str, session_id: str) -> dict[str, int]:
    """Workspace item counts via build_desktop_pet_workspace_panel (no content)."""
    builder = getattr(engine, "build_desktop_pet_workspace_panel", None)
    if not callable(builder):
        return {"files": 0, "outputs": 0, "tasks": 0}
    try:
        panel = builder(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=60,
        )
        counts = panel.get("counts") if isinstance(panel, dict) else {}
        return {
            "files": int(counts.get("files", 0) if isinstance(counts, dict) else 0),
            "outputs": int(counts.get("outputs", 0) if isinstance(counts, dict) else 0),
            "tasks": int(counts.get("tasks", 0) if isinstance(counts, dict) else 0),
        }
    except Exception:
        return {"files": -1, "outputs": -1, "tasks": -1}


def _build_capability_object(engine: Any, *, profile_user_id: str, session_id: str) -> dict[str, Any]:
    """Capability selection via registry.select() for desktop_pet mode."""
    declared = list(DESKTOP_PET_CAPABILITIES)

    try:
        ctx = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
        )
        if hasattr(engine, "_build_capability_snapshot"):
            snapshot = engine._build_capability_snapshot(
                client_context=ctx,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
        else:
            snapshot = CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET)
    except Exception:
        snapshot = CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET)

    registry = getattr(engine, "capability_registry", None)
    if not isinstance(registry, CapabilityRegistry):
        registry = CapabilityRegistry()

    try:
        selection = registry.select(snapshot)
    except Exception:
        return {
            "declared": declared,
            "effective_modules": [],
            "tool_layers": [],
            "tool_names": [],
        }

    return {
        "declared": declared,
        "effective_modules": list(getattr(selection, "module_names", ())),
        "tool_layers": list(getattr(selection, "layer_names", ())),
        "tool_names": list(getattr(selection, "tool_names", ())),
    }


def build_desktop_pet_diagnostics_payload(
    *,
    engine: Any,
    profile_user_id: str = "",
    session_id: str = "",
    character_pack_id: str = "",
    preferred_outfit: str = "",
    preferred_emotion: str = "",
    runtime_metrics: dict[str, float] | None = None,
    public_guard_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a read-only diagnostics payload for /desktop-pet/diagnostics.

    Returns structural meta-info about the desktop pet runtime without
    exposing API keys, tokens, prompts, chat content, screenshots,
    or file full text.
    """

    return {
        "status": "ok",
        "client_mode": DESKTOP_PET_CLIENT_MODE,
        "contract_version": DESKTOP_PET_CONTRACT_VERSION,
        "server_time": int(time.time()),
        "capabilities": _build_capability_object(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
        ),
        "resources": _build_resource_summary(
            engine,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            preferred_outfit=preferred_outfit,
            preferred_emotion=preferred_emotion,
        ),
        "workspace": _build_workspace_counts(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
        ),
        "runtime": {
            "pid": os.getpid(),
            "python": sys.executable,
            "metrics": dict(runtime_metrics or {}),
        },
        "safety": {
            "secrets_exposed": False,
            "desktop_actions_require_client": True,
            "full_disk_scan": False,
            "public_guard": dict(public_guard_snapshot or {}),
        },
    }


def _safe_character_pack_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if any(marker in raw for marker in ("/", "\\", "..")):
        return ""
    return raw[:120]
