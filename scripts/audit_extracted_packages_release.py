from __future__ import annotations

import argparse
import json
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
    PackageSpec("voicecore", "voicecore", runtime_dependency=True),
    PackageSpec("promptpack-core", "promptpack_core", runtime_dependency=True),
    PackageSpec("charpack-core", "charpack_core", runtime_dependency=True),
    PackageSpec("channelcore-onebot", "channelcore_onebot", runtime_dependency=True),
    PackageSpec("capcore", "capcore", runtime_dependency=True),
    PackageSpec("capcore-adapter-python", "capcore_adapter_python", runtime_dependency=True),
    PackageSpec("capcore-adapter-mcp", "capcore_adapter_mcp", runtime_dependency=True),
    PackageSpec("capcore-adapter-speech", "capcore_adapter_speech", runtime_dependency=True),
    PackageSpec("capcore-adapter-comfyui", "capcore_adapter_comfyui", runtime_dependency=True),
    PackageSpec("capcore-provider-native-tools", "capcore_provider_native_tools", runtime_dependency=True),
    PackageSpec("capcore-provider-openai", "capcore_provider_openai", runtime_dependency=True),
    PackageSpec("capcore-provider-anthropic", "capcore_provider_anthropic", runtime_dependency=True),
    PackageSpec("capcore-host-utils", "capcore_host_utils"),
)

