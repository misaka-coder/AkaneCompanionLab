"""Host validation and model-result projection for NapCat miniapp generation.

NapCat owns templates/signing. The host keeps generated Ark behind scoped,
short-lived handles so the model need not copy signed JSON into a send call.
"""

from __future__ import annotations

import ipaddress
import copy
import json
import threading
import time
import uuid
from collections.abc import Callable
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
    if (templated and params["type"] == "bili") or (not templated and params.get("appId") == "1109937557"):
        # One controlled direct card played, but arbitrary BV/URL input cannot
        # obtain a rich QQ preview through a stable, version-independent API.
        # Keep only the generally deployable native-forward path exposed.
        return "bilibili_native_card_forward_required"
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


class MiniappCardStore:
    """Per-Bot ephemeral cards, scoped to their actor/conversation/character.

    Sending claims a handle once, including ambiguous failures. A repeated call
    returns its real receipt, never a second external action. Restart expires all
    handles; persistent retry/replay is deliberately not supported.
    """

    def __init__(self, *, ttl: float = 900.0, capacity: int = 128, clock=time.monotonic):
        self._clock = clock
        self._ttl = ttl
        self._capacity = capacity
        self._cards: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def put(self, scope: tuple, message: list) -> str:
        with self._lock:
            now = self._clock()
            self._cards = {key: value for key, value in self._cards.items() if value['expires'] > now}
            if len(self._cards) >= self._capacity:
                return ""
            ref = "miniapp_" + uuid.uuid4().hex
            self._cards[ref] = {"scope": scope, "expires": now + self._ttl,
                                "message": copy.deepcopy(message), "target": None, "receipt": None}
            return ref

    def send(self, ref: Any, *, scope: tuple, target: tuple,
             sender: Callable[[list], dict[str, Any]]) -> dict[str, Any]:
        def failure(reason):
            return {"ok": False, "status": "unavailable", "reason": reason, "action": target[0]}

        with self._lock:
            card = self._cards.get(ref) if isinstance(ref, str) else None
            if card is None or card["expires"] <= self._clock():
                return failure("miniapp_card_expired_or_unknown")
            if card["scope"] != scope:
                return failure("miniapp_card_scope_mismatch")
            if card["target"] is not None:
                if card["target"] != target:
                    return failure("miniapp_card_already_used")
                if card["receipt"] is None:
                    return failure("miniapp_card_send_in_progress")
                return {**copy.deepcopy(card["receipt"]), "duplicate_suppressed": True}
            card["target"] = target
            message = card.pop("message")
        outcome = failure("miniapp_card_delivery_unknown")
        try:
            outcome = sender(message)
            return outcome
        finally:
            # Exceptions also consume the handle: an external send may have run.
            with self._lock:
                card["receipt"] = copy.deepcopy(outcome)
