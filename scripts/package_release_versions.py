"""Exact Python package versions shared by build and installed-artifact checks."""
from __future__ import annotations

import re
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements-packages.txt"
# Built for consumers, but not an Akane runtime requirement.
RELEASE_ONLY_VERSIONS = {"capcore-host-utils": "0.1.0"}


def expected_package_version(name: str) -> str:
    versions = dict(RELEASE_ONLY_VERSIONS)
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([a-z0-9-]+)==([A-Za-z0-9.+-]+)", line)
        if not match:
            raise ValueError("package_release_exact_pin_required")
        package, version = match.groups()
        if package in versions:
            raise ValueError("package_release_duplicate_pin")
        versions[package] = version
    if name not in versions:
        raise ValueError(f"package_release_pin_missing:{name}")
    return versions[name]
