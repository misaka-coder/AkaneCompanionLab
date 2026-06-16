from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from fastapi import FastAPI

from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.desktop_pet_contract import (
    DESKTOP_PET_CONTRACT_VERSION,
    build_desktop_pet_diagnostics_payload,
    build_desktop_pet_error_payload,
    build_desktop_pet_health_payload,
    decorate_resource_manifest_for_desktop_pet,
)
from companion_v01.final_output_engine import normalize_final_output
from companion_v01.output_adapters import OutputAdapterRegistry
from companion_v01.resource_manifest import ResourceManifest


def write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class FakeEngine:
    def __init__(self, resource_manifest: ResourceManifest) -> None:
        self.resource_manifest = resource_manifest
        self.capability_registry = CapabilityRegistry()
        self.resource_manifest_calls: list[dict[str, str]] = []

    def build_resource_manifest(
        self,
        *,
        profile_user_id: str = "",
        client_mode: str = "",
        character_pack_id: str = "",
    ) -> dict:
        self.resource_manifest_calls.append(
            {
                "profile_user_id": profile_user_id,
                "client_mode": client_mode,
                "character_pack_id": character_pack_id,
            }
        )
        return self.resource_manifest.get_manifest()

    def build_desktop_pet_workspace_panel(self, **_kwargs):
        return {
            "ok": True,
            "counts": {
                "files": 2,
                "outputs": 1,
                "tasks": 0,
            },
        }

    def _resolve_client_protocol_context(self, _payload):
        return ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
        )

    def _normalize_tool_call(self, *_args, **_kwargs):
        return None

    def _normalize_memory_tags(self, _value):
        return []

    def _normalize_choices(self, _value):
        return []

    def _get_persona_card_service(self):
        return None

    def _get_output_adapter_registry(self):
        return OutputAdapterRegistry()

    def _get_user_runtime_projection(self, _profile_user_id):
        return {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }


