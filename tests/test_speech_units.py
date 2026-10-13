from __future__ import annotations

import asyncio
import io
import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.llm_runtime import _TopLevelJSONStreamTap
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.speech_policy import character_speech_policy, configure_speech_prompt
from companion_v01.speech_units import VoiceEventReplay, project_speech_units, voice_segments
from companion_v01.tts_provider_runtime import resolve_tts_runtime_provider, synthesize_speech_units
from companion_v01.tts_service import SynthesizedTTSResult, TTSServiceError


class SpeechUnitsTests(unittest.TestCase):
    def test_hidden_voice_values_keep_original_fields_and_emit_no_audio(self):
        from companion_v01.speech_units import SpeechUnitsStream

        for marker in ("[hidden]", "[archived]"):
            units = [{"emotion": "shy", "text": "旧话。", "say": " " + marker + " "},
                     {"text": "继续。", "say": "続けます。"}]
            raw = json.dumps({"utterances": units}, ensure_ascii=False)
            expected = voice_segments(units)
            self.assertEqual([event["text"] for event in expected], ["続けます。"])
            self.assertEqual(expected[0]["emotion"], "shy")
            for size in (1, 2, 11, len(raw)):
                with self.subTest(marker=marker, size=size):
                    tap = SpeechUnitsStream()
                    events = []
                    for start in range(0, len(raw), size):
                        events.extend(tap.feed(raw[start:start + size]))
                    events.extend(tap.finish())
                    self.assertEqual([event for event in events if event["type"] == "voice_segment"], expected)
                    self.assertEqual([event["code"] for event in events if event["type"] == "speech_warning"], ["say_hidden"])
                    self.assertFalse(next(event for event in events if event["type"] == "voice_unit_end")["has_voice"])
                    self.assertEqual(tap.snapshot_units()[0]["say"], units[0]["say"])

    def test_expression_changes_inherit_without_changing_voice_styles_or_sentence_count(self):
        units = [{"emotion": "normal", "style": "calm", "text": "先解释。再补充。", "say": "説明します。続けます。"},
                 {"style": "happy", "text": "还是同一表情。", "say": "続きです。"},
                 {"emotion": "shy", "text": "（移开视线）", "say": None},
                 {"style": "calm", "text": "有点不好意思。", "say": "少し恥ずかしいです。"}]
        payload = {"emotion": "happy", "utterances": units}
        raw = json.dumps(payload, ensure_ascii=True)
        expected_voice = voice_segments(units, default_emotion="happy")
        for size in (1, 2, 11, len(raw)):
            with self.subTest(size=size):
                tap = _TopLevelJSONStreamTap()
                events = []
                for start in range(0, len(raw), size):
                    events.extend(tap.feed(raw[start:start + size]))
                self.assertEqual([event for event in events if event["type"] == "voice_segment"], expected_voice)
                self.assertEqual([event["emotion"] for event in events if event["type"] == "speech_unit_text"],
                                 ["normal", "normal", "shy", "shy"])
                self.assertEqual(tap.latest_emotion, "happy", "QQ's overall expression stays independent")
                self.assertEqual(project_speech_units(payload)["utterances"][0]["emotion"], "normal")
        self.assertEqual([event["style"] for event in expected_voice], ["calm", "calm", "happy", "calm"])

    def test_missing_first_expression_uses_root_and_invalid_expression_is_ignored(self):
        units = [{"text": "开始。", "say": "はい。"},
                 {"emotion": ["shy"], "text": "继续。", "say": "続けます。"},
                 {"emotion": "x" * 81, "text": "结束。", "say": "終わりです。"}]
        tap = _TopLevelJSONStreamTap()
        events = tap.feed(json.dumps({"emotion": "happy", "utterances": units}))
        self.assertEqual([event["emotion"] for event in events if event["type"] == "speech_unit_text"], ["happy"] * 3)
        self.assertTrue(all("emotion" not in unit for unit in project_speech_units({"utterances": units})["utterances"]))

    def test_late_or_nested_expression_does_not_change_an_already_delivered_unit(self):
        tap = _TopLevelJSONStreamTap()
        first = tap.feed('{"emotion":"normal","utterances":[{"text":"你好。","say":"こんにちは。次')
        self.assertEqual([event["emotion"] for event in first if event["type"] == "voice_segment"], ["normal"])
        tail = tap.feed('です。","emotion":"shy","tool":{"emotion":"angry"}}]}')
        self.assertTrue(all(event.get("emotion", "normal") == "normal" for event in tail))
        self.assertNotIn("emotion", tap._units_stream.snapshot_units()[0])

    def test_history_and_voice_artifact_keep_expression_but_archive_spoken_history(self):
        from companion_v01.assistant_history_projection import project_assistant_history_content
        from companion_v01.voice_runtime.speech_artifact import (
            MEDIA_TYPE,
            decode_speech_artifact,
            encode_speech_artifact,
        )
        unit = {"emotion": "shy", "style": "calm", "text": "有点害羞。", "say": "少し恥ずかしいです。"}
        projected = project_assistant_history_content(json.dumps({"utterances": [unit]}))
        self.assertIn("有点害羞。", projected)
        self.assertIn("shy", projected)
        self.assertNotIn("少し恥ずかしいです。", projected)
        event = voice_segments([unit])[0]
        spoken = decode_speech_artifact(SimpleNamespace(media_type=MEDIA_TYPE, text=encode_speech_artifact(event)))
        self.assertEqual((spoken["emotion"], spoken["style"], spoken["text"], spoken["say"]),
                         ("shy", "calm", unit["text"], unit["say"]))

    def test_full_text_is_available_before_say_for_preview(self):
        tap = _TopLevelJSONStreamTap()
        events = tap.feed('{"utterances":[{"text":"累了就休息吧。慢慢来。",')
        self.assertEqual([e for e in events if e["type"] == "speech_unit_text"],
                         [{"type": "speech_unit_text", "unit_index": 0, "text": "累了就休息吧。慢慢来。"}])
        self.assertFalse([e for e in events if e["type"] == "voice_segment"])

    def test_silent_unit_completion_preserves_text_and_does_not_invent_audio(self):
        for unit in ({"text": "（递来热茶）", "say": None}, {"text": "（递来热茶）"},
                     {"text": "（递来热茶）", "say": ""}, {"text": "（递来热茶）", "say": "  "}):
            with self.subTest(unit=unit):
                tap = _TopLevelJSONStreamTap()
                events = tap.feed(json.dumps({"utterances": [unit]}, ensure_ascii=False)) + tap.finish()
                self.assertEqual([e for e in events if e["type"] == "voice_unit_end"],
                                 [{"type": "voice_unit_end", "unit_index": 0, "display_text": unit["text"], "has_voice": False}])
                self.assertFalse([e for e in events if e["type"] == "voice_segment"])

    def test_voice_unit_end_arrives_when_say_closes_before_tool_arguments(self):
        tap = _TopLevelJSONStreamTap()
        first = tap.feed('{"utterances":[{"style":"温柔关心","text":"休息一下吧。","say":"無理をしないで。休んでください。')
        self.assertEqual([event for event in first if event["type"] == "voice_unit_end"], [])
        end = tap.feed('"}')
        self.assertEqual([event for event in end if event["type"] == "voice_unit_end"],
                         [{"type": "voice_unit_end", "unit_index": 0, "display_text": "休息一下吧。", "has_voice": True}])
        self.assertFalse(tap._units_stream.complete)
        self.assertEqual(tap.finish(), [], "An unfinished array must not invent another completion")

    def test_retry_and_final_do_not_replay_already_spoken_prefix(self):
        replay = VoiceEventReplay()
        first = {"index": 0, "text": "你好。", "style": "calm"}
        second = {"index": 1, "text": "你好。", "style": "calm"}
        self.assertEqual(replay.accept(first)["index"], 0)
        self.assertIsNone(replay.accept(first))
        replay.begin_stage()
        self.assertIsNone(replay.accept(first))
        self.assertEqual(replay.accept(second)["index"], 1)
        self.assertIsNone(replay.accept(second))

    def test_chunk_boundaries_do_not_change_display_or_audio(self):
        units = [{"style": "calm", "text": "（微笑）回来啦。今天好吗？", "say": "おかえり。今日はどう？"},
                 {"style": "happy", "text": "😊一起喝茶吧。", "say": "お茶にしよう。"}]
        payload = {"emotion": "happy", "utterances": units, "memory_metadata": {}}
        raw = json.dumps(payload, ensure_ascii=True)
        expected_voice = voice_segments(units, default_emotion=payload["emotion"])
        for size in (1, 2, 7, 41, len(raw)):
            with self.subTest(size=size):
                tap = _TopLevelJSONStreamTap()
                events = []
                for start in range(0, len(raw), size):
                    events += tap.feed(raw[start:start + size])
                events += tap.finish()
                self.assertEqual(tap.latest_speech, "".join(unit["text"] for unit in units))
                self.assertEqual([e for e in events if e["type"] == "voice_segment"], expected_voice)
                self.assertEqual([e["unit_index"] for e in events if e["type"] == "voice_unit_end"], [0, 1])
                self.assertEqual([e["text"] for e in events if e["type"] == "speech_unit_text"], [unit["text"] for unit in units])
                self.assertEqual([e["text"] for e in events if e["type"] == "speech_segment"],
                                 ["（微笑）回来啦。", "今天好吗？", "😊一起喝茶吧。"])

    def test_long_say_starts_synthesis_before_string_or_unit_closes(self):
        tap = _TopLevelJSONStreamTap()
        events = tap.feed('{"utterances":[{"style":"calm","text":"第一句。第二句。","say":"一つ目です。二つ目')
        voices = [e for e in events if e["type"] == "voice_segment"]
        self.assertEqual([e["text"] for e in voices], ["一つ目です。"])
        self.assertEqual(voices[0]["style"], "calm")
        self.assertEqual(tap._units_stream.snapshot_units(), [{"text": "第一句。第二句。", "say": "一つ目です。", "style": "calm"}])
        self.assertEqual(tap.finish(), [])  # Broken transport doesn't read the unfinished tail.

    def test_display_continues_across_style_groups_without_forced_message(self):
        tap = _TopLevelJSONStreamTap()
        raw = '{"utterances":[{"text":"今天","say":null},{"text":"很开心。","say":null}]}'
        events = tap.feed(raw) + tap.finish()
        self.assertEqual([e["text"] for e in events if e["type"] == "speech_segment"], ["今天很开心。"])
        self.assertFalse([e for e in events if e["type"] == "voice_segment"])
        self.assertFalse([e for e in events if e["type"] == "speech_warning"])

    def test_nested_tool_json_is_not_speech(self):
        tap = _TopLevelJSONStreamTap()
        events = tap.feed(json.dumps({"tool_call": {"utterances": [{"text": "假正文", "say": "不朗读"}]}}))
        self.assertEqual(events + tap.finish(), [])

    def test_missing_say_never_reads_stage_directions(self):
        payload = project_speech_units({"utterances": [{"text": "（拿起杯子）"}], "speech": "伪造正文"})
        self.assertEqual(payload["speech"], "（拿起杯子）")
        self.assertEqual(voice_segments(payload["utterances"]), [])

    def test_wrong_field_order_is_buffered_until_unit_complete(self):
        tap = _TopLevelJSONStreamTap()
        events = tap.feed('{"utterances":[{"say":"こんにちは。","style":"calm",')
        self.assertFalse([e for e in events if e["type"] == "voice_segment"])
        events += tap.feed('"text":"你好。"}]}') + tap.finish()
        self.assertEqual([e["text"] for e in events if e["type"] == "voice_segment"], ["こんにちは。"])

    def test_one_say_is_many_audio_requests_and_style_does_not_inherit(self):
        events = voice_segments([{"text": "中文", "say": "一。二？三！", "style": "calm"},
                                 {"text": "继续", "say": "四。"}])
        self.assertEqual([e["text"] for e in events], ["一。", "二？", "三！", "四。"])
        self.assertEqual([e["style"] for e in events], ["calm", "calm", "calm", ""])


class SpeechPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "cecilia").mkdir()
        self.path = root / "cecilia" / "character.json"
        self.path.write_text(json.dumps({"speech_output": {"enabled": True, "language": "日语",
            "default_style": "calm", "styles": {"calm": {"profile_id": "cecilia_calm", "description": "平静"},
                                                 "happy": {"profile_id": "cecilia_happy"}}}}), encoding="utf-8")
        self.engine = SimpleNamespace(desktop_pet_character_resources=SimpleNamespace(characters_dir=root))

    def test_only_opted_in_clients_get_new_prompt(self):
        context = ClientProtocolContext(effective_mode=ClientMode.DESKTOP_PET, capabilities=("speech_segments",))
        profile = PromptProfileRegistry().resolve(context)
        self.assertIs(configure_speech_prompt(self.engine, "cecilia", context, profile), profile)
        context.capabilities += ("speech_units_v1",)
        updated = configure_speech_prompt(self.engine, "cecilia", context, profile)
        self.assertNotIn("speech 是唯一", updated.system_prompt_override)
        self.assertIn("日语", updated.fast_mode_prompt)
        self.assertIn("一组不等于一次合成", updated.fast_mode_prompt)
        self.assertIn("第一组省略则用顶层，后续省略则延续上一组", updated.fast_mode_prompt)
        self.assertIn("换表情或语气再分组", updated.fast_mode_prompt)
        self.assertNotIn("cecilia_calm", updated.fast_mode_prompt)
        self.assertNotIn(self.temp.name, updated.fast_mode_prompt)

    def test_style_selects_existing_profile_and_unknown_style_has_visible_fallback(self):
        payload = {"speech_protocol": "speech_units_v1", "character_pack_id": "cecilia", "voice_style": "happy"}
        result = resolve_tts_runtime_provider(engine=self.engine, payload=payload)
        self.assertEqual(result["voiceProfileId"], "cecilia_happy")
        result = resolve_tts_runtime_provider(engine=self.engine, payload={**payload, "voice_style": "invented"})
        self.assertEqual(result["voiceProfileId"], "cecilia_calm")
        self.assertTrue(result["voiceStyleFallback"])

    def test_style_prompt_and_reference_mapping_reload_without_recreating_engine(self):
        context = ClientProtocolContext(effective_mode=ClientMode.QQ_TEXT, capabilities=("speech_units_v1",))
        profile = PromptProfileRegistry().resolve(context)
        first = configure_speech_prompt(self.engine, "cecilia", context, profile)
        self.assertNotIn("shy", first.fast_mode_prompt)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["speech_output"]["styles"]["shy"] = {"profile_id": "cecilia_shy", "description": "害羞迟疑"}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self.path)
        updated = configure_speech_prompt(self.engine, "cecilia", context, profile)
        self.assertIn("shy（害羞迟疑）", updated.fast_mode_prompt)
        self.assertNotIn("cecilia_shy", updated.fast_mode_prompt)
        result = resolve_tts_runtime_provider(engine=self.engine, payload={"speech_protocol": "speech_units_v1",
            "character_pack_id": "cecilia", "voice_style": "shy"})
        self.assertEqual(result["voiceProfileId"], "cecilia_shy")
        data["speech_output"]["enabled"] = False
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self.path)
        self.assertIs(configure_speech_prompt(self.engine, "cecilia", context, profile), profile)

    def test_new_qq_prompt_retains_identity_and_conversation_rules(self):
        context = ClientProtocolContext(effective_mode=ClientMode.QQ_TEXT, capabilities=("speech_units_v1",))
        profile = PromptProfileRegistry().resolve(context)
        updated = configure_speech_prompt(self.engine, "cecilia", context, profile)
        self.assertIn("actor_relation=owner", updated.system_prompt_override)
        self.assertIn("群聊结合发送者、目标、@ 和引用", updated.system_prompt_override)
        self.assertIn("text 是发送到 QQ 的消息正文", updated.system_prompt_override)
        self.assertNotIn("speech 是发送到 QQ", updated.system_prompt_override)

    def test_attention_guidance_does_not_override_character_speech_format(self):
        from companion_v01.channels.qq.turns.attention import GROUP_ATTENTION_REVIEW_CONTEXT
        context = ClientProtocolContext(effective_mode=ClientMode.QQ_TEXT, capabilities=("speech_units_v1",))
        profile = configure_speech_prompt(self.engine, "cecilia", context, PromptProfileRegistry().resolve(context))
        combined = profile.fast_mode_prompt + "\n" + GROUP_ATTENTION_REVIEW_CONTEXT
        self.assertIn("utterances: []", combined)
        self.assertIn("say 用日语", combined)
        self.assertNotIn('{"speech":""}', combined)
        self.assertNotIn("utterances", GROUP_ATTENTION_REVIEW_CONTEXT,
                         "Attention decides participation; the active profile owns the output schema")

    def test_missing_policy_and_path_escape_fail_closed(self):
        self.assertEqual(character_speech_policy(self.engine, "../cecilia"), {})
        result = resolve_tts_runtime_provider(engine=self.engine,
            payload={"speech_protocol": "speech_units_v1", "character_pack_id": "missing"})
        self.assertEqual(result["status"], "unavailable")
        result = resolve_tts_runtime_provider(engine=self.engine,
            payload={"speech_protocol": "speech_units_v1", "character_pack_id": "cecilia", "voiceProvider": "text_only"})
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["reason"], "text_only_requested")

    def test_qq_synthesizes_sentences_with_their_style_then_builds_one_wav(self):
        data = io.BytesIO()
        with wave.open(data, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(32000)
            audio.writeframes(b"\x00\x00" * 320)
        sample = SynthesizedTTSResult(audio=data.getvalue(), media_type="audio/wav", provider_id="gpt",
                                     voice_profile_id="test", emotion="", emotion_voice_id="", profile_fingerprint="fp")
        units = [{"emotion": "shy", "text": "你好。回来啦。", "say": "こんにちは。おかえり。", "style": "calm"},
                 {"emotion": "normal", "text": "好开心。", "say": "うれしい。", "style": "happy"}]
        with patch("companion_v01.tts_provider_runtime.synthesize_tts_resolution", new_callable=AsyncMock, return_value=sample) as synth:
            result = asyncio.run(synthesize_speech_units(engine=self.engine, units=units, payload={"character_pack_id": "cecilia"}))
        self.assertEqual([call.kwargs["text"] for call in synth.call_args_list], ["こんにちは。", "おかえり。", "うれしい。"])
        self.assertEqual([call.kwargs["resolution"]["voiceProfileId"] for call in synth.call_args_list],
                         ["cecilia_calm", "cecilia_calm", "cecilia_happy"])
        with wave.open(io.BytesIO(result.audio), "rb") as audio:
            self.assertEqual(audio.getnframes(), 960)
        with patch("companion_v01.tts_provider_runtime.synthesize_tts_resolution", new_callable=AsyncMock,
                   side_effect=[sample, TTSServiceError("provider_failed")]):
            with self.assertRaisesRegex(TTSServiceError, "provider_failed"):
                asyncio.run(synthesize_speech_units(engine=self.engine, units=units, payload={"character_pack_id": "cecilia"}))


if __name__ == "__main__":
    unittest.main()
