"""Pure normalization at the QQ configuration and file boundaries."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from companion_v01.channels.qq.constants import QQ_CHARACTER_PACK_ID_RE, QQ_FILE_NAME_MAX_UTF8_BYTES, QQ_REPLY_MODES
from companion_v01.model_service_config import normalize_provider_model_id


def _safe_qq_file_name(value: str, *, fallback: str) -> str:
    clean_name = Path(str(value or fallback).replace("\\", "/")).name.strip()
    if not clean_name:
        clean_name = Path(str(fallback or "file").replace("\\", "/")).name.strip() or "file"
    if len(clean_name.encode("utf-8")) <= QQ_FILE_NAME_MAX_UTF8_BYTES:
        return clean_name

    suffix = Path(clean_name).suffix
    stem = clean_name[: -len(suffix)] if suffix else clean_name
    marker = "…"
    fixed_bytes = len((marker + suffix).encode("utf-8"))
    stem_budget = max(1, QQ_FILE_NAME_MAX_UTF8_BYTES - fixed_bytes)
    kept: list[str] = []
    used_bytes = 0
    for character in stem:
        character_bytes = len(character.encode("utf-8"))
        if used_bytes + character_bytes > stem_budget:
            break
        kept.append(character)
        used_bytes += character_bytes
    short_stem = "".join(kept).rstrip(" ._-") or "file"
    return f"{short_stem}{marker}{suffix}"

def _safe_character_pack_id(value: Any) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not QQ_CHARACTER_PACK_ID_RE.fullmatch(pack_id):
        return ""
    return pack_id

def _safe_qq_session_key(value: Any) -> str:
    key = str(value or "").strip()
    if key == "master":
        return key
    if re.fullmatch(r"qq_pri_\d+", key):
        return key
    if re.fullmatch(r"qq_group_shared_\d+", key):
        return key
    return ""

def _safe_outfit_id(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip('`\'"""‘’')
    text = text.rstrip("。.!！?？,，;；")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > 80:
        return ""
    if any(ord(char) < 0x20 or char in {"/", "\\", "\x7f"} for char in text):
        return ""
    if text in {".", ".."}:
        return ""
    return text

def _safe_chat_model_id(value: Any) -> str:
    return normalize_provider_model_id(value)

def _chat_model_route_prefix(value: Any) -> str:
    model = _safe_chat_model_id(value)
    if not model.startswith("["):
        return ""
    end = model.find("]")
    if end <= 1:
        return ""
    return model[: end + 1]

def _chat_model_base_id(value: Any) -> str:
    model = _safe_chat_model_id(value)
    prefix = _chat_model_route_prefix(model)
    return model[len(prefix) :] if prefix else model

def _outfit_lookup_key(value: Any) -> str:
    return re.sub(r"[\s_\-.]+", "", _safe_outfit_id(value).lower())

def _safe_reply_mode(value: Any, *, default: str = "text") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "text": "text",
        "文字": "text",
        "文本": "text",
        "voice": "voice",
        "audio": "voice",
        "record": "voice",
        "语音": "voice",
        "both": "both",
        "all": "both",
        "text_voice": "both",
        "voice_text": "both",
        "文字语音": "both",
        "双发": "both",
        "auto": "text",
        "automatic": "text",
        "自动": "text",
    }
    fallback = "text" if default == "auto" else default if default in QQ_REPLY_MODES or default == "" else "text"
    if not text:
        return fallback
    mode = aliases.get(text, "")
    if mode:
        return mode
    return fallback
