from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from companion_v01.client_protocol import ClientMode
from companion_v01.desktop_pet_engine import prepare_desktop_turn_attachments
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.tool_handlers.attachments import InspectAttachmentToolHandler
from companion_v01.tool_handlers.core import ToolExecutionContext
from tests.test_desktop_workspace_panel import _make_workspace_engine
from tests.test_turn_mainline_contract import _Harness, _speech_output
from tests.test_prompt_builder import _build_minimal_final


class DesktopCurrentAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = _make_workspace_engine(self.root)
        self.engine.native_chat_vision_status = lambda **kw: {"enabled": True}
        self.engine.vision_service.schedule_attachment_image_observation = Mock(
            side_effect=AssertionError("desktop import must not launch legacy observer"))

    def import_file(self, name="photo.png", *, profile="u", session="s", character="akane_v1"):
        source = self.root / name
        if source.suffix == ".png":
            Image.new("RGB", (4, 4), "red").save(source)
        else:
            source.write_text("reference content", encoding="utf-8")
        result = self.engine.import_desktop_pet_local_paths(
            profile_user_id=profile, session_id=session, paths=[str(source)],
            character_pack_id=character)
        self.assertEqual(result["imported"], 1)
        return result["attachments"][0]

    def prepare(self, ids, **kwargs):
        return prepare_desktop_turn_attachments(self.engine, profile_user_id="u", session_id="s",
            character_pack_id=kwargs.pop("character", "akane_v1"), attachment_ids=ids, **kwargs)

    def test_import_to_real_native_pixels_without_observation_or_private_paths(self):
        item = self.import_file()
        self.assertEqual(item["status"], "ready")
        self.engine.vision_service.schedule_attachment_image_observation.assert_not_called()
        result = self.prepare([item["attachment_id"]])
        self.assertEqual(result["attachment_ids"], [item["attachment_id"]])
        self.assertTrue(result["images"][0]["data_url"].startswith("data:image/png;base64,"))
        self.assertIn(item["attachment_handle"], result["context"])
        self.assertNotIn(str(self.root), result["context"])
        self.assertNotIn("storage_relpath", result["context"])

    def test_exact_scope_rejects_foreign_character_profile_session_and_alias(self):
        own = self.import_file()
        foreign = [self.import_file("other.png", character="other")["attachment_id"],
            self.import_file("other-user.png", profile="other")["attachment_id"],
            self.import_file("other-session.png", session="other")["attachment_id"], "latest"]
        result = self.prepare([own["attachment_id"], *foreign, own["attachment_id"]])
        self.assertEqual(result["attachment_ids"], [own["attachment_id"]])
        self.assertEqual(len(result["skipped"]), 4)
        self.assertEqual(len(result["images"]), 1)

    def test_unavailable_vision_keeps_file_binding_without_claiming_pixels(self):
        item = self.import_file()
        self.engine.native_chat_vision_status = lambda **kw: {"enabled": False, "reason": "vision_disabled"}
        result = self.prepare([item["attachment_id"]])
        self.assertEqual(result["images"], [])
        self.assertEqual(result["attachment_ids"], [item["attachment_id"]])
        self.assertIn("vision_disabled", result["context"])

    def test_real_sync_and_stream_mainline_passes_bound_pixels_and_metadata(self):
        item = self.import_file()
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                harness = _Harness([_speech_output("已看到附件。")], client_mode=ClientMode.DESKTOP_PET)
                harness.store.get_attachment_inbox_item = self.engine.store.get_attachment_inbox_item
                harness.engine.prepare_native_image_inputs = self.engine.prepare_native_image_inputs
                harness.engine._extract_native_user_images = AkaneMemoryEngine._extract_native_user_images.__get__(harness.engine)
                payload = harness.payload(user_id="s", real_user_id="u", current_attachment_ids=[item["attachment_id"]])
                (harness.run_stream if streaming else harness.run_sync)(payload)
                generation = harness.script.generation_kwargs[0]
                self.assertEqual(generation["user_images"][0]["attachment_id"], item["attachment_id"])
                self.assertIn(item["attachment_handle"], generation["extra_user_context"])
                user = next(x for x in harness.store.messages if x["role"] == "user")
                self.assertEqual(user["memory_metadata"]["current_attachment_ids"], [item["attachment_id"]])

    def test_latest_is_bound_to_current_not_newer_workspace_item(self):
        first = self.import_file("first.txt")
        self.import_file("second.txt")
        handler = InspectAttachmentToolHandler(attachment_service=self.engine.attachment_inbox_service)
        context = ToolExecutionContext(profile_user_id="u", session_id="s", now_ts=100,
            visual_payload={}, client_mode="desktop_pet", request_context={"current_attachment_ids": [first["attachment_id"]]})
        result = handler.execute(call={"target": "latest", "kind": "any"}, context=context)
        self.assertEqual(result.stream_events[0]["attachment"]["attachment_id"], first["attachment_id"])
        empty = ToolExecutionContext(profile_user_id="u", session_id="s", now_ts=100,
            visual_payload={}, client_mode="desktop_pet", request_context={"current_attachment_ids": []})
        result = handler.execute(call={"target": "latest", "kind": "any"}, context=empty)
        self.assertEqual(result.stream_events, [])
        self.assertIn("没有绑定", result.followup_context)

    def test_current_materials_change_provider_tail_not_stable_prefix(self):
        builder = PromptBuilder(load_persona_config())
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET)
        runtime = LLMRuntime.__new__(LLMRuntime)
        bundle = SimpleNamespace(client=SimpleNamespace(_akane_protocol="openai",
            base_url="https://example.invalid/v1"), model="same-vision-model")
        history = [{"role": "user", "content": "帮我看看附件"}, {"role": "assistant", "content": "可以。"}]
        payloads, keys = [], []
        for name in ("one.png", "two.png"):
            prepared = self.prepare([self.import_file(name)["attachment_id"]])
            prompt = _build_minimal_final(builder, current_message_text="这张有什么？",
                history_turns=history, volatile_extra_context=prepared["context"],
                system_prompt_override=profile.system_prompt_override,
                mode_prompt_override=profile.mode_prompt_override(debug_enabled=False))
            keys.append(AkaneMemoryEngine._final_prompt_cache_key(prompt))
            prefix_length = 1 + len(prompt["history_turns"])
            payloads.append(runtime._build_completion_kwargs(bundle=bundle,
                system_prompt=prompt["system_prompt"], user_prompt=prompt["user_prompt"], temperature=0.7,
                history_turns=prompt["history_turns"], ephemeral_turns=prompt.get("ephemeral_turns"),
                system_extra_blocks=prompt.get("system_extra_blocks"), user_images=prepared["images"],
                prompt_cache_key=keys[-1]))
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(payloads[0]["messages"][:prefix_length], payloads[1]["messages"][:prefix_length])
        self.assertNotEqual(payloads[0]["messages"][prefix_length:], payloads[1]["messages"][prefix_length:])


if __name__ == "__main__":
    unittest.main()
