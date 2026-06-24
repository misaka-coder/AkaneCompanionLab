from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.local_capability_config import save_provider_config, save_workflow_config
from companion_v01.local_workflow_execution import WorkflowExecutionAsset, WorkflowExecutionRequest
from companion_v01.local_workflow_runners.comfyui import (
    ComfyUiClient,
    ComfyUiClientError,
    ComfyUiImageRef,
    ComfyUiSlotMappingError,
    ComfyUiWorkflowRunner,
    apply_comfyui_input_slots,
    extract_comfyui_output_images,
)


class FakeResponse:
    def __init__(
        self,
        payload=None,
        *,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload if payload is not None else {}
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/upload/image"):
            return FakeResponse({"name": "portrait.png", "subfolder": "akane", "type": "input"})
        if url.endswith("/prompt"):
            return FakeResponse({"prompt_id": "prompt-001"})
        return FakeResponse({}, status_code=404)

    def get(self, url: str, **kwargs):
        self.gets.append((url, kwargs))
        if "/history/" in url:
            return FakeResponse({"prompt-001": {"status": {"completed": True}}})
        if url.endswith("/view"):
            return FakeResponse(
                {},
                content=b"png-bytes",
                headers={"content-type": "image/png"},
            )
        return FakeResponse({}, status_code=404)


class FakeWorkflowSession(FakeSession):
    def get(self, url: str, **kwargs):
        self.gets.append((url, kwargs))
        if "/history/" in url:
            return FakeResponse(
                {
                    "prompt-001": {
                        "outputs": {
                            "30": {
                                "images": [
                                    {"filename": "cutout.png", "subfolder": "akane", "type": "output"}
                                ]
                            }
                        }
                    }
                }
            )
        if url.endswith("/view"):
            return FakeResponse({}, content=b"\x89PNG\r\n\x1a\ncutout", headers={"content-type": "image/png"})
        return FakeResponse({}, status_code=404)


class ComfyUiClientTests(unittest.TestCase):
    def test_client_uses_loopback_only_normalized_endpoint(self) -> None:
        client = ComfyUiClient("http://localhost:8188/ui?token=secret", session=FakeSession())

        self.assertEqual(client.endpoint, "http://127.0.0.1:8188")
        with self.assertRaises(ValueError):
            ComfyUiClient("https://example.com:8188", session=FakeSession())

    def test_upload_queue_history_and_view_use_public_comfyui_routes(self) -> None:
        session = FakeSession()
        client = ComfyUiClient("http://127.0.0.1:8188", session=session, timeout_seconds=1)

        image_ref = client.upload_image(
            b"image-bytes",
            filename="portrait.png",
            subfolder="akane",
            image_type="input",
        )
        prompt_id = client.queue_prompt({"1": {"class_type": "LoadImage"}}, client_id="akane-workshop")
        history = client.get_history(prompt_id)
        image = client.get_image(ComfyUiImageRef("result.png", "akane", "output"))

        self.assertEqual(image_ref.filename, "portrait.png")
        self.assertEqual(prompt_id, "prompt-001")
        self.assertIn("prompt-001", history)
        self.assertEqual(image.data, b"png-bytes")
        self.assertEqual(image.content_type, "image/png")
        upload_url, upload_kwargs = session.posts[0]
        self.assertTrue(upload_url.endswith("/upload/image"))
        self.assertEqual(upload_kwargs["data"]["subfolder"], "akane")
        self.assertEqual(upload_kwargs["data"]["type"], "input")
        self.assertEqual(upload_kwargs["files"]["image"][0], "portrait.png")
        prompt_url, prompt_kwargs = session.posts[1]
        self.assertTrue(prompt_url.endswith("/prompt"))
        self.assertEqual(prompt_kwargs["json"]["client_id"], "akane-workshop")
        view_url, view_kwargs = session.gets[-1]
        self.assertTrue(view_url.endswith("/view"))
        self.assertEqual(view_kwargs["params"]["filename"], "result.png")
        self.assertEqual(view_kwargs["params"]["subfolder"], "akane")
        self.assertEqual(view_kwargs["params"]["type"], "output")

    def test_client_rejects_paths_and_bad_responses_before_execution_boundary(self) -> None:
        client = ComfyUiClient("http://127.0.0.1:8188", session=FakeSession())

        with self.assertRaises(ValueError):
            client.upload_image(b"image-bytes", filename=r"C:\Users\ExampleUser\portrait.png")
        with self.assertRaises(ValueError):
            client.get_image("../result.png")
        with self.assertRaises(ValueError):
            client.queue_prompt({"1": {}}, client_id="token=secret")
        with self.assertRaises(ValueError):
            client.upload_image(b"", filename="portrait.png")

        broken = FakeSession()
        broken.post = lambda *_args, **_kwargs: FakeResponse({"prompt_id": "../bad"})
        broken_client = ComfyUiClient("http://127.0.0.1:8188", session=broken)
        with self.assertRaises(ComfyUiClientError):
            broken_client.queue_prompt({"1": {}})

    def test_extract_output_images_from_history_response(self) -> None:
        history = {
            "prompt-001": {
                "outputs": {
                    "12": {
                        "images": [
                            {"filename": "portrait.png", "subfolder": "akane", "type": "output"},
                            {"filename": "mask.webp", "subfolder": "", "type": "temp"},
                        ]
                    },
                    "20": {"text": ["ignored"]},
                }
            }
        }

        refs = extract_comfyui_output_images(history, prompt_id="prompt-001")

        self.assertEqual(
            refs,
            [
                ComfyUiImageRef("portrait.png", "akane", "output"),
                ComfyUiImageRef("mask.webp", "", "temp"),
            ],
        )

    def test_extract_output_images_discards_unsafe_history_values(self) -> None:
        history = {
            "outputs": {
                "12": {
                    "images": [
                        {"filename": "safe.png", "subfolder": "akane", "type": "output"},
                        {"filename": "../escape.png", "subfolder": "akane", "type": "output"},
                        {"filename": "http://example.test/escape.png", "subfolder": "akane", "type": "output"},
                        {"filename": "token_secret.png", "subfolder": "akane", "type": "output"},
                        {"filename": "bad-subfolder.png", "subfolder": "../akane", "type": "output"},
                        {"filename": "bad-type.png", "subfolder": "akane", "type": "api_key"},
                        {"filename": "bad-url-subfolder.png", "subfolder": "http://example.test/a", "type": "output"},
                        "not-a-dict",
                    ]
                }
            }
        }

        refs = extract_comfyui_output_images(history)

        self.assertEqual(refs, [ComfyUiImageRef("safe.png", "akane", "output")])

    def test_extract_output_images_rejects_ambiguous_or_malformed_history(self) -> None:
        with self.assertRaises(ValueError):
            extract_comfyui_output_images({}, prompt_id="../prompt")

        with self.assertRaises(ComfyUiClientError):
            extract_comfyui_output_images(
                {
                    "prompt-001": {"outputs": {}},
                    "prompt-002": {"outputs": {}},
                }
            )

        with self.assertRaises(ComfyUiClientError):
            extract_comfyui_output_images({"outputs": []})

    def test_apply_input_slots_updates_workflow_copy_only(self) -> None:
        workflow = {
            "12": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
            "20": {"class_type": "SomeNode", "inputs": {"options": {"padding": 0}}},
        }

        patched = apply_comfyui_input_slots(
            workflow,
            {
                "input_image_handle": "12.inputs.image",
                "padding": "20.inputs.options.padding",
            },
            {
                "input_image_handle": "uploaded.png",
                "padding": 12,
            },
        )

        self.assertEqual(patched["12"]["inputs"]["image"], "uploaded.png")
        self.assertEqual(patched["20"]["inputs"]["options"]["padding"], 12)
        self.assertEqual(workflow["12"]["inputs"]["image"], "old.png")
        self.assertEqual(workflow["20"]["inputs"]["options"]["padding"], 0)

    def test_apply_input_slots_rejects_unsafe_or_non_input_paths(self) -> None:
        workflow = {"12": {"class_type": "LoadImage", "inputs": {"image": "old.png"}}}

        with self.assertRaises(ComfyUiSlotMappingError):
            apply_comfyui_input_slots(workflow, {"input_image_handle": "31.outputs.images"}, {"input_image_handle": "x"})
        with self.assertRaises(ComfyUiSlotMappingError):
            apply_comfyui_input_slots(workflow, {"input_image_handle": "404.inputs.image"}, {"input_image_handle": "x"})
        with self.assertRaises(ValueError):
            apply_comfyui_input_slots(workflow, {"token=secret": "12.inputs.image"}, {"token=secret": "x"})

    def test_workflow_runner_executes_configured_comfyui_cutout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.comfyui.local",
                payload={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            )
            save_workflow_config(
                base_dir=temp_dir,
                profile_user_id="master",
                workflow_id="workflow.workshop.portrait.cutout",
                payload={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
                    },
                },
            )
            workflow_path = Path(temp_dir) / "master" / "capabilities" / "workflows" / "comfyui" / "portrait_cutout.json"
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text(
                '{"12":{"class_type":"LoadImage","inputs":{"image":"old.png"}},'
                '"20":{"class_type":"SaveImage","inputs":{"filename_prefix":"old"}}}',
                encoding="utf-8",
            )
            session = FakeWorkflowSession()
            runner = ComfyUiWorkflowRunner(
                config_base_dir=temp_dir,
                client_factory=lambda endpoint: ComfyUiClient(endpoint, session=session, timeout_seconds=1),
                sleep=lambda _seconds: None,
                poll_interval_seconds=0.01,
                max_poll_seconds=0.05,
            )
            request = WorkflowExecutionRequest(
                job_id="workflowjob_0123456789abcdef0123456789abcdef",
                workflow_id="workflow.workshop.portrait.cutout",
                capability_id="workshop.portrait.cutout",
                profile_user_id="master",
                session_id="desktop",
                inputs={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
                input_assets={
                    "portrait_source": WorkflowExecutionAsset(
                        handle="portrait_source",
                        data=b"\x89PNG\r\n\x1a\nsource",
                        content_type="image/png",
                    )
                },
            )

            result = runner.execute_workflow(request)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.reason, "workflow_completed")
            self.assertEqual(result.outputs[0]["handle"], "portrait_cutout")
            self.assertEqual(result.output_assets[0].data, b"\x89PNG\r\n\x1a\ncutout")
            self.assertEqual(result.output_assets[0].content_type, "image/png")

            upload_url, upload_kwargs = session.posts[0]
            self.assertTrue(upload_url.endswith("/upload/image"))
            self.assertEqual(upload_kwargs["data"]["subfolder"], "akane")
            self.assertEqual(upload_kwargs["files"]["image"][0], "akane_456789abcdef_input.png")
            prompt_url, prompt_kwargs = session.posts[1]
            self.assertTrue(prompt_url.endswith("/prompt"))
            prompt = prompt_kwargs["json"]["prompt"]
            self.assertEqual(prompt["12"]["inputs"]["image"], "portrait.png")
            self.assertEqual(prompt["20"]["inputs"]["filename_prefix"], "portrait_cutout")

    def test_workflow_runner_fails_without_input_bytes_or_safe_workflow_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.comfyui.local",
                payload={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            )
            save_workflow_config(
                base_dir=temp_dir,
                profile_user_id="master",
                workflow_id="workflow.workshop.portrait.cutout",
                payload={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
                    },
                },
            )
            runner = ComfyUiWorkflowRunner(config_base_dir=temp_dir, sleep=lambda _seconds: None)
            request = WorkflowExecutionRequest(
                job_id="workflowjob_0123456789abcdef0123456789abcdef",
                workflow_id="workflow.workshop.portrait.cutout",
                capability_id="workshop.portrait.cutout",
                profile_user_id="master",
                session_id="desktop",
                inputs={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            )

            result = runner.execute_workflow(request)

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "input_image_bytes_required")


if __name__ == "__main__":
    unittest.main()
