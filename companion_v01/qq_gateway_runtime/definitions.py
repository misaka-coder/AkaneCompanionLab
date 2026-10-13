"""Shared QQ gateway limits and compatibility constants."""
from __future__ import annotations
import re
from ..client_protocol import QQ_TEXT_DEFAULT_CAPABILITIES


QQ_TEXT_CAPABILITIES = (*QQ_TEXT_DEFAULT_CAPABILITIES, "speech_units_v1")
QQ_MUSIC_PLATFORM_TO_ONEBOT = {
    "netease_music": "163",
}
QQ_CHARACTER_PACK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
QQ_CHARACTER_BUILTIN_IDS = {"akane", "builtin", "built-in", "内置", "内置akane"}
QQ_INLINE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
QQ_INLINE_FILE_MAX_BYTES = 4 * 1024 * 1024
QQ_FILE_NAME_MAX_UTF8_BYTES = 96
QQ_REPLY_MODES = {"text", "voice", "both"}
QQ_REPLY_MODE_LABELS = {
    "text": "文字模式",
    "voice": "语音模式",
    "both": "双发模式",
}
QQ_GATEWAY_STATE_SCHEMA_VERSION = "akane.qq_gateway_state.v1"
_QTY_RE = re.compile(r"^(.+?)\s*[Xx×*×](\d+)$")
VALID_USABLE_IN: frozenset[str] = frozenset({"desktop_pet", "qq"})
