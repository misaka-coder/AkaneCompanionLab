"""Deployment-owned QQ channel profiles selected by safe Bot profile refs."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping
from urllib.parse import urlsplit


QQ_CHANNEL_PROFILES_SCHEMA_VERSION = 1
QQ_CHANNEL_PROFILES_RELATIVE_PATH = Path("secrets") / "qq_profiles.toml"
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ROOT_FIELDS = frozenset({"schema_version", "profiles"})
_PROFILE_FIELDS = frozenset(
    {
        "profile_ref",
        "bot_qq",
        "onebot_http_url",
        "webhook_secret",
        "onebot_access_token",
        "onebot_shared_data_root",
    }
)
_MAX_PROFILES = 64


class QQChannelProfileError(ValueError):
    def __init__(self, *, status: str = "invalid_config", reason: str, field_name: str = "") -> None:
        self.status = str(status or "invalid_config")
        self.reason = str(reason or "qq_channel_profile_invalid")
        self.field_name = str(field_name or "")
        super().__init__(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "status": self.status, "reason": self.reason}
        if self.field_name:
            payload["field"] = self.field_name
        return payload


@dataclass(frozen=True, slots=True)
class QQChannelDeploymentProfile:
    profile_ref: str
    bot_qq: str
    onebot_http_url: str
    webhook_secret: str = field(repr=False)
    onebot_access_token: str = field(repr=False)
    onebot_shared_data_root: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        _safe_id(self.profile_ref, field_name="profile_ref")
        _bot_qq(self.bot_qq, field_name="bot_qq")
        _http_url(self.onebot_http_url, field_name="onebot_http_url")
        _secret(self.webhook_secret, field_name="webhook_secret")
        _secret(self.onebot_access_token, field_name="onebot_access_token")
        _optional_absolute_path(
            self.onebot_shared_data_root,
            field_name="onebot_shared_data_root",
        )


@dataclass(frozen=True, slots=True)
class QQChannelProfileSet:
    profiles: tuple[QQChannelDeploymentProfile, ...] = ()

    def get(self, profile_ref: str) -> QQChannelDeploymentProfile | None:
        normalized = _safe_id(profile_ref, field_name="profile_ref")
        for profile in self.profiles:
            if profile.profile_ref == normalized:
                return profile
        return None

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "status": "configured" if self.profiles else "not_configured",
            "count": len(self.profiles),
            "profile_refs": [profile.profile_ref for profile in self.profiles],
        }


def parse_qq_channel_profiles(payload: Any) -> QQChannelProfileSet:
    if not isinstance(payload, Mapping):
        _fail("profile_root_must_be_table")
    _reject_unknown(payload, _ROOT_FIELDS, field_name="")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _fail("schema_version_must_be_integer", field_name="schema_version")
    if schema_version != QQ_CHANNEL_PROFILES_SCHEMA_VERSION:
        _fail("unsupported_schema_version", field_name="schema_version", status="incompatible")

    raw_profiles = payload.get("profiles", [])
    if not isinstance(raw_profiles, list):
        _fail("profiles_must_be_array", field_name="profiles")
    if len(raw_profiles) > _MAX_PROFILES:
        _fail("too_many_profiles", field_name="profiles")
    profiles: list[QQChannelDeploymentProfile] = []
    seen_refs: set[str] = set()
    seen_bot_ids: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        prefix = f"profiles.{index}"
        if not isinstance(raw_profile, Mapping):
            _fail("profile_must_be_table", field_name=prefix)
        _reject_unknown(raw_profile, _PROFILE_FIELDS, field_name=prefix)
        profile = QQChannelDeploymentProfile(
            profile_ref=_safe_id(raw_profile.get("profile_ref"), field_name=f"{prefix}.profile_ref"),
            bot_qq=_bot_qq(raw_profile.get("bot_qq"), field_name=f"{prefix}.bot_qq"),
            onebot_http_url=_http_url(
                raw_profile.get("onebot_http_url"),
                field_name=f"{prefix}.onebot_http_url",
            ),
            webhook_secret=_secret(
                raw_profile.get("webhook_secret"),
                field_name=f"{prefix}.webhook_secret",
            ),
            onebot_access_token=_secret(
                raw_profile.get("onebot_access_token"),
                field_name=f"{prefix}.onebot_access_token",
            ),
            onebot_shared_data_root=_optional_absolute_path(
                raw_profile.get("onebot_shared_data_root", ""),
                field_name=f"{prefix}.onebot_shared_data_root",
            ),
        )
        if profile.profile_ref in seen_refs:
            _fail("duplicate_profile_ref", field_name=f"{prefix}.profile_ref")
        if profile.bot_qq in seen_bot_ids:
            _fail("duplicate_qq_bot_id", field_name=f"{prefix}.bot_qq")
        seen_refs.add(profile.profile_ref)
        seen_bot_ids.add(profile.bot_qq)
        profiles.append(profile)
    return QQChannelProfileSet(profiles=tuple(profiles))


def load_qq_channel_profiles(path: Path, *, missing_ok: bool = False) -> QQChannelProfileSet:
    try:
        with Path(path).open("rb") as profile_file:
            payload = tomllib.load(profile_file)
    except FileNotFoundError:
        if missing_ok:
            return QQChannelProfileSet()
        _fail("qq_channel_profiles_not_found", status="unavailable")
    except (OSError, tomllib.TOMLDecodeError):
        _fail("qq_channel_profiles_unreadable")
    return parse_qq_channel_profiles(payload)


def _reject_unknown(payload: Mapping[str, Any], allowed: frozenset[str], *, field_name: str) -> None:
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        _fail(
            "unsupported_qq_channel_profile_field",
            field_name=f"{field_name}.{unknown[0]}" if field_name else unknown[0],
        )


def _safe_id(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_safe_id", field_name=field_name)
    normalized = value.strip()
    if normalized != value or _SAFE_ID_PATTERN.fullmatch(normalized) is None:
        _fail("invalid_safe_id", field_name=field_name)
    return normalized


def _bot_qq(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        _fail("qq_bot_id_required", field_name=field_name)
    normalized = value.strip()
    if normalized != value or not normalized.isdigit() or not (5 <= len(normalized) <= 20):
        _fail("qq_bot_id_required", field_name=field_name)
    return normalized


def _http_url(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        _fail("qq_onebot_http_url_invalid", field_name=field_name)
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        _fail("qq_onebot_http_url_invalid", field_name=field_name)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        _fail("qq_onebot_http_url_invalid", field_name=field_name)
    return normalized


def _secret(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        _fail("qq_channel_secret_required", field_name=field_name)
    normalized = value.strip()
    if normalized != value or not normalized or len(normalized) > 4096 or any(ord(char) < 32 for char in normalized):
        _fail("qq_channel_secret_required", field_name=field_name)
    return normalized


def _optional_absolute_path(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        _fail("qq_shared_data_root_invalid", field_name=field_name)
    normalized = value.strip().rstrip("/\\")
    if not normalized:
        return ""
    if normalized != value.rstrip("/\\") or "\x00" in normalized:
        _fail("qq_shared_data_root_invalid", field_name=field_name)
    path = PureWindowsPath(normalized) if PureWindowsPath(normalized).drive else PurePosixPath(normalized)
    if not path.is_absolute() or ".." in path.parts:
        _fail("qq_shared_data_root_invalid", field_name=field_name)
    return normalized


def _fail(reason: str, *, field_name: str = "", status: str = "invalid_config") -> None:
    raise QQChannelProfileError(status=status, reason=reason, field_name=field_name)


__all__ = [
    "QQ_CHANNEL_PROFILES_RELATIVE_PATH",
    "QQ_CHANNEL_PROFILES_SCHEMA_VERSION",
    "QQChannelDeploymentProfile",
    "QQChannelProfileError",
    "QQChannelProfileSet",
    "load_qq_channel_profiles",
    "parse_qq_channel_profiles",
]
