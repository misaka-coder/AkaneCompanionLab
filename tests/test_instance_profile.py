from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01.instance_profile import (
    InstanceProfileError,
    instance_context_from_request,
    resolve_instance_context,
)


VALID_MANIFEST = """\
schema_version = 1
instance_id = "akane-personal"
character_pack_id = "akane_v1"

[features]
care = true
"""


class InstanceProfileTests(unittest.TestCase):
    def _write_manifest(self, root: Path, text: str = VALID_MANIFEST) -> Path:
        path = root / "instances" / "akane-personal" / "instance.toml"
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_unselected_runtime_preserves_local_default_without_creating_instance_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = resolve_instance_context(data_root=root, selected_instance_id="")

            self.assertEqual(context.instance_id, "local-default")
            self.assertEqual(context.character_pack_id, "")
            self.assertTrue(context.features.care)
            self.assertTrue(context.is_compatibility_default)
            self.assertFalse((root / "instances").exists())

    def test_explicit_manifest_resolves_to_immutable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest(root)

            context = resolve_instance_context(data_root=root, selected_instance_id="akane-personal")

            self.assertEqual(context.instance_id, "akane-personal")
            self.assertEqual(context.character_pack_id, "akane_v1")
            self.assertTrue(context.features.care)
            self.assertEqual(context.source, "manifest")
            with self.assertRaises(dataclasses.FrozenInstanceError):
                context.features.care = False  # type: ignore[misc]

    def test_explicit_manifest_can_disable_care(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest(root, VALID_MANIFEST.replace("care = true", "care = false"))

            context = resolve_instance_context(data_root=root, selected_instance_id="akane-personal")

            self.assertFalse(context.features.care)
            self.assertFalse(context.is_compatibility_default)

    def test_explicit_missing_manifest_is_not_silently_downgraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(InstanceProfileError) as raised:
                resolve_instance_context(
                    data_root=Path(temp_dir),
                    selected_instance_id="missing-instance",
                )

        self.assertEqual(raised.exception.status, "unavailable")
        self.assertEqual(raised.exception.reason, "instance_manifest_not_found")
        self.assertNotIn(temp_dir, str(raised.exception))

    def test_selector_rejects_path_traversal_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(InstanceProfileError) as raised:
                resolve_instance_context(data_root=Path(temp_dir), selected_instance_id="../escape")

        self.assertEqual(raised.exception.reason, "invalid_safe_id")
        self.assertEqual(raised.exception.field, "AKANE_INSTANCE_ID")

    def test_manifest_id_must_match_selected_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest(root, VALID_MANIFEST.replace("akane-personal", "another-instance", 1))
            with self.assertRaises(InstanceProfileError) as raised:
                resolve_instance_context(data_root=root, selected_instance_id="akane-personal")

        self.assertEqual(raised.exception.reason, "selected_instance_id_mismatch")

    def test_future_plugin_field_is_rejected_in_m65_a(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest(root, VALID_MANIFEST + '\n[[plugins]]\nid = "akane.finance"\nenabled = true\n')
            with self.assertRaises(InstanceProfileError) as raised:
                resolve_instance_context(data_root=root, selected_instance_id="akane-personal")

        self.assertEqual(raised.exception.reason, "unsupported_manifest_field")
        self.assertEqual(raised.exception.field, "plugins")

    def test_unknown_feature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest(root, VALID_MANIFEST + "energy = true\n")
            with self.assertRaises(InstanceProfileError) as raised:
                resolve_instance_context(data_root=root, selected_instance_id="akane-personal")

        self.assertEqual(raised.exception.reason, "unknown_feature")
        self.assertEqual(raised.exception.field, "features.energy")

    def test_bound_context_is_available_through_real_request_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = resolve_instance_context(data_root=Path(temp_dir), selected_instance_id="")
            app = FastAPI()
            app.state.akane_instance_context = context

            @app.get("/instance-test")
            def instance_test(request: Request) -> dict[str, object]:
                return instance_context_from_request(request).as_dict()

            response = TestClient(app).get("/instance-test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["instance_id"], "local-default")
        self.assertNotIn(temp_dir, response.text)

    def test_unbound_request_context_fails_structurally(self) -> None:
        app = FastAPI()

        @app.get("/instance-test")
        def instance_test(request: Request) -> dict[str, object]:
            return instance_context_from_request(request).as_dict()

        with self.assertRaises(InstanceProfileError) as raised:
            TestClient(app, raise_server_exceptions=True).get("/instance-test")

        self.assertEqual(raised.exception.status, "unavailable")
        self.assertEqual(raised.exception.reason, "instance_context_not_bound")


if __name__ == "__main__":
    unittest.main()