PETDESK_PACKAGES: tuple[str, ...] = (
    "petcore-protocol",
    "petdesk-character-host",
    "petdesk-live2d-pixi-driver",
    "petdesk-runtime",
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
        raw_pyproject = _read_text(pyproject_path)
        normalized_pyproject = raw_pyproject.replace("\\", "/").lower()
        if "[tool.uv.sources]" in normalized_pyproject:
            errors.append(f"{spec.name}:tool_uv_sources_forbidden")
        if 'path = "../' in normalized_pyproject or "editable = true" in normalized_pyproject:
            errors.append(f"{spec.name}:source_path_dependency_forbidden")
        project = _load_pyproject(pyproject_path)
        if project.get("name") != spec.name:
            errors.append(f"{spec.name}:project_name_mismatch:{project.get('name')!r}")
        if project.get("version") != EXPECTED_VERSION:
            errors.append(f"{spec.name}:version_not_{EXPECTED_VERSION}:{project.get('version')!r}")

    lock_path = package_root / "uv.lock"
    if lock_path.is_file():
        normalized_lock = _read_text(lock_path).replace("\\", "/").lower()
        if 'editable = "../' in normalized_lock or 'directory = "../' in normalized_lock:
            errors.append(f"{spec.name}:lock_contains_source_path")

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

    release_text_suffixes = {".md", ".py", ".toml", ".json", ".yaml", ".yml", ".txt"}
    ignored_parts = {".git", ".venv", ".claude", ".agents", ".vscode", "build", "dist", "__pycache__"}
    for path in package_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in release_text_suffixes:
            continue
        relative = path.relative_to(package_root)
        if any(part in ignored_parts for part in relative.parts):
            continue
        text = _read_text(path).lower().replace("\\\\", "\\")
        if any(root in text for root in ("f:/akane", "f:\\akane", "f:/cache", "f:\\cache")):
            errors.append(f"{spec.name}:source_contains_machine_path:{relative}")
        if "examples" in relative.parts and "sys.path.insert" in text:
            errors.append(f"{spec.name}:example_injects_source_path:{relative}")

    return errors


def _audit_akane_runtime_dependencies() -> list[str]:
    errors: list[str] = []
    requirements = _normalized_requirements_lines(ROOT / "requirements-packages.txt")
    aggregate_requirements = _normalized_requirements_lines(ROOT / "requirements.txt")
    bootstrap_path = ROOT / "scripts" / "bootstrap_akane_windows.ps1"
    bootstrap_text = _read_text(bootstrap_path) if bootstrap_path.is_file() else ""
    checker_path = ROOT / "scripts" / "check_packaged_dependencies.py"
    checker_text = _read_text(checker_path) if checker_path.is_file() else ""

    for spec in PACKAGES:
        if not spec.runtime_dependency:
            continue
        pin = f"{spec.name}=={EXPECTED_VERSION}".lower()
        if pin not in requirements:
            errors.append(f"akane:requirements_missing_exact_pin:{pin}")
        if spec.name not in checker_text:
            errors.append(f"akane:packaged_dependency_check_missing_distribution:{spec.name}")
        if spec.import_name not in checker_text:
            errors.append(f"akane:packaged_dependency_check_missing_import:{spec.import_name}")

    if "-r requirements-packages.txt" not in aggregate_requirements:
        errors.append("akane:requirements_missing_package_release_set")
    for line in (*requirements, *aggregate_requirements):
        if line.startswith("-e ") or "../" in line:
            errors.append(f"akane:source_requirement_forbidden:{line}")

    required_bootstrap_contract = (
        "check_packaged_dependencies.py",
        "akane-package-wheelhouse.json",
        "AKANE_PACKAGE_INDEX_URL",
        "--no-index",
        "--find-links",
        "akane_package_wheelhouse_invalid",
        "internal_artifact_hash_mismatch",
        "runtime_dependency_closure_missing",
    )
    for marker in required_bootstrap_contract:
        if marker not in bootstrap_text:
            errors.append(f"akane:bootstrap_missing_package_artifact_contract:{marker}")
    if "Assert-CoreSourceDependencies" in bootstrap_text or "source checkout was not found" in bootstrap_text:
        errors.append("akane:bootstrap_still_requires_sibling_source")

    for smoke_name in ("smoke_extracted_package_ecosystem.py", "smoke_ai_product_host_turn.py"):
        smoke_text = _read_text(ROOT / "scripts" / smoke_name)
        if "_add_sibling_sources" in smoke_text or "sys.path.insert" in smoke_text:
            errors.append(f"akane:{smoke_name}:sibling_source_injection_forbidden")

    memcore_manager_text = _read_text(ROOT / "companion_v01" / "memcore_integration" / "manager.py")
    if 'parent / "memcore"' in memcore_manager_text or "sys.path.insert" in memcore_manager_text:
        errors.append("akane:memcore_manager:sibling_source_fallback_forbidden")

    petdesk_runtime_scripts = (
        "build_petdesk_runtime_release.ps1",
        "check_petdesk_release.ps1",
        "export_petdesk_release_bundle.ps1",
        "start_petdesk_runtime.ps1",
        "stop_petdesk_runtime.ps1",
    )
    for script_name in petdesk_runtime_scripts:
        script_text = _read_text(ROOT / "scripts" / script_name).replace("\\", "/")
        if "../petdesk-runtime" in script_text:
            errors.append(f"akane:{script_name}:implicit_sibling_runtime_forbidden")
        if "PETDESK_RUNTIME_ROOT" not in script_text or "petdesk_runtime_dir_required" not in script_text:
            errors.append(f"akane:{script_name}:missing_explicit_runtime_root_contract")

    return errors


def _audit_petdesk_packages() -> list[str]:
    errors: list[str] = []
    text_suffixes = {".md", ".ts", ".js", ".json", ".toml", ".yaml", ".yml", ".txt"}
    ignored_parts = {".git", ".claude", ".agents", ".vscode", "node_modules", "dist", "target"}
    for package_name in PETDESK_PACKAGES:
        package_root = AKANE_PARENT / package_name
        package_json = package_root / "package.json"
        if not package_json.is_file():
            errors.append(f"{package_name}:missing_package_json")
            continue
        try:
            payload = json.loads(_read_text(package_json))
        except ValueError:
            errors.append(f"{package_name}:invalid_package_json")
            continue
        if payload.get("name") != package_name:
            errors.append(f"{package_name}:package_name_mismatch")
        if payload.get("version") != EXPECTED_VERSION:
            errors.append(f"{package_name}:version_not_{EXPECTED_VERSION}")
        normalized = _read_text(package_json).replace("\\", "/").lower()
        if "file:../" in normalized or "link:../" in normalized:
            errors.append(f"{package_name}:source_path_dependency_forbidden")
        dependencies = payload.get("dependencies")
        if package_name != "petcore-protocol":
            version = dependencies.get("petcore-protocol") if isinstance(dependencies, dict) else None
            if version != EXPECTED_VERSION:
                errors.append(f"{package_name}:petcore_protocol_not_exact_{EXPECTED_VERSION}:{version!r}")
        lock_path = package_root / "pnpm-lock.yaml"
        if lock_path.is_file():
            normalized_lock = _read_text(lock_path).replace("\\", "/").lower()
            if "file:../" in normalized_lock or "directory: ../" in normalized_lock:
                errors.append(f"{package_name}:lock_contains_source_path")
        for path in package_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            relative = path.relative_to(package_root)
            if any(part in ignored_parts for part in relative.parts):
                continue
            text = _read_text(path).lower().replace("\\\\", "\\")
            if any(root in text for root in ("f:/akane", "f:\\akane", "f:/cache", "f:\\cache")):
                errors.append(f"{package_name}:source_contains_machine_path:{relative}")

        cargo_config = package_root / ".cargo" / "config.toml"
        if cargo_config.is_file():
            cargo_config_text = _read_text(cargo_config)
            if re.search(r'(?im)^\s*target-dir\s*=\s*"[a-z]:[\\/]', cargo_config_text):
                errors.append(f"{package_name}:cargo_target_uses_machine_path")

    runtime_bridge = AKANE_PARENT / "petdesk-runtime" / "examples" / "runtime-bridge-process.ts"
    if runtime_bridge.is_file():
        bridge_text = _read_text(runtime_bridge).replace("\\", "/")
        if "../../petdesk-character-host" in bridge_text:
            errors.append("petdesk-runtime:starter_assumes_sibling_character_host")
        if "PETDESK_CHARACTER_HOST_DIR" not in bridge_text:
            errors.append("petdesk-runtime:starter_missing_explicit_character_host_contract")
    return errors


def audit(*, allow_dirty: bool) -> list[str]:
    errors: list[str] = []
    for spec in PACKAGES:
        errors.extend(_audit_package(spec, allow_dirty=allow_dirty))
    errors.extend(_audit_akane_runtime_dependencies())
    errors.extend(_audit_petdesk_packages())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit path-free, versioned Akane package release contracts.",
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
    print("runtime_packages:", sum(1 for package in PACKAGES if package.runtime_dependency))
    print("petdesk_packages:", len(PETDESK_PACKAGES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
