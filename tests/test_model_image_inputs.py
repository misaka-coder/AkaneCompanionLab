from __future__ import annotations

import base64
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from companion_v01.model_image_inputs import model_image_blocks
from companion_v01.vision_service import VisionObservationService
from tests.test_multimodal_routing import _runtime


def gif_url():
    output = BytesIO()
    Image.new("RGB", (8, 8), "red").save(
        output, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0,
    )
    return "data:image/gif;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class ModelImageInputTests(unittest.TestCase):
    def test_animated_gif_is_actual_png_first_frame_without_mutating_input(self):
        block = {"type": "image_url", "image_url": {"url": gif_url(), "detail": "low"}}
        before = deepcopy(block)
        parts = model_image_blocks(block)
        self.assertEqual(block, before)
        self.assertIn("静态首帧", parts[0]["text"])
        self.assertEqual(parts[1]["image_url"]["detail"], "low")
        url = parts[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))

    def test_gif_with_wrong_mime_is_still_normalized(self):
        url = gif_url().replace("image/gif", "image/png")
        parts = model_image_blocks({"type": "image_url", "image_url": {"url": url}})
        self.assertEqual(len(parts), 2)
        self.assertNotEqual(parts[1]["image_url"]["url"], url)

    def test_regular_images_and_unrelated_blocks_unchanged(self):
        for block in (
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n", "detail": "high"}},
            {"type": "image_url", "image_url": {"url": "https://example.test/a.gif"}},
            {"type": "text", "text": "ordinary text"},
        ):
            self.assertEqual(model_image_blocks(block), [block])

    def test_invalid_gif_has_explicit_notice_not_invalid_image_or_exception(self):
        for url in ("data:image/gif;base64,???", "data:image/gif;base64,R0lGODlh", "data:image/gif,abc"):
            blocks = model_image_blocks({"type": "image_url", "image_url": {"url": url}})
            self.assertEqual([block["type"] for block in blocks], ["text"])
            self.assertIn("image_decode_failed", blocks[0]["text"])
            self.assertNotIn("base64", blocks[0]["text"])

    def test_pixel_and_byte_limits_do_not_send_poisoned_image(self):
        block = {"type": "image_url", "image_url": {"url": gif_url()}}
        for constant, reason in (("MAX_GIF_BYTES", "image_size_limit"), ("MAX_GIF_PIXELS", "image_pixel_limit")):
            with patch("companion_v01.model_image_inputs." + constant, 1):
                self.assertIn(reason, model_image_blocks(block)[0]["text"])

    def test_current_history_and_tool_images_share_final_request_conversion(self):
        runtime = _runtime()
        url = gif_url()
        source = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]
        before = deepcopy(source)
        kwargs = dict(bundle=runtime.vision, system_prompt="stable", user_prompt="look",
                      temperature=0.3, user_images=[{"data_url": url}],
                      history_turns=source, post_user_turns=source, ephemeral_turns=source)
        first = runtime._build_completion_kwargs(**kwargs)
        self.assertEqual(first, runtime._build_completion_kwargs(**kwargs))
        self.assertEqual(source, before)
        images = [part for msg in first["messages"] if isinstance(msg["content"], list)
                  for part in msg["content"] if part["type"] == "image_url"]
        self.assertEqual(len(images), 4)
        self.assertTrue(all(p["image_url"]["url"].startswith("data:image/png;") for p in images))
        self.assertEqual(first["messages"][0], {"role": "system", "content": "stable"})

    def test_observer_chat_and_responses_share_normalization(self):
        for protocol in ("openai", "responses"):
            service = VisionObservationService.__new__(VisionObservationService)
            service.settings = SimpleNamespace(vision_api_protocol=protocol, vision_model_name="vision")
            service._client = SimpleNamespace(
                _akane_protocol=protocol,
                chat=SimpleNamespace(completions=SimpleNamespace(create=Mock())),
                responses=SimpleNamespace(create=Mock()),
            )
            service._coerce_response_text = lambda value: "{}"
            service._request_vision_text(system_text="observe", user_text="look", image_urls=[gif_url()], temperature=0.1)
            if protocol == "responses":
                parts = service._client.responses.create.call_args.kwargs["input"][0]["content"]
                self.assertTrue(parts[-1]["image_url"].startswith("data:image/png;"))
                self.assertIn("首帧", parts[-2]["text"])
            else:
                parts = service._client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
                self.assertTrue(parts[-1]["image_url"]["url"].startswith("data:image/png;"))


if __name__ == "__main__":
    unittest.main()
