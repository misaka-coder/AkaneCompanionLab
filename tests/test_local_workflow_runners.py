from __future__ import annotations

import unittest

from companion_v01.local_workflow_runners.comfyui import (
    ComfyUiClient,
    ComfyUiClientError,
    ComfyUiImageRef,
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
            client.upload_image(b"image-bytes", filename=r"C:\Users\Lenovo\portrait.png")
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


if __name__ == "__main__":
    unittest.main()
