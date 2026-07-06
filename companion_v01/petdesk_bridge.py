from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .desktop_pet_character_resources import sanitize_character_pack_id


PETDESK_BRIDGE_VERSION = "akane-petdesk-bridge.m32"
PET_DISPLAY_SCHEMA_VERSION = "pet.display.v1"
PETDESK_CHARACTER_PACK_PREFIX = "/petdesk-character-packs"
LEGACY_CHARACTER_PACK_PREFIX = "/desktop-pet-character-packs"

_SAFE_HANDLE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_UNSAFE_HANDLE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ALLOWED_MOTIONS = {"idle", "thinking", "speaking", "interrupted", "local_reaction"}
_AKANE_INTERACTION_PROFILE = {
    "window": {
        "baseSize": {"width": 320, "height": 560},
        "minScale": 0.7,
        "maxScale": 1.45,
    },
    "layout": {
        "--pet-bubble-top": "12px",
        "--pet-bubble-left": "12px",
        "--pet-bubble-max-width": "min(236px, calc(100% - 72px))",
        "--pet-drag-top": "92px",
        "--pet-drag-left": "88%",
        "--pet-static-left": "3%",
        "--pet-static-bottom": "0%",
        "--pet-static-width": "94%",
        "--pet-static-height": "90%",
        "--pet-debug-right": "10px",
        "--pet-debug-bottom": "10px",
    },
    "nativeHitTest": {
        "includeBubble": True,
        "includeControls": True,
    },
    "drag": {
        "portraitDrag": True,
    },
}


@dataclass(frozen=True)
class PetdeskResourceBundle:
    character_pack_id: str
    runtime_manifest: dict[str, Any]
    defaults: dict[str, str]
    static_image_handles: dict[tuple[str, str], str]
    first_static_image_handle: str


def resolve_petdesk_character_pack_id(character_resources: Any, raw_value: Any = "") -> str:
    requested = sanitize_character_pack_id(str(raw_value or ""))
    if requested and _has_character_manifest(character_resources, requested):
        return requested
    if requested:
        return ""

    lister = getattr(character_resources, "list_character_packs", None)
    if not callable(lister):
        return ""
    try:
        packs = lister()
    except Exception:
        return ""
    for pack in packs if isinstance(packs, list) else []:
        if not isinstance(pack, dict):
            continue
        pack_id = sanitize_character_pack_id(str(pack.get("pack_id") or pack.get("id") or ""))
        if pack_id and _has_character_manifest(character_resources, pack_id):
            return pack_id
    return ""


def build_petdesk_resource_bundle(character_resources: Any, character_pack_id: Any = "") -> PetdeskResourceBundle:
    pack_id = resolve_petdesk_character_pack_id(character_resources, character_pack_id)
    runtime_manifest: dict[str, Any] = {
        "schemaVersion": "pet.resource_manifest.v1",
        "staticImages": {},
        "live2dModels": {},
        "audio": {},
        "metadata": {
            "source": "akane_character_pack",
            "characterPackId": pack_id,
            "bridgeVersion": PETDESK_BRIDGE_VERSION,
        },
    }
    if not pack_id:
        return PetdeskResourceBundle(
            character_pack_id="",
            runtime_manifest=runtime_manifest,
            defaults={"outfit": "default", "emotion": "normal"},
            static_image_handles={},
            first_static_image_handle="",
        )

    manifest = _get_character_manifest(character_resources, pack_id)
    old_runtime = _build_old_runtime_manifest(manifest)
    defaults = _extract_defaults(old_runtime)
    handles: dict[tuple[str, str], str] = {}
    first_handle = ""
    static_images: dict[str, dict[str, str]] = {}
    pack_segment = safe_handle_segment(pack_id, "pack")

    characters = old_runtime.get("characters") if isinstance(old_runtime, dict) else {}
    outfits = characters.get("outfits") if isinstance(characters, dict) else []
    if not isinstance(outfits, list):
        outfits = []
    for outfit in outfits:
        if not isinstance(outfit, dict):
            continue
        outfit_id = clean_text(outfit.get("id") or outfit.get("name") or defaults["outfit"])
        if not outfit_id:
            continue
        outfit_segment = safe_handle_segment(outfit_id, "outfit")
        emotions = outfit.get("emotions")
        if not isinstance(emotions, list):
            continue
        for emotion in emotions:
            if not isinstance(emotion, dict):
                continue
            emotion_id = clean_text(emotion.get("id") or emotion.get("name") or defaults["emotion"])
            if not emotion_id:
                continue
            url = normalize_petdesk_resource_url(emotion.get("path"), pack_id=pack_id)
            if not url:
                continue
            emotion_segment = safe_handle_segment(emotion_id, "emotion")
            handle = f"{pack_segment}/static/{outfit_segment}/{emotion_segment}"
            static_images[handle] = {
                "kind": "static_image",
                "handle": handle,
                "url": url,
                "source": "akane_character_pack",
            }
            handles[(lookup_key(outfit_id), lookup_key(emotion_id))] = handle
            if not first_handle:
                first_handle = handle

    runtime_manifest["staticImages"] = static_images
    runtime_manifest["metadata"]["staticImageCount"] = len(static_images)
    runtime_manifest["metadata"]["defaults"] = dict(defaults)
    return PetdeskResourceBundle(
        character_pack_id=pack_id,
        runtime_manifest=runtime_manifest,
        defaults=defaults,
        static_image_handles=handles,
        first_static_image_handle=first_handle,
    )


