from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import check_packaged_dependencies


ROOT = Path(__file__).resolve().parents[1]
AKANE_PARENT = ROOT.parent
VERSION = "0.1.0"

PYTHON_PACKAGES = (
    "memcore",
    "promptpack-core",
    "charpack-core",
    "channelcore-onebot",
    "capcore",
    "capcore-adapter-python",
    "capcore-adapter-mcp",
    "capcore-adapter-speech",
    "capcore-adapter-comfyui",
    "capcore-provider-native-tools",
    "capcore-provider-openai",
    "capcore-provider-anthropic",
    "capcore-host-utils",
)

RUNTIME_PACKAGES = tuple(name for name in PYTHON_PACKAGES if name != "capcore-host-utils")

PETDESK_PACKAGES = (
    "petcore-protocol",
    "petdesk-character-host",
    "petdesk-live2d-pixi-driver",
    "petdesk-runtime",
)


class PackageIndependenceContractTests(unittest.TestCase):
    def test_packaged_dependency_audit_rejects_missing_runtime_contract(self) -> None:
        spec = check_packaged_dependencies.PackageSpec(
            "memcore",
            "memcore",
            ("memory_metadata_has_signal",),
        )
        artifact = SimpleNamespace(version=VERSION, ok=True, reason="")
        with (
            mock.patch.object(check_packaged_dependencies, "PACKAGES", (spec,)),
            mock.patch.object(
                check_packaged_dependencies.importlib.metadata,
                "distribution",
                return_value=object(),
            ),
            mock.patch.object(
                check_packaged_dependencies,
                "audit_distribution_artifact",
                return_value=artifact,
            ),
            mock.patch.object(
                check_packaged_dependencies.importlib,
                "import_module",
                return_value=SimpleNamespace(),
            ),
        ):
            installed, errors = check_packaged_dependencies.audit_installed_packages()

        self.assertEqual([entry["distribution"] for entry in installed], ["memcore"])
        self.assertEqual(errors, ["memcore:runtime_contract_missing:memory_metadata_has_signal"])

    def test_python_package_manifests_have_no_sibling_source_overrides(self) -> None:
        for name in PYTHON_PACKAGES:
            root = AKANE_PARENT / name
            pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
            normalized = pyproject.replace("\\", "/").lower()
            self.assertNotIn("[tool.uv.sources]", normalized, name)
            self.assertNotIn('path = "../', normalized, name)
            self.assertNotIn("editable = true", normalized, name)
            lock = root / "uv.lock"
            if lock.is_file():
                normalized_lock = lock.read_text(encoding="utf-8").replace("\\", "/").lower()
                self.assertNotIn('editable = "../', normalized_lock, name)
                self.assertNotIn('directory = "../', normalized_lock, name)

    def test_capcore_extensions_depend_on_bounded_release_versions(self) -> None:
        extensions = tuple(name for name in PYTHON_PACKAGES if name.startswith("capcore-") and name != "capcore")
        for name in extensions:
            with (AKANE_PARENT / name / "pyproject.toml").open("rb") as handle:
                project = tomllib.load(handle)["project"]
            dependencies = tuple(str(item) for item in project.get("dependencies", ()))
            self.assertTrue(any(item == "capcore>=0.1.0,<0.2.0" for item in dependencies), name)

    def test_akane_uses_exact_package_release_set(self) -> None:
        aggregate = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        package_requirements = (ROOT / "requirements-packages.txt").read_text(encoding="utf-8")
        normalized = f"{aggregate}\n{package_requirements}".replace("\\", "/").lower()
        self.assertIn("-r requirements-packages.txt", aggregate)
        self.assertNotIn("-e ../", normalized)
        for name in RUNTIME_PACKAGES:
            self.assertIn(f"{name}=={VERSION}", package_requirements)

        bootstrap = (ROOT / "scripts" / "bootstrap_akane_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("--force-reinstall --no-deps", bootstrap)
        self.assertIn("-r $packageRequirementsPath", bootstrap)

    def test_smokes_do_not_inject_sibling_source_directories(self) -> None:
        for name in ("smoke_extracted_package_ecosystem.py", "smoke_ai_product_host_turn.py"):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("_add_sibling_sources", source)
            self.assertNotIn("sys.path.insert", source)

        manager_source = (ROOT / "companion_v01" / "memcore_integration" / "manager.py").read_text(encoding="utf-8")
        self.assertNotIn('parent / "memcore"', manager_source)
        self.assertNotIn("sys.path.insert", manager_source)

        capcore_smoke = (AKANE_PARENT / "capcore" / "examples" / "ecosystem_native_tool_smoke.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("sys.path.insert", capcore_smoke)
        self.assertNotIn("AKANE_ROOT", capcore_smoke)

    def test_petdesk_dependencies_are_versioned_not_file_linked(self) -> None:
        for name in PETDESK_PACKAGES:
            package_json_path = AKANE_PARENT / name / "package.json"
            payload = json.loads(package_json_path.read_text(encoding="utf-8"))
            normalized = package_json_path.read_text(encoding="utf-8").replace("\\", "/").lower()
            self.assertNotIn("file:../", normalized, name)
            if name != "petcore-protocol":
                self.assertEqual(payload["dependencies"]["petcore-protocol"], VERSION, name)
            lock = AKANE_PARENT / name / "pnpm-lock.yaml"
            if lock.is_file():
                normalized_lock = lock.read_text(encoding="utf-8").replace("\\", "/").lower()
                self.assertNotIn("file:../", normalized_lock, name)

    def test_petdesk_runtime_starter_requires_explicit_character_host(self) -> None:
        source = (AKANE_PARENT / "petdesk-runtime" / "examples" / "runtime-bridge-process.ts").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("../../petdesk-character-host", source.replace("\\", "/"))
        self.assertIn("PETDESK_CHARACTER_HOST_DIR", source)
        self.assertIn("character_host_dir_required", source)

    def test_akane_petdesk_scripts_require_explicit_runtime_root(self) -> None:
        scripts = (
            "build_petdesk_runtime_release.ps1",
            "check_petdesk_release.ps1",
            "export_petdesk_release_bundle.ps1",
            "start_petdesk_runtime.ps1",
            "stop_petdesk_runtime.ps1",
        )
        for name in scripts:
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("../petdesk-runtime", source.replace("\\", "/"), name)
            self.assertIn("PETDESK_RUNTIME_ROOT", source, name)
            self.assertIn("petdesk_runtime_dir_required", source, name)

    def test_release_sources_do_not_pin_workspace_machine_paths(self) -> None:
        roots = tuple(AKANE_PARENT / name for name in (*PYTHON_PACKAGES, *PETDESK_PACKAGES))
        suffixes = {".md", ".py", ".ts", ".js", ".toml", ".json", ".yaml", ".yml", ".txt"}
        ignored = {
            ".git",
            ".venv",
            ".claude",
            ".agents",
            ".vscode",
            "node_modules",
            "build",
            "dist",
            "target",
            "__pycache__",
        }
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                relative = path.relative_to(root)
                if any(part in ignored for part in relative.parts):
                    continue
                source = path.read_text(encoding="utf-8", errors="ignore").lower().replace("\\\\", "\\")
                for forbidden in ("f:/akane", "f:\\akane", "f:/cache", "f:\\cache"):
                    self.assertNotIn(forbidden, source, f"{root.name}/{relative}")


if __name__ == "__main__":
    unittest.main()
