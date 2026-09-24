from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.1.0"


@dataclass(frozen=True)
class PackageSpec:
    name: str
    depends_on_protocol: bool = False


PACKAGES: tuple[PackageSpec, ...] = (
    PackageSpec("petcore-protocol"),
    PackageSpec("petdesk-character-host", depends_on_protocol=True),
    PackageSpec("petdesk-live2d-pixi-driver", depends_on_protocol=True),
    PackageSpec("petdesk-runtime", depends_on_protocol=True),
)


def _run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command))
    executable = shutil.which(command[0])
    if executable is None:
        raise RuntimeError(f"executable_not_found:{command[0]}")
    subprocess.run([executable, *command[1:]], cwd=cwd, check=True)


def _load_package_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("package_json_not_object")
    return payload


def _assert_manifest(spec: PackageSpec, package_root: Path) -> None:
    package_json = package_root / "package.json"
    if not package_json.is_file():
        raise RuntimeError(f"{spec.name}:missing_package_json")
    payload = _load_package_json(package_json)
    if payload.get("name") != spec.name:
        raise RuntimeError(f"{spec.name}:name_mismatch")
    if payload.get("version") != EXPECTED_VERSION:
        raise RuntimeError(f"{spec.name}:version_mismatch")
    normalized = package_json.read_text(encoding="utf-8").replace("\\", "/").lower()
    if "file:../" in normalized or "link:../" in normalized:
        raise RuntimeError(f"{spec.name}:source_path_dependency_forbidden")
    if spec.depends_on_protocol:
        dependencies = payload.get("dependencies")
        protocol_version = dependencies.get("petcore-protocol") if isinstance(dependencies, dict) else None
        if protocol_version != EXPECTED_VERSION:
            raise RuntimeError(f"{spec.name}:petcore_protocol_version_mismatch:{protocol_version}")


def _artifact_for(spec: PackageSpec, output_dir: Path) -> Path:
    matches = sorted(output_dir.glob(f"{spec.name}-{EXPECTED_VERSION}.tgz"))
    if len(matches) != 1:
        raise RuntimeError(f"{spec.name}:expected_one_artifact:found_{len(matches)}")
    return matches[0]


def _assert_artifact(spec: PackageSpec, artifact: Path) -> None:
    with tarfile.open(artifact, mode="r:gz") as archive:
        member = archive.getmember("package/package.json")
        handle = archive.extractfile(member)
        if handle is None:
            raise RuntimeError(f"{spec.name}:package_json_missing_from_artifact")
        raw = handle.read().decode("utf-8")
        for packed_member in archive.getmembers():
            if not packed_member.isfile() or packed_member.size > 5 * 1024 * 1024:
                continue
            packed_handle = archive.extractfile(packed_member)
            if packed_handle is None:
                continue
            packed_text = packed_handle.read().decode("utf-8", errors="ignore").lower().replace("\\\\", "\\")
            if any(root in packed_text for root in ("f:/akane", "f:\\akane", "f:/cache", "f:\\cache")):
                raise RuntimeError(f"{spec.name}:artifact_contains_machine_path:{packed_member.name}")
    normalized = raw.replace("\\", "/").lower()
    if "file:../" in normalized or "link:../" in normalized:
        raise RuntimeError(f"{spec.name}:artifact_contains_source_path_dependency")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(*, source_root: Path, output_dir: Path) -> list[dict[str, str]]:
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, str]] = []

    for spec in PACKAGES:
        package_root = source_root / spec.name
        _assert_manifest(spec, package_root)
        for stale in output_dir.glob(f"{spec.name}-*.tgz"):
            stale.unlink()
        _run(["pnpm", "pack", "--pack-destination", str(output_dir)], cwd=package_root)
        artifact = _artifact_for(spec, output_dir)
        _assert_artifact(spec, artifact)
        artifacts.append(
            {"name": spec.name, "version": EXPECTED_VERSION, "file": artifact.name, "sha256": _sha256(artifact)}
        )

    manifest = {
        "schema": "akane.petdesk-package-artifacts.v1",
        "version": EXPECTED_VERSION,
        "packages": artifacts,
    }
    (output_dir / "akane-petdesk-package-artifacts.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifacts


def verify_install(*, output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    artifact_by_name = {spec.name: _artifact_for(spec, output_dir) for spec in PACKAGES}
    protocol_uri = artifact_by_name["petcore-protocol"].as_uri()
    with tempfile.TemporaryDirectory(prefix="akane-petdesk-package-verify-") as temp_dir:
        root = Path(temp_dir)
        dependencies = {name: artifact.as_uri() for name, artifact in artifact_by_name.items()}
        package_json = {
            "name": "akane-petdesk-artifact-smoke",
            "private": True,
            "type": "module",
            "dependencies": dependencies,
            "pnpm": {"overrides": {"petcore-protocol": protocol_uri}},
        }
        (root / "package.json").write_text(
            json.dumps(package_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _run(["pnpm", "install", "--ignore-scripts", "--no-frozen-lockfile"], cwd=root)
        _run(
            [
                "node",
                "--input-type=module",
                "-e",
                "await import('petcore-protocol'); await import('petdesk-character-host'); "
                "await import('petdesk-live2d-pixi-driver'); console.log('PETDESK_ARTIFACT_IMPORT_OK')",
            ],
            cwd=root,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and source-blind verify versioned petdesk npm artifacts.")
    parser.add_argument("--source-root", type=Path, default=ROOT.parent)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "package_artifacts" / "petdesk")
    parser.add_argument("--skip-install-verify", action="store_true")
    args = parser.parse_args()
    try:
        artifacts = build(source_root=args.source_root, output_dir=args.output_dir)
        if not args.skip_install_verify:
            verify_install(output_dir=args.output_dir)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"AKANE_PETDESK_PACKAGE_ARTIFACTS_FAILED:{exc}")
        return 1
    print("AKANE_PETDESK_PACKAGE_ARTIFACTS_OK")
    print(f"packages: {len(artifacts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
