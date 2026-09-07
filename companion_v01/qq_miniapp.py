"""Host validation and model-result projection for NapCat miniapp generation.

NapCat owns templates/signing. This module neither fetches links nor builds a
second card implementation; the generated Ark is passed unchanged to JSON send.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlsplit


_COMMON = {"title", "desc", "picUrl", "jumpUrl"}
_CUSTOM = {
    "iconUrl",
    "appId",
    "scene",
    "templateType",
    "businessType",
    "verType",
    "shareType",
    "versionId",
    "sdkId",
    "withShareTicket",
}
_NUMERIC = {"appId", "scene", "templateType", "businessType", "verType", "shareType", "withShareTicket"}
_OPTIONAL = {"webUrl", "rawArkData"}
_MAX_ARK_BYTES = 256 * 1024


def _public_url(value: str) -> bool:
    # Preserve operational URLs, including signed queries. No host-side fetch.
    if "\\" in value or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or "%" in host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port == 0
        ):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            address = ipaddress.ip_address(host)
            return address.is_global and not (address.is_multicast or address.is_reserved)
        except ValueError:
            return "." in host and ":" not in host
    except ValueError:
        return False


def validate_miniapp_params(params: dict[str, Any]) -> str:
    """Return a stable reason (no input values), or empty on valid input."""
    templated = "type" in params
    if templated and params["type"] not in ("bili", "weibo"):
        return "miniapp_template_unsupported"
    required = _COMMON | ({"type"} if templated else _CUSTOM)
    allowed = required | _OPTIONAL
    if set(params) - allowed:
        return "miniapp_unknown_fields"
    if required - set(params):
        return "miniapp_missing_fields"
    for key, value in params.items():
        if not isinstance(value, str):
            return "miniapp_string_required"
        limit = 8192 if key in {"picUrl", "jumpUrl", "webUrl", "iconUrl"} else 4096 if key == "desc" else 512
        if len(value) > limit or "\x00" in value:
            return "miniapp_field_invalid"
        if key not in {"desc", "webUrl"} and not value.strip():
            return "miniapp_field_empty"
        if key in _NUMERIC and (not value.isascii() or not value.isdecimal() or len(value) > 16):
            return "miniapp_numeric_string_required"
    if params.get("rawArkData", "false") not in {"true", "false"}:
        return "miniapp_raw_flag_invalid"
    for key in ("picUrl", "iconUrl", "webUrl"):
        if params.get(key) and not _public_url(params[key]):
            return "miniapp_public_url_required"
    jump = params["jumpUrl"]
    if "\\" in jump or any(char.isspace() or ord(char) < 32 for char in jump):
        return "miniapp_jump_invalid"
    if ":" in jump.split("?", 1)[0] or jump.startswith("//"):
        if not _public_url(jump):
            return "miniapp_jump_invalid"
    elif any(part == ".." for part in jump.split("?", 1)[0].split("/")):
        return "miniapp_jump_invalid"
    return ""


def project_miniapp_result(payload: dict[str, Any], *, raw: bool = False) -> dict[str, Any]:
    """Keep provider data; add a send-ready message only for a valid normal Ark."""
    if not payload.get("ok"):
        return payload
    data = payload.get("data")
    ark = data.get("data") if isinstance(data, dict) else None
    # The current endpoint returns data.data; some documented versions wrap ark.
    if isinstance(ark, dict) and set(ark) == {"ark"}:
        ark = ark["ark"]
    try:
        serialized = ark if isinstance(ark, str) else json.dumps(ark, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > _MAX_ARK_BYTES:
            raise ValueError("oversized")
        decoded = json.loads(serialized)
        app_key, view_key, meta_key = ("appName", "appView", "metaData") if raw else ("app", "view", "meta")
        if (
            not isinstance(decoded, dict)
            or not isinstance(decoded.get(app_key), str)
            or not decoded[app_key].strip()
            or not isinstance(decoded.get(view_key), str)
            or not decoded[view_key].strip()
            or not isinstance(decoded.get(meta_key), dict)
            or not decoded[meta_key]
        ):
            raise ValueError("missing Ark")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        # Do not expose malformed provider output (which may be a diagnostic).
        return {
            "ok": False,
            "status": "failed",
            "reason": "miniapp_ark_invalid",
            "code": "miniapp_ark_invalid",
            "action": "get_mini_app_ark",
        }
    result = dict(payload)
    result["stage"] = "generated"
    result["raw_ark"] = raw
    if not raw:
        result["message"] = [{"type": "json", "data": {"data": serialized}}]
    return result
