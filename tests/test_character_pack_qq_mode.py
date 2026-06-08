from __future__ import annotations

import unittest

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine


class FakeCharacterResourceService:
    def __init__(self) -> None:
        self.last_client_mode = ""

    def build_character_identity(self, character_pack_id: str) -> dict[str, str]:
        return {
            "character_id": character_pack_id,
            "assistant_name": "Reimu",
            "user_label": "你",
            "app_name": "Reimu Pet",
            "pack_id": character_pack_id,
        }

    def build_persona_prompt_context(
        self,
        character_pack_id: str,
        *,
        resource_manifest=None,
        client_mode: str = "",
    ) -> dict[str, str]:
        self.last_client_mode = client_mode
        return {
            "system_context": f"{client_mode}:{character_pack_id}",
            "reference_context": "persona",
            "active_id": character_pack_id,
        }


class FakeCatgirlResourceManifest:
    def refresh(self):
        return {"ok": True}

    def build_runtime_manifest(self, **kwargs):
        return {
            "defaults": {
                "major": "default",
                "minor": "default",
                "background": "evening_classroom",
                "bgm": "",
                "outfit": "猫娘",
                "emotion": "正常",
            }
        }

    def build_prompt_context(self, **kwargs):
        return "全局 Akane 资源：猫娘"

    def build_character_prompt_context(self, **kwargs):
        return "全局 Akane 桌宠资源：猫娘"

    def normalize_visual_output(self, value, **kwargs):
        return value


class FakeGiftService:
    def build_runtime_projection(self, *, profile_user_id: str) -> dict[str, list]:
        return {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }

    def build_pending_prompt_context(self, **kwargs) -> str:
        return ""


class FakeAkanePersonaCardService:
    def build_prompt_context(self, **kwargs) -> dict[str, str]:
        return {
            "system_context": "Akane 表达侧面：猫娘",
            "reference_context": "你熟悉的其他模样：猫娘",
            "active_id": "akane_catgirl",
        }


class CharacterPackQQModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        self.service = FakeCharacterResourceService()
        self.engine.desktop_pet_character_resources = self.service

    def test_qq_text_speaker_identity_uses_character_pack(self) -> None:
        context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )

        identity = self.engine._resolve_turn_speaker_identity(context, "reimu_demo")

        self.assertEqual(identity["assistant_name"], "Reimu")
        self.assertEqual(identity["user_label"], "你")
        self.assertEqual(identity["pack_id"], "reimu_demo")

    def test_qq_text_prompt_context_forwards_client_mode(self) -> None:
        prompt_context = self.engine._build_desktop_pet_character_pack_prompt_context(
            character_pack_id="reimu_demo",
            client_mode=ClientMode.QQ_TEXT.value,
        )

        self.assertEqual(self.service.last_client_mode, "qq_text")
        self.assertEqual(prompt_context["system_context"], "qq_text:reimu_demo")
        self.assertEqual(prompt_context["active_id"], "reimu_demo")

    def test_qq_text_generation_context_does_not_leak_global_catgirl_resources_or_persona_cards(self) -> None:
        self.engine.resource_manifest = FakeCatgirlResourceManifest()
        self.engine.gift_service = FakeGiftService()
        self.engine.persona_card_service = FakeAkanePersonaCardService()
        self.engine.vision_service = None
        self.engine.store = None
        context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
            capabilities=("speech_segments",),
        )

        generation_context = self.engine._prepare_final_response_context(
            session_id="qq-session",
            user_message="你好",
            recent_raw=[
                {
                    "role": "user",
                    "content": "你好",
                    "timestamp": 1712400000,
                }
            ],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=1712400000,
            profile_user_id="master",
            current_visual_payload={"emotion": "normal"},
            client_context=context,
            resource_manifest=None,
            character_pack_id="reimu_demo",
            allow_tool_call=False,
            final_debug_enabled=False,
        )

        combined_prompt = "\n".join(
            [
                str(generation_context.get("system_prompt") or ""),
                str(generation_context.get("user_prompt") or ""),
                str(generation_context.get("fallback") or ""),
                str(generation_context.get("visual_defaults") or ""),
            ]
        )
        self.assertIn("qq_text:reimu_demo", combined_prompt)
        self.assertNotIn("猫娘", combined_prompt)
        self.assertNotIn("全局 Akane", combined_prompt)
        self.assertNotIn("Akane 表达侧面", combined_prompt)
        self.assertEqual(generation_context["fallback"]["persona"]["active"], "reimu_demo")
        self.assertNotIn("character", generation_context["fallback"])
        self.assertNotIn("scene", generation_context["fallback"])

    def test_memory_compaction_persona_context_skips_akane_persona_cards_for_character_pack(self) -> None:
        self.engine.persona_card_service = FakeAkanePersonaCardService()

        context = self.engine._build_memory_compaction_persona_context(
            profile_user_id="master",
            session_id="qq-session",
            character_pack_id="reimu_demo",
        )

        combined = "\n".join([context["system_context"], context["reference_context"]])
        self.assertIn("memory:reimu_demo", combined)
        self.assertNotIn("猫娘", combined)
        self.assertNotIn("akane_catgirl", context["active_id"])


if __name__ == "__main__":
    unittest.main()
