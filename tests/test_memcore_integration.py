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
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.memcore_integration.manager import (
    MemcoreManager,
    normalize_memory_backend,
    resolve_memcore_provider_profile,
)
from companion_v01.memcore_integration.adapters import build_akane_token_counter
from companion_v01.memcore_integration.timeline import MemcoreTimelineToolService
from companion_v01.prompt_profiles import PromptModule
from companion_v01.retrieval_types import RetrievalPipelineResult
from companion_v01 import retrieval_engine
from companion_v01.store import MemoryStore
from companion_v01.tool_invocation import NATIVE_OPENAI, TOOL_SOURCE_FIELD
from companion_v01.tool_runtime import ReadMemoryTimelineToolHandler, ToolExecutionContext, ToolExecutionResult


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
        self.calls: list[dict[str, object]] = []
        self.persona = SimpleNamespace(
            final_debug_mode_prompt="debug mode",
            final_fast_mode_prompt="fast mode",
        )

    def build_final_generation_context(self, **kwargs):
        self.kwargs = dict(kwargs)
        self.calls.append(dict(kwargs))
        linear_timeline_turn = bool(
            kwargs.get("current_message_in_raw")
            and not str(kwargs.get("memory_text") or "").strip()
        )
        ephemeral_parts = [
            str(kwargs.get("memory_text") or "").strip(),
            str(kwargs.get("volatile_extra_context") or "").strip(),
            str(kwargs.get("current_visual_context") or "").strip(),
        ]
        ephemeral_text = "\n\n".join(part for part in ephemeral_parts if part)
        return {
            "system_prompt": "system",
            "user_prompt": str(kwargs.get("current_message_text") or "").strip(),
            "ephemeral_turns": [{"role": "user", "content": ephemeral_text}] if ephemeral_text else [],
            "fallback": {"speech": "", "tool_call": None},
            "visual_defaults": dict(kwargs.get("visual_defaults") or {}),
            "debug_enabled": bool(kwargs.get("debug_enabled")),
            "tool_prompt_context": str(kwargs.get("tool_prompt_context") or ""),
            "system_extra_blocks": [],
            "history_turns": [dict(turn) for turn in list(kwargs.get("history_turns") or [])],
            "prompt_audit_sections": [],
            "linear_timeline_turn": linear_timeline_turn,
        }


class _PromptContextMemcoreManager:
    enabled = True
    available = True

    def __init__(self, payload: dict[str, object], projection_payload: dict[str, object] | None = None) -> None:
        self.payload = payload
        self.projection_payload = projection_payload
        self.projection_calls: list[dict[str, object]] = []
        self.compare_calls: list[dict[str, object]] = []

    def build_context_projection(self, **kwargs) -> dict[str, object]:
        self.projection_calls.append(dict(kwargs))
        return dict(
            self.projection_payload
            or {
                "ok": False,
                "status": "failed",
                "reason": "projection_build_failed",
            }
        )

    def compare_context_projection(self, **kwargs) -> dict[str, object]:
        self.compare_calls.append(dict(kwargs))
        return {
            "ok": True,
            "status": "match",
            "provider_profile": "openai_chat",
            "projection_hash": "a" * 64,
            "actual_history_hash": "a" * 64,
            "strict_prefix": True,
            "first_divergence_index": -1,
            "divergence_reason": "",
            "source_ids": ["previous"],
        }


class _PromptContextEngine:
    def __init__(self, *, memcore_manager: _PromptContextMemcoreManager) -> None:
        self.resource_manifest = None
        self.store = SimpleNamespace()
        self.vision_service = None
        self.memcore_manager = memcore_manager
        self.prompt_builder = _CapturePromptBuilder()
        self.llm = SimpleNamespace(
            chat_provider_protocol=lambda **_kwargs: "openai",
            normalize_chat_history_turns=lambda turns, **_kwargs: [dict(turn) for turn in turns],
        )

    def _resolve_client_protocol_context(self, _payload) -> ClientProtocolContext:
        return ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

    def _get_prompt_profile_registry(self):
        return SimpleNamespace(resolve=lambda _client_context, **_kwargs: _FinalPromptProfile())

    def _get_user_runtime_projection(self, _profile_user_id: str) -> dict[str, list[object]]:
        return {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }

    def _split_history_records(self, **kwargs):
        return [], {
            "source_id": "current",
            "role": "user",
            "content": str(kwargs.get("user_message") or ""),
            "timestamp": int(kwargs.get("now_ts") or 1712400000),
        }

    def _render_current_message_line(self, **kwargs) -> str:
        record = kwargs.get("current_user_record") or {}
        return f"User: {record.get('content') or ''}"

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

    def _build_extra_context_audit_sections(self, candidates) -> list[dict[str, str]]:
        return [
            {"name": str(name), "text": str(text)}
            for name, text in candidates
            if str(name).strip() and str(text).strip()
        ]

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
            "anchor_source_id": str(kwargs.get("anchor_source_id") or ""),
            "before_turns": int(kwargs.get("before_turns") or 0),
            "after_turns": int(kwargs.get("after_turns") or 0),
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
            "anchor_source_id": str(kwargs.get("anchor_source_id") or ""),
            "before_turns": int(kwargs.get("before_turns") or 0),
            "after_turns": int(kwargs.get("after_turns") or 0),
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


