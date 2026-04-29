from __future__ import annotations

import unittest

from companion_v01.desktop_pet_contract import (
    DESKTOP_PET_CONTRACT_VERSION,
    build_desktop_pet_error_payload,
    build_desktop_pet_health_payload,
    decorate_resource_manifest_for_desktop_pet,
)


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


if __name__ == "__main__":
    unittest.main()
