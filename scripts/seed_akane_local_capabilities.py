from __future__ import annotations

import argparse
import copy
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml


PORTABLE_SECTIONS = ("mcpServers", "providers", "voiceProfiles", "workflows")


def seed_local_capability_profile(
    *,
    source_users_data_root: Path,
    destination_users_data_root: Path,
    profile_user_id: str = "master",
) -> tuple[str, int]:
    """Seed missing local capabilities without replacing instance-owned choices."""

    profile = str(profile_user_id or "master").strip() or "master"
    source_path = source_users_data_root / profile / "capabilities" / "capabilities.yaml"
    destination_path = destination_users_data_root / profile / "capabilities" / "capabilities.yaml"
    if not source_path.is_file() or source_path.resolve() == destination_path.resolve():
        return "source-unavailable", 0

    source = _read_mapping(source_path)
    destination = _read_mapping(destination_path) if destination_path.is_file() else {}
    merged = copy.deepcopy(destination)
    seeded = 0
    for section in PORTABLE_SECTIONS:
        source_entries = source.get(section)
        if not isinstance(source_entries, Mapping):
            continue
        destination_entries = merged.get(section)
        if not isinstance(destination_entries, Mapping):
            destination_entries = {}
        else:
            destination_entries = dict(destination_entries)
        for entry_id, entry in source_entries.items():
            if entry_id in destination_entries:
                continue
            destination_entries[entry_id] = copy.deepcopy(entry)
            seeded += 1
        merged[section] = destination_entries

    merged["schemaVersion"] = max(
        _safe_int(source.get("schemaVersion"), 1),
        _safe_int(merged.get("schemaVersion"), 1),
    )
    if seeded == 0:
        return "unchanged", 0
    _write_yaml_atomic(destination_path, merged)
    return "seeded", seeded


def _read_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, Mapping):
        raise RuntimeError(f"invalid_capability_profile:{path}")
    return dict(data)


def _write_yaml_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-users-data-root", required=True, type=Path)
    parser.add_argument("--destination-users-data-root", required=True, type=Path)
    parser.add_argument("--profile-user-id", default="master")
    args = parser.parse_args()
    status, seeded = seed_local_capability_profile(
        source_users_data_root=args.source_users_data_root,
        destination_users_data_root=args.destination_users_data_root,
        profile_user_id=args.profile_user_id,
    )
    print(f"{status}:{seeded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
