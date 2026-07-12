from __future__ import annotations

import builtins
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import time
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


def _raw_vector_record() -> dict[str, object]:
    return {
        "source_id": "raw-1",
        "profile_user_id": "u1",
        "session_id": "s1",
        "character_pack_id": "char",
        "seq_no": 1,
        "role": "user",
        "content": "我喜欢冰可乐。",
        "timestamp": _ts(2026, 6, 1, 9, 0),
        "date_label": "2026-06-01",
        "time_of_day": "morning",
        "semantic_tags": ["可乐"],
        "memory_metadata": {"keywords": ["可乐"], "categories": ["preference"]},
        "index_in_vector": True,
    }


class _ToolFakeStore:
    def __init__(self) -> None:
        self.current_record = {
            "source_id": "current",
            "content": "用户问旧饮料偏好",
            "timestamp": 100,
        }
        self.legacy_visible_reads: list[str] = []

    def get_message_by_source_id(self, source_id: str) -> dict[str, object]:
        return {**self.current_record, "source_id": str(source_id)}

    def get_unsummarized_messages(self, session_id: str, *, character_pack_id: str = "") -> list[dict]:
        self.legacy_visible_reads.append("raw")
        return [{"source_id": "visible-raw", "content": "已在 prompt 里的 raw"}]

    def get_visible_episodic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        self.legacy_visible_reads.append("episodic")
        return [{"summary_id": "visible-summary"}]

    def get_recent_semantic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        self.legacy_visible_reads.append("semantic")
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


class _MaterialFakeSystem:
    def __init__(self) -> None:
        self.references: list[dict[str, object]] = []
        self.cleanups: list[dict[str, object]] = []

    def record_material_reference(self, **kwargs) -> dict[str, object]:
        self.references.append(dict(kwargs))
        return {"source_id": str(kwargs.get("source_id") or ""), "index_status": "indexed"}

    def record_material_cleanup(self, **kwargs) -> dict[str, object]:
        self.cleanups.append(dict(kwargs))
        return {"source_id": str(kwargs.get("source_id") or ""), "index_status": "indexed"}


class _MaterialMemcoreManager(MemcoreManager):
    def __init__(self) -> None:
        self.backend = "memcore"
        self._available = True
        self._reason = ""
        self._memcore_module = SimpleNamespace(
            Actor=lambda *, stable_id, display_name: SimpleNamespace(
                stable_id=stable_id,
                display_name=display_name,
            )
        )
        self.system = _MaterialFakeSystem()
        self.system_calls: list[dict[str, object]] = []

    def _get_system_or_none(self, **kwargs) -> _MaterialFakeSystem:
        self.system_calls.append(dict(kwargs))
        return self.system


class _ActorCaptureMemcoreManager:
    enabled = True
    available = True

    def __init__(self) -> None:
        self.user_calls: list[dict[str, object]] = []
        self.metadata_calls: list[dict[str, object]] = []

    def record_user_turn(self, record: dict[str, object], **kwargs) -> dict[str, object]:
        self.user_calls.append({"record": dict(record), **dict(kwargs)})
        return {"ok": True, "status": "recorded"}

    def update_turn_metadata(
        self,
        source_id: str,
        memory_metadata: dict[str, object],
        **kwargs,
    ) -> dict[str, object]:
        self.metadata_calls.append(
            {
                "source_id": source_id,
                "memory_metadata": dict(memory_metadata),
                **dict(kwargs),
            }
        )
        return {"ok": True, "status": "updated"}


class _VectorWriteStore:
    def __init__(self) -> None:
        self.index_updates: list[tuple[str, bool]] = []

    def update_message_index_in_vector(self, source_id: str, value: bool) -> None:
        self.index_updates.append((source_id, value))


class _VectorWriteRecorder:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    def upsert_entries(self, entries: list[dict[str, object]]) -> None:
        self.entries.extend(entries)


class _ExplodingLegacyIndexStore:
    def count_vectorizable_records(self) -> int:
        raise AssertionError("legacy vector count should not run in memcore mode")


class _ExplodingLegacyVectorStore:
    collection_name = "legacy_vector"

    def count_entries(self) -> int:
        raise AssertionError("legacy vector count should not run in memcore mode")


