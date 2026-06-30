from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.capability_adapters.manifest_loader import load_manifest
from companion_v01.capability_adapters.types import CapabilityManifest, InvalidManifest


FIXTURES = Path(__file__).resolve().parent.parent / "docs" / "fixtures" / "capability_adapter_m1"


class CapabilityAdapterManifestLoaderTests(unittest.TestCase):
    def load(self, name: str) -> CapabilityManifest | InvalidManifest:
        return load_manifest(FIXTURES / name, source_layer="builtin")

    def load_text(self, text: str) -> CapabilityManifest | InvalidManifest:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.yaml"
            path.write_text(text, encoding="utf-8")
            return load_manifest(path, source_layer="builtin")

    def test_valid_comfyui_manifest_loads_fields(self) -> None:
        manifest = self.load("valid_comfyui.yaml")
        self.assertIsInstance(manifest, CapabilityManifest)
        assert isinstance(manifest, CapabilityManifest)
        self.assertEqual(manifest.provider_id, "comfyui")
        self.assertEqual(manifest.provider_type, "comfyui")
        self.assertEqual(manifest.display_name, "ComfyUI")
        self.assertIsNotNone(manifest.endpoint)
        self.assertEqual(manifest.endpoint.url, "http://127.0.0.1:8188")
        self.assertTrue(manifest.endpoint.loopback_only)
        self.assertIsNotNone(manifest.health)
        self.assertEqual(manifest.health.path, "/system_stats")
        self.assertEqual(manifest.tiers[0].id, "cpu")
        self.assertEqual(manifest.secrets, ("akane_comfyui_key",))
        self.assertEqual(manifest.capabilities[0].id, "portrait_cutout")
        self.assertEqual(manifest.capabilities[0].visible_in, ("desktop", "web"))
        self.assertTrue(manifest.capabilities[0].prompt_exposed)
        self.assertEqual(manifest.capabilities[0].inputs[0].max_bytes, 8388608)
        self.assertEqual(manifest.capabilities[0].outputs[0].delivery, "generated_file")

    def test_missing_provider_id_returns_invalid(self) -> None:
        manifest = self.load("invalid_missing_id.yaml")
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "missing_provider_id")

    def test_bad_type_returns_invalid(self) -> None:
        manifest = self.load("invalid_bad_type.yaml")
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "provider_type_not_allowed")

    def test_non_loopback_endpoint_returns_invalid(self) -> None:
        manifest = self.load("invalid_non_loopback.yaml")
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "endpoint_not_loopback")

    def test_plaintext_secret_returns_invalid(self) -> None:
        manifest = self.load("invalid_plaintext_secret.yaml")
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "secrets_must_be_key_names")

    def test_endpoint_url_query_does_not_trigger_secret_key_name_validation(self) -> None:
        manifest = self.load_text(
            """\
schema: capability_adapter/v1
provider:
  id: local_http
  type: python_plugin
  endpoint:
    url: http://127.0.0.1:8080/callback?code=123&redirect_uri=http%3A%2F%2Ftest.local
    loopback_only: true
capabilities: []
"""
        )
        self.assertIsInstance(manifest, CapabilityManifest)

    def test_command_exec_effect_forces_high_risk(self) -> None:
        manifest = self.load("effects_high.yaml")
        self.assertIsInstance(manifest, CapabilityManifest)
        assert isinstance(manifest, CapabilityManifest)
        capability = manifest.capabilities[0]
        self.assertEqual(capability.risk, "high")
        self.assertEqual(capability.confirm, "always")

    def test_file_write_effect_promotes_low_to_medium(self) -> None:
        manifest = self.load("effects_medium.yaml")
        self.assertIsInstance(manifest, CapabilityManifest)
        assert isinstance(manifest, CapabilityManifest)
        capability = manifest.capabilities[0]
        self.assertEqual(capability.risk, "medium")
        self.assertEqual(capability.confirm, "first_time")

    def test_prompt_exposed_defaults_false(self) -> None:
        manifest = self.load("defaults.yaml")
        self.assertIsInstance(manifest, CapabilityManifest)
        assert isinstance(manifest, CapabilityManifest)
        self.assertFalse(manifest.capabilities[0].prompt_exposed)

    def test_missing_risk_defaults_medium_first_time(self) -> None:
        manifest = self.load("defaults.yaml")
        self.assertIsInstance(manifest, CapabilityManifest)
        assert isinstance(manifest, CapabilityManifest)
        capability = manifest.capabilities[0]
        self.assertEqual(capability.risk, "medium")
        self.assertEqual(capability.confirm, "first_time")

    def test_yaml_parse_error_returns_invalid(self) -> None:
        manifest = self.load("invalid_yaml.yaml")
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "yaml_parse_error")

    def test_invalid_loopback_only_bool_returns_invalid(self) -> None:
        manifest = self.load_text(
            """\
schema: capability_adapter/v1
provider:
  id: remote_comfyui
  type: comfyui
  endpoint:
    url: http://example.com:8188
    loopback_only: ture
capabilities: []
"""
        )
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "endpoint_loopback_only_invalid")

    def test_duplicate_capability_id_returns_invalid(self) -> None:
        manifest = self.load_text(
            """\
schema: capability_adapter/v1
provider:
  id: demo
  type: comfyui
capabilities:
  - id: echo
  - id: echo
"""
        )
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "capability_id_not_unique")

    def test_invalid_input_slot_max_bytes_returns_invalid(self) -> None:
        manifest = self.load_text(
            """\
schema: capability_adapter/v1
provider:
  id: demo
  type: comfyui
capabilities:
  - id: echo
    inputs:
      - name: image
        kind: image_bytes
        max_bytes: nope
"""
        )
        self.assertIsInstance(manifest, InvalidManifest)
        self.assertEqual(manifest.reason, "input_slot_max_bytes_invalid")


if __name__ == "__main__":
    unittest.main()
