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

    pack_id = sanitize_character_pack_id(character_pack_id)
    characters_dir = getattr(service, "characters_dir", None)
    if not pack_id or characters_dir is None:
        return {}
    try:
        root = Path(characters_dir).resolve()
        metadata_path = (root / pack_id / "character.json").resolve()
        metadata_path.relative_to(root)
        if not metadata_path.is_file() or metadata_path.stat().st_size > MAX_CHARACTER_METADATA_BYTES:
            return {}
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    care = payload.get("care") if isinstance(payload, dict) else None
    return care if isinstance(care, dict) else {}

__all__ = [
    "ALIAS_PROMPT_LIMIT",
    "CLICK_LINE_PROMPT_LIMIT",
    "PACK_ID_PATTERN",
    "PERSONA_PROMPT_CHAR_LIMIT",
    "CharacterPackResourceService",
    "DesktopPetCharacterResourceService",
    "MAX_CHARACTER_METADATA_BYTES",
    "load_character_care_config",
    "sanitize_character_pack_id",
]
