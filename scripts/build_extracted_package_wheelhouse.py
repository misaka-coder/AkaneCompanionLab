from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.1.0"


@dataclass(frozen=True)
class PackageSpec:
    name: str

    @property
    def wheel_prefix(self) -> str:
        return self.name.replace("-", "_")


PACKAGES: tuple[PackageSpec, ...] = (
    PackageSpec("capcore"),
    PackageSpec("memcore"),
    PackageSpec("promptpack-core"),
    PackageSpec("charpack-core"),
    PackageSpec("channelcore-onebot"),
    PackageSpec("capcore-adapter-python"),
    PackageSpec("capcore-adapter-mcp"),
    PackageSpec("capcore-adapter-speech"),
    PackageSpec("capcore-adapter-comfyui"),
    PackageSpec("capcore-provider-native-tools"),
    PackageSpec("capcore-provider-openai"),
    PackageSpec("capcore-provider-anthropic"),
    PackageSpec("capcore-host-utils"),
)

RELEASE_TEXT_SUFFIXES = frozenset({".md", ".py", ".toml", ".json", ".yaml", ".yml", ".txt"})
IGNORED_SOURCE_PARTS = frozenset({".git", ".venv", ".claude", ".agents", ".vscode", "build", "dist", "__pycache__"})


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _load_project(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    project = payload.get("project")
    return project if isinstance(project, dict) else {}


def _assert_source_manifest(spec: PackageSpec, source_root: Path) -> Path:
    package_root = source_root / spec.name
    pyproject = package_root / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"{spec.name}:missing_pyproject")

    raw = pyproject.read_text(encoding="utf-8")
    normalized = raw.replace("\\", "/").lower()
    if "[tool.uv.sources]" in normalized:
        raise RuntimeError(f"{spec.name}:tool_uv_sources_forbidden")
    if 'path = "../' in normalized or "editable = true" in normalized:
        raise RuntimeError(f"{spec.name}:source_path_dependency_forbidden")

    project = _load_project(pyproject)
    if project.get("name") != spec.name:
        raise RuntimeError(f"{spec.name}:project_name_mismatch")
    if project.get("version") != EXPECTED_VERSION:
        raise RuntimeError(f"{spec.name}:project_version_mismatch:{project.get('version')}")

    for path in package_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in RELEASE_TEXT_SUFFIXES:
            continue
        if any(part in IGNORED_SOURCE_PARTS for part in path.relative_to(package_root).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower().replace("\\\\", "\\")
        if any(root in text for root in ("f:/akane", "f:\\akane", "f:/cache", "f:\\cache")):
            raise RuntimeError(f"{spec.name}:source_contains_machine_path:{path.relative_to(package_root)}")
        if "examples" in path.relative_to(package_root).parts and "sys.path.insert" in text:
            raise RuntimeError(f"{spec.name}:example_injects_source_path:{path.relative_to(package_root)}")
    return package_root


def _wheel_for(spec: PackageSpec, output_dir: Path) -> Path:
    matches = sorted(output_dir.glob(f"{spec.wheel_prefix}-{EXPECTED_VERSION}-*.whl"))
    if len(matches) != 1:
        raise RuntimeError(f"{spec.name}:expected_one_wheel:found_{len(matches)}")
    return matches[0]


def _assert_wheel_metadata(spec: PackageSpec, wheel: Path, source_root: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"{spec.name}:wheel_metadata_missing_or_ambiguous")
        metadata = archive.read(metadata_names[0]).decode("utf-8")

    normalized = metadata.replace("\\", "/").lower()
    forbidden = (
        "requires-dist: ../",
        "requires-dist: file:",
        "requires-dist: -e ",
        source_root.as_posix().lower(),
    )
    for token in forbidden:
        if token and token in normalized:
            raise RuntimeError(f"{spec.name}:wheel_metadata_contains_forbidden_source:{token}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_wheelhouse(
    *, source_root: Path, output_dir: Path, download_runtime: bool, reuse_internal: bool = False
) -> dict[str, object]:
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not reuse_internal:
        for spec in PACKAGES:
            for stale in output_dir.glob(f"{spec.wheel_prefix}-*.whl"):
                stale.unlink()

    built: list[dict[str, str]] = []
    for spec in PACKAGES:
        package_root = _assert_source_manifest(spec, source_root)
        if not reuse_internal:
            _run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--wheel",
                    "--outdir",
                    str(output_dir),
                    str(package_root),
                ]
            )
        wheel = _wheel_for(spec, output_dir)
        _assert_wheel_metadata(spec, wheel, source_root)
        built.append({"name": spec.name, "version": EXPECTED_VERSION, "file": wheel.name, "sha256": _sha256(wheel)})

    if download_runtime:
        runtime_requirements = ROOT / "requirements-runtime.txt"
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--dest",
                str(output_dir),
                "-r",
                str(runtime_requirements),
            ]
        )

    manifest = {
        "schema": "akane.package-wheelhouse.v1",
        "version": EXPECTED_VERSION,
        "internal_packages": built,
        "runtime_dependencies_downloaded": bool(download_runtime),
    }
    (output_dir / "akane-package-wheelhouse.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build path-free Akane package wheels and an optional complete offline runtime wheelhouse.",
    )
    parser.add_argument("--source-root", type=Path, default=ROOT.parent)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "package_wheels")
    parser.add_argument(
        "--internal-only",
        action="store_true",
        help="Build only Akane-owned wheels; the result cannot bootstrap Akane offline.",
    )
    parser.add_argument(
        "--reuse-internal",
        action="store_true",
        help="Reuse and re-audit existing internal wheels before downloading the runtime closure.",
    )
    args = parser.parse_args()

    if shutil.which("python") is None and not Path(sys.executable).is_file():
        print("AKANE_PACKAGE_WHEELHOUSE_FAILED:python_not_found")
        return 1
    try:
        manifest = build_wheelhouse(
            source_root=args.source_root,
            output_dir=args.output_dir,
            download_runtime=not args.internal_only,
            reuse_internal=args.reuse_internal,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"AKANE_PACKAGE_WHEELHOUSE_FAILED:{exc}")
        return 1

    print("AKANE_PACKAGE_WHEELHOUSE_OK")
    print(f"packages: {len(manifest['internal_packages'])}")
    print(f"runtime_dependencies_downloaded: {manifest['runtime_dependencies_downloaded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
