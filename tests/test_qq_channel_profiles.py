from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.qq_channel_profiles import (
    QQChannelProfileError,
    load_qq_channel_profiles,
    parse_qq_channel_profiles,
)


def _payload() -> dict:
    return {
        "schema_version": 1,
        "profiles": [
            {
                "profile_ref": "qq.bot-a",
                "bot_qq": "10000001",
                "onebot_http_url": "http://127.0.0.1:3001",
                "webhook_secret": "webhook-a",
                "onebot_access_token": "token-a",
            },
            {
                "profile_ref": "qq.bot-b",
                "bot_qq": "10000002",
                "onebot_http_url": "http://127.0.0.1:3002",
                "webhook_secret": "webhook-b",
                "onebot_access_token": "token-b",
            },
        ],
    }


class QQChannelProfileTests(unittest.TestCase):
    def test_profiles_are_selected_by_safe_ref_without_secret_repr_or_snapshot(self) -> None:
        payload = _payload()
        payload["profiles"][0]["onebot_shared_data_root"] = "/srv/akane/bots/bot-a"
        profiles = parse_qq_channel_profiles(payload)

        profile_a = profiles.get("qq.bot-a")
        self.assertIsNotNone(profile_a)
        self.assertEqual(profile_a.bot_qq, "10000001")
        self.assertEqual(profile_a.onebot_http_url, "http://127.0.0.1:3001")
        self.assertEqual(profile_a.onebot_shared_data_root, "/srv/akane/bots/bot-a")
        self.assertNotIn("webhook-a", repr(profile_a))
        self.assertNotIn("token-a", repr(profile_a))
        self.assertNotIn("/srv/akane", repr(profile_a))
        public = profiles.public_snapshot()
        self.assertEqual(public["profile_refs"], ["qq.bot-a", "qq.bot-b"])
        self.assertNotIn("webhook", str(public))
        self.assertNotIn("token-a", str(public))
        self.assertNotIn("/srv/akane", str(public))

    def test_shared_data_root_must_be_absolute(self) -> None:
        payload = _payload()
        payload["profiles"][0]["onebot_shared_data_root"] = "../napcat"

        with self.assertRaises(QQChannelProfileError) as raised:
            parse_qq_channel_profiles(payload)

        self.assertEqual(raised.exception.reason, "qq_shared_data_root_invalid")
        self.assertEqual(
            raised.exception.field_name,
            "profiles.0.onebot_shared_data_root",
        )

    def test_duplicate_bot_account_and_unknown_secret_field_are_rejected_safely(self) -> None:
        duplicate = _payload()
        duplicate["profiles"][1]["bot_qq"] = "10000001"
        unknown = _payload()
        unknown["profiles"][0]["api_key"] = "must-not-leak"

        cases = (
            (duplicate, "duplicate_qq_bot_id", "profiles.1.bot_qq"),
            (unknown, "unsupported_qq_channel_profile_field", "profiles.0.api_key"),
        )
        for payload, reason, field_name in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(QQChannelProfileError) as raised:
                    parse_qq_channel_profiles(payload)
                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(raised.exception.field_name, field_name)
                self.assertNotIn("must-not-leak", str(raised.exception))

    def test_deploy_example_is_valid_and_missing_runtime_secret_file_can_be_empty(self) -> None:
        example_path = Path(__file__).resolve().parents[1] / "deploy" / "qq_profiles.example.toml"
        profiles = load_qq_channel_profiles(example_path)
        self.assertEqual(len(profiles.profiles), 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            missing = load_qq_channel_profiles(Path(temp_dir) / "missing.toml", missing_ok=True)
        self.assertEqual(missing.profiles, ())


if __name__ == "__main__":
    unittest.main()
