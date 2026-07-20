from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from channelcore_onebot import normalize_inbound_event, resolve_quoted_message

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
                        "message": [{"type": "image", "data": {"file": "quoted.png"}}],
                    },
                }

        with (
            patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()),
            patch(
                "companion_v01.qq_gateway.resolve_onebot_quoted_message",
                wraps=resolve_quoted_message,
            ) as package_resolver,
        ):
            result = gateway.resolve_quoted_attachments(event, context=context)

        package_resolver.assert_called_once()
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["attachments"][0]["quoted_message_id"], "quoted-1")
        self.assertEqual(result["attachments"][0]["sender_label"], "伙伴")

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

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()):
            result = gateway.resolve_quoted_attachments(event, context=context)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "scope_unverifiable")
        self.assertEqual(result["attachments"], [])


if __name__ == "__main__":
    unittest.main()
