from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import patch

from channelcore_onebot import (
    build_message_action,
    normalize_action_response,
    normalize_inbound_event,
    resolve_quoted_message,
)

from companion_v01.qq_gateway import NapCatQQGateway


BOT_ID = "10000001"
USER_ID = "20000001"
GROUP_ID = "30000001"


class QQChannelcoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        bot_patcher = patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", BOT_ID)
        bot_patcher.start()
        self.addCleanup(bot_patcher.stop)

    def test_gateway_real_context_path_uses_package_inbound_normalizer(self) -> None:
        gateway = NapCatQQGateway(wake_words=("Akane",))
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "group_id": GROUP_ID,
            "message_id": "package-backed-1",
            "sender": {"card": "伙伴"},
            "message": [
                {"type": "at", "data": {"qq": BOT_ID}},
                {"type": "text", "data": {"text": " 看看这个"}},
                {
                    "type": "image",
                    "data": {
                        "file": "image.jpg",
                        "url": "https://provider.invalid/private-image",
                    },
                },
            ],
        }

        with patch(
            "companion_v01.qq_gateway.normalize_inbound_event",
            wraps=normalize_inbound_event,
        ) as package_parser:
            context = gateway.build_message_context(event)

        package_parser.assert_called_once_with(event, bot_account_id=BOT_ID, wake_words=("Akane",))
        self.assertTrue(context.should_respond)
        self.assertEqual(context.reason, "group_mention")
        self.assertEqual(context.sender_label, "伙伴")
        self.assertEqual(context.clean_message, "看看这个 [图片]")
        self.assertEqual(len(context.attachments or []), 1)
        self.assertEqual((context.attachments or [])[0]["kind"], "image")

    def test_gateway_poke_path_uses_same_package_normalizer(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": BOT_ID,
            "operator_id": USER_ID,
            "user_id": BOT_ID,
            "target_id": BOT_ID,
            "time": int(time.time()),
        }

        with patch(
            "companion_v01.qq_gateway.normalize_inbound_event",
            wraps=normalize_inbound_event,
        ) as package_parser:
            context = gateway.build_message_context(event)

        package_parser.assert_called_once()
        self.assertTrue(context.should_respond)
        self.assertEqual(context.reason, "qq_poke")
        self.assertEqual(context.user_id, int(USER_ID))

    def test_package_parses_sticker_without_claiming_akane_material_support(self) -> None:
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "message_id": "sticker-1",
            "message": [
                {"type": "text", "data": {"text": "这个表情"}},
                {
                    "type": "mface",
                    "data": {
                        "emoji_id": "mface-1",
                        "summary": "开心",
                        "url": "https://provider.invalid/private-sticker",
                    },
                },
            ],
        }

        normalized = normalize_inbound_event(event, bot_account_id=BOT_ID)
        context = NapCatQQGateway().build_message_context(event)

        self.assertEqual(normalized.message.attachments[0].kind if normalized.message else "", "sticker")
        self.assertEqual(normalized.message.attachments[0].platform_id if normalized.message else "", "mface-1")
        self.assertEqual(context.attachments, [])
        self.assertTrue(context.should_respond)
        self.assertEqual(context.clean_message, "这个表情")
        self.assertNotIn("provider.invalid", str(normalized.as_dict()))

    def test_gateway_group_context_delegates_trigger_decision_to_package_policy(self) -> None:
        gateway = NapCatQQGateway(wake_words=("Akane",))
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "group_id": GROUP_ID,
            "message_id": "package-backed-group-trigger-1",
            "message": [
                {"type": "text", "data": {"text": "Akane 看看这个"}},
                {
                    "type": "image",
                    "data": {"file": "image.jpg", "url": "https://provider.invalid/image"},
                },
            ],
        }

        with patch.object(
            gateway._group_trigger,
            "evaluate",
            wraps=gateway._group_trigger.evaluate,
        ) as trigger_policy:
            context = gateway.build_message_context(event)

        trigger_policy.assert_called_once()
        kwargs = trigger_policy.call_args.kwargs
        self.assertEqual(kwargs["group_id"], GROUP_ID)
        self.assertEqual(kwargs["actor_id"], USER_ID)
        self.assertFalse(kwargs["mentioned_bot"])
        self.assertTrue(kwargs["mentioned_wake_word"])
        self.assertTrue(kwargs["has_attachments"])
        self.assertTrue(kwargs["allow_attachment_follow"])
        self.assertTrue(context.should_respond)
        self.assertEqual(context.reason, "group_wake_word")

    def test_gateway_quoted_lookup_delegates_action_and_scope_to_package(self) -> None:
        gateway = NapCatQQGateway(wake_words=("Akane",))
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "group_id": GROUP_ID,
            "message_id": "current-quote-1",
            "message": [
                {"type": "reply", "data": {"id": "quoted-1"}},
                {"type": "text", "data": {"text": "Akane 看看图"}},
            ],
        }
        context = gateway.build_message_context(event)

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "message_id": "quoted-1",
                        "self_id": BOT_ID,
                        "message_type": "group",
                        "group_id": GROUP_ID,
                        "user_id": USER_ID,
                        "sender": {"user_id": USER_ID, "card": "伙伴"},
                        "time": 1_721_485_640,
                        "message": [
                            {"type": "text", "data": {"text": "这是很久以前的原话"}},
                            {"type": "image", "data": {"file": "quoted.png"}},
                        ],
                    },
                }

        with (
            patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()),
            patch(
                "companion_v01.qq_gateway.resolve_onebot_quoted_message",
                wraps=resolve_quoted_message,
            ) as package_resolver,
        ):
            result = gateway.resolve_quoted_attachments(event, context=context)

        package_resolver.assert_called_once()
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["quoted_message"]["text"], "这是很久以前的原话 [图片]")
        self.assertEqual(result["quoted_message"]["actor_id"], USER_ID)
        self.assertEqual(result["quoted_message"]["actor_label"], "伙伴")
        self.assertFalse(result["quoted_message"]["actor_is_bot"])
        self.assertEqual(result["quoted_message"]["timestamp"], 1_721_485_640)
        self.assertEqual(result["quoted_message"]["conversation_kind"], "group")
        self.assertEqual(result["quoted_message"]["conversation_id"], GROUP_ID)
        self.assertEqual(result["attachments"][0]["quoted_message_id"], "quoted-1")
        self.assertEqual(result["attachments"][0]["sender_label"], "伙伴")

    def test_gateway_preserves_direct_and_quoted_video_attachments(self) -> None:
        gateway = NapCatQQGateway(wake_words=("Akane",))
        direct_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "group_id": GROUP_ID,
            "message_id": "direct-video",
            "message": [
                {"type": "text", "data": {"text": "Akane 看看这个视频"}},
                {
                    "type": "video",
                    "data": {
                        "file": "clip.mp4",
                        "url": "https://provider.invalid/private-video",
                    },
                },
            ],
        }

        context = gateway.build_message_context(direct_event)

        self.assertTrue(context.should_respond)
        self.assertEqual(len(context.attachments), 1)
        self.assertEqual(context.attachments[0]["kind"], "video")
        self.assertEqual(context.attachments[0]["mime_type"], "video/mp4")

        quoted_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "group_id": GROUP_ID,
            "message_id": "current-video-quote",
            "message": [
                {"type": "reply", "data": {"id": "quoted-video"}},
                {"type": "text", "data": {"text": "Akane 看看这个"}},
            ],
        }
        quoted_context = gateway.build_message_context(quoted_event)

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "message_id": "quoted-video",
                        "self_id": BOT_ID,
                        "message_type": "group",
                        "group_id": GROUP_ID,
                        "user_id": USER_ID,
                        "sender": {"user_id": USER_ID, "card": "伙伴"},
                        "message": [
                            {
                                "type": "video",
                                "data": {
                                    "file": "quoted.mp4",
                                    "url": "https://provider.invalid/private-quoted-video",
                                },
                            }
                        ],
                    },
                }

        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()):
            result = gateway.resolve_quoted_attachments(quoted_event, context=quoted_context)

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["attachments"][0]["kind"], "video")
        self.assertEqual(result["attachments"][0]["mime_type"], "video/mp4")
        self.assertEqual(result["attachments"][0]["quoted_message_id"], "quoted-video")

    def test_gateway_marks_quoted_bot_reply_as_assistant_self(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "message_id": "current-private-self-quote",
            "message": [
                {"type": "reply", "data": {"id": "quoted-bot-reply"}},
                {"type": "text", "data": {"text": "你这句是什么意思？"}},
            ],
        }
        context = gateway.build_message_context(event)

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "message_id": "quoted-bot-reply",
                        "self_id": BOT_ID,
                        "message_type": "private",
                        "user_id": BOT_ID,
                        "target_id": USER_ID,
                        "sender": {"user_id": BOT_ID, "nickname": "Akane群昵称"},
                        "message": [{"type": "text", "data": {"text": "我刚才分段发出的其中一句。"}}],
                    },
                }

        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()):
            result = gateway.resolve_quoted_message_evidence(event, context=context)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "resolved")
        self.assertTrue(result["quoted_message"]["actor_is_bot"])
        self.assertEqual(result["quoted_message"]["actor_label"], "Akane群昵称")
        self.assertEqual(result["quoted_message"]["text"], "我刚才分段发出的其中一句。")

    def test_gateway_private_quote_fails_closed_when_scope_cannot_be_verified(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": BOT_ID,
            "user_id": USER_ID,
            "message_id": "current-private-quote-1",
            "message": [
                {"type": "reply", "data": {"id": "quoted-private-1"}},
                {"type": "text", "data": {"text": "看看"}},
            ],
        }
        context = gateway.build_message_context(event)

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "message_id": "quoted-private-1",
                        "message_type": "private",
                        "message": [],
                    },
                }

        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()):
            result = gateway.resolve_quoted_attachments(event, context=context)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "scope_unverifiable")
        self.assertEqual(result["attachments"], [])

    def test_gateway_outbound_reply_uses_package_plan_and_real_reply_segment(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": BOT_ID,
                "user_id": USER_ID,
                "message_id": "outbound-current-1",
                "raw_message": "hello",
            }
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {"message_id": "sent-1"}}

        with (
            patch("companion_v01.qq_gateway.build_message_action", wraps=build_message_action) as package_builder,
            patch(
                "companion_v01.onebot_transport.normalize_action_response",
                wraps=normalize_action_response,
            ) as package_result_parser,
            patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()) as request,
        ):
            result = gateway.send_reply(context, "world")

        self.assertTrue(result["ok"])
        package_builder.assert_called_once()
        package_result_parser.assert_called_once()
        self.assertEqual(
            request.call_args.kwargs["json"]["message"],
            [
                {"type": "reply", "data": {"id": "outbound-current-1"}},
                {"type": "text", "data": {"text": "world"}},
            ],
        )

    def test_gateway_has_no_second_outbound_protocol_implementation(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "companion_v01" / "qq_gateway.py").read_text(encoding="utf-8")
        self.assertNotIn('"send_private_msg"', source)
        self.assertNotIn('"send_group_msg"', source)
        self.assertNotIn('"upload_private_file"', source)
        self.assertNotIn('"upload_group_file"', source)
        self.assertNotIn('"type": "record"', source)
        self.assertIn("build_message_action(", source)
        self.assertIn("build_upload_file_action(", source)


if __name__ == "__main__":
    unittest.main()