class _ActorCaptureMemcoreManager:
    enabled = True
    available = True

    def __init__(self) -> None:
        self.user_calls: list[dict[str, object]] = []
        self.metadata_calls: list[dict[str, object]] = []

    def begin_input_turn(self, record: dict[str, object], **kwargs) -> dict[str, object]:
        self.user_calls.append({"record": dict(record), **dict(kwargs)})
        return {"ok": True, "status": "opened", "turn_id": "turn-1"}

    def append_standalone_message(
        self, record: dict[str, object], *, role: str, **kwargs
    ) -> dict[str, object]:
        self.user_calls.append({"record": dict(record), "role": role, **dict(kwargs)})
        return {"ok": True, "status": "recorded"}

    def stage_turn_metadata(
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
    def test_provider_protocol_maps_to_projection_profile_without_bot_specific_branching(self) -> None:
        self.assertEqual(resolve_memcore_provider_profile("openai"), "openai_chat")
        self.assertEqual(resolve_memcore_provider_profile("responses"), "openai_chat")
        self.assertEqual(resolve_memcore_provider_profile("ollama"), "openai_chat")
        self.assertEqual(resolve_memcore_provider_profile("anthropic"), "anthropic_messages")
        self.assertEqual(resolve_memcore_provider_profile("canonical"), "canonical_user_assistant")
        self.assertEqual(resolve_memcore_provider_profile("finance_bot"), "")

    def test_compaction_token_difference_policy_is_host_configurable(self) -> None:
        manager = MemcoreManager.__new__(MemcoreManager)
        manager.visible_scope = "user"
        manager.enable_flavor = True
        fake_memcore = SimpleNamespace(
            DEFAULT_CATEGORIES=("tool_trace",),
            MemoryConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        )
        with (
            patch.object(config, "MEMCORE_RAW_TOKEN_TRIGGER", 24000, create=True),
            patch.object(config, "MEMCORE_RAW_TOKEN_BATCH_RATIO", 0.67, create=True),
            patch.object(config, "MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET", 3200, create=True),
        ):
            memory_config = manager._build_memory_config(fake_memcore)
        self.assertEqual(memory_config.raw_token_trigger, 24000)
        self.assertEqual(memory_config.raw_token_batch_ratio, 0.67)
        self.assertEqual(memory_config.retrieval_result_token_budget, 3200)
        self.assertFalse(hasattr(memory_config, "raw_trigger_count"))
        self.assertFalse(hasattr(memory_config, "summary_batch_size"))

    def test_akane_token_counter_is_explicitly_estimated(self) -> None:
        counter = build_akane_token_counter()
        self.assertEqual(counter.quality, "estimated")
        self.assertEqual(counter.count_text("中文ab12"), 3)
        self.assertEqual(counter.count_text(""), 0)

    def test_manager_injects_single_v2_config_and_estimated_counter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v2.db",
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                system = manager._get_system(
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertEqual(system.token_counter.quality, "estimated")
                self.assertEqual(system.config.raw_token_trigger, config.MEMCORE_RAW_TOKEN_TRIGGER)
                self.assertEqual(
                    system.config.retrieval_result_token_budget,
                    config.MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET,
                )
                self.assertFalse(hasattr(system.config, "raw_trigger_count"))
                self.assertFalse(hasattr(system.config, "summary_batch_size"))
            finally:
                manager.close()

    def test_process_runtime_uses_configured_compaction_workers(self) -> None:
        from companion_v01.memcore_integration import manager as manager_module

        created: list[int] = []

        class _Runtime:
            def __init__(self, *, compaction_workers: int) -> None:
                created.append(compaction_workers)

            def close(self, *, wait: bool) -> None:
                self.wait = wait

        fake_memcore = SimpleNamespace(MemCoreRuntime=_Runtime)
        with (
            patch.object(config, "MEMCORE_COMPACTION_WORKERS", 3, create=True),
            patch.object(manager_module, "_PROCESS_RUNTIME", None),
            patch.object(manager_module, "_PROCESS_RUNTIME_LEASES", 0),
        ):
            runtime = manager_module._acquire_process_runtime(fake_memcore)
            manager_module._release_process_runtime(runtime)
        self.assertEqual(created, [3])

    def test_projection_facades_delegate_to_memcore_and_return_safe_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "projection-stimulus", "content": "测试投影", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                comparison = manager.compare_context_projection(
                    provider_profile="responses",
                    actual_history_messages=list(projection["payloads"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                divergence = manager.compare_context_projection(
                    provider_profile="responses",
                    actual_history_messages=[{"role": "user", "content": "different private text"}],
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                current_messages = [
                    message
                    for message in projection["messages"]
                    if "projection-stimulus" in message["source_ids"]
                ]
                recorded = manager.record_request_projection(
                    turn_id=str(opened["turn_id"]),
                    provider_profile="responses",
                    turn_messages=current_messages,
                    history_messages=list(projection["payloads"]),
                    attempt=1,
                    model_route={"protocol": "responses", "model": "safe-model-id"},
                    system_prefix="stable system prefix",
                    tool_schema=[{"name": "safe_tool"}],
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                    created_at=101,
                )
            finally:
                manager.close()

        self.assertTrue(projection["ok"], projection)
        self.assertEqual(projection["provider_profile"], "openai_chat")
        self.assertEqual(projection["source_ids"], ["projection-stimulus"])
        self.assertRegex(projection["stable_prefix_hash"], r"^[a-f0-9]{64}$")
        self.assertTrue(comparison["strict_prefix"], comparison)
        self.assertEqual(comparison["projection_hash"], comparison["actual_history_hash"])
        self.assertNotIn("actual_history_messages", comparison)
        self.assertFalse(divergence["strict_prefix"])
        self.assertEqual(divergence["first_divergence_index"], 0)
        self.assertEqual(divergence["divergence_reason"], "message_mismatch")
        self.assertNotIn("different private text", repr(divergence))
        self.assertTrue(recorded["ok"], recorded)
        self.assertEqual(recorded["status"], "recorded")
        self.assertEqual(recorded["source_ids"], ["projection-stimulus"])
        self.assertRegex(recorded["full_prefix_hash"], r"^[a-f0-9]{64}$")
        self.assertNotIn("history_messages", recorded)
        self.assertNotIn("stable system prefix", repr(recorded))

    def test_real_request_observer_freezes_actual_user_and_raw_assistant_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "wire-user-1", "content": "第一问", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                current_messages = [
                    dict(message)
                    for message in projection["messages"]
                    if message.get("turn_id") == opened["turn_id"]
                ]
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.llm = SimpleNamespace(supports_request_observer=True)
                engine.memcore_manager = manager
                generation_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": current_messages,
                    }
                }
                observer = engine._build_memcore_request_observer(
                    generation_context=generation_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                actual_user = {"role": "user", "content": "[100] message.user\ncontent:\n第一问"}
                ephemeral = {"role": "user", "content": "本轮状态"}
                missing_slot = observer(
                    {
                        "protocol": "responses",
                        "history_messages": [actual_user, ephemeral],
                        "audit_history_messages": [actual_user, ephemeral],
                    }
                )
                self.assertEqual(missing_slot["reason"], "persistent_turn_messages_missing")
                observed = observer(
                    {
                        "protocol": "responses",
                        "model_route": {"protocol": "responses", "model": "safe-model"},
                        "system_prefix": "stable system",
                        "tool_schema": [{"name": "retrieve_memory"}],
                        "history_messages": [
                            {"role": "user", "content": "stable context"},
                            actual_user,
                            ephemeral,
                        ],
                        "persistent_turn_messages": [actual_user],
                        "audit_history_messages": [
                            {"role": "user", "content": "stable context"},
                            actual_user,
                            ephemeral,
                        ],
                    }
                )
                self.assertTrue(observed["ok"], observed)

                completed = manager.complete_input_turn(
                    turn_id=str(opened["turn_id"]),
                    assistant_record={"source_id": "wire-final-1", "content": "第一答", "timestamp": 101},
                    memory_metadata={"topic_terms": ["第一问"]},
                    provider_output_raw='{"speech":"第一答","memory_metadata":{}}',
                    provider_profile="responses",
                    provider_projection={
                        "role": "assistant",
                        "content": '{"speech":"第一答","memory_metadata":{}}',
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(completed["ok"], completed)
                manager.begin_input_turn(
                    {"source_id": "wire-user-2", "content": "第二问", "timestamp": 102},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                next_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
            finally:
                manager.close()

        payloads = list(next_projection["payloads"])
        self.assertEqual(payloads[0], actual_user)
        self.assertEqual(payloads[1], {"role": "assistant", "content": '{"speech":"第一答","memory_metadata":{}}'})
        self.assertNotIn("本轮状态", repr(payloads))
        self.assertEqual(generation_context["memcore_request_projection"]["status"], "recorded")

    def test_legacy_json_tool_round_freezes_the_real_linear_provider_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "legacy-user-1", "content": "查一下北京天气", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.llm = SimpleNamespace(
                    supports_request_observer=True,
                    chat_provider_protocol=lambda **_kwargs: "responses",
                )
                engine.memcore_manager = manager
                initial_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                first_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": [
                            dict(message)
                            for message in initial_projection["messages"]
                            if message.get("turn_id") == opened["turn_id"]
                        ],
                    }
                }
                first_observer = engine._build_memcore_request_observer(
                    generation_context=first_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                actual_user = {
                    "role": "user",
                    "content": "[100] message.user\ncontent:\n查一下北京天气",
                }
                first = first_observer(
                    {
                        "protocol": "responses",
                        "model_route": {"protocol": "responses", "model": "gpt-test"},
                        "system_prefix": "stable system",
                        "tool_schema": [],
                        "history_messages": [actual_user],
                        "persistent_turn_messages": [actual_user],
                        "audit_history_messages": [actual_user],
                    }
                )
                self.assertTrue(first["ok"], first)

                batch = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "legacy-call-1",
                            "tool_input": {"query": "北京天气"},
                            "result": "北京今天晴，25°C。",
                            "source": "search-api",
                            "timestamp": 101,
                            "source_id_prefix": "legacy-weather",
                            "result_status": "success",
                        }
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                trace_ids = [
                    source_id
                    for exchange in batch["exchanges"]
                    for source_id in (exchange["tool_use_source_id"], exchange["tool_result_source_id"])
                ]
                provider_raw = (
                    '{"speech":"我查一下。","tool_call":{"type":"web_search",'
                    '"query":"北京天气"},"memory_metadata":{}}'
                )
                post_turns: list[dict[str, object]] = []
                projected = engine._append_tool_history_batch(
                    tool_history_turns=post_turns,
                    items=[
                        (
                            {"type": "web_search", "query": "北京天气"},
                            ToolExecutionResult(
                                tool_type="web_search",
                                followup_context="北京今天晴，25°C。",
                            ),
                            "北京今天晴，25°C。",
                            "",
                        )
                    ],
                    trace_source_ids=trace_ids,
                    provider_output_raw=provider_raw,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(projected["ok"], projected)
                self.assertEqual(post_turns[0], {"role": "assistant", "content": provider_raw})
                self.assertEqual(post_turns[1]["role"], "user")
                self.assertIn("[tool.result]", str(post_turns[1]["content"]))
                self.assertNotIn("tool_calls", post_turns[0])

                tool_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                second_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": [
                            dict(message)
                            for message in tool_projection["messages"]
                            if message.get("turn_id") == opened["turn_id"]
                        ],
                    }
                }
                second_observer = engine._build_memcore_request_observer(
                    generation_context=second_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                actual_second_history = [actual_user, *post_turns]
                second = second_observer(
                    {
                        "protocol": "responses",
                        "model_route": {"protocol": "responses", "model": "gpt-test"},
                        "system_prefix": "stable system",
                        "tool_schema": [],
                        "history_messages": actual_second_history,
                        "persistent_turn_messages": actual_second_history,
                        "audit_history_messages": actual_second_history,
                    }
                )
                self.assertTrue(second["ok"], second)

                completed = manager.complete_input_turn(
                    turn_id=str(opened["turn_id"]),
                    assistant_record={"source_id": "legacy-final-1", "content": "北京今天晴。", "timestamp": 102},
                    memory_metadata={"entity_anchors": ["北京"], "topic_terms": ["天气"]},
                    provider_output_raw='{"speech":"北京今天晴。","memory_metadata":{}}',
                    provider_profile="responses",
                    provider_projection={
                        "role": "assistant",
                        "content": '{"speech":"北京今天晴。","memory_metadata":{}}',
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(completed["ok"], completed)
                manager.begin_input_turn(
                    {"source_id": "legacy-user-2", "content": "继续", "timestamp": 103},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                next_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
            finally:
                manager.close()

        self.assertEqual(next_projection["payloads"][:3], actual_second_history)

    def test_final_completion_failure_preserves_reply_and_aborts_open_turn(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        completion_calls: list[dict[str, object]] = []
        abort_calls: list[dict[str, object]] = []

        def fail_completion(**kwargs):
            completion_calls.append(dict(kwargs))
            return {"ok": False, "status": "failed", "reason": "private path must not escape"}

        def abort_turn(**kwargs):
            abort_calls.append(dict(kwargs))
            return {"ok": True, "status": "aborted"}

        engine._complete_memcore_input_turn = fail_completion
        engine._abort_memcore_input_turn = abort_turn
        final_output = {"speech": "这是模型已经生成的真实回复。"}

        completed = engine._finalize_memcore_input_turn_for_delivery(
            final_output=final_output,
            turn_id="turn-final-failure",
            assistant_record={"source_id": "assistant-1", "content": final_output["speech"]},
            memory_metadata={},
            provider_output_raw='{"speech":"这是模型已经生成的真实回复。"}',
            chat_model_override="",
            annotation_status="accepted_model",
            profile_user_id="u1",
            session_id="s1",
            character_pack_id="char",
        )

        self.assertFalse(completed)
        self.assertEqual(len(completion_calls), 2)
        self.assertEqual(len(abort_calls), 1)
        self.assertEqual(final_output["speech"], "这是模型已经生成的真实回复。")
        self.assertEqual(final_output["_memcore_failure"]["reason"], "input_turn_completion_failed")
        self.assertEqual(final_output["_memcore_failure"]["recovery_status"], "aborted")
        self.assertNotIn("private path", repr(final_output["_memcore_failure"]))

    def test_aborted_input_turn_schedules_compaction_with_actual_provider_protocol(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        abort_calls: list[dict[str, object]] = []
        compact_calls: list[dict[str, object]] = []

        def abort_turn(**kwargs):
            abort_calls.append(dict(kwargs))
            return {"ok": True, "status": "aborted"}

        def compact_due_background(**kwargs):
            compact_calls.append(dict(kwargs))
            return {"ok": True, "status": "scheduled"}

        manager = SimpleNamespace(
            abort_input_turn=abort_turn,
            compact_due_background=compact_due_background,
        )
        engine.memcore_manager = manager
        engine.llm = SimpleNamespace(
            chat_provider_protocol=lambda **_kwargs: "responses",
        )
        engine._memcore_manager_if_enabled = lambda: manager

        result = engine._abort_memcore_input_turn(
            turn_id="turn-aborted",
            reason="assistant_turn_not_persisted",
            chat_model_override="finance-model",
            profile_user_id="u1",
            session_id="group:1",
            character_pack_id="char",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(abort_calls), 1)
        self.assertEqual(len(compact_calls), 1)
        self.assertEqual(compact_calls[0]["provider_profile"], "responses")
        self.assertEqual(result["compaction"]["status"], "scheduled")

    def test_legacy_anthropic_tool_result_is_normalized_to_neutral_user_history(self) -> None:
        class AnthropicProjectionManager:
            @staticmethod
            def build_context_projection(**_kwargs):
                return {
                    "ok": True,
                    "provider_profile": "anthropic_messages",
                    "messages": [
                        {
                            "payload": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "legacy-anthropic-call",
                                        "name": "web_search",
                                        "input": {"query": "天气"},
                                    }
                                ],
                            },
                            "source_ids": ["legacy-anthropic-use"],
                        },
                        {
                            "payload": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "legacy-anthropic-call",
                                        "content": "晴，25°C。",
                                    }
                                ],
                            },
                            "source_ids": ["legacy-anthropic-result"],
                        },
                    ],
                }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = SimpleNamespace(chat_provider_protocol=lambda **_kwargs: "anthropic_messages")
        engine.memcore_manager = AnthropicProjectionManager()
        history: list[dict[str, object]] = []
        raw = '{"speech":"我查一下。","tool_call":{"type":"web_search","query":"天气"}}'

        projected = engine._append_tool_history_batch(
            tool_history_turns=history,
            items=[
                (
                    {"type": "web_search", "query": "天气"},
                    ToolExecutionResult(tool_type="web_search", followup_context="晴，25°C。"),
                    "晴，25°C。",
                    "",
                )
            ],
            trace_source_ids=["legacy-anthropic-use", "legacy-anthropic-result"],
            provider_output_raw=raw,
            profile_user_id="u1",
            session_id="s1",
            character_pack_id="char",
        )

        self.assertTrue(projected["ok"], projected)
        self.assertEqual(history[0], {"role": "assistant", "content": raw})
        self.assertEqual(history[1]["role"], "user")
        self.assertIn("call_id: legacy-anthropic-call", str(history[1]["content"]))
        self.assertNotIn("tool_result", repr(history[1]))

    def test_parallel_native_tool_wire_is_restored_in_order_on_next_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "parallel-user-1", "content": "同时查天气和新闻", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                initial_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.llm = SimpleNamespace(supports_request_observer=True)
                engine.memcore_manager = manager
                generation_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": [
                            dict(message)
                            for message in initial_projection["messages"]
                            if message.get("turn_id") == opened["turn_id"]
                        ],
                    }
                }
                observer = engine._build_memcore_request_observer(
                    generation_context=generation_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                runtime = LLMRuntime.__new__(LLMRuntime)
                bundle = SimpleNamespace(
                    client=SimpleNamespace(_akane_protocol="responses", protocol="responses"),
                    model="gpt-test",
                )
                actual_user = {
                    "role": "user",
                    "content": "[100] message.user\ncontent:\n同时查天气和新闻",
                }
                first_chat_payload = {
                    "model": "gpt-test",
                    "messages": [
                        {"role": "system", "content": "stable system"},
                        actual_user,
                    ],
                    "tools": [],
                }
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload=first_chat_payload,
                    observer=observer,
                    persistent_turn_messages=[actual_user],
                )

                batch = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "weather",
                            "tool_call_id": "call-weather",
                            "tool_input": {"city": "北京", "unit": "c"},
                            "result": "晴，25°C。",
                            "source": "weather-api",
                            "timestamp": 101,
                            "source_id_prefix": "parallel-weather",
                            "result_status": "success",
                        },
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "call-news",
                            "tool_input": {"query": "北京新闻", "limit": 3},
                            "result": "今天有一条公开新闻。",
                            "source": "search-api",
                            "timestamp": 101,
                            "source_id_prefix": "parallel-news",
                            "result_status": "success",
                        },
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(batch["ok"], batch)
                tool_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                current_wire_messages = [
                    dict(message["payload"])
                    for message in tool_projection["messages"]
                    if message.get("turn_id") == opened["turn_id"]
                ]
                second_generation_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": [
                            dict(message)
                            for message in tool_projection["messages"]
                            if message.get("turn_id") == opened["turn_id"]
                        ],
                    }
                }
                second_observer = engine._build_memcore_request_observer(
                    generation_context=second_generation_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                second_chat_payload = {
                    "model": "gpt-test",
                    "messages": [
                        {"role": "system", "content": "stable system"},
                        *current_wire_messages,
                    ],
                    "tools": [],
                }
                expected_responses_wire = runtime._responses_payload_from_chat(second_chat_payload)["input"]
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload=second_chat_payload,
                    observer=second_observer,
                    persistent_turn_messages=current_wire_messages,
                )

                completed = manager.complete_input_turn(
                    turn_id=str(opened["turn_id"]),
                    assistant_record={
                        "source_id": "parallel-final-1",
                        "content": "北京天气晴朗，也有一条公开新闻。",
                        "timestamp": 102,
                    },
                    memory_metadata={
                        "memory_facets": ["knowledge"],
                        "about_roles": ["external"],
                        "entity_anchors": ["北京"],
                        "topic_terms": ["天气", "新闻"],
                    },
                    provider_output_raw='{"speech":"北京天气晴朗，也有一条公开新闻。","memory_metadata":{}}',
                    provider_profile="responses",
                    provider_projection={
                        "role": "assistant",
                        "content": '{"speech":"北京天气晴朗，也有一条公开新闻。","memory_metadata":{}}',
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(completed["ok"], completed)
                manager.begin_input_turn(
                    {"source_id": "parallel-user-2", "content": "继续", "timestamp": 103},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                next_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
            finally:
                manager.close()

        restored = [
            dict(message["payload"])
            for message in next_projection["messages"]
            if message.get("turn_id") == opened["turn_id"]
        ]
        self.assertEqual(restored[0], actual_user)
        self.assertEqual([call["id"] for call in restored[1]["tool_calls"]], ["call-weather", "call-news"])
        self.assertEqual(
            [call["function"]["arguments"] for call in restored[1]["tool_calls"]],
            ['{"city":"北京","unit":"c"}', '{"limit":3,"query":"北京新闻"}'],
        )
        self.assertEqual([message["tool_call_id"] for message in restored[2:4]], ["call-weather", "call-news"])
        self.assertEqual([message["content"] for message in restored[2:4]], ["晴，25°C。", "今天有一条公开新闻。"])
        self.assertEqual(
            restored[4],
            {
                "role": "assistant",
                "content": '{"speech":"北京天气晴朗，也有一条公开新闻。","memory_metadata":{}}',
            },
        )
        self.assertEqual(
            [item.get("type") for item in expected_responses_wire],
            [None, "function_call", "function_call", "function_call_output", "function_call_output"],
        )
        self.assertEqual(generation_context["memcore_request_projection"]["attempt"], 1)
        self.assertEqual(second_generation_context["memcore_request_projection"]["attempt"], 2)

    def test_tool_loaded_image_is_appended_after_tool_result_without_mutating_frozen_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "image-user-1", "content": "看看工具找到的图", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                initial_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.llm = SimpleNamespace(supports_request_observer=True)
                engine.memcore_manager = manager
                runtime = LLMRuntime.__new__(LLMRuntime)
                bundle = SimpleNamespace(
                    client=SimpleNamespace(_akane_protocol="responses", protocol="responses"),
                    model="gpt-test",
                )
                actual_user = {
                    "role": "user",
                    "content": "[100] message.user\ncontent:\n看看工具找到的图",
                }
                first_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": [
                            dict(message)
                            for message in initial_projection["messages"]
                            if message.get("turn_id") == opened["turn_id"]
                        ],
                    }
                }
                first_observer = engine._build_memcore_request_observer(
                    generation_context=first_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload={
                        "model": "gpt-test",
                        "messages": [{"role": "system", "content": "stable system"}, actual_user],
                        "tools": [],
                    },
                    observer=first_observer,
                    persistent_turn_messages=[actual_user],
                )

                batch = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "load_material",
                            "tool_call_id": "call-image",
                            "tool_input": {"file_id": "img_001"},
                            "result": "图片已加载到模型多模态通道。",
                            "source": "load_material",
                            "timestamp": 101,
                            "source_id_prefix": "image-load",
                            "result_status": "success",
                        }
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(batch["ok"], batch)
                trace_ids = [
                    source_id
                    for exchange in batch["exchanges"]
                    for source_id in (exchange["tool_use_source_id"], exchange["tool_result_source_id"])
                ]
                media = manager.append_turn_media_input(
                    items=[
                        {
                            "attachment_id": "attachment-1",
                            "attachment_handle": "img_001",
                            "mime_type": "image/png",
                            "data_url": "data:image/png;base64,MUST_NOT_BE_STORED",
                        }
                    ],
                    turn_id=str(opened["turn_id"]),
                    related_source_ids=trace_ids,
                    timestamp=102,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(media["ok"], media)
                current_projection = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                current_messages = [
                    dict(message)
                    for message in current_projection["messages"]
                    if message.get("turn_id") == opened["turn_id"]
                ]
                second_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": current_messages,
                    }
                }
                second_observer = engine._build_memcore_request_observer(
                    generation_context=second_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                native_history: list[dict] = []
                image_input = {
                    "attachment_id": "attachment-1",
                    "attachment_handle": "img_001",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
                projected = engine._append_tool_history_batch(
                    tool_history_turns=native_history,
                    items=[
                        (
                            {
                                "type": "load_material",
                                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                            },
                            ToolExecutionResult(
                                tool_type="load_material",
                                followup_context="图片已加载到模型多模态通道。",
                                model_image_inputs=[image_input],
                            ),
                            "图片已加载到模型多模态通道。",
                            "",
                        )
                    ],
                    trace_source_ids=trace_ids,
                    media_source_ids=[media["source_id"]],
                    model_image_inputs=[image_input],
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(projected["ok"], projected)
                second_chat_payload = {
                    "model": "gpt-test",
                    "messages": [
                        {"role": "system", "content": "stable system"},
                        actual_user,
                        *native_history,
                    ],
                    "tools": [],
                }
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload=second_chat_payload,
                    observer=second_observer,
                    persistent_turn_messages=[actual_user, *native_history],
                )
                frozen = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
            finally:
                manager.close()

        frozen_turn = [
            dict(message)
            for message in frozen["messages"]
            if message.get("turn_id") == opened["turn_id"]
        ]
        self.assertEqual(frozen_turn[0]["payload"], actual_user)
        media_message = next(
            message for message in frozen_turn if media["source_id"] in message.get("source_ids", [])
        )
        persisted = str(media_message["payload"])
        self.assertIn("omitted from persistent history", persisted)
        self.assertNotIn("AAAA", persisted)
        self.assertNotIn("MUST_NOT_BE_STORED", persisted)
        self.assertTrue(second_context["memcore_request_projection"]["media_omitted"])

    def test_native_tool_history_rebuilds_complete_open_turn_across_two_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "media-user-1", "content": "分离后把两个文件发给我", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.llm = SimpleNamespace(
                    supports_request_observer=True,
                    chat_provider_protocol=lambda **_kwargs: "responses",
                )
                engine.memcore_manager = manager
                runtime = LLMRuntime.__new__(LLMRuntime)
                bundle = SimpleNamespace(
                    client=SimpleNamespace(_akane_protocol="responses", protocol="responses"),
                    model="gpt-test",
                )
                actual_user = {
                    "role": "user",
                    "content": "[100] message.user\ncontent:\n分离后把两个文件发给我",
                }

                initial = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                first_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": [
                            dict(message)
                            for message in initial["messages"]
                            if message.get("turn_id") == opened["turn_id"]
                        ],
                    }
                }
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload={
                        "model": "gpt-test",
                        "messages": [{"role": "system", "content": "stable system"}, actual_user],
                        "tools": [],
                    },
                    observer=engine._build_memcore_request_observer(
                        generation_context=first_context,
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    ),
                    persistent_turn_messages=[actual_user],
                )

                intermediate = manager.append_turn_intermediate(
                    {"source_id": "media-preface-1", "content": "我先把人声和伴奏拆出来。", "timestamp": 101},
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(intermediate["ok"], intermediate)
                first_batch = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "separate_audio_stems",
                            "tool_call_id": "call-separate",
                            "tool_input": {"file_id": "att_audio"},
                            "result": "已生成 gen_vocals 和 gen_instrumental。",
                            "source": "local_media_executor",
                            "timestamp": 102,
                            "source_id_prefix": "separate",
                            "result_status": "success",
                        }
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                first_trace_ids = [
                    source_id
                    for exchange in first_batch["exchanges"]
                    for source_id in (exchange["tool_use_source_id"], exchange["tool_result_source_id"])
                ]
                native_history: list[dict] = []
                first_projected = engine._append_tool_history_batch(
                    tool_history_turns=native_history,
                    items=[
                        (
                            {
                                "type": "separate_audio_stems",
                                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                            },
                            ToolExecutionResult(
                                tool_type="separate_audio_stems",
                                followup_context="已生成 gen_vocals 和 gen_instrumental。",
                            ),
                            "已生成 gen_vocals 和 gen_instrumental。",
                            "",
                        )
                    ],
                    trace_source_ids=first_trace_ids,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(first_projected["ok"], first_projected)
                self.assertEqual(native_history[0]["content"], "我先把人声和伴奏拆出来。")

                after_first = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                first_turn_messages = [
                    dict(message)
                    for message in after_first["messages"]
                    if message.get("turn_id") == opened["turn_id"]
                ]
                second_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": first_turn_messages,
                    }
                }
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload={
                        "model": "gpt-test",
                        "messages": [
                            {"role": "system", "content": "stable system"},
                            actual_user,
                            *native_history,
                        ],
                        "tools": [],
                    },
                    observer=engine._build_memcore_request_observer(
                        generation_context=second_context,
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    ),
                    persistent_turn_messages=[actual_user, *native_history],
                )

                second_batch = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "send_file",
                            "tool_call_id": "call-send",
                            "tool_input": {"targets": ["gen_vocals", "gen_instrumental"]},
                            "result": "两个文件已发送。",
                            "source": "qq",
                            "timestamp": 103,
                            "source_id_prefix": "send-files",
                            "result_status": "success",
                        }
                    ],
                    turn_id=str(opened["turn_id"]),
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                second_trace_ids = [
                    source_id
                    for exchange in second_batch["exchanges"]
                    for source_id in (exchange["tool_use_source_id"], exchange["tool_result_source_id"])
                ]
                second_projected = engine._append_tool_history_batch(
                    tool_history_turns=native_history,
                    items=[
                        (
                            {"type": "send_file", TOOL_SOURCE_FIELD: NATIVE_OPENAI},
                            ToolExecutionResult(
                                tool_type="send_file",
                                followup_context="两个文件已发送。",
                            ),
                            "两个文件已发送。",
                            "",
                        )
                    ],
                    trace_source_ids=second_trace_ids,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                self.assertTrue(second_projected["ok"], second_projected)

                after_second = manager.build_context_projection(
                    provider_profile="responses",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                complete_turn_messages = [
                    dict(message)
                    for message in after_second["messages"]
                    if message.get("turn_id") == opened["turn_id"]
                ]
                self.assertEqual(len(native_history), len(complete_turn_messages) - 1)
                third_context = {
                    "memcore_projection_read": {
                        "current_turn_id": opened["turn_id"],
                        "current_turn_messages": complete_turn_messages,
                    }
                }
                third_observer = engine._build_memcore_request_observer(
                    generation_context=third_context,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                runtime._observe_completion_request(
                    bundle=bundle,
                    payload={
                        "model": "gpt-test",
                        "messages": [
                            {"role": "system", "content": "stable system"},
                            actual_user,
                            *native_history,
                        ],
                        "tools": [],
                    },
                    observer=third_observer,
                    persistent_turn_messages=[actual_user, *native_history],
                )
                self.assertEqual(third_context["memcore_request_projection"]["status"], "recorded")
            finally:
                manager.close()

    def test_proactive_request_projection_failure_returns_memcore_error_not_persona_fallback(self) -> None:
        class RejectingManager:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def record_request_projection(self, **kwargs) -> dict[str, object]:
                self.calls.append(dict(kwargs))
                return {"ok": False, "status": "failed", "reason": "projection_write_failed"}

        class RejectingLLM:
            supports_request_observer = True

            @staticmethod
            def snapshot_metrics() -> dict[str, int]:
                return {}

            @staticmethod
            def call_chat_json_result(**kwargs):
                observed = kwargs["request_observer"](
                    {
                        "protocol": "responses",
                        "model_route": {"protocol": "responses", "model": "gpt-test"},
                        "system_prefix": "stable system",
                        "tool_schema": [],
                        "history_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                        "persistent_turn_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                        "audit_history_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                    }
                )
                return SimpleNamespace(
                    parsed=dict(kwargs["fallback"]),
                    raw_text="",
                    error=f"request_observer_rejected:{observed['reason']}",
                )

        manager = RejectingManager()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = RejectingLLM()
        engine.memcore_manager = manager
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "stable system",
            "user_prompt": "event.finance current",
            "fallback": {"speech": "我在认真听你说，要不要多告诉我一点。"},
            "visual_defaults": {"emotion": "normal"},
            "debug_enabled": False,
            "prompt_scope": "plugin_proactive",
            "memcore_projection_read": {
                "current_turn_id": "turn-proactive",
                "current_turn_messages": [
                    {
                        "turn_id": "turn-proactive",
                        "payload": {"role": "user", "content": "canonical event"},
                        "source_ids": ["event-proactive"],
                        "projection_index": 0,
                        "projection_status": "complete",
                        "projection_version": 1,
                    }
                ],
            },
        }

        result = engine._build_final_response(
            session_id="private:u1",
            profile_user_id="u1",
            user_message="event.finance current",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=100,
            character_pack_id="char",
            prompt_scope="plugin_proactive",
        )

        self.assertEqual(len(manager.calls), 1)
        self.assertTrue(result["_transient_final_failure"])
        self.assertEqual(result["_memcore_failure"]["reason"], "request_projection_record_failed")
        self.assertIn("上下文没有完整衔接成功", result["speech"])
        self.assertNotIn("认真听你说", result["speech"])

    def test_memcore_final_retry_reuses_identical_user_payload(self) -> None:
        class RecordingManager:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def record_request_projection(self, **kwargs):
                self.calls.append(dict(kwargs))
                return {
                    "ok": True,
                    "status": "recorded",
                    "attempt": len(self.calls),
                    "turn_id": kwargs["turn_id"],
                }

        class RetryingLLM:
            supports_request_observer = True

            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            @staticmethod
            def snapshot_metrics() -> dict[str, int]:
                return {}

            @staticmethod
            def record_metric(*_args, **_kwargs) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                observed = kwargs["request_observer"](
                    {
                        "protocol": "responses",
                        "model_route": {"protocol": "responses", "model": "gpt-test"},
                        "system_prefix": kwargs["system_prompt"],
                        "tool_schema": [],
                        "history_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                        "persistent_turn_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                        "audit_history_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                    }
                )
                self.assert_observed = observed
                speech = "retry" if len(self.calls) == 1 else "完成"
                return SimpleNamespace(parsed={"speech": speech}, raw_text=f'{{"speech":"{speech}"}}', error="")

        manager = RecordingManager()
        llm = RetryingLLM()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = llm
        engine.memcore_manager = manager
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "stable system",
            "user_prompt": "[100] message.user\ncontent:\n请回答",
            "fallback": {"speech": "fallback"},
            "visual_defaults": {"emotion": "normal"},
            "debug_enabled": False,
            "allow_tool_call": False,
            "prompt_scope": "",
            "memcore_projection_read": {
                "current_turn_id": "turn-retry",
                "current_turn_messages": [
                    {
                        "turn_id": "turn-retry",
                        "payload": {"role": "user", "content": "canonical"},
                        "source_ids": ["retry-user"],
                        "projection_index": 0,
                        "projection_status": "complete",
                        "projection_version": 1,
                    }
                ],
            },
        }
        engine._normalize_final_output = lambda *, result, **_kwargs: dict(result or {})
        engine._attach_memory_annotation_truth = lambda *_args, **_kwargs: None
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
        engine._is_retryable_final_output = lambda output, **_kwargs: output.get("speech") == "retry"

        result = engine._build_final_response(
            session_id="private:u1",
            profile_user_id="u1",
            user_message="请回答",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=100,
            character_pack_id="char",
        )

        self.assertEqual(result["speech"], "完成")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[0]["user_prompt"], llm.calls[1]["user_prompt"])
        self.assertEqual(len(manager.calls), 2)
        self.assertTrue(llm.assert_observed["ok"])

    def test_stream_transport_fallback_stops_when_second_projection_record_is_rejected(self) -> None:
        class FlakyManager:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def record_request_projection(self, **kwargs) -> dict[str, object]:
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return {"ok": True, "status": "recorded", "attempt": 1}
                return {"ok": False, "status": "failed", "reason": "projection_write_failed"}

        class FlakyLLM:
            supports_request_observer = True

            @staticmethod
            def snapshot_metrics() -> dict[str, int]:
                return {}

            @staticmethod
            def record_metric(*_args, **_kwargs) -> None:
                return None

            @staticmethod
            def _request(kwargs: dict[str, object]) -> dict[str, object]:
                return {
                    "protocol": "responses",
                    "model_route": {"protocol": "responses", "model": "gpt-test"},
                    "system_prefix": "stable system",
                    "tool_schema": [],
                    "history_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                    "persistent_turn_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                    "audit_history_messages": [{"role": "user", "content": kwargs["user_prompt"]}],
                }

            def stream_chat_json(self, **kwargs):
                observed = kwargs["request_observer"](self._request(kwargs))

                def generate():
                    if not observed["ok"]:
                        return SimpleNamespace(
                            parsed=dict(kwargs["fallback"]),
                            raw_text="",
                            error=f"request_observer_rejected:{observed['reason']}",
                            latest_emotion="",
                            latest_speech="",
                        )
                    if False:
                        yield {}
                    return SimpleNamespace(
                        parsed=dict(kwargs["fallback"]),
                        raw_text="",
                        error="upstream disconnected",
                        latest_emotion="",
                        latest_speech="",
                    )

                return generate()

            def call_chat_json_result(self, **kwargs):
                observed = kwargs["request_observer"](self._request(kwargs))
                return SimpleNamespace(
                    parsed=dict(kwargs["fallback"]),
                    raw_text="",
                    error=f"request_observer_rejected:{observed['reason']}",
                )

        manager = FlakyManager()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = FlakyLLM()
        engine.memcore_manager = manager
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "stable system",
            "user_prompt": "event.finance current",
            "fallback": {"speech": "我在认真听你说，要不要多告诉我一点。"},
            "visual_defaults": {"emotion": "normal"},
            "debug_enabled": False,
            "prompt_scope": "plugin_proactive",
            "allow_tool_call": False,
            "memcore_projection_read": {
                "current_turn_id": "turn-proactive-stream",
                "current_turn_messages": [
                    {
                        "turn_id": "turn-proactive-stream",
                        "payload": {"role": "user", "content": "canonical event"},
                        "source_ids": ["event-proactive-stream"],
                        "projection_index": 0,
                        "projection_status": "complete",
                        "projection_version": 1,
                    }
                ],
            },
        }
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda *, result, **_kwargs: dict(result or {})
        engine._attach_memory_annotation_truth = lambda *_args, **_kwargs: None
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
        engine._is_retryable_final_output = lambda *_args, **_kwargs: True

        stream = engine._stream_final_response(
            session_id="private:u1",
            profile_user_id="u1",
            user_message="event.finance current",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=100,
            character_pack_id="char",
            prompt_scope="plugin_proactive",
        )
        events: list[dict[str, object]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stopped:
                result = stopped.value
                break

        self.assertEqual(len(manager.calls), 2)
        self.assertTrue(result["_transient_final_failure"])
        self.assertEqual(result["_memcore_failure"]["reason"], "request_projection_record_failed")
        self.assertNotIn("认真听你说", result["speech"])
        self.assertEqual(events[-1]["text"], result["speech"])

    def test_prompt_token_estimate_counts_structured_history(self) -> None:
        base = {
            "system_prompt": "system",
            "user_prompt": "current",
            "system_extra_blocks": [],
            "post_user_turns": [],
        }
        without_history = response_builder._estimate_generation_context_tokens(
            {**base, "history_turns": []},
            [],
        )
        with_history = response_builder._estimate_generation_context_tokens(
            {**base, "history_turns": [{"role": "user", "content": "历史" * 200}]},
            [],
        )
        with_ephemeral = response_builder._estimate_generation_context_tokens(
            {**base, "history_turns": [], "ephemeral_turns": [{"role": "user", "content": "证据" * 200}]},
            [],
        )

        self.assertGreater(with_history, without_history + 150)
        self.assertGreater(with_ephemeral, without_history + 150)

    def test_prompt_raw_trim_preserves_current_and_halves_large_oldest_record(self) -> None:
        records = [
            {"source_id": "old", "role": "tool.search call_1", "content": "x" * 1200},
            {"source_id": "current", "role": "user", "content": "current"},
        ]

        changed = response_builder._trim_oldest_prompt_raw_record(
            records,
            current_source_id="current",
            current_content="current",
        )

        self.assertTrue(changed)
        self.assertEqual(records[-1]["source_id"], "current")
        self.assertIn("更早内容已由上下文高水位保护省略", records[0]["content"])
        self.assertLess(len(records[0]["content"]), 900)

    def test_tool_exchange_is_visible_in_memcore_provider_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                user = manager.begin_input_turn(
                    {"source_id": "user-1", "content": "查一下北京天气", "timestamp": 100},
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                trace = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "call_1",
                            "tool_input": {"query": "北京天气"},
                            "result": "北京今天晴，25°C。",
                            "source": "anysearch",
                            "timestamp": 101,
                            "source_id_prefix": "trace-1",
                        }
                    ],
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                    turn_id=str(user.get("turn_id") or ""),
                )
                projection = manager.build_context_projection(
                    provider_profile="native_openai",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
            finally:
                manager.close()

        self.assertTrue(user["ok"])
        self.assertTrue(trace["ok"])
        self.assertTrue(projection["ok"], projection)
        payloads = [item["payload"] for item in projection["messages"]]
        self.assertEqual(payloads[1]["tool_calls"][0]["function"]["name"], "web_search")
        self.assertEqual(payloads[2]["tool_call_id"], "call_1")
        self.assertIn("北京今天晴", payloads[2]["content"])

    def test_index_warmup_is_scheduled_without_blocking_first_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
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

    def test_default_managers_share_process_runtime_without_mixing_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "first.db",
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            second = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "second.db",
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                first_system = first._get_system(
                    profile_user_id="personal-user",
                    session_id="private:personal-user",
                    character_pack_id="akane-personal",
                )
                second_system = second._get_system(
                    profile_user_id="finance-user",
                    session_id="group:finance-room",
                    character_pack_id="akane-finance",
                )

                self.assertIs(first._runtime, second._runtime)
                self.assertIs(first_system.runtime, second_system.runtime)
                self.assertNotEqual(first_system.namespace.hard_key(), second_system.namespace.hard_key())
                self.assertNotEqual(
                    first_system.namespace.conversation_id,
                    second_system.namespace.conversation_id,
                )

                shared_runtime = first_system.runtime
                first.close()
                self.assertEqual(shared_runtime.submit_index_repair(lambda: "alive").result(timeout=1), "alive")
            finally:
                first.close()
                second.close()
            with self.assertRaisesRegex(RuntimeError, "memcore_runtime_closed"):
                shared_runtime.submit_index_repair(lambda: "closed")

    def test_manager_does_not_close_injected_runtime(self) -> None:
        from memcore import MemCoreRuntime

        runtime = MemCoreRuntime(compaction_workers=1, index_workers=1)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                    runtime=runtime,
                )
                system = manager._get_system(
                    profile_user_id="user",
                    session_id="private:user",
                    character_pack_id="akane",
                )
                self.assertIs(system.runtime, runtime)
                manager.close()

            self.assertEqual(runtime.submit_index_repair(lambda: 7).result(timeout=1), 7)
        finally:
            runtime.close()

    def test_manager_waits_for_running_compaction_before_closing_store(self) -> None:
        from memcore import MemCoreRuntime

        runtime = MemCoreRuntime(compaction_workers=1, index_workers=1)
        started = threading.Event()
        release = threading.Event()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                    runtime=runtime,
                )
                system = manager._get_system(
                    profile_user_id="user",
                    session_id="private:user",
                    character_pack_id="akane",
                )

                def _run_slow_compaction():
                    started.set()
                    release.wait(timeout=2)
                    return {"status": "not_due"}

                submitted_profiles: list[str] = []

                def _submit_slow_compaction(**kwargs):
                    submitted_profiles.append(str(kwargs.get("provider_profile") or ""))
                    return runtime.submit_compaction(_run_slow_compaction)

                system.compact_due_background = _submit_slow_compaction
                scheduled = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="private:user",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(scheduled["status"], "scheduled")
                self.assertEqual(scheduled["provider_profile"], "openai_chat")
                self.assertEqual(submitted_profiles, ["openai_chat"])
                self.assertTrue(started.wait(timeout=1))
                coalesced = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="private:user",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(coalesced["status"], "coalesced")
                self.assertEqual(submitted_profiles, ["openai_chat"])

                close_thread = threading.Thread(target=manager.close)
                close_thread.start()
                close_thread.join(timeout=0.05)
                self.assertTrue(close_thread.is_alive())
                release.set()
                close_thread.join(timeout=1)
                self.assertFalse(close_thread.is_alive())
                self.assertTrue(manager._closed)
                self.assertEqual(submitted_profiles, ["openai_chat"])
        finally:
            release.set()
            runtime.close()

    def test_manager_coalesces_burst_into_one_followup_compaction(self) -> None:
        from memcore import MemCoreRuntime

        runtime = MemCoreRuntime(compaction_workers=1, index_workers=1)
        first_started = threading.Event()
        first_release = threading.Event()
        second_started = threading.Event()
        second_release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                    runtime=runtime,
                )
                system = manager._get_system(
                    profile_user_id="user",
                    session_id="group:busy-room",
                    character_pack_id="akane",
                )

                def _run_compaction():
                    nonlocal calls
                    with calls_lock:
                        calls += 1
                        call_number = calls
                    if call_number == 1:
                        first_started.set()
                        first_release.wait(timeout=2)
                    elif call_number == 2:
                        second_started.set()
                        second_release.wait(timeout=2)
                    return {"status": "not_due"}

                submitted_profiles: list[str] = []

                def _submit_compaction(**kwargs):
                    submitted_profiles.append(str(kwargs.get("provider_profile") or ""))
                    return runtime.submit_compaction(_run_compaction)

                system.compact_due_background = _submit_compaction
                first = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="group:busy-room",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(first["status"], "scheduled")
                self.assertTrue(first_started.wait(timeout=1))

                repeated = [
                    manager.compact_due_background(
                        profile_user_id="user",
                        session_id="group:busy-room",
                        character_pack_id="akane",
                        provider_profile="responses",
                    )
                    for _ in range(20)
                ]
                self.assertTrue(all(item["status"] == "coalesced" for item in repeated))
                self.assertEqual(submitted_profiles, ["openai_chat"])

                first_release.set()
                self.assertTrue(second_started.wait(timeout=1))
                self.assertEqual(submitted_profiles, ["openai_chat", "openai_chat"])
                second_release.set()

                deadline = time.monotonic() + 1
                while manager._background_futures and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(calls, 2)
                self.assertFalse(manager._background_futures)
                manager.close()
        finally:
            first_release.set()
            second_release.set()
            runtime.close()

    def test_manager_cools_down_failed_compaction_without_queued_retry_burst(self) -> None:
        from memcore import MemCoreRuntime

        runtime = MemCoreRuntime(compaction_workers=1, index_workers=1)
        started = threading.Event()
        release = threading.Event()
        calls = 0
        try:
            with tempfile.TemporaryDirectory() as temp_dir, patch.object(
                config,
                "MEMCORE_COMPACTION_FAILURE_COOLDOWN_SECONDS",
                0.2,
                create=True,
            ):
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=Path(temp_dir) / "memcore_v01.db",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                    runtime=runtime,
                )
                system = manager._get_system(
                    profile_user_id="user",
                    session_id="group:failing-room",
                    character_pack_id="akane",
                )

                def _run_compaction():
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        started.set()
                        release.wait(timeout=2)
                        return {"status": "failed", "reason": "summary_retry_pending"}
                    return {"status": "not_due"}

                system.compact_due_background = lambda **_kwargs: runtime.submit_compaction(_run_compaction)
                first = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="group:failing-room",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(first["status"], "scheduled")
                self.assertTrue(started.wait(timeout=1))
                pending = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="group:failing-room",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(pending["status"], "coalesced")

                release.set()
                deadline = time.monotonic() + 1
                while manager._background_futures and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(calls, 1)

                deferred = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="group:failing-room",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(deferred["status"], "deferred")
                self.assertGreaterEqual(deferred["retry_after_seconds"], 1)
                self.assertEqual(calls, 1)

                time.sleep(0.22)
                resumed = manager.compact_due_background(
                    profile_user_id="user",
                    session_id="group:failing-room",
                    character_pack_id="akane",
                    provider_profile="responses",
                )
                self.assertEqual(resumed["status"], "scheduled")
                deadline = time.monotonic() + 1
                while manager._background_futures and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(calls, 2)
                manager.close()
        finally:
            release.set()
            runtime.close()

    def test_compaction_result_log_reports_safe_structured_counts(self) -> None:
        future = SimpleNamespace(
            cancelled=lambda: False,
            result=lambda: {
                "status": "compacted",
                "provider_profile": "openai_chat",
                "before_projected_tokens": 220_000,
                "after_projected_tokens": 28_000,
                "before_raw_projected_tokens": 24_100,
                "after_raw_projected_tokens": 8_000,
                "planned_source_tokens": 16_080,
                "selected_projected_tokens": 16_100,
                "compaction_generation": 2,
                "source_turn_count": 39,
                "source_entry_count": 78,
                "summaries_created": 1,
                "summary_source_ids": ["must-not-be-logged"],
            },
        )

        with self.assertLogs("akane.memcore", level="INFO") as captured:
            MemcoreManager._log_compaction_result(
                future,
                namespace_hash="abc123def456",
                requested_profile="openai_chat",
            )

        rendered = "\n".join(captured.output)
        self.assertIn("status=compacted", rendered)
        self.assertIn("before_tokens=220000", rendered)
        self.assertIn("after_tokens=28000", rendered)
        self.assertIn("raw_before_tokens=24100", rendered)
        self.assertIn("raw_after_tokens=8000", rendered)
        self.assertIn("planned_source_tokens=16080", rendered)
        self.assertIn("selected_source_tokens=16100", rendered)
        self.assertIn("namespace=abc123def456", rendered)
        self.assertNotIn("must-not-be-logged", rendered)

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
        result = manager.append_standalone_message(
            {"source_id": "u1", "content": "hello", "timestamp": 100},
            role="user",
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
            [
                {
                    "profile_user_id": "u1",
                    "session_id": "s1",
                    "character_pack_id": "char",
                    "provider_profile": "",
                }
            ],
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
            self.assertIsNotNone(manager._store.get_record_by_source_id("old-assistant"))
            self.assertIsNone(manager._store.get_record_by_source_id("other-profile"))
            self.assertIsNone(manager._store.get_record_by_source_id("tool-turn"))
            found = manager.inspect_turn_source(
                "old-user",
                profile_user_id="u1",
                session_id="old-session",
                character_pack_id="char",
            )
            missing = manager.inspect_turn_source(
                "not-present",
                profile_user_id="u1",
                session_id="old-session",
                character_pack_id="char",
            )
            conflict = manager.inspect_turn_source(
                "old-user",
                profile_user_id="u2",
                session_id="old-session",
                character_pack_id="char",
            )
            self.assertEqual(found["status"], "found")
            self.assertTrue(found["exists"])
            self.assertNotIn("content", found)
            self.assertEqual(missing["status"], "missing")
            self.assertFalse(missing["exists"])
            self.assertEqual(conflict["status"], "conflict")
            self.assertFalse(conflict["ok"])
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
            self.assertEqual(summary["memory_metadata"]["memory_facets"], ["preference"])
            self.assertEqual(summary["memory_metadata"]["topic_terms"], ["新番", "动漫"])
            self.assertNotIn("source_system", summary["memory_metadata"])
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
            first = manager.begin_input_turn(
                user_record,
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            duplicate = manager.begin_input_turn(
                user_record,
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            metadata = {
                "turn_intent": "",
                "memory_facets": ["preference"],
                "about_roles": ["user"],
                "entity_anchors": ["可乐"],
                "topic_terms": ["饮料"],
                "retrieval_priority": "high",
                "mood_tags": ["happy"],
            }
            updated = manager.stage_turn_metadata(
                "user-turn-1",
                metadata,
                profile_user_id="profile-1",
                session_id="session-1",
                character_pack_id="char-1",
            )
            assistant = manager.complete_input_turn(
                turn_id=str(first.get("turn_id") or ""),
                assistant_record={
                    "source_id": "assistant-turn-1",
                    "content": "我记住啦，下次聊饮料会想到冰可乐。",
                    "timestamp": 1_777_777_010,
                    "memory_metadata": {},
                },
                memory_metadata=metadata,
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
            self.assertEqual(stored_user["memory_metadata"]["entity_anchors"], ["可乐"])
            self.assertEqual(stored_user["memory_metadata"]["topic_terms"], ["饮料"])
            self.assertEqual(stored_user["memory_metadata"]["memory_facets"], ["preference"])
            self.assertEqual(stored_user["memory_metadata"]["mood_tags"], ["happy"])
            manager.close()

    def test_v2_turn_keeps_parallel_tools_correlated_and_completes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {
                        "source_id": "stimulus-1",
                        "content": "同时查天气和新闻。",
                        "timestamp": 100,
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                turn_id = str(opened.get("turn_id") or "")
                intermediate = manager.append_turn_intermediate(
                    {
                        "source_id": "preface-1",
                        "content": "我一起查一下。",
                        "timestamp": 101,
                    },
                    turn_id=turn_id,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                batch = manager.record_tool_batch(
                    exchanges=[
                        {
                            "tool_name": "weather",
                            "tool_call_id": "call-weather",
                            "tool_input": {"city": "北京"},
                            "result": "晴，25°C。",
                            "source": "weather-api",
                            "timestamp": 102,
                            "source_id_prefix": "trace-weather",
                            "result_status": "success",
                        },
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "call-news",
                            "tool_input": {"query": "北京新闻"},
                            "result": "今天有一条公开新闻。",
                            "source": "search-api",
                            "timestamp": 102,
                            "source_id_prefix": "trace-news",
                            "result_status": "success",
                        },
                    ],
                    turn_id=turn_id,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                metadata = {
                    "memory_facets": ["knowledge"],
                    "about_roles": ["external"],
                    "entity_anchors": ["北京"],
                    "topic_terms": ["天气", "新闻"],
                    "retrieval_priority": "normal",
                }
                staged = manager.stage_turn_metadata(
                    "stimulus-1",
                    metadata,
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                before = manager._store.get_record_by_source_id("stimulus-1")
                completed = manager.complete_input_turn(
                    turn_id=turn_id,
                    assistant_record={
                        "source_id": "final-1",
                        "content": "北京天气晴朗，也有一条公开新闻。",
                        "timestamp": 103,
                    },
                    memory_metadata=metadata,
                    provider_output_raw="",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )

                system = manager._get_system(
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                entries = manager._store.get_turn_entries(namespace=system.namespace, turn_id=turn_id)
                projection = system.build_context_projection(provider_profile="openai_chat")
                openai_projection = manager.build_context_projection(
                    provider_profile="native_openai",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                anthropic_projection = manager.build_context_projection(
                    provider_profile="native_anthropic",
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
            finally:
                manager.close()

        self.assertTrue(opened["ok"], opened)
        self.assertTrue(intermediate["ok"], intermediate)
        self.assertTrue(batch["ok"], batch)
        self.assertTrue(staged["ok"], staged)
        self.assertEqual(before["annotation_status"], "unannotated")
        self.assertEqual(before["retrieval_visibility"], "explicit")
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(
            [str(entry.turn_role or "") for entry in entries],
            ["stimulus", "intermediate", "action", "action", "observation", "observation", "final"],
        )
        self.assertEqual(
            [entry.correlation_id for entry in entries if str(entry.turn_role or "") == "action"],
            ["call-weather", "call-news"],
        )
        trace_entries = [entry for entry in entries if str(entry.turn_role or "") in {"action", "observation"}]
        self.assertTrue(all(str(entry.retrieval_visibility) == "explicit" for entry in trace_entries))
        self.assertTrue(all(entry.index_status == "indexed" for entry in trace_entries))
        self.assertTrue(
            all(
                not any(
                    entry.memory_metadata.get(key)
                    for key in (
                        "memory_facets",
                        "about_roles",
                        "entity_anchors",
                        "topic_terms",
                        "mood_tags",
                    )
                )
                for entry in trace_entries
            )
        )
        openai_payloads = [message.payload for message in projection.messages]
        tool_call_messages = [payload for payload in openai_payloads if payload.get("tool_calls")]
        self.assertEqual(len(tool_call_messages), 1)
        self.assertEqual(
            [item["id"] for item in tool_call_messages[0]["tool_calls"]],
            ["call-weather", "call-news"],
        )
        self.assertTrue(openai_projection["ok"], openai_projection)
        openai_messages = [item["payload"] for item in openai_projection["messages"]]
        self.assertEqual(openai_messages[1]["content"], "我一起查一下。")
        self.assertEqual(
            [item["function"]["name"] for item in openai_messages[1]["tool_calls"]],
            ["weather", "web_search"],
        )
        self.assertEqual(
            [item["tool_call_id"] for item in openai_messages[2:4]],
            ["call-weather", "call-news"],
        )
        self.assertEqual(
            [item["content"] for item in openai_messages[2:4]],
            ["晴，25°C。", "今天有一条公开新闻。"],
        )
        self.assertTrue(anthropic_projection["ok"], anthropic_projection)
        anthropic_messages = [item["payload"] for item in anthropic_projection["messages"]]
        self.assertEqual(anthropic_messages[1]["content"][0], {"type": "text", "text": "我一起查一下。"})
        self.assertEqual(
            [item["id"] for item in anthropic_messages[1]["content"][1:]],
            ["call-weather", "call-news"],
        )
        self.assertEqual(
            [item["tool_use_id"] for item in anthropic_messages[2]["content"]],
            ["call-weather", "call-news"],
        )
        final = entries[-1]
        self.assertEqual(final.semantic_text, "北京天气晴朗，也有一条公开新闻。")
        self.assertEqual(final.payload.get("provider_output_raw"), "")

    def test_external_event_can_open_and_complete_the_same_v2_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "finance-event-1", "content": "", "timestamp": 200},
                    external_event={
                        "event_type": "finance",
                        "source": "公开快讯",
                        "fields": {"title": "政策方向更新", "summary": "尚无执行细节。"},
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                staged = manager.stage_turn_metadata(
                    "finance-event-1",
                    {
                        "memory_facets": ["event"],
                        "about_roles": ["external"],
                        "topic_terms": ["政策"],
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                completed = manager.complete_input_turn(
                    turn_id=str(opened.get("turn_id") or ""),
                    assistant_record={
                        "source_id": "finance-final-1",
                        "content": "这是一条方向性评论，暂时不能外推为具体刺激方案。",
                        "timestamp": 201,
                    },
                    memory_metadata={
                        "memory_facets": ["event"],
                        "about_roles": ["external"],
                        "topic_terms": ["政策"],
                    },
                    profile_user_id="u1",
                    session_id="s1",
                    character_pack_id="char",
                )
                event = manager._store.get_record_by_source_id("finance-event-1")
                final = manager._store.get_record_by_source_id("finance-final-1")
            finally:
                manager.close()

        self.assertTrue(opened["ok"], opened)
        self.assertTrue(staged["ok"], staged)
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(event["kind"], "event.finance")
        self.assertEqual(event["turn_id"], final["turn_id"])
        self.assertEqual(event["annotation_status"], "accepted_model")

    def test_three_finance_events_keep_one_strict_projection_prefix_and_group_isolation(self) -> None:
        metadata = {
            "memory_facets": ["event"],
            "about_roles": ["external"],
            "topic_terms": ["财经"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                private_prefixes: list[list[dict[str, object]]] = []
                for index in range(3):
                    opened = manager.begin_input_turn(
                        {
                            "source_id": f"finance-event-{index}",
                            "content": "",
                            "timestamp": 1_784_512_320 + (index * 60),
                        },
                        external_event={
                            "event_type": "finance",
                            "source": "虚拟公开源",
                            "fields": {
                                "published_at": f"2026-07-20T08:{52 + index:02d}:00+08:00",
                                "title": f"虚拟财经事件 {index}",
                                "summary": f"中性摘要 {index}",
                                "url": f"https://example.com/finance/{index}",
                            },
                        },
                        profile_user_id="finance-user",
                        session_id="private:finance-user",
                        character_pack_id="akane_v1",
                    )
                    self.assertTrue(opened["ok"], opened)
                    open_projection = manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="finance-user",
                        session_id="private:finance-user",
                        character_pack_id="akane_v1",
                    )
                    self.assertTrue(open_projection["ok"], open_projection)
                    if private_prefixes:
                        self.assertEqual(
                            open_projection["payloads"][: len(private_prefixes[-1])],
                            private_prefixes[-1],
                        )
                    self.assertEqual(open_projection["payloads"][-1]["role"], "user")
                    self.assertIn(f"虚拟财经事件 {index}", open_projection["payloads"][-1]["content"])
                    self.assertNotIn("插件", open_projection["payloads"][-1]["content"])
                    completed = manager.complete_input_turn(
                        turn_id=str(opened.get("turn_id") or ""),
                        assistant_record={
                            "source_id": f"finance-final-{index}",
                            "content": f"财经分析 {index}",
                            "timestamp": 1_784_512_321 + (index * 60),
                        },
                        memory_metadata=metadata,
                        provider_output_raw=f'{{"speech":"财经分析 {index}"}}',
                        profile_user_id="finance-user",
                        session_id="private:finance-user",
                        character_pack_id="akane_v1",
                    )
                    self.assertTrue(completed["ok"], completed)
                    projection = manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="finance-user",
                        session_id="private:finance-user",
                        character_pack_id="akane_v1",
                    )
                    self.assertTrue(projection["ok"], projection)
                    payloads = [dict(item) for item in projection["payloads"]]
                    if private_prefixes:
                        self.assertEqual(payloads[: len(private_prefixes[-1])], private_prefixes[-1])
                    private_prefixes.append(payloads)

                group_opened = manager.begin_input_turn(
                    {
                        "source_id": "group-user-1",
                        "content": "群里正常聊一下市场。",
                        "timestamp": 1_784_513_000,
                    },
                    profile_user_id="finance-user",
                    session_id="group:9988",
                    character_pack_id="akane_v1",
                )
                group_completed = manager.complete_input_turn(
                    turn_id=str(group_opened.get("turn_id") or ""),
                    assistant_record={
                        "source_id": "group-final-1",
                        "content": "可以，我们按普通群聊继续。",
                        "timestamp": 1_784_513_001,
                    },
                    memory_metadata={},
                    provider_output_raw='{"speech":"可以，我们按普通群聊继续。"}',
                    profile_user_id="finance-user",
                    session_id="group:9988",
                    character_pack_id="akane_v1",
                )
                group_projection = manager.build_context_projection(
                    provider_profile="openai",
                    profile_user_id="finance-user",
                    session_id="group:9988",
                    character_pack_id="akane_v1",
                )
                private_after_group = manager.build_context_projection(
                    provider_profile="openai",
                    profile_user_id="finance-user",
                    session_id="private:finance-user",
                    character_pack_id="akane_v1",
                )
            finally:
                manager.close()

        self.assertTrue(group_opened["ok"], group_opened)
        self.assertTrue(group_completed["ok"], group_completed)
        self.assertTrue(group_projection["ok"], group_projection)
        self.assertEqual(private_after_group["payloads"], private_prefixes[-1])
        private_text = repr(private_prefixes[-1])
        group_text = repr(group_projection["payloads"])
        self.assertEqual(private_text.count("event.finance"), 3)
        self.assertNotIn("插件", private_text)
        self.assertIn("群里正常聊一下市场", group_text)
        self.assertNotIn("虚拟财经事件", group_text)

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

            created = manager.begin_input_turn(
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
            updated = manager.stage_turn_metadata(
                "qq-group-turn-1",
                {
                    "memory_facets": ["preference"],
                    "about_roles": ["user"],
                    "entity_anchors": ["稳健型基金"],
                    "topic_terms": ["基金偏好"],
                    "retrieval_priority": "high",
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
            self.assertEqual(stored["memory_metadata"]["entity_anchors"], ["稳健型基金"])
            self.assertEqual(stored["memory_metadata"]["topic_terms"], ["基金偏好"])
            manager.close()

    def test_manager_does_not_expose_standalone_external_event_adapter(self) -> None:
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
            self.assertFalse(hasattr(manager, "record_external_event"))
            manager.close()

    def test_manager_marks_explicit_legacy_message_imports(self) -> None:
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

            result = manager.import_legacy_message(
                {
                    "source_id": "legacy-assistant-1",
                    "content": "历史分析结果。",
                    "timestamp": 100,
                    "memory_metadata": {
                        "keywords": ["金融分析"],
                        "categories": ["life_event"],
                        "importance": 0.8,
                    },
                },
                role="assistant",
                profile_user_id="u1",
                session_id="legacy-session",
                character_pack_id="char",
            )

            self.assertTrue(result["ok"], result)
            stored = manager._store.get_record_by_source_id("legacy-assistant-1")
            self.assertEqual(stored["role"], "assistant")
            self.assertEqual(stored["annotation_status"], "accepted_legacy")
            self.assertEqual(stored["memory_metadata"]["memory_facets"], ["event"])
            self.assertEqual(stored["memory_metadata"]["topic_terms"], ["金融分析"])
            self.assertEqual(stored["relation_status"], "standalone")
            manager.close()

    def test_engine_memcore_wrappers_forward_turn_actor(self) -> None:
        manager = _ActorCaptureMemcoreManager()
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = manager

        recorded = engine._begin_memcore_input_turn(
            user_record={"source_id": "qq-turn-1", "content": "关注黄金", "timestamp": 100},
            external_event=None,
            profile_user_id="qq-group-1",
            session_id="qq-group-1",
            character_pack_id="char-1",
            actor_stable_id="qq:10001",
            actor_display_name="张三",
        )
        updated = engine._stage_memcore_turn_metadata(
            source_id="qq-turn-1",
            memory_metadata={"entity_anchors": ["黄金"]},
            profile_user_id="qq-group-1",
            session_id="qq-group-1",
            character_pack_id="char-1",
            actor_stable_id="qq:10001",
            actor_display_name="张三",
        )
        passive = engine._append_memcore_passive_message(
            user_record={"source_id": "qq-passive-1", "content": "群里路过", "timestamp": 101},
            profile_user_id="qq-group-1",
            session_id="qq-group-1",
            character_pack_id="char-1",
            actor_stable_id="qq:10002",
            actor_display_name="李四",
        )

        self.assertTrue(recorded["ok"])
        self.assertTrue(updated["ok"])
        self.assertTrue(passive["ok"])
        self.assertEqual(manager.user_calls[0]["actor_stable_id"], "qq:10001")
        self.assertEqual(manager.user_calls[0]["actor_display_name"], "张三")
        self.assertEqual(manager.user_calls[1]["role"], "user")
        self.assertEqual(manager.user_calls[1]["actor_stable_id"], "qq:10002")
        self.assertEqual(manager.user_calls[1]["actor_display_name"], "李四")
        self.assertEqual(manager.metadata_calls[0]["actor_stable_id"], "qq:10001")
        self.assertEqual(manager.metadata_calls[0]["actor_display_name"], "张三")

    def test_engine_never_reuses_terminal_memcore_turn_as_writable(self) -> None:
        class TerminalTurnManager:
            enabled = True
            available = True

            @staticmethod
            def begin_input_turn(_record, **_kwargs):
                return {
                    "ok": True,
                    "status": "aborted",
                    "turn_id": "old-terminal-turn",
                    "writable": False,
                }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = TerminalTurnManager()

        opened = engine._begin_memcore_input_turn(
            user_record={"source_id": "same-event", "content": "retry", "timestamp": 100},
            external_event=None,
            profile_user_id="u1",
            session_id="s1",
            character_pack_id="char",
            actor_stable_id="",
            actor_display_name="",
        )

        self.assertTrue(opened["ok"])
        self.assertEqual(opened["status"], "aborted")
        self.assertFalse(opened["writable"])
        self.assertEqual(opened["turn_id"], "")
        self.assertEqual(
            engine._memcore_input_turn_failure(opened),
            {
                "status": "aborted",
                "reason": "input_turn_not_writable",
                "detail": "aborted",
                "delivery_status": "model_reply_preserved",
            },
        )

    def test_nonfatal_memcore_write_failure_never_replaces_real_reply(self) -> None:
        output = {
            "speech": "工具已经真实返回了文件 gen_009。",
            "speech_segments": ["工具已经真实返回了文件 gen_009。"],
        }

        AkaneMemoryEngine._attach_nonfatal_memcore_failure(
            output,
            {
                "status": "failed",
                "reason": "tool_trace_record_failed",
                "detail": "turn_not_open",
            },
        )

        self.assertEqual(output["speech"], "工具已经真实返回了文件 gen_009。")
        self.assertNotIn("_transient_final_failure", output)
        self.assertEqual(output["_memcore_failure"]["detail"], "turn_not_open")
        self.assertEqual(output["_memcore_failure"]["delivery_status"], "model_reply_preserved")

    def test_material_trace_bridge_records_safe_attachment_anchor(self) -> None:
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
            "storage_relpath": "C:/Users/ExampleUser/secret/meal.jpg",
            "detail": {
                "character_pack_id": "akane_v1",
                "summary": "盘子里有热汤。",
                "qq_sender_id": "10001",
                "qq_sender_label": "张三",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                reference = manager.record_material_reference(item=item, timestamp=100)
                cleanup = manager.record_material_cleanup(
                    item=item,
                    timestamp=120,
                    reason="聊完了",
                    delete_storage=True,
                )
                stored_reference = manager._store.get_record_by_source_id(reference["source_id"])
                stored_cleanup = manager._store.get_record_by_source_id(cleanup["source_id"])
                projection = manager.build_context_projection(
                    provider_profile="openai_chat",
                    profile_user_id="master",
                    session_id="qq_group_1",
                    character_pack_id="akane_v1",
                )
            finally:
                manager.close()

        self.assertTrue(reference["ok"])
        self.assertTrue(cleanup["ok"])
        self.assertEqual(stored_reference["kind"], "material.reference")
        self.assertEqual(stored_reference["payload"]["file_id"], "img_001")
        self.assertEqual(stored_reference["payload"]["file_status"], "ready")
        self.assertEqual(stored_reference["payload"]["derived_status"], "ready")
        self.assertEqual(stored_reference["actor_id"], "qq:10001")
        self.assertEqual(stored_reference["actor_display_name"], "张三")
        self.assertIn("reference:ready:100", stored_reference["source_id"])
        self.assertNotIn("storage_relpath", stored_reference["payload"])
        self.assertNotIn("C:/Users", str(stored_reference))
        self.assertEqual(stored_cleanup["kind"], "material.cleanup")
        self.assertEqual(stored_cleanup["payload"]["file_status"], "deleted")
        self.assertEqual(stored_cleanup["payload"]["reason"], "聊完了")
        self.assertEqual(stored_cleanup["turn_id"], "")
        projected_text = "\n".join(str(item.get("content") or "") for item in projection["payloads"])
        self.assertEqual(projected_text.count("file_id: img_001"), 2)
        self.assertNotIn("data:", projected_text)
        self.assertNotIn("mime_type:", projected_text)

    def test_failed_material_trace_preserves_structured_failure_evidence(self) -> None:
        item = {
            "attachment_id": "attachment::failed",
            "attachment_handle": "file_033",
            "profile_user_id": "master",
            "session_id": "qq_group_1",
            "kind": "file",
            "origin_name": "彩虹.flac",
            "mime_type": "audio/flac",
            "status": "failed",
            "error_message": "attachment_too_large",
            "short_hint": "文件超过当前大小限制。",
            "storage_relpath": "",
            "detail": {
                "character_pack_id": "akane_v1",
                "failure": {
                    "code": "attachment_too_large",
                    "reason": "文件超过当前大小限制。",
                    "observed_bytes": 27_461_517,
                    "limit_bytes": 20_971_520,
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                reference = manager.record_material_reference(item=item, timestamp=100)
                stored = manager._store.get_record_by_source_id(reference["source_id"])
                projection = manager.build_context_projection(
                    provider_profile="openai_chat",
                    profile_user_id="master",
                    session_id="qq_group_1",
                    character_pack_id="akane_v1",
                )
            finally:
                manager.close()

        self.assertTrue(reference["ok"])
        self.assertEqual(stored["payload"]["file_status"], "failed")
        self.assertEqual(stored["payload"]["failure"]["code"], "attachment_too_large")
        self.assertEqual(stored["payload"]["failure"]["observed_bytes"], 27_461_517)
        projected_text = "\n".join(str(item.get("content") or "") for item in projection["payloads"])
        self.assertIn("failure:\n  code: attachment_too_large", projected_text)
        self.assertIn("reason: 文件超过当前大小限制。", projected_text)
        self.assertIn("observed_bytes: 27461517", projected_text)
        self.assertIn("limit_bytes: 20971520", projected_text)

    def test_task_event_bridge_records_safe_explicit_timeline_event(self) -> None:
        task = {
            "task_id": "task::abc",
            "profile_user_id": "master",
            "session_id": "qq_group_1",
            "status": "completed",
            "normalized_goal": "整理 F:\\Private\\report.docx 并交付。",
            "artifacts": [{"id": "gen_001", "storage_relpath": "private/output.docx"}],
            "updated_at": 120,
        }
        event = {
            "event_id": "task_event::done",
            "event_type": "worker_completed",
            "from_actor": "document_agent",
            "priority": "high",
            "requires_user": False,
            "message": "任务完成，token=private-value，文件在 C:\\Private\\output.docx。",
            "payload": {
                "handoff": {
                    "summary": "终稿已生成。",
                    "artifacts": [{"id": "gen_001"}],
                }
            },
            "created_at": 120,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                result = manager.record_task_event(task=task, event=event, character_pack_id="akane_v1")
                stored = manager._store.get_record_by_source_id(result["source_id"])
                projection = manager.build_context_projection(
                    provider_profile="openai_chat",
                    profile_user_id="master",
                    session_id="qq_group_1",
                    character_pack_id="akane_v1",
                )
            finally:
                manager.close()

        self.assertTrue(result["ok"])
        self.assertEqual(stored["kind"], "event.task.worker_completed")
        self.assertEqual(stored["payload"]["source"], "task_workspace")
        self.assertEqual(stored["payload"]["task_id"], "task::abc")
        self.assertEqual(stored["payload"]["artifacts"], "gen_001")
        self.assertEqual(stored["retrieval_policy"], "explicit")
        self.assertEqual(stored["retrieval_visibility"], "explicit")
        self.assertNotIn("private-value", str(stored))
        self.assertNotIn("F:\\Private", str(stored))
        self.assertNotIn("C:\\Private", str(stored))
        self.assertNotIn("storage_relpath", str(stored))
        projected_text = str(projection["payloads"][0]["content"])
        self.assertEqual(projected_text.count("source: task_workspace"), 1)
        self.assertEqual(projected_text.count("task_id: task::abc"), 1)
        self.assertNotIn("content:", projected_text)
        self.assertNotIn("data:", projected_text)

    def test_manager_does_not_expose_v1_prompt_context_facade(self) -> None:
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
            self.assertFalse(hasattr(manager, "build_prompt_context"))
            self.assertFalse(hasattr(manager, "_render_prompt_context_layers"))
            self.assertFalse(hasattr(manager, "record_user_turn"))
            self.assertFalse(hasattr(manager, "record_assistant_turn"))
            self.assertFalse(hasattr(manager, "record_external_event"))
            self.assertFalse(hasattr(manager, "record_tool_exchange"))
            self.assertFalse(hasattr(manager, "update_turn_metadata"))
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
            manager.append_standalone_message(
                {
                    "source_id": "old-morning",
                    "content": "上午讨论了角色提示词。",
                    "timestamp": _ts(2026, 6, 13, 9, 0),
                    "memory_metadata": {
                        "memory_facets": ["procedure"],
                        "about_roles": ["external"],
                        "topic_terms": ["角色提示词"],
                        "retrieval_priority": "high",
                    },
                },
                role="user",
                profile_user_id="u1",
                session_id="old-session",
                character_pack_id="char",
            )
            manager.append_standalone_message(
                {
                    "source_id": "current-query",
                    "content": "请读取今天上午的原始对话。",
                    "timestamp": _ts(2026, 6, 13, 10, 0),
                    "memory_metadata": {},
                },
                role="user",
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
            created = manager.begin_input_turn(
                {"source_id": "shared-source", "content": "u1 private", "timestamp": 123},
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )
            crossed = manager.stage_turn_metadata(
                "shared-source",
                {
                    "memory_facets": ["preference"],
                    "about_roles": ["user"],
                    "entity_anchors": ["leak"],
                    "retrieval_priority": "high",
                },
                profile_user_id="u2",
                session_id="s1",
                character_pack_id="char",
            )

            self.assertTrue(created["ok"])
            self.assertFalse(crossed["ok"])
            self.assertEqual(crossed["status"], "forbidden")
            stored_user = manager._store.get_record_by_source_id("shared-source")
            self.assertEqual(stored_user["memory_metadata"].get("entity_anchors", []), [])
            manager.close()

    def test_dual_write_keeps_raw_user_turns_when_vector_index_is_disabled(self) -> None:
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
            result = manager.append_standalone_message(
                {
                    "source_id": "memory-query-turn",
                    "content": "你还记得我之前说过什么吗？",
                    "timestamp": 123,
                    "index_in_vector": False,
                },
                role="user",
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "recorded")
            self.assertEqual(result["index_status"], "skipped")
            stored = manager._store.get_record_by_source_id("memory-query-turn")
            self.assertIsNotNone(stored)
            self.assertEqual(stored["content"], "你还记得我之前说过什么吗？")
            self.assertEqual(stored["index_status"], "skipped")
            self.assertEqual(stored["turn_id"], "")
            self.assertEqual(stored["relation_status"], "standalone")
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
            manager.append_standalone_message(
                {
                    "source_id": "old-like",
                    "content": "我以前说过我喜欢冰可乐。",
                    "timestamp": 1_777_700_000,
                    "memory_metadata": {
                        "memory_facets": ["preference"],
                        "about_roles": ["user"],
                        "entity_anchors": ["可乐"],
                        "topic_terms": ["饮料"],
                        "retrieval_priority": "high",
                    },
                },
                role="user",
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
            manager.append_standalone_message(
                current,
                role="user",
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
                    entity_anchors=["可乐"],
                    topic_terms=["饮料"],
                    memory_facets=["preference"],
                    about_roles=["user"],
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ok")
            self.assertGreaterEqual(result["snippet_count"], 1)
            self.assertTrue(result["snippet_hashes"])
            self.assertNotIn("snippets", result)
            self.assertNotIn("冰可乐", repr(result))
            manager.close()

    def test_retrieve_memory_returns_all_normal_memcore_matches_without_model_limit(self) -> None:
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
                manager.append_standalone_message(
                    {
                        "source_id": f"old-like-{index}",
                        "content": f"我以前说过我喜欢{drink}。",
                        "timestamp": 1_777_700_000 + index,
                        "memory_metadata": {
                            "memory_facets": ["preference"],
                            "about_roles": ["user"],
                            "entity_anchors": ["可乐"],
                            "topic_terms": ["饮料"],
                            "retrieval_priority": "high",
                        },
                    },
                    role="user",
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
            manager.append_standalone_message(
                current,
                role="user",
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
                entity_anchors=["可乐"],
                topic_terms=["饮料"],
                source_layers=["raw"],
                memory_facets=["preference"],
                about_roles=["user"],
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["snippet_count"], 2)
            self.assertEqual(len(result["snippets"]), 2)
            self.assertTrue(all("可乐" in snippet for snippet in result["snippets"]))
            manager.close()

    def test_explicit_event_retrieval_is_host_authorized_and_message_kind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                opened = manager.begin_input_turn(
                    {"source_id": "finance-explicit-1", "content": "", "timestamp": 1_777_700_000},
                    external_event={
                        "event_type": "finance",
                        "source": "公开快讯",
                        "fields": {"title": "消费政策方向更新", "summary": "尚无执行细节。"},
                    },
                    profile_user_id="u1",
                    session_id="private:archive",
                    character_pack_id="char",
                )
                manager.stage_turn_metadata(
                    "finance-explicit-1",
                    {
                        "memory_facets": ["event"],
                        "about_roles": ["external"],
                        "entity_anchors": ["消费政策"],
                        "topic_terms": ["政策方向"],
                        "retrieval_priority": "high",
                    },
                    profile_user_id="u1",
                    session_id="private:archive",
                    character_pack_id="char",
                )
                manager.complete_input_turn(
                    turn_id=str(opened.get("turn_id") or ""),
                    assistant_record={
                        "source_id": "finance-explicit-answer-1",
                        "content": "这仍是方向性信息。",
                        "timestamp": 1_777_700_001,
                    },
                    memory_metadata={
                        "memory_facets": ["event"],
                        "about_roles": ["external"],
                        "entity_anchors": ["消费政策"],
                        "topic_terms": ["政策方向"],
                        "retrieval_priority": "high",
                    },
                    profile_user_id="u1",
                    session_id="private:archive",
                    character_pack_id="char",
                )

                allowed = manager.retrieve_memory(
                    profile_user_id="u1",
                    session_id="private:u1",
                    character_pack_id="char",
                    current_user_record={
                        "source_id": "current-explicit-query",
                        "content": "刚才的消费政策快讯是什么？",
                        "timestamp": 1_777_800_000,
                    },
                    query="消费政策方向更新",
                    entity_anchors=["消费政策"],
                    topic_terms=["政策方向"],
                    include_explicit=True,
                    kind_patterns=["event.finance.*"],
                )
                forbidden = manager.retrieve_memory(
                    profile_user_id="u1",
                    session_id="private:u1",
                    character_pack_id="char",
                    current_user_record={},
                    query="普通消息",
                    include_explicit=True,
                    kind_patterns=["message.*"],
                )
                invalid = manager.retrieve_memory(
                    profile_user_id="u1",
                    session_id="private:u1",
                    character_pack_id="char",
                    current_user_record={},
                    query="财经事件",
                    include_explicit=False,
                    kind_patterns=["event.finance.*"],
                )
            finally:
                manager.close()

        self.assertTrue(allowed["ok"], allowed)
        self.assertGreaterEqual(allowed["snippet_count"], 1)
        self.assertIn("消费政策方向更新", "\n".join(allowed["snippets"]))
        self.assertFalse(forbidden["ok"])
        self.assertEqual(forbidden["status"], "forbidden")
        self.assertEqual(forbidden["reason"], "kind_pattern_not_authorized")
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["status"], "invalid_request")
        self.assertEqual(invalid["reason"], "kind_patterns_require_include_explicit")

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
                call={
                    "query": "可乐",
                    "entity_anchors": ["可乐"],
                    "topic_terms": ["饮料"],
                    "memory_facets": ["preference"],
                    "about_roles": ["user"],
                    "include_explicit": True,
                    "kind_patterns": ["event.finance.*"],
                },
                context=_tool_context(),
            )

        self.assertIn("memcore snippet about cola", result.followup_context)
        self.assertEqual(retrieval_service.calls, [])
        self.assertEqual(len(memcore_manager.calls), 1)
        call = memcore_manager.calls[0]
        self.assertEqual(call["query"], "可乐")
        self.assertEqual(call["entity_anchors"], ["可乐"])
        self.assertEqual(call["topic_terms"], ["饮料"])
        self.assertEqual(call["memory_facets"], ["preference"])
        self.assertEqual(call["about_roles"], ["user"])
        self.assertNotIn("limit", call)
        self.assertTrue(call["include_explicit"])
        self.assertEqual(call["kind_patterns"], ["event.finance.*"])
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

    def test_final_prompt_context_uses_memcore_provider_projection_in_memcore_mode(self) -> None:
        projection_messages = [
            {"payload": {"role": "user", "content": "previous question"}, "source_ids": ["u0"]},
            {"payload": {"role": "assistant", "content": "previous answer"}, "source_ids": ["a0"]},
            {
                "payload": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search", "arguments": "{}"},
                        }
                    ],
                },
                "source_ids": ["t0-use"],
            },
            {
                "payload": {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
                "source_ids": ["t0-result"],
            },
            {"payload": {"role": "user", "content": "现在的问题"}, "source_ids": ["current"]},
        ]
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": True,
                "status": "ok",
                "raw": [
                    {"source_id": "u0", "role": "user", "content": "previous question", "timestamp": 1712399900},
                    {"source_id": "a0", "role": "assistant", "content": "previous answer", "timestamp": 1712399901},
                    {
                        "source_id": "t0-use",
                        "role": "assistant.tool_call search call_1",
                        "content": "tool input",
                        "timestamp": 1712399902,
                    },
                    {
                        "source_id": "t0-result",
                        "role": "tool.search call_1",
                        "content": "tool result",
                        "timestamp": 1712399903,
                    },
                    {"source_id": "current", "role": "user", "content": "现在的问题", "timestamp": 1712400000},
                ],
                "raw_text": "MEMCORE RAW",
                "episodic_text": "MEMCORE EPISODIC",
                "semantic_text": "MEMCORE SEMANTIC",
                "raw_count": 1,
                "episodic_count": 1,
                "semantic_count": 1,
            },
            projection_payload={
                "ok": True,
                "status": "ok",
                "provider_profile": "openai_chat",
                "messages": projection_messages,
                "stable_prefix_hash": "a" * 64,
                "projection_version": 1,
                "compaction_generation": 0,
                "projection_generation": 1,
            },
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            first = response_builder.prepare_context(
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
            second = response_builder.prepare_context(
                engine,
                session_id="s2",
                profile_user_id="u1",
                user_message="另一个会话的问题",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=1712400001,
                character_pack_id="char",
            )

        captured = engine.prompt_builder.calls[0]
        self.assertEqual(captured["raw_text"], "")
        self.assertEqual(captured["episodic_summary_text"], "")
        self.assertEqual(captured["semantic_summary_text"], "")
        self.assertNotIn("LEGACY", repr(captured))
        self.assertEqual(memcore_manager.projection_calls[0]["profile_user_id"], "u1")
        self.assertEqual(memcore_manager.projection_calls[0]["session_id"], "s1")
        self.assertEqual(memcore_manager.projection_calls[0]["character_pack_id"], "char")
        self.assertEqual(
            [turn["role"] for turn in captured["history_turns"]],
            ["user", "assistant", "assistant", "tool"],
        )
        self.assertNotIn("现在的问题", repr(captured["history_turns"]))
        self.assertIn("call_1", repr(captured["history_turns"]))
        self.assertIn("tool result", repr(captured["history_turns"]))
        self.assertRegex(str(first["prompt_cache_scope_hash"]), r"^[0-9a-f]{64}$")
        self.assertNotEqual(first["prompt_cache_scope_hash"], second["prompt_cache_scope_hash"])

    def test_prompt_budget_compacts_then_refreshes_only_provider_projection(self) -> None:
        initial_projection = {
            "ok": True,
            "status": "ok",
            "provider_profile": "openai_chat",
            "messages": [
                {
                    "payload": {"role": "user", "content": "很长的旧历史" * 1000},
                    "source_ids": ["previous"],
                },
                {"payload": {"role": "user", "content": "当前问题"}, "source_ids": ["current"]},
            ],
            "stable_prefix_hash": "1" * 64,
            "projection_version": 1,
            "compaction_generation": 0,
            "projection_generation": 1,
        }
        refreshed_projection = {
            **initial_projection,
            "messages": [
                {"payload": {"role": "user", "content": "当前问题"}, "source_ids": ["current"]},
            ],
            "stable_prefix_hash": "2" * 64,
            "compaction_generation": 1,
        }

        class _CompactingProjectionManager(_PromptContextMemcoreManager):
            def __init__(self) -> None:
                super().__init__({}, projection_payload=initial_projection)
                self.compact_calls: list[dict[str, object]] = []

            def compact_due_sync(self, **kwargs) -> dict[str, object]:
                self.compact_calls.append(dict(kwargs))
                self.projection_payload = refreshed_projection
                return {"ok": True, "status": "completed", "stats": {}}

        memcore_manager = _CompactingProjectionManager()
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(
            config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 100
        ):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="当前问题",
                recent_raw=[{"source_id": "current", "role": "user", "content": "当前问题"}],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=100,
                character_pack_id="char",
            )

        self.assertEqual(len(memcore_manager.compact_calls), 1)
        self.assertEqual(memcore_manager.compact_calls[0]["provider_profile"], "openai_chat")
        self.assertEqual(len(memcore_manager.projection_calls), 2)
        self.assertFalse(hasattr(memcore_manager, "build_prompt_context"))
        self.assertTrue(result["prompt_budget"]["compact_attempted"])
        self.assertEqual(result["memcore_projection_read"]["compaction_generation"], 1)
        history_start = int(result["memcore_history_start_index"])
        self.assertEqual(result["history_turns"][history_start:], [])
        self.assertNotIn("很长的旧历史", repr(result))

    def test_plain_prompt_shadow_compares_normalized_history_without_changing_prompt(self) -> None:
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": True,
                "status": "ok",
                "raw": [
                    {"source_id": "previous", "role": "assistant", "content": "上一轮原文", "timestamp": 99},
                    {"source_id": "current", "role": "user", "content": "当前问题", "timestamp": 100},
                ],
                "raw_text": "MEMCORE RAW",
                "episodic_text": "",
                "semantic_text": "",
            },
            projection_payload={
                "ok": True,
                "status": "ok",
                "provider_profile": "openai_chat",
                "messages": [
                    {
                        "turn_id": "turn-previous",
                        "payload": {"role": "assistant", "content": "上一轮原文"},
                        "source_ids": ["previous"],
                    },
                    {
                        "turn_id": "turn-current",
                        "payload": {"role": "user", "content": "当前问题"},
                        "source_ids": ["current"],
                    },
                ],
                "stable_prefix_hash": "a" * 64,
                "projection_version": 1,
            },
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(
            config, "MEMCORE_SHADOW_COMPARE", True
        ):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="当前问题",
                recent_raw=[{"source_id": "current", "role": "user", "content": "当前问题", "timestamp": 100}],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=100,
                character_pack_id="char",
            )

        self.assertEqual(result["memcore_projection_shadow"]["status"], "match")
        self.assertTrue(result["memcore_projection_shadow"]["strict_prefix"])
        self.assertEqual(len(memcore_manager.compare_calls), 1)
        compare_call = memcore_manager.compare_calls[0]
        self.assertEqual(compare_call["provider_profile"], "openai")
        self.assertEqual(compare_call["exclude_source_ids"], ["current"])
        history_start = int(result["memcore_history_start_index"])
        self.assertEqual(compare_call["actual_history_messages"], result["history_turns"][history_start:])
        self.assertNotIn("memcore_projection_shadow", result["system_prompt"])
        self.assertNotIn("memcore_projection_shadow", result["user_prompt"])

    def test_plain_prompt_reads_memcore_projection_without_envelope_or_duplicate_current_message(self) -> None:
        raw_final = '{"speech":"上一轮原始回复","memory_metadata":{}}'
        projection_messages = [
            {
                "payload": {"role": "user", "content": "【阶段摘要】稳定摘要"},
                "source_ids": ["summary-1"],
            },
            {
                "payload": {"role": "user", "content": "上一轮问题"},
                "source_ids": ["previous-user"],
            },
            {
                "payload": {
                    "role": "assistant",
                    "content": "我查一下。",
                    "tool_calls": [
                        {
                            "id": "call-history-1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": '{"query":"Akane"}'},
                        }
                    ],
                },
                "source_ids": ["previous-preface", "previous-tool-use"],
            },
            {
                "payload": {"role": "tool", "tool_call_id": "call-history-1", "content": "历史工具结果"},
                "source_ids": ["previous-tool-result"],
            },
            {
                "payload": {"role": "assistant", "content": raw_final},
                "source_ids": ["previous-assistant"],
            },
            {
                "payload": {"role": "user", "content": "当前问题"},
                "source_ids": ["current"],
            },
        ]
        memcore_manager = _PromptContextMemcoreManager(
            {
                "ok": True,
                "status": "ok",
                "raw": [
                    {"source_id": "previous-user", "role": "user", "content": "LEGACY QUESTION"},
                    {"source_id": "previous-assistant", "role": "assistant", "content": "LEGACY ANSWER"},
                    {"source_id": "current", "role": "user", "content": "当前问题"},
                ],
                "raw_text": "LEGACY RAW",
                "episodic_text": "LEGACY EPISODIC",
                "semantic_text": "LEGACY SEMANTIC",
            },
            projection_payload={
                "ok": True,
                "status": "ok",
                "provider_profile": "openai_chat",
                "messages": projection_messages,
                "stable_prefix_hash": "b" * 64,
                "projection_version": 1,
                "compaction_generation": 0,
                "projection_generation": 1,
            },
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="当前问题",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=100,
                character_pack_id="char",
            )

        history_start = int(result["memcore_history_start_index"])
        projected_history = result["history_turns"][history_start:]
        self.assertEqual(projected_history, [item["payload"] for item in projection_messages[:-1]])
        self.assertEqual(result["memcore_projection_read"]["status"], "active")
        self.assertEqual(result["user_prompt"].count("当前问题"), 1)
        self.assertNotIn("当前问题", repr(projected_history))
        self.assertIn(raw_final, repr(projected_history))
        self.assertEqual(projected_history[2]["tool_calls"][0]["id"], "call-history-1")
        self.assertEqual(projected_history[3]["tool_call_id"], "call-history-1")
        self.assertEqual(projected_history[3]["content"], "历史工具结果")
        self.assertNotIn("LEGACY EPISODIC", repr(result["history_turns"]))
        self.assertNotIn("LEGACY SEMANTIC", repr(result["history_turns"]))

    def test_plain_projection_failure_does_not_fall_back_to_legacy_envelope_authority(self) -> None:
        memcore_manager = _PromptContextMemcoreManager(
            {
                "ok": True,
                "status": "ok",
                "raw": [{"source_id": "current", "role": "user", "content": "当前问题"}],
                "raw_text": "LEGACY RAW MUST STAY OUT",
                "episodic_text": "LEGACY EPISODIC MUST STAY OUT",
                "semantic_text": "LEGACY SEMANTIC MUST STAY OUT",
            },
            projection_payload={"ok": False, "status": "failed", "reason": "projection_build_failed"},
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="当前问题",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=100,
                character_pack_id="char",
            )

        self.assertEqual(result["memcore_projection_failure"]["status"], "unavailable")
        self.assertEqual(result["memcore_projection_failure"]["reason"], "projection_build_failed")
        self.assertNotIn("LEGACY", repr(result))

    def test_native_tool_projection_has_no_prompt_envelope_store_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            previous = store.add_message(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
                role="user",
                content="上一条消息",
                timestamp=1712399900,
            )
            assistant = store.add_message(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
                role="assistant",
                content="上一条回复",
                timestamp=1712399901,
            )
            current = store.add_message(
                profile_user_id="u1",
                session_id="s1",
                character_pack_id="char",
                role="user",
                content="现在的问题",
                timestamp=1712400000,
            )
            projected_previous = "MemCore projected previous"
            memcore_manager = _PromptContextMemcoreManager(
                {
                    "operation": "build_prompt_context",
                    "ok": True,
                    "status": "ok",
                    "raw": [previous, assistant, current],
                    "raw_text": "MEMCORE RAW",
                    "episodic_text": "",
                    "semantic_text": "",
                },
                projection_payload={
                    "ok": True,
                    "status": "ok",
                    "provider_profile": "openai_chat",
                    "messages": [
                        {
                            "payload": {"role": "user", "content": projected_previous},
                            "source_ids": [previous["source_id"]],
                        },
                        {
                            "payload": {"role": "assistant", "content": "上一条回复"},
                            "source_ids": [assistant["source_id"]],
                        },
                        {
                            "payload": {"role": "user", "content": "现在的问题"},
                            "source_ids": [current["source_id"]],
                        },
                    ],
                    "stable_prefix_hash": "c" * 64,
                    "projection_version": 1,
                    "compaction_generation": 0,
                    "projection_generation": 1,
                },
            )
            engine = _PromptContextEngine(memcore_manager=memcore_manager)
            engine.store = store
            engine._split_history_records = lambda **_kwargs: ([], dict(current))

            with patch.object(config, "MEMORY_BACKEND", "memcore"):
                response_builder.prepare_context(
                    engine,
                    session_id="s1",
                    profile_user_id="u1",
                    user_message="现在的问题",
                    recent_raw=[current],
                    recent_episodic_summaries=[],
                    recent_semantic_summaries=[],
                    confirmed_snippets=[],
                    now_ts=1712400000,
                    character_pack_id="char",
                )

            captured = engine.prompt_builder.kwargs
            self.assertEqual(captured["history_turns"][0]["content"], projected_previous)
            self.assertEqual(captured["history_turns"][1]["role"], "assistant")
            self.assertFalse(hasattr(store, "upsert_message_prompt_envelope"))
            self.assertFalse(hasattr(store, "get_message_prompt_envelopes"))
            self.assertFalse(hasattr(store, "prune_message_prompt_envelopes"))
            self.assertEqual(store.get_message_by_source_id(current["source_id"])["content"], "现在的问题")
            self.assertEqual(store.get_message_by_source_id(current["source_id"])["memory_metadata"], {})

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
            result = response_builder.prepare_context(
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

        self.assertEqual(result["memcore_projection_failure"]["status"], "unavailable")
        self.assertEqual(result["memcore_projection_failure"]["reason"], "projection_build_failed")
        self.assertEqual(engine.prompt_builder.calls, [])
        self.assertNotIn("LEGACY", repr(result))

    def test_plugin_proactive_prompt_context_isolates_conversation_history(self) -> None:
        previous_event = (
            "[2026-07-20 周一 08:50 | 上午] event.finance\n"
            "source: 东方财富\n"
            "title: 上一条财经事件"
        )
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": True,
                "status": "ok",
                "raw": [{"source_id": "current", "role": "user", "content": "真实插件事件"}],
                "raw_text": "MEMCORE RAW WITH CURRENT EVENT：真实插件事件",
                "episodic_text": "MEMCORE EPISODIC",
                "semantic_text": "MEMCORE SEMANTIC",
            },
            projection_payload={
                "ok": True,
                "status": "ok",
                "provider_profile": "openai_chat",
                "messages": [
                    {
                        "payload": {"role": "user", "content": "【蓬壶人】发来了一张图片。"},
                        "source_ids": ["group-image-placeholder"],
                    },
                    {
                        "payload": {"role": "assistant", "content": "上一条群聊回复。"},
                        "source_ids": ["group-chat-final"],
                    },
                    {
                        "payload": {"role": "user", "content": previous_event},
                        "source_ids": ["event-previous"],
                    },
                    {
                        "payload": {"role": "assistant", "content": "上一条事件的分析。"},
                        "source_ids": ["event-previous-final"],
                    },
                    {
                        "payload": {"role": "user", "content": "真实插件事件"},
                        "source_ids": ["current"],
                    },
                ],
                "stable_prefix_hash": "d" * 64,
                "projection_version": 1,
                "compaction_generation": 0,
                "projection_generation": 1,
            },
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(
            config, "MEMCORE_SHADOW_COMPARE", True
        ):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="真实插件事件",
                recent_raw=[{"role": "user", "content": "LEGACY RAW MUST STAY OUT", "timestamp": 1712400000}],
                recent_episodic_summaries=[{"diary_summary": "LEGACY EPISODIC", "timestamp": 1712400000}],
                recent_semantic_summaries=[{"semantic_summary": "LEGACY SEMANTIC", "timestamp": 1712400000}],
                confirmed_snippets=["AUTOMATIC RETRIEVAL MUST STAY OUT"],
                now_ts=1712400000,
                character_pack_id="char",
                extra_user_context="PLUGIN INSTRUCTION",
                stable_system_context="STABLE PLUGIN SYSTEM",
                prompt_scope="plugin_proactive",
            )

        captured = engine.prompt_builder.kwargs
        self.assertEqual(captured["raw_text"], "")
        self.assertEqual(captured["episodic_summary_text"], "")
        self.assertEqual(captured["semantic_summary_text"], "")
        self.assertEqual(captured["memory_text"], "AUTOMATIC RETRIEVAL MUST STAY OUT")
        self.assertEqual(captured["stable_system_context"], "STABLE PLUGIN SYSTEM")
        self.assertTrue(captured["current_message_in_raw"])
        self.assertEqual(
            captured["history_turns"],
            [
                {"role": "user", "content": previous_event},
                {"role": "assistant", "content": "上一条事件的分析。"},
            ],
        )
        self.assertEqual(captured["prompt_scope"], "plugin_proactive")
        self.assertEqual(result["prompt_scope"], "plugin_proactive")
        self.assertEqual(result["memcore_projection_read"]["status"], "active")
        self.assertEqual(result["memcore_projection_shadow"]["status"], "skipped")
        self.assertEqual(result["memcore_projection_shadow"]["reason"], "plugin_proactive_event_history_filtered")
        self.assertEqual(memcore_manager.compare_calls, [])
        self.assertFalse(hasattr(memcore_manager, "build_prompt_context"))
        self.assertEqual((repr(captured["history_turns"]) + result["user_prompt"]).count("真实插件事件"), 1)
        self.assertNotIn("插件", previous_event)
        self.assertNotIn("LEGACY RAW MUST STAY OUT", repr(captured))

    def test_plugin_proactive_scope_keeps_normal_akane_modules_enabled(self) -> None:
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": True,
                "status": "ok",
                "raw": [{"source_id": "current", "role": "user", "content": "插件事件"}],
                "raw_text": "PLUGIN EVENT RAW",
                "episodic_text": "",
                "semantic_text": "",
            },
            projection_payload={
                "ok": True,
                "status": "ok",
                "provider_profile": "openai_chat",
                "messages": [
                    {
                        "payload": {"role": "user", "content": "插件事件"},
                        "source_ids": ["current"],
                    }
                ],
                "stable_prefix_hash": "f" * 64,
                "projection_version": 1,
                "compaction_generation": 0,
                "projection_generation": 1,
            },
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)
        enabled_modules = {
            PromptModule.EXTRA_CONTEXT,
            PromptModule.CURRENT_VISUAL_STATE,
            PromptModule.PENDING_GIFTS,
            PromptModule.PERSONA,
        }
        care_values: list[bool] = []
        profile = SimpleNamespace(
            supports_thought_debug=False,
            system_prompt_override="",
            includes=lambda module: module in enabled_modules,
            mode_prompt_override=lambda **_kwargs: "",
            to_public_dict=lambda: {"name": "enabled"},
        )
        engine._get_prompt_profile_registry = lambda: SimpleNamespace(
            resolve=lambda _context, *, care_enabled: care_values.append(bool(care_enabled)) or profile
        )
        task_index_reads: list[dict[str, object]] = []
        attachment_index_reads: list[dict[str, object]] = []
        generated_index_reads: list[bool] = []
        engine._get_generated_file_service = lambda: generated_index_reads.append(True)
        engine._get_task_workspace_service = lambda: SimpleNamespace(
            activity_prompt_context_lifecycle=lambda: "event_backed",
            build_activity_prompt_context=lambda **kwargs: task_index_reads.append(dict(kwargs))
            or "TASK WORKSPACE CONTEXT"
        )
        engine._get_attachment_inbox_service = lambda: SimpleNamespace(
            activity_prompt_context_lifecycle=lambda: "event_backed",
            build_activity_prompt_context=lambda **kwargs: attachment_index_reads.append(dict(kwargs))
            or "ATTACHMENT FOCUS CONTEXT"
        )
        engine.gift_service = SimpleNamespace(
            build_pending_prompt_context=lambda **_kwargs: "PENDING GIFT CONTEXT",
            resolve_focus_asset=lambda **_kwargs: None,
        )
        engine._get_persona_card_service = lambda: SimpleNamespace(
            build_prompt_context=lambda **_kwargs: {
                "system_context": "PERSONA SYSTEM",
                "reference_context": "PERSONA REFERENCE",
                "active_id": "persona-1",
            }
        )
        engine._merge_prompt_persona_contexts = lambda _character, persona: dict(persona)
        engine._build_memory_relationship_context = lambda **_kwargs: "RELATIONSHIP CONTEXT"
        engine._build_current_visual_context = lambda **_kwargs: "CURRENT VISUAL CONTEXT"
        engine._build_extra_context_audit_sections = lambda candidates: [
            {"name": str(name), "text": str(text)} for name, text in candidates if str(text).strip()
        ]

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="插件事件",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=1712400000,
                prompt_scope="plugin_proactive",
                extra_user_context="TURN CONTEXT",
                client_context=ClientProtocolContext(
                    requested_mode=ClientMode.QQ_TEXT,
                    effective_mode=ClientMode.QQ_TEXT,
                ),
            )

        captured = engine.prompt_builder.kwargs
        self.assertEqual(care_values, [True])
        self.assertIn("RELATIONSHIP CONTEXT", captured["extra_context"])
        self.assertNotIn("TASK WORKSPACE CONTEXT", captured["extra_context"])
        self.assertNotIn("ATTACHMENT FOCUS CONTEXT", captured["extra_context"])
        self.assertNotIn("PENDING GIFT CONTEXT", captured["extra_context"])
        self.assertNotIn("TURN CONTEXT", captured["extra_context"])
        self.assertEqual(
            captured["volatile_extra_context"],
            "PENDING GIFT CONTEXT\n\nTURN CONTEXT",
        )
        self.assertNotIn("TASK WORKSPACE CONTEXT", captured["volatile_extra_context"])
        self.assertNotIn("ATTACHMENT FOCUS CONTEXT", captured["volatile_extra_context"])
        self.assertEqual(task_index_reads, [])
        self.assertEqual(attachment_index_reads, [])
        self.assertEqual(generated_index_reads, [])
        self.assertEqual(
            result["prompt_context_lifecycle"],
            {
                "event_timeline_authoritative": True,
                "skipped_event_backed": ["task_workspace", "attachment_focus"],
            },
        )
        self.assertEqual(captured["persona_system_context"], "PERSONA SYSTEM")
        self.assertEqual(captured["persona_reference_context"], "PERSONA REFERENCE")
        self.assertEqual(captured["current_visual_context"], "CURRENT VISUAL CONTEXT")
        self.assertEqual(result["memcore_projection_read"]["status"], "active")

    def test_automatic_character_library_context_stays_after_history_not_in_persona_prefix(self) -> None:
        memcore_manager = _PromptContextMemcoreManager(
            {
                "operation": "build_prompt_context",
                "ok": True,
                "status": "ok",
                "raw": [{"source_id": "current", "role": "user", "content": "聊聊魔理沙"}],
                "raw_text": "聊聊魔理沙",
                "episodic_text": "",
                "semantic_text": "",
            },
            projection_payload={
                "ok": True,
                "status": "ok",
                "provider_profile": "openai_chat",
                "messages": [
                    {
                        "payload": {"role": "user", "content": "先前的稳定对话"},
                        "source_ids": ["previous"],
                    },
                    {
                        "payload": {"role": "user", "content": "聊聊魔理沙"},
                        "source_ids": ["current"],
                    },
                ],
                "stable_prefix_hash": "e" * 64,
                "projection_version": 1,
                "compaction_generation": 0,
                "projection_generation": 1,
            },
        )
        engine = _PromptContextEngine(memcore_manager=memcore_manager)
        enabled_modules = {PromptModule.EXTRA_CONTEXT, PromptModule.PERSONA}
        profile = SimpleNamespace(
            supports_thought_debug=False,
            system_prompt_override="",
            includes=lambda module: module in enabled_modules,
            mode_prompt_override=lambda **_kwargs: "",
            to_public_dict=lambda: {"name": "character-test"},
        )
        engine._get_prompt_profile_registry = lambda: SimpleNamespace(
            resolve=lambda _context, **_kwargs: profile
        )
        engine._build_desktop_pet_character_pack_prompt_context = lambda **_kwargs: {
            "system_context": "STABLE CHARACTER SYSTEM",
            "reference_context": "STABLE CHARACTER REFERENCE",
            "resource_context": "ACTIVE OUTFIT EMOTIONS",
            "active_id": "char",
        }
        engine._merge_prompt_persona_contexts = lambda character, _profile: dict(character)
        engine.desktop_pet_character_resources = SimpleNamespace(
            context_libraries=SimpleNamespace(
                build_automatic_context=lambda character_pack_id, text: (
                    "AUTOMATIC MARISA REFERENCE" if character_pack_id == "char" and "魔理沙" in text else ""
                )
            )
        )

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = response_builder.prepare_context(
                engine,
                session_id="s1",
                profile_user_id="u1",
                user_message="聊聊魔理沙",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=1712400000,
                character_pack_id="char",
                client_context=ClientProtocolContext(
                    requested_mode=ClientMode.QQ_TEXT,
                    effective_mode=ClientMode.QQ_TEXT,
                ),
            )

        captured = engine.prompt_builder.kwargs
        self.assertEqual(captured["persona_system_context"], "STABLE CHARACTER SYSTEM")
        self.assertEqual(captured["persona_reference_context"], "STABLE CHARACTER REFERENCE")
        self.assertNotIn("AUTOMATIC MARISA REFERENCE", captured["persona_reference_context"])
        self.assertNotIn("ACTIVE OUTFIT EMOTIONS", captured["persona_system_context"])
        self.assertNotIn("ACTIVE OUTFIT EMOTIONS", captured["persona_reference_context"])
        self.assertIn("AUTOMATIC MARISA REFERENCE", captured["volatile_extra_context"])
        self.assertNotIn("ACTIVE OUTFIT EMOTIONS", captured["volatile_extra_context"])
        self.assertIn("ACTIVE OUTFIT EMOTIONS", captured["extra_context"])
        self.assertNotIn("AUTOMATIC MARISA REFERENCE", repr(captured["history_turns"]))
        self.assertNotIn("ACTIVE OUTFIT EMOTIONS", repr(captured["history_turns"]))
        self.assertIn("AUTOMATIC MARISA REFERENCE", result["ephemeral_turns"][0]["content"])
        self.assertNotIn("ACTIVE OUTFIT EMOTIONS", result["ephemeral_turns"][0]["content"])

    def test_persona_context_merge_preserves_resource_context_as_a_separate_channel(self) -> None:
        merged = AkaneMemoryEngine._merge_prompt_persona_contexts(
            {
                "system_context": "CHARACTER SYSTEM",
                "reference_context": "CHARACTER REFERENCE",
                "resource_context": "ACTIVE CHARACTER RESOURCES",
                "active_id": "character",
            },
            {
                "system_context": "PROFILE SYSTEM",
                "reference_context": "PROFILE REFERENCE",
                "active_id": "profile",
            },
        )

        self.assertEqual(merged["system_context"], "CHARACTER SYSTEM\n\nPROFILE SYSTEM")
        self.assertEqual(merged["reference_context"], "CHARACTER REFERENCE\n\nPROFILE REFERENCE")
        self.assertEqual(merged["resource_context"], "ACTIVE CHARACTER RESOURCES")
        self.assertEqual(merged["active_id"], "profile")

    def test_character_pack_prompt_context_forwards_host_runtime_outfits(self) -> None:
        captured: dict[str, object] = {}

        def build_context(_pack_id: str, **kwargs) -> dict[str, str]:
            captured.update(kwargs)
            return {
                "system_context": "SYSTEM",
                "reference_context": "REFERENCE",
                "resource_context": "ACTIVE RESOURCES",
                "active_id": "character",
            }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.desktop_pet_character_resources = SimpleNamespace(
            build_persona_prompt_context=build_context,
        )
        runtime_outfits = [
            {
                "id": "gift_outfit",
                "name": "礼服",
                "emotions": [{"id": "quiet", "name": "安静"}],
            }
        ]

        context = engine._build_desktop_pet_character_pack_prompt_context(
            character_pack_id="character",
            resource_manifest=SimpleNamespace(),
            client_mode="desktop_pet",
            preferred_outfit="gift_outfit",
            extra_character_outfits=runtime_outfits,
        )

        self.assertEqual(captured["extra_character_outfits"], runtime_outfits)
        self.assertEqual(context["resource_context"], "ACTIVE RESOURCES")

    def test_read_memory_timeline_tool_uses_memcore_adapter_in_memcore_mode(self) -> None:
        legacy = _TimelineLegacyService()
        memcore_manager = _TimelineMemcoreManager()
        service = MemcoreTimelineToolService(legacy_service=legacy, memcore_manager=memcore_manager)
        handler = ReadMemoryTimelineToolHandler(timeline_service=service)
        call = handler.normalize_call(
            {
                "type": "read_memory_timeline",
                "date_from": "2026-06-13",
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

    def test_read_memory_timeline_raw_anchor_stays_in_current_conversation(self) -> None:
        memcore_manager = _TimelineMemcoreManager()
        service = MemcoreTimelineToolService(legacy_service=None, memcore_manager=memcore_manager)
        handler = ReadMemoryTimelineToolHandler(timeline_service=service)
        call = handler.normalize_call(
            {
                "type": "read_memory_timeline",
                "anchor_source_id": "raw-hit-1",
                "before_turns": 3,
                "after_turns": 5,
            }
        )
        self.assertIsNotNone(call)
        assert call is not None

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = handler.execute(
                call=call,
                context=ToolExecutionContext(
                    profile_user_id="u1",
                    session_id="group:42",
                    character_pack_id="char",
                    now_ts=_ts(2026, 6, 13, 12, 0),
                    visual_payload={},
                    current_user_source_id="current-query",
                ),
            )

        forwarded = memcore_manager.calls[0]
        self.assertEqual(forwarded["session_id"], "group:42")
        self.assertEqual(forwarded["anchor_source_id"], "raw-hit-1")
        self.assertEqual(forwarded["before_turns"], 3)
        self.assertEqual(forwarded["after_turns"], 5)
        self.assertFalse(forwarded["cross_conversation"])
        self.assertIn("完整对话 turn", result.followup_context)

    def test_read_memory_timeline_keeps_structured_invalid_mode_error(self) -> None:
        manager = SimpleNamespace(
            enabled=True,
            available=True,
            read_memory_timeline=lambda **_kwargs: {
                "operation": "read_memory_timeline",
                "ok": False,
                "status": "invalid_filter",
                "reason": "timeline_modes_are_mutually_exclusive",
                "backend": "memcore",
                "date_from": "2026-06-13",
                "date_to": "",
                "time_periods": [],
                "anchor_source_id": "raw-hit-1",
                "before_turns": 0,
                "after_turns": 0,
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
            },
        )
        service = MemcoreTimelineToolService(legacy_service=None, memcore_manager=manager)

        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            result = service.read(
                profile_user_id="u1",
                session_id="group:42",
                character_pack_id="char",
                date_from="2026-06-13",
                anchor_source_id="raw-hit-1",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid_filter")
        self.assertEqual(result["reason"], "timeline_modes_are_mutually_exclusive")
        self.assertIn("不能同时使用", service.render_tool_context(result))

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
        call = handler.normalize_call({"type": "read_memory_timeline", "date_from": "2026-06-13"})
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

    def test_tool_trace_text_is_sanitized_and_bounded_before_memcore_write(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        source = "api_key=secret-value " + ("证据" * 900)
        with patch.object(config, "MEMCORE_TOOL_TRACE_MAX_CHARS", 1000):
            result = engine._sanitize_tool_trace_text(source)

        self.assertNotIn("secret-value", result)
        self.assertIn("tool_trace_truncated", result)
        self.assertLess(len(result), 1100)

    def test_prompt_layer_high_water_trimming_makes_monotonic_progress(self) -> None:
        value = "\n".join(f"line-{index}" for index in range(20))
        lengths = []
        while value:
            lengths.append(len(value))
            value = response_builder._drop_oldest_prompt_lines(value)
        self.assertTrue(all(later < earlier for earlier, later in zip(lengths, lengths[1:])))

    def test_final_prompt_cache_key_ignores_volatile_persona_and_tool_readiness_state(self) -> None:
        from companion_v01.prompt_blocks import CURRENT_ASSISTANT_STATE_MARKER

        base = {
            "system_prompt": f"stable rules\n{CURRENT_ASSISTANT_STATE_MARKER}\nstate A",
            "fallback": {"persona": {"active": "akane_v1"}},
            "prompt_profile": {"id": "qq_text"},
            "domain_profile": {"id": "default"},
            "prompt_cache_scope_hash": "conversation-a",
            "tool_prompt_context_hash": "tool-context-a",
            "native_tools": [{"type": "function", "function": {"name": "search"}}],
        }
        changed_state = dict(base, system_prompt=f"stable rules\n{CURRENT_ASSISTANT_STATE_MARKER}\nstate B")
        changed_tools = dict(base, native_tools=[{"type": "function", "function": {"name": "quote"}}])
        changed_scope = dict(base, prompt_scope="plugin_proactive")
        changed_dynamic_event = dict(base, user_prompt="a different finance event")
        changed_stable_system = dict(base, stable_system_context_hash="different-stable-system")
        changed_conversation = dict(base, prompt_cache_scope_hash="conversation-b")
        changed_tool_context = dict(base, tool_prompt_context_hash="tool-context-b")

        self.assertEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_state),
        )
        self.assertEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_tools),
        )
        self.assertEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_scope),
        )
        self.assertEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_dynamic_event),
        )
        interleaved_keys = {
            AkaneMemoryEngine._final_prompt_cache_key(
                dict(
                    base,
                    prompt_scope=scope,
                    user_prompt=current_message,
                )
            )
            for scope, current_message in (
                ("", "ordinary message A"),
                ("plugin_proactive", "event.finance A"),
                ("", "ordinary message B"),
                ("plugin_proactive", "event.finance B"),
            )
        }
        self.assertEqual(len(interleaved_keys), 1)
        self.assertNotEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_stable_system),
        )
        self.assertNotEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_conversation),
        )
        self.assertEqual(
            AkaneMemoryEngine._final_prompt_cache_key(base),
            AkaneMemoryEngine._final_prompt_cache_key(changed_tool_context),
        )
        with patch("companion_v01.engine.FINAL_PROMPT_CACHE_LAYOUT_VERSION", "responses-next-layout"):
            changed_layout_key = AkaneMemoryEngine._final_prompt_cache_key(base)
        self.assertNotEqual(AkaneMemoryEngine._final_prompt_cache_key(base), changed_layout_key)
        self.assertTrue(AkaneMemoryEngine._final_prompt_cache_key(changed_scope).startswith("chat:final:"))
        self.assertEqual(AkaneMemoryEngine._final_response_max_attempts(changed_scope), 1)


if __name__ == "__main__":
    unittest.main()
