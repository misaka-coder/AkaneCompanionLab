"""Latest-turn lookups preserve scope and ordering without scanning old turns."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from companion_v01.store import MemoryStore


class EvalTurnIndexCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = MemoryStore(self.root)

    def append(self, trace_id, timestamp=100, *, owner="owner", session="session", character="cecilia"):
        with patch("companion_v01.store.eval_turns.time.time", return_value=timestamp):
            self.store.append_eval_turn(
                trace_id=trace_id, profile_user_id=owner, session_id=session,
                character_pack_id=character, user_message="input",
                router_json={"route": "direct_answer"}, verifier_json={"status": "ok"},
                final_json={"speech": "台词", "character": {"outfit": trace_id}},
            )

    def latest(self, **kwargs):
        return self.store.get_latest_eval_turn_for_session(
            profile_user_id="owner", session_id="session", **kwargs,
        )

    def test_timestamp_then_insertion_order_and_replacement_survive_reopen(self):
        self.append("first", 200)
        self.append("second", 200)
        self.append("later_insert_with_older_time", 199)
        self.assertEqual(self.latest()["trace_id"], "second")
        self.append("first", 200)
        expected = self.latest()
        self.assertEqual(expected["trace_id"], "first")
        self.assertEqual(expected["final_json"]["character"]["outfit"], "first")
        self.store = MemoryStore(self.root)
        self.assertEqual(self.latest(), expected)
        self.assertEqual(self.store.get_latest_eval_turn("session"), expected)

    def test_owner_session_and_optional_character_keep_their_original_scope(self):
        self.append("cecilia", 100)
        self.append("other_character", 101, character="another")
        self.append("unscoped_character", 102, character="")
        self.append("different_owner", 103, owner="other")
        self.append("different_session", 104, session="other_session")
        self.assertEqual(self.latest()["trace_id"], "unscoped_character")
        self.assertEqual(self.latest(character_pack_id="")["trace_id"], "unscoped_character")
        self.assertEqual(self.latest(character_pack_id="../invalid")["trace_id"], "unscoped_character")
        self.assertEqual(self.latest(character_pack_id=" cecilia ")["trace_id"], "cecilia")
        self.assertIsNone(self.latest(character_pack_id="missing"))
        self.assertEqual(self.store.get_latest_eval_turn("session")["trace_id"], "different_owner")
        self.assertEqual(self.store.get_latest_eval_turn("session", character_pack_id="")["trace_id"], "unscoped_character")
        self.assertIsNone(self.store.get_latest_eval_turn("missing_session"))
        self.assertIsNone(self.store.get_latest_eval_turn_for_session(
            profile_user_id="missing_owner", session_id="session"))

    def test_latest_queries_do_bounded_work_among_thousands_of_unrelated_turns(self):
        with self.store._connect() as conn:
            conn.executemany(
                "INSERT INTO eval_turns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(f"old-{i}", i, f"unrelated-{i}", "other", "cecilia", "input", "{}", "{}", "{}")
                 for i in range(8000)],
            )
        self.append("current", 100)
        connect = self.store._connect
        steps = []

        @contextmanager
        def measured():
            with connect() as conn:
                conn.set_progress_handler(lambda: steps.append(1) or 0, 100)
                yield conn

        for scoped in (False, True):
            for character in (None, "cecilia"):
                with self.subTest(scoped=scoped, character=character):
                    steps.clear()
                    with patch.object(self.store, "_connect", measured):
                        latest = (self.latest(character_pack_id=character) if scoped else
                                  self.store.get_latest_eval_turn("session", character_pack_id=character))
                    self.assertEqual(latest["trace_id"], "current")
                    # A scan/sort over 8000 records takes tens of thousands of
                    # VM instructions. This bound is independent of wall time.
                    self.assertLess(len(steps), 10)

    def test_failed_replacement_preserves_the_committed_latest_turn(self):
        self.append("first", 100)
        self.append("second", 100)
        expected = self.latest()
        with self.store._connect() as conn:
            conn.executescript("""
                CREATE TRIGGER reject_eval_write BEFORE INSERT ON eval_turns
                BEGIN SELECT RAISE(ABORT, 'test write failure'); END;
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test write failure"):
            self.append("first", 101)
        self.store = MemoryStore(self.root)
        self.assertEqual(self.latest(), expected)
        with self.store._connect() as conn:
            conn.execute("DROP TRIGGER reject_eval_write")
        self.append("first", 101)
        self.assertEqual(self.latest()["trace_id"], "first")

    def test_legacy_records_remain_readable_after_idempotent_index_creation(self):
        with self.store._connect() as conn:
            conn.executescript("""
                DROP TABLE eval_turns;
                CREATE TABLE eval_turns (
                    trace_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
                    session_id TEXT NOT NULL, profile_user_id TEXT NOT NULL,
                    user_message TEXT NOT NULL, router_json TEXT NOT NULL,
                    verifier_json TEXT NOT NULL, final_json TEXT NOT NULL
                );
                INSERT INTO eval_turns VALUES (
                    'legacy', 100, 'session', 'owner', 'input', '{}', '{}', '{"speech":"old"}'
                );
            """)
        self.store = MemoryStore(self.root)
        expected = self.latest(character_pack_id="")
        self.assertEqual(expected["trace_id"], "legacy")
        self.assertEqual(expected["character_pack_id"], "")
        self.store = MemoryStore(self.root)
        self.assertEqual(self.latest(character_pack_id=""), expected)
        with self.store._connect() as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(eval_turns)")}
        self.assertTrue({"idx_eval_session_time", "idx_eval_profile_session_time"} <= indexes)


if __name__ == "__main__":
    unittest.main()
