"""Verified public artifacts for Akane's independently packaged libraries.

This module only uses the standard library: it runs before dependencies exist.
Runtime imports still come from installed wheels, never sibling source trees.
"""
from __future__ import annotations

from contextlib import contextmanager
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

SCHEMA = "akane.public-package-supply.v1"
WHEELHOUSE_SCHEMA = "akane.package-wheelhouse.v1"
MANIFEST = "akane-package-wheelhouse.json"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_pins(requirements: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([a-z0-9-]+)==([A-Za-z0-9.+-]+)", line)
        if not match or match[1] in pins:
            raise ValueError("package_supply_exact_pins_required")
        pins[match[1]] = match[2]
    return pins


def _artifact_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", value):
        raise ValueError("package_supply_unsafe_artifact_name")
    return value


def validate_wheelhouse(
    wheelhouse: Path, pins: dict[str, str], *, offline_profile: str | None = None,
) -> dict:
    try:
        manifest = json.loads((wheelhouse / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("package_supply_manifest_invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != WHEELHOUSE_SCHEMA or manifest.get("version") != "0.1.0":
        raise ValueError("package_supply_manifest_contract_mismatch")
    entries = manifest.get("internal_packages")
    if not isinstance(entries, list):
        raise ValueError("package_supply_manifest_entries_invalid")
    seen: dict[str, str] = {}
    files: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("package_supply_manifest_entries_invalid")
        name, version = entry.get("name"), entry.get("version")
        if not isinstance(name, str) or name in seen:
            raise ValueError("package_supply_duplicate_package")
        artifact = _artifact_name(entry.get("file"))
        if artifact in files:
            raise ValueError("package_supply_duplicate_artifact")
        files.add(artifact)
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError("package_supply_artifact_hash_invalid")
        wheel = wheelhouse / artifact
        if not wheel.is_file() or wheel.is_symlink() or sha256(wheel) != expected_hash:
            raise ValueError("package_supply_artifact_hash_mismatch")
        with zipfile.ZipFile(wheel) as archive:
            metadata = [p for p in archive.namelist() if p.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError("package_supply_wheel_metadata_invalid")
            headers = BytesParser().parsebytes(archive.read(metadata[0]))
            normalized = re.sub(r"[-_.]+", "-", headers.get("Name", "")).lower()
            if normalized != name or headers.get("Version") != version:
                raise ValueError("package_supply_wheel_identity_mismatch")
            if any(" @ " in dep or "file:" in dep for dep in headers.get_all("Requires-Dist", [])):
                raise ValueError("package_supply_source_dependency_forbidden")
        seen[name] = version
    if any(seen.get(name) != version for name, version in pins.items()):
        raise ValueError("package_supply_version_set_mismatch")
    if offline_profile:
        if manifest.get("runtime_dependencies_downloaded") is not True:
            raise ValueError("package_supply_runtime_dependency_closure_missing")
        if offline_profile == "full" and manifest.get("profile", "full") != "full":
            raise ValueError("package_supply_full_profile_required")
    return manifest


def load_public_lock(root: Path) -> dict:
    try:
        lock = json.loads((root / "deploy" / "public-package-supply.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("package_supply_public_lock_missing_or_invalid") from exc
    if not isinstance(lock, dict) or lock.get("schema") != SCHEMA:
        raise ValueError("package_supply_public_lock_contract_mismatch")
    if lock.get("package_versions") != exact_pins(root / "requirements-packages.txt"):
        raise ValueError("package_supply_public_lock_version_mismatch")
    artifact = lock.get("internal_wheel_bundle", {})
    if not isinstance(artifact, dict):
        raise ValueError("package_supply_public_artifact_invalid")
    _validate_public_artifact(artifact)
    return lock


def _validate_public_artifact(artifact: dict) -> None:
    url = urlsplit(str(artifact.get("url", "")))
    if (url.scheme != "https" or url.netloc != "github.com" or url.username or url.password
            or not url.path.startswith("/misaka-coder/AkaneCompanionLab/releases/download/")
            or url.query or url.fragment):
        raise ValueError("package_supply_public_url_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256", ""))):
        raise ValueError("package_supply_public_hash_invalid")


def _download(url: str, destination: Path, expected_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == expected_hash:
        return
    temporary = None
    try:
        request = Request(url, headers={"User-Agent": "Akane-Package-Bootstrap/1"})
        with urlopen(request, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".partial", delete=False) as handle:
                temporary = Path(handle.name)
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ValueError("package_supply_download_too_large")
                    handle.write(chunk)
        if sha256(temporary) != expected_hash:
            raise ValueError("package_supply_download_hash_mismatch")
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_bundle(archive_path: Path, destination: Path, pins: dict[str, str]) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        files = archive.infolist()
        if len(files) > 64 or sum(p.file_size for p in files) > MAX_EXPANDED_BYTES:
            raise ValueError("package_supply_archive_too_large")
        names: set[str] = set()
        for item in files:
            name = item.filename
            if (name in names or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000
                    or (name != MANIFEST and name != _artifact_name(name))):
                raise ValueError("package_supply_archive_member_invalid")
            names.add(name)
        destination.mkdir(parents=True, exist_ok=True)
        for item in files:
            (destination / item.filename).write_bytes(archive.read(item))
    manifest = validate_wheelhouse(destination, pins)
    if names != {MANIFEST, *(p["file"] for p in manifest["internal_packages"])}:
        raise ValueError("package_supply_archive_unlisted_artifact")
    return manifest


@contextmanager
def public_wheelhouse(root: Path, *, cache_dir: Path | None = None) -> Iterator[Path]:
    lock = load_public_lock(root)
    artifact = lock["internal_wheel_bundle"]
    cache = cache_dir or root / ".cache" / "package-supply"
    archive = cache / (artifact["sha256"] + ".zip")
    _download(artifact["url"], archive, artifact["sha256"])
    # Each install gets its own extraction: concurrent installs cannot replace
    # files underneath pip, and a cached archive is verified again before use.
    with tempfile.TemporaryDirectory(prefix="verified-", dir=cache) as temporary:
        wheelhouse = Path(temporary)
        extract_bundle(archive, wheelhouse, exact_pins(root / "requirements-packages.txt"))
        yield wheelhouse


@contextmanager
def public_package_sources(root: Path, *, cache_dir: Path | None = None) -> Iterator[Path]:
    lock = load_public_lock(root)
    artifact = lock.get("source_bundle")
    if not isinstance(artifact, dict):
        raise ValueError("package_supply_source_artifact_missing")
    _validate_public_artifact(artifact)
    cache = cache_dir or root / ".cache" / "package-supply"
    archive_path = cache / (artifact["sha256"] + ".zip")
    _download(artifact["url"], archive_path, artifact["sha256"])
    with tempfile.TemporaryDirectory(prefix="verified-sources-", dir=cache) as temporary:
        destination = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            files = archive.infolist()
            if len(files) > 10000 or sum(p.file_size for p in files) > MAX_EXPANDED_BYTES:
                raise ValueError("package_supply_source_archive_too_large")
            for item in files:
                relative = Path(item.filename)
                target = destination / relative
                if (not target.resolve().is_relative_to(destination.resolve())
                        or "\\" in item.filename or ":" in item.filename
                        or any(p in {".git", ".env", "__pycache__"} for p in relative.parts)
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("package_supply_source_archive_unsafe_member")
                if item.is_dir():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
        provenance = json.loads((destination / "akane-package-sources.json").read_text(encoding="utf-8"))
        versions = {p["name"]: p["version"] for p in provenance.get("packages", [])}
        if provenance.get("schema") != "akane.package-sources.v1" or any(
                versions.get(name) != version for name, version in lock["package_versions"].items()):
            raise ValueError("package_supply_source_version_set_mismatch")
        yield destination