class _VisibleMemoryStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_unsummarized_messages(self, session_id: str, *, character_pack_id: str = "") -> list[dict]:
        self.calls.append(f"raw:{session_id}:{character_pack_id}")
        return [{"source_id": "legacy-raw", "content": "旧 raw"}]

    def get_visible_episodic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        self.calls.append(f"episodic:{profile_user_id}:{character_pack_id}:{limit}")
        return [{"summary_id": "legacy-episodic", "diary_summary": "旧 episodic"}]

    def get_recent_semantic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        self.calls.append(f"semantic:{profile_user_id}:{character_pack_id}:{limit}")
        return [{"semantic_id": "legacy-semantic", "semantic_summary": "旧 semantic"}]


class _ExplodingVisibleMemoryStore:
    def get_unsummarized_messages(self, session_id: str, *, character_pack_id: str = "") -> list[dict]:
        raise AssertionError("legacy raw visible memory should not be read in memcore mode")

    def get_visible_episodic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        raise AssertionError("legacy episodic visible memory should not be read in memcore mode")

    def get_recent_semantic_summaries(
        self,
        profile_user_id: str,
        *,
        limit: int,
        character_pack_id: str = "",
    ) -> list[dict]:
        raise AssertionError("legacy semantic visible memory should not be read in memcore mode")


class _LegacyRawStore:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.batch_sizes: list[int] = []

    def iter_messages_for_vector_reindex(self, batch_size: int = 64):
        self.batch_sizes.append(int(batch_size))
        for start in range(0, len(self.rows), max(1, int(batch_size))):
            yield self.rows[start : start + max(1, int(batch_size))]