def build_petdesk_display_envelope(
    frame: dict[str, Any] | None,
    *,
    bundle: PetdeskResourceBundle,
    resource_manifest: Any = None,
    turn_id: str = "",
    fallback_speech: str = "我在，petdesk runtime 已连接。",
) -> dict[str, Any]:
    source = frame if isinstance(frame, dict) else {}
    normalized = _normalize_frame_visual(source, resource_manifest=resource_manifest)
    character = normalized.get("character") if isinstance(normalized.get("character"), dict) else {}
    outfit = clean_text(character.get("outfit") or bundle.defaults.get("outfit") or "default") or "default"
    emotion = clean_text(normalized.get("emotion") or bundle.defaults.get("emotion") or "normal") or "normal"
    speech = clean_text(source.get("speech"))
    segments = normalize_speech_segments(source.get("speech_segments"), speech=speech)
    if not speech and segments:
        speech = "".join(segment["text"] for segment in segments)
    if not speech:
        speech = fallback_speech
        segments = [{"id": "seg_1", "text": fallback_speech}]
    elif not segments:
        segments = [{"id": "seg_1", "text": speech}]

    asset_handle = resolve_static_asset_handle(bundle, outfit=outfit, emotion=emotion)
    warnings: list[str] = []
    if not asset_handle:
        warnings.append("petdesk_static_asset_missing")

    envelope: dict[str, Any] = {
        "schemaVersion": PET_DISPLAY_SCHEMA_VERSION,
        "clientMode": "desktop",
        "speech": speech,
        "segments": segments,
        "visual": {
            "renderer": "static_portrait",
            "emotion": emotion,
            "outfit": outfit,
            "motion": resolve_motion(source, has_speech=bool(speech)),
        },
        "actions": [],
        "safety": {
            "status": "warning" if warnings else "ok",
            "warnings": warnings,
        },
        "metadata": {
            "source": "akane",
            "bridgeVersion": PETDESK_BRIDGE_VERSION,
            "characterPackId": bundle.character_pack_id,
        },
    }
    if turn_id:
        envelope["turnId"] = clean_text(turn_id)[:160]
    if asset_handle:
        envelope["visual"]["assetHandle"] = asset_handle

    status = clean_text(source.get("status"))
    if status:
        envelope["metadata"]["akaneStatus"] = status
    trace_id = clean_text(source.get("trace_id"))
    if trace_id:
        envelope["metadata"]["traceId"] = trace_id[:160]
    return envelope


def build_petdesk_health_payload(character_resources: Any, character_pack_id: Any = "") -> dict[str, Any]:
    pack_id = resolve_petdesk_character_pack_id(character_resources, character_pack_id)
    bundle = build_petdesk_resource_bundle(character_resources, pack_id)
    packs = _list_character_packs(character_resources)
    return {
        "ok": True,
        "status": "ready",
        "version": PETDESK_BRIDGE_VERSION,
        "snapshot": "/pet/snapshot",
        "turn": "/pet/turn",
        "resourceManifest": {
            "staticImageCount": len(bundle.runtime_manifest.get("staticImages") or {}),
            "characterPackId": bundle.character_pack_id,
            "prefix": PETDESK_CHARACTER_PACK_PREFIX,
        },
        "characterPacks": packs,
        "defaultCharacterPackId": pack_id,
        "runtimeEnv": build_petdesk_runtime_env(),
    }


