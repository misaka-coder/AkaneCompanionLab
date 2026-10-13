from __future__ import annotations

import json
import importlib.metadata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import check_packaged_dependencies
from scripts.package_supply import exact_pins


ROOT = Path(__file__).resolve().parents[1]
AKANE_PARENT = ROOT.parent
VERSION = "0.1.0"

PYTHON_PACKAGES = (
    "memcore",
    "voicecore",
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

RUNTIME_PACKAGES = tuple(spec.distribution for spec in check_packaged_dependencies.PACKAGES)

PETDESK_PACKAGES = (
    "petcore-protocol",
    "petdesk-character-host",
    "petdesk-live2d-pixi-driver",
    "petdesk-runtime",
)


class PackageIndependenceContractTests(unittest.TestCase):
    def test_packaged_dependency_audit_rejects_missing_runtime_contract(self) -> None:
        spec = check_packaged_dependencies.PackageSpec(
            "memcore-kernel",
            "memcore",
            ("memory_metadata_has_signal",),
        )
        artifact = SimpleNamespace(version=check_packaged_dependencies.expected_package_version("memcore-kernel"), ok=True, reason="")
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

        self.assertEqual([entry["distribution"] for entry in installed], ["memcore-kernel"])
        self.assertEqual(errors, ["memcore-kernel:runtime_contract_missing:memory_metadata_has_signal"])

    def test_packaged_dependency_audit_rejects_missing_nested_runtime_contract(self) -> None:
        spec = check_packaged_dependencies.PackageSpec(
            "memcore-kernel",
            "memcore",
            ("MemorySystem.build_context_surface",),
        )
        artifact = SimpleNamespace(version=check_packaged_dependencies.expected_package_version("memcore-kernel"), ok=True, reason="")
        legacy_memory_system = type("LegacyMemorySystem", (), {})
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
                return_value=SimpleNamespace(MemorySystem=legacy_memory_system),
            ),
        ):
            installed, errors = check_packaged_dependencies.audit_installed_packages()

        self.assertEqual([entry["distribution"] for entry in installed], ["memcore-kernel"])
        self.assertEqual(errors, ["memcore-kernel:runtime_contract_missing:MemorySystem.build_context_surface"])

    def test_packaged_speech_audit_rejects_pre_call_scoped_adapter(self) -> None:
        spec = check_packaged_dependencies.PackageSpec(
            "capcore-adapter-speech",
            "capcore_adapter_speech",
            ("NormalizedASRSession",),
        )
        artifact = SimpleNamespace(version=VERSION, ok=True, reason="")
        legacy_session = type("LegacyNormalizedASRSession", (), {})
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
                return_value=SimpleNamespace(NormalizedASRSession=legacy_session),
            ),
        ):
            installed, errors = check_packaged_dependencies.audit_installed_packages()

        self.assertEqual(
            [entry["distribution"] for entry in installed],
            ["capcore-adapter-speech"],
        )
        self.assertEqual(
            errors,
            [
                "capcore-adapter-speech:runtime_contract_missing:"
                "NormalizedASRSession.supports_turn_commit,"
                "NormalizedASRSession.commit_turn,"
                "NormalizedASRSession.finish_call"
            ],
        )

    def test_python_package_manifests_have_no_sibling_source_overrides(self) -> None:
        # Consumer CI must audit installed artifacts, not require the author's
        # private sibling repositories. Source audits remain maintainer gates.
        installed, errors = check_packaged_dependencies.audit_installed_packages()
        self.assertEqual(errors, [])
        self.assertEqual({p["distribution"] for p in installed}, set(RUNTIME_PACKAGES))

    def test_capcore_extensions_depend_on_bounded_release_versions(self) -> None:
        extensions = tuple(name for name in RUNTIME_PACKAGES if name.startswith("capcore-"))
        for name in extensions:
            declared = importlib.metadata.distribution(name).requires or []
            capcore = next(item.replace(" ", "") for item in declared
                           if item.replace(" ", "").startswith("capcore<")
                           or item.replace(" ", "").startswith("capcore>"))
            self.assertIn(">=0.1.0", capcore, name)
            self.assertIn("<0.2.0", capcore, name)

    def test_akane_uses_exact_package_release_set(self) -> None:
        aggregate = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        package_requirements = (ROOT / "requirements-packages.txt").read_text(encoding="utf-8")
        normalized = f"{aggregate}\n{package_requirements}".replace("\\", "/").lower()
        self.assertIn("-r requirements-packages.txt", aggregate)
        self.assertNotIn("-e ../", normalized)
        pins = exact_pins(ROOT / "requirements-packages.txt")
        self.assertEqual(set(pins), set(RUNTIME_PACKAGES))
        for name in RUNTIME_PACKAGES:
            self.assertIn(f"{name}=={check_packaged_dependencies.expected_package_version(name)}", package_requirements)

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


    def test_petdesk_dependencies_are_versioned_not_file_linked(self) -> None:
        for name in ("desktop_pet_next", "desktop_pet_creator_kit"):
            package_json_path = ROOT / name / "package.json"
            payload = json.loads(package_json_path.read_text(encoding="utf-8"))
            normalized = package_json_path.read_text(encoding="utf-8").replace("\\", "/").lower()
            self.assertNotIn("file:../", normalized, name)
            for dependency in (payload.get("dependencies") or {}).values():
                self.assertFalse(str(dependency).startswith(("file:", "link:")), name)
            lock = ROOT / name / "package-lock.json"
            if lock.is_file():
                normalized_lock = lock.read_text(encoding="utf-8").replace("\\", "/").lower()
                self.assertNotIn("file:../", normalized_lock, name)

    def test_petdesk_runtime_starter_requires_explicit_character_host(self) -> None:
        source = (ROOT / "scripts" / "build_petdesk_package_artifacts.py").read_text(encoding="utf-8")
        self.assertIn('"--source-root"', source)
        self.assertNotIn("../../petdesk-character-host", source.replace("\\", "/"))

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
        suffixes = {".md", ".py", ".ts", ".js", ".toml", ".json", ".yaml", ".yml", ".txt"}
        for spec in check_packaged_dependencies.PACKAGES:
            distribution = importlib.metadata.distribution(spec.distribution)
            for relative in distribution.files or ():
                if relative.parts[0] != spec.import_name or Path(relative).suffix.lower() not in suffixes:
                    continue
                path = Path(distribution.locate_file(relative))
                source = path.read_text(encoding="utf-8", errors="ignore").lower().replace("\\\\", "\\")
                for forbidden in ("f:/akane", "f:\\akane", "f:/cache", "f:\\cache"):
                    self.assertNotIn(forbidden, source, f"{spec.distribution}/{relative}")


if __name__ == "__main__":
    unittest.main()
