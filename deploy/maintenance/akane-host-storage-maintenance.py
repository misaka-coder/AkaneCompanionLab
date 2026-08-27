#!/usr/bin/env python3
"""Bounded retention and disk-health reporting for the Akane cloud Host."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


GIB = 1024**3


@dataclass(frozen=True, slots=True)
class RetentionTarget:
    path: Path
    category: str
    bytes_before: int


def _direct_children(root: Path, *, directories_only: bool = True) -> list[Path]:
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for child in children:
        if child.is_symlink():
            continue
        if directories_only and not child.is_dir():
            continue
        result.append(child)
    return result


def _newest(paths: Iterable[Path], count: int) -> set[Path]:
    ranked_entries: list[tuple[float, str, Path]] = []
    for path in paths:
        try:
            ranked_entries.append((path.stat().st_mtime, path.name, path))
        except FileNotFoundError:
            continue
    ranked = [entry[2] for entry in sorted(ranked_entries, reverse=True)]
    return set(ranked[: max(0, count)])


def _tree_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            candidate = Path(root) / name
            try:
                if not candidate.is_symlink():
                    total += candidate.stat().st_size
            except FileNotFoundError:
                continue
    return total


def collect_release_targets(
    root: Path,
    *,
    active_release: Path | None,
    keep: int,
) -> list[RetentionTarget]:
    releases = _direct_children(root)
    retained = _newest(releases, keep)
    if active_release is not None:
        try:
            active = active_release.resolve(strict=False)
            root_resolved = root.resolve(strict=False)
        except OSError:
            active = active_release
            root_resolved = root
        if active.parent == root_resolved:
            retained.add(active)
    return [
        RetentionTarget(path=path, category="release", bytes_before=_tree_size(path))
        for path in releases
        if path.resolve(strict=False) not in retained and path not in retained
    ]


def collect_backup_targets(
    root: Path,
    *,
    category: str,
    keep: int,
    required_fragment: str = "",
) -> list[RetentionTarget]:
    candidates = [
        path
        for path in _direct_children(root)
        if not required_fragment or required_fragment in path.name
    ]
    retained = _newest(candidates, keep)
    return [
        RetentionTarget(path=path, category=category, bytes_before=_tree_size(path))
        for path in candidates
        if path not in retained
    ]


def collect_expired_targets(
    roots: Iterable[Path],
    *,
    category: str,
    older_than_epoch: float,
    direct_children_only: bool,
    keep_newest: int = 0,
) -> list[RetentionTarget]:
    candidates: list[Path] = []
    for root in roots:
        entries = _direct_children(root, directories_only=False) if direct_children_only else list(root.glob("*.jsonl"))
        for entry in entries:
            try:
                if not entry.is_symlink() and entry.stat().st_mtime < older_than_epoch:
                    candidates.append(entry)
            except FileNotFoundError:
                continue
    retained = _newest(candidates, keep_newest)
    return [
        RetentionTarget(path=path, category=category, bytes_before=_tree_size(path))
        for path in candidates
        if path not in retained
    ]


def remove_targets(targets: Iterable[RetentionTarget], *, dry_run: bool) -> tuple[list[dict[str, object]], int]:
    removed: list[dict[str, object]] = []
    reclaimed = 0
    for target in targets:
        payload = {
            "category": target.category,
            "name": target.path.name,
            "bytes_before": target.bytes_before,
            "dry_run": dry_run,
        }
        if not dry_run:
            try:
                if target.path.is_dir():
                    shutil.rmtree(target.path)
                else:
                    target.path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            reclaimed += target.bytes_before
        removed.append(payload)
    return removed, reclaimed


def resolve_active_release(unit: str) -> Path | None:
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=WorkingDirectory", "--value"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def disk_health(path: Path, *, warning_free_bytes: int, critical_free_bytes: int) -> dict[str, object]:
    usage = shutil.disk_usage(path)
    if usage.free < critical_free_bytes:
        status = "critical"
    elif usage.free < warning_free_bytes:
        status = "warning"
    else:
        status = "ready"
    return {
        "status": status,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_ratio": round(usage.used / usage.total, 6) if usage.total else 1.0,
    }


def write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def emit_alert(status: str, free_bytes: int) -> None:
    if status not in {"warning", "critical"}:
        return
    try:
        subprocess.run(
            [
                "logger",
                "--priority",
                "daemon.warning" if status == "warning" else "daemon.crit",
                "--tag",
                "akane-storage",
                f"storage_health status={status} free_bytes={free_bytes}",
            ],
            check=False,
            timeout=10,
        )
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", default="akane-host.service")
    parser.add_argument("--release-root", type=Path, default=Path("/opt/akane/releases"))
    parser.add_argument("--code-backup-root", type=Path, default=Path("/opt/akane/backups"))
    parser.add_argument("--data-backup-root", type=Path, default=Path("/var/lib/akane/backups"))
    parser.add_argument("--deploy-backup-root", type=Path, default=Path("/var/lib/akane-host/deploy-backups"))
    parser.add_argument(
        "--audit-root",
        action="append",
        type=Path,
        default=[],
        help="Prompt-audit directory; may be repeated.",
    )
    parser.add_argument(
        "--transport-cache-root",
        action="append",
        type=Path,
        default=[],
        help="OneBot transport cache directory; may be repeated.",
    )
    parser.add_argument(
        "--artifact-root",
        action="append",
        type=Path,
        default=[],
        help="Ephemeral workspace artifact directory; may be repeated.",
    )
    parser.add_argument("--state-file", type=Path, default=Path("/var/lib/akane-host-storage/state.json"))
    parser.add_argument("--keep-releases", type=int, default=3)
    parser.add_argument("--keep-code-backups", type=int, default=2)
    parser.add_argument("--keep-data-backups", type=int, default=2)
    parser.add_argument("--deploy-backup-days", type=int, default=30)
    parser.add_argument("--audit-days", type=int, default=7)
    parser.add_argument("--transport-cache-hours", type=int, default=12)
    parser.add_argument("--artifact-days", type=int, default=7)
    parser.add_argument("--warning-free-gib", type=float, default=4.0)
    parser.add_argument("--critical-free-gib", type=float, default=1.0)
    parser.add_argument("--active-release", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = time.time()
    audit_roots = args.audit_root or [
        Path("/var/lib/akane-host/bots/personal/logs/llm_prompt_audit"),
        Path("/var/lib/akane-host/bots/finance/logs/llm_prompt_audit"),
    ]
    transport_cache_roots = args.transport_cache_root or [
        Path("/var/lib/akane-host/bots/personal/napcat/qq/NapCat/temp"),
        Path("/var/lib/akane-host/bots/finance/napcat/qq/NapCat/temp"),
    ]
    artifact_roots = args.artifact_root or [
        Path("/var/lib/akane-host/bots/personal/workspace/Inbox"),
        Path("/var/lib/akane-host/bots/personal/workspace/Outputs"),
        Path("/var/lib/akane-host/bots/finance/workspace/Inbox"),
        Path("/var/lib/akane-host/bots/finance/workspace/Outputs"),
        Path("/var/lib/akane-host/execution_workspace/outputs"),
    ]
    active_release = args.active_release or resolve_active_release(args.unit)
    targets = [
        *collect_release_targets(
            args.release_root,
            active_release=active_release,
            keep=max(1, args.keep_releases),
        ),
        *collect_backup_targets(
            args.code_backup_root,
            category="code_backup",
            keep=max(1, args.keep_code_backups),
        ),
        *collect_backup_targets(
            args.data_backup_root,
            category="data_predeploy_backup",
            keep=max(1, args.keep_data_backups),
            required_fragment="-predeploy",
        ),
        *collect_expired_targets(
            [args.deploy_backup_root],
            category="deploy_backup",
            older_than_epoch=now - max(1, args.deploy_backup_days) * 86400,
            direct_children_only=True,
            keep_newest=5,
        ),
        *collect_expired_targets(
            audit_roots,
            category="prompt_audit",
            older_than_epoch=now - max(1, args.audit_days) * 86400,
            direct_children_only=False,
        ),
        *collect_expired_targets(
            transport_cache_roots,
            category="onebot_transport_cache",
            older_than_epoch=now - max(1, args.transport_cache_hours) * 3600,
            direct_children_only=True,
        ),
        *collect_expired_targets(
            artifact_roots,
            category="workspace_artifact",
            older_than_epoch=now - max(1, args.artifact_days) * 86400,
            direct_children_only=True,
            keep_newest=1,
        ),
    ]
    removed, reclaimed = remove_targets(targets, dry_run=args.dry_run)
    health = disk_health(
        Path("/"),
        warning_free_bytes=max(1, int(args.warning_free_gib * GIB)),
        critical_free_bytes=max(1, int(args.critical_free_gib * GIB)),
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "checked_at_epoch": now,
        "dry_run": args.dry_run,
        "active_release": active_release.name if active_release is not None else "",
        "removed": removed,
        "reclaimed_bytes_estimate": reclaimed,
        "disk": health,
    }
    if not args.dry_run:
        write_state(args.state_file, payload)
        emit_alert(str(health["status"]), int(health["free_bytes"]))
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True), flush=True)
    return 2 if health["status"] == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
