"""Settings precedence, process readers and isolated explicit Bot overrides."""
from __future__ import annotations

import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from companion_v01.bot_profile import BotConfig, BotQQChannelConfig
from companion_v01.bot_runtime_components.factory import _load_runtime_settings
from companion_v01.model_service_config import ModelServiceConfigStore, ModelServiceSettings
from companion_v01.settings_overrides import SettingsOverrideStore, set_override


class BotSettingsAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = SimpleNamespace(
            TOOL_ROUND_HARD_LIMIT=11, CHAT_MODEL_NAME="environment-model",
            OWNER_PRIVATE_SESSION_SHARED=False, PROMPT_CACHE_NAMESPACE="settings-test",
            DATA_DIR="process-data", DATA_ROOT="process-data",
        )
        self.bot = BotConfig(
            schema_version=1, bot_id="bot-a", enabled=True,
            display_name="bot-a", character_pack_id="reimu", memory_space_id="bot-a",
            model_profile_ref="default", capability_profile_ref="default", care_enabled=True,
            qq=BotQQChannelConfig(), plugins=(),
        )

    def load(self, name="first", *, explicit=False, bot=None, settings_overrides=None):
        root = self.root / name
        effective_bot = bot or self.bot
        return _load_runtime_settings(
            config_module=self.base, logger=logging.getLogger("akane.settings-test"),
            runtime_layout=SimpleNamespace(config_dir=root / "config", data_root=root),
            bot_config=effective_bot if explicit else None, effective_bot_config=effective_bot,
            settings_overrides=settings_overrides,
        )

    def store(self, name="first"):
        return SettingsOverrideStore(self.root / name / "config" / "settings_overrides.json")

    def test_explicit_bot_uses_its_saved_settings_and_data_root_without_changing_base(self):
        self.store().save({"TOOL_ROUND_HARD_LIMIT": 5})
        before = vars(self.base).copy()
        runtime_config, _settings, _model_store, _override_store = self.load(explicit=True)

        self.assertEqual(runtime_config.TOOL_ROUND_HARD_LIMIT, 5)
        self.assertEqual(runtime_config.DATA_ROOT, str(self.root / "first"))
        self.assertEqual(runtime_config.DATA_DIR, str(self.root / "first"))
        self.assertEqual(vars(self.base), before)

    def test_two_explicit_bots_and_later_edits_do_not_share_values(self):
        self.store("first").save({"TOOL_ROUND_HARD_LIMIT": 3})
        self.store("second").save({"TOOL_ROUND_HARD_LIMIT": 7})
        first, first_settings, *_ = self.load("first", explicit=True)
        second, second_settings, *_ = self.load(
            "second", explicit=True,
            bot=replace(self.bot, bot_id="bot-b", display_name="bot-b", memory_space_id="bot-b"),
        )

        self.assertEqual(first.TOOL_ROUND_HARD_LIMIT, 3)
        self.assertEqual(second.TOOL_ROUND_HARD_LIMIT, 7)
        self.assertEqual(first_settings.prompt_cache_namespace, "settings-test:bot:bot-a")
        self.assertEqual(second_settings.prompt_cache_namespace, "settings-test:bot:bot-b")
        set_override(first, self.store("first"), key="TOOL_ROUND_HARD_LIMIT", raw_value=4)
        self.assertEqual(first.TOOL_ROUND_HARD_LIMIT, 4)
        self.assertEqual(second.TOOL_ROUND_HARD_LIMIT, 7)
        self.assertEqual(self.base.TOOL_ROUND_HARD_LIMIT, 11)

    def test_saved_model_and_assembly_override_precedence_preserve_cache_namespace(self):
        ModelServiceConfigStore(self.root / "first" / "config" / "model_service.json").save(
            ModelServiceSettings(
                provider_id="ollama", protocol="ollama", base_url="http://localhost:11434",
                api_key="", chat_model="saved-model",
            )
        )
        config_view, settings, *_ = self.load(explicit=True)
        self.assertEqual(settings.chat_model_name, "saved-model")
        self.assertEqual(config_view.CHAT_MODEL_NAME, "environment-model")
        self.assertEqual(self.base.CHAT_MODEL_NAME, "environment-model")
        self.assertEqual(settings.prompt_cache_namespace, "settings-test:bot:bot-a")

        _, overridden, *_ = self.load(explicit=True, settings_overrides={"chat_model_name": "assembly-model"})
        self.assertEqual(overridden.chat_model_name, "assembly-model")
        _, implicit, *_ = self.load()
        self.assertEqual(implicit.prompt_cache_namespace, "settings-test")

    def test_restart_setting_stays_pending_until_a_new_configuration_is_built(self):
        view, *_ = self.load(explicit=True)
        set_override(view, self.store(), key="OWNER_PRIVATE_SESSION_SHARED", raw_value=True)
        self.assertFalse(view.OWNER_PRIVATE_SESSION_SHARED)

        rebuilt, *_ = self.load(explicit=True)
        self.assertTrue(rebuilt.OWNER_PRIVATE_SESSION_SHARED)
        self.assertFalse(view.OWNER_PRIVATE_SESSION_SHARED)
        self.assertFalse(self.base.OWNER_PRIVATE_SESSION_SHARED)

    def test_implicit_default_replays_saved_values_for_existing_process_readers(self):
        self.store().save({"TOOL_ROUND_HARD_LIMIT": 5, "OWNER_PRIVATE_SESSION_SHARED": True})
        view, settings, *_ = self.load()
        self.assertIs(view, self.base)
        self.assertEqual(self.base.TOOL_ROUND_HARD_LIMIT, 5)
        self.assertTrue(self.base.OWNER_PRIVATE_SESSION_SHARED)
        self.assertEqual(settings.prompt_cache_namespace, "settings-test")

    def test_corrupt_saved_files_keep_base_defaults_and_report_both_failures(self):
        directory = self.root / "first" / "config"
        directory.mkdir(parents=True)
        for filename in ("settings_overrides.json", "model_service.json"):
            (directory / filename).write_text("{bad-json", encoding="utf-8")
        with self.assertLogs("akane.settings-test", level="WARNING") as logged:
            view, settings, *_ = self.load()
        self.assertEqual(len(logged.output), 2)
        self.assertEqual(view.TOOL_ROUND_HARD_LIMIT, 11)
        self.assertEqual(settings.chat_model_name, "environment-model")

    def test_invalid_saved_fields_do_not_discard_valid_fields_or_override_secrets(self):
        self.store().save({
            "TOOL_ROUND_HARD_LIMIT": "invalid", "OWNER_PRIVATE_SESSION_SHARED": True,
            "TEXT_API_KEY": "must-not-be-applied",
        })
        with self.assertLogs("akane.settings-test", level="WARNING") as logged:
            view, *_ = self.load(explicit=True)
        self.assertEqual(view.TOOL_ROUND_HARD_LIMIT, 11)
        self.assertTrue(view.OWNER_PRIVATE_SESSION_SHARED)
        self.assertFalse(hasattr(view, "TEXT_API_KEY"))
        self.assertNotIn("must-not-be-applied", " ".join(logged.output))


if __name__ == "__main__":
    unittest.main()
