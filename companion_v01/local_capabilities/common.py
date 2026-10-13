"""Shared scalar normalization and profile/host path boundaries."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .definitions import (
    HOST_MCP_REGISTRY_PATH_TEMPLATE,
    PROFILE_CONFIG_PATH_TEMPLATE,
    PROFILE_ID_SAFE_CHARS,
)


def _profile_config_path(base_dir: Path | str | None, profile_user_id: str) -> Path | None:
    if base_dir is None:
        return None
    root = Path(base_dir).resolve()
    profile = _safe_profile_id(profile_user_id)
    path = (root / profile / "capabilities" / "capabilities.yaml").resolve()
    if root not in path.parents:
        return None
    return path


def _host_mcp_registry_path(base_dir: Path | str | None) -> Path | None:
    if base_dir is None:
        return None
    root = Path(base_dir).resolve()
    path = (root / "capabilities" / "mcp_servers.yaml").resolve()
    if root not in path.parents:
        return None
    return path


def _safe_profile_id(profile_user_id: str) -> str:
    raw = str(profile_user_id or "").strip() or "default"
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-") or "default"
    return safe[:120]


def _public_config_scope(profile_user_id: str) -> dict[str, Any]:
    return {
        "profileUserId": _safe_profile_id(profile_user_id),
        "explicitConfigPath": PROFILE_CONFIG_PATH_TEMPLATE,
    }


def _public_mcp_config_scope(profile_user_id: str) -> dict[str, Any]:
    return {
        "registry": "host",
        "registryPath": HOST_MCP_REGISTRY_PATH_TEMPLATE,
        "profileOverlayUserId": _safe_profile_id(profile_user_id),
        "profileOverlayPath": PROFILE_CONFIG_PATH_TEMPLATE,
    }


def _safe_short_token(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text):
        return default
    return text


def _safe_optional_bool(value: Any, *, default: bool | None = None) -> bool | None:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _safe_optional_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _safe_optional_float(value: Any, *, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _safe_path_basename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    name = text.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        return ""
    return name[:120]


def _safe_reason(value: Any) -> str:
    text = _safe_short_text(value, limit=160)
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)=([^\s&]+)", r"\1=redacted", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text


def _safe_short_text(value: Any, *, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
