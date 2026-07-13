from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import sys
from dataclasses import dataclass
from typing import Any


EXPECTED_VERSION = "0.1.0"


@dataclass(frozen=True)
class PackageSpec:
    distribution: str
    import_name: str


PACKAGES: tuple[PackageSpec, ...] = (
    PackageSpec("capcore", "capcore"),
    PackageSpec("capcore-adapter-mcp", "capcore_adapter_mcp"),
    PackageSpec("capcore-adapter-python", "capcore_adapter_python"),
    PackageSpec("capcore-adapter-speech", "capcore_adapter_speech"),
    PackageSpec("capcore-adapter-comfyui", "capcore_adapter_comfyui"),
    PackageSpec("charpack-core", "charpack_core"),
    PackageSpec("promptpack-core", "promptpack_core"),
    PackageSpec("capcore-provider-native-tools", "capcore_provider_native_tools"),
    PackageSpec("capcore-provider-openai", "capcore_provider_openai"),
    PackageSpec("capcore-provider-anthropic", "capcore_provider_anthropic"),
    PackageSpec("memcore", "memcore"),
)


def _direct_url_payload(dist: importlib.metadata.Distribution) -> dict[str, Any]:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"invalid": True}
    return payload if isinstance(payload, dict) else {"invalid": True}


def audit_installed_packages() -> tuple[list[dict[str, str]], list[str]]:
    installed: list[dict[str, str]] = []
    errors: list[str] = []

    for spec in PACKAGES:
        try:
            dist = importlib.metadata.distribution(spec.distribution)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"{spec.distribution}:not_installed")
            continue

        version = str(dist.version or "")
        if version != EXPECTED_VERSION:
            errors.append(f"{spec.distribution}:version_mismatch:{version}")

        direct_url = _direct_url_payload(dist)
        dir_info = direct_url.get("dir_info")
        if isinstance(dir_info, dict):
            if bool(dir_info.get("editable")):
                errors.append(f"{spec.distribution}:editable_install_forbidden")
            else:
                errors.append(f"{spec.distribution}:source_directory_install_forbidden")
        if direct_url.get("invalid"):
            errors.append(f"{spec.distribution}:invalid_direct_url_metadata")

        try:
            module = importlib.import_module(spec.import_name)
        except Exception as exc:  # pragma: no cover - exercised by bootstrap failures
            errors.append(f"{spec.distribution}:import_failed:{exc.__class__.__name__}")
            continue

        installed.append(
            {
                "distribution": spec.distribution,
                "version": version,
                "module": spec.import_name,
            }
        )

    return installed, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject missing, wrong-version, editable, or source-directory Akane package installs.",
    )
    parser.add_argument("--json", action="store_true", help="Emit one JSON result instead of human-readable lines.")
    args = parser.parse_args()

    installed, errors = audit_installed_packages()
    payload = {
        "ok": not errors,
        "expected_version": EXPECTED_VERSION,
        "package_count": len(PACKAGES),
        "installed": installed,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif errors:
        print("AKANE_PACKAGED_DEPENDENCIES_FAILED")
        for error in errors:
            print(f"- {error}")
    else:
        print("AKANE_PACKAGED_DEPENDENCIES_OK")
        print(f"packages: {len(installed)}")
        print(f"version: {EXPECTED_VERSION}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
