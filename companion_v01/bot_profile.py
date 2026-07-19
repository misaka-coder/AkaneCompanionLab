"""Validated product configuration for one Akane Host and its Bots.

BotConfig is the product-level authority for multi-Bot composition.  It keeps
only safe ids and feature selections; deployment secrets and absolute paths
remain outside this file.  The legacy InstanceManifest is adapted into this
shape during the migration, never maintained as a second writable authority.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .plugin_api import is_valid_plugin_id


BOT_PROFILE_SCHEMA_VERSION = 1
BOT_PROFILE_FILENAME = "bots.toml"
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ROOT_FIELDS = frozenset({"schema_version", "default_bot_id", "bots"})
_BOT_FIELDS = frozenset(
    {
        "bot_id",
        "enabled",
        "display_name",
        "character_pack_id",
        "memory_space_id",
        "model_profile_ref",
        "capability_profile_ref",
        "care_enabled",
        "channels",
        "plugins",
    }
)
_CHANNEL_FIELDS = frozenset({"qq"})
_QQ_FIELDS = frozenset({"enabled", "profile_ref"})
_PLUGIN_FIELDS = frozenset({"id", "enabled"})
_MAX_BOTS = 64
_MAX_PLUGINS = 32


class BotProfileError(ValueError):
    """Structured safe configuration failure without values or file paths."""

    def __init__(self, *, status: str = "invalid_config", reason: str, field: str = "") -> None:
        self.status = str(status or "invalid_config")
        self.reason = str(reason or "bot_profile_invalid")
        self.field = str(field or "")
        super().__init__(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "status": self.status, "reason": self.reason}
        if self.field:
            payload["field"] = self.field
        return payload


@dataclass(frozen=True, slots=True)
class BotPluginSelection:
    plugin_id: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class BotQQChannelConfig:
    enabled: bool = False
    profile_ref: str = ""


@dataclass(frozen=True, slots=True)
class BotConfig:
    schema_version: int
    bot_id: str
    enabled: bool
    display_name: str
    character_pack_id: str
    memory_space_id: str
    model_profile_ref: str
    capability_profile_ref: str
    care_enabled: bool
    qq: BotQQChannelConfig
    plugins: tuple[BotPluginSelection, ...]
    source: str = "bot_profile"

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != BOT_PROFILE_SCHEMA_VERSION:
            _fail("unsupported_schema_version", field="schema_version", status="incompatible")
        _safe_id(self.bot_id, field="bot_id")
        _bool(self.enabled, field="enabled")
        _display_name(self.display_name, field="display_name")
        _optional_safe_id(self.character_pack_id, field="character_pack_id")
        _safe_id(self.memory_space_id, field="memory_space_id")
        _safe_id(self.model_profile_ref, field="model_profile_ref")
        _safe_id(self.capability_profile_ref, field="capability_profile_ref")
        _bool(self.care_enabled, field="care_enabled")
        if not isinstance(self.qq, BotQQChannelConfig):
            _fail("qq_channel_invalid", field="channels.qq")
        _bool(self.qq.enabled, field="channels.qq.enabled")
        _optional_safe_id(self.qq.profile_ref, field="channels.qq.profile_ref")
        if self.qq.enabled and not self.qq.profile_ref:
            _fail("channel_profile_ref_required", field="channels.qq.profile_ref")
        if not isinstance(self.plugins, tuple) or len(self.plugins) > _MAX_PLUGINS:
            _fail("plugins_invalid", field="plugins")
        seen_plugin_ids: set[str] = set()
        for index, item in enumerate(self.plugins):
            if not isinstance(item, BotPluginSelection) or not is_valid_plugin_id(item.plugin_id):
                _fail("invalid_plugin_id", field=f"plugins.{index}.id")
            _bool(item.enabled, field=f"plugins.{index}.enabled")
            if item.plugin_id in seen_plugin_ids:
                _fail("duplicate_plugin_id", field=f"plugins.{index}.id")
            seen_plugin_ids.add(item.plugin_id)
        if self.source not in {"bot_profile", "instance_adapter"}:
            _fail("invalid_config_source", field="source")

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "enabled": self.enabled,
            "display_name": self.display_name,
            "character_pack_id": self.character_pack_id,
            "memory_space_id": self.memory_space_id,
            "model_profile_ref": self.model_profile_ref,
            "capability_profile_ref": self.capability_profile_ref,
            "care_enabled": self.care_enabled,
            "channels": {"qq": {"enabled": self.qq.enabled, "profile_ref": self.qq.profile_ref}},
            "plugins": [{"id": item.plugin_id, "enabled": item.enabled} for item in self.plugins],
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class BotHostProfile:
    schema_version: int
    default_bot_id: str
    bots: tuple[BotConfig, ...]

    @property
    def enabled_bots(self) -> tuple[BotConfig, ...]:
        return tuple(bot for bot in self.bots if bot.enabled)

    def require(self, bot_id: str) -> BotConfig:
        normalized = _safe_id(bot_id, field="bot_id")
        for bot in self.bots:
            if bot.bot_id == normalized:
                return bot
        _fail("bot_not_configured", field="bot_id", status="not_found")


def parse_bot_config(payload: Any, *, field_prefix: str = "bot") -> BotConfig:
    if not isinstance(payload, Mapping):
        _fail("bot_must_be_table", field=field_prefix)
    _reject_unknown(payload, _BOT_FIELDS, field=field_prefix)

    bot_id = _safe_id(payload.get("bot_id"), field=f"{field_prefix}.bot_id")
    enabled = _bool(payload.get("enabled", True), field=f"{field_prefix}.enabled")
    display_name = _display_name(payload.get("display_name", bot_id), field=f"{field_prefix}.display_name")
    character_pack_id = _optional_safe_id(
        payload.get("character_pack_id", ""),
        field=f"{field_prefix}.character_pack_id",
    )
    memory_space_id = _safe_id(
        payload.get("memory_space_id", bot_id),
        field=f"{field_prefix}.memory_space_id",
    )
    model_profile_ref = _safe_id(
        payload.get("model_profile_ref", "default"),
        field=f"{field_prefix}.model_profile_ref",
    )
    capability_profile_ref = _safe_id(
        payload.get("capability_profile_ref", "default"),
        field=f"{field_prefix}.capability_profile_ref",
    )
    care_enabled = _bool(payload.get("care_enabled", True), field=f"{field_prefix}.care_enabled")

    channels_payload = payload.get("channels", {})
    if not isinstance(channels_payload, Mapping):
        _fail("channels_must_be_table", field=f"{field_prefix}.channels")
    _reject_unknown(channels_payload, _CHANNEL_FIELDS, field=f"{field_prefix}.channels")
    qq_payload = channels_payload.get("qq", {})
    if not isinstance(qq_payload, Mapping):
        _fail("qq_channel_must_be_table", field=f"{field_prefix}.channels.qq")
    _reject_unknown(qq_payload, _QQ_FIELDS, field=f"{field_prefix}.channels.qq")
    qq_enabled = _bool(qq_payload.get("enabled", False), field=f"{field_prefix}.channels.qq.enabled")
    qq_profile_ref = _optional_safe_id(
        qq_payload.get("profile_ref", ""),
        field=f"{field_prefix}.channels.qq.profile_ref",
    )
    if qq_enabled and not qq_profile_ref:
        _fail("channel_profile_ref_required", field=f"{field_prefix}.channels.qq.profile_ref")

    raw_plugins = payload.get("plugins", [])
    if not isinstance(raw_plugins, list):
        _fail("plugins_must_be_array", field=f"{field_prefix}.plugins")
    if len(raw_plugins) > _MAX_PLUGINS:
        _fail("too_many_plugins", field=f"{field_prefix}.plugins")
    plugins: list[BotPluginSelection] = []
    seen_plugin_ids: set[str] = set()
    for index, raw_plugin in enumerate(raw_plugins):
        plugin_field = f"{field_prefix}.plugins.{index}"
        if not isinstance(raw_plugin, Mapping):
            _fail("plugin_must_be_table", field=plugin_field)
        _reject_unknown(raw_plugin, _PLUGIN_FIELDS, field=plugin_field)
        plugin_id = raw_plugin.get("id")
        if not is_valid_plugin_id(plugin_id):
            _fail("invalid_plugin_id", field=f"{plugin_field}.id")
        if plugin_id in seen_plugin_ids:
            _fail("duplicate_plugin_id", field=f"{plugin_field}.id")
        seen_plugin_ids.add(plugin_id)
        plugins.append(
            BotPluginSelection(
                plugin_id=str(plugin_id),
                enabled=_bool(raw_plugin.get("enabled"), field=f"{plugin_field}.enabled"),
            )
        )

    return BotConfig(
        schema_version=BOT_PROFILE_SCHEMA_VERSION,
        bot_id=bot_id,
        enabled=enabled,
        display_name=display_name,
        character_pack_id=character_pack_id,
        memory_space_id=memory_space_id,
        model_profile_ref=model_profile_ref,
        capability_profile_ref=capability_profile_ref,
        care_enabled=care_enabled,
        qq=BotQQChannelConfig(enabled=qq_enabled, profile_ref=qq_profile_ref),
        plugins=tuple(plugins),
    )


def parse_bot_host_profile(payload: Any) -> BotHostProfile:
    if not isinstance(payload, Mapping):
        _fail("profile_root_must_be_table")
    _reject_unknown(payload, _ROOT_FIELDS, field="")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _fail("schema_version_must_be_integer", field="schema_version")
    if schema_version != BOT_PROFILE_SCHEMA_VERSION:
        _fail("unsupported_schema_version", field="schema_version", status="incompatible")

    raw_bots = payload.get("bots")
    if not isinstance(raw_bots, list):
        _fail("bots_must_be_array", field="bots")
    if not raw_bots:
        _fail("bot_required", field="bots")
    if len(raw_bots) > _MAX_BOTS:
        _fail("too_many_bots", field="bots")

    bots = tuple(parse_bot_config(item, field_prefix=f"bots.{index}") for index, item in enumerate(raw_bots))
    bot_ids: set[str] = set()
    memory_spaces: set[str] = set()
    for index, bot in enumerate(bots):
        if bot.bot_id in bot_ids:
            _fail("duplicate_bot_id", field=f"bots.{index}.bot_id")
        if bot.memory_space_id in memory_spaces:
            _fail("duplicate_memory_space_id", field=f"bots.{index}.memory_space_id")
        bot_ids.add(bot.bot_id)
        memory_spaces.add(bot.memory_space_id)

    enabled_ids = {bot.bot_id for bot in bots if bot.enabled}
    if not enabled_ids:
        _fail("enabled_bot_required", field="bots")
    default_bot_id = _safe_id(payload.get("default_bot_id"), field="default_bot_id")
    if default_bot_id not in enabled_ids:
        _fail("default_bot_must_be_enabled", field="default_bot_id")
    return BotHostProfile(
        schema_version=schema_version,
        default_bot_id=default_bot_id,
        bots=bots,
    )


def load_bot_host_profile(path: Path) -> BotHostProfile:
    try:
        with Path(path).open("rb") as profile_file:
            payload = tomllib.load(profile_file)
    except FileNotFoundError:
        _fail("bot_profile_not_found", status="unavailable")
    except (OSError, tomllib.TOMLDecodeError):
        _fail("bot_profile_unreadable")
    return parse_bot_host_profile(payload)


def resolve_bot_data_root(host_data_root: Path, bot_config: BotConfig) -> Path:
    """Resolve a Bot-owned root from a safe memory-space id."""

    if not isinstance(bot_config, BotConfig):
        _fail("bot_config_invalid")
    try:
        host_root = Path(host_data_root).expanduser().resolve()
        bots_root = (host_root / "bots").resolve()
        bot_root = (bots_root / bot_config.memory_space_id).resolve()
        bots_root.relative_to(host_root)
        bot_root.relative_to(bots_root)
    except (OSError, ValueError):
        _fail("bot_data_root_unavailable", status="unavailable")
    return bot_root


def bot_config_from_instance_context(context: Any) -> BotConfig:
    """Thin read adapter for existing InstanceManifest deployments."""

    bot_id = _safe_id(getattr(context, "instance_id", ""), field="instance_id")
    channels = getattr(context, "channels", None)
    qq = getattr(channels, "qq", None)
    features = getattr(context, "features", None)
    raw_plugins = tuple(getattr(context, "plugins", ()) or ())
    return BotConfig(
        schema_version=BOT_PROFILE_SCHEMA_VERSION,
        bot_id=bot_id,
        enabled=True,
        display_name=bot_id,
        character_pack_id=_optional_safe_id(
            getattr(context, "character_pack_id", ""),
            field="character_pack_id",
        ),
        memory_space_id=bot_id,
        model_profile_ref="default",
        capability_profile_ref="default",
        care_enabled=bool(getattr(features, "care", True)),
        qq=BotQQChannelConfig(
            enabled=bool(getattr(qq, "enabled", False)),
            profile_ref=_optional_safe_id(getattr(qq, "profile_ref", ""), field="channels.qq.profile_ref"),
        ),
        plugins=tuple(
            BotPluginSelection(
                plugin_id=str(getattr(item, "plugin_id", "")),
                enabled=bool(getattr(item, "enabled", False)),
            )
            for item in raw_plugins
        ),
        source="instance_adapter",
    )


def _reject_unknown(payload: Mapping[str, Any], allowed: frozenset[str], *, field: str) -> None:
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        _fail("unsupported_bot_profile_field", field=f"{field}.{unknown[0]}" if field else unknown[0])


def _safe_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_safe_id", field=field)
    normalized = value.strip()
    if normalized != value or _SAFE_ID_PATTERN.fullmatch(normalized) is None:
        _fail("invalid_safe_id", field=field)
    return normalized


def _optional_safe_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_safe_id", field=field)
    return "" if value == "" else _safe_id(value, field=field)


def _bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        _fail("boolean_required", field=field)
    return value


def _display_name(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        _fail("display_name_must_be_string", field=field)
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > 80 or any(ord(char) < 32 for char in normalized):
        _fail("invalid_display_name", field=field)
    return normalized


def _fail(reason: str, *, field: str = "", status: str = "invalid_config") -> None:
    raise BotProfileError(status=status, reason=reason, field=field)


__all__ = [
    "BOT_PROFILE_FILENAME",
    "BOT_PROFILE_SCHEMA_VERSION",
    "BotConfig",
    "BotHostProfile",
    "BotPluginSelection",
    "BotProfileError",
    "BotQQChannelConfig",
    "bot_config_from_instance_context",
    "load_bot_host_profile",
    "parse_bot_config",
    "parse_bot_host_profile",
    "resolve_bot_data_root",
]
