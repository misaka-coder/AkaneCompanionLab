from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import config
from companion_v01.deployment_security import AdminWriteAuth, QQChannelRuntimeConfig
from companion_v01.onebot_transport import OneBotActionTransport
from companion_v01.qq_gateway_runtime.preferences import QQSessionPreferences
from companion_v01.routes.control_center import build_control_center_router
from companion_v01.routes.memory_settings import build_memory_settings_router
from companion_v01.routes.backend_restart import build_backend_restart_router
from companion_v01.settings_overrides import RuntimeConfigView, SettingsOverrideStore, load_saved_overrides, set_override
from companion_v01.store import MemoryStore
from companion_v01.turn_coordination import TurnCoordinator

SHARED = "OWNER_PRIVATE_SESSION_SHARED"
MIRROR = "OWNER_PRIVATE_GROUP_MEMORY_MIRROR"


class MemorySettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.config = RuntimeConfigView(config, {SHARED: False, MIRROR: False, "QQ_CHARACTER_PACK_ID": "cecilia"})
        self.overrides = SettingsOverrideStore(root / "overrides.json")
        self.preferences = QQSessionPreferences(state_path=root / "qq.json",
            default_character_pack_id=lambda: "cecilia", default_reply_mode=lambda: "text")
        self.transport = Mock()
        self.transport.call.return_value = SimpleNamespace(ok=True, data={"items": [
            {"group_id": 8700, "group_name": "伙伴们"}, {"group_id": 4200, "group_name": "另一个群"}]})
        self.store = MemoryStore(root / "host")
        self.store.add_message(profile_user_id="qq_group_shared_8700", session_id="qq_group_shared_8700",
            character_pack_id="cecilia", role="user", content="PRIVATE-BODY-MUST-NOT-BE-READ",
            memory_metadata={"client_mode": "qq_text"})
        self.runtime = SimpleNamespace(bot_id="first", qq_gateway=SimpleNamespace(
            _preferences=self.preferences, _onebot_transport=self.transport), config_module=self.config,
            settings_override_store=self.overrides, admin_write_auth=AdminWriteAuth(
                token="fixture-owner", require_token=True, allow_loopback_without_token=False),
            engine=SimpleNamespace(store=self.store, owner_private_policy=SimpleNamespace(enabled=False, master_qq="12345"),
                                   owner_private_group_memory=SimpleNamespace(enabled=False)))
        app = FastAPI()
        app.include_router(build_memory_settings_router(runtime=self.runtime), prefix="/api/bots/first")
        app.include_router(build_control_center_router(config_module=self.config, settings_override_store=self.overrides,
                                                      admin_auth=self.runtime.admin_write_auth), prefix="/api/bots/first")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer fixture-owner"}
        self.url = "/api/bots/first/control-center/memory-settings"

    def read(self):
        return self.client.get(self.url, headers=self.headers).json()

    def save_group(self, enabled, group="8700"):
        return self.client.post(self.url + "/groups/" + group, headers=self.headers, json={"enabled": enabled})

    def test_reads_and_writes_require_owner_auth(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.post(self.url + "/groups/8700", json={"enabled": True}).status_code, 401)
        self.transport.call.assert_not_called()

    def test_restart_setting_persists_without_hot_apply_and_replays_at_startup(self):
        response = self.client.post("/api/bots/first/control-center/settings-catalog/" + MIRROR,
                                    headers=self.headers, json={"value": True}).json()
        self.assertEqual(response["status"], "saved")
        self.assertTrue(response["pendingRestart"])
        self.assertFalse(self.config.OWNER_PRIVATE_GROUP_MEMORY_MIRROR)
        row = next(row for row in self.read()["settings"] if row["key"] == MIRROR)
        self.assertEqual(row, {"key": MIRROR, "current": False, "desired": True, "pendingRestart": True})
        restarted = RuntimeConfigView(config, load_saved_overrides(self.overrides))
        self.assertTrue(restarted.OWNER_PRIVATE_GROUP_MEMORY_MIRROR)
        other = RuntimeConfigView(config, {MIRROR: False})
        self.assertFalse(other.OWNER_PRIVATE_GROUP_MEMORY_MIRROR)
        set_override(self.config, self.overrides, key=MIRROR, raw_value=False)
        self.assertFalse(next(row for row in self.read()["settings"] if row["key"] == MIRROR)["pendingRestart"])

    def test_catalog_uses_bot_config_and_saved_desired(self):
        set_override(self.config, self.overrides, key=SHARED, raw_value=True)
        body = self.client.get("/api/bots/first/control-center/settings-catalog").json()
        rows = {row["key"]: row for cat in body["categories"] for row in cat["settings"]}
        self.assertFalse(rows[SHARED]["current"])
        self.assertTrue(rows[SHARED]["desired"])
        self.assertTrue(rows[SHARED]["pendingRestart"])
        self.assertNotIn("fixture-owner", str(body))
        self.assertFalse(rows["TEXT_API_KEY"]["editable"])

    def test_group_names_and_consent_use_gateway_state_with_no_dialogue_or_delivery(self):
        state = self.read()
        self.assertNotIn("PRIVATE-BODY", str(state))
        self.assertFalse(state["groupMemoryRunning"])
        group = next(row for row in state["groups"] if row["groupId"] == "8700")
        self.assertEqual(group["name"], "伙伴们")
        self.assertTrue(group["hasCharacterMemory"])
        self.assertTrue(self.save_group(True).json()["enabled"])
        self.assertTrue(self.preferences.private_memory_policy(8700)[0])
        generation = self.preferences.private_memory_policy(8700)[1]
        self.assertEqual(self.save_group(True).json()["generation"], generation)
        self.assertFalse(self.save_group(False).json()["enabled"])
        self.assertGreater(self.preferences.private_memory_policy(8700)[1], generation)
        self.assertTrue(all(call.args[0] == "get_group_list" for call in self.transport.call.call_args_list))

    def test_offline_lists_trusted_history_and_allows_revocation(self):
        self.preferences.set_private_memory_enabled(8700, True)
        self.transport.call.side_effect = OSError("offline")
        state = self.read()
        self.assertFalse(state["groupNamesAvailable"])
        self.assertEqual([row["groupId"] for row in state["groups"]], ["8700"])
        self.assertFalse(self.save_group(False).json()["enabled"])
        self.transport.call.assert_called_once()

    def test_real_transport_array_projection_reaches_group_selector(self):
        transport = OneBotActionTransport(QQChannelRuntimeConfig(enabled=True, profile_ref="fixture", bot_id="bot",
            onebot_http_url="http://127.0.0.1:9", webhook_secret="", onebot_access_token="",
            require_webhook_auth=False, require_self_id=False))
        self.runtime.qq_gateway._onebot_transport = transport
        with patch.object(transport, "_request_json", return_value=({"status": "ok", "retcode": 0,
            "data": [{"group_id": 8700, "group_name": "实际协议群名"}]}, 200)):
            result = self.read()
        self.assertTrue(result["groupNamesAvailable"])
        self.assertEqual(result["groups"][0]["name"], "实际协议群名")

    def test_failed_persistence_and_invalid_group_do_not_change_consent(self):
        with patch.object(self.preferences, "_persist_gateway_state", return_value=False):
            result = self.save_group(True)
        self.assertEqual(result.status_code, 500)
        self.assertFalse(self.preferences.private_memory_policy(8700)[0])
        self.assertEqual(self.save_group(True, "9999").status_code, 404)
        self.assertEqual(self.save_group("yes").status_code, 400)

    def test_known_group_can_be_selected_before_this_character_has_history(self):
        self.assertTrue(self.save_group(True, "4200").json()["ok"])
        group = next(row for row in self.read()["groups"] if row["groupId"] == "4200")
        self.assertTrue(group["enabled"])
        self.assertFalse(group["hasCharacterMemory"])

    def test_corrupt_settings_and_failed_save_are_truthful(self):
        with patch.object(self.overrides, "update", side_effect=OSError("disk full")):
            result = self.client.post("/api/bots/first/control-center/settings-catalog/" + MIRROR,
                                     headers=self.headers, json={"value": True})
        self.assertEqual(result.status_code, 500)
        self.assertFalse(self.config.OWNER_PRIVATE_GROUP_MEMORY_MIRROR)
        self.overrides.path.write_text("{broken", encoding="utf-8")
        self.assertFalse(self.read()["ok"])

    def test_parallel_saves_keep_both_restart_values(self):
        original_load = self.overrides.load
        def slow_load():
            values = original_load()
            time.sleep(0.02)
            return values
        with patch.object(self.overrides, "load", side_effect=slow_load), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(set_override, self.config, self.overrides, key=key, raw_value=True)
                       for key in (SHARED, MIRROR)]
            for future in futures:
                future.result(timeout=5)
        self.assertTrue(self.overrides.load()[SHARED])
        self.assertTrue(self.overrides.load()[MIRROR])


class RestartPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbox_worker_before_or_after_model_hold_blocks_restart(self):
        coordinator = TurnCoordinator()
        registry = SimpleNamespace(values=lambda: [SimpleNamespace(engine=SimpleNamespace(turn_coordinator=coordinator),
            session_work_queue=SimpleNamespace(is_idle_for_restart=lambda: False))],
            stop_all=AsyncMock(return_value={"status": "stopped"}))
        app = FastAPI()
        app.include_router(build_backend_restart_router(registry=registry, admin_auth=AdminWriteAuth.local_compatibility()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            response = (await client.post("/control-center/backend-restart/prepare")).json()
            self.assertEqual(response["status"], "busy")
            registry.stop_all.assert_not_called()
            async with coordinator.hold("master", "master"):
                pass

    async def test_busy_sibling_prevents_shutdown_and_releases_partial_fences(self):
        first, second = TurnCoordinator(), TurnCoordinator()
        registry = SimpleNamespace(values=lambda: [SimpleNamespace(engine=SimpleNamespace(turn_coordinator=coordinator))
                                                   for coordinator in (first, second)], stop_all=AsyncMock(return_value={"status": "stopped"}))
        app = FastAPI()
        app.include_router(build_backend_restart_router(registry=registry, admin_auth=AdminWriteAuth.local_compatibility()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            async with second.hold("master", "master"):
                response = (await client.post("/control-center/backend-restart/prepare")).json()
                self.assertEqual(response["status"], "busy")
                registry.stop_all.assert_not_called()
                async with first.hold("master", "other"):
                    pass
            response = (await client.post("/control-center/backend-restart/prepare")).json()
            self.assertEqual(response["status"], "ready")
            self.assertTrue(response["ok"])
            registry.stop_all.assert_awaited_once()
            self.assertTrue((await client.post("/control-center/backend-restart/prepare")).json()["ok"])
            registry.stop_all.assert_awaited_once()
            with self.assertRaisesRegex(RuntimeError, "backend_restart_pending"):
                async with first.hold("master", "later"):
                    pass

    async def test_shutdown_failure_never_reports_restart_ready(self):
        registry = SimpleNamespace(values=lambda: [SimpleNamespace(engine=SimpleNamespace(turn_coordinator=TurnCoordinator()))],
                                   stop_all=AsyncMock(return_value={"status": "degraded"}))
        app = FastAPI()
        app.include_router(build_backend_restart_router(registry=registry, admin_auth=AdminWriteAuth.local_compatibility()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            response = (await client.post("/control-center/backend-restart/prepare")).json()
            self.assertFalse(response["ok"])
            self.assertEqual(response["reason"], "backend_restart_drain_failed")


if __name__ == "__main__":
    unittest.main()
