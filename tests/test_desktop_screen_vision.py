from __future__ import annotations

import time
import unittest

from companion_v01.client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from companion_v01.desktop_context_engine import build_turn_extra_user_context
from companion_v01.desktop_screen_vision import DesktopScreenVisionWorkspace


DATA_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="


class FakeVisionService:
    def analyze_screen_clip(self, *, frames, context):
        return {
            "summary": "主人刚刚在游戏里从菜单切回了战斗画面，节奏一下子紧起来了。",
            "current_state": "画面停在战斗准备界面。",
            "visible_text": ["Battle", "Start"],
            "concrete_details": ["中间有战斗准备面板", "右侧有角色状态栏"],
            "changes": ["先是菜单", "后来切回战斗"],
            "topics": ["游戏", "战斗"],
            "mood_tags": ["紧张"],
            "salience": 0.72,
            "sensitive": False,
            "confidence": 0.86,
        }


class FakeReactionLLM:
    def call_chat_json(self, **kwargs):
        raise AssertionError("screen vision reactions should use the ready visual note without a second LLM call")


class DesktopScreenVisionWorkspaceTests(unittest.TestCase):
    def test_clip_observation_is_short_term_prompt_context(self) -> None:
        workspace = DesktopScreenVisionWorkspace(vision_service=FakeVisionService(), ttl_sec=600)
        clip = workspace.submit_clip(
            profile_user_id="master",
            session_id="desktop-session",
            frames=[
                {"data_url": DATA_URL, "captured_at": 100, "width": 320, "height": 180},
                {"data_url": DATA_URL, "captured_at": 101, "width": 320, "height": 180},
            ],
            foreground={"title": "Game", "process_name": "game.exe"},
            captured_start_ts=100,
            captured_end_ts=101,
        )

        ready = None
        for _ in range(40):
            ready = workspace.get_clip(
                profile_user_id="master",
                session_id="desktop-session",
                clip_id=clip["clip_id"],
            )
            if ready and ready["status"] == "ready":
                break
            time.sleep(0.025)

        self.assertIsNotNone(ready)
        self.assertEqual(ready["status"], "ready")
        self.assertIn("游戏", ready["summary"])
        prompt = workspace.build_prompt_context(
            profile_user_id="master",
            session_id="desktop-session",
        )
        self.assertIn("【刚刚看到的画面】", prompt)
        self.assertIn("自然参考", prompt)
        self.assertIn("不要把它当成主人亲口说过的话", prompt)
        self.assertIn("Battle", prompt)
        self.assertIn("战斗准备面板", prompt)
        reaction = workspace.build_reaction_with_llm(llm=FakeReactionLLM(), observation=ready)
        self.assertFalse(reaction["skip"])
        self.assertIn("有意思", reaction["speech"])

    def test_desktop_context_adds_screen_vision_only_with_capability(self) -> None:
        class Engine:
            def build_desktop_screen_vision_context(self, **_kwargs):
                return "【刚刚看到的画面】几秒前：主人正在看视频。"

        context = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
            capabilities=(ClientCapability.SCREEN_VISION.value,),
            output_profile=ClientMode.DESKTOP_PET.value,
            renderer_profile=ClientMode.DESKTOP_PET.value,
        )
        payload = {"user_id": "desktop-session", "real_user_id": "master"}
        text = build_turn_extra_user_context(Engine(), payload, context)
        self.assertIn("主人正在看视频", text)

        no_cap_context = context.model_copy(update={"capabilities": ()})
        self.assertEqual(build_turn_extra_user_context(Engine(), payload, no_cap_context), "")


if __name__ == "__main__":
    unittest.main()
