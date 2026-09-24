#!/usr/bin/env python3
"""Restart the Akane Host when the finance plugin push service is dead.

The finance plugin host supervises background services with a restart-only
contract: a service task that exits (cancelled/failed/exited) is never revived
by the host, so proactive QQ pushes silently stop.  This watchdog polls the
bot-scoped plugin status endpoint and restarts the host unit when the
active ``akane.finance`` generation no longer publishes its required service.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


FINANCE_PLUGIN_ID = "akane.finance"
FINANCE_SERVICE_ID = "public-news-push"
HOST_ENV_PATH = "/etc/akane/host.env"
DEFAULT_STATUS_URL = "http://127.0.0.1:10001/api/bots/finance/admin/plugins/status"


def load_admin_token(token_file: str) -> str:
    """Read AKANE_ADMIN_TOKEN from the host environment file, never echo it."""
    try:
        lines = Path(token_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("AKANE_ADMIN_TOKEN="):
            value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            return value
    return ""


def fetch_plugin_status(url: str, token: str, timeout: float = 10.0) -> tuple[bool, dict[str, Any]]:
    """Return (reachable, payload).  A reachable endpoint that denies auth is
    still ``reachable`` so an auth mismatch is reported instead of ignored."""
    if token:
        headers = {"Authorization": f"Bearer {token}"}
    else:
        headers = {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, {"error": str(exc)}
    return True, payload


def job_is_healthy(payload: dict[str, Any]) -> bool:
    """The active finance generation must publish its required service."""
    if not payload.get("ok"):
        return False
    if str(payload.get("status") or "") != "active":
        return False
    for plugin in payload.get("plugins") or []:
        if str(plugin.get("plugin_id") or "") != FINANCE_PLUGIN_ID:
            continue
        contribution = plugin.get("contribution_snapshot") or {}
        services = contribution.get("background_services") or []
        return (
            str(plugin.get("status") or "") == "active"
            and FINANCE_SERVICE_ID in services
            and int(payload.get("background_service_count") or 0) > 0
        )
    return False


def job_failure_reason(payload: dict[str, Any]) -> str:
    for plugin in payload.get("plugins") or []:
        if str(plugin.get("plugin_id") or "") != FINANCE_PLUGIN_ID:
            continue
        status = str(plugin.get("status") or "")
        reason = str(plugin.get("reason") or "")
        contribution = plugin.get("contribution_snapshot") or {}
        services = contribution.get("background_services") or []
        if FINANCE_SERVICE_ID not in services:
            return "finance_service_missing"
        return f"plugin_status={status} reason={reason}"
    return "finance_plugin_missing"


def load_state(path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "last_restart_epoch": max(0, int(payload.get("last_restart_epoch") or 0)),
            "consecutive_failures": max(0, int(payload.get("consecutive_failures") or 0)),
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"last_restart_epoch": 0, "consecutive_failures": 0}


def save_state(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def restart_host(unit: str) -> tuple[bool, str]:
    """Restart the host unit and confirm it comes back active."""
    try:
        subprocess.run(["systemctl", "restart", unit], check=True, timeout=120)
    except (subprocess.CalledProcessError, OSError) as exc:
        return False, f"restart_failed: {exc}"
    try:
        subprocess.run(["systemctl", "is-active", "--quiet", unit], check=True, timeout=30)
    except (subprocess.CalledProcessError, OSError):
        return False, "restart_issued_but_not_active"
    return True, "restart_confirmed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", default="akane-host.service")
    parser.add_argument("--status-url", default=DEFAULT_STATUS_URL)
    parser.add_argument("--host-env", default=HOST_ENV_PATH)
    parser.add_argument("--state-file", default="/var/lib/akane-host-finance-watchdog/state.json")
    parser.add_argument("--minimum-consecutive-failures", type=int, default=2)
    parser.add_argument("--cooldown-seconds", type=int, default=30 * 60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    state = load_state(state_path)
    now = int(time.time())

    token = load_admin_token(args.host_env)
    if not token:
        print(
            "finance_watchdog action=skip reason=admin_token_unavailable "
            f"host_env={args.host_env}",
            flush=True,
        )
        return 0

    reachable, payload = fetch_plugin_status(args.status_url, token)
    if not reachable:
        print(
            "finance_watchdog action=skip reason=status_endpoint_unreachable "
            f"unit={args.unit}",
            flush=True,
        )
        return 0

    if job_is_healthy(payload):
        if state["consecutive_failures"]:
            state["consecutive_failures"] = 0
            save_state(state_path, state)
        print(
            "finance_watchdog action=ok service=published "
            f"consecutive_failures={state['consecutive_failures']}",
            flush=True,
        )
        return 0

    failures = state["consecutive_failures"] + 1
    state["consecutive_failures"] = failures
    save_state(state_path, state)
    detail = job_failure_reason(payload)
    print(
        "finance_watchdog action=incident "
            f"unit={args.unit} {detail} "
            f"consecutive_failures={failures}/{args.minimum_consecutive_failures}",
        flush=True,
    )
    if failures < args.minimum_consecutive_failures:
        return 0

    if now - state["last_restart_epoch"] < max(0, args.cooldown_seconds):
        print(
            "finance_watchdog action=skip reason=cooldown "
            f"seconds_since_last_restart={now - state['last_restart_epoch']}",
            flush=True,
        )
        return 0

    print(
        "finance_watchdog action=restart "
        f"unit={args.unit} {detail} consecutive_failures={failures}",
        flush=True,
    )
    if args.dry_run:
        return 0

    state["last_restart_epoch"] = now
    save_state(state_path, state)
    ok, status_text = restart_host(args.unit)
    if not ok:
        state["consecutive_failures"] = failures
        state["last_restart_epoch"] = 0
        save_state(state_path, state)
        print(
            f"finance_watchdog action=failed status={status_text} unit={args.unit}",
            flush=True,
        )
        return 1
    print(
        f"finance_watchdog action=restarted status={status_text} unit={args.unit}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
