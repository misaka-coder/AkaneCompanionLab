"""Tests for music_control_store — "让她也能" permission persistence."""

from __future__ import annotations

import sqlite3
import unittest

from companion_v01 import music_control_store


class MusicControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        music_control_store.ensure_schema(self.conn)
        self.addCleanup(self.conn.close)

    # ------------------------------------------------------------------
    # Default semantics (no rows)
    # ------------------------------------------------------------------

    def test_default_all_enabled_when_table_empty(self) -> None:
        enabled = music_control_store.get_enabled_controls(
            self.conn, profile_user_id="alice"
        )
        self.assertEqual(enabled, {"pause", "next", "prev", "recommend"})

    # ------------------------------------------------------------------
    # Single-item toggle
    # ------------------------------------------------------------------

    def test_disable_single_control(self) -> None:
        music_control_store.set_control_enabled(
            self.conn,
            profile_user_id="alice",
            control_name="next",
            enabled=False,
            now_ts=1_700_000_000,
        )
        enabled = music_control_store.get_enabled_controls(
            self.conn, profile_user_id="alice"
        )
        self.assertNotIn("next", enabled)
        self.assertIn("pause", enabled)
        self.assertIn("prev", enabled)
        self.assertIn("recommend", enabled)

    def test_re_enable_after_disable(self) -> None:
        music_control_store.set_control_enabled(
            self.conn,
            profile_user_id="alice",
            control_name="next",
            enabled=False,
            now_ts=1_700_000_000,
        )
        music_control_store.set_control_enabled(
            self.conn,
            profile_user_id="alice",
            control_name="next",
            enabled=True,
            now_ts=1_700_000_001,
        )
        enabled = music_control_store.get_enabled_controls(
            self.conn, profile_user_id="alice"
        )
        self.assertIn("next", enabled)

    # ------------------------------------------------------------------
    # Bulk update
    # ------------------------------------------------------------------

    def test_bulk_partial_update_only_affects_given_keys(self) -> None:
        music_control_store.bulk_set_controls(
            self.conn,
            profile_user_id="alice",
            controls={"next": False},
            now_ts=1_700_000_000,
        )
        enabled = music_control_store.get_enabled_controls(
            self.conn, profile_user_id="alice"
        )
        self.assertNotIn("next", enabled)
        self.assertIn("pause", enabled)
        self.assertIn("prev", enabled)
        self.assertIn("recommend", enabled)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def test_set_unknown_control_name_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            music_control_store.set_control_enabled(
                self.conn,
                profile_user_id="alice",
                control_name="wipe_disk",
                enabled=False,
                now_ts=1_700_000_000,
            )

    def test_bulk_unknown_key_silently_ignored(self) -> None:
        music_control_store.bulk_set_controls(
            self.conn,
            profile_user_id="alice",
            controls={"wipe_disk": True, "next": False},
            now_ts=1_700_000_000,
        )
        enabled = music_control_store.get_enabled_controls(
            self.conn, profile_user_id="alice"
        )
        self.assertNotIn("next", enabled)
        # "wipe_disk" should not have been written
        rows = self.conn.execute(
            "SELECT control_name FROM music_control_permissions WHERE profile_user_id = ?",
            ("alice",),
        ).fetchall()
        names = {row[0] for row in rows}
        self.assertNotIn("wipe_disk", names)

    # ------------------------------------------------------------------
    # Profile isolation
    # ------------------------------------------------------------------

    def test_profile_isolation(self) -> None:
        music_control_store.set_control_enabled(
            self.conn,
            profile_user_id="alice",
            control_name="next",
            enabled=False,
            now_ts=1_700_000_000,
        )
        bob_enabled = music_control_store.get_enabled_controls(
            self.conn, profile_user_id="bob"
        )
        self.assertIn("next", bob_enabled)


if __name__ == "__main__":
    unittest.main()
