"""Host limits and safe public identifiers."""

from __future__ import annotations

from typing import Any
import hashlib
import re

from ..plugin_api import is_valid_plugin_id


_SAFE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")


_HOST_AVAILABLE_STATES = frozenset({"active", "degraded"})


_MAX_ADAPTERS_PER_PLUGIN = 16


_MAX_CAPABILITIES_PER_PLUGIN = 64


_MAX_QQ_COMMANDS_PER_PLUGIN = 32


_MAX_QQ_COMMAND_LENGTH = 64


_QQ_COMMAND_PATTERN = re.compile(r"^/[^\s/]{1,63}$")


_MAX_PROMPT_BLOCKS_PER_PLUGIN = 8


_MAX_PROMPT_BLOCK_CHARS = 16_000


_MAX_PROMPT_BLOCK_TOTAL_CHARS = 32_000


_MAX_PERMISSIONS_PER_PLUGIN = 32


_PROMPT_BLOCK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


_MAX_BACKGROUND_SERVICE_ID_CHARS = 64


_BACKGROUND_SERVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _is_safe_version(version: Any) -> bool:
    return isinstance(version, str) and _SAFE_VERSION_PATTERN.fullmatch(version) is not None


def _public_plugin_id(plugin_id: Any) -> str:
    return plugin_id if is_valid_plugin_id(plugin_id) else "invalid-plugin-id"


def _plugin_skill_mount_name(plugin_id: str, skill_name: str) -> str:
    material = f"{plugin_id}\0{skill_name}".encode("utf-8")
    return f"plugin_skill_{hashlib.sha256(material).hexdigest()[:16]}"
