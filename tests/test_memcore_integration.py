from __future__ import annotations

import builtins
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import config
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.memcore_integration.manager import MemcoreManager, normalize_memory_backend
from companion_v01.memcore_integration.timeline import MemcoreTimelineToolService
from companion_v01.retrieval_types import RetrievalPipelineResult
from companion_v01 import retrieval_engine
from companion_v01.tool_runtime import ReadMemoryTimelineToolHandler, ToolExecutionContext


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


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


class _ToolFakeStore:
    def __init__(self) -> None:
        self.current_record = {
            "source_id": "current",
            "content": "用户问旧饮料偏好",
            "timestamp": 100,
        }

    def get_message_by_source_id(self, source_id: str) -> dict[str, object]:
        return {**self.current_record, "source_id": str(source_id)}

    def get_unsummarized_messages(self, session_id: str, *, character_pack_id: str = "") -> list[dict]:
        return [{"source_id": "visible-raw", "content": "已在 prompt 里的 raw"}]

    def get_visible_episodic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        return [{"summary_id": "visible-summary"}]

    def get_recent_semantic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        return [{"semantic_id": "visible-semantic"}]


class _ToolFakeRetrievalService:
    def __init__(self, snippets: list[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.snippets = snippets or ["legacy snippet about cola"]

    def run_explicit(self, **kwargs) -> RetrievalPipelineResult:
        self.calls.append(dict(kwargs))
        return RetrievalPipelineResult(
            used_retrieval=True,
            confirmed_snippets=list(self.snippets),
            router_output={},
            router_timing={},
            retrieval_result={"fused_hits": [{"source_id": "legacy-1"}]},
            verifier_output={"match_result": "match"},
            verifier_timing={},
        )


class _ToolFakeMemcoreManager:
    enabled = True
    available = True

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def retrieve_memory(self, **kwargs) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(self.payload)

    def status(self) -> dict[str, object]:
        return {"reason": "fake"}


class _ToolFakeEngine:
    def __init__(
        self,
        *,
        memcore_manager: _ToolFakeMemcoreManager | None = None,
        retrieval_service: _ToolFakeRetrievalService | None = None,
    ) -> None:
        self.store = _ToolFakeStore()
        self.memcore_manager = memcore_manager
        self.retrieval_service = retrieval_service or _ToolFakeRetrievalService()

    def _get_retrieval_service(self) -> _ToolFakeRetrievalService:
        return self.retrieval_service


class _FinalPromptProfile:
    supports_thought_debug = False
    system_prompt_override = ""

    def includes(self, _module) -> bool:
        return False

    def mode_prompt_override(self, *, debug_enabled: bool = False) -> str:
        return ""

    def to_public_dict(self) -> dict[str, object]:
        return {"name": "fake"}


class _CapturePromptBuilder:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def build_final_generation_context(self, **kwargs):
        self.kwargs = dict(kwargs)
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "", "tool_call": None},
            "visual_defaults": dict(kwargs.get("visual_defaults") or {}),
            "debug_enabled": bool(kwargs.get("debug_enabled")),
            "tool_prompt_context": str(kwargs.get("tool_prompt_context") or ""),
            "system_extra_blocks": [],
            "history_turns": [],
            "prompt_audit_sections": [],
        }


class _PromptContextMemcoreManager:
    enabled = True
    available = True

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def build_prompt_context(self, **kwargs) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(self.payload)


