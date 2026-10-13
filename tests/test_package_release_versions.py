from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import build_extracted_package_wheelhouse as builder
from scripts import package_release_versions as versions


class PackageReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.memcore_spec = next(spec for spec in builder.PACKAGES if spec.name == "memcore-kernel")

    def test_every_build_package_has_one_exact_pin(self):
        for spec in builder.PACKAGES:
            self.assertTrue(versions.expected_package_version(spec.name))
        self.assertEqual(versions.expected_package_version("memcore-kernel"), "0.1.7")
        self.assertEqual(versions.expected_package_version("capcore"), "0.1.3")

    def test_runtime_download_resolves_internal_wheels_and_runtime_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(builder, "PACKAGES", ()), patch.object(builder, "_run") as run:
                builder.build_wheelhouse(source_root=root, output_dir=root / "wheels", download_runtime=True)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-r") + 1], str(builder.ROOT / "requirements.txt"))
            self.assertEqual(Path(command[command.index("--find-links") + 1]).resolve(), (root / "wheels").resolve())

    def test_backend_profile_downloads_only_its_runtime_closure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(builder, "PACKAGES", ()), patch.object(builder, "_run") as run:
                manifest = builder.build_wheelhouse(source_root=root, output_dir=root / "wheels",
                                                   download_runtime=True, profile="backend")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-r") + 1], str(builder.ROOT / "requirements-backend.txt"))
            self.assertEqual(manifest["profile"], "backend")
            self.assertTrue(manifest["runtime_dependencies_downloaded"])

    def test_source_manifest_uses_import_directory_and_pinned_distribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "memcore"
            package.mkdir()
            manifest = package / "pyproject.toml"
            spec = self.memcore_spec
            for version, accepted in [("0.1.1", False), ("0.1.6", False), ("0.1.7", True), ("0.1.7+unknown", False)]:
                with self.subTest(version=version):
                    manifest.write_text(f'[project]\nname = "memcore-kernel"\nversion = "{version}"\n', encoding="utf-8")
                    if accepted:
                        self.assertEqual(builder._assert_source_manifest(spec, root), package)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "project_version_mismatch"):
                            builder._assert_source_manifest(spec, root)

    def test_wheel_selection_and_metadata_use_the_same_exact_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = self.memcore_spec
            old = root / "memcore-0.1.0-py3-none-any.whl"
            old.touch()
            with self.assertRaisesRegex(RuntimeError, "expected_one_wheel"):
                builder._wheel_for(spec, root)
            current = root / "memcore_kernel-0.1.7-py3-none-any.whl"
            for name, metadata_version, accepted in [
                ("memcore", "0.1.7", False),
                ("memcore-kernel", "0.1.1", False),
                ("memcore-kernel", "0.1.6", False),
                ("memcore-kernel", "0.1.7", True),
            ]:
                with zipfile.ZipFile(current, "w") as archive:
                    archive.writestr("memcore_kernel.dist-info/METADATA", f"Name: {name}\nVersion: {metadata_version}\n")
                self.assertEqual(builder._wheel_for(spec, root), current)
                if not accepted:
                    with self.assertRaisesRegex(RuntimeError, "wheel_identity_mismatch"):
                        builder._assert_wheel_metadata(spec, current, root / "source")
                else:
                    builder._assert_wheel_metadata(spec, current, root / "source")

    def test_bad_duplicate_or_missing_pins_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            requirements = Path(temporary) / "requirements.txt"
            with patch.object(versions, "REQUIREMENTS", requirements):
                for content, reason in [
                    ("memcore-kernel>=0.1.0", "exact_pin_required"),
                    ("memcore-kernel==0.1.0\nmemcore-kernel==0.1.0", "duplicate_pin"),
                    ("capcore==0.1.0", "pin_missing"),
                ]:
                    requirements.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, reason):
                        versions.expected_package_version("memcore-kernel")

    def test_local_runtime_keeps_research_untouched_but_still_checks_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "memcore"
            research = package / "examples" / "research_pilot" / "run.py"
            research.parent.mkdir(parents=True)
            research.write_text("sys.path.insert(0, 'development-only')", encoding="utf-8")
            (package / "pyproject.toml").write_text(
                '[project]\nname="memcore-kernel"\nversion="0.1.7"\n', encoding="utf-8"
            )
            spec = self.memcore_spec
            with self.assertRaisesRegex(RuntimeError, "example_injects_source_path"):
                builder._assert_source_manifest(spec, root)
            builder._assert_source_manifest(spec, root, local_runtime=True)
            self.assertIn("development-only", research.read_text(encoding="utf-8"))
            runtime = package / "memcore" / "bad.py"
            runtime.parent.mkdir()
            runtime.write_text("ROOT = 'F:/Akane/private'", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source_contains_machine_path"):
                builder._assert_source_manifest(spec, root, local_runtime=True)

    def test_wheel_content_gate_rejects_research_and_machine_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = root / "test.whl"
            for member, content, reason in [
                ("examples/research_pilot/run.py", "pass", "research_artifact"),
                ("memcore/bad.py", "ROOT='F:/Akane/private'", "machine_path"),
            ]:
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr("memcore_kernel.dist-info/METADATA", "Name: memcore-kernel\nVersion: 0.1.7\n")
                    archive.writestr(member, content)
                with self.assertRaisesRegex(RuntimeError, reason):
                    builder._assert_wheel_metadata(self.memcore_spec, wheel, root / "source")


if __name__ == "__main__":
    unittest.main()
