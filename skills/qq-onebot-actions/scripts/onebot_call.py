#!/usr/bin/env python3
"""Small, structured OneBot HTTP caller used by the qq-onebot-actions Skill."""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from typing import Any


PREFIX = "Bearer "
ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def find_configs() -> list[str]:
    roots = ["/var/lib/akane-host", "/opt", "/root", "/home"]
    paths: list[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        paths.extend(glob.glob(root + "/**/onebot11*.json", recursive=True))
    valid: list[str] = []
    def priority(path: str) -> tuple[int, str]:
        normalized = path.replace("\\", "/")
        if "/bots/personal/" in normalized:
            return (0, normalized)
        if "/bots/finance/" in normalized:
            return (1, normalized)
        return (2, normalized)

    for path in sorted(set(paths), key=priority):
        try:
            with open(path, encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, ValueError):
            continue
        valid.append(path)
    return valid


def extract_token(config: Any) -> str | None:
    stack = [config]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"token", "accessToken", "authorization"} and isinstance(item, str) and len(item) > 10:
                    return item
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
    return None


def discover_tokens() -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for path in find_configs():
        try:
            with open(path, encoding="utf-8") as handle:
                token = extract_token(json.load(handle))
        except (OSError, ValueError):
            continue
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def probe_ports() -> list[int]:
    ports: list[int] = []
    for port in range(3000, 3010):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                ports.append(port)
        finally:
            sock.close()
    return ports


def _decode_body(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {"status": "failed", "retcode": -1, "message": raw.decode("utf-8", errors="replace")}
    return value if isinstance(value, dict) else {"status": "failed", "retcode": -1, "data": value}


def call(port: int, token: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/{action}",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": PREFIX + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = _decode_body(response.read())
    except urllib.error.HTTPError as exc:
        result = _decode_body(exc.read())
        result.setdefault("status", "failed")
        result["http_status"] = exc.code
        return result
    except (OSError, urllib.error.URLError) as exc:
        return {"status": "failed", "retcode": -1, "reason": "transport_error", "message": str(exc)}
    try:
        retcode = int(result.get("retcode", -1))
    except (TypeError, ValueError):
        retcode = -1
    if result.get("status") != "ok" or retcode != 0:
        result.setdefault("status", "failed")
        result.setdefault("reason", "onebot_retcode_failed")
    return result


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _is_success(value: dict[str, Any]) -> bool:
    try:
        retcode = int(value.get("retcode", -1))
    except (TypeError, ValueError):
        return False
    return value.get("status") == "ok" and retcode == 0


def _discover_endpoints(tokens: list[str]) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for token in tokens:
        for port in probe_ports():
            result = call(port, token, "get_login_info", {})
            if not _is_success(result):
                continue
            info = result.get("data") if isinstance(result.get("data"), dict) else {}
            endpoints.append({"port": port, "self_id": str(info.get("user_id") or ""), "nickname": info.get("nickname"), "token": token})
    return endpoints


def main() -> int:
    if len(sys.argv) < 2:
        print("USAGE: onebot_call.py <action> ['<json params>'] | probe", file=sys.stderr)
        return 2
    tokens = discover_tokens()
    if not tokens:
        _print({"status": "failed", "reason": "no_token"})
        return 2
    if sys.argv[1] == "probe":
        accounts = [
            {key: value for key, value in endpoint.items() if key != "token"}
            for endpoint in _discover_endpoints(tokens)
        ]
        _print({"status": "ok", "ok_ports": [item["port"] for item in accounts], "accounts": accounts})
        return 0 if accounts else 2
    action = sys.argv[1]
    if not ACTION_RE.fullmatch(action):
        _print({"status": "failed", "reason": "invalid_action"})
        return 2
    try:
        params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except ValueError as exc:
        _print({"status": "failed", "reason": "invalid_json", "message": str(exc)})
        return 2
    if not isinstance(params, dict):
        _print({"status": "failed", "reason": "params_must_be_object"})
        return 2
    endpoints = _discover_endpoints(tokens)
    expected_self_id = str(os.environ.get("QQ_BOT_QQ") or os.environ.get("AKANE_BOT_QQ") or "").strip()
    if expected_self_id:
        endpoints = [item for item in endpoints if item.get("self_id") == expected_self_id]
    last: dict[str, Any] = {"status": "failed", "reason": "no_matching_onebot_account" if expected_self_id else "no_listening_port"}
    for endpoint in endpoints:
        result = call(int(endpoint["port"]), str(endpoint["token"]), action, params)
        if _is_success(result):
            _print(result)
            return 0
        last = {**result, "port": endpoint["port"], "self_id": endpoint.get("self_id")}
    _print(last)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
