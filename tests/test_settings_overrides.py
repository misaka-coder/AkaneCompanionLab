from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from companion_v01 import settings_overrides as so
from companion_v01.routes.control_center import build_control_center_router


def _temp_store() -> so.SettingsOverrideStore:
    return so.SettingsOverrideStore(Path(tempfile.mkdtemp()) / "overrides.json")


class SettingsOverrideStoreTests(unittest.TestCase):
    def test_coerce_types(self) -> None:
        self.assertIs(so.coerce_value("ENABLE_SEMANTIC_MEMORY", "false"), False)
        self.assertEqual(so.coerce_value("MAX_TOOL_ROUNDS", "4"), 4)
        self.assertAlmostEqual(so.coerce_value("DRIFT_PROBABILITY", "0.3"), 0.3)
        self.assertEqual(so.coerce_value("QQ_REPLY_MODE", "voice"), "voice")

    def test_invalid_value_rejected(self) -> None:
        with self.assertRaises(so.SettingOverrideError):
            so.coerce_value("MAX_TOOL_ROUNDS", "not-an-int")

    def test_set_override_applies_and_persists_runtime_key(self) -> None:
        store = _temp_store()
        original = config.MAX_TOOL_ROUNDS
        try:
            applied = so.set_override(config, store, key="MAX_TOOL_ROUNDS", raw_value="5")
            self.assertEqual(applied, 5)
            self.assertEqual(config.MAX_TOOL_ROUNDS, 5)
            self.assertEqual(store.load().get("MAX_TOOL_ROUNDS"), 5)
        finally:
            config.MAX_TOOL_ROUNDS = original

    def test_non_editable_keys_rejected(self) -> None:
        store = _temp_store()
        # secret, restart-scope, restart_client-scope, and managed-elsewhere
        # (STREAMING_TTS_ENABLED is runtime but owned by the capabilities page)
        # all stay read-only.
        for key in ("TEXT_API_KEY", "HOST", "PUBLIC_GUARD_ENABLED", "STREAMING_TTS_ENABLED"):
            with self.assertRaises(so.SettingOverrideError) as ctx:
                so.set_override(config, store, key=key, raw_value="x")
            self.assertEqual(ctx.exception.reason, "not_editable")

    def test_startup_replay_skips_non_editable(self) -> None:
        store = _temp_store()
        store.save({"MAX_TOOL_ROUNDS": 7, "TEXT_API_KEY": "secret", "HOST": "0.0.0.0"})
        original = config.MAX_TOOL_ROUNDS
        try:
            applied = so.load_and_apply_saved_overrides(config, store)
            self.assertEqual(applied, {"MAX_TOOL_ROUNDS": 7})
            self.assertEqual(config.MAX_TOOL_ROUNDS, 7)
        finally:
            config.MAX_TOOL_ROUNDS = original


class SettingsUpdateEndpointTests(unittest.TestCase):
    def _client(self, store) -> TestClient:
        app = FastAPI()
        app.include_router(
            build_control_center_router(settings_override_store=store, config_module=config)
        )
        return TestClient(app)

    def test_post_applies_editable_key(self) -> None:
        store = _temp_store()
        original = config.MAX_TOOL_ROUNDS
        try:
            resp = self._client(store).post(
                "/control-center/settings-catalog/MAX_TOOL_ROUNDS", json={"value": 5}
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["value"], 5)
            self.assertEqual(config.MAX_TOOL_ROUNDS, 5)
        finally:
            config.MAX_TOOL_ROUNDS = original

    def test_post_rejects_secret_key(self) -> None:
        resp = self._client(_temp_store()).post(
            "/control-center/settings-catalog/TEXT_API_KEY", json={"value": "leak"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["status"], "not_editable")

    def test_post_rejects_invalid_value(self) -> None:
        resp = self._client(_temp_store()).post(
            "/control-center/settings-catalog/MAX_TOOL_ROUNDS", json={"value": "not-an-int"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["status"], "invalid_value")


if __name__ == "__main__":
    unittest.main()
