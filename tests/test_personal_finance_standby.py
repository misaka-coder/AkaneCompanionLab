from __future__ import annotations

import importlib.util
import tomllib
import unittest
from pathlib import Path
from types import ModuleType


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "recovery"
    / "akane-personal-finance-standby.py"
)


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("akane_personal_finance_standby", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("standby_helper_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = _load_helper()

MANIFEST_WITHOUT_PERSONAL_PLUGIN = """\
schema_version = 1
default_bot_id = "personal"

[[bots]]
bot_id = "personal"
enabled = true
display_name = "Akane"

[bots.channels.qq]
enabled = true
profile_ref = "qq.personal"

[[bots]]
bot_id = "finance"
enabled = true
display_name = "Finance"

[bots.channels.qq]
enabled = true
profile_ref = "qq.finance"

[[bots.plugins]]
id = "akane.finance"
enabled = true
"""

MANIFEST_WITH_DISABLED_PERSONAL_PLUGIN = """\
schema_version = 1
default_bot_id = "personal"

[[bots]]
bot_id = "personal"
enabled = true
display_name = "Akane"

[bots.channels.qq]
enabled = true
profile_ref = "qq.personal"

[[bots.plugins]]
id = "akane.finance"
enabled = false # emergency standby

[[bots]]
bot_id = "finance"
enabled = true
display_name = "Finance"

[[bots.plugins]]
id = "akane.finance"
enabled = true
"""


class PersonalFinanceStandbyTests(unittest.TestCase):
    def test_enable_inserts_only_personal_finance_plugin(self) -> None:
        updated = HELPER.update_manifest_text(
            MANIFEST_WITHOUT_PERSONAL_PLUGIN,
            enabled=True,
        )
        parsed = tomllib.loads(updated)

        self.assertTrue(HELPER.finance_standby_enabled(updated))
        self.assertEqual(parsed["bots"][0]["plugins"], [{"id": "akane.finance", "enabled": True}])
        self.assertEqual(parsed["bots"][1]["plugins"], [{"id": "akane.finance", "enabled": True}])
        self.assertEqual(parsed["bots"][0]["channels"]["qq"]["profile_ref"], "qq.personal")

    def test_disable_changes_personal_plugin_without_touching_finance_bot(self) -> None:
        enabled = HELPER.update_manifest_text(
            MANIFEST_WITH_DISABLED_PERSONAL_PLUGIN,
            enabled=True,
        )
        disabled = HELPER.update_manifest_text(enabled, enabled=False)
        parsed = tomllib.loads(disabled)

        self.assertFalse(HELPER.finance_standby_enabled(disabled))
        self.assertFalse(parsed["bots"][0]["plugins"][0]["enabled"])
        self.assertTrue(parsed["bots"][1]["plugins"][0]["enabled"])
        self.assertIn("enabled = false # emergency standby", disabled)

    def test_idempotent_toggle_keeps_manifest_byte_for_byte(self) -> None:
        self.assertEqual(
            HELPER.update_manifest_text(
                MANIFEST_WITH_DISABLED_PERSONAL_PLUGIN,
                enabled=False,
            ),
            MANIFEST_WITH_DISABLED_PERSONAL_PLUGIN,
        )


if __name__ == "__main__":
    unittest.main()