class DesktopPetBackendContractTests(unittest.TestCase):
    def test_health_payload_exposes_desktop_pet_routes_and_capabilities(self) -> None:
        payload = build_desktop_pet_health_payload(
            profile_user_id="master",
            session_id="desktop_pet_next_1",
            streaming_tts_enabled=True,
            yt_dlp_available=False,
        )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["contract_version"], DESKTOP_PET_CONTRACT_VERSION)
        self.assertEqual(payload["client_mode"], "desktop_pet")
        self.assertEqual(payload["profile_user_id"], "master")
        self.assertIn("speech_segments", payload["capabilities"])
        self.assertIn("desktop_context", payload["capabilities"])
        self.assertEqual(payload["endpoints"]["think"], "/think")
        self.assertEqual(payload["endpoints"]["session_ensure"], "/sessions/ensure")
        self.assertEqual(payload["tts"]["response_media_type"], "audio/mpeg")

    def test_resource_manifest_decoration_adds_desktop_pet_projection(self) -> None:
        manifest = {
            "schema_version": 2,
            "characters": {
                "outfits": [
                    {
                        "id": "猫娘",
                        "name": "猫娘",
                        "aliases": ["catgirl"],
                        "allowed_emotions": ["正常", "开心"],
                        "emotions": [
                            {"id": "开心", "name": "开心", "path": "/assets/characters/猫娘/开心.png"},
                            {"id": "正常", "name": "正常", "path": "/assets/characters/猫娘/正常.png"},
                        ],
                    }
                ]
            },
            "defaults": {"outfit": "猫娘", "emotion": "开心"},
        }

        payload = decorate_resource_manifest_for_desktop_pet(
            manifest,
            profile_user_id="master",
            session_id="desktop_pet_next_1",
        )
        desktop = payload["clients"]["desktop_pet"]

        self.assertEqual(payload["defaults"]["desktop_pet_outfit"], "猫娘")
        self.assertEqual(payload["defaults"]["desktop_pet_emotion"], "正常")
        self.assertEqual(desktop["default_outfit"], "猫娘")
        self.assertEqual(desktop["default_emotion"], "正常")
        self.assertEqual(desktop["emotion_match_fields"], ["id", "name", "aliases"])
        self.assertTrue(desktop["supports"]["allowed_emotions"])

    def test_resource_manifest_decoration_falls_back_to_manifest_defaults(self) -> None:
        manifest = {
            "schema_version": 2,
            "characters": {
                "outfits": [
                    {
                        "id": "睡衣",
                        "name": "睡衣",
                        "emotions": [
                            {"id": "困困", "name": "困困", "path": "/assets/characters/睡衣/困困.png"},
                        ],
                    }
                ]
            },
            "defaults": {"outfit": "睡衣", "emotion": "困困"},
        }

        payload = decorate_resource_manifest_for_desktop_pet(manifest)
        desktop = payload["clients"]["desktop_pet"]

        self.assertEqual(desktop["default_outfit"], "睡衣")
        self.assertEqual(desktop["default_emotion"], "困困")

    def test_error_payload_is_stable_json_contract(self) -> None:
        payload = build_desktop_pet_error_payload(
            error="tts_failed",
            message="TTS failed",
            retryable=True,
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["contract_version"], DESKTOP_PET_CONTRACT_VERSION)
        self.assertEqual(payload["error"], "tts_failed")
        self.assertTrue(payload["retryable"])

    def test_diagnostics_payload_has_expected_structure(self) -> None:
        payload = build_desktop_pet_diagnostics_payload(
            engine=FakeEngine(ResourceManifest(Path(tempfile.mkdtemp()) / "assets")),
            profile_user_id="master",
            session_id="session_1",
            runtime_metrics={"think_requests_total": 5.0, "think_ok_total": 4.0},
            public_guard_snapshot={
                "enabled": False,
                "max_concurrent_thinks": 2,
                "daily_think_limit": 200,
                "active_thinks": 0,
                "used_today": 0,
                "day_key": "2026-05-01",
            },
        )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["client_mode"], "desktop_pet")
        self.assertEqual(payload["contract_version"], DESKTOP_PET_CONTRACT_VERSION)
        self.assertIsInstance(payload["server_time"], int)

        # capabilities — object, not list
        self.assertIsInstance(payload["capabilities"], dict)
        self.assertIn("declared", payload["capabilities"])
        self.assertIn("speech_segments", payload["capabilities"]["declared"])
        self.assertIn("effective_modules", payload["capabilities"])
        self.assertIn("tool_layers", payload["capabilities"])
        self.assertIn("tool_names", payload["capabilities"])
        self.assertIsInstance(payload["capabilities"]["effective_modules"], list)
        self.assertIn("base", payload["capabilities"]["effective_modules"])
        self.assertIn("common", payload["capabilities"]["tool_layers"])
        self.assertIn("retrieve_memory", payload["capabilities"]["tool_names"])

        # resources — desktop-pet focused
        self.assertIn("resource_manifest_ok", payload["resources"])
        self.assertIn("character_pack_id", payload["resources"])
        self.assertIn("outfit", payload["resources"])
        self.assertIn("default_emotion", payload["resources"])
        self.assertIn("emotion_count", payload["resources"])
        self.assertIsInstance(payload["resources"]["emotion_count"], int)

        # workspace shape
        self.assertIn("files", payload["workspace"])
        self.assertIn("outputs", payload["workspace"])
        self.assertIn("tasks", payload["workspace"])
        self.assertEqual(payload["workspace"]["files"], 2)
        self.assertEqual(payload["workspace"]["outputs"], 1)
        self.assertEqual(payload["workspace"]["tasks"], 0)

        # runtime shape
        self.assertIsInstance(payload["runtime"]["pid"], int)
        self.assertIsInstance(payload["runtime"]["python"], str)
        self.assertIsInstance(payload["runtime"]["metrics"], dict)
        self.assertEqual(payload["runtime"]["metrics"]["think_requests_total"], 5.0)

        # safety — minimum required fields
        self.assertIn("secrets_exposed", payload["safety"])
        self.assertFalse(payload["safety"]["secrets_exposed"])
        self.assertIn("desktop_actions_require_client", payload["safety"])
        self.assertTrue(payload["safety"]["desktop_actions_require_client"])
        self.assertIn("full_disk_scan", payload["safety"])
        self.assertFalse(payload["safety"]["full_disk_scan"])
        self.assertIn("public_guard", payload["safety"])
        self.assertFalse(payload["safety"]["public_guard"]["enabled"])
        self.assertEqual(payload["safety"]["public_guard"]["day_key"], "2026-05-01")

    def test_diagnostics_payload_omits_sensitive_keys(self) -> None:
        payload = build_desktop_pet_diagnostics_payload(
            engine=FakeEngine(ResourceManifest(Path(tempfile.mkdtemp()) / "assets")),
        )
        raw = json.dumps(payload, ensure_ascii=False)

        sensitive_keys = [
            "api_key",
            "api-key",
            "apikey",
            "token",
            "password",
            "authorization",
            "full_prompt",
            "chat_content",
            "screenshot",
            "file_content",
            "conversation",
        ]
        for key in sensitive_keys:
            self.assertNotIn(f'"{key}"', raw, f"Diagnostics payload should not contain '{key}'")
        # "secret" as a standalone JSON key, not substring like "secrets_exposed"
        self.assertFalse(re.search(r'["\']secret["\']\s*:', raw), "Diagnostics payload should not contain 'secret' key")

    def test_diagnostics_route_returns_expected_response(self) -> None:
        from companion_v01.routes.core import build_core_router

        engine = FakeEngine(ResourceManifest(Path(tempfile.mkdtemp()) / "assets"))
        app = FastAPI()
        app.include_router(
            build_core_router(
                engine=engine,
                config_module=MagicMock(STREAMING_TTS_ENABLED=True),
                resolve_identity_from_query=lambda r: ("session", "user"),
            )
        )
        client = TestClient(app)
        response = client.get("/desktop-pet/diagnostics?character_pack_id=akane_sample")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

        data: dict = response.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("client_mode"), "desktop_pet")

        # capabilities — object, not list
        caps = data.get("capabilities", {})
        self.assertIsInstance(caps, dict)
        self.assertIsInstance(caps.get("declared"), list)
        self.assertIsInstance(caps.get("effective_modules"), list)
        self.assertIsInstance(caps.get("tool_layers"), list)
        self.assertIsInstance(caps.get("tool_names"), list)

        # resources
        resources = data.get("resources", {})
        self.assertIsInstance(resources.get("resource_manifest_ok"), bool)
        self.assertIsInstance(resources.get("character_pack_id"), str)
        self.assertEqual(resources.get("character_pack_id"), "akane_sample")
        self.assertIsInstance(resources.get("emotion_count"), int)
        self.assertEqual(engine.resource_manifest_calls[-1]["client_mode"], "desktop_pet")
        self.assertEqual(engine.resource_manifest_calls[-1]["character_pack_id"], "akane_sample")

        # safety — minimum fields
        safety = data.get("safety", {})
        self.assertIsInstance(safety.get("secrets_exposed"), bool)
        self.assertIsInstance(safety.get("desktop_actions_require_client"), bool)
        self.assertIsInstance(safety.get("full_disk_scan"), bool)
        self.assertIsInstance(safety.get("public_guard"), dict)

        # runtime
        runtime = data.get("runtime", {})
        self.assertIsInstance(runtime.get("pid"), int)
        self.assertIsInstance(runtime.get("python"), str)
        self.assertIsInstance(runtime.get("metrics"), dict)

        # no sensitive keys
        raw = json.dumps(data, ensure_ascii=False)
        for key in ["api_key", "token", "password", "full_prompt",
                     "chat_content", "screenshot", "file_content"]:
            self.assertNotIn(f'"{key}"', raw, f"Route response should not contain '{key}'")
        self.assertFalse(re.search(r'["\']secret["\']\s*:', raw), "Route response should not contain 'secret' key")

    def test_final_output_can_normalize_against_desktop_pack_manifest(self) -> None:
        web_temp = tempfile.TemporaryDirectory()
        desktop_temp = tempfile.TemporaryDirectory()
        self.addCleanup(web_temp.cleanup)
        self.addCleanup(desktop_temp.cleanup)

        web_assets = Path(web_temp.name) / "assets"
        desktop_assets = Path(desktop_temp.name) / "assets"
        write_bytes(web_assets / "characters" / "猫娘" / "思考中.png")
        write_bytes(desktop_assets / "characters" / "猫娘" / "开心.png")
        write_bytes(desktop_assets / "characters" / "猫娘" / "害羞.png")

        web_manifest = ResourceManifest(web_assets)
        desktop_manifest = ResourceManifest(
            desktop_assets,
            public_prefix="/desktop-pet-character-packs/demo/assets",
        )
        desktop_defaults = desktop_manifest.refresh()["defaults"]

        normalized = normalize_final_output(
            FakeEngine(web_manifest),
            result={
                "emotion": "thinking",
                "speech": "我想一想。",
                "character": {"outfit": "猫娘"},
                "scene": {},
            },
            visual_defaults=desktop_defaults,
            allow_tool_call=False,
            debug_enabled=False,
            resource_manifest=desktop_manifest,
        )

        self.assertIn(normalized["emotion"], {"开心", "害羞"})
        self.assertNotEqual(normalized["emotion"], "思考中")
        self.assertEqual(normalized["client_mode"], "desktop_pet")
        self.assertEqual(normalized["character"], {"outfit": "猫娘"})
        self.assertNotIn("scene", normalized)
        self.assertNotIn("last_scene", normalized["_runtime_state"])
        self.assertEqual(normalized["_runtime_state"]["last_character"]["outfit_id"], "猫娘")

    def test_qq_final_output_uses_character_pack_emotion_image_ids(self) -> None:
        web_temp = tempfile.TemporaryDirectory()
        character_temp = tempfile.TemporaryDirectory()
        self.addCleanup(web_temp.cleanup)
        self.addCleanup(character_temp.cleanup)

        web_assets = Path(web_temp.name) / "assets"
        character_assets = Path(character_temp.name) / "assets"
        write_bytes(web_assets / "characters" / "猫娘" / "开心.png")
        write_bytes(character_assets / "characters" / "default" / "害羞.png")

        web_manifest = ResourceManifest(web_assets)
        character_manifest = ResourceManifest(character_assets)
        visual_defaults = character_manifest.refresh()["defaults"]
        qq_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )

        normalized = normalize_final_output(
            FakeEngine(web_manifest),
            result={
                "emotion": "开心",
                "reply_medium": "text",
                "speech": "测试。",
            },
            visual_defaults=visual_defaults,
            allow_tool_call=False,
            debug_enabled=False,
            client_context=qq_context,
            resource_manifest=character_manifest,
        )

        self.assertEqual(normalized["emotion"], "害羞")
        self.assertEqual(normalized["client_mode"], "qq_text")
        self.assertNotIn("character", normalized)
        self.assertNotIn("scene", normalized)


if __name__ == "__main__":
    unittest.main()
