from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AKANE_PARENT = ROOT.parent
EXPECTED_VERSION = "0.1.0"


@dataclass(frozen=True)
class PackageSpec:
    name: str
    import_name: str
    runtime_dependency: bool = False


PACKAGES: tuple[PackageSpec, ...] = (
    PackageSpec("memcore", "memcore", runtime_dependency=True),
    PackageSpec("promptpack-core", "promptpack_core", runtime_dependency=True),
    PackageSpec("charpack-core", "charpack_core", runtime_dependency=True),
    PackageSpec("capcore", "capcore", runtime_dependency=True),
    PackageSpec("capcore-adapter-python", "capcore_adapter_python", runtime_dependency=True),
    PackageSpec("capcore-adapter-mcp", "capcore_adapter_mcp", runtime_dependency=True),
    PackageSpec("capcore-adapter-speech", "capcore_adapter_speech", runtime_dependency=True),
    PackageSpec("capcore-adapter-comfyui", "capcore_adapter_comfyui", runtime_dependency=True),
    PackageSpec("capcore-provider-native-tools", "capcore_provider_native_tools", runtime_dependency=True),
    PackageSpec("capcore-provider-openai", "capcore_provider_openai", runtime_dependency=True),
    PackageSpec("capcore-provider-anthropic", "capcore_provider_anthropic"),
    PackageSpec("capcore-host-utils", "capcore_host_utils"),
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_pyproject(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    if not isinstance(project, dict):
        return {}
    return project


def _dir_has_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(child.is_file() for child in path.rglob("*"))


def _manifest_has_include(manifest: str, filename: str) -> bool:
    pattern = rf"(?m)^\s*include\s+{re.escape(filename)}\b"
    return bool(re.search(pattern, manifest, flags=re.IGNORECASE))


def _manifest_has_recursive_include(manifest: str, directory: str) -> bool:
    pattern = rf"(?m)^\s*recursive-include\s+{re.escape(directory)}\b"
    return bool(re.search(pattern, manifest, flags=re.IGNORECASE))


def _git_status(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "git status failed"
        return [f"git_status_failed:{stderr}"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def _normalized_requirements_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = set()
    for raw_line in _read_text(path).splitlines():
        line = raw_line.split("#", 1)[0].strip().replace("\\", "/")
        if line:
            lines.add(line.lower())
    return lines


def _audit_package(spec: PackageSpec, *, allow_dirty: bool) -> list[str]:
    errors: list[str] = []
    package_root = AKANE_PARENT / spec.name

    if not package_root.is_dir():
        return [f"{spec.name}:missing_package_dir:{package_root}"]

    if not (package_root / ".git").exists():
        errors.append(f"{spec.name}:missing_git_repository")
    elif not allow_dirty:
        dirty = _git_status(package_root)
        if dirty:
            preview = "; ".join(dirty[:5])
            if len(dirty) > 5:
                preview = f"{preview}; ... ({len(dirty)} entries)"
            errors.append(f"{spec.name}:git_not_clean:{preview}")

    pyproject_path = package_root / "pyproject.toml"
    if not pyproject_path.is_file():
        errors.append(f"{spec.name}:missing_pyproject")
    else:
        project = _load_pyproject(pyproject_path)
        if project.get("name") != spec.name:
            errors.append(f"{spec.name}:project_name_mismatch:{project.get('name')!r}")
        if project.get("version") != EXPECTED_VERSION:
            errors.append(f"{spec.name}:version_not_{EXPECTED_VERSION}:{project.get('version')!r}")

    for filename in ("README.md", "AGENTS.md", "LICENSE", "MANIFEST.in"):
        if not (package_root / filename).is_file():
            errors.append(f"{spec.name}:missing_file:{filename}")

    for dirname in ("docs", "examples", "tests"):
        if not _dir_has_files(package_root / dirname):
            errors.append(f"{spec.name}:missing_or_empty_dir:{dirname}")

    manifest_path = package_root / "MANIFEST.in"
    if manifest_path.is_file():
        manifest = _read_text(manifest_path)
        for filename in ("README.md", "AGENTS.md", "LICENSE"):
            if not _manifest_has_include(manifest, filename):
                errors.append(f"{spec.name}:manifest_missing_include:{filename}")
        for dirname in ("docs", "examples", "tests"):
            if (package_root / dirname).exists() and not _manifest_has_recursive_include(manifest, dirname):
                errors.append(f"{spec.name}:manifest_missing_recursive_include:{dirname}")

    return errors


def _audit_akane_runtime_dependencies() -> list[str]:
    errors: list[str] = []
    requirements = _normalized_requirements_lines(ROOT / "requirements.txt")
    bootstrap_path = ROOT / "scripts" / "bootstrap_akane_windows.ps1"
    bootstrap_text = _read_text(bootstrap_path) if bootstrap_path.is_file() else ""

    for spec in PACKAGES:
        if not spec.runtime_dependency:
            continue
        editable = f"-e ../{spec.name}".lower()
        if editable not in requirements:
            errors.append(f"akane:requirements_missing_editable:{editable}")
        if spec.name not in bootstrap_text:
            errors.append(f"akane:bootstrap_missing_source_check:{spec.name}")
        if spec.import_name not in bootstrap_text:
            errors.append(f"akane:bootstrap_missing_import_check:{spec.import_name}")

    return errors


def audit(*, allow_dirty: bool) -> list[str]:
    errors: list[str] = []
    for spec in PACKAGES:
        errors.extend(_audit_package(spec, allow_dirty=allow_dirty))
    errors.extend(_audit_akane_runtime_dependencies())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Akane extracted sibling packages for 0.1 release-state metadata.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Skip package git cleanliness checks while editing release metadata.",
    )
    args = parser.parse_args()

    errors = audit(allow_dirty=args.allow_dirty)
    if errors:
        print("AKANE_EXTRACTED_PACKAGE_RELEASE_AUDIT_FAILED")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    print("AKANE_EXTRACTED_PACKAGE_RELEASE_AUDIT_OK")
    print(f"packages: {len(PACKAGES)}")
    print(f"expected_version: {EXPECTED_VERSION}")
    print("runtime_editables:", sum(1 for package in PACKAGES if package.runtime_dependency))
    return 0


if __name__ == "__main__":
    sys.exit(main())