class _PromptContextEngine:
    def __init__(self, *, memcore_manager: _PromptContextMemcoreManager) -> None:
        self.resource_manifest = None
        self.store = SimpleNamespace()
        self.vision_service = None
        self.memcore_manager = memcore_manager
        self.prompt_builder = _CapturePromptBuilder()

    def _resolve_client_protocol_context(self, _payload) -> ClientProtocolContext:
        return ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

    def _get_prompt_profile_registry(self):
        return SimpleNamespace(resolve=lambda _client_context: _FinalPromptProfile())

    def _get_user_runtime_projection(self, _profile_user_id: str) -> dict[str, list[object]]:
        return {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }

    def _split_history_records(self, **_kwargs):
        return [], {"source_id": "current", "role": "user", "content": "现在的问题", "timestamp": 1712400000}

    def _render_current_message_line(self, **_kwargs) -> str:
        return "User: 现在的问题"

    def _get_attachment_inbox_service(self):
        return None

    def _get_generated_file_service(self):
        return None

    def _get_workspace_file_service(self):
        return None

    def _get_task_workspace_service(self):
        return None

    def _get_persona_card_service(self):
        return None

    def _build_desktop_pet_character_pack_prompt_context(self, **_kwargs) -> dict[str, str]:
        return {"system_context": "", "reference_context": "", "active_id": ""}

    def _merge_prompt_persona_contexts(self, _character_pack, _profile) -> dict[str, str]:
        return {"system_context": "", "reference_context": "", "active_id": ""}

    def _resolve_current_visual_payload(self, **_kwargs):
        return None

    def _build_memory_relationship_context(self, **_kwargs) -> str:
        return ""

    def _build_extra_context_audit_sections(self, _candidates) -> list[dict[str, str]]:
        return []

    def _build_tool_prompt_context(self, **_kwargs) -> str:
        return ""

    def _get_prompt_builder(self) -> _CapturePromptBuilder:
        return self.prompt_builder


class _TimelineLegacyService:
    def __init__(self) -> None:
        self.read_calls: list[dict[str, object]] = []

    def normalize_time_periods(self, values) -> list[str]:
        mapping = {"上午": "morning", "morning": "morning"}
        return [mapping[str(item)] for item in values or [] if str(item) in mapping]

    def read(self, **kwargs) -> dict[str, object]:
        self.read_calls.append(dict(kwargs))
        return {
            "status": "ok",
            "reason": "",
            "date_from": str(kwargs.get("date_from") or ""),
            "date_to": str(kwargs.get("date_to") or ""),
            "time_periods": list(kwargs.get("time_periods") or []),
            "active_dates": ["2026-06-13"],
            "message_count": 1,
            "messages": [{"content": "LEGACY TIMELINE"}],
        }

    def render_tool_context(self, _result: dict[str, object]) -> str:
        return "LEGACY TIMELINE"

    def build_acquaintance_prompt(self, **_kwargs) -> str:
        return ""


class _TimelineMemcoreManager:
    enabled = True
    available = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def read_memory_timeline(self, **kwargs) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {
            "operation": "read_memory_timeline",
            "ok": True,
            "status": "ok",
            "reason": "",
            "date_from": str(kwargs.get("date_from") or ""),
            "date_to": str(kwargs.get("date_to") or ""),
            "time_periods": list(kwargs.get("time_periods") or []),
            "active_dates": ["2026-06-13"],
            "message_count": 1,
            "messages": [{"source_id": "m1", "content": "MEMCORE TIMELINE"}],
            "text": "MEMCORE TIMELINE",
            "backend": "memcore",
        }


class _LegacyCompactionRecorder:
    def __init__(self) -> None:
        self.scheduled: list[dict[str, object]] = []
        self.ran: list[dict[str, object]] = []

    def schedule_summary_cycle(self, **kwargs) -> None:
        self.scheduled.append(dict(kwargs))

    def run_summary_cycle(self, **kwargs) -> None:
        self.ran.append(dict(kwargs))


class _CompactionMemcoreManager:
    enabled = True

    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.sync_calls: list[dict[str, object]] = []

    def compact_due_sync(self, **kwargs) -> dict[str, object]:
        self.sync_calls.append(dict(kwargs))
        return {"ok": True, "status": "completed", "stats": {}}


