from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

from companion_v01.desktop_satellite_specs import DESKTOP_SCREENSHOT_TOOL_SPEC
from companion_v01.executor_broker import BrokerExecutionResult
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import execute_tool_invocation
from companion_v01.tool_runtime import DesktopSatelliteToolHandler
from tests.test_plugin_resources import services


def picture():
    output = io.BytesIO()
    Image.new("RGB", (80, 50), "blue").save(output, format="PNG")
    return {"ok": True, "width": 80, "height": 50, "capturedAt": 123,
            "mimeType": "image/png", "imageBase64": base64.b64encode(output.getvalue()).decode()}


class DesktopDeviceRepairTests(unittest.TestCase):
    def invoke(self, files, payload, *, request_context=None):
        broker = SimpleNamespace(execute=lambda **kw: BrokerExecutionResult(status="succeeded", data=payload))
        engine = SimpleNamespace(tool_handlers={"desktop_screenshot": DesktopSatelliteToolHandler(tool_id="desktop_screenshot")},
            executor_broker=broker, _get_generated_file_service=lambda: files,
            _get_image_material_resolver=lambda: SimpleNamespace(build_model_image_inputs=lambda **kw: {"images": [{"test_image": True}]}))
        return execute_tool_invocation(engine, invocation=ToolInvocation(name="desktop_screenshot", arguments={},
            execution_receipt={"present": True}), profile_user_id="owner", session_id="session",
            visual_payload={}, now_ts=1000, request_context=request_context)

    def test_real_artifact_is_session_scoped_and_bytes_do_not_enter_feedback(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, files = services(Path(temporary))
            payload = picture()
            result, envelope = self.invoke(files, payload)
            self.assertEqual(envelope.status, "ok")
            handle = envelope.data["result"]["generated_handle"]
            stored = files.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=handle)
            self.assertEqual(Path(stored["absolute_path"]).read_bytes(), base64.b64decode(payload["imageBase64"]))
            self.assertIsNone(files.resolve_generated_artifact(profile_user_id="owner", session_id="other", target=handle))
            self.assertEqual(result.model_image_inputs, [{"test_image": True}])
            self.assertTrue(any(e["type"] == "generated_file_ready" for e in result.stream_events))
            public = json.dumps(envelope.data) + result.followup_context + json.dumps(result.stream_events)
            self.assertNotIn("imageBase64", public)
            self.assertNotIn(payload["imageBase64"], public)
            self.assertNotIn(temporary, public)

    def test_invalid_image_fails_without_success_event_or_image_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, files = services(Path(temporary))
            for payload in (dict(picture(), imageBase64="bad!"), dict(picture(), width=90), {}):
                result, envelope = self.invoke(files, payload)
                self.assertEqual(envelope.status, "error")
                self.assertEqual(envelope.data["code"], "screenshot_registration_failed")
                self.assertFalse(result.model_image_inputs)
                self.assertFalse(any(e["type"] == "generated_file_ready" for e in result.stream_events))
                self.assertNotIn("imageBase64", str(envelope.data))

    def test_qq_non_owner_cannot_capture(self):
        with tempfile.TemporaryDirectory() as temporary, patch("config.MASTER_QQ", "12345"):
            _, _, files = services(Path(temporary))
            result, envelope = self.invoke(files, picture(), request_context={
                "qq_delivery_context": {"user_id": "99999", "is_group": True}})
            self.assertEqual(envelope.data["code"], "device_action_requires_owner")
            self.assertFalse(result.model_image_inputs)

    def test_process_query_validation_and_pagination_contract(self):
        handler = DesktopSatelliteToolHandler(tool_id="system_process_snapshot")
        self.assertEqual(handler.normalize_call({"type": handler.tool_type, "query": "Code", "offset": 128, "limit": 20}),
            {"type": handler.tool_type, "query": "Code", "offset": 128, "limit": 20})
        for bad in ({"offset": -1}, {"offset": True}, {"limit": 129}, {"limit": 0}, {"query": 123}, {"query": "a"*101}):
            self.assertIsNone(handler.normalize_call({"type": handler.tool_type, **bad}))

    def test_all_satellite_contracts_match_rust_wire_constants(self):
        import re
        from companion_v01.desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS
        source = (Path(__file__).resolve().parents[1] / "desktop_pet_next/src-tauri/src/main.rs").read_text(encoding="utf-8")
        for spec in DESKTOP_SATELLITE_TOOL_SPECS:
            prefix = spec.capability_id.upper()
            match = re.search(r"const " + prefix + r'_SCHEMA_HASH: &str =\s*"([^" ]+)"', source)
            self.assertIsNotNone(match, prefix)
            self.assertEqual(match.group(1), spec.schema_hash)


if __name__ == "__main__":
    unittest.main()
