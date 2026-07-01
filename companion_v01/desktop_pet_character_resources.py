from __future__ import annotations

from charpack_core.character_resources import (
    ALIAS_PROMPT_LIMIT,
    CLICK_LINE_PROMPT_LIMIT,
    PACK_ID_PATTERN,
    PERSONA_PROMPT_CHAR_LIMIT,
    CharacterPackResourceService,
    DesktopPetCharacterResourceService,
    _as_dict,
    _clean_text,
    _coerce_alias_map,
    _coerce_click_lines,
    _coerce_string_list,
    _collect_available_emotions,
    _emotion_alias_candidates,
    _emotion_lookup_key,
    _expand_qq_delivery_emotion_mface_aliases,
    _file_mtime,
    _format_alias_lines,
    _format_persona_form,
    _load_json,
    _read_text,
    _resolve_manifest_asset_file,
    _truncate_text,
    sanitize_character_pack_id,
)

__all__ = [
    "ALIAS_PROMPT_LIMIT",
    "CLICK_LINE_PROMPT_LIMIT",
    "PACK_ID_PATTERN",
    "PERSONA_PROMPT_CHAR_LIMIT",
    "CharacterPackResourceService",
    "DesktopPetCharacterResourceService",
    "sanitize_character_pack_id",
]
