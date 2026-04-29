from __future__ import annotations

import copy
import os
import sys
import time
from typing import Any


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
