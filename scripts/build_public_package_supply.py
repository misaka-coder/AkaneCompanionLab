"""Build public wheels and matching source archives from committed package code.

Uncommitted library edits and local runtime/research data are never exported.
The source archive can be passed directly to build_extracted_package_wheelhouse
after extraction; consumer installations continue to use ordinary wheels.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_extracted_package_wheelhouse import PACKAGES, build_wheelhouse
from scripts.package_supply import MANIFEST, SCHEMA, exact_pins, sha256, validate_wheelhouse

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_METADATA = {"pyproject.toml", "README.md", "README_EN.md", "LICENSE", "NOTICE", "MANIFEST.in"}


def committed_runtime_source(repository: Path, destination: Path, module: str) -> str:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    raw = subprocess.check_output(["git", "archive", "--format=zip", commit], cwd=repository)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            relative = Path(item.filename)
            if not relative.parts or (relative.parts[0] != module and item.filename not in PACKAGE_METADATA):
                continue
            if item.is_dir():
                continue
            target = destination / relative
            if not target.resolve().is_relative_to(destination.resolve()) or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("public_package_source_unsafe_member")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
    return commit


def build(*, source_root: Path, output_dir: Path, release_tag: str,
          no_build_isolation: bool = False, write_lock: Path | None = None) -> dict:
    if not release_tag or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in release_tag):
        raise ValueError("public_package_release_tag_invalid")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    provenance = []
    with tempfile.TemporaryDirectory(prefix="akane-public-package-source-") as temporary:
        sources = Path(temporary)
        for spec in PACKAGES:
            name = spec.source_name or spec.name
            commit = committed_runtime_source(source_root / name, sources / name,
                                              spec.import_name or spec.wheel_prefix)
            provenance.append({"name": spec.name, "source_directory": name, "commit": commit})
        wheels = output_dir / "internal-wheels"
        manifest = build_wheelhouse(source_root=sources, output_dir=wheels, download_runtime=False,
                                   no_build_isolation=no_build_isolation)
        validate_wheelhouse(wheels, exact_pins(ROOT / "requirements-packages.txt"))
        versions = {p["name"]: p["version"] for p in manifest["internal_packages"]}
        for entry in provenance:
            entry["version"] = versions[entry["name"]]
        bundle = output_dir / "akane-internal-packages-py3-any.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(wheels / MANIFEST, MANIFEST)
            for entry in manifest["internal_packages"]:
                archive.write(wheels / entry["file"], entry["file"])
        source_bundle = output_dir / "akane-package-sources.zip"
        with zipfile.ZipFile(source_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(sources.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(sources)
                # Builds create egg-info; export only the original runtime tree
                # and its build metadata, README and license.
                spec = next(s for s in PACKAGES if (s.source_name or s.name) == relative.parts[0])
                module = spec.import_name or spec.wheel_prefix
                if relative.parts[1] != module and len(relative.parts[1:]) != 1:
                    continue
                if relative.parts[1] != module and relative.name not in PACKAGE_METADATA:
                    continue
                if any(p in {"__pycache__", "build", "dist"} for p in relative.parts) or path.suffix in {".pyc", ".pyo"}:
                    continue
                archive.write(path, relative.as_posix())
            archive.writestr("akane-package-sources.json", json.dumps({
                "schema": "akane.package-sources.v1", "packages": provenance,
                "scope": "Committed runtime sources, package metadata and licenses; no local data or uncommitted edits.",
            }, ensure_ascii=False, indent=2) + "\n")
    base = f"https://github.com/misaka-coder/AkaneCompanionLab/releases/download/{release_tag}/"
    lock = {"schema": SCHEMA, "release_tag": release_tag,
            "package_versions": exact_pins(ROOT / "requirements-packages.txt"),
            "internal_wheel_bundle": {"url": base + bundle.name, "sha256": sha256(bundle)},
            "source_bundle": {"url": base + source_bundle.name, "sha256": sha256(source_bundle)}}
    lock_path = output_dir / "public-package-supply.json"
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "SHA256SUMS").write_text(
        f"{sha256(bundle)}  {bundle.name}\n{sha256(source_bundle)}  {source_bundle.name}\n", encoding="utf-8")
    if write_lock:
        write_lock.parent.mkdir(parents=True, exist_ok=True)
        temporary_lock = write_lock.with_suffix(write_lock.suffix + ".tmp")
        temporary_lock.write_bytes(lock_path.read_bytes())
        temporary_lock.replace(write_lock)
    return lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--no-build-isolation", action="store_true")
    parser.add_argument("--write-lock", type=Path)
    args = parser.parse_args()
    try:
        build(source_root=args.source_root.resolve(), output_dir=args.output_dir,
              release_tag=args.release_tag, no_build_isolation=args.no_build_isolation, write_lock=args.write_lock)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"AKANE_PUBLIC_PACKAGE_SUPPLY_FAILED:{type(exc).__name__}", file=sys.stderr)
        return 1
    print("AKANE_PUBLIC_PACKAGE_SUPPLY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
