from __future__ import annotations

import re
from typing import Any, Callable


def qq_attachment_ready_wait_seconds(context: Any, config_module: Any) -> float:
    base_wait = max(0.0, float(getattr(config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0))
    attachments = list(getattr(context, "attachments", None) or [])
    has_image = any(
        isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image" for item in attachments
    )
    if not has_image:
        return base_wait

    vision_timeout = max(1.0, float(getattr(config_module, "VISION_REQUEST_TIMEOUT", 60.0) or 60.0))
    return max(base_wait, vision_timeout + 5.0)


def qq_pending_image_attachment_ids(
    registered_items: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    wait_result: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(wait_result, dict):
        return []
    pending_ids = {
        str(item or "").strip()
        for item in list(wait_result.get("pending") or [])
        if str(item or "").strip()
    }
    if not pending_ids:
        return []

    kinds_by_id = wait_result.get("kinds_by_id") if isinstance(wait_result.get("kinds_by_id"), dict) else {}
    image_ids_from_wait = [
        item_id
        for item_id in pending_ids
        if str(kinds_by_id.get(item_id) or "").strip().lower() == "image"
    ]
    if image_ids_from_wait:
        return sorted(image_ids_from_wait)

    image_ids: list[str] = []
    for item in registered_items or []:
        if not isinstance(item, dict):
            continue
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id or attachment_id not in pending_ids:
            continue
        if str(item.get("kind") or "").strip().lower() == "image":
            image_ids.append(attachment_id)
    return image_ids


def build_qq_resource_manifest_builder(engine: Any, context: Any) -> Callable[[str], dict[str, Any]]:
    def _builder(character_pack_id: str) -> dict[str, Any]:
        builder = getattr(engine, "build_resource_manifest", None)
        if builder is None:
            return {}
        try:
            manifest = builder(
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                client_mode="qq_text",
                character_pack_id=str(character_pack_id or "").strip(),
            )
        except Exception:
            return {}
        return manifest if isinstance(manifest, dict) else {}

    return _builder


def apply_qq_current_outfit_visual(turn_payload: dict[str, Any], *, qq_gateway: Any, context: Any, engine: Any) -> None:
    character_pack_id = str(turn_payload.get("character_pack_id") or "").strip()
    if not character_pack_id:
        return
    manifest = build_qq_resource_manifest_builder(engine, context)(character_pack_id)
    characters = manifest.get("characters") if isinstance(manifest.get("characters"), dict) else {}
    outfits = characters.get("outfits") if isinstance(characters, dict) else []
    defaults = manifest.get("defaults") if isinstance(manifest.get("defaults"), dict) else {}
    clients = manifest.get("clients") if isinstance(manifest.get("clients"), dict) else {}
    desktop = clients.get("desktop_pet") if isinstance(clients.get("desktop_pet"), dict) else {}
    requested_outfit = ""
    resolver = getattr(qq_gateway, "resolve_session_outfit_id", None)
    if resolver is not None:
        try:
            requested_outfit = str(resolver(str(getattr(context, "session_id", "") or "")) or "").strip()
        except Exception:
            requested_outfit = ""
    outfit = (
        _find_manifest_entry(outfits, requested_outfit)
        or _find_manifest_entry(outfits, desktop.get("default_outfit"))
        or _find_manifest_entry(outfits, defaults.get("desktop_pet_outfit"))
        or _find_manifest_entry(outfits, defaults.get("outfit"))
        or (outfits[0] if isinstance(outfits, list) and outfits and isinstance(outfits[0], dict) else None)
    )
    if not isinstance(outfit, dict):
        return
    outfit_id = str(outfit.get("id") or outfit.get("name") or "").strip()
    if not outfit_id:
        return
    emotions = outfit.get("emotions") if isinstance(outfit.get("emotions"), list) else []
    default_emotion = str(defaults.get("emotion") or defaults.get("desktop_pet_emotion") or "").strip()
    emotion = _find_manifest_entry(emotions, default_emotion) or (
        emotions[0] if emotions and isinstance(emotions[0], dict) else {}
    )
    emotion_id = (
        str(emotion.get("id") or emotion.get("name") or default_emotion or "normal").strip()
        if isinstance(emotion, dict)
        else "normal"
    )
    turn_payload["current_visual"] = {
        "emotion": emotion_id or "normal",
        "character": {"outfit": outfit_id},
    }


def qq_current_outfit_id_from_turn_payload(turn_payload: dict[str, Any]) -> str:
    current_visual = turn_payload.get("current_visual") if isinstance(turn_payload, dict) else {}
    current_character = (
        current_visual.get("character")
        if isinstance(current_visual, dict) and isinstance(current_visual.get("character"), dict)
        else {}
    )
    return str(current_character.get("outfit") or "").strip()


def _find_manifest_entry(items: Any, value: Any) -> dict[str, Any] | None:
    target = _manifest_lookup_key(value)
    if not target:
        return None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        candidates = [item.get("id"), item.get("name"), *list(item.get("aliases") or [])]
        if any(_manifest_lookup_key(candidate) == target for candidate in candidates):
            return item
    return None


def _manifest_lookup_key(value: Any) -> str:
    return re.sub(r"[\s_\-.]+", "", str(value or "").strip().lower())
