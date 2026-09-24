from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.capability_registry import INSPECT_ATTACHMENT_TOOL_SPEC, LOAD_MATERIAL_TOOL_SPEC
from companion_v01.generated_files import GeneratedFileService
from companion_v01.image_materials import SessionImageMaterialResolver
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import LoadMaterialToolHandler, ToolExecutionContext


PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-image-bytes"


class ImageMaterialTests(unittest.TestCase):
    def test_image_inspection_and_pixel_loading_tool_contracts_are_distinct(self) -> None:
        inspect_description = INSPECT_ATTACHMENT_TOOL_SPEC.description
        load_description = LOAD_MATERIAL_TOOL_SPEC.description

        self.assertIn("metadata and any existing summary", inspect_description)
        self.assertIn("pass the exact returned handle to load_material", inspect_description)
        self.assertIn("original pixels", load_description)
        self.assertIn("regardless of who sent them", load_description)

    def _services(self, root: Path):
        store = MemoryStore(root / "store")
        attachment_root = root / "inbox"
        output_root = root / "outputs"
        attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
        generated_service = GeneratedFileService(
            base_dir=output_root,
            store=store,
            attachment_service=attachment_service,
            work_dir=root / "work",
        )
        resolver = SessionImageMaterialResolver(
            attachment_service=attachment_service,
            generated_file_service=generated_service,
        )
        return store, attachment_service, generated_service, resolver

    def _add_attachment(self, root: Path, attachment_service: AttachmentInboxService) -> dict:
        image_path = root / "inbox" / "user" / "session" / "reference.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(PNG_BYTES)
        item = attachment_service.create_pending(
            profile_user_id="user",
            session_id="session",
            source="qq",
            kind="image",
            origin_name="reference.png",
            mime_type="image/png",
            storage_relpath="user/session/reference.png",
            timestamp=100,
        )
        return (
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=item["attachment_id"],
                summary_title="参考图",
                short_hint="合成测试图片",
                timestamp=101,
            )
            or item
        )

    def test_load_material_returns_internal_model_images_without_public_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, attachment_service, generated_service, resolver = self._services(root)
            attachment = self._add_attachment(root, attachment_service)
            handler = LoadMaterialToolHandler(image_material_resolver=resolver)
            context = ToolExecutionContext(profile_user_id="user", session_id="session", now_ts=200, visual_payload={})

            result = handler.execute(
                call={"type": "load_material", "targets": [attachment["attachment_handle"]]},
                context=context,
            )
            isolated = resolver.build_model_image_inputs(
                profile_user_id="other",
                session_id="session",
                targets=[attachment["attachment_handle"]],
            )

            self.assertEqual(result.tool_type, "load_material")
            self.assertEqual(result.stream_events[0]["handles"], ["img_001"])
            self.assertEqual(len(result.model_image_inputs), 1)
            self.assertTrue(result.model_image_inputs[0]["data_url"].startswith("data:image/png;base64,"))
            self.assertNotIn("base64", result.followup_context)
            self.assertNotIn(str(root), result.followup_context)
            self.assertNotIn("data_url", str(result.stream_events))
            self.assertFalse(isolated["ok"])
            self.assertIsNotNone(generated_service)

    def test_load_material_uses_pixel_signature_over_stale_transport_mime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, attachment_service, _generated_service, resolver = self._services(root)
            image_path = root / "inbox" / "user" / "session" / "stale.gif"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"\xff\xd8\xff\xe0synthetic-jpeg")
            attachment = attachment_service.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="stale.gif",
                mime_type="image/gif",
                storage_relpath="user/session/stale.gif",
                timestamp=100,
            )
            handler = LoadMaterialToolHandler(image_material_resolver=resolver)

            result = handler.execute(
                call={"type": "load_material", "targets": [attachment["attachment_handle"]]},
                context=ToolExecutionContext(
                    profile_user_id="user",
                    session_id="session",
                    now_ts=200,
                    visual_payload={},
                ),
            )

            self.assertEqual(result.model_image_inputs[0]["media_type"], "image/jpeg")
            self.assertTrue(result.model_image_inputs[0]["data_url"].startswith("data:image/jpeg;base64,"))

    def test_material_tool_publishes_precise_native_schema(self) -> None:
        handler = LoadMaterialToolHandler(image_material_resolver=object())
        specs = build_openai_native_tool_specs({"load_material": handler}, allowed_tool_names={"load_material"})
        schema = specs[0]["function"]["parameters"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["targets"]["maxItems"], 5)


if __name__ == "__main__":
    unittest.main()
