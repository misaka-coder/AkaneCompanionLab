#!/usr/bin/env python3
"""Offline, one-source-to-one-instance Akane data-root migration."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1
BINDING_FILENAME = "instance-binding.json"
INCOMPLETE_FILENAME = "migration-incomplete.json"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class MigrationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "migration_failed")
        super().__init__(self.reason)


def migrate_instance(
    *,
    source_root: Path,
    target_root: Path,
    instance_id: str,
    character_pack_id: str,
    source_instance_id: str = "",
    source_workspace: Path | None = None,
    care_enabled: bool = True,
) -> dict[str, Any]:
    target_id = _safe_id(instance_id, reason="invalid_instance_id")
    pack_id = _safe_id(character_pack_id, reason="invalid_character_pack_id")
    old_instance_id = _safe_id(
        source_instance_id or target_id,
        reason="invalid_source_instance_id",
    )
    source = _existing_real_directory(source_root, reason="source_root_unavailable")
    target = _resolve_new_target(target_root)
    if source == target:
        raise MigrationError("source_and_target_must_differ")
    _verify_source_binding(source, old_instance_id)
    _assert_source_not_running(source)
    _verify_source_databases_offline(source)

    target.mkdir(parents=True, exist_ok=False)
    marker = target / INCOMPLETE_FILENAME
    _atomic_json(
        marker,
        {
            "schema_version": SCHEMA_VERSION,
            "instance_id": target_id,
            "status": "incomplete",
            "reason": "migration_in_progress",
        },
    )
    copied_files = 0
    try:
        engine_source = source / "users_data" / "akane_memory_v01"
        engine_target = target / "users_data" / "akane_memory_v01"
        copied_files += _copy_required_file(
            engine_source / "akane_memory_v01.db",
            engine_target / "akane_memory_v01.db",
        )
        copied_files += _copy_optional_file(
            engine_source / "memcore_v01.db",
            engine_target / "memcore_v01.db",
        )
        copied_files += _copy_optional_file(
            engine_source / "care_runtime.json",
            engine_target / "care_runtime.json",
        )
        copied_files += _copy_optional_tree(
            engine_source / "generic_npc_memory_v01",
            engine_target / "generic_npc_memory_v01",
        )
        copied_files += _copy_optional_tree(
            engine_source / "user_assets",
            engine_target / "user_assets",
        )
        copied_files += _copy_optional_file(
            source / "state" / "qq_gateway_state.json",
            target / "state" / "qq_gateway_state.json",
        )

        workspace_source = source_workspace or (source / "workspace")
        if workspace_source.exists():
            workspace_source = _existing_real_directory(
                workspace_source,
                reason="source_workspace_unavailable",
            )
            for layer in ("Inbox", "Outputs", "Archive"):
                copied_files += _copy_optional_tree(
                    workspace_source / layer,
                    target / "workspace" / layer,
                )

        copied_files += _copy_optional_tree(
            source / "instances" / old_instance_id / "plugins",
            target / "instances" / target_id / "plugins",
        )
        copied_files += _copy_optional_tree(
            source / "characters",
            target / "characters",
            skip_dir=lambda path: path.name == "_local",
        )

        _validate_sqlite_tree(target)
        _write_instance_manifest(
            target,
            instance_id=target_id,
            character_pack_id=pack_id,
            care_enabled=care_enabled,
        )
        _atomic_json(
            target / BINDING_FILENAME,
            {"schema_version": SCHEMA_VERSION, "instance_id": target_id},
        )
        marker.unlink()
        return {
            "ok": True,
            "status": "completed",
            "instance_id": target_id,
            "copied_files": copied_files,
            "source_unchanged": True,
        }
    except MigrationError as exc:
        _mark_incomplete(marker, instance_id=target_id, reason=exc.reason)
        raise
    except Exception as exc:
        _mark_incomplete(marker, instance_id=target_id, reason="unexpected_migration_failure")
        raise MigrationError("unexpected_migration_failure") from exc


def _safe_id(value: str, *, reason: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID_RE.fullmatch(text):
        raise MigrationError(reason)
    return text


def _existing_real_directory(value: Path, *, reason: str) -> Path:
    path = Path(value).expanduser()
    try:
        if path.is_symlink() or not path.is_dir():
            raise MigrationError(reason)
        return path.resolve(strict=True)
    except OSError as exc:
        raise MigrationError(reason) from exc


def _resolve_new_target(value: Path) -> Path:
    path = Path(value).expanduser()
    try:
        if path.exists() or path.is_symlink():
            raise MigrationError("target_root_must_not_exist")
        parent = path.parent.resolve(strict=True)
        target = (parent / path.name).resolve()
        if target.parent != parent:
            raise MigrationError("target_root_unsafe")
        return target
    except OSError as exc:
        raise MigrationError("target_parent_unavailable") from exc


def _verify_source_binding(source: Path, expected_instance_id: str) -> None:
    binding = source / BINDING_FILENAME
    if not binding.exists():
        return
    if binding.is_symlink() or not binding.is_file():
        raise MigrationError("source_binding_invalid")
    try:
        payload = json.loads(binding.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("source_binding_invalid") from exc
    if payload != {"schema_version": SCHEMA_VERSION, "instance_id": expected_instance_id}:
        raise MigrationError("source_binding_mismatch")


def _assert_source_not_running(source: Path) -> None:
    lock_path = source / "run" / "instance.lock"
    if not lock_path.exists():
        return
    if lock_path.is_symlink() or not lock_path.is_file():
        raise MigrationError("source_lock_invalid")
    fd = -1
    lock_kind = ""
    try:
        fd = os.open(lock_path, os.O_RDWR)
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            lock_kind = "windows"
        elif os.name == "posix":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_kind = "posix"
        else:
            raise MigrationError("source_lock_platform_unsupported")
    except (OSError, ImportError) as exc:
        raise MigrationError("source_instance_running") from exc
    finally:
        if fd >= 0:
            try:
                if lock_kind == "windows":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                elif lock_kind == "posix":
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _verify_source_databases_offline(source: Path) -> None:
    engine = source / "users_data" / "akane_memory_v01"
    candidates = [
        engine / "akane_memory_v01.db",
        engine / "memcore_v01.db",
        engine / "generic_npc_memory_v01" / "akane_memory_v01.db",
    ]
    for database in candidates:
        if not database.exists():
            continue
        if database.is_symlink() or not database.is_file():
            raise MigrationError("source_database_invalid")
        if database.with_name(database.name + "-wal").exists():
            raise MigrationError("source_database_not_clean")
        _sqlite_quick_check(database, reason="source_database_check_failed")


def _copy_required_file(source: Path, target: Path) -> int:
    if not source.exists():
        raise MigrationError("source_main_database_missing")
    return _copy_file(source, target)


def _copy_optional_file(source: Path, target: Path) -> int:
    return _copy_file(source, target) if source.exists() else 0


def _copy_file(source: Path, target: Path) -> int:
    if source.is_symlink() or not source.is_file():
        raise MigrationError("source_contains_unsupported_entry")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise MigrationError("target_collision")
    shutil.copy2(source, target, follow_symlinks=False)
    return 1


def _copy_optional_tree(
    source: Path,
    target: Path,
    *,
    skip_dir: Callable[[Path], bool] | None = None,
) -> int:
    if not source.exists():
        return 0
    if source.is_symlink() or not source.is_dir():
        raise MigrationError("source_contains_unsupported_entry")
    count = 0
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise MigrationError("source_contains_symlink")
        destination = target / entry.name
        if entry.is_dir():
            if skip_dir is not None and skip_dir(entry):
                continue
            count += _copy_optional_tree(entry, destination, skip_dir=skip_dir)
        elif entry.is_file():
            count += _copy_file(entry, destination)
        else:
            raise MigrationError("source_contains_unsupported_entry")
    return count


def _validate_sqlite_tree(target: Path) -> None:
    engine = target / "users_data" / "akane_memory_v01"
    for database in (
        engine / "akane_memory_v01.db",
        engine / "memcore_v01.db",
        engine / "generic_npc_memory_v01" / "akane_memory_v01.db",
    ):
        if database.exists():
            _sqlite_quick_check(database, reason="target_database_check_failed")


def _sqlite_quick_check(path: Path, *, reason: str) -> None:
    connection: sqlite3.Connection | None = None
    row: tuple[Any, ...] | None = None
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        row = connection.execute("PRAGMA quick_check").fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise MigrationError(reason) from exc
    finally:
        if connection is not None:
            connection.close()
    if not row or str(row[0]).lower() != "ok":
        raise MigrationError(reason)


def _write_instance_manifest(
    target: Path,
    *,
    instance_id: str,
    character_pack_id: str,
    care_enabled: bool,
) -> None:
    content = (
        f"schema_version = {SCHEMA_VERSION}\n"
        f'instance_id = "{instance_id}"\n'
        f'character_pack_id = "{character_pack_id}"\n\n'
        "[features]\n"
        f"care = {'true' if care_enabled else 'false'}\n\n"
        "[channels.qq]\n"
        "enabled = false\n"
        'profile_ref = ""\n'
    )
    path = target / "instances" / instance_id / "instance.toml"
    _atomic_text(path, content)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _mark_incomplete(marker: Path, *, instance_id: str, reason: str) -> None:
    try:
        _atomic_json(
            marker,
            {
                "schema_version": SCHEMA_VERSION,
                "instance_id": instance_id,
                "status": "incomplete",
                "reason": str(reason or "migration_failed")[:120],
            },
        )
    except OSError:
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline one-to-one Akane instance migration")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--character-pack-id", required=True)
    parser.add_argument("--source-instance-id", default="")
    parser.add_argument("--source-workspace", type=Path)
    parser.add_argument("--disable-care", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = migrate_instance(
            source_root=args.source_root,
            target_root=args.target_root,
            instance_id=args.instance_id,
            character_pack_id=args.character_pack_id,
            source_instance_id=args.source_instance_id,
            source_workspace=args.source_workspace,
            care_enabled=not args.disable_care,
        )
    except MigrationError as exc:
        print(json.dumps({"ok": False, "status": "failed", "reason": exc.reason}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
