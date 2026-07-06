from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.petdesk_bridge import (
    PET_DISPLAY_SCHEMA_VERSION,
    build_petdesk_display_envelope,
    build_petdesk_resource_bundle,
    safe_handle_segment,
)
from companion_v01.routes.petdesk import build_petdesk_router


def write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class FakeRuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))


class FakeGuard:
    def __init__(self) -> None:
        self.released = 0

    def try_acquire(self):
        return SimpleNamespace(allowed=True, acquired=True, reason="", message="")

    def release(self) -> None:
        self.released += 1


class PetdeskBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.characters_dir = Path(self.temp_dir.name) / "characters"
        self.pack_dir = self.characters_dir / "mika_pack"
        write_bytes(self.pack_dir / "assets" / "characters" / "猫娘" / "开心.png")
        write_bytes(self.pack_dir / "assets" / "characters" / "猫娘" / "害羞.png")
        write_json(
            self.pack_dir / "character.json",
            {
                "identity": {"id": "mika_pack", "name": "Mika"},
                "appearance": {"default_outfit": "猫娘", "default_emotion": "开心"},
                "emotion_aliases": {"cheerful": ["开心"], "shy": ["害羞"]},
            },
        )
        self.resources = DesktopPetCharacterResourceService(characters_dir=self.characters_dir)

    def test_static_manifest_uses_safe_handles_and_petdesk_urls(self) -> None:
        bundle = build_petdesk_resource_bundle(self.resources, "mika_pack")

        self.assertEqual(bundle.character_pack_id, "mika_pack")
        static_images = bundle.runtime_manifest["staticImages"]
        self.assertEqual(len(static_images), 2)
        for handle, entry in static_images.items():
            self.assertNotIn("猫娘", handle)
            self.assertNotIn("开心", handle)
            self.assertNotIn("://", handle)
            self.assertNotIn("..", handle)
            self.assertNotIn("\\", handle)
            self.assertRegex(handle, r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)+$")
            self.assertEqual(entry["kind"], "static_image")
            self.assertEqual(entry["handle"], handle)
            self.assertTrue(entry["url"].startswith("/petdesk-character-packs/mika_pack/assets/characters/"))
            self.assertNotIn("/desktop-pet-character-packs/", entry["url"])
        serialized = json.dumps(bundle.runtime_manifest, ensure_ascii=False)
        self.assertNotIn(str(self.characters_dir), serialized)

    def test_display_envelope_preserves_labels_but_uses_safe_asset_handle(self) -> None:
        bundle = build_petdesk_resource_bundle(self.resources, "mika_pack")
        manifest = self.resources.get_manifest("mika_pack")

        envelope = build_petdesk_display_envelope(
            {
                "status": "ok",
                "speech": "我在。",
                "speech_segments": [{"text": "我在。"}],
                "emotion": "cheerful",
                "character": {"outfit": "猫娘"},
            },
            bundle=bundle,
            resource_manifest=manifest,
            turn_id="turn-1",
        )

        self.assertEqual(envelope["schemaVersion"], PET_DISPLAY_SCHEMA_VERSION)
        self.assertEqual(envelope["turnId"], "turn-1")
        self.assertEqual(envelope["speech"], "我在。")
        self.assertEqual(envelope["visual"]["renderer"], "static_portrait")
        self.assertEqual(envelope["visual"]["emotion"], "开心")
        self.assertEqual(envelope["visual"]["outfit"], "猫娘")
        self.assertEqual(envelope["visual"]["motion"], "speaking")
        self.assertIn(envelope["visual"]["assetHandle"], bundle.runtime_manifest["staticImages"])
        self.assertEqual(envelope["safety"]["status"], "ok")

    def test_router_exposes_health_snapshot_and_turn_stream(self) -> None:
        runtime = FakeRuntimeMetrics()
        guard = FakeGuard()
        calls: list[dict[str, Any]] = []

        class FakeEngine:
            def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
                calls.append(payload)
                return {
                    "status": "ok",
                    "speech": f"echo: {payload['message']}",
                    "emotion": "shy",
                    "character": {"outfit": "猫娘"},
                }

        app = FastAPI()
        app.include_router(
            build_petdesk_router(
                engine=FakeEngine(),
                character_resources=self.resources,
                runtime_metrics=runtime,
                public_guard=guard,
                log_event=lambda *_args, **_kwargs: None,
            )
        )
        client = TestClient(app)

        health = client.get("/pet/health?character_pack_id=mika_pack")
        snapshot = client.get("/pet/snapshot?character_pack_id=mika_pack")
        turn = client.post(
            "/pet/turn",
            json={
                "text": "hi",
                "turnId": "turn-2",
                "metadata": {
                    "character_pack_id": "mika_pack",
                    "user_id": "desktop",
                    "real_user_id": "master",
                },
            },
        )

        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertEqual(health.json()["resourceManifest"]["staticImageCount"], 2)
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json()["schemaVersion"], PET_DISPLAY_SCHEMA_VERSION)
        self.assertEqual(turn.status_code, 200)
        self.assertIn("event: resource_manifest", turn.text)
        self.assertIn("event: display", turn.text)
        self.assertIn("event: done", turn.text)
        self.assertEqual(calls[0]["message"], "hi")
        self.assertEqual(calls[0]["client_mode"], "desktop_pet")
        self.assertEqual(calls[0]["character_pack_id"], "mika_pack")
        self.assertEqual(calls[0]["user_id"], "desktop")
        self.assertEqual(calls[0]["real_user_id"], "master")
        self.assertEqual(guard.released, 1)
        self.assertIn(("pet_turn", True), runtime.observed)

        display_match = re.search(r"event: display\ndata: (.+?)\n\n", turn.text, flags=re.S)
        self.assertIsNotNone(display_match)
        display = json.loads(display_match.group(1)) if display_match else {}
        self.assertEqual(display["turnId"], "turn-2")
        self.assertEqual(display["speech"], "echo: hi")
        self.assertEqual(display["visual"]["emotion"], "害羞")
        static_images = build_petdesk_resource_bundle(self.resources, "mika_pack").runtime_manifest["staticImages"]
        self.assertIn(display["visual"]["assetHandle"], static_images)

    def test_safe_handle_segment_never_returns_path_or_url_shape(self) -> None:
        for raw in ("../secret.png", "https://example.com/a.png", "C:/Users/a.png", "猫娘/开心"):
            segment = safe_handle_segment(raw, "asset")
            self.assertNotIn("/", segment)
            self.assertNotIn("\\", segment)
            self.assertNotIn(":", segment)
            self.assertRegex(segment, r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


if __name__ == "__main__":
    unittest.main()
