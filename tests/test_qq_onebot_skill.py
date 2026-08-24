from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[1]
_SKILL = _ROOT / "skills" / "qq-onebot-actions" / "SKILL.md"
_LEGACY_SCRIPT = _ROOT / "skills" / "qq-onebot-actions" / "scripts" / "onebot_call.py"


class QQOneBotSkillTests(unittest.TestCase):
    def test_skill_uses_the_native_action_as_its_only_execution_authority(self) -> None:
        source = _SKILL.read_text(encoding="utf-8")

        self.assertIn("`onebot_action` 是唯一执行入口", source)
        self.assertIn('"action":"capabilities"', source)
        self.assertIn("send_group_forward_msg", source)
        self.assertIn("delete_msg", source)
        self.assertNotIn("onebot_call.py", source)
        self.assertNotIn("python3", source)

    def test_legacy_token_scanning_shell_path_is_removed(self) -> None:
        self.assertFalse(_LEGACY_SCRIPT.exists())


if __name__ == "__main__":
    unittest.main()
