"""Host-side retention_anchor slice: oversized anchors never drop tool batches.

Regression for the production ``operation_retention_anchor_too_large`` failures.
Covers record_tool_batch behavior against the real memcore manager:
  - an oversized retention_anchor no longer aborts the whole batch;
  - every exchange in a parallel batch is recorded when one is oversized;
  - duplicate tool_call_id is rejected before any append (no partial records);
  - the next-turn prompt projection is unchanged (no anchor text leaks).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.memcore_integration.manager import MemcoreManager


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


def _manager(temp_dir: str) -> MemcoreManager:
    return MemcoreManager(
        backend="memcore",
        storage_path=Path(temp_dir) / "memcore_v01.db",
        visible_scope="conversation",
        enable_flavor=True,
        shadow_compare=False,
        llm=_FakeLLM(),
        embedding_provider=_FakeEmbeddingProvider(),
    )


def _big_anchor() -> dict:
    return {
        "operation": "read_timeline",
        "cursor": "c-" + ("9" * 9000),
        "covered_ids": ["id-%05d" % i for i in range(2000)],
    }


def _exchange(tool: str, call_id: str, ts: int, result: str, anchor: dict | None = None) -> dict:
    exchange = {
        "tool_name": tool,
        "tool_call_id": call_id,
        "tool_input": {"q": "x"},
        "result": result,
        "source": "memcore",
        "timestamp": ts,
        "result_status": "success",
    }
    if anchor is not None:
        exchange["retention_anchor"] = anchor
    return exchange


class RecordToolBatchOversizedAnchorTests(unittest.TestCase):
    def test_oversized_anchor_single_exchange_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _manager(temp_dir)
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "u-1", "content": "查一下", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                batch = manager.record_tool_batch(
                    exchanges=[
                        _exchange("open_memory", "call-1", 101, "七月确认了统一事件流", _big_anchor())
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(batch["ok"], batch)
                self.assertEqual(len(batch["exchanges"]), 1)
                stored = manager._store.get_record_by_source_id(batch["exchanges"][0]["tool_result_source_id"])
                self.assertIsNotNone(stored)
                trace = stored["trace_metadata"]
                self.assertEqual(trace["retention_anchor_status"], "oversized")
                self.assertGreater(trace["retention_anchor_bytes"], 4096)
                self.assertIn("七月确认了统一事件流", stored["semantic_text"])
            finally:
                manager.close()

    def test_parallel_batch_with_one_oversized_anchor_records_all(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _manager(temp_dir)
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "u-2", "content": "并行", "timestamp": 200},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                batch = manager.record_tool_batch(
                    exchanges=[
                        _exchange("web_search", "call-p1", 201, "search rows"),
                        _exchange("open_memory", "call-p2", 203, "card content", _big_anchor()),
                        _exchange("read_timeline", "call-p3", 205, "timeline rows"),
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(batch["ok"], batch)
                self.assertEqual(len(batch["exchanges"]), 3)
                for item in batch["exchanges"]:
                    self.assertTrue(item["tool_use_source_id"])
                    self.assertTrue(item["tool_result_source_id"])
            finally:
                manager.close()

    def test_duplicate_tool_call_id_aborts_before_any_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _manager(temp_dir)
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "u-3", "content": "重复工具", "timestamp": 300},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                count_before = int(
                    manager._store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                )
                batch = manager.record_tool_batch(
                    exchanges=[
                        _exchange("web_search", "call-same", 301, "first"),
                        _exchange("open_memory", "call-same", 303, "second"),
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertFalse(batch["ok"], batch)
                self.assertEqual(batch["status"], "failed")
                self.assertIn("duplicate_tool_call_id_in_batch", batch.get("reason", ""))
                count_after = int(
                    manager._store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                )
                self.assertEqual(count_after, count_before)
            finally:
                manager.close()

    def test_next_turn_projection_unchanged_by_oversized_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _manager(temp_dir)
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "u-4", "content": "看看", "timestamp": 400},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                evidence = "【MemCore 记忆证据】\n七月确认了统一事件流与完整工具结果。"
                batch = manager.record_tool_batch(
                    exchanges=[_exchange("open_memory", "call-4", 401, evidence, _big_anchor())],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(batch["ok"], batch)

                projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                active_turn = [
                    message["payload"]
                    for message in projection["messages"]
                    if message.get("turn_id") == opened["turn_id"]
                ]
                tool_messages = [message for message in active_turn if message.get("role") == "tool"]
                self.assertEqual(len(tool_messages), 1)
                self.assertEqual(tool_messages[0]["content"], evidence)
                projected_text = str(projection)
                self.assertNotIn("covered_ids", projected_text)
                self.assertNotIn("retention_anchor", projected_text)
            finally:
                manager.close()


    def test_execution_unknown_result_status_is_persisted_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _manager(temp_dir)
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "u-5", "content": "播放", "timestamp": 500},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                exchange = _exchange("system_media_control", "call-5", 501, "已发送指令但状态未确认")
                exchange["result_status"] = "execution_unknown"
                batch = manager.record_tool_batch(
                    exchanges=[exchange],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(batch["ok"], batch)
                stored = manager._store.get_record_by_source_id(batch["exchanges"][0]["tool_result_source_id"])
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored["trace_metadata"]["status"], "execution_unknown")
            finally:
                manager.close()

    def test_task_event_text_keeps_operation_paths_and_redacts_secrets(self) -> None:
        from companion_v01.memcore_integration.manager import MemcoreManager

        text = MemcoreManager._safe_task_event_text(
            "处理 C:\\Users\\Lenovo\\Desktop\\a.txt，api_key=supersecret123，token=xyz",
            limit=200,
        )
        self.assertIn("C:\\Users\\Lenovo\\Desktop\\a.txt", text)
        self.assertNotIn("supersecret123", text)
        self.assertNotIn("[local_path]", text)

    def test_legacy_path_projection_migration_runs_via_maintenance_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _manager(temp_dir)
            try:
                manager.begin_input_turn(
                    {"source_id": "u-6", "content": "迁移前", "timestamp": 600},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                dry = manager.run_legacy_path_projection_migration(dry_run=True)
                self.assertTrue(dry["ok"], dry)
                self.assertTrue(dry["dry_run"])
                self.assertIn("totals", dry)
                applied = manager.run_legacy_path_projection_migration(dry_run=False)
                self.assertTrue(applied["ok"], applied)
                self.assertFalse(applied["dry_run"])
                self.assertEqual(applied["failed_namespace_count"], 0)
            finally:
                manager.close()

    def test_legacy_path_migration_cli_accepts_explicit_dry_run(self) -> None:
        from scripts.migrate_legacy_path_projections import build_parser

        parser = build_parser()
        explicit = parser.parse_args(["--db", "memcore.db", "--dry-run"])
        self.assertTrue(explicit.dry_run)
        self.assertFalse(explicit.apply)
        default = parser.parse_args(["--db", "memcore.db"])
        self.assertFalse(default.apply)


if __name__ == "__main__":
    unittest.main()
