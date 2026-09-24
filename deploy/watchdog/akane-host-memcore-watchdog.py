#!/usr/bin/env python3
"""Restart the Akane Host only when MemCore is both large and repeatedly stuck."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable


LINE_RE = re.compile(
    r"^(?P<epoch>\d+(?:\.\d+)?)\s+.*memcore_compaction "
    r"status=(?P<status>\S+) namespace=(?P<namespace>\S+).*?"
    r"raw_before_tokens=(?P<raw>\d+)"
)
BAD_STATUSES = frozenset({"failed", "blocked_by_open_turn"})


def find_stuck_namespaces(
    lines: Iterable[str],
    *,
    minimum_raw_tokens: int,
    after_epoch: float = 0.0,
) -> dict[str, dict[str, int]]:
    incidents: dict[str, dict[str, int]] = {}
    for line in lines:
        match = LINE_RE.search(str(line or "").strip())
        if not match or float(match.group("epoch")) <= after_epoch:
            continue
        namespace = match.group("namespace")
        status = match.group("status")
        raw_tokens = int(match.group("raw"))
        if status == "compacted":
            incidents.pop(namespace, None)
            continue
        if status not in BAD_STATUSES or raw_tokens < minimum_raw_tokens:
            continue
        incident = incidents.setdefault(namespace, {"count": 0, "max_raw_tokens": 0})
        incident["count"] += 1
        incident["max_raw_tokens"] = max(incident["max_raw_tokens"], raw_tokens)
    return incidents


def load_last_restart(path: Path) -> float:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0.0, float(payload.get("last_restart_epoch") or 0.0))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def save_last_restart(path: Path, epoch: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"last_restart_epoch": epoch}, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_journal(unit: str, lookback_minutes: int) -> list[str]:
    result = subprocess.run(
        [
            "journalctl",
            "-u",
            unit,
            "--since",
            f"{lookback_minutes} minutes ago",
            "--no-pager",
            "-o",
            "short-unix",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.splitlines()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", default="akane-host.service")
    parser.add_argument("--state-file", default="/var/lib/akane-host-watchdog/state.json")
    parser.add_argument("--lookback-minutes", type=int, default=45)
    parser.add_argument("--minimum-raw-tokens", type=int, default=96_000)
    parser.add_argument("--minimum-failures", type=int, default=3)
    parser.add_argument("--cooldown-seconds", type=int, default=30 * 60)
    parser.add_argument("--journal-file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    now = time.time()
    last_restart = load_last_restart(state_path)
    if now - last_restart < max(0, args.cooldown_seconds):
        return 0

    if args.journal_file:
        lines = Path(args.journal_file).read_text(encoding="utf-8").splitlines()
    else:
        lines = read_journal(args.unit, max(5, args.lookback_minutes))
    incidents = find_stuck_namespaces(
        lines,
        minimum_raw_tokens=max(1, args.minimum_raw_tokens),
        after_epoch=last_restart,
    )
    candidates = [
        (namespace, detail)
        for namespace, detail in incidents.items()
        if detail["count"] >= max(1, args.minimum_failures)
    ]
    if not candidates:
        return 0

    namespace, detail = max(
        candidates,
        key=lambda item: (item[1]["max_raw_tokens"], item[1]["count"]),
    )
    print(
        "memcore_watchdog action=restart "
        f"unit={args.unit} namespace={namespace} failures={detail['count']} "
        f"max_raw_tokens={detail['max_raw_tokens']}",
        flush=True,
    )
    if args.dry_run:
        return 0

    save_last_restart(state_path, now)
    subprocess.run(
        ["systemctl", "restart", args.unit],
        check=True,
        timeout=120,
    )
    subprocess.run(
        ["systemctl", "is-active", "--quiet", args.unit],
        check=True,
        timeout=30,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
