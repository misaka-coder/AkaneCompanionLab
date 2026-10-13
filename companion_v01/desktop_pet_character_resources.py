from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from charpack_core.character_resources import (
    ALIAS_PROMPT_LIMIT,
    CLICK_LINE_PROMPT_LIMIT,
    PACK_ID_PATTERN,
    PERSONA_PROMPT_CHAR_LIMIT,
    CharacterPackResourceService,
    DesktopPetCharacterResourceService,
    sanitize_character_pack_id,
)


MAX_CHARACTER_METADATA_BYTES = 1024 * 1024


def load_character_care_config(
    service: DesktopPetCharacterResourceService | None,
    character_pack_id: str,
) -> dict[str, Any]:
    """Read one pack's declarative Care config through a safe host adapter.

    ``charpack-core`` remains the owner of pack-id sanitizing and the resource
    service. Akane owns interpreting the declarative Care block as product
    runtime policy. Paths and parse failures never escape this boundary.
    """

    return _load_character_block(service, character_pack_id, "care")


def load_character_speech_config(service, character_pack_id: str) -> dict[str, Any]:
    """Optional host speech policy, read through the same safe pack boundary."""
    return _load_character_block(service, character_pack_id, "speech_output")


def load_character_dialogue_services(service, character_pack_id: str) -> dict[str, Any]:
    """Optional service presentation; pack metadata cannot grant capabilities."""
    return _load_character_block(service, character_pack_id, "dialogue_services", strict=True)


def build_character_care_preference_context(service, character_pack_id: str) -> str:
    """Stable, small character facts shared by QQ, desktop and compaction."""
    care = load_character_care_config(service, character_pack_id)
    preferences = care.get("care_preferences", care.get("carePreferences"))
    if not isinstance(preferences, dict):
        return ""
    labels = {
        "sweet": "甜食", "tea": "茶", "traditional_snack": "传统点心",
        "shrine": "神社风味", "offering": "供品",
    }

    def values(snake: str, camel: str, *, tags: bool = False) -> list[str]:
        raw = preferences.get(snake, preferences.get(camel))
        if not isinstance(raw, list):
            return []
        cleaned = []
        for value in raw[:16]:
            if isinstance(value, str):
                text = " ".join(value.split())[:40]
                if tags:
                    text = labels.get(text, text)
                if text and text not in cleaned:
                    cleaned.append(text)
        return cleaned

    lines = []
    for label, snake, camel, tags in (
        ("喜欢的食物或礼物", "favorite_items", "favoriteItems", False),
        ("不喜欢的食物或礼物", "disliked_items", "dislikedItems", False),
        ("喜欢的类型", "favorite_tags", "favoriteTags", True),
        ("不喜欢的类型", "disliked_tags", "dislikedTags", True),
    ):
        items = values(snake, camel, tags=tags)
        if items:
            lines.append(f"{label}：{'、'.join(items)}。")
    return "【养成偏好】\n" + "\n".join(lines) if lines else ""


def load_character_care_shop_items(service, character_pack_id: str) -> list[dict[str, Any]]:
    """Expose the same declarative shelf and channel flags as the desktop editor."""
    from .care_runtime import MAX_DESKTOP_INVENTORY_ITEMS

    care = load_character_care_config(service, character_pack_id)
    raw_items = care.get("shop_items", care.get("shopItems"))
    if not isinstance(raw_items, list):
        return []
    items = []
    for raw in raw_items[:MAX_DESKTOP_INVENTORY_ITEMS]:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
            continue
        item = dict(raw)
        if "usable_in" not in item and "usableIn" in item:
            item["usable_in"] = item["usableIn"]
        items.append(item)
    return items


def _load_character_block(service, character_pack_id: str, block: str, *, strict: bool = False) -> dict[str, Any]:
    pack_id = sanitize_character_pack_id(character_pack_id)
    characters_dir = getattr(service, "characters_dir", None)
    if not pack_id or characters_dir is None:
        return {}
    try:
        root = Path(characters_dir).resolve()
        resolver = getattr(service, "_resolve_pack_dir", None)
        pack_dir = resolver(pack_id) if callable(resolver) else root / pack_id
        if pack_dir is None:
            raise ValueError("character_pack_unavailable")
        pack_dir = Path(pack_dir).resolve()
        pack_dir.relative_to(root)
        metadata_path = (pack_dir / "character.json").resolve()
        metadata_path.relative_to(pack_dir)
        metadata_path.relative_to(root)
        if not metadata_path.is_file() or metadata_path.stat().st_size > MAX_CHARACTER_METADATA_BYTES:
            raise ValueError("character_metadata_unavailable")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        if strict:
            raise ValueError("service_customization_invalid") from None
        return {}
    if strict and (not isinstance(payload, dict) or (block in payload and not isinstance(payload[block], dict))):
        raise ValueError("service_customization_invalid")
    value = payload.get(block) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else {}

__all__ = [
    "ALIAS_PROMPT_LIMIT",
    "CLICK_LINE_PROMPT_LIMIT",
    "PACK_ID_PATTERN",
    "PERSONA_PROMPT_CHAR_LIMIT",
    "CharacterPackResourceService",
    "build_character_care_preference_context",
    "DesktopPetCharacterResourceService",
    "MAX_CHARACTER_METADATA_BYTES",
    "load_character_care_config",
    "load_character_care_shop_items",
    "load_character_dialogue_services",
    "load_character_speech_config",
    "sanitize_character_pack_id",
]