def build_petdesk_runtime_env() -> dict[str, str]:
    return {
        "VITE_PETDESK_INTERACTION_PROFILE": "default",
        "VITE_PETDESK_INTERACTION_PROFILE_JSON": json.dumps(
            _AKANE_INTERACTION_PROFILE,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def build_akane_turn_payload(
    request_payload: dict[str, Any],
    *,
    query_params: dict[str, Any] | None = None,
    character_resources: Any = None,
) -> dict[str, Any]:
    metadata = request_payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    query = query_params or {}

    text = clean_text(request_payload.get("text"))
    session_id = (
        clean_text(metadata.get("user_id"))
        or clean_text(metadata.get("session_id"))
        or clean_text(query.get("user_id"))
        or clean_text(query.get("session_id"))
        or "petdesk"
    )
    profile_user_id = (
        clean_text(metadata.get("real_user_id"))
        or clean_text(metadata.get("profile_user_id"))
        or clean_text(metadata.get("profileUserId"))
        or clean_text(query.get("real_user_id"))
        or clean_text(query.get("profile_user_id"))
        or clean_text(query.get("profileUserId"))
        or session_id
    )
    raw_pack_id = (
        clean_text(metadata.get("character_pack_id"))
        or clean_text(metadata.get("characterPackId"))
        or clean_text(metadata.get("packId"))
        or clean_text(request_payload.get("character_pack_id"))
        or clean_text(request_payload.get("characterPackId"))
        or clean_text(query.get("character_pack_id"))
        or clean_text(query.get("characterPackId"))
        or clean_text(query.get("packId"))
    )
    character_pack_id = resolve_petdesk_character_pack_id(character_resources, raw_pack_id)
    return {
        "message": text,
        "user_id": session_id,
        "real_user_id": profile_user_id,
        "client_mode": "desktop_pet",
        "client_capabilities": [
            "speech_segments",
            "tts",
            "audio_playback",
            "static_sprite",
            "resource_manifest",
            "sessions",
        ],
        "character_pack_id": character_pack_id,
    }


def serialize_petdesk_sse(event: str, payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def normalize_petdesk_resource_url(raw_value: Any, *, pack_id: str) -> str:
    value = clean_text(raw_value).replace("\\", "/")
    if not value or _looks_like_url(value) or re.match(r"^[A-Za-z]:", value):
        return ""
    if "?" in value or "#" in value or value.startswith("//"):
        return ""

    legacy_prefix = f"{LEGACY_CHARACTER_PACK_PREFIX}/{pack_id}/"
    petdesk_prefix = f"{PETDESK_CHARACTER_PACK_PREFIX}/{pack_id}/"
    if value.startswith(legacy_prefix):
        value = f"{PETDESK_CHARACTER_PACK_PREFIX}/{pack_id}/{value[len(legacy_prefix) :]}"
    if not value.startswith(petdesk_prefix):
        return ""

    path = value.lstrip("/")
    segments = path.split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        return ""
    return "/" + "/".join(quote(segment, safe="-._~%") for segment in segments)


def resolve_static_asset_handle(
    bundle: PetdeskResourceBundle,
    *,
    outfit: str,
    emotion: str,
) -> str:
    candidates = [
        (outfit, emotion),
        (outfit, bundle.defaults.get("emotion", "")),
        (bundle.defaults.get("outfit", ""), emotion),
        (bundle.defaults.get("outfit", ""), bundle.defaults.get("emotion", "")),
    ]
    for raw_outfit, raw_emotion in candidates:
        handle = bundle.static_image_handles.get((lookup_key(raw_outfit), lookup_key(raw_emotion)))
        if handle:
            return handle
    return bundle.first_static_image_handle


def normalize_speech_segments(value: Any, *, speech: str = "") -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    if isinstance(value, list):
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                text = clean_text(item.get("text") or item.get("speech"))
                segment_id = clean_text(item.get("id")) or f"seg_{index}"
            else:
                text = clean_text(item)
                segment_id = f"seg_{index}"
            if text:
                segments.append({"id": segment_id[:160], "text": text[:20000]})
    if not segments and speech:
        segments.append({"id": "seg_1", "text": speech[:20000]})
    return segments[:80]


def resolve_motion(frame: dict[str, Any], *, has_speech: bool) -> str:
    candidates: list[Any] = [frame.get("motion")]
    for key in ("pet", "visual", "live2d"):
        section = frame.get(key)
        if isinstance(section, dict):
            candidates.append(section.get("motion"))
    for candidate in candidates:
        motion = clean_text(candidate)
        if motion in _ALLOWED_MOTIONS:
            return motion
    return "speaking" if has_speech else "idle"


def safe_handle_segment(value: Any, fallback_prefix: str) -> str:
    raw = clean_text(value)
    if _SAFE_HANDLE_SEGMENT_RE.fullmatch(raw):
        return raw[:80]
    digest_source = raw or fallback_prefix
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:12]
    ascii_hint = _UNSAFE_HANDLE_CHARS_RE.sub("-", raw).strip(".-_").lower()
    if ascii_hint and not ascii_hint[0].isalnum():
        ascii_hint = ""
    if ascii_hint:
        candidate = f"{ascii_hint[:48]}-{digest}"
        if _SAFE_HANDLE_SEGMENT_RE.fullmatch(candidate):
            return candidate[:80]
    prefix = _UNSAFE_HANDLE_CHARS_RE.sub("-", clean_text(fallback_prefix).lower()).strip(".-_") or "handle"
    if not prefix[0].isalnum():
        prefix = "handle"
    return f"{prefix[:48]}-{digest}"[:80]


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def lookup_key(value: Any) -> str:
    return clean_text(value).lower().replace("-", "_").replace(" ", "_")


def _extract_defaults(runtime_manifest: dict[str, Any]) -> dict[str, str]:
    defaults = runtime_manifest.get("defaults") if isinstance(runtime_manifest, dict) else {}
    defaults = defaults if isinstance(defaults, dict) else {}
    return {
        "outfit": clean_text(defaults.get("desktop_pet_outfit") or defaults.get("outfit") or "default") or "default",
        "emotion": clean_text(defaults.get("desktop_pet_emotion") or defaults.get("emotion") or "normal") or "normal",
    }


def _normalize_frame_visual(frame: dict[str, Any], *, resource_manifest: Any = None) -> dict[str, Any]:
    normalized = copy.deepcopy(frame)
    normalizer = getattr(resource_manifest, "normalize_visual_output", None)
    if not callable(normalizer):
        return normalized
    try:
        return normalizer(normalized)
    except Exception:
        return normalized


def _get_character_manifest(character_resources: Any, pack_id: str) -> Any:
    getter = getattr(character_resources, "get_manifest", None)
    if not callable(getter) or not pack_id:
        return None
    try:
        return getter(pack_id)
    except Exception:
        return None


def _has_character_manifest(character_resources: Any, pack_id: str) -> bool:
    return _get_character_manifest(character_resources, pack_id) is not None


def _build_old_runtime_manifest(manifest: Any) -> dict[str, Any]:
    if manifest is None:
        return {}
    refresher = getattr(manifest, "refresh", None)
    if callable(refresher):
        try:
            refresher()
        except Exception:
            pass
    builder = getattr(manifest, "build_runtime_manifest", None)
    if callable(builder):
        try:
            result = builder()
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}
    getter = getattr(manifest, "get_manifest", None)
    if callable(getter):
        try:
            result = getter()
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}
    return {}


def _list_character_packs(character_resources: Any) -> list[dict[str, str]]:
    lister = getattr(character_resources, "list_character_packs", None)
    if not callable(lister):
        return []
    try:
        packs = lister()
    except Exception:
        return []
    result: list[dict[str, str]] = []
    for item in packs if isinstance(packs, list) else []:
        if not isinstance(item, dict):
            continue
        pack_id = sanitize_character_pack_id(str(item.get("pack_id") or item.get("id") or ""))
        if not pack_id:
            continue
        result.append(
            {
                "pack_id": pack_id,
                "id": pack_id,
                "name": clean_text(item.get("name")) or pack_id,
                "app_name": clean_text(item.get("app_name")) or clean_text(item.get("name")) or pack_id,
                "user_title": clean_text(item.get("user_title")) or "用户",
            }
        )
    return result


def _looks_like_url(value: str) -> bool:
    return re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value) is not None
