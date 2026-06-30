from __future__ import annotations

import builtins
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from companion_v01.memcore_integration.manager import MemcoreManager, normalize_memory_backend
from companion_v01.retrieval_types import RetrievalPipelineResult
from companion_v01 import retrieval_engine
from companion_v01.tool_runtime import ToolExecutionContext


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


def _blocking_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "memcore":
        raise ModuleNotFoundError("blocked memcore import")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


_REAL_IMPORT = builtins.__import__


class MemcoreIntegrationTests(unittest.TestCase):
    def test_normalize_memory_backend(self) -> None:
        self.assertEqual(normalize_memory_backend("legacy"), "legacy")
        self.assertEqual(normalize_memory_backend("dual"), "dual")
        self.assertEqual(normalize_memory_backend("memcore"), "memcore")
        self.assertEqual(normalize_memory_backend("surprise"), "legacy")

    def test_legacy_manager_does_not_import_memcore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("builtins.__import__", side_effect=_blocking_import):
                manager = MemcoreManager(
                    backend="legacy",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="user",
                    enable_flavor=True,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
        status = manager.status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["available"])
        self.assertEqual(status["backend"], "legacy")

    def test_nonlegacy_manager_reports_unavailable_without_memcore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("builtins.__import__", side_effect=_blocking_import):
                manager = MemcoreManager(
                    backend="dual",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="bad-scope",
                    enable_flavor=True,
                    shadow_compare=True,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
        status = manager.status()
        self.assertTrue(status["enabled"])
        self.assertFalse(status["available"])
        self.assertEqual(status["backend"], "dual")
        self.assertEqual(status["visible_scope"], "user")
        self.assertTrue(status["shadow_compare"])
        self.assertTrue(status["degraded_embedding"])
        self.assertIn("blocked memcore import", status["reason"])

    def test_unavailable_manager_dual_write_methods_do_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("builtins.__import__", side_effect=_blocking_import):
                manager = MemcoreManager(
                    backend="dual",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="user",
                    enable_flavor=True,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
        result = manager.record_user_turn(
            {"source_id": "u1", "content": "hello", "timestamp": 100},
            profile_user_id="user-a",
            session_id="session-a",
            character_pack_id="char-a",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unavailable")

    def test_dual_write_records_raw_and_updates_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="dual",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            self.assertTrue(manager.available, manager.status())

            user_record = {
                "source_id": "user-turn-1",
                "content": "我喜欢可乐，尤其是冰的。",
                "timestamp": 1_777_777_000,
                "memory_metadata": {},
            }
            first = manager.record_user_turn(
                user_record,
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            duplicate = manager.record_user_turn(
                user_record,
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            metadata = {
                "keywords": ["可乐", "饮料"],
                "subject_scopes": ["user"],
                "categories": ["preference"],
                "mood_tags": ["happy"],
                "importance": 0.8,
                "confidence": 0.9,
            }
            updated = manager.update_turn_metadata(
                "user-turn-1",
                metadata,
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            assistant = manager.record_assistant_turn(
                {
                    "source_id": "assistant-turn-1",
                    "content": "我记住啦，下次聊饮料会想到冰可乐。",
                    "timestamp": 1_777_777_010,
                    "memory_metadata": {},
                },
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            compact = manager.compact_due_background(
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )

            self.assertTrue(first["ok"])
            self.assertTrue(duplicate["ok"])
            self.assertTrue(updated["ok"])
            self.assertTrue(assistant["ok"])
            self.assertEqual(compact["status"], "scheduled")

            system = manager._get_system(
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            records = manager._store.list_index_records(namespace=system.namespace, with_conversation=True)
            source_ids = [record["source_id"] for record in records]
            self.assertEqual(source_ids.count("user-turn-1"), 1)
            self.assertIn("assistant-turn-1", source_ids)

            stored_user = manager._store.get_record_by_source_id("user-turn-1")
            self.assertEqual(stored_user["memory_metadata"]["keywords"], ["可乐", "饮料"])
            self.assertEqual(stored_user["memory_metadata"]["categories"], ["preference"])
            self.assertEqual(stored_user["memory_metadata"]["mood_tags"], ["happy"])
            self.assertEqual(stored_user["memory_metadata"]["importance"], 0.8)
            manager.close()

    def test_dual_write_rejects_cross_namespace_metadata_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="dual",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            created = manager.record_user_turn(
                {"source_id": "shared-source", "content": "u1 private", "timestamp": 123},
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )
            crossed = manager.update_turn_metadata(
                "shared-source",
                {"keywords": ["leak"], "categories": ["preference"], "importance": 0.9},
                profile_user_id="u2",
                session_id="s1",
                character_pack_id="char",
            )

            self.assertTrue(created["ok"])
            self.assertFalse(crossed["ok"])
            self.assertEqual(crossed["status"], "failed")
            stored_user = manager._store.get_record_by_source_id("shared-source")
            self.assertEqual(stored_user["memory_metadata"]["keywords"], [])
            manager.close()

    def test_dual_write_skips_user_turns_disabled_by_legacy_index_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="dual",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            result = manager.record_user_turn(
                {
                    "source_id": "memory-query-turn",
                    "content": "你还记得我之前说过什么吗？",
                    "timestamp": 123,
                    "index_in_vector": False,
                },
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "legacy_index_disabled")
            self.assertIsNone(manager._store.get_record_by_source_id("memory-query-turn"))
            manager.close()

    def test_shadow_retrieve_returns_structural_hash_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="dual",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            manager.record_user_turn(
                {
                    "source_id": "old-like",
                    "content": "我以前说过我喜欢冰可乐。",
                    "timestamp": 1_777_700_000,
                    "memory_metadata": {"keywords": ["可乐"], "categories": ["preference"], "importance": 0.9},
                },
                profile_user_id="u1",
                session_id="older-session",
                character_pack_id="char",
            )
            current = {
                "source_id": "current-query",
                "content": "我以前说过喜欢什么饮料？",
                "timestamp": 1_777_800_000,
                "memory_metadata": {},
            }
            manager.record_user_turn(
                current,
                profile_user_id="u1",
                session_id="current-session",
                character_pack_id="char",
            )

            with patch.object(config, "MEMCORE_SHADOW_COMPARE", True):
                result = manager.shadow_retrieve_memory(
                    profile_user_id="u1",
                    session_id="current-session",
                    character_pack_id="char",
                    current_user_record=current,
                    query="可乐",
                    keywords=["可乐"],
                    categories=["preference"],
                    importance_min=0.5,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ok")
            self.assertGreaterEqual(result["snippet_count"], 1)
            self.assertTrue(result["snippet_hashes"])
            self.assertNotIn("冰可乐", repr(result))
            manager.close()

    def test_retrieve_memory_tool_attaches_shadow_without_changing_followup(self) -> None:
        class _FakeStore:
            def get_message_by_source_id(self, source_id: str) -> dict[str, object]:
                return {
                    "source_id": str(source_id),
                    "content": "用户问旧饮料偏好",
                    "timestamp": 100,
                }

            def get_unsummarized_messages(self, session_id: str, *, character_pack_id: str = "") -> list[dict]:
                return []

            def get_visible_episodic_summaries(self, profile_user_id: str, *, limit: int, character_pack_id: str = "") -> list[dict]:
                return []

            def get_recent_semantic_summaries(self, profile_user_id: str, *, limit: int, character_pack_id: str = "") -> list[dict]:
                return []

        class _FakeRetrievalService:
            def run_explicit(self, **kwargs) -> RetrievalPipelineResult:
                return RetrievalPipelineResult(
                    used_retrieval=True,
                    confirmed_snippets=["legacy snippet about cola"],
                    router_output={},
                    router_timing={},
                    retrieval_result={"fused_hits": [{"source_id": "legacy-1"}]},
                    verifier_output={"match_result": "match"},
                    verifier_timing={},
                )

        class _FakeShadowManager:
            enabled = True
            available = True

            def shadow_retrieve_memory(self, **kwargs) -> dict[str, object]:
                return {
                    "operation": "shadow_retrieve_memory",
                    "ok": True,
                    "status": "ok",
                    "snippet_count": 2,
                    "snippet_hashes": ["abc123"],
                    "latency_ms": 1,
                }

        class _FakeEngine:
            store = _FakeStore()
            memcore_manager = _FakeShadowManager()

            def _get_retrieval_service(self) -> _FakeRetrievalService:
                return _FakeRetrievalService()

        context = ToolExecutionContext(
            profile_user_id="u1",
            session_id="s1",
            now_ts=100,
            visual_payload={},
            character_pack_id="char",
            current_user_source_id="current",
        )
        with patch.object(config, "MEMCORE_SHADOW_COMPARE", True):
            result = retrieval_engine.execute_retrieve_memory_tool(
                _FakeEngine(),
                call={"query": "可乐", "keywords": ["可乐"]},
                context=context,
            )

        self.assertIn("legacy snippet about cola", result.followup_context)
        self.assertNotIn("abc123", result.followup_context)
        shadow = result.state_updates["memory_retrieval"]["memcore_shadow"]
        self.assertEqual(shadow["status"], "ok")
        self.assertEqual(shadow["snippet_count"], 2)
        self.assertEqual(shadow["legacy_snippet_count"], 1)
        self.assertEqual(shadow["snippet_hashes"], ["abc123"])


if __name__ == "__main__":
    unittest.main()
