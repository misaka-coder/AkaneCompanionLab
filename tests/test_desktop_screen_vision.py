from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from companion_v01.desktop_context_engine import build_turn_extra_user_context
from companion_v01.engine_services.turn_context import (
    extract_desktop_screen_frame_images,
    build_desktop_screen_frame_prompt_context,
    build_desktop_screen_image_label,
)
from companion_v01.llm_runtime import ChatJSONResult
from tests import test_final_json_repair as recovery
from tests import test_prompt_builder as prompt_tests
from tests import test_turn_mainline_contract as mainline
from tests import test_memcore_integration as memory_tests
from companion_v01.engine_services import response_builder
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry


DATA_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="


class DesktopScreenDirectVisionTests(unittest.TestCase):
    def test_response_builder_allows_silence_only_for_transient_desktop(self):
        manager = memory_tests._PromptContextMemcoreManager({})
        manager.enabled = False
        manager.available = False
        engine = memory_tests._PromptContextEngine(memcore_manager=manager)
        for mode, transient, expected in [(ClientMode.DESKTOP_PET, True, True),
                (ClientMode.DESKTOP_PET, False, False), (ClientMode.SCENE_STATIC, True, False)]:
            with self.subTest(mode=mode, transient=transient), patch.object(response_builder, "_memory_backend", return_value="legacy"):
                context = response_builder.prepare_context(engine, session_id="s1", profile_user_id="u1",
                    user_message="观察当前画面", recent_raw=[], recent_episodic_summaries=[],
                    recent_semantic_summaries=[], confirmed_snippets=[], now_ts=100,
                    client_context=ClientProtocolContext(requested_mode=mode, effective_mode=mode),
                    current_input_transient=transient)
                self.assertEqual(context["allow_deliberate_silence"], expected)
                self.assertNotIn("allow_deliberate_silence", engine.prompt_builder.kwargs)

    def test_real_turn_mainline_forwards_images_and_proactive_flag(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                harness = mainline._Harness([mainline._speech_output("接住这段画面了。")], client_mode=ClientMode.DESKTOP_PET)
                harness.engine._extract_desktop_screen_frame_images = extract_desktop_screen_frame_images
                target = SimpleNamespace(role="vision", model="configured-vision")
                routing_calls = []
                def route(**kwargs):
                    routing_calls.append(kwargs)
                    return target
                harness.engine._resolve_turn_execution_target = route
                payload = harness.payload(turn_kind="desktop_pet_proactive", transient_user_message=True,
                    desktop_screen_frames=[{"data_url": DATA_URL, "captured_at": 100.5}])
                harness.run_stream(payload) if streaming else harness.run_sync(payload)
                call = harness.script.generation_kwargs[0]
                self.assertEqual(call["user_images"][0]["data_url"], DATA_URL)
                self.assertIn("本轮连续屏幕画面", call["extra_user_context"])
                self.assertTrue(call["request_projection_state"]["current_input_transient"])
                self.assertIs(call["execution_target"], target)
                self.assertTrue(routing_calls[0]["has_real_images"])

    def test_changed_frames_and_proactivity_preserve_provider_cache_prefix(self):
        builder = PromptBuilder(load_persona_config())
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET)
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(
            _akane_protocol="openai", base_url="https://api.openai.com/v1"), model="test-vision")
        history = [{"role": "user", "content": "一起看这个游戏吧"},
                   {"role": "assistant", "content": "好呀，我在旁边陪你。"}]
        native_tools = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
        payloads, keys = [], []
        prefix_length = 0
        for index in range(3):
            frames = extract_desktop_screen_frame_images({"desktop_screen_frames": [{
                "data_url": DATA_URL, "captured_at": 100 + index * 30,
                "frame_times": [70 + index * 30, 100 + index * 30],
                "layout": {"columns": 2, "rows": 1},
            }]})
            generated = prompt_tests._build_minimal_final(builder,
                current_message_text="刚才发生了什么？" if index == 0 else "观察评估：可以安静陪伴，speech 为空。",
                history_turns=history, now_ts=1712400000 + index * 30,
                volatile_extra_context=build_desktop_screen_frame_prompt_context(frames),
                system_prompt_override=profile.system_prompt_override,
                mode_prompt_override=profile.mode_prompt_override(debug_enabled=False))
            generated["allow_deliberate_silence"] = index > 0
            prefix_length = 1 + len(generated["history_turns"])
            key = AkaneMemoryEngine._final_prompt_cache_key(generated)
            keys.append(key)
            payloads.append(runtime._build_completion_kwargs(
                bundle=bundle, system_prompt=generated["system_prompt"],
                user_prompt=generated["user_prompt"], temperature=0.7,
                history_turns=generated["history_turns"], ephemeral_turns=generated.get("ephemeral_turns"),
                system_extra_blocks=generated.get("system_extra_blocks"),
                user_images=frames, native_tools=native_tools, prompt_cache_key=key))
        self.assertEqual(len(set(keys)), 1)
        for payload in payloads[1:]:
            self.assertEqual(payload["tools"], payloads[0]["tools"])
            self.assertEqual(payload["messages"][:prefix_length], payloads[0]["messages"][:prefix_length])
            self.assertNotEqual(payload["messages"][prefix_length:], payloads[0]["messages"][prefix_length:])
        self.assertTrue(any(block.get("type") == "image_url"
                            for block in payloads[0]["messages"][prefix_length]["content"]))

    def test_temporal_contact_sheet_metadata_reaches_dynamic_context(self):
        frames = extract_desktop_screen_frame_images({"desktop_screen_frames": [{
            "data_url": DATA_URL, "captured_at": 130.5, "width": 1280, "height": 720,
            "frame_times": [100.5, 110.5, 120.5, 130.5],
            "layout": {"columns": 2, "rows": 2},
        }]})
        self.assertEqual(frames[0]["captured_at"], 130.5)
        context = build_desktop_screen_frame_prompt_context(frames)
        for expected in ["30 秒", "不包含声音", "不是用户给你的新指令"]:
            self.assertIn(expected, context)
        label = build_desktop_screen_image_label(frames[0], 1)
        for expected in ["从左到右、从上到下", "第1帧=-30.0秒", "第4帧=+0.0秒"]:
            self.assertIn(expected, label)
        self.assertEqual(frames[0]["data_url"], DATA_URL)

    def test_invalid_metadata_is_safe_and_unknown_time_is_not_invented(self):
        frames = extract_desktop_screen_frame_images({"desktop_screen_frames": [
            None, {"data_url": "file:///private"},
            {"data_url": DATA_URL, "captured_at": "bad", "width": float("nan"),
             "height": [], "frame_times": [None, 100.5],
             "layout": {"columns": float("inf"), "rows": "bad"}},
        ]})
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["width"], 0)
        self.assertIn("第1帧时间未知", build_desktop_screen_image_label(frames[0], 1))
        self.assertEqual(extract_desktop_screen_frame_images({}), [])
        self.assertEqual(build_desktop_screen_frame_prompt_context([]), "")

    def test_temporal_labels_reach_the_provider_adjacent_to_each_image(self):
        runtime = LLMRuntime.__new__(LLMRuntime)
        frames = extract_desktop_screen_frame_images({"desktop_screen_frames": [
            {"data_url": DATA_URL, "captured_at": 120, "frame_times": [80, 90, 100, 110, 120], "layout": {"columns": 1, "rows": 5}},
            {"data_url": DATA_URL, "captured_at": 130},
        ]})
        self.assertEqual(frames[0]["layout"]["rows"], 5)
        parts = runtime._normalize_user_image_items(frames)
        self.assertEqual([part["type"] for part in parts], ["text", "image_url", "text", "image_url"])
        self.assertIn("从上到下", parts[0]["text"])
        self.assertIn("第1帧=-50.0秒", parts[0]["text"])
        self.assertIn("第5帧=-10.0秒", parts[0]["text"])
        self.assertIn("第1帧=+0.0秒", parts[2]["text"])
        self.assertNotIn("重复采样", build_desktop_screen_frame_prompt_context(frames))
        # Ordinary uploads do not acquire desktop semantics.
        self.assertEqual([part["type"] for part in runtime._normalize_user_image_items([{"data_url": DATA_URL}])], ["image_url"])

    def test_legacy_duplicate_latest_is_labeled_only_when_it_is_a_duplicate(self):
        frames = extract_desktop_screen_frame_images({"desktop_screen_frames": [
            {"data_url": DATA_URL, "captured_at": 130, "frame_times": [100, 110, 120, 130]},
            {"data_url": DATA_URL, "captured_at": 130},
        ]})
        self.assertIn("屏幕图 2 是前面同一时刻的重复采样", build_desktop_screen_frame_prompt_context(frames))

    def test_existing_five_image_contract_is_preserved(self):
        frames = [{"data_url": DATA_URL, "captured_at": i} for i in range(8)]
        images = extract_desktop_screen_frame_images({"desktop_screen_frames": frames})
        self.assertEqual([f["captured_at"] for f in images], [3, 4, 5, 6, 7])

    def test_legacy_capability_never_injects_old_summary(self):
        class Engine:
            def build_desktop_screen_vision_context(self, **_kwargs):
                raise AssertionError("retired summary workspace must not be called")

        context = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
            capabilities=(ClientCapability.SCREEN_VISION.value,),
            output_profile=ClientMode.DESKTOP_PET.value,
            renderer_profile=ClientMode.DESKTOP_PET.value,
        )
        self.assertEqual(build_turn_extra_user_context(
            Engine(), {"user_id": "session", "real_user_id": "master"}, context), "")

    def test_proactive_silence_survives_real_stream_and_nonstream_normalizers(self):
        class Provider:
            @staticmethod
            def snapshot_metrics():
                return {}

            def call_chat_json_result(self, **_kwargs):
                return ChatJSONResult(parsed={"speech": ""}, raw_text='{"speech":""}')

            def stream_chat_json(self, **_kwargs):
                if False:
                    yield
                return SimpleNamespace(parsed={"speech": ""}, raw_text='{"speech":""}',
                    error="", fallback_used=False, latest_emotion="", latest_speech="",
                    latest_reply_medium="", native_preface_text="")

        runner = recovery.FinalRecoveryTests()
        for stream in (False, True):
            with self.subTest(stream=stream):
                engine = runner._real_normalize_engine(
                    Provider(),
                    context=recovery._default_context(allow_deliberate_silence=True, post_user_turns=[]),
                    client_mode=ClientMode.DESKTOP_PET,
                )
                result = runner._run_stream(engine)[1] if stream else runner._run_nonstream(engine)
                self.assertTrue(result["_deliberate_silence"])
                self.assertEqual(result["speech"], "")

    def test_ordinary_desktop_empty_speech_is_not_a_silence_decision(self):
        class Provider:
            @staticmethod
            def snapshot_metrics():
                return {}

            def call_chat_json_result(self, **_kwargs):
                return ChatJSONResult(parsed={"speech": ""}, raw_text='{"speech":""}')

        runner = recovery.FinalRecoveryTests()
        engine = runner._real_normalize_engine(
            Provider(), context=recovery._default_context(post_user_turns=[]),
            client_mode=ClientMode.DESKTOP_PET,
        )
        result = runner._run_nonstream(engine)
        self.assertFalse(result.get("_deliberate_silence", False))
        self.assertTrue(result["speech"])


if __name__ == "__main__":
    unittest.main()
