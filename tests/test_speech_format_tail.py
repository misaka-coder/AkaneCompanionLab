"""Speech contracts stay stable without a per-request formatting reminder."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.engine_services import response_builder
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.speech_policy import speech_prompt_profile
from tests.test_memcore_integration import _PromptContextEngine, _PromptContextMemcoreManager


class SpeechFormatTailTests(unittest.TestCase):
    def test_real_context_pipeline_has_no_formatting_tail_on_desktop_private_or_group(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            character = root / "cecilia" / "character.json"
            character.parent.mkdir()
            character.write_text(json.dumps({"voice": {"provider": "irodori"},
                "speech_output": {"enabled": True, "language": "日语"}}), encoding="utf-8")
            manager = _PromptContextMemcoreManager({})
            manager.enabled = manager.available = False
            engine = _PromptContextEngine(memcore_manager=manager)
            engine.prompt_builder = PromptBuilder(load_persona_config())
            engine._get_prompt_profile_registry = PromptProfileRegistry
            engine._build_current_visual_context = lambda **_kwargs: ""
            engine.desktop_pet_character_resources = DesktopPetCharacterResourceService(characters_dir=root)
            for mode, session in ((ClientMode.DESKTOP_PET, "desktop"),
                    (ClientMode.QQ_TEXT, "master"), (ClientMode.QQ_TEXT, "qq_group_example")):
                transient_by_capability = []
                for capability in (True, False):
                    with self.subTest(mode=mode, session=session, capability=capability):
                        context = ClientProtocolContext(requested_mode=mode, effective_mode=mode,
                            capabilities=("speech_units_v1",) if capability else ())
                        kwargs = dict(session_id=session, profile_user_id="owner", character_pack_id="cecilia",
                            user_message="Current question", recent_raw=[], recent_episodic_summaries=[],
                            recent_semantic_summaries=[], confirmed_snippets=[], now_ts=100,
                            client_context=context)
                        with patch("config.MEMORY_BACKEND", "legacy"), patch("config.LLM_AUTO_COMPACT_TOKEN_LIMIT", 0):
                            first = response_builder.prepare_context(engine, **kwargs)
                            second = response_builder.prepare_context(engine, **kwargs)
                        for result in (first, second):
                            self.assertFalse(any(section.get("name") in
                                {"user.speech_format_tail", "user.final_reply_format_reminder"}
                                for section in result.get("prompt_audit_sections", [])))
                            self.assertFalse(any(str(turn.get("content", "")).startswith("准备最终回复时")
                                for turn in result["ephemeral_turns"]))
                            if capability:
                                self.assertIn("普通对话填写完整 say 用于语音合成", result["system_prompt"])
                        self.assertEqual(first["ephemeral_turns"], second["ephemeral_turns"])
                        transient_by_capability.append(first["ephemeral_turns"])
                self.assertEqual(*transient_by_capability)

    def test_all_speech_profiles_keep_complete_live_examples_in_the_stable_contract(self):
        profile = PromptProfileRegistry().get(ClientMode.DESKTOP_PET)
        policies = [{"language": language, "style_mode": "description"}
                    for language in ("日语", "日本語", "ja", "英语")]
        policies.append({"language": "日语", "default_style": "calm",
            "styles": {"calm": {"profile_id": "reference", "description": "平静"}}})
        for policy in policies:
            with self.subTest(policy=policy):
                updated = speech_prompt_profile(profile, policy)
                self.assertFalse(hasattr(updated, "request_tail_prompt"))
                sample = json.loads(next(line for line in updated.fast_mode_prompt.splitlines() if line.startswith("{")))
                self.assertTrue(all(sample["utterances"][0][key] for key in ("text", "say", "style")))
                self.assertEqual(updated.fast_mode_prompt, updated.debug_mode_prompt)
                self.assertNotIn("[archived]", updated.fast_mode_prompt)
                self.assertNotIn("[hidden]", updated.fast_mode_prompt)


if __name__ == "__main__":
    unittest.main()