def _tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="u1",
        session_id="s1",
        now_ts=100,
        visual_payload={"_memory_retrieval_exclude_source_ids": ["extra-visible"]},
        character_pack_id="char",
        current_user_source_id="current",
    )


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

    def test_engine_memcore_mode_lets_memcore_own_compaction_when_available(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        legacy_compaction = _LegacyCompactionRecorder()
        memcore_manager = _CompactionMemcoreManager(available=True)
        engine.compaction_service = legacy_compaction
        engine.memcore_manager = memcore_manager

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            engine._schedule_summary_cycle(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )
            engine._run_summary_cycle(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )

        self.assertEqual(legacy_compaction.scheduled, [])
        self.assertEqual(legacy_compaction.ran, [])
        self.assertEqual(
            memcore_manager.sync_calls,
            [{"profile_user_id": "u1", "session_id": "s1", "character_pack_id": "char"}],
        )

    def test_engine_memcore_mode_keeps_legacy_compaction_fallback_when_unavailable(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        legacy_compaction = _LegacyCompactionRecorder()
        memcore_manager = _CompactionMemcoreManager(available=False)
        engine.compaction_service = legacy_compaction
        engine.memcore_manager = memcore_manager

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            engine._schedule_summary_cycle(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )
            engine._run_summary_cycle(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )

        expected = {"profile_user_id": "u1", "session_id": "s1", "character_pack_id": "char"}
        self.assertEqual(legacy_compaction.scheduled, [expected])
        self.assertEqual(legacy_compaction.ran, [expected])
        self.assertEqual(memcore_manager.sync_calls, [])

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

    def test_build_prompt_context_returns_memcore_visible_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            manager.record_user_turn(
                {
                    "source_id": "visible-user-1",
                    "content": "我今天提到想喝冰可乐。",
                    "timestamp": 1_712_400_000,
                    "memory_metadata": {"keywords": ["可乐"], "categories": ["preference"], "importance": 0.7},
                },
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )

            result = manager.build_prompt_context(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
                current_user_record={"source_id": "visible-user-1", "timestamp": 1_712_400_000},
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["raw_count"], 1)
            self.assertIn("【近期原始对话(未摘要)】", result["raw_text"])
            self.assertIn("冰可乐", result["raw_text"])
            self.assertIn("冰可乐", result["rendered_text"])
            manager.close()

    def test_read_memory_timeline_returns_memcore_raw_and_excludes_current_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            manager.record_user_turn(
                {
                    "source_id": "old-morning",
                    "content": "上午讨论了角色提示词。",
                    "timestamp": _ts(2026, 6, 13, 9, 0),
                    "memory_metadata": {"keywords": ["提示词"], "categories": ["project_work"], "importance": 0.8},
                },
                profile_user_id="u1",
                session_id="old-session",
                character_pack_id="char",
            )
            manager.record_user_turn(
                {
                    "source_id": "current-query",
                    "content": "请读取今天上午的原始对话。",
                    "timestamp": _ts(2026, 6, 13, 10, 0),
                    "memory_metadata": {},
                },
                profile_user_id="u1",
                session_id="current-session",
                character_pack_id="char",
            )

            result = manager.read_memory_timeline(
                profile_user_id="u1",
                session_id="current-session",
                character_pack_id="char",
                date_from="2026-06-13",
                date_to="2026-06-13",
                time_periods=["morning"],
                exclude_source_ids=["current-query"],
                cross_conversation=True,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["message_count"], 1)
            self.assertIn("上午讨论了角色提示词", result["text"])
            self.assertNotIn("请读取今天上午", result["text"])
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
            self.assertNotIn("snippets", result)
            self.assertNotIn("冰可乐", repr(result))
            manager.close()

    def test_retrieve_memory_returns_snippets_and_honors_limit(self) -> None:
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
            for index, drink in enumerate(["冰可乐", "无糖可乐"], start=1):
                manager.record_user_turn(
                    {
                        "source_id": f"old-like-{index}",
                        "content": f"我以前说过我喜欢{drink}。",
                        "timestamp": 1_777_700_000 + index,
                        "memory_metadata": {
                            "keywords": ["可乐", "饮料"],
                            "categories": ["preference"],
                            "subject_scopes": ["user"],
                            "importance": 0.9,
                        },
                    },
                    profile_user_id="u1",
                    session_id=f"older-session-{index}",
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

            result = manager.retrieve_memory(
                profile_user_id="u1",
                session_id="current-session",
                character_pack_id="char",
                current_user_record=current,
                query="可乐",
                keywords=["可乐"],
                source_layers=["raw"],
                categories=["preference"],
                importance_min=0.5,
                limit=1,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["snippet_count"], 1)
            self.assertEqual(len(result["snippets"]), 1)
            self.assertIn("可乐", result["snippets"][0])
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

    def test_retrieve_memory_tool_uses_memcore_read_side_without_legacy_call(self) -> None:
        memcore_manager = _ToolFakeMemcoreManager(
            {
                "operation": "retrieve_memory",
                "ok": True,
                "status": "ok",
                "snippet_count": 1,
                "snippet_hashes": ["hash-1"],
                "latency_ms": 2,
                "snippets": ["memcore snippet about cola"],
            }
        )
        retrieval_service = _ToolFakeRetrievalService()
        engine = _ToolFakeEngine(memcore_manager=memcore_manager, retrieval_service=retrieval_service)

        with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(config, "MEMCORE_SHADOW_COMPARE", False):
            result = retrieval_engine.execute_retrieve_memory_tool(
                engine,
                call={"query": "可乐", "keywords": ["可乐"], "categories": ["preference"], "limit": 3},
                context=_tool_context(),
            )

        self.assertIn("memcore snippet about cola", result.followup_context)
        self.assertEqual(retrieval_service.calls, [])
        self.assertEqual(len(memcore_manager.calls), 1)
        call = memcore_manager.calls[0]
        self.assertEqual(call["query"], "可乐")
        self.assertEqual(call["limit"], 3)
        self.assertIn("current", call["exclude_source_ids"])
        self.assertIn("visible-raw", call["exclude_source_ids"])
        self.assertIn("visible-summary", call["exclude_source_ids"])
        self.assertIn("visible-semantic", call["exclude_source_ids"])
        self.assertIn("extra-visible", call["exclude_source_ids"])
        state = result.state_updates["memory_retrieval"]
        self.assertEqual(state["retrieval_backend"], "memcore")
        self.assertEqual(state["confirmed_snippets"], ["memcore snippet about cola"])
        self.assertEqual(state["memcore_read"]["snippet_count"], 1)
        self.assertNotIn("snippets", state["memcore_read"])

    def test_retrieve_memory_tool_memcore_no_hit_keeps_no_hit_followup(self) -> None:
        memcore_manager = _ToolFakeMemcoreManager(
            {
                "operation": "retrieve_memory",
                "ok": True,
                "status": "ok",
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": 1,
                "snippets": [],
            }
        )
        retrieval_service = _ToolFakeRetrievalService()
        engine = _ToolFakeEngine(memcore_manager=memcore_manager, retrieval_service=retrieval_service)

        with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(config, "MEMCORE_SHADOW_COMPARE", False):
            result = retrieval_engine.execute_retrieve_memory_tool(
                engine,
                call={"query": "可乐"},
                context=_tool_context(),
            )

        self.assertIn("没有找到足以回答主人问题", result.followup_context)
        self.assertEqual(retrieval_service.calls, [])
        state = result.state_updates["memory_retrieval"]
        self.assertEqual(state["retrieval_backend"], "memcore")
        self.assertEqual(state["confirmed_snippets"], [])
        self.assertEqual(state["verifier_output"]["match_result"], "no_match")

    def test_retrieve_memory_tool_falls_back_to_legacy_when_memcore_fails(self) -> None:
        memcore_manager = _ToolFakeMemcoreManager(
            {
                "operation": "retrieve_memory",
                "ok": False,
                "status": "failed",
                "reason": "boom",
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": 1,
                "snippets": ["should not leak"],
            }
        )
        retrieval_service = _ToolFakeRetrievalService(snippets=["legacy fallback snippet"])
        engine = _ToolFakeEngine(memcore_manager=memcore_manager, retrieval_service=retrieval_service)

        with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(config, "MEMCORE_SHADOW_COMPARE", False):
            result = retrieval_engine.execute_retrieve_memory_tool(
                engine,
                call={"query": "可乐"},
                context=_tool_context(),
            )

        self.assertIn("legacy fallback snippet", result.followup_context)
        self.assertEqual(len(retrieval_service.calls), 1)
        state = result.state_updates["memory_retrieval"]
        self.assertEqual(state["retrieval_backend"], "legacy")
        self.assertEqual(state["confirmed_snippets"], ["legacy fallback snippet"])
        self.assertEqual(state["memcore_read"]["status"], "failed")
        self.assertEqual(state["memcore_read"]["reason"], "boom")
        self.assertNotIn("snippets", state["memcore_read"])

    def test_final_prompt_context_uses_memcore_visible_layers_in_memcore_mode(self) -> None:
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": True,
                "status": "ok",
                "raw_text": "MEMCORE RAW",
                "episodic_text": "MEMCORE EPISODIC",
                "semantic_text": "MEMCORE SEMANTIC",
                "raw_count": 1,
                "episodic_count": 1,
                "semantic_count": 1,
            }
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="现在的问题",
                recent_raw=[{"role": "user", "content": "LEGACY RAW", "timestamp": 1712400000}],
                recent_episodic_summaries=[{"diary_summary": "LEGACY EPISODIC", "timestamp": 1712400000}],
                recent_semantic_summaries=[{"semantic_summary": "LEGACY SEMANTIC", "timestamp": 1712400000}],
                confirmed_snippets=[],
                now_ts=1712400000,
                character_pack_id="char",
            )

        captured = engine.prompt_builder.kwargs
        self.assertEqual(captured["raw_text"], "MEMCORE RAW")
        self.assertEqual(captured["episodic_summary_text"], "MEMCORE EPISODIC")
        self.assertEqual(captured["semantic_summary_text"], "MEMCORE SEMANTIC")
        self.assertNotIn("LEGACY", repr(captured))
        self.assertEqual(memcore_manager.calls[0]["profile_user_id"], "u1")
        self.assertEqual(memcore_manager.calls[0]["session_id"], "s1")
        self.assertEqual(memcore_manager.calls[0]["character_pack_id"], "char")
        self.assertEqual(memcore_manager.calls[0]["current_user_record"]["source_id"], "current")

    def test_read_memory_timeline_tool_uses_memcore_adapter_in_memcore_mode(self) -> None:
        legacy = _TimelineLegacyService()
        memcore_manager = _TimelineMemcoreManager()
        service = MemcoreTimelineToolService(legacy_service=legacy, memcore_manager=memcore_manager)
        handler = ReadMemoryTimelineToolHandler(timeline_service=service)
        call = handler.normalize_call(
            {
                "type": "read_memory_timeline",
                "date": "2026-06-13",
                "time_periods": ["上午"],
            }
        )
        self.assertIsNotNone(call)
        assert call is not None

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = handler.execute(
                call=call,
                context=ToolExecutionContext(
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                    now_ts=_ts(2026, 6, 13, 12, 0),
                    visual_payload={},
                    current_user_source_id="current-query",
                ),
            )

        self.assertIn("MEMCORE TIMELINE", result.followup_context)
        self.assertIn("memcore", result.followup_context)
        self.assertEqual(legacy.read_calls, [])
        self.assertEqual(memcore_manager.calls[0]["profile_user_id"], "u1")
        self.assertEqual(memcore_manager.calls[0]["session_id"], "u1")
        self.assertEqual(memcore_manager.calls[0]["character_pack_id"], "char")
        self.assertEqual(memcore_manager.calls[0]["time_periods"], ["morning"])
        self.assertEqual(memcore_manager.calls[0]["exclude_source_ids"], ["current-query"])
        self.assertTrue(memcore_manager.calls[0]["cross_conversation"])
        self.assertEqual(result.state_updates["memory_timeline"]["status"], "ok")
        self.assertEqual(result.state_updates["memory_timeline"]["message_count"], 1)


if __name__ == "__main__":
    unittest.main()
