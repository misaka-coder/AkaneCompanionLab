from __future__ import annotations

import unittest

from companion_v01.client_protocol import ClientCapability, ClientMode
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.output_adapters import OutputAdapterRegistry
from companion_v01.prompt_profiles import PromptModule, PromptProfileRegistry


class ClientProtocolTests(unittest.TestCase):
    def test_default_payload_resolves_to_scene_static(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({})

        self.assertEqual(context.requested_mode, ClientMode.SCENE_STATIC)
        self.assertEqual(context.effective_mode, ClientMode.SCENE_STATIC)
        self.assertIn(ClientCapability.SPEECH_SEGMENTS.value, context.capabilities)
        self.assertIn(ClientCapability.BACKGROUND.value, context.capabilities)
        self.assertEqual(context.degraded_from, "")

    def test_unknown_or_legacy_gal_mode_resolves_to_scene_static(self) -> None:
        registry = ModeProfileRegistry()

        unknown = registry.resolve_from_payload({"client_mode": "something_else"})
        legacy = registry.resolve_from_payload({"mode": "gal"})

        self.assertEqual(unknown.effective_mode, ClientMode.SCENE_STATIC)
        self.assertEqual(legacy.effective_mode, ClientMode.SCENE_STATIC)

    def test_unimplemented_live2d_mode_degrades_to_scene_static(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload(
            {
                "client_mode": "scene_live2d",
                "client_capabilities": ["speech_segments", "background", "bgm", "live2d"],
            }
        )

        self.assertEqual(context.requested_mode, ClientMode.SCENE_LIVE2D)
        self.assertEqual(context.effective_mode, ClientMode.SCENE_STATIC)
        self.assertEqual(context.degraded_from, ClientMode.SCENE_LIVE2D.value)
        self.assertEqual(context.degrade_reason, "profile_not_implemented")

    def test_qq_text_mode_is_implemented_as_text_profile(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})

        self.assertEqual(context.requested_mode, ClientMode.QQ_TEXT)
        self.assertEqual(context.effective_mode, ClientMode.QQ_TEXT)
        self.assertEqual(context.degraded_from, "")

    def test_scene_static_adapter_adds_client_and_runtime_state(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({"client_mode": "scene_static"})
        output = OutputAdapterRegistry().normalize(
            {
                "emotion": "shy",
                "speech": "主人。",
                "scene": {"major": "home", "minor": "bedroom", "background": "night", "bgm": "quiet"},
                "character": {"outfit": "水手服"},
            },
            context,
        )

        self.assertEqual(output["client_mode"], "scene_static")
        self.assertEqual(output["client"]["effective_mode"], "scene_static")
        self.assertEqual(output["_runtime_state"]["last_scene"]["background_id"], "night")
        self.assertEqual(output["_runtime_state"]["last_character"]["outfit_id"], "水手服")
        self.assertEqual(output["_runtime_state"]["last_emotion"], "shy")

    def test_qq_text_adapter_strips_scene_only_render_fields(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})
        output = OutputAdapterRegistry().normalize(
            {
                "emotion": "normal",
                "speech": "主人，我在。",
                "scene": {"major": "home"},
                "character": {"outfit": "水手服"},
                "live2d": {"model": "akane"},
            },
            context,
        )

        self.assertEqual(output["client_mode"], "qq_text")
        self.assertNotIn("scene", output)
        self.assertNotIn("character", output)
        self.assertNotIn("live2d", output)
        self.assertNotIn("last_scene", output["_runtime_state"])
        self.assertNotIn("last_character", output["_runtime_state"])

    def test_desktop_pet_adapter_strips_web_fields_but_keeps_character_and_activity(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({"client_mode": "desktop_pet"})
        output = OutputAdapterRegistry().normalize(
            {
                "emotion": "happy",
                "speech": "主人，我在。",
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": "quiet"},
                "character": {"outfit": "猫娘", "sprite": "legacy_sprite"},
                "live2d": {"model": "akane"},
                "pet": {"motion": "idle"},
                "activity": {"action": "pause", "target": "current"},
            },
            context,
        )

        self.assertEqual(output["client_mode"], "desktop_pet")
        self.assertEqual(output["character"], {"outfit": "猫娘"})
        self.assertEqual(output["activity"], {"action": "pause", "target": "current"})
        self.assertNotIn("scene", output)
        self.assertNotIn("live2d", output)
        self.assertNotIn("pet", output)
        self.assertEqual(output["_runtime_state"]["last_character"]["outfit_id"], "猫娘")
        self.assertEqual(output["_runtime_state"]["last_character"]["expression_id"], "happy")
        self.assertNotIn("last_scene", output["_runtime_state"])
        self.assertNotIn("last_live2d", output["_runtime_state"])
        self.assertNotIn("last_pet", output["_runtime_state"])

    def test_scene_static_prompt_profile_keeps_full_scene_modules(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({"client_mode": "scene_static"})
        profile = PromptProfileRegistry().resolve(context)

        self.assertEqual(profile.id, ClientMode.SCENE_STATIC.value)
        self.assertTrue(profile.includes(PromptModule.CLIENT_MODE))
        self.assertTrue(profile.includes(PromptModule.CURRENT_VISUAL_STATE))
        self.assertTrue(profile.includes(PromptModule.SCENE_OBSERVATION))
        self.assertTrue(profile.includes(PromptModule.OUTFIT_OBSERVATION))
        self.assertTrue(profile.includes(PromptModule.RESOURCE_MANIFEST))
        self.assertTrue(profile.includes(PromptModule.PENDING_GIFTS))
        self.assertTrue(profile.includes(PromptModule.FOCUSED_GIFT_OBSERVATION))
        self.assertTrue(profile.includes(PromptModule.PERSONA))
        self.assertTrue(profile.includes(PromptModule.TOOLS))
        self.assertIn("scene_visual_resources", profile.system_block_ids)
        self.assertIn("tool_execution_intent", profile.system_block_ids)
        self.assertNotIn("desktop_pet_visual", profile.system_block_ids)
        self.assertNotIn("qq_text_mode", profile.system_block_ids)

    def test_unimplemented_mode_uses_effective_scene_static_prompt_profile(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload(
            {
                "client_mode": "scene_live2d",
                "client_capabilities": ["speech_segments", "background", "bgm", "live2d"],
            }
        )
        profile = PromptProfileRegistry().resolve(context)

        self.assertEqual(context.requested_mode, ClientMode.SCENE_LIVE2D)
        self.assertEqual(context.effective_mode, ClientMode.SCENE_STATIC)
        self.assertEqual(profile.id, ClientMode.SCENE_STATIC.value)

    def test_qq_prompt_profile_uses_text_schema_override(self) -> None:
        context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})
        profile = PromptProfileRegistry().resolve(context)

        self.assertEqual(profile.id, ClientMode.QQ_TEXT.value)
        self.assertFalse(profile.includes(PromptModule.CURRENT_VISUAL_STATE))
        self.assertFalse(profile.includes(PromptModule.RESOURCE_MANIFEST))
        self.assertFalse(profile.supports_thought_debug)
        self.assertIn("qq_text_mode", profile.system_block_ids)
        self.assertIn("tool_execution_intent", profile.system_block_ids)
        self.assertNotIn("scene_visual_resources", profile.system_block_ids)
        self.assertNotIn("desktop_pet_visual", profile.system_block_ids)
        self.assertNotIn("character.outfit", profile.system_prompt_override)
        self.assertNotIn("scene.major", profile.system_prompt_override)
        self.assertIn("当前是 QQ 文字聊天模式", profile.system_prompt_override)
        self.assertIn("qq_text", profile.mode_prompt_override(debug_enabled=False))
        self.assertIn("字段固定为 emotion, reply_medium, speech, speech_segments, tool_call", profile.mode_prompt_override(debug_enabled=False))
        self.assertIn('"reply_medium":"text","speech":"主人，我在哦。","speech_segments":[],"tool_call":null', profile.mode_prompt_override(debug_enabled=False))
        self.assertNotIn("不要输出 scene", profile.mode_prompt_override(debug_enabled=False))
        self.assertNotIn("thought", profile.mode_prompt_override(debug_enabled=True).split("字段固定为", 1)[-1].split("。", 1)[0])

    def test_desktop_prompt_profile_excludes_scene_observations_but_keeps_pet_context(self) -> None:
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET)

        self.assertTrue(profile.includes(PromptModule.CLIENT_MODE))
        self.assertTrue(profile.includes(PromptModule.CURRENT_VISUAL_STATE))
        self.assertTrue(profile.includes(PromptModule.RESOURCE_MANIFEST))
        self.assertTrue(profile.includes(PromptModule.OUTFIT_OBSERVATION))
        self.assertTrue(profile.includes(PromptModule.PENDING_GIFTS))
        self.assertTrue(profile.includes(PromptModule.FOCUSED_GIFT_OBSERVATION))
        self.assertTrue(profile.includes(PromptModule.PERSONA))
        self.assertTrue(profile.includes(PromptModule.TOOLS))
        self.assertFalse(profile.includes(PromptModule.SCENE_OBSERVATION))
        self.assertIn("desktop_pet_visual", profile.system_block_ids)
        self.assertIn("desktop_pet_activity", profile.system_block_ids)
        self.assertIn("tool_execution_intent", profile.system_block_ids)
        self.assertNotIn("scene_visual_resources", profile.system_block_ids)
        self.assertNotIn("qq_text_mode", profile.system_block_ids)
        self.assertIn("desktop_pet 桌宠模式", profile.system_prompt_override)
        self.assertIn("[CURRENT ASSISTANT STATE - EMBODY THIS]", profile.system_prompt_override)
        self.assertIn("activity 是给桌宠执行的请求", profile.system_prompt_override)
        self.assertNotIn("scene.major 表示场景大类", profile.system_prompt_override)
        self.assertNotIn("像 galgame 选项", profile.system_prompt_override)
        self.assertNotIn("背景变体", profile.system_prompt_override)


if __name__ == "__main__":
    unittest.main()
