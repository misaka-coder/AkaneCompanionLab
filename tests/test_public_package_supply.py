from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import package_supply as supply
from scripts import install_dependencies as installer


class PublicPackageSupplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pins = {"memcore-kernel": "0.1.7"}
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.name = "memcore_kernel-0.1.7-py3-none-any.whl"
        self.wheel = self.wheels / self.name
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("memcore_kernel-0.1.7.dist-info/METADATA",
                             "Metadata-Version: 2.1\nName: memcore-kernel\nVersion: 0.1.7\n")
        self.manifest = {"schema": supply.WHEELHOUSE_SCHEMA, "version": "0.1.0",
                         "runtime_dependencies_downloaded": False,
                         "internal_packages": [{"name": "memcore-kernel", "version": "0.1.7",
                                                "file": self.name, "sha256": supply.sha256(self.wheel)}]}
        self.write_manifest()

    def write_manifest(self) -> None:
        (self.wheels / supply.MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")

    def bundle(self, extra: dict[str, bytes] | None = None) -> Path:
        bundle = self.root / "bundle.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.write(self.wheel, self.name)
            archive.write(self.wheels / supply.MANIFEST, supply.MANIFEST)
            for name, data in (extra or {}).items():
                archive.writestr(name, data)
        return bundle

    def public_root(self) -> Path:
        (self.root / "deploy").mkdir(exist_ok=True)
        (self.root / "requirements-packages.txt").write_text("memcore-kernel==0.1.7\n", encoding="utf-8")
        bundle = self.bundle()
        lock = {"schema": supply.SCHEMA, "package_versions": self.pins,
                "internal_wheel_bundle": {
                    "url": "https://github.com/misaka-coder/AkaneCompanionLab/releases/download/deps-test/packages.zip",
                    "sha256": supply.sha256(bundle)}}
        (self.root / "deploy/public-package-supply.json").write_text(json.dumps(lock), encoding="utf-8")
        return self.root

    def test_accepts_exact_distribution_and_unchanged_archive(self) -> None:
        result = supply.extract_bundle(self.bundle(), self.root / "extracted", self.pins)
        self.assertEqual(result, self.manifest)

    def test_rejects_modified_wheel_before_pip(self) -> None:
        self.wheel.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
            supply.validate_wheelhouse(self.wheels, self.pins)

    def test_rejects_wrong_pin_and_legacy_memcore_distribution(self) -> None:
        for pins in [{"memcore-kernel": "0.1.0"}, {"memcore": "0.1.7"}]:
            with self.subTest(pins=pins), self.assertRaisesRegex(ValueError, "version_set_mismatch"):
                supply.validate_wheelhouse(self.wheels, pins)

    def test_offline_supply_requires_dependency_closure_and_matching_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "closure_missing"):
            supply.validate_wheelhouse(self.wheels, self.pins, offline_profile="backend")
        self.manifest.update(runtime_dependencies_downloaded=True, profile="backend")
        self.write_manifest()
        supply.validate_wheelhouse(self.wheels, self.pins, offline_profile="backend")
        with self.assertRaisesRegex(ValueError, "full_profile_required"):
            supply.validate_wheelhouse(self.wheels, self.pins, offline_profile="full")

    def test_rejects_traversal_and_unlisted_artifact(self) -> None:
        for extra in [{"../outside.whl": b"bad"}, {"/outside.whl": b"bad"},
                      {"another-1-py3-none-any.whl": b"bad"}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                supply.extract_bundle(self.bundle(extra), self.root / "extracted", self.pins)
        self.assertFalse((self.root / "outside.whl").exists())

    def test_rejects_duplicate_packages_and_unsafe_manifest_filename(self) -> None:
        self.manifest["internal_packages"].append(dict(self.manifest["internal_packages"][0]))
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "duplicate_package"):
            supply.validate_wheelhouse(self.wheels, self.pins)
        self.manifest["internal_packages"] = self.manifest["internal_packages"][:1]
        self.manifest["internal_packages"][0]["file"] = "../" + self.name
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "unsafe_artifact_name"):
            supply.validate_wheelhouse(self.wheels, self.pins)

    def test_corrupt_download_does_not_replace_existing_cache(self) -> None:
        destination = self.root / "cached.zip"
        destination.write_bytes(b"existing")
        with mock.patch.object(supply, "urlopen", return_value=io.BytesIO(b"corrupt")):
            with self.assertRaisesRegex(ValueError, "download_hash_mismatch"):
                supply._download("https://example.invalid/bundle", destination,
                                 hashlib.sha256(b"expected").hexdigest())
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertFalse(list(self.root.glob("*.partial")))

    def test_verified_cache_supports_distinct_concurrent_extractions(self) -> None:
        root = self.public_root()
        bundle = self.root / "bundle.zip"
        cache = self.root / "cache"
        cache.mkdir()
        (cache / (supply.sha256(bundle) + ".zip")).write_bytes(bundle.read_bytes())
        with mock.patch.object(supply, "urlopen", side_effect=AssertionError("network must remain unused")):
            with supply.public_wheelhouse(root, cache_dir=cache) as first:
                with supply.public_wheelhouse(root, cache_dir=cache) as second:
                    self.assertNotEqual(first, second)
                    self.assertTrue((first / self.name).exists())
                    self.assertTrue((second / self.name).exists())
                self.assertTrue((first / self.name).exists())

    def test_public_lock_rejects_changed_versions_and_credential_url(self) -> None:
        root = self.public_root()
        path = root / "deploy/public-package-supply.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        lock["package_versions"]["memcore-kernel"] = "0.1.0"
        path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "version_mismatch"):
            supply.load_public_lock(root)
        lock["package_versions"] = self.pins
        lock["internal_wheel_bundle"]["url"] = "https://secret:password@github.com/misaka-coder/AkaneCompanionLab/releases/download/test/packages.zip"
        path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "url_invalid"):
            supply.load_public_lock(root)

    def test_explicit_offline_install_has_no_network_fallback(self) -> None:
        self.public_root()
        self.manifest.update(runtime_dependencies_downloaded=True, profile="backend")
        self.write_manifest()
        with (mock.patch.object(installer, "ROOT", self.root),
              mock.patch.object(installer, "_pip") as pip,
              mock.patch.object(installer.subprocess, "run")):
            installer.install(profile="backend", wheelhouse=self.wheels)
        calls = [c.args for c in pip.call_args_list]
        self.assertIn("--no-index", calls[0])
        self.assertIn("--no-index", calls[1])
        self.assertEqual(calls[-1], ("check",))


if __name__ == "__main__":
    unittest.main()
