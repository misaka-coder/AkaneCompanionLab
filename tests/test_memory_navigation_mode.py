"""No-model memory deployment through Akane's real manager and tool paths."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from companion_v01.capability_registry_runtime.modules import build_default_modules
from companion_v01.engine_services.service_factories import ServiceFactoryMixin
from companion_v01.memcore_integration.contracts import normalize_retrieval_mode
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_handlers.catalog import build_builtin_tool_handlers
from tests import test_memcore_keyword_catalog as keyword_catalog
from tests import test_memcore_sources_batch as sources_batch
from tests import test_tool_exposure_memcore as exposure
from tests.test_capability_exposure import CountingTool


def navigation_manager(**kwargs):
    kwargs.update(retrieval_mode="navigation", embedding_provider=None)
    return MemcoreManager(**kwargs)


class NavigationKeywordCatalogSmokeTests(keyword_catalog.MemcoreKeywordCatalogSmokeTests):
    def setUp(self):
        with patch.object(keyword_catalog, "MemcoreManager", side_effect=navigation_manager):
            super().setUp()
        self.assertIsNone(self.manager._index)
        self.assertFalse(self.manager._background_futures)


class NavigationSourcesBatchSmokeTests(sources_batch.MemcoreSourcesBatchSmokeTests):
    def setUp(self):
        with patch.object(sources_batch, "MemcoreManager", side_effect=navigation_manager):
            super().setUp()
        self.assertIsNone(self.manager._index)
        self.assertFalse(self.manager._warmed_index_keys)


class NavigationModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "memory.db"
        self.scope = dict(profile_user_id="navigation", session_id="chat", character_pack_id="fixture")
        backend = patch.object(config, "MEMORY_BACKEND", "memcore")
        backend.start()
        self.addCleanup(backend.stop)

    def manager(self):
        manager = navigation_manager(
            backend="memcore",
            storage_path=self.path,
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=True,
            llm=object(),
        )
        self.addCleanup(manager.close)
        return manager

    def test_factory_never_constructs_any_embedding_provider_in_navigation(self):
        for provider_name in ("auto", "huggingface", "remote", "jina", "hashed"):
            engine = ServiceFactoryMixin()
            with (
                patch.object(config, "MEMCORE_RETRIEVAL_MODE", "navigation"),
                patch.object(config, "EMBEDDING_PROVIDER", provider_name),
                patch(
                    "companion_v01.engine_services.service_factories.HuggingFaceEmbeddingProvider",
                    side_effect=AssertionError,
                ),
                patch(
                    "companion_v01.engine_services.service_factories.RemoteEmbeddingProvider",
                    side_effect=AssertionError,
                ),
                patch(
                    "companion_v01.engine_services.service_factories.JinaEmbeddingProvider", side_effect=AssertionError
                ),
                patch(
                    "companion_v01.engine_services.service_factories.HashedEmbeddingProvider",
                    side_effect=AssertionError,
                ),
                patch(
                    "companion_v01.engine_services.service_factories.CachedEmbeddingProvider",
                    side_effect=AssertionError,
                ),
            ):
                engine.embedding_provider = engine._build_embedding_provider()
            self.assertIsNone(engine.embedding_provider)
            self.assertEqual(engine._embedding_startup_status["status"], "disabled")
            # A later config override cannot describe this constructed engine as hybrid.
            with patch.object(config, "MEMCORE_RETRIEVAL_MODE", "hybrid"):
                self.assertEqual(engine.snapshot_embedding_runtime()["status"], "disabled")
            self.assertEqual(engine.snapshot_embedding_runtime()["cuda_allocated_bytes"], 0)

    def test_navigation_manager_bootstraps_writes_and_projects_without_index_jobs(self):
        with (
            patch(
                "companion_v01.memcore_integration.lifecycle.build_akane_embedding_provider", side_effect=AssertionError
            ),
            patch("memcore.InMemoryVectorIndex", side_effect=AssertionError),
        ):
            manager = self.manager()
            self.assertTrue(manager.available)
            opened = manager.begin_input_turn(
                {"source_id": "user-1", "content": "我喜欢冰可乐。", "timestamp": 1775782800}, **self.scope
            )
            self.assertTrue(opened["ok"], opened)
            completed = manager.complete_input_turn(
                turn_id=opened["turn_id"],
                assistant_record={"source_id": "assistant-1", "content": "我记下了。", "timestamp": 1775782801},
                provider_output_raw="我记下了。",
                memory_metadata=None,
                **self.scope,
            )
            self.assertTrue(completed["ok"], completed)
            projection = manager.build_context_projection(provider_profile="openai", **self.scope)
            self.assertTrue(projection["ok"], projection)
            timeline = manager.read_memory_timeline(date_from="2026-04-10", **self.scope)
            self.assertTrue(timeline["ok"], timeline)
        self.assertIsNone(manager._embedding)
        self.assertIsNone(manager._index)
        self.assertFalse(manager._warmed_index_keys)
        self.assertFalse(manager._background_futures)
        self.assertEqual(manager.status()["retrieval_mode"], "navigation")
        self.assertFalse(manager.status()["degraded_embedding"])

    def test_explicit_and_shadow_retrieval_are_truthfully_disabled(self):
        manager = self.manager()
        with patch.object(manager, "_get_system_or_none", side_effect=AssertionError("retrieval attempted")):
            result = manager.retrieve_memory(query="我喜欢什么", **self.scope)
            shadow = manager.shadow_retrieve_memory(query="我喜欢什么", **self.scope)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "retrieval_disabled")
        self.assertEqual(result["snippets"], [])
        self.assertEqual(shadow["status"], "disabled")
        self.assertEqual(shadow["reason"], "retrieval_disabled")

    def test_host_llm_compaction_continues_to_semantic_memory_without_embedding(self):
        class SummaryLLM:
            def call_aux_json(self, *, prompt_cache_key, **kwargs):
                if prompt_cache_key == "memcore:summary":
                    return {
                        "diary_summary": "保留可乐偏好",
                        "period_label": "对话",
                        "event_type": "闲聊",
                        "importance": 0.7,
                        "key_events": ["喜欢可乐"],
                        "core_facts": ["偏好可乐"],
                    }
                return {
                    "semantic_summary": "持续喜欢可乐",
                    "importance": 0.8,
                    "stable_facts": ["偏好可乐"],
                    "recurring_topics": ["可乐"],
                    "important_people": [],
                    "open_loops": [],
                }

        with (
            patch.object(config, "MEMCORE_RAW_TOKEN_TRIGGER", 1000),
            patch.object(config, "EPISODIC_COMPACT_TRIGGER_COUNT", 3),
            patch.object(config, "EPISODIC_COMPACT_BATCH_SIZE", 2),
        ):
            manager = navigation_manager(
                backend="memcore",
                storage_path=self.path,
                visible_scope="conversation",
                enable_flavor=False,
                shadow_compare=False,
                llm=SummaryLLM(),
            )
        self.addCleanup(manager.close)
        original = "原话定位记号:我喜欢冰可乐。" * 300
        for number in range(5):
            timestamp = 1775782800 + number * 60
            opened = manager.begin_input_turn(
                {"source_id": f"u-{number}", "content": original, "timestamp": timestamp}, **self.scope
            )
            self.assertTrue(opened["ok"], opened)
            completed = manager.complete_input_turn(
                turn_id=opened["turn_id"],
                assistant_record={"source_id": f"a-{number}", "content": "我记下了。", "timestamp": timestamp + 1},
                provider_output_raw="我记下了。",
                memory_metadata=None,
                **self.scope,
            )
            self.assertTrue(completed["ok"], completed)
            compacted = manager.compact_due_sync(**self.scope)
            self.assertTrue(compacted["ok"], compacted)
        self.assertEqual(compacted["stats"]["index_status"], "disabled")
        system = manager._get_system(**self.scope)
        semantic = system.browse_memory(keywords=["可乐"], node_types=["semantic"])
        self.assertGreater(semantic["matched_card_count"], 0)
        self.assertIn(original, system.read_timeline(date_from="2026-04-10")["text"])

    def test_engine_manager_uses_the_constructed_mode_after_config_changes(self):
        engine = ServiceFactoryMixin()
        engine.base_dir = self.path.parent
        engine.memcore_storage_path = self.path
        engine.llm = object()
        with patch.object(config, "MEMCORE_RETRIEVAL_MODE", "navigation"):
            engine.embedding_provider = engine._build_embedding_provider()
        with patch.object(config, "MEMCORE_RETRIEVAL_MODE", "hybrid"):
            manager = MemcoreManager.from_engine(engine)
        self.addCleanup(manager.close)
        self.assertTrue(manager.available)
        self.assertEqual(manager.retrieval_mode, "navigation")

    def test_host_default_validation_and_legacy_dual_compatibility(self):
        self.assertEqual(config.Settings.model_fields["MEMCORE_RETRIEVAL_MODE"].default, "navigation")
        for backend in ("legacy", "dual"):
            self.assertEqual(normalize_retrieval_mode("navigation", backend=backend), "hybrid")
        for mode in ("keyword", "", None):
            with self.assertRaises(ValueError):
                normalize_retrieval_mode(mode)

    def test_real_factory_removes_native_legacy_and_discovery_retrieval_entries(self):
        kwargs = dict(
            store=None,
            sticker_assets=None,
            capability_offer_source=None,
            capability_config_base_dir=self.path.parent,
            memory_timeline_service=None,
            context_libraries=None,
            attachment_service=None,
            image_material_resolver=None,
            workspace_file_service=None,
            attachment_ingest_service=None,
            generated_file_service=None,
            retrieve_fn=lambda **_: None,
        )
        with patch("companion_v01.tool_handlers.catalog.RetrieveMemoryToolHandler", side_effect=AssertionError):
            handlers = build_builtin_tool_handlers(memory_retrieval_enabled=False, **kwargs)
        names = ("browse_memory", "open_memory", "read_memory_timeline", "list_memory_conversations")
        memory = {name: handlers[name] for name in names}
        self.assertNotIn("retrieve_memory", handlers)
        native = build_openai_native_tool_specs(memory)
        legacy = "\n".join(handler.build_prompt_instruction() for handler in memory.values())
        self.assertNotIn("retrieve_memory", json.dumps(native) + legacy)
        self.assertNotIn("retrieve_for_turn", json.dumps(native) + legacy)
        hints = "\n".join(module.light_hint for module in build_default_modules())
        self.assertNotIn("retrieve_memory", hints)
        self.assertIn("retrieve_memory", build_builtin_tool_handlers(memory_retrieval_enabled=True, **kwargs))


class NavigationExposureTests(unittest.TestCase):
    setUp = exposure.ToolExposureMemcoreTests.setUp
    open_manager = exposure.ToolExposureMemcoreTests.open_manager
    freeze = exposure.ToolExposureMemcoreTests.freeze

    def test_old_hybrid_declaration_is_revoked_after_restart_without_publishing_other_pending_tools(self):
        self.engine.tool_handlers["retrieve_memory"] = CountingTool("retrieve_memory")
        original, _, _ = self.freeze()
        self.assertIn("retrieve_memory", original.schema_tool_names)
        self.manager.close()
        self.manager = navigation_manager(
            backend="memcore",
            storage_path=self.path,
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=False,
            llm=object(),
        )
        self.engine.memcore_manager = self.manager
        self.engine.tool_handlers.pop("retrieve_memory")
        self.engine.tool_handlers["demo.beta"] = CountingTool("demo.beta")
        restored, _, receipt = self.freeze()
        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["memory_mode_revocation_exception"])
        self.assertTrue(receipt["pending"])
        self.assertNotIn("retrieve_memory", restored.schema_tool_names)
        self.assertNotIn("retrieve_memory", restored.tool_names)
        self.assertNotIn("retrieve_memory", restored.capability_catalog.capability_ids)
        self.assertNotIn("retrieve_memory", restored.published_contracts)
        self.assertNotIn("demo.beta", restored.schema_tool_names)
        self.assertEqual(restored.published_contracts["demo.alpha"], original.published_contracts["demo.alpha"])
