from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from capcore import CapabilityToolSpec

import config
from companion_v01.capability_discovery import CapabilityDiscoveryCatalog
from companion_v01.client_protocol import ClientMode, ClientProtocolContext, default_capabilities_for_mode
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_handlers.core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult
from companion_v01.tool_orchestration_engine import build_native_tool_decision_plan, validate_legacy_tool_call
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD


class _DemoHandler(BaseToolHandler):
    def __init__(self, capability_id: str, display_name: str) -> None:
        self.tool_type = capability_id
        self._spec = CapabilityToolSpec(
            capability_id=capability_id,
            display_name=display_name,
            description=f"Deterministic {display_name} capability",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            output_schema={"type": "object"},
            risk="low",
            confirm="never",
            effects=(),
            visible_in=("desktop",),
        )

    def tool_spec(self):
        return self._spec

    def normalize_call(self, value):
        if not isinstance(value, dict) or str(value.get("type") or "") != self.tool_type:
            return None
        text = value.get("text")
        if not isinstance(text, str):
            return None
        return {"type": self.tool_type, "text": text}

    def execute(self, *, call, context) -> ToolExecutionResult:
        return ToolExecutionResult(tool_type=self.tool_type, followup_context=str(call.get("text") or ""))


def _engine() -> SimpleNamespace:
    handlers = {
        f"demo.{name}": _DemoHandler(f"demo.{name}", name.title())
        for name in ("alpha", "beta", "gamma")
    }
    engine = SimpleNamespace(tool_handlers=handlers)
    engine._resolve_tool_handlers = lambda capability_selection=None, **_: {
        name: handler
        for name, handler in dict(getattr(capability_selection, "resolved_handlers", {}) or {}).items()
    }
    return engine


class CapabilityDiscoveryTests(unittest.TestCase):
    def test_search_is_short_paginated_and_cursor_is_scope_bound(self) -> None:
        engine = _engine()
        catalog = CapabilityDiscoveryCatalog(
            engine.tool_handlers,
            profile_user_id="alice",
            session_id="session-1",
            client_mode="desktop_pet",
            cursor_secret=b"test-discovery-secret",
        )
        first = catalog.search(limit=1)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(set(first["items"][0]), {"capability_id", "display_name", "description"})
        self.assertNotIn("input_schema", first["items"][0])
        self.assertTrue(first["next_cursor"])

        second = catalog.search(cursor=first["next_cursor"], limit=1)
        self.assertEqual(second["status"], "ok")
        self.assertNotEqual(first["items"][0]["capability_id"], second["items"][0]["capability_id"])

        tampered = catalog.search(cursor=first["next_cursor"] + "x", limit=1)
        self.assertEqual(tampered["reason"], "cursor_invalid")
        changed_query = catalog.search(query="alpha", cursor=first["next_cursor"], limit=1)
        self.assertEqual(changed_query["reason"], "cursor_invalid")

        other_scope = CapabilityDiscoveryCatalog(
            engine.tool_handlers,
            profile_user_id="bob",
            session_id="session-1",
            client_mode="desktop_pet",
            cursor_secret=b"test-discovery-secret",
        )
        self.assertEqual(
            other_scope.search(cursor=first["next_cursor"], limit=1)["reason"],
            "cursor_invalid",
        )

    def test_load_requires_exact_available_ids_and_returns_full_schema(self) -> None:
        engine = _engine()
        catalog = CapabilityDiscoveryCatalog(
            engine.tool_handlers,
            profile_user_id="alice",
            session_id="session-1",
        )
        loaded = catalog.load(["demo.alpha"])
        self.assertEqual(loaded["status"], "ok")
        self.assertTrue(loaded["capabilities"][0]["contract_ref"])
        self.assertNotIn("loaded_capability_ids", loaded)
        self.assertEqual(loaded["capabilities"][0]["input_schema"]["required"], ["text"])
        self.assertIn("native_schema", loaded["capabilities"][0])

        alias = catalog.load(["demo_alpha"])
        self.assertEqual(alias["reason"], "capability_not_available")
        duplicate = catalog.load(["demo.alpha", "demo.alpha"])
        self.assertEqual(duplicate["status"], "ok")
        self.assertEqual(len(duplicate["capabilities"]), 1)

    def test_migrated_g1_uses_fixed_tools_and_loading_never_adds_schema(self):
        from companion_v01.capability_exposure_config import save_preferences
        with tempfile.TemporaryDirectory() as temp:
            engine = _engine()
            engine.capability_config_base_dir = Path(temp)
            with patch.object(config, "ENABLE_PROGRESSIVE_CAPABILITY_DISCOVERY", True):
                first = resolve_capability_selection(engine, profile_user_id="alice", session_id="s")
                fixed = {"capability_list", "capability_load", "capability_invoke", "capability_search"}
                self.assertEqual(set(first.schema_tool_names), fixed)
                self.assertIn("demo.alpha", first.resolved_handlers)
                result = first.resolved_handlers["capability_load"].execute(
                    call={"type": "capability_load", "capability_ids": ["demo.alpha"]}, context=SimpleNamespace())
                self.assertFalse(result.state_updates)
                second = resolve_capability_selection(engine, profile_user_id="alice", session_id="s")
                self.assertEqual(first.schema_tool_names, second.schema_tool_names)
                saved = save_preferences(base_dir=Path(temp), profile_user_id="alice",
                    payload={"revision": 0, "toolModes": {"demo.alpha": "resident"}})
                self.assertTrue(saved["ok"], saved)
                selected = resolve_capability_selection(engine, profile_user_id="alice", session_id="s")
                self.assertIn("demo.alpha", selected.schema_tool_names)
                self.assertNotIn("demo.beta", selected.schema_tool_names)

    def test_query_miss_is_distinct_from_empty_catalog_and_changed_cursor(self):
        catalog = CapabilityDiscoveryCatalog(_engine().tool_handlers, profile_user_id="alice", session_id="s")
        for query in ("天气", "Alpha Beta", "absent"):
            result = catalog.search(query=query)
            self.assertEqual(result["items"], [])
            self.assertEqual(result["status"], "ok")
        self.assertEqual(catalog.load(["demo.alpha"])["status"], "ok")
        cursor = catalog.search(limit=1)["next_cursor"]
        changed = CapabilityDiscoveryCatalog({"demo.alpha": _engine().tool_handlers["demo.alpha"]},
                                            profile_user_id="alice", session_id="s")
        self.assertEqual(changed.search(cursor=cursor)["reason"], "cursor_stale")
        empty = CapabilityDiscoveryCatalog({}, profile_user_id="alice", session_id="s")
        self.assertEqual(empty.search()["items"], [])
        self.assertEqual(empty.load(["demo.alpha"])["reason"], "capability_not_available")

    def test_discovery_tool_error_does_not_claim_schema_loaded(self) -> None:
        engine = _engine()
        selection = resolve_capability_selection(
            engine,
            client_context=None,
            profile_user_id="alice",
            session_id="session-1",
        )
        result = selection.resolved_handlers["capability_load"].execute(
            call={"type": "capability_load", "capability_ids": ["missing.capability"]},
            context=SimpleNamespace(),
        )
        self.assertEqual(json.loads(result.followup_context)["reason"], "capability_not_available")
        self.assertNotIn("capability_discovery", result.state_updates)


if __name__ == "__main__":
    unittest.main()
