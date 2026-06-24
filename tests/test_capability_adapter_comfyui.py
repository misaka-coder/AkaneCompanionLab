from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.capability_adapters import (
    CapabilityProtocolError,
    ComfyUiCapabilityAdapter,
    ComfyUiWorkflowCapability,
    InvocationContext,
    build_comfyui_adapter_from_manifest,
    load_manifest,
)
from companion_v01.capability_adapters.types import CapabilityManifest
from companion_v01.local_workflow_runners.comfyui import ComfyUiClient


class FakeResponse:
    def __init__(self, payload=None, *, status_code: int = 200, content: bytes = b"", headers=None) -> None:
        self.payload = payload if payload is not None else {}
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeWorkflowSession:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/upload/image"):
            return FakeResponse({"name": "uploaded.png", "subfolder": "akane", "type": "input"})
        if url.endswith("/prompt"):
            return FakeResponse({"prompt_id": "prompt-001"})
        return FakeResponse({}, status_code=404)

    def get(self, url: str, **kwargs):
        self.gets.append((url, kwargs))
        if "/history/" in url:
            return FakeResponse(
                {
                    "prompt-001": {
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "result.png", "subfolder": "akane", "type": "output"}
                                ]
                            }
                        }
                    }
                }
            )
        if url.endswith("/view"):
            return FakeResponse({}, content=b"\x89PNG\r\n\x1a\nresult", headers={"content-type": "image/png"})
        return FakeResponse({}, status_code=404)


def write_workflow(path: Path) -> None:
    path.write_text(
        '{"1":{"class_type":"LoadImage","inputs":{"image":"old.png"}},'
        '"2":{"class_type":"SaveImage","inputs":{"filename_prefix":"old"}}}',
        encoding="utf-8",
    )


class ComfyUiCapabilityAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_capabilities_describes_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "portrait.json"
            write_workflow(workflow_path)
            adapter = ComfyUiCapabilityAdapter(
                provider_id="comfyui",
                endpoint="http://127.0.0.1:8188",
                capabilities=(
                    ComfyUiWorkflowCapability(
                        capability_id="comfyui.portrait_cutout",
                        display_name="Portrait Cutout",
                        workflow_path=workflow_path,
                        slot_mapping={
                            "input_image_handle": "1.inputs.image",
                            "output_image_handle": "2.inputs.filename_prefix",
                        },
                    ),
                ),
            )

            descriptor = (await adapter.list_capabilities())[0]

            self.assertEqual(descriptor.id, "comfyui.portrait_cutout")
            self.assertEqual(descriptor.effects, ("media_generation",))
            self.assertEqual(descriptor.inputs[0].kind, "image_bytes")

    async def test_invoke_runs_configured_workflow_without_business_code_per_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "upscale.json"
            write_workflow(workflow_path)
            session = FakeWorkflowSession()
            adapter = ComfyUiCapabilityAdapter(
                provider_id="comfyui",
                endpoint="http://127.0.0.1:8188",
                capabilities=(
                    ComfyUiWorkflowCapability(
                        capability_id="comfyui.image_upscale",
                        display_name="Image Upscale",
                        workflow_path=workflow_path,
                        slot_mapping={
                            "input_image_handle": "1.inputs.image",
                            "output_image_handle": "2.inputs.filename_prefix",
                        },
                    ),
                ),
                client_factory=lambda endpoint: ComfyUiClient(endpoint, session=session, timeout_seconds=1),
                sleep=lambda _seconds: None,
                poll_interval_seconds=0.01,
                max_poll_seconds=0.05,
            )

            result = await adapter.invoke(
                "comfyui.image_upscale",
                {
                    "image": b"\x89PNG\r\n\x1a\nsource",
                    "output_handle": "upscaled",
                    "content_type": "image/png",
                },
                InvocationContext(profile_user_id="master", session_id="desktop"),
            )

            self.assertFalse(result.is_error)
            output_asset = result.content["outputAssets"][0]
            self.assertEqual(output_asset.handle, "upscaled")
            self.assertEqual(output_asset.data, b"\x89PNG\r\n\x1a\nresult")
            prompt = session.posts[1][1]["json"]["prompt"]
            self.assertEqual(prompt["1"]["inputs"]["image"], "uploaded.png")
            self.assertEqual(prompt["2"]["inputs"]["filename_prefix"], "upscaled")

    async def test_unknown_capability_raises_protocol_error(self) -> None:
        adapter = ComfyUiCapabilityAdapter(provider_id="comfyui", endpoint="http://127.0.0.1:8188", capabilities=())
        with self.assertRaises(CapabilityProtocolError):
            await adapter.invoke("comfyui.missing", {}, InvocationContext())

    async def test_manifest_plus_workflow_json_builds_second_workflow_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_path = root / "workflows" / "upscale.json"
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            write_workflow(workflow_path)
            manifest_path = root / "comfyui.yaml"
            manifest_path.write_text(
                """
schema: capability_adapter/v1
provider:
  id: comfyui
  type: comfyui
  endpoint:
    url: http://127.0.0.1:8188
    loopback_only: true
capabilities:
  - id: comfyui.image_upscale
    display_name: Image Upscale
    short_hint: Upscale an image.
    visible_in: [desktop]
    prompt_exposed: false
    risk: medium
    confirm: first_time
    effects: [media_generation]
    workflow_template: workflows/upscale.json
    slot_mapping:
      input_image_handle: 1.inputs.image
      output_image_handle: 2.inputs.filename_prefix
""".strip(),
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path, source_layer="builtin")
            self.assertIsInstance(manifest, CapabilityManifest)
            session = FakeWorkflowSession()
            adapter = build_comfyui_adapter_from_manifest(
                manifest,
                client_factory=lambda endpoint: ComfyUiClient(endpoint, session=session, timeout_seconds=1),
                sleep=lambda _seconds: None,
                poll_interval_seconds=0.01,
                max_poll_seconds=0.05,
            )

            result = await adapter.invoke(
                "comfyui.image_upscale",
                {"image": b"\x89PNG\r\n\x1a\nsource", "output_handle": "manifest_upscaled"},
                InvocationContext(),
            )

            self.assertFalse(result.is_error)
            self.assertEqual(result.content["outputAssets"][0].handle, "manifest_upscaled")


if __name__ == "__main__":
    unittest.main()
