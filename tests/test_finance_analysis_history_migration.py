from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

from scripts.migrate_finance_analysis_history import (
    _plugin_event_source_id,
    build_timeline_facade,
    create_backups,
    load_delivered_history,
    migrate_delivered_history,
)


class _FakeTimeline:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def inspect_turn_source(self, source_id: str, **scope: Any) -> dict[str, Any]:
        del scope
        record = self.records.get(source_id)
        return {
            "ok": True,
            "status": "found" if record else "missing",
            "exists": bool(record),
            "source_id": source_id,
        }

    def record_user_turn(self, record: dict[str, Any], **scope: Any) -> dict[str, Any]:
        return self._record("user", record, scope)

    def record_assistant_turn(self, record: dict[str, Any], **scope: Any) -> dict[str, Any]:
        return self._record("assistant", record, scope)

    def _record(self, role: str, record: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
        source_id = str(record["source_id"])
        self.records[source_id] = {**record, "role": role, "scope": dict(scope)}
        self.writes.append((role, dict(record)))
        return {"ok": True, "status": "recorded", "source_id": source_id}


class FinanceAnalysisHistoryMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp.name) / "finance_state.sqlite3"
        self._create_source_db()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _create_source_db(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE subscriptions(
                    subscription_id INTEGER PRIMARY KEY,
                    recipient_id TEXT NOT NULL,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL
                );
                CREATE TABLE delivery_outbox(
                    subscription_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE analysis_history(
                    history_id INTEGER PRIMARY KEY,
                    subscription_id INTEGER NOT NULL,
                    recipient_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    published_at INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    delivered_at INTEGER NOT NULL
                );
                """
            )
            connection.execute("INSERT INTO subscriptions VALUES (1, 'recipient', 'profile', 'session', 'akane_v1')")
            valid_payload = json.dumps(
                {
                    "event_id": "event-1",
                    "published_at": 100,
                    "published_at_iso": "2026-07-17T12:00:00+08:00",
                    "title": "market event",
                    "summary": "trusted summary",
                    "source": "trusted source",
                    "url": "https://example.test/event-1",
                },
                ensure_ascii=False,
            )
            connection.execute(
                "INSERT INTO delivery_outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, "event-1", "recipient", "delivered analysis", "finance-news:stable-1", "delivered", valid_payload),
            )
            connection.execute(
                "INSERT INTO analysis_history VALUES (1, 1, 'recipient', 'event-1', 100, 'delivered analysis', 120)"
            )
            connection.execute(
                "INSERT INTO delivery_outbox VALUES (1, 'event-2', 'recipient', 'pending analysis', 'finance-news:stable-2', 'pending', '{}')"
            )
            connection.execute(
                "INSERT INTO analysis_history VALUES (2, 1, 'recipient', 'event-2', 200, 'pending analysis', 220)"
            )
            connection.execute(
                "INSERT INTO delivery_outbox VALUES (1, 'event-3', 'recipient', 'bad payload analysis', 'finance-news:stable-3', 'delivered', '{}')"
            )
            connection.execute(
                "INSERT INTO analysis_history VALUES (3, 1, 'recipient', 'event-3', 300, 'bad payload analysis', 320)"
            )
            connection.commit()

    def test_loads_only_trusted_delivered_rows_with_structured_skip_reasons(self) -> None:
        entries, report = load_delivered_history(self.db_path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(report["scanned"], 3)
        self.assertEqual(report["eligible"], 1)
        self.assertEqual(report["skip_reasons"], {"event_identity_mismatch": 1, "not_delivered": 1})
        entry = entries[0]
        self.assertEqual(entry.published_at, 100)
        self.assertEqual(entry.delivered_at, 120)

    def test_migration_writes_event_then_historical_analysis_and_is_idempotent(self) -> None:
        entries, _ = load_delivered_history(self.db_path)
        timeline = _FakeTimeline()

        first = migrate_delivered_history(entries, timeline=timeline)
        second = migrate_delivered_history(entries, timeline=timeline)

        self.assertEqual(first["pairs_completed"], 1)
        self.assertEqual(first["turns_written"], 2)
        self.assertEqual([role for role, _ in timeline.writes], ["user", "assistant"])
        self.assertEqual(second["already_complete"], 1)
        self.assertEqual(second["turns_written"], 0)
        user_record = timeline.writes[0][1]
        assistant_record = timeline.writes[1][1]
        self.assertEqual(user_record["timestamp"], 100)
        self.assertEqual(assistant_record["timestamp"], 120)
        self.assertIn("外部财经快讯，不是用户发言", user_record["content"])
        self.assertIn("仅代表当时判断，不是当前事实更新", assistant_record["content"])
        self.assertTrue(user_record["source_id"].startswith("plugin-event:"))
        self.assertTrue(assistant_record["source_id"].startswith("finance-history-analysis:"))

    def test_event_source_matches_live_plugin_idempotency_contract(self) -> None:
        from companion_v01.engine import AkaneMemoryEngine

        entries, _ = load_delivered_history(self.db_path)
        entry = entries[0]
        expected = AkaneMemoryEngine._pop_user_memory_source_id(
            {"memory_idempotency_key": entry.idempotency_key},
            profile_user_id=entry.profile_user_id,
            session_id=entry.session_id,
            character_pack_id=entry.character_pack_id,
        )

        self.assertEqual(_plugin_event_source_id(entry), expected)

    def test_real_memcore_facade_and_backups_complete_idempotently(self) -> None:
        entries, _ = load_delivered_history(self.db_path)
        memcore_path = Path(self._temp.name) / "memcore_v01.db"
        with closing(sqlite3.connect(memcore_path)):
            pass
        backup_dir = Path(self._temp.name) / "backups"

        backup_report = create_backups(self.db_path, memcore_path, backup_dir)
        manager = build_timeline_facade(memcore_path)
        try:
            first = migrate_delivered_history(entries, timeline=manager)
            second = migrate_delivered_history(entries, timeline=manager)
        finally:
            manager.close()

        self.assertEqual(backup_report, {"source": True, "memcore": True})
        self.assertEqual(len(list(backup_dir.glob("*.sqlite3"))), 2)
        self.assertEqual(first["turns_written"], 2)
        self.assertEqual(second["already_complete"], 1)
        with closing(sqlite3.connect(memcore_path)) as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
