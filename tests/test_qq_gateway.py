from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from companion_v01.qq_gateway import NapCatQQGateway


class QQGatewayTests(unittest.TestCase):
    def test_render_reply_messages_prefers_speech_segments(self) -> None:
        gateway = NapCatQQGateway()

        messages = gateway.render_reply_messages(
            {
                "speech": "第一句\n第二句",
                "speech_segments": ["第一句", "第二句"],
                "code_snippet": "",
            }
        )

        self.assertEqual(messages, ["第一句", "第二句"])

    def test_render_reply_messages_appends_code_to_last_segment(self) -> None:
        gateway = NapCatQQGateway()

        messages = gateway.render_reply_messages(
            {
                "speech": "",
                "speech_segments": ["我先说一句。", "代码在这里。"],
                "code_snippet": "print('hi')",
            }
        )

        self.assertEqual(messages, ["我先说一句。", "代码在这里。\n\nprint('hi')"])

    def test_duplicate_message_id_is_ignored(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "message_id": "abc-1",
            "raw_message": "在吗",
        }

        first = gateway.build_message_context(event)
        second = gateway.build_message_context(dict(event))

        self.assertTrue(first.should_respond)
        self.assertFalse(second.should_respond)
        self.assertEqual(second.reason, "duplicate_event")

    @patch("companion_v01.qq_gateway.config.QQ_ALLOW_STALE_EVENTS", False)
    @patch("companion_v01.qq_gateway.config.QQ_EVENT_MAX_AGE_SECONDS", 300)
    def test_stale_message_event_is_ignored(self) -> None:
        gateway = NapCatQQGateway()

        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "old-message-1",
                "time": int(time.time()) - 3600,
                "raw_message": "在吗",
            }
        )

        self.assertFalse(context.should_respond)
        self.assertEqual(context.reason, "stale_event")

    @patch("companion_v01.qq_gateway.config.QQ_ALLOW_STALE_EVENTS", True)
    @patch("companion_v01.qq_gateway.config.QQ_EVENT_MAX_AGE_SECONDS", 300)
    def test_stale_message_event_can_be_allowed_for_debug(self) -> None:
        gateway = NapCatQQGateway()

        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "old-message-debug-1",
                "time": int(time.time()) - 3600,
                "raw_message": "在吗",
            }
        )

        self.assertTrue(context.should_respond)

    def test_group_follow_is_not_armed_after_mention(self) -> None:
        gateway = NapCatQQGateway()
        mention_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "group_id": 123456,
            "message_id": "group-1",
            "message": [
                {"type": "at", "data": {"qq": "2184046306"}},
                {"type": "text", "data": {"text": " 在吗"}},
            ],
        }
        follow_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "group_id": 123456,
            "message_id": "group-2",
            "message": [
                {"type": "text", "data": {"text": "我是在回复别人"}},
            ],
        }

        mentioned = gateway.build_message_context(mention_event)
        follow = gateway.build_message_context(follow_event)

        self.assertTrue(mentioned.should_respond)
        self.assertEqual(mentioned.reason, "group_mention")
        self.assertFalse(follow.should_respond)
        self.assertEqual(follow.reason, "group_message_without_mention")

    def test_group_mention_opens_attachment_only_buffer_for_same_sender(self) -> None:
        gateway = NapCatQQGateway()
        mention_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "group_id": 123456,
            "message_id": "group-buffer-1",
            "message": [
                {"type": "at", "data": {"qq": "2184046306"}},
                {"type": "text", "data": {"text": " 我等下补图"}},
            ],
        }
        image_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "group_id": 123456,
            "message_id": "group-buffer-2",
            "message": [
                {
                    "type": "image",
                    "data": {
                        "file": "follow-up.jpg",
                        "url": "http://127.0.0.1/follow-up.jpg",
                    },
                },
            ],
        }
        text_event = {
            **image_event,
            "message_id": "group-buffer-3",
            "message": [{"type": "text", "data": {"text": "这句没有 at，不该回"}}],
        }
        other_user_image_event = {
            **image_event,
            "user_id": 222333444,
            "message_id": "group-buffer-4",
        }

        mentioned = gateway.build_message_context(mention_event)
        buffered_image = gateway.build_message_context(image_event)
        plain_text = gateway.build_message_context(text_event)
        other_user_image = gateway.build_message_context(other_user_image_event)

        self.assertTrue(mentioned.should_respond)
        self.assertEqual(mentioned.reason, "group_mention")
        self.assertTrue(buffered_image.should_respond)
        self.assertEqual(buffered_image.reason, "group_attachment_buffer")
        self.assertEqual(buffered_image.clean_message, "发来了一张图片。")
        self.assertEqual(len(buffered_image.attachments or []), 1)
        self.assertFalse(plain_text.should_respond)
        self.assertEqual(plain_text.reason, "group_message_without_mention")
        self.assertFalse(other_user_image.should_respond)
        self.assertEqual(other_user_image.reason, "group_message_without_mention")

    def test_group_members_share_group_scoped_memory(self) -> None:
        gateway = NapCatQQGateway()
        first_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 2184046306,
            "user_id": 111222333,
            "group_id": 123456,
            "message_id": "group-member-1",
            "sender": {"card": "休比", "nickname": "fallback"},
            "message": [
                {"type": "at", "data": {"qq": "2184046306"}},
                {"type": "text", "data": {"text": " 你好"}},
            ],
        }
        second_event = {
            **first_event,
            "user_id": 444555666,
            "message_id": "group-member-2",
        }

        first = gateway.build_message_context(first_event)
        second = gateway.build_message_context(second_event)

        self.assertTrue(first.should_respond)
        self.assertTrue(second.should_respond)
        self.assertEqual(first.session_id, "qq_group_shared_123456")
        self.assertEqual(second.session_id, "qq_group_shared_123456")
        self.assertEqual(first.profile_user_id, "qq_group_shared_123456")
        self.assertEqual(second.profile_user_id, "qq_group_shared_123456")

    def test_group_turn_payload_keeps_sender_label_for_shared_memory(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 2184046306,
            "user_id": 111222333,
            "group_id": 123456,
            "message_id": "group-speaker-1",
            "sender": {"card": "休比", "nickname": "fallback"},
            "message": [
                {"type": "at", "data": {"qq": "2184046306"}},
                {"type": "text", "data": {"text": " 你好"}},
            ],
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()

        self.assertEqual(context.sender_label, "休比")
        self.assertEqual(payload["message"], "【休比】你好")
        self.assertIn("【昵称】", payload["extra_context"])

    def test_extracts_image_and_file_attachments_from_segments(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "message_id": "attachment-1",
            "message": [
                {"type": "text", "data": {"text": "看看这个"}},
                {
                    "type": "image",
                    "data": {
                        "file": "dinner.jpg",
                        "url": "http://127.0.0.1:3001/dinner.jpg",
                        "size": "1234",
                    },
                },
                {
                    "type": "file",
                    "data": {
                        "name": "计划.md",
                        "url": "http://127.0.0.1:3001/plan.md",
                    },
                },
            ],
        }

        context = gateway.build_message_context(event)

        self.assertTrue(context.should_respond)
        self.assertEqual(context.clean_message, "看看这个 [图片] [文件]")
        self.assertEqual(len(context.attachments or []), 2)
        image, document = context.attachments or []
        self.assertEqual(image["kind"], "image")
        self.assertEqual(image["file"], "dinner.jpg")
        self.assertEqual(image["origin_name"], "dinner.jpg")
        self.assertEqual(image["file_size"], 1234)
        self.assertEqual(document["kind"], "document")
        self.assertEqual(document["file"], "计划.md")
        self.assertEqual(document["origin_name"], "计划.md")

    def test_extracts_raw_cq_attachment_fallbacks(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 2184046306,
            "user_id": 1906243651,
            "message_id": "attachment-raw-1",
            "raw_message": "[CQ:image,file=cat.png,url=http://127.0.0.1/cat.png]",
        }

        context = gateway.build_message_context(event)

        self.assertTrue(context.should_respond)
        self.assertEqual(context.clean_message, "发来了一张图片。")
        self.assertEqual(len(context.attachments or []), 1)
        self.assertEqual((context.attachments or [])[0]["origin_name"], "cat.png")

    @patch("companion_v01.qq_gateway.config.QQ_ATTACHMENT_DEBOUNCE_SECONDS", 1.2)
    def test_attachment_debounce_only_latest_event_processes(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "debounce-1",
                "raw_message": "[CQ:image,file=one.png,url=http://127.0.0.1/one.png]",
            }
        )

        first = gateway.register_attachment_debounce(context, attachment_ids=["attachment::1"])
        second = gateway.register_attachment_debounce(context, attachment_ids=["attachment::2"])

        self.assertFalse(gateway.consume_attachment_debounce(first)["process"])
        latest = gateway.consume_attachment_debounce(second)
        self.assertTrue(latest["process"])
        self.assertEqual(latest["attachment_ids"], ["attachment::1", "attachment::2"])

    @patch("companion_v01.qq_gateway.config.QQ_ATTACHMENT_DEBOUNCE_SECONDS", 0.0)
    def test_attachment_debounce_can_be_disabled(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "debounce-disabled-1",
                "raw_message": "[CQ:image,file=one.png,url=http://127.0.0.1/one.png]",
            }
        )

        token = gateway.register_attachment_debounce(context, attachment_ids=["attachment::1"])

        self.assertFalse(token["enabled"])
        self.assertTrue(token["process"])
        self.assertEqual(token["attachment_ids"], ["attachment::1"])

    def test_send_generated_files_uses_onebot_upload_action(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "generated-send-1",
                "raw_message": "发我文件",
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "generated_file_ready",
                        "send_to_user": True,
                        "generated_file": {
                            "generated_id": "generated::1",
                            "absolute_path": "C:/tmp/akane.md",
                            "output_title": "Akane整理",
                            "file_ext": "md",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        mocked_post.assert_called_once()
        url = mocked_post.call_args.args[0]
        payload = mocked_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/upload_private_file"))
        self.assertEqual(payload["user_id"], 1906243651)
        self.assertEqual(payload["file"], "C:/tmp/akane.md")
        self.assertEqual(payload["name"], "Akane整理.md")

    @patch("companion_v01.qq_gateway.config.QQ_REQUIRE_FILE_DELIVERY_INTENT", True)
    def test_send_generated_files_blocks_without_current_delivery_intent(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "generated-block-1",
                "raw_message": "在吗",
            }
        )

        with patch("companion_v01.qq_gateway.requests.post") as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "generated_file_ready",
                        "send_to_user": True,
                        "generated_file": {
                            "generated_id": "generated::old",
                            "absolute_path": "C:/tmp/old.md",
                            "output_title": "旧文件",
                            "file_ext": "md",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(result["reason"], "missing_file_delivery_intent")
        mocked_post.assert_not_called()

    def test_file_delivery_intent_respects_negative_request(self) -> None:
        gateway = NapCatQQGateway()

        self.assertTrue(gateway.message_requests_file_delivery("把 gen_001 发我一下"))
        self.assertFalse(gateway.message_requests_file_delivery("先别发文件，我只是问问进度"))

    def test_send_generated_files_accepts_generic_file_ready_event(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": 2184046306,
                "group_id": 12345,
                "user_id": 1906243651,
                "message_id": "file-ready-1",
                "message": [
                    {"type": "at", "data": {"qq": "2184046306"}},
                    {"type": "text", "data": {"text": " 发我文件"}},
                ],
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "file_ready",
                        "send_to_user": True,
                        "file": {
                            "source_type": "attachment",
                            "source_id": "attachment::1",
                            "absolute_path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        mocked_post.assert_called_once()
        url = mocked_post.call_args.args[0]
        payload = mocked_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/upload_group_file"))
        self.assertEqual(payload["group_id"], 12345)
        self.assertEqual(payload["file"], "C:/tmp/video.mp4")
        self.assertEqual(payload["name"], "video.mp4")

    def test_send_generated_files_ignores_desktop_client_file_events(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 2184046306,
                "user_id": 1906243651,
                "message_id": "file-ready-desktop-1",
                "raw_message": "发我文件",
            }
        )

        with patch("companion_v01.qq_gateway.requests.post") as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "file_ready",
                        "client_mode": "desktop_pet",
                        "send_to_user": True,
                        "delivery_action": "save_desktop",
                        "desktop_delivery": {
                            "action": "save_desktop",
                            "path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                        "file": {
                            "source_type": "attachment",
                            "source_id": "attachment::1",
                            "absolute_path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        mocked_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
