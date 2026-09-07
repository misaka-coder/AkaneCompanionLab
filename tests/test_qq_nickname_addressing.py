from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.bot_profile import bot_config_from_instance_context
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.qq_gateway import NapCatQQGateway


class QQNicknameAddressingTests(unittest.TestCase):
    def gateway(self, words=()):
        gateway = NapCatQQGateway(
            wake_words=words,
            channel_config=QQChannelRuntimeConfig(
                enabled=True,
                profile_ref="test",
                bot_id="10001",
                onebot_http_url="http://127.0.0.1:1",
                webhook_secret="",
                onebot_access_token="",
                require_webhook_auth=False,
                require_self_id=True,
            ),
        )
        self.login = SimpleNamespace(ok=True, code="ok", data={"user_id": 10001, "nickname": "塞西莉亚"})
        self.transport = self.enterContext(patch.object(gateway._onebot_transport, "call", return_value=self.login))
        return gateway

    def event(self, text, *, targets=(), message_id="1"):
        return {
            "post_type": "message",
            "message_type": "group",
            "self_id": "10001",
            "user_id": "20002",
            "group_id": "30003",
            "message_id": message_id,
            "sender": {"nickname": "用户"},
            "message": [{"type": "at", "data": {"qq": target, "name": "Akane"}} for target in targets]
            + [{"type": "text", "data": {"text": text}}],
        }

    def test_legacy_adapter_does_not_supply_fixed_wake_word(self):
        self.assertEqual(bot_config_from_instance_context(SimpleNamespace(instance_id="test")).wake_words, ())

    def test_automatic_nickname_wakes_but_product_name_does_not(self):
        gateway = self.gateway()
        self.assertEqual(gateway.build_message_context(self.event("塞西莉亚，在吗")).reason, "group_wake_word")
        self.assertFalse(gateway.build_message_context(self.event("Akane 在吗", message_id="2")).should_respond)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(gateway._strip_wake_word_command_prefix("塞西莉亚，当前角色"), "当前角色")

    def test_other_mention_is_passive_and_self_projection_uses_verified_nickname(self):
        gateway = self.gateway()
        other = gateway.build_message_context(self.event("塞西莉亚 在吗", targets=("40004",)))
        self.assertFalse(other.should_respond)
        self.assertEqual(other.to_turn_payload()["message_addressing"]["primary_target"]["actor_id"], "qq:40004")
        own = gateway.build_message_context(self.event("你好", targets=("10001", "40004"), message_id="2"))
        self.assertEqual(own.reason, "group_mention")
        self.assertIn("@塞西莉亚", own.to_turn_payload()["memory_message"])
        self.assertTrue(own.to_turn_payload()["message_addressing"]["explicit_assistant_mention"])

    def test_failed_lookup_backs_off_and_keeps_explicit_mentions(self):
        gateway = self.gateway()
        self.transport.return_value = SimpleNamespace(ok=False, code="timeout", data={})
        self.assertFalse(gateway.build_message_context(self.event("Akane 在吗")).should_respond)
        own = gateway.build_message_context(self.event("你好", targets=("10001",), message_id="2"))
        self.assertTrue(own.should_respond)
        self.assertIn("@助手", own.to_turn_payload()["memory_message"])
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(gateway._bot_nickname_status, "timeout")

    def test_nickname_refresh_and_account_mismatch_do_not_keep_stale_alias(self):
        gateway = self.gateway()
        self.assertEqual(gateway._effective_wake_words(), ("塞西莉亚",))
        self.login.data["nickname"] = "新昵称"
        gateway._bot_nickname_refresh_at = 0
        self.assertEqual(gateway._effective_wake_words(), ("新昵称",))
        self.assertFalse(gateway.message_mentions_wake_word("塞西莉亚 在吗"))
        self.login.data["user_id"] = 40004
        gateway._bot_nickname_refresh_at = 0
        self.assertEqual(gateway._effective_wake_words(), ())
        self.assertEqual(gateway._bot_nickname_status, "account_identity_mismatch")

    def test_configured_aliases_override_automatic_nickname(self):
        gateway = self.gateway(("小塞",))
        self.assertTrue(gateway.message_mentions_wake_word("小塞 在吗"))
        self.assertFalse(gateway.message_mentions_wake_word("塞西莉亚 在吗"))
        self.transport.assert_not_called()

    def test_invalid_nickname_never_becomes_a_wake_word(self):
        for nickname in ("", "x" * 33, "bad\nname"):
            gateway = self.gateway()
            self.login.data["nickname"] = nickname
            self.assertEqual(gateway._effective_wake_words(), ())
            self.assertEqual(gateway._bot_nickname_status, "invalid_login_nickname")

    def test_self_check_reports_real_wake_mode_without_a_second_login_request(self):
        gateway = self.gateway()
        self.transport.side_effect = [self.login, SimpleNamespace(ok=True, data={"online": True})]
        result = gateway.self_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["wake_word_mode"], "qq_nickname")
        self.assertEqual(result["wake_words"], ["塞西莉亚"])
        self.assertEqual(result["wake_word_status"], "resolved")
        self.assertEqual(self.transport.call_count, 2)
