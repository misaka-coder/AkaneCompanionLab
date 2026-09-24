from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from companion_v01.capability_exposure_service import read_exposure_state
from companion_v01.routes.capabilities import build_capabilities_router
from tests import test_tool_exposure_wire as wire


class ToolExposureApiTests(unittest.TestCase):
    # Reuse fixture methods without inheriting and repeating all wire cases.
    setUp = wire.ToolExposureWireTests.setUp
    provider = wire.ToolExposureWireTests.provider
    begin = wire.ToolExposureWireTests.begin
    request = wire.ToolExposureWireTests.request
    def test_settings_read_does_not_publish_a_baseline_and_save_reports_pending(self):
        app = FastAPI()
        app.include_router(build_capabilities_router(engine=self.engine, config_module=config,
            capability_config_base_dir=self.root))
        with TestClient(app) as client:
            url = "/capabilities/tool-exposure?user_id=wire-session&real_user_id=wire-user&character_pack_id=wire-character"
            initial = client.get(url).json()
            self.assertTrue(initial["ok"], initial)
            self.assertEqual(initial["status"], "awaiting_baseline")
            self.assertEqual(initial["tools"][0]["currentMode"], "not_published")
            self.provider("openai")
            self.begin(1)
            self.request()
            saved = client.post(url, json={**initial["preferences"], "toolModes": {"demo.alpha": "on_demand"}}).json()
            self.assertTrue(saved["ok"], saved)
            row = saved["state"]["tools"][0]
            self.assertEqual((row["targetMode"], row["currentMode"], row["pending"]), ("on_demand", "not_published", True))
            self.assertEqual(row["pendingReason"], "next_request")
            self.assertTrue(row["executionAvailable"])
            self.request()
            applied = client.get(url).json()
            self.assertEqual(applied["tools"][0]["currentMode"], "on_demand")
            self.assertFalse(applied["tools"][0]["pending"])
            self.assertEqual(applied["compactionGeneration"], saved["state"]["compactionGeneration"])
            repeated = client.post(url, json=applied["preferences"]).json()
            self.assertEqual(repeated["preferences"]["revision"], applied["preferences"]["revision"])
            self.assertFalse(repeated["state"]["preferencePending"])
            stale = client.post(url, json=initial["preferences"]).json()
            self.assertEqual(stale["reason"], "tool_exposure_revision_conflict")
            other = client.get(url.replace("wire-session", "new-session")).json()
            self.assertEqual(other["status"], "awaiting_baseline")
            self.assertEqual(other["tools"][0]["targetMode"], "on_demand")

    def test_unsupported_modes_and_projection_failure_are_explicit(self):
        for backend in ("legacy", "dual"):
            state = read_exposure_state(self.engine, base_dir=self.root, **self.scope,
                config_module=SimpleNamespace(MEMORY_BACKEND=backend))
            self.assertTrue(state["ok"])
            self.assertFalse(state["boundarySupported"])
            self.assertEqual(state["status"], "immediate")
        with patch.object(self.manager, "build_context_projection", return_value={"ok": False}):
            state = read_exposure_state(self.engine, base_dir=self.root, **self.scope)
        self.assertFalse(state["boundarySupported"])
        self.assertEqual(state["reason"], "memcore_projection_unavailable")

    def test_plugin_group_uses_host_owner_when_worker_binding_has_no_provider_id(self):
        handler = self.engine.tool_handlers["demo.alpha"]
        handler.plugin_id = "demo.plugin"
        handler.adapter = SimpleNamespace()
        state = read_exposure_state(self.engine, base_dir=self.root, **self.scope)
        row = next(tool for tool in state["tools"] if tool["id"] == "demo.alpha")
        self.assertEqual(row["source"], "插件 · demo.plugin")
        handler.adapter = SimpleNamespace(server_id="demo-mcp", provider_id="irrelevant")
        state = read_exposure_state(self.engine, base_dir=self.root, **self.scope)
        self.assertEqual(next(tool for tool in state["tools"] if tool["id"] == "demo.alpha")["source"], "MCP · demo-mcp")

    def test_same_mode_semantic_upgrade_is_pending_and_profiles_stay_separate(self):
        self.provider("openai")
        self.begin(1)
        self.request()
        self.engine.tool_handlers["demo.alpha"].contract_revision = "2"
        state = read_exposure_state(self.engine,base_dir=self.root,**self.scope,config_module=config)
        row = state["tools"][0]
        self.assertEqual((row["targetMode"],row["currentMode"]),("resident","resident"))
        self.assertTrue(row["contractPending"])
        self.assertTrue(row["pending"])
        self.assertFalse(row["nativeContractValid"])
        from companion_v01.capability_exposure_config import save_preferences
        saved = save_preferences(base_dir=self.root,profile_user_id="wire-user",payload={"revision":0,"defaultMode":"on_demand"})
        self.assertTrue(saved["ok"],saved)
        other = read_exposure_state(self.engine,base_dir=self.root,**{**self.scope,"profile_user_id":"other"},config_module=config)
        self.assertEqual(other["preferences"]["defaultMode"],"resident")
        self.assertEqual(other["status"],"awaiting_baseline")
