from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.qq_gateway import NapCatQQGateway


BOT_ID = "10000001"
USER_ID = "20000001"


def _event(*, message_id: str, self_id: str = BOT_ID, timestamp: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "post_type": "message",
        "message_type": "private",
        "self_id": self_id,
        "user_id": USER_ID,
        "message_id": message_id,
        "raw_message": "在吗",
    }
    if timestamp is not None:
        payload["time"] = timestamp
    return payload


class QQChannelcoreAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        bot_patcher = patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", BOT_ID)
        bot_patcher.start()
        self.addCleanup(bot_patcher.stop)

    def test_wrong_self_id_does_not_consume_the_same_replay_key(self) -> None:
        gateway = NapCatQQGateway(
            channel_config=QQChannelRuntimeConfig(
                enabled=True,
                profile_ref="test-bot",
                bot_id=BOT_ID,
                onebot_http_url="http://127.0.0.1:3001",
                webhook_secret="secret",
                onebot_access_token="token",
                require_webhook_auth=True,
                require_self_id=True,
            )
        )

        wrong = gateway.build_message_context(_event(message_id="identity-1", self_id="99999"))
        valid = gateway.build_message_context(_event(message_id="identity-1"))

        self.assertFalse(wrong.should_respond)
        self.assertEqual(wrong.reason, "qq_self_id_mismatch")
        self.assertTrue(valid.should_respond)

    def test_runtime_stale_settings_are_read_on_each_admission(self) -> None:
        gateway = NapCatQQGateway()
        old_timestamp = int(time.time()) - 3600

        with patch("companion_v01.qq_gateway.config.QQ_ALLOW_STALE_EVENTS", False):
            rejected = gateway.build_message_context(_event(message_id="runtime-stale-1", timestamp=old_timestamp))
        with patch("companion_v01.qq_gateway.config.QQ_ALLOW_STALE_EVENTS", True):
            allowed = gateway.build_message_context(_event(message_id="runtime-stale-2", timestamp=old_timestamp))

        self.assertEqual(rejected.reason, "stale_event")
        self.assertTrue(allowed.should_respond)

    def test_stale_event_does_not_claim_replay_key(self) -> None:
        gateway = NapCatQQGateway()
        old_timestamp = int(time.time()) - 3600

        stale = gateway.build_message_context(_event(message_id="stale-replay-1", timestamp=old_timestamp))
        fresh = gateway.build_message_context(_event(message_id="stale-replay-1", timestamp=int(time.time())))

        self.assertEqual(stale.reason, "stale_event")
        self.assertTrue(fresh.should_respond)

    def test_concurrent_duplicate_events_only_one_reaches_context(self) -> None:
        gateway = NapCatQQGateway()
        event = _event(message_id="concurrent-1")

        with ThreadPoolExecutor(max_workers=16) as executor:
            contexts = list(executor.map(lambda _index: gateway.build_message_context(dict(event)), range(16)))

        self.assertEqual(sum(context.should_respond for context in contexts), 1)
        self.assertEqual(sum(context.reason == "duplicate_event" for context in contexts), 15)


if __name__ == "__main__":
    unittest.main()
