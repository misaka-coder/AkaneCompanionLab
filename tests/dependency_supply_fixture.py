"""Provide a real offline dependency wheelhouse for plugin installation tests.

Managed plugin installs stage `Requires-Dist` before publishing a candidate, so a
test that installs a first-party wheel with third-party runtime dependencies must
give `ManagedPluginArtifactStore` the same explicit supply source a deployment
would configure. This helper reuses the repository wheelhouse and only downloads
a requirement that the offline wheelhouse cannot satisfy, so the fixture stays
offline for every dependency the repository already ships.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_WHEELHOUSE = ROOT / "package_wheels"
_CACHE = ROOT / "work" / "dependency-supply-wheelhouse"
_READY: dict[tuple[str, ...], Path] = {}


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _download(requirement: str, target: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "download", "--no-deps", "--dest", str(target), requirement],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "test dependency supply could not fetch "
            f"{requirement}: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def wheelhouse(*requirements: str) -> Path:
    """Return an offline wheelhouse that satisfies the given requirements."""

    key = tuple(sorted(requirements))
    cached = _READY.get(key)
    if cached is not None:
        return cached
    _CACHE.mkdir(parents=True, exist_ok=True)
    if REPOSITORY_WHEELHOUSE.is_dir():
        for wheel in REPOSITORY_WHEELHOUSE.iterdir():
            if wheel.is_file() and wheel.suffix == ".whl":
                _link_or_copy(wheel, _CACHE / wheel.name)
    for requirement in requirements:
        probe = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--dry-run",
                # The host environment must not satisfy a requirement the
                # offline wheelhouse cannot, or the fixture would silently pass
                # while a real deployment install fails.
                "--ignore-installed",
                "--no-index",
                "--find-links",
                str(_CACHE),
                requirement,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            _download(requirement, _CACHE)
    _READY[key] = _CACHE
    return _CACHE


def plugin_requirements(*plugin_directories: str) -> tuple[str, ...]:
    """Read the runtime requirements a first-party plugin declares."""

    requirements: list[str] = []
    for directory in plugin_directories:
        pyproject = ROOT / "plugins" / directory / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"].get("dependencies") or ()
        for requirement in declared:
            if requirement.split(" ")[0].split("=")[0].split("<")[0].split(">")[0].strip().lower() in {
                "akane-plugin",
                "capcore",
            }:
                # Host-provided public contracts are not installed from supply.
                continue
            requirements.append(requirement)
    return tuple(requirements)


def wheelhouse_for_plugins(*plugin_directories: str) -> Path:
    """Return an offline wheelhouse covering the given plugins' requirements.

    Source installs with a configured supply also build in isolation from that
    supply, so the common build requirements are included explicitly instead of
    silently falling back to the host environment.
    """

    return wheelhouse(*plugin_requirements(*plugin_directories), "setuptools>=68", "wheel")
