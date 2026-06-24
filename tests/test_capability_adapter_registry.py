from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.capability_adapters.registry import CapabilityAdapterRegistry


VALID_MANIFEST = """\
schema: capability_adapter/v1
provider:
  id: {provider_id}
  type: comfyui
  display_name: {display_name}
  endpoint:
    url: http://127.0.0.1:8188
    loopback_only: true
capabilities: []
"""

INVALID_MANIFEST = """\
schema: capability_adapter/v1
provider:
  type: comfyui
capabilities: []
"""


class CapabilityAdapterRegistryTests(unittest.TestCase):
    def test_empty_directories_scan_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin = root / "builtin"
            profile = root / "profile"
            builtin.mkdir()
            profile.mkdir()
            registry = CapabilityAdapterRegistry(
                builtin_dir=builtin,
                profile_dir_provider=lambda: profile,
            )
            registry.scan()
            self.assertEqual(registry.list_manifests(), ())
            self.assertEqual(registry.list_invalid(), ())

    def test_builtin_loads_when_profile_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin = root / "builtin"
            builtin.mkdir()
            (builtin / "comfyui.yaml").write_text(
                VALID_MANIFEST.format(provider_id="comfyui", display_name="Builtin ComfyUI"),
                encoding="utf-8",
            )
            registry = CapabilityAdapterRegistry(
                builtin_dir=builtin,
                profile_dir_provider=lambda: root / "missing_profile",
            )
            registry.scan()
            manifests = registry.list_manifests()
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0].provider_id, "comfyui")
            self.assertEqual(manifests[0].source_layer, "builtin")

    def test_profile_manifest_overrides_builtin_same_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin = root / "builtin"
            profile = root / "profile"
            builtin.mkdir()
            profile.mkdir()
            (builtin / "comfyui.yaml").write_text(
                VALID_MANIFEST.format(provider_id="comfyui", display_name="Builtin ComfyUI"),
                encoding="utf-8",
            )
            (profile / "comfyui.yaml").write_text(
                VALID_MANIFEST.format(provider_id="comfyui", display_name="Profile ComfyUI"),
                encoding="utf-8",
            )
            registry = CapabilityAdapterRegistry(
                builtin_dir=builtin,
                profile_dir_provider=lambda: profile,
            )
            registry.scan()
            manifests = registry.list_manifests()
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0].display_name, "Profile ComfyUI")
            self.assertEqual(manifests[0].source_layer, "profile")

    def test_invalid_manifest_does_not_block_valid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin = root / "builtin"
            builtin.mkdir()
            (builtin / "valid.yaml").write_text(
                VALID_MANIFEST.format(provider_id="comfyui", display_name="Builtin ComfyUI"),
                encoding="utf-8",
            )
            (builtin / "invalid.yaml").write_text(INVALID_MANIFEST, encoding="utf-8")
            registry = CapabilityAdapterRegistry(
                builtin_dir=builtin,
                profile_dir_provider=lambda: root / "missing_profile",
            )
            registry.scan()
            self.assertEqual(len(registry.list_manifests()), 1)
            self.assertEqual(len(registry.list_invalid()), 1)
            self.assertEqual(registry.list_invalid()[0].reason, "missing_provider_id")

    def test_get_missing_provider_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = CapabilityAdapterRegistry(
                builtin_dir=root / "builtin",
                profile_dir_provider=lambda: root / "profile",
            )
            registry.scan()
            self.assertIsNone(registry.get("missing"))


if __name__ == "__main__":
    unittest.main()
