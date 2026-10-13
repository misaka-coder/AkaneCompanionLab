"""Response preparation boundaries during compaction and consecutive requests."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import config
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine_services import response_builder
from companion_v01.engine_services.final_response_request import FinalResponseRequestMixin
from tests.test_memcore_integration import _PromptContextEngine, _PromptContextMemcoreManager


def _record(content, source_id):
    return {"payload": {"role": "user", "content": content}, "source_ids": [source_id]}


def _catalog_projection(generation):
    def catalog(role, client):
        return f"prompt-context:capability-catalog:{generation}:{role}:digest:scope:anchor:{client}"

    return {
        "ok": True,
        "status": "ok",
        "provider_profile": "openai_chat",
        "compaction_generation": generation,
        "messages": [
            _record(f"Desktop base {generation}", catalog("base", "desktop_pet")),
            _record(f"QQ base {generation}", catalog("base", "qq_text")),
            _record(f"Desktop update {generation}", catalog("update", "desktop_pet")),
            _record(f"QQ update {generation}", catalog("update", "qq_text")),
            _record("Historical material " * 300, "history"),
            _record("Current question", "current"),
        ],
    }


class _CompactingManager(_PromptContextMemcoreManager):
    def __init__(self, next_projection):
        super().__init__({}, projection_payload=_catalog_projection(2))
        self.next_projection = next_projection
        self.compact_calls = 0

    def compact_due_sync(self, **_kwargs):
        self.compact_calls += 1
        self.projection_payload = deepcopy(self.next_projection)
        return {"ok": True, "status": "completed"}


class _RequestContextEngine(_PromptContextEngine, FinalResponseRequestMixin):
    pass


class ResponseContextTests(unittest.TestCase):
    def test_chat_entry_runs_prompt_stages_and_preserves_native_images(self):
        for request_mode in ("independent", "chat_prefix"):
            for client_mode in (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET):
                with self.subTest(request_mode=request_mode, client_mode=client_mode):
                    manager = _CompactingManager(_catalog_projection(3))
                    engine = _RequestContextEngine(memcore_manager=manager)
                    engine._build_memcore_request_observer = Mock(return_value=None)
                    images = [{"url": "data:image/png;base64,fixture-image"}]
                    with (
                        patch.object(config, "MEMORY_BACKEND", "memcore"),
                        patch.object(config, "MEMCORE_COMPACTION_REQUEST_MODE", request_mode),
                        patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0),
                        patch.object(response_builder, "fit_context_budget",
                                     wraps=response_builder.fit_context_budget) as fit,
                    ):
                        request = engine._prepare_final_response_request(
                            session_id="session", profile_user_id="owner", character_pack_id="character",
                            user_message="Current question", recent_raw=[], recent_episodic_summaries=[],
                            recent_semantic_summaries=[], confirmed_snippets=[], now_ts=100,
                            client_context=ClientProtocolContext(effective_mode=client_mode),
                            request_projection_state={"current_user_source_id": "current"},
                            user_images=images,
                        )
                    self.assertIsNone(request.failure_output)
                    self.assertIs(request.user_images, images)
                    self.assertIs(fit.call_args.args[1].native_user_images, images)
                    self.assertIn("Current question", request.generation_context["user_prompt"])
                    self.assertEqual(len(engine.prompt_builder.calls), 1)
                    self.assertEqual(manager.compact_calls, 0)
                    engine._build_memcore_request_observer.assert_called_once()

    def prepare(self, engine, **overrides):
        values = dict(
            session_id="session",
            profile_user_id="owner",
            character_pack_id="character",
            user_message="Current question",
            current_user_source_id="current",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=100,
        )
        values.update(overrides)
        return response_builder.prepare_context(engine, **values)

    def test_shared_catalog_without_writer_keeps_client_scope_after_compaction(self):
        for mode, own, other in ((ClientMode.DESKTOP_PET, "Desktop", "QQ"), (ClientMode.QQ_TEXT, "QQ", "Desktop")):
            with self.subTest(mode=mode):
                manager = _CompactingManager(_catalog_projection(3))
                engine = _PromptContextEngine(memcore_manager=manager)
                context = ClientProtocolContext(effective_mode=mode)
                with (
                    patch.object(config, "MEMORY_BACKEND", "memcore"),
                    patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 100),
                    patch(
                        "companion_v01.owner_private_session.current_private_scope",
                        return_value=SimpleNamespace(shared=True),
                    ),
                ):
                    result = self.prepare(engine, client_context=context)

                self.assertEqual(manager.compact_calls, 1)
                self.assertTrue(result["prompt_budget"]["compact_attempted"])
                self.assertEqual(result["memcore_projection_read"]["compaction_generation"], 3)
                self.assertEqual(engine.prompt_builder.calls[-1]["capability_catalog_context"], f"{own} base 3")
                for call in engine.prompt_builder.calls:
                    content = str(call["history_turns"])
                    self.assertNotIn(f"{other} update", content)
                self.assertEqual(manager.next_projection, _catalog_projection(3))

    def test_failed_projection_refresh_stops_before_building_a_new_prompt(self):
        manager = _CompactingManager({"ok": False, "status": "failed", "reason": "controlled_refresh_failure"})
        engine = _PromptContextEngine(memcore_manager=manager)
        with (
            patch.object(config, "MEMORY_BACKEND", "memcore"),
            patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 100),
        ):
            result = self.prepare(engine)
        self.assertEqual(manager.compact_calls, 1)
        self.assertEqual(len(engine.prompt_builder.calls), 1)
        self.assertEqual(result["memcore_projection_failure"]["reason"], "projection_build_failed")
        self.assertNotIn("native_tools", result)

    def test_legacy_budget_preserves_current_input_and_supplied_records(self):
        manager = _CompactingManager(_catalog_projection(3))
        engine = _PromptContextEngine(memcore_manager=manager)
        engine._build_history_turns = lambda records: deepcopy(records)
        raw = [
            {"role": "user", "source_id": "old", "content": "Previous material " * 300},
            {"role": "user", "source_id": "current", "content": "Current question"},
        ]
        original = deepcopy(raw)
        with (
            patch.object(config, "MEMORY_BACKEND", "legacy"),
            patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 100),
        ):
            result = self.prepare(
                engine,
                recent_raw=raw,
                recent_episodic_summaries=[{"summary": "Unused legacy diary"}],
                recent_semantic_summaries=[{"summary": "Unused legacy semantic layer"}],
            )
        self.assertEqual(manager.compact_calls, 0)
        self.assertEqual(result["prompt_budget"]["trimmed_layers"], ["raw"])
        self.assertLessEqual(result["prompt_budget"]["final_estimated_tokens"], 100)
        self.assertIn("Current question", result["user_prompt"])
        self.assertNotIn("Previous material", str(result))
        self.assertEqual(engine.prompt_builder.calls[-1]["episodic_summary_text"], "")
        self.assertEqual(engine.prompt_builder.calls[-1]["semantic_summary_text"], "")
        self.assertEqual(raw, original)

    def test_consecutive_scopes_do_not_share_projection_or_budget_worksets(self):
        original = _catalog_projection(2)
        manager = _CompactingManager(original)
        engine = _PromptContextEngine(memcore_manager=manager)
        raw_records = [
            {"role": "user", "source_id": "old", "content": "Raw previous material"},
            {"role": "user", "source_id": "current", "content": "Current question"},
        ]
        supplied_turns = [{"role": "assistant", "content": "Exact tool continuation"}]
        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            with patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 100):
                first = self.prepare(engine, session_id="first", recent_raw=raw_records, post_user_turns=supplied_turns)
            with patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0):
                second = self.prepare(engine, session_id="second", recent_raw=raw_records)
        self.assertNotEqual(first["prompt_cache_scope_hash"], second["prompt_cache_scope_hash"])
        self.assertTrue(first["prompt_budget"]["trimmed_layers"])
        self.assertEqual(second["prompt_budget"]["trimmed_layers"], [])
        self.assertTrue(second["history_turns"])
        self.assertEqual(second["post_user_turns"], [])
        self.assertEqual(raw_records[0]["content"], "Raw previous material")
        self.assertEqual(supplied_turns, [{"role": "assistant", "content": "Exact tool continuation"}])
        self.assertEqual(manager.projection_payload, original)


if __name__ == "__main__":
    unittest.main()
