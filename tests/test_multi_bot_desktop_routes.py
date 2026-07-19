from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.bot_http_routes import build_bot_runtime_routers
from companion_v01.bot_registry import BotRegistry
from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.model_service_config import ModelServiceConfigStore
from companion_v01.routes.bots import build_bots_router
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.settings_overrides import SettingsOverrideStore


class _GuardDecision:
    allowed = True
    acquired = True
    reason = ""
    message = ""


class _Guard:
    def try_acquire(self) -> _GuardDecision:
        return _GuardDecision()

    def release(self) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": False}


class _Metrics:
    def incr(self, _key: str, _amount: float = 1.0) -> None:
        return None

    def observe_request(self, _name: str, *, duration_ms: float, ok: bool) -> None:
        del duration_ms, ok

    def snapshot(self) -> dict[str, float]:
        return {}


class _Lease:
    def __init__(self, bot_id: str, layout: Any) -> None:
        self.instance_id = bot_id
        self.layout = layout

    def public_health_snapshot(self) -> dict[str, str]:
        return {"status": "ok", "instance_id": self.instance_id, "root_binding": "valid"}


class _Engine:
    def __init__(self, marker: str, character_resources: Any) -> None:
        self.marker = marker
        self.desktop_pet_character_resources = character_resources
        self.executor_broker = None
        self.background_tasks = None
        self.calls: list[dict[str, Any]] = []

    def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(payload))
        return {"status": "ok", "speech": self.marker, "runtime": self.marker}

    def care_feature_status(self) -> dict[str, Any]:
        return {"enabled": True}


class _Runtime:
    def __init__(self, root: Path, bot_id: str, display_name: str) -> None:
        users_data = root / "users_data"
        characters = root / "characters"
        users_data.mkdir(parents=True)
        characters.mkdir(parents=True)
        self.bot_id = bot_id
        self.display_name = display_name
        self.runtime_layout = SimpleNamespace(
            data_root=root,
            users_data_dir=users_data,
            characters_dir=characters,
        )
        self.instance_runtime = _Lease(bot_id, self.runtime_layout)
        self.desktop_pet_character_resources = DesktopPetCharacterResourceService(
            characters_dir=characters,
            public_prefix=f"/api/bots/{bot_id}/desktop-pet-character-packs",
        )
        self.engine = _Engine(bot_id, self.desktop_pet_character_resources)
        self.config_module = SimpleNamespace(
            STREAMING_TTS_ENABLED=True,
            WEB_IDENTITY_MODE="owner",
            WEB_OWNER_PROFILE_USER_ID="master",
        )
        self.runtime_metrics = _Metrics()
        self.public_guard = _Guard()
        self.admin_write_auth = AdminWriteAuth.local_compatibility()
        self.tts_client = None
        self.settings = BotSettingsView()
        self.model_service_config_store = ModelServiceConfigStore(users_data / "model-service.json")
        self.settings_override_store = SettingsOverrideStore(users_data / "settings-overrides.json")
        self.plugin_host = SimpleNamespace()

    def reload_model_services(self, _settings: Any) -> dict[str, str]:
        return {"status": "reloaded"}


def _query_identity(request: Any) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or "desktop")
    return session_id, str(request.query_params.get("real_user_id") or session_id)


def _payload_identity(payload: dict[str, Any]) -> tuple[str, str]:
    session_id = str(payload.get("user_id") or "desktop")
    return session_id, str(payload.get("real_user_id") or session_id)


class MultiBotDesktopRouteTests(unittest.TestCase):
    def test_same_route_assembly_dispatches_turns_to_isolated_runtime_engines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            personal = _Runtime(root / "personal", "personal", "Akane")
            finance = _Runtime(root / "finance", "finance", "Akane Finance")
            registry = BotRegistry(default_bot_id="personal")
            registry.add(personal, default=True)
            registry.add(finance)

            app = FastAPI()
            app.include_router(build_bots_router(bot_registry=registry))
            for runtime in registry.values():
                prefix = f"/api/bots/{runtime.bot_id}"
                routers = build_bot_runtime_routers(
                    runtime=runtime,
                    route_prefix=prefix,
                    resolve_identity_from_query=_query_identity,
                    resolve_identity_from_payload=_payload_identity,
                    log_event=lambda *_args, **_kwargs: None,
                )
                for router in routers:
                    app.include_router(router, prefix=prefix)
                    if runtime.bot_id == registry.default_bot_id:
                        app.include_router(router)

            client = TestClient(app)
            personal_response = client.post(
                "/api/bots/personal/think_once",
                json={"message": "personal", "user_id": "desktop", "real_user_id": "master"},
            )
            finance_response = client.post(
                "/api/bots/finance/think_once",
                json={"message": "finance", "user_id": "desktop", "real_user_id": "master"},
            )
            legacy_response = client.post(
                "/think_once",
                json={"message": "legacy", "user_id": "desktop", "real_user_id": "master"},
            )

            self.assertEqual(personal_response.json()["runtime"], "personal")
            self.assertEqual(finance_response.json()["runtime"], "finance")
            self.assertEqual(legacy_response.json()["runtime"], "personal")
            self.assertEqual([item["message"] for item in personal.engine.calls], ["personal", "legacy"])
            self.assertEqual([item["message"] for item in finance.engine.calls], ["finance"])
            self.assertNotEqual(client.get("/api/bots/unknown/desktop-pet/health").status_code, 200)

            finance_health = client.get("/api/bots/finance/desktop-pet/health").json()
            self.assertEqual(finance_health["endpoints"]["think"], "/api/bots/finance/think")
            self.assertEqual(finance_health["tts"]["endpoint"], "/api/bots/finance/tts")
            paths = {route.path for route in app.routes if hasattr(route, "path")}
            self.assertIn("/api/bots/finance/desktop-pet/vision/clip", paths)
            self.assertIn("/api/bots/finance/pet/turn", paths)
            self.assertIn("/api/bots/finance/capabilities", paths)

            catalog = client.get("/api/bots").json()
            self.assertEqual(catalog["defaultBotId"], "personal")
            self.assertEqual([item["botId"] for item in catalog["bots"]], ["personal", "finance"])
            self.assertNotIn("data_root", str(catalog))


if __name__ == "__main__":
    unittest.main()
