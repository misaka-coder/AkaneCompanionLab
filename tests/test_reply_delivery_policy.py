from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.qq_commands import resolve_qq_command
from companion_v01.qq_gateway_runtime.preferences import QQSessionPreferences
from companion_v01.qq_reply_delivery import _resolve_delivery_medium, _streaming_allows_text


class ReplyDeliveryPolicyTests(unittest.TestCase):
    def preferences(self, path: Path) -> QQSessionPreferences:
        preferences = QQSessionPreferences(
            state_path=path,
            default_character_pack_id=lambda: "cecilia",
            default_reply_mode=lambda: "auto",
        )
        preferences._load_persisted_state()
        return preferences

    def test_modes_survive_restart_and_reset_without_affecting_other_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            preferences = self.preferences(path)
            self.assertTrue(preferences.set_session_reply_mode("qq_pri_123", "voice"))
            self.assertTrue(preferences.set_session_reply_mode("qq_group_shared_87", "both"))
            restored = self.preferences(path)
            self.assertEqual(restored.resolve_reply_mode("qq_pri_123"), "voice")
            self.assertEqual(restored.resolve_reply_mode("qq_group_shared_87"), "both")
            self.assertTrue(restored.clear_session_reply_mode_override("qq_pri_123"))
            restored = self.preferences(path)
            self.assertEqual(restored.resolve_reply_mode("qq_pri_123"), "text")
            self.assertEqual(restored.resolve_reply_mode("qq_group_shared_87"), "both")

    def test_legacy_auto_normalizes_when_existing_state_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text(json.dumps({"reply_mode_overrides": {
                "qq_pri_123": "auto", "qq_pri_456": "voice", "qq_pri_789": "invalid",
            }}), encoding="utf-8")
            preferences = self.preferences(path)
            self.assertEqual(preferences.reply_mode_overrides,
                             {"qq_pri_123": "text", "qq_pri_456": "voice"})
            self.assertEqual(preferences.resolve_reply_mode("qq_pri_999"), "text")

    def test_save_failure_preserves_active_and_persisted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            preferences = self.preferences(path)
            self.assertTrue(preferences.set_session_reply_mode("qq_pri_123", "voice"))
            with patch.object(Path, "replace", side_effect=OSError("save denied")):
                self.assertFalse(preferences.set_session_reply_mode("qq_pri_123", "both"))
                self.assertFalse(preferences.clear_session_reply_mode_override("qq_pri_123"))
            self.assertEqual(preferences.resolve_reply_mode("qq_pri_123"), "voice")
            self.assertEqual(self.preferences(path).resolve_reply_mode("qq_pri_123"), "voice")

    def test_host_decides_before_any_model_output(self):
        gateway = SimpleNamespace(resolve_reply_mode=lambda _session: "both")
        for mode, expected in (("text", "text"), ("voice", "voice"), ("auto", "text"), ("", "both")):
            with self.subTest(mode=mode):
                plan = _resolve_delivery_medium(
                    context=SimpleNamespace(session_id="qq_pri_123", reply_mode=mode),
                    qq_gateway=gateway,
                )
                self.assertEqual(plan["medium"], expected)
                self.assertEqual(_streaming_allows_text(expected), expected != "voice")

    def test_auto_is_no_longer_a_command(self):
        resolution = resolve_qq_command("/回复 自动")
        self.assertIsNotNone(resolution)
        self.assertTrue(resolution.error)
        self.assertFalse(resolve_qq_command("/回复 默认").error)


if __name__ == "__main__":
    unittest.main()
