from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.bot_profile import (
    BotConfig,
    BotProfileError,
    BotQQChannelConfig,
    bot_config_from_instance_context,
    load_bot_host_profile,
    parse_bot_host_profile,
    resolve_bot_data_root,
)
from companion_v01.instance_profile import instance_context_from_bot_config, resolve_instance_context


def _profile_payload() -> dict:
    return {
        "schema_version": 1,
        "default_bot_id": "bot-a",
        "bots": [
            {
                "bot_id": "bot-a",
                "enabled": True,
                "display_name": "Akane A",
                "character_pack_id": "akane_v1",
                "memory_space_id": "memory-a",
                "model_profile_ref": "default",
                "capability_profile_ref": "default",
                "care_enabled": True,
                "channels": {"qq": {"enabled": True, "profile_ref": "qq.bot-a"}},
                "plugins": [{"id": "akane.finance", "enabled": False}],
            },
            {
                "bot_id": "bot-b",
                "enabled": True,
                "display_name": "Akane B",
                "memory_space_id": "memory-b",
                "care_enabled": False,
            },
            {
                "bot_id": "bot-disabled",
                "enabled": False,
                "display_name": "Disabled",
                "memory_space_id": "memory-disabled",
            },
        ],
    }


class BotProfileTests(unittest.TestCase):
    def test_deploy_example_is_a_valid_two_bot_profile(self) -> None:
        path = Path(__file__).resolve().parents[1] / "deploy" / "bots.example.toml"

        profile = load_bot_host_profile(path)

        self.assertEqual(profile.default_bot_id, "akane-personal")
        self.assertEqual([bot.bot_id for bot in profile.enabled_bots], ["akane-personal", "akane-finance"])
        self.assertFalse(profile.require("akane-personal").qq.enabled)
        self.assertTrue(profile.require("akane-finance").plugins[0].enabled)

    def test_toml_file_loads_config_only_bot_registration(self) -> None:
        text = """\
schema_version = 1
default_bot_id = "bot-a"

[[bots]]
bot_id = "bot-a"
enabled = true
display_name = "Akane A"
memory_space_id = "memory-a"
care_enabled = true

[bots.channels.qq]
enabled = false

[[bots]]
bot_id = "bot-b"
enabled = true
display_name = "Akane B"
memory_space_id = "memory-b"
care_enabled = false

[[bots.plugins]]
id = "akane.finance"
enabled = true
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bots.toml"
            path.write_text(text, encoding="utf-8")
            profile = load_bot_host_profile(path)

        self.assertEqual(profile.default_bot_id, "bot-a")
        self.assertEqual([bot.bot_id for bot in profile.enabled_bots], ["bot-a", "bot-b"])
        self.assertEqual(profile.require("bot-b").plugins[0].plugin_id, "akane.finance")

    def test_profile_parses_three_config_only_bots_and_projects_instance_adapter(self) -> None:
        profile = parse_bot_host_profile(_profile_payload())

        self.assertEqual(profile.default_bot_id, "bot-a")
        self.assertEqual([bot.bot_id for bot in profile.enabled_bots], ["bot-a", "bot-b"])
        bot_a = profile.require("bot-a")
        context = instance_context_from_bot_config(bot_a)
        self.assertEqual(context.instance_id, "bot-a")
        self.assertEqual(context.character_pack_id, "akane_v1")
        self.assertTrue(context.features.care)
        self.assertTrue(context.channels.qq.enabled)
        self.assertEqual(context.channels.qq.profile_ref, "qq.bot-a")
        self.assertEqual([(item.plugin_id, item.enabled) for item in context.plugins], [("akane.finance", False)])
        self.assertEqual(context.source, "bot_config_adapter")

    def test_profile_rejects_secrets_paths_unknown_fields_and_duplicate_roots(self) -> None:
        cases = []
        secret_payload = _profile_payload()
        secret_payload["bots"][0]["api_key"] = "secret"
        cases.append((secret_payload, "unsupported_bot_profile_field", "bots.0.api_key"))
        path_payload = _profile_payload()
        path_payload["bots"][0]["data_root"] = "C:/secret"
        cases.append((path_payload, "unsupported_bot_profile_field", "bots.0.data_root"))
        duplicate_payload = _profile_payload()
        duplicate_payload["bots"][1]["memory_space_id"] = "memory-a"
        cases.append((duplicate_payload, "duplicate_memory_space_id", "bots.1.memory_space_id"))

        for payload, reason, field in cases:
            with self.subTest(reason=reason, field=field):
                with self.assertRaises(BotProfileError) as raised:
                    parse_bot_host_profile(payload)
                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(raised.exception.field, field)
                self.assertNotIn("secret", str(raised.exception))

    def test_enabled_qq_bots_require_non_overlapping_wake_words(self) -> None:
        overlapping = _profile_payload()
        overlapping["bots"][0]["wake_words"] = ["Akane"]
        overlapping["bots"][1]["channels"] = {"qq": {"enabled": True, "profile_ref": "qq.bot-b"}}
        overlapping["bots"][1]["wake_words"] = ["Akane Finance"]

        with self.assertRaises(BotProfileError) as raised:
            parse_bot_host_profile(overlapping)

        self.assertEqual(raised.exception.reason, "overlapping_qq_wake_word")
        self.assertEqual(raised.exception.field, "bots.1.wake_words")

        non_overlapping = _profile_payload()
        non_overlapping["bots"][0]["wake_words"] = ["Akane"]
        non_overlapping["bots"][1]["channels"] = {"qq": {"enabled": True, "profile_ref": "qq.bot-b"}}
        non_overlapping["bots"][1]["wake_words"] = ["Akane218"]
        profile = parse_bot_host_profile(non_overlapping)

        self.assertEqual(profile.require("bot-a").wake_words, ("Akane",))
        self.assertEqual(profile.require("bot-b").wake_words, ("Akane218",))

    def test_default_bot_must_exist_and_be_enabled(self) -> None:
        payload = _profile_payload()
        payload["bots"][0]["enabled"] = False

        with self.assertRaises(BotProfileError) as raised:
            parse_bot_host_profile(payload)

        self.assertEqual(raised.exception.reason, "default_bot_must_be_enabled")
        self.assertEqual(raised.exception.field, "default_bot_id")

    def test_direct_bot_config_construction_cannot_bypass_safe_id_validation(self) -> None:
        with self.assertRaises(BotProfileError) as raised:
            BotConfig(
                schema_version=1,
                bot_id="../escape",
                enabled=True,
                display_name="Unsafe",
                character_pack_id="",
                memory_space_id="safe-memory",
                model_profile_ref="default",
                capability_profile_ref="default",
                care_enabled=True,
                qq=BotQQChannelConfig(),
                plugins=(),
            )

        self.assertEqual(raised.exception.reason, "invalid_safe_id")
        self.assertEqual(raised.exception.field, "bot_id")

    def test_memory_space_ids_resolve_to_distinct_host_owned_roots(self) -> None:
        profile = parse_bot_host_profile(_profile_payload())
        with tempfile.TemporaryDirectory() as temp_dir:
            host_root = Path(temp_dir)
            roots = [resolve_bot_data_root(host_root, bot) for bot in profile.enabled_bots]

        self.assertEqual(len(set(roots)), 2)
        self.assertTrue(all(root.parent.name == "bots" for root in roots))

    def test_legacy_instance_profile_is_a_thin_read_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = resolve_instance_context(data_root=Path(temp_dir), selected_instance_id="")
            bot_config = bot_config_from_instance_context(context)

        self.assertEqual(bot_config.bot_id, "local-default")
        self.assertEqual(bot_config.memory_space_id, "local-default")
        self.assertEqual(bot_config.source, "instance_adapter")
        self.assertTrue(bot_config.care_enabled)
        self.assertFalse(bot_config.qq.enabled)


if __name__ == "__main__":
    unittest.main()
