from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


APP_DIRECTORY_NAME = "Akane"
DATA_ROOT_ENV = "AKANE_DATA_ROOT"


@dataclass(frozen=True)
class AkaneDataPaths:
    root: Path
    users_data: Path
    characters: Path
    state: Path
    logs: Path


@dataclass(frozen=True)
class AkaneDataMigrationResult:
    copied: int = 0
    skipped: int = 0
    failed: int = 0

    def merge(self, other: "AkaneDataMigrationResult") -> "AkaneDataMigrationResult":
        return AkaneDataMigrationResult(
            copied=self.copied + other.copied,
            skipped=self.skipped + other.skipped,
            failed=self.failed + other.failed,
        )


def resolve_akane_data_root(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | str | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    explicit = str(values.get(DATA_ROOT_ENV, "") or "").strip()
    if explicit:
        return _absolute_path(explicit)

    current_platform = platform or sys.platform
    home_path = Path(home).expanduser() if home is not None else Path.home()

    if current_platform.startswith("win"):
        base = str(values.get("LOCALAPPDATA", "") or values.get("APPDATA", "") or "").strip()
        if base:
            return _absolute_path(base) / APP_DIRECTORY_NAME
        return _absolute_path(home_path / "AppData" / "Local") / APP_DIRECTORY_NAME

    if current_platform == "darwin":
        return _absolute_path(home_path / "Library" / "Application Support") / APP_DIRECTORY_NAME

    xdg_data_home = str(values.get("XDG_DATA_HOME", "") or "").strip()
    base = _absolute_path(xdg_data_home) if xdg_data_home else _absolute_path(home_path / ".local" / "share")
    return base / APP_DIRECTORY_NAME


def get_akane_data_paths(**kwargs: object) -> AkaneDataPaths:
    root = resolve_akane_data_root(**kwargs)
    return AkaneDataPaths(
        root=root,
        users_data=root / "users_data",
        characters=root / "characters",
        state=root / "state",
        logs=root / "logs",
    )


def ensure_akane_data_paths(paths: AkaneDataPaths | None = None) -> AkaneDataPaths:
    resolved = paths or get_akane_data_paths()
    for directory in (
        resolved.root,
        resolved.users_data,
        resolved.characters,
        resolved.state,
        resolved.logs,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return resolved


def migrate_legacy_data(
    project_root: Path | str,
    *,
    paths: AkaneDataPaths | None = None,
) -> AkaneDataMigrationResult:
    resolved = ensure_akane_data_paths(paths)
    project = Path(project_root)
    result = _copy_missing_tree(project / "users_data", resolved.users_data)
    return result.merge(
        _copy_missing_tree(
            project / "desktop_pet_creator_kit" / "characters",
            resolved.characters,
        )
    )


def _copy_missing_tree(source: Path, destination: Path) -> AkaneDataMigrationResult:
    if not source.is_dir() or source.resolve() == destination.resolve():
        return AkaneDataMigrationResult()

    destination.mkdir(parents=True, exist_ok=True)
    result = AkaneDataMigrationResult()
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_symlink():
            result = result.merge(AkaneDataMigrationResult(skipped=1))
            continue
        if entry.is_dir():
            result = result.merge(_copy_missing_tree(entry, target))
            continue
        if not entry.is_file():
            result = result.merge(AkaneDataMigrationResult(skipped=1))
            continue
        if target.exists():
            result = result.merge(AkaneDataMigrationResult(skipped=1))
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with entry.open("rb") as source_file, target.open("xb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)
            shutil.copystat(entry, target, follow_symlinks=False)
            result = result.merge(AkaneDataMigrationResult(copied=1))
        except FileExistsError:
            result = result.merge(AkaneDataMigrationResult(skipped=1))
        except OSError:
            try:
                if target.is_file():
                    target.unlink()
            except OSError:
                pass
            result = result.merge(AkaneDataMigrationResult(failed=1))
    return result


def _absolute_path(value: Path | str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path