class _LegacyLongTermStore:
    def __init__(
        self,
        *,
        summaries: list[dict[str, object]] | None = None,
        semantic_summaries: list[dict[str, object]] | None = None,
    ) -> None:
        self.summaries = summaries or []
        self.semantic_summaries = semantic_summaries or []

    def iter_summaries_for_vector_reindex(self, batch_size: int = 64):
        step = max(1, int(batch_size))
        for start in range(0, len(self.summaries), step):
            yield self.summaries[start : start + step]

    def iter_semantic_summaries_for_vector_reindex(self, batch_size: int = 64):
        step = max(1, int(batch_size))
        for start in range(0, len(self.semantic_summaries), step):
            yield self.semantic_summaries[start : start + step]


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
    def test_index_warmup_is_scheduled_without_blocking_first_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="legacy",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="user",
                enable_flavor=False,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            started = threading.Event()
            release = threading.Event()

            class _SlowWarmupSystem:
                namespace = SimpleNamespace(hard_key=lambda: ("tenant", "user", "domain"))

                def reindex_all(self, **_kwargs):
                    started.set()
                    release.wait(timeout=2)

            started_at = time.perf_counter()
            manager._warm_index_for_system(_SlowWarmupSystem(), operation="test")
            elapsed = time.perf_counter() - started_at
            try:
                self.assertLess(elapsed, 0.2)
                self.assertTrue(started.wait(timeout=1))
                close_thread = threading.Thread(target=manager.close)
                close_thread.start()
                close_thread.join(timeout=0.05)
                self.assertTrue(close_thread.is_alive())
            finally:
                release.set()
                close_thread.join(timeout=1)
                self.assertFalse(close_thread.is_alive())

    def test_normalize_memory_backend(self) -> None:
        self.assertEqual(normalize_memory_backend("legacy"), "legacy")
        self.assertEqual(normalize_memory_backend("dual"), "dual")
        self.assertEqual(normalize_memory_backend("memcore"), "memcore")
        self.assertEqual(normalize_memory_backend("surprise"), "memcore")
        self.assertEqual(normalize_memory_backend(""), "memcore")

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

    def test_engine_memcore_mode_stops_legacy_raw_vector_writes_when_available(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.store = _VectorWriteStore()
        engine.vector_store = _VectorWriteRecorder()
        engine.memcore_manager = _CompactionMemcoreManager(available=True)
        record = _raw_vector_record()

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            engine._upsert_raw_record(record)

        self.assertEqual(engine.vector_store.entries, [])
        self.assertEqual(engine.store.index_updates, [("raw-1", False)])
        self.assertFalse(record["index_in_vector"])

    def test_engine_memcore_mode_keeps_legacy_raw_vector_fallback_when_unavailable(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.store = _VectorWriteStore()
        engine.vector_store = _VectorWriteRecorder()
        engine.memcore_manager = _CompactionMemcoreManager(available=False)
        record = _raw_vector_record()

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            engine._upsert_raw_record(record)

        self.assertEqual([entry["source_id"] for entry in engine.vector_store.entries], ["raw-1"])
        self.assertEqual(engine.store.index_updates, [])
        self.assertTrue(record["index_in_vector"])

    def test_engine_dual_mode_keeps_legacy_raw_vector_writes(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.store = _VectorWriteStore()
        engine.vector_store = _VectorWriteRecorder()
        engine.memcore_manager = _CompactionMemcoreManager(available=True)
        record = _raw_vector_record()

        with patch.object(config, "MEMORY_BACKEND", "dual"):
            engine._upsert_raw_record(record)

        self.assertEqual([entry["source_id"] for entry in engine.vector_store.entries], ["raw-1"])
        self.assertEqual(engine.store.index_updates, [])
        self.assertTrue(record["index_in_vector"])

    def test_engine_memcore_mode_skips_legacy_embedding_reindex_startup(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.store = _ExplodingLegacyIndexStore()
        engine.vector_store = _ExplodingLegacyVectorStore()
        engine.memcore_manager = _CompactionMemcoreManager(available=True)
        engine._embedding_reindex_lock = threading.RLock()
        engine._embedding_reindex_thread = None
        engine._embedding_reindex_status = {
            "state": "idle",
            "processed": 0,
            "total": 0,
            "started_at": 0.0,
            "finished_at": 0.0,
            "error": "",
            "collection_name": "legacy_vector",
        }

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            engine._maybe_start_embedding_reindex()

        self.assertEqual(engine._embedding_reindex_status["state"], "disabled")
        self.assertEqual(engine._embedding_reindex_status["error"], "memcore_owns_legacy_vector_index")
        self.assertIsNone(engine._embedding_reindex_thread)

    def test_engine_memcore_mode_skips_legacy_timeline_mirror_when_available(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = _CompactionMemcoreManager(available=True)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            self.assertFalse(engine._should_init_legacy_memory_timeline())

    def test_engine_memcore_unavailable_keeps_legacy_timeline_mirror_fallback(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = _CompactionMemcoreManager(available=False)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            self.assertTrue(engine._should_init_legacy_memory_timeline())

    def test_engine_memcore_mode_uses_current_turn_as_visible_memory_boundary(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.store = _ExplodingVisibleMemoryStore()
        engine.memcore_manager = _CompactionMemcoreManager(available=True)
        user_record = {
            "source_id": "current",
            "role": "user",
            "content": "现在的问题",
            "timestamp": 100,
        }

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            recent_raw, episodic, semantic = engine._load_turn_visible_memory(
                session_id="s1",
                profile_user_id="u1",
                character_pack_id="char",
                user_record=user_record,
                include_transient_user_record=False,
            )

        self.assertEqual(recent_raw, [user_record])
        self.assertEqual(episodic, [])
        self.assertEqual(semantic, [])

    def test_engine_memcore_unavailable_keeps_legacy_visible_memory_fallback(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        store = _VisibleMemoryStore()
        engine.store = store
        engine.memcore_manager = _CompactionMemcoreManager(available=False)
        transient_record = {
            "source_id": "current",
            "role": "user",
            "content": "临时问题",
            "timestamp": 100,
        }

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            recent_raw, episodic, semantic = engine._load_turn_visible_memory(
                session_id="s1",
                profile_user_id="u1",
                character_pack_id="char",
                user_record=transient_record,
                include_transient_user_record=True,
            )

        self.assertEqual([row["source_id"] for row in recent_raw], ["legacy-raw", "current"])
        self.assertEqual(episodic[0]["summary_id"], "legacy-episodic")
        self.assertEqual(semantic[0]["semantic_id"], "legacy-semantic")
        episodic_limit = max(
            1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5)))
        )
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        self.assertEqual(
            store.calls,
            [
                "raw:s1:char",
                f"episodic:u1:char:{episodic_limit}",
                f"semantic:u1:char:{semantic_limit}",
            ],
        )

    def test_memcore_pre_retrieval_skip_does_not_lazy_load_legacy_retrieval(self) -> None:
        class _Engine:
            def _coerce_bool(self, _value):
                return None

            def _get_retrieval_service(self):
                raise AssertionError("legacy retrieval should not load in memcore mode")

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = retrieval_engine.run_pre_retrieval_pipeline(
                _Engine(),
                payload={},
                profile_user_id="u1",
                character_pack_id="char",
                user_message="晚上我们聊过什么？",
                now_ts=_ts(2026, 6, 1, 21, 0),
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                current_user_source_id="current",
                verifier_debug_enabled=False,
            )

        self.assertFalse(result.used_retrieval)
        self.assertEqual(result.router_output["route"], "pre_retrieval_disabled")
        self.assertEqual(result.router_output["time_hint"]["time_of_day"], "night")
        self.assertEqual(result.router_timing["mode"], "shortcut")
        self.assertIn("memcore 模式", result.router_output["reason"])

    def test_import_legacy_raw_messages_is_idempotent_and_filtered(self) -> None:
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
            legacy_store = _LegacyRawStore(
                [
                    {
                        "source_id": "old-user",
                        "profile_user_id": "u1",
                        "session_id": "old-session",
                        "character_pack_id": "char",
                        "role": "user",
                        "content": "以前聊过冰可乐。",
                        "timestamp": _ts(2026, 6, 1, 9, 0),
                        "memory_metadata": {"keywords": ["可乐"], "categories": ["preference"], "importance": 0.8},
                    },
                    {
                        "source_id": "old-assistant",
                        "profile_user_id": "u1",
                        "session_id": "old-session",
                        "character_pack_id": "char",
                        "role": "assistant",
                        "content": "我会记住你喜欢冰可乐。",
                        "timestamp": _ts(2026, 6, 1, 9, 1),
                        "memory_metadata": {},
                    },
                    {
                        "source_id": "other-profile",
                        "profile_user_id": "u2",
                        "session_id": "old-session",
                        "character_pack_id": "char",
                        "role": "user",
                        "content": "别人的记忆不能导入 u1。",
                        "timestamp": _ts(2026, 6, 1, 9, 2),
                    },
                    {
                        "source_id": "tool-turn",
                        "profile_user_id": "u1",
                        "session_id": "old-session",
                        "character_pack_id": "char",
                        "role": "npc:旁白",
                        "content": "工具或 NPC 原始行先不回填。",
                        "timestamp": _ts(2026, 6, 1, 9, 3),
                    },
                ]
            )

            first = manager.import_legacy_raw_messages(
                legacy_store=legacy_store,
                profile_user_id="u1",
                character_pack_id="char",
                batch_size=2,
            )
            second = manager.import_legacy_raw_messages(
                legacy_store=legacy_store,
                profile_user_id="u1",
                character_pack_id="char",
                batch_size=2,
            )

            self.assertTrue(first["ok"], first)
            self.assertEqual(first["scanned"], 4)
            self.assertEqual(first["upserted"], 2)
            self.assertEqual(first["filtered"], 1)
            self.assertEqual(first["skipped"], 1)
            self.assertEqual(first["failed"], 0)
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["upserted"], 2)

            system = manager._get_system(
                profile_user_id="u1",
                session_id="old-session",
                character_pack_id="char",
            )
            records = manager._store.list_index_records(namespace=system.namespace, with_conversation=True)
            source_ids = [record["source_id"] for record in records]
            self.assertEqual(source_ids.count("old-user"), 1)
            self.assertEqual(source_ids.count("old-assistant"), 1)
            self.assertNotIn("other-profile", source_ids)
            self.assertNotIn("tool-turn", source_ids)
            manager.close()

    def test_import_legacy_long_term_memory_preserves_old_summary_layers(self) -> None:
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
            legacy_store = _LegacyLongTermStore(
                summaries=[
                    {
                        "summary_id": "summary::anime-plan",
                        "profile_user_id": "u1",
                        "session_id": "old-session",
                        "character_pack_id": "reimu",
                        "timestamp": _ts(2026, 6, 20, 21, 0),
                        "date_label": "2026-06-20",
                        "time_of_day": "晚上",
                        "period_label": "旧约定",
                        "event_type": "preference",
                        "importance": 0.9,
                        "diary_summary": "用户和灵梦约好七月一起看新番。",
                        "key_events": ["约好七月看新番"],
                        "core_facts": ["用户喜欢聊动漫和新番"],
                        "semantic_tags": ["新番", "动漫"],
                        "memory_metadata": {"keywords": ["新番", "动漫"], "categories": ["preference"]},
                        "is_semanticized": 1,
                        "semantic_id": "semantic::anime-plan",
                        "source_ids": ["old-user", "old-assistant"],
                    },
                    {
                        "summary_id": "summary::other-profile",
                        "profile_user_id": "u2",
                        "session_id": "old-session",
                        "character_pack_id": "reimu",
                        "timestamp": _ts(2026, 6, 20, 21, 0),
                        "diary_summary": "别人的摘要不能导入。",
                    },
                ],
                semantic_summaries=[
                    {
                        "semantic_id": "semantic::anime-plan",
                        "profile_user_id": "u1",
                        "session_id": "old-session",
                        "character_pack_id": "reimu",
                        "timestamp": _ts(2026, 6, 20, 21, 0),
                        "period_start_ts": _ts(2026, 6, 20, 21, 0),
                        "period_end_ts": _ts(2026, 6, 20, 21, 5),
                        "date_label": "2026-06-20",
                        "time_of_day": "晚上",
                        "importance": 0.95,
                        "semantic_summary": "用户和灵梦有七月一起看新番的约定，用户也喜欢聊动漫偏好。",
                        "stable_facts": ["用户喜欢动漫和新番"],
                        "recurring_topics": ["七月新番"],
                        "important_people": ["灵梦"],
                        "open_loops": ["七月看新番"],
                        "semantic_tags": ["新番", "动漫", "约定"],
                        "memory_metadata": {"keywords": ["七月", "新番"], "categories": ["preference"]},
                        "source_summary_ids": ["summary::anime-plan"],
                        "reinforcement_count": 2,
                        "last_reinforced_ts": _ts(2026, 6, 20, 21, 5),
                    }
                ],
            )

            first = manager.import_legacy_long_term_memory(
                legacy_store=legacy_store,
                profile_user_id="u1",
                character_pack_id="reimu",
                batch_size=1,
            )
            second = manager.import_legacy_long_term_memory(
                legacy_store=legacy_store,
                profile_user_id="u1",
                character_pack_id="reimu",
                batch_size=1,
            )

            self.assertTrue(first["ok"], first)
            self.assertEqual(first["upserted"], 2)
            self.assertEqual(first["filtered"], 1)
            self.assertEqual(first["failed"], 0)
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["upserted"], 0)
            self.assertEqual(second["skipped"], 2)

            summary = manager._store.get_record_by_source_id("summary::anime-plan")
            semantic = manager._store.get_record_by_source_id("semantic::anime-plan")
            self.assertEqual(summary["diary_summary"], "用户和灵梦约好七月一起看新番。")
            self.assertEqual(summary["timestamp"], _ts(2026, 6, 20, 21, 0))
            self.assertEqual(summary["is_semanticized"], 1)
            self.assertEqual(summary["semantic_id"], "semantic::anime-plan")
            self.assertEqual(summary["memory_metadata"]["source_system"], "akane_legacy")
            self.assertTrue(summary["memory_metadata"]["legacy_import"])
            self.assertIn("七月一起看新番", semantic["semantic_summary"])
            manager.close()

    def test_import_legacy_long_term_memory_reports_namespace_conflict_without_overwrite(self) -> None:
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
            other_system = manager._get_system(
                profile_user_id="u1",
                session_id="old-session",
                character_pack_id="akane_v1",
            )
            manager._store.add_summary(
                namespace=other_system.namespace,
                record={
                    "summary_id": "summary::shared-id",
                    "timestamp": _ts(2026, 6, 1, 20, 0),
                    "diary_summary": "这是 Akane 域已有的摘要，不能被灵梦域覆盖。",
                },
            )
            legacy_store = _LegacyLongTermStore(
                summaries=[
                    {
                        "summary_id": "summary::shared-id",
                        "profile_user_id": "u1",
                        "session_id": "old-session",
                        "character_pack_id": "reimu",
                        "timestamp": _ts(2026, 6, 2, 20, 0),
                        "diary_summary": "这条如果强写就会跨角色域覆盖。",
                    }
                ]
            )

            result = manager.import_legacy_summaries(
                legacy_store=legacy_store,
                profile_user_id="u1",
                character_pack_id="reimu",
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["conflicted"], 1)
            self.assertEqual(result["failed"], 1)
            stored = manager._store.get_record_by_source_id("summary::shared-id")
            self.assertEqual(stored["domain_id"], "akane_v1")
            self.assertEqual(stored["diary_summary"], "这是 Akane 域已有的摘要，不能被灵梦域覆盖。")
            manager.close()

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

    def test_dual_write_preserves_qq_actor_during_metadata_update(self) -> None:
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

            created = manager.record_user_turn(
                {
                    "source_id": "qq-group-turn-1",
                    "content": "我更关注稳健型基金。",
                    "timestamp": 1_777_777_000,
                    "memory_metadata": {},
                },
                profile_user_id="qq-group-1",
                session_id="qq-group-1",
                character_pack_id="char-1",
                actor_stable_id="qq:10001",
                actor_display_name="张三",
            )
            updated = manager.update_turn_metadata(
                "qq-group-turn-1",
                {
                    "keywords": ["稳健型基金", "基金偏好"],
                    "subject_scopes": ["user"],
                    "categories": ["preference"],
                    "importance": 0.8,
                    "confidence": 0.9,
                },
                profile_user_id="qq-group-1",
                session_id="qq-group-1",
                character_pack_id="char-1",
                actor_stable_id="qq:10001",
                actor_display_name="张三",
            )

            self.assertTrue(created["ok"], created)
            self.assertTrue(updated["ok"], updated)
            stored = manager._store.get_record_by_source_id("qq-group-turn-1")
            self.assertEqual(stored["actor_id"], "qq:10001")
            self.assertEqual(stored["actor_display_name"], "张三")
            self.assertEqual(stored["memory_metadata"]["keywords"], ["稳健型基金", "基金偏好"])
            manager.close()

    def test_engine_memcore_wrappers_forward_turn_actor(self) -> None:
        manager = _ActorCaptureMemcoreManager()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = manager

        recorded = engine._record_memcore_user_turn(
            user_record={"source_id": "qq-turn-1", "content": "关注黄金", "timestamp": 100},
            profile_user_id="qq-group-1",
            session_id="qq-group-1",
            character_pack_id="char-1",
            actor_stable_id="qq:10001",
            actor_display_name="张三",
        )
        updated = engine._update_memcore_turn_metadata(
            source_id="qq-turn-1",
            memory_metadata={"keywords": ["黄金"]},
            profile_user_id="qq-group-1",
            session_id="qq-group-1",
            character_pack_id="char-1",
            actor_stable_id="qq:10001",
            actor_display_name="张三",
        )

        self.assertTrue(recorded["ok"])
        self.assertTrue(updated["ok"])
        self.assertEqual(manager.user_calls[0]["actor_stable_id"], "qq:10001")
        self.assertEqual(manager.user_calls[0]["actor_display_name"], "张三")
        self.assertEqual(manager.metadata_calls[0]["actor_stable_id"], "qq:10001")
        self.assertEqual(manager.metadata_calls[0]["actor_display_name"], "张三")

    def test_material_trace_bridge_records_safe_attachment_anchor(self) -> None:
        manager = _MaterialMemcoreManager()
        item = {
            "attachment_id": "attachment::abc",
            "attachment_handle": "img_001",
            "profile_user_id": "master",
            "session_id": "qq_group_1",
            "kind": "image",
            "origin_name": "meal.jpg",
            "mime_type": "image/jpeg",
            "status": "ready",
            "summary_title": "晚餐图片",
            "storage_relpath": "C:/Users/Lenovo/secret/meal.jpg",
            "detail": {
                "character_pack_id": "akane_v1",
                "summary": "盘子里有热汤。",
                "qq_sender_id": "10001",
                "qq_sender_label": "张三",
            },
        }

        reference = manager.record_material_reference(item=item, timestamp=100)
        cleanup = manager.record_material_cleanup(
            item=item,
            timestamp=120,
            reason="聊完了",
            delete_storage=True,
        )

        self.assertTrue(reference["ok"])
        self.assertTrue(cleanup["ok"])
        self.assertEqual(manager.system_calls[0]["character_pack_id"], "akane_v1")
        self.assertEqual(manager.system.references[0]["file_id"], "img_001")
        self.assertEqual(manager.system.references[0]["file_status"], "ready")
        self.assertEqual(manager.system.references[0]["derived_status"], "ready")
        self.assertEqual(manager.system.references[0]["actor"].stable_id, "qq:10001")
        self.assertEqual(manager.system.references[0]["actor"].display_name, "张三")
        self.assertIn("reference:ready:100", manager.system.references[0]["source_id"])
        self.assertNotIn("storage_relpath", manager.system.references[0])
        self.assertNotIn("C:/Users", str(manager.system.references[0]))
        self.assertEqual(manager.system.cleanups[0]["file_status"], "deleted")
        self.assertEqual(manager.system.cleanups[0]["reason"], "聊完了")

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

            def get_visible_episodic_summaries(
                self, profile_user_id: str, *, limit: int, character_pack_id: str = ""
            ) -> list[dict]:
                return []

            def get_recent_semantic_summaries(
                self, profile_user_id: str, *, limit: int, character_pack_id: str = ""
            ) -> list[dict]:
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
        with patch.object(config, "MEMORY_BACKEND", "dual"), patch.object(config, "MEMCORE_SHADOW_COMPARE", True):
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
        self.assertEqual(call["exclude_source_ids"], ["current", "extra-visible"])
        self.assertEqual(engine.store.legacy_visible_reads, [])
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

    def test_retrieve_memory_tool_does_not_fallback_to_legacy_when_memcore_fails(self) -> None:
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

        self.assertIn("没有找到足以回答主人问题", result.followup_context)
        self.assertEqual(retrieval_service.calls, [])
        state = result.state_updates["memory_retrieval"]
        self.assertEqual(state["retrieval_backend"], "memcore")
        self.assertEqual(state["confirmed_snippets"], [])
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

    def test_final_prompt_context_does_not_fallback_to_legacy_when_memcore_fails(self) -> None:
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": False,
                "status": "failed",
                "reason": "boom",
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
        self.assertEqual(captured["raw_text"], "")
        self.assertEqual(captured["episodic_summary_text"], "")
        self.assertEqual(captured["semantic_summary_text"], "")
        self.assertNotIn("LEGACY", repr(captured))

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

    def test_read_memory_timeline_adapter_does_not_need_legacy_service_in_memcore_mode(self) -> None:
        memcore_manager = _TimelineMemcoreManager()
        service = MemcoreTimelineToolService(legacy_service=None, memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            periods = service.normalize_time_periods(["晚上", "morning", "凌晨", "unknown"])
            result = service.read(
                profile_user_id="u1",
                character_pack_id="char",
                date_from="2026-06-13",
                date_to="2026-06-13",
                time_periods=periods,
                exclude_source_ids=["current-query"],
            )
            rendered = service.render_tool_context(result)

        self.assertEqual(periods, ["midnight", "morning", "night"])
        self.assertEqual(result["status"], "ok")
        self.assertIn("MEMCORE TIMELINE", rendered)
        self.assertEqual(service.build_acquaintance_prompt(profile_user_id="u1", now_ts=100), "")
        self.assertEqual(memcore_manager.calls[0]["exclude_source_ids"], ["current-query"])

    def test_read_memory_timeline_tool_does_not_fallback_to_legacy_when_memcore_unavailable(self) -> None:
        legacy = _TimelineLegacyService()
        memcore_manager = _TimelineMemcoreManager()
        memcore_manager.available = False
        service = MemcoreTimelineToolService(legacy_service=legacy, memcore_manager=memcore_manager)
        handler = ReadMemoryTimelineToolHandler(timeline_service=service)
        call = handler.normalize_call({"type": "read_memory_timeline", "date": "2026-06-13"})
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

        self.assertEqual(legacy.read_calls, [])
        self.assertNotIn("LEGACY TIMELINE", result.followup_context)
        self.assertEqual(result.state_updates["memory_timeline"]["backend"], "memcore")
        self.assertEqual(result.state_updates["memory_timeline"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
