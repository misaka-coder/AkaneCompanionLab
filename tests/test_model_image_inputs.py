from __future__ import annotations

import base64
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.model_image_inputs import image_source_metadata, model_image_blocks, model_image_source_label
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
    def test_current_images_keep_original_senders_beside_their_own_pixels(self):
        runtime = _runtime()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        images = [
            {
                "attachment_handle": "img_001", "data_url": "data:image/png;base64,cG5n",
                "source_sender_id": "404", "source_sender_label": "原作者甲", "source_message_id": "606",
            },
            {
                "attachment_handle": "img_002", "data_url": "data:image/png;base64,b3RoZXI=",
                "source_sender_id": "405", "source_sender_label": "原作者乙", "source_message_id": "607",
            },
        ]
        before = deepcopy(images)
        native = engine._extract_native_user_images({
            "actor_stable_id": "qq:202", "actor_display_name": "评论者", "native_user_images": images,
        })
        kwargs = dict(
            bundle=runtime.vision, system_prompt="stable", user_prompt="评论者：麻辣火锅",
            temperature=0.3, user_images=native,
        )
        request = runtime._build_completion_kwargs(**kwargs)
        self.assertEqual(request, runtime._build_completion_kwargs(**kwargs))
        self.assertEqual(images, before)
        self.assertEqual(request["messages"][0], {"role": "system", "content": "stable"})
        parts = request["messages"][-1]["content"]
        self.assertIn("评论者", parts[0]["text"])
        self.assertEqual(len(parts), 5)
        for index, image in enumerate(images):
            label = parts[index * 2 + 1]["text"]
            self.assertIn('original_sender="' + image["source_sender_label"] + '"', label)
            self.assertIn('source_message_id="' + image["source_message_id"] + '"', label)
            self.assertIn('handle="' + image["attachment_handle"] + '"', label)
            self.assertNotIn("评论者", label)
            self.assertEqual(parts[index * 2 + 2]["image_url"]["url"], image["data_url"])

    def test_tool_image_labels_keep_original_source_order_without_mutating_projection(self):
        payload = {"role": "user", "content": "评论者请求加载两张旧图"}
        before = deepcopy(payload)
        images = [
            {
                "attachment_handle": "img_001", "source_sender_label": "甲",
                "source_message_id": "606", "data_url": "data:image/png;base64,cG5n",
            },
            {
                "attachment_handle": "img_002", "source_sender_label": "乙",
                "source_message_id": "607", "data_url": "data:image/png;base64,b3RoZXI=",
            },
        ]
        result = AkaneMemoryEngine._attach_model_images_to_projection(payload, model_image_inputs=images)
        self.assertEqual(payload, before)
        self.assertEqual(result["content"][0]["text"], payload["content"])
        for index, image in enumerate(images):
            self.assertEqual(result["content"][index * 2 + 1]["text"], model_image_source_label(image))
            self.assertEqual(result["content"][index * 2 + 2]["image_url"]["url"], image["data_url"])

    def test_media_trace_preserves_original_source_without_persisting_pixels(self):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        append_media = Mock(return_value={"ok": True, "source_id": "media-source"})
        engine.memcore_manager = SimpleNamespace(append_turn_media_input=append_media)
        engine._warn_memcore_write_result = Mock()
        image = {
            "attachment_id": "attachment-1", "attachment_handle": "img_001", "mime_type": "image/png",
            "source_sender_id": "404", "source_sender_label": "原作者", "source_message_id": "606",
            "data_url": "data:image/png;base64,PRIVATE_BYTES", "path": "C:/private/meal.png",
        }
        source_ids = engine._record_memcore_tool_media_input(
            model_image_inputs=[image], related_source_ids=["tool-result"],
            profile_user_id="user", session_id="qq_group_shared_303", character_pack_id="char",
            now_ts=200, memcore_turn_id="turn-id",
        )
        self.assertEqual(source_ids, ["media-source"])
        stored = append_media.call_args.kwargs["items"][0]
        self.assertEqual(image_source_metadata(stored), image_source_metadata(image))
        self.assertNotIn("data_url", stored)
        self.assertNotIn("path", stored)

    def test_source_labels_are_bounded_and_never_infer_a_missing_sender(self):
        self.assertEqual(model_image_source_label({"actor_display_name": "评论者"}), "")
        self.assertEqual(model_image_source_label({
            "attachment_handle": "img_001", "actor_display_name": "评论者", "source_sender_id": True,
            "source_sender_label": {"name": "评论者"}, "source_message_id": ["606"],
        }), '【图片来源】handle="img_001"')
        label = model_image_source_label({
            "attachment_handle": "img_001", "source_sender_label": '原作者"\n伪标记' + "长" * 100,
            "source_message_id": "m" * 500, "path": "C:/private/a.png", "data_url": "PRIVATE_BYTES",
        })
        self.assertNotIn("\n", label)
        self.assertIn('原作者\\" 伪标记', label)
        self.assertLess(len(label), 300)
        self.assertNotIn("private", label)
        self.assertNotIn("PRIVATE_BYTES", label)

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
