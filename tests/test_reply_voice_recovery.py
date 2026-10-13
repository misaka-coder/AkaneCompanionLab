"""Voice survives the final-reply repair paths on QQ text, streaming and sync.

The QQ voice bridge only synthesizes what the shipped protocol put in
``utterances``: one reply that keeps the client's own fields delivers text,
voice and emotion from the same frame, while a frame reduced to display text
can only deliver text. These tests pin that correspondence across the normal
reply, the after-tool tail, the repair path, and a turn whose reply was already
partly delivered. Standard library and existing fixtures only, so they run
without the TTS plugin install.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from companion_v01.channels.qq.delivery.replies import _send_qq_delivery
from companion_v01.channels.qq.delivery.stream import _filter_unsent_reply_messages
from companion_v01.client_protocol import ClientMode, ClientProtocolContext, default_capabilities_for_mode
from companion_v01.engine import FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK, AkaneMemoryEngine
from companion_v01.llm_runtime import ChatJSONResult, ChatTextResult
from companion_v01.model_runtime.json_stream import _TopLevelJSONStreamTap
from companion_v01.output_adapters import OutputAdapterRegistry
from companion_v01.speech_units import VoiceEventReplay, project_speech_units, voice_segments

UNITS = [
    {"emotion": "happy", "style": "明るく元気に", "text": "查完了，结论是这样。", "say": "調べ終わりました。結論はこうです。"},
    {"style": "落ち着いて", "text": "报告放在工作区里了。", "say": "レポートは作業ディレクトリに置きました。"},
]
TAIL_UNITS = [{"style": "落ち着いて", "text": "剩下的部分也写好了。", "say": "残りの部分も書き終えました。"}]


def _drain(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


def _default_context(**overrides) -> dict:
    context = {
        "system_prompt": "full system prompt",
        "user_prompt": "full projected user prompt",
        "fallback": {"speech": "", "tool_call": None},
        "visual_defaults": {
            "emotion": "normal",
            "outfit": "default",
            "major": "日常",
            "minor": "房间",
            "background": "room",
            "bgm": "",
        },
        "debug_enabled": False,
        "allow_tool_call": True,
        "native_tools": [{"type": "function", "function": {"name": "exec_run"}}],
        "native_tool_choice": "auto",
        "system_extra_blocks": ["large context"],
        "history_turns": [{"role": "user", "content": "large history"}],
        "ephemeral_turns": [],
        "post_user_turns": [{"role": "tool", "content": "tool result"}],
        "prompt_audit_sections": [],
        "prompt_scope": "",
    }
    context.update(overrides)
    return context


def units_json(units, *, emotion="happy") -> str:
    return json.dumps({"emotion": emotion, "utterances": units, "memory_metadata": {}}, ensure_ascii=False)


def reply_frame(units=None, *, emotion="happy") -> dict:
    """Frame shape the real pipeline produces for one protocol-shaped reply."""
    return {
        "emotion": emotion,
        "utterances": [dict(unit) for unit in (units if units is not None else UNITS)],
        "speech": "".join(unit["text"] for unit in (units if units is not None else UNITS)),
    }


def streamed_reply(units, *, error="", raw=None):
    wire = raw if raw is not None else units_json(units)
    tap = _TopLevelJSONStreamTap()
    events = tap.feed(wire) + tap.finish()
    parsed = project_speech_units({"emotion": "happy", "utterances": tap._units_stream.snapshot_units()})
    return {"parsed": parsed, "raw_text": wire, "error": error, "stream_events": events}


def recovered_reply():
    llm = FakeLLM([streamed_reply([UNITS[0]], error="connection reset"), streamed_reply(TAIL_UNITS)])
    events, frame = run_stream(build_engine(llm))
    return llm, events, frame


class FakeLLM:
    """Answers a scripted sequence of raw wire replies on either transport."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.text_calls = 0
        self.metrics: list[str] = []

    @staticmethod
    def snapshot_metrics() -> dict:
        return {}

    def record_metric(self, name: str) -> None:
        self.metrics.append(name)

    def _next(self, kwargs):
        self.calls.append(dict(kwargs))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        allowed = {field.name for field in fields(ChatJSONResult)}
        return ChatJSONResult(**{key: value for key, value in reply.items() if key in allowed})

    def call_chat_json_result(self, **kwargs):
        return self._next(kwargs)

    def stream_chat_json(self, **kwargs):
        reply = self.replies[min(len(self.calls), len(self.replies) - 1)]
        for event in reply.get("stream_events", ()):
            yield event
        yield from ()
        result = self._next(kwargs)
        # A stream attempt returns the same result object the sync path returns.
        return result

    def call_chat_text(self, **_kwargs):
        self.text_calls += 1
        return ChatTextResult(text="", raw_text="")


def build_engine(llm, *, context=None, mode=ClientMode.QQ_TEXT) -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.llm = llm
    engine.resource_manifest = None
    engine._prepare_final_response_context = lambda **_kwargs: dict(context or _default_context())
    engine._build_memcore_request_observer = lambda **_kwargs: None
    engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "塞西莉亚"}
    engine._attach_memory_annotation_truth = lambda *_args, **_kwargs: None
    engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
    engine._resolve_client_protocol_context = lambda _payload: ClientProtocolContext(
        requested_mode=mode,
        effective_mode=mode,
        capabilities=(*default_capabilities_for_mode(mode), "speech_units_v1"),
    )
    engine._get_persona_card_service = lambda: None
    engine._get_user_runtime_projection = lambda _profile_user_id: {
        "extra_bgm_tracks": [],
        "extra_scene_groups": [],
        "extra_character_outfits": [],
    }
    engine._get_output_adapter_registry = lambda: OutputAdapterRegistry()
    engine._normalize_final_output = AkaneMemoryEngine._normalize_final_output.__get__(
        engine, AkaneMemoryEngine
    )
    return engine


def run_stream(engine, **overrides):
    return _drain(
        engine._stream_final_response(
            session_id="qq_group_shared_87",
            profile_user_id="qq_group_shared_87",
            user_message="看看仓库",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
            **overrides,
        )
    )


def run_nonstream(engine):
    return engine._build_final_response(
        session_id="qq_group_shared_87",
        profile_user_id="qq_group_shared_87",
        user_message="看看仓库",
        recent_raw=[],
        recent_episodic_summaries=[],
        recent_semantic_summaries=[],
        confirmed_snippets=[],
        now_ts=0,
    )


class DamagedJsonClassificationTests(unittest.TestCase):
    """A damaged document must not be reported as prose around a JSON object."""

    def test_unclosed_document_is_not_external_text(self):
        for raw in (
            '{"emotion":"happy","utterances":[{"text":"a","say":"b"},{"text":"c","say":"d"}]',
            '{"emotion":"happy","utterances":[{"text":"a","say":"b"},{"text":"c","s',
            '{"emotion":"normal","speech":"仓库看完了。","tool_call":{"type":"pending"',
        ):
            with self.subTest(raw=raw):
                self.assertFalse(AkaneMemoryEngine._raw_json_has_external_text(raw))

    def test_plain_object_and_fence_and_real_prose_keep_their_meaning(self):
        self.assertFalse(AkaneMemoryEngine._raw_json_has_external_text(units_json(UNITS)))
        self.assertFalse(
            AkaneMemoryEngine._raw_json_has_external_text("```json\n" + units_json(UNITS) + "\n```")
        )

    def test_large_damaged_object_decodes_only_at_the_root_boundary(self):
        raw = '{"utterances":[' + ','.join(units_json(UNITS) for _ in range(300))
        decoder = json.JSONDecoder.raw_decode
        with patch.object(json.JSONDecoder, "raw_decode", autospec=True, side_effect=decoder) as decode:
            self.assertFalse(AkaneMemoryEngine._raw_json_has_external_text(raw))
        self.assertEqual(decode.call_count, 2, "one full parse and one root candidate; no inner-object scans")

    def test_tool_examples_and_nested_fields_are_not_pending_calls(self):
        for example in (
            "工具示例：{'tool_call': {'type': 'web_search'}}。",
            '字段示例："tool_call": {"type":"web_search"}。',
            '这里有转义符 \\ 和括号 { [ ] }。',
        ):
            payload = {"emotion": "happy", "utterances": [{**UNITS[0], "text": example}],
                       "memory_metadata": {"tool_call": {"example": True}}}
            wire = json.dumps(payload, ensure_ascii=False)
            for runner in (run_nonstream, lambda e: run_stream(e)[1]):
                with self.subTest(example=example, runner=runner):
                    llm = FakeLLM([{"parsed": payload, "raw_text": wire}])
                    frame = runner(build_engine(llm))
                    self.assertEqual(len(llm.calls), 1)
                    self.assertEqual(llm.text_calls, 0)
                    self.assertNotIn("_transient_final_failure", frame)
                    self.assertEqual(frame["utterances"][0]["text"], example)

    def test_truncated_tool_detection_reads_only_root_keys(self):
        for wire, pending in (
            ('{"speech":"说明：\\\"tool_call\\\":{示例}","broken":', False),
            ('{"memory_metadata":{"tool_call":{}},"utterances":[', False),
            ('{"tool_call":null,"utterances":[', False),
            ('{"memory_metadata":{"tool_call":null},"tool_call":{', True),
            ('{"tool_call":{', True),
            ('{"tool_call":', True),
            ('{"tool_\\u0063all":{', True),
            ('```json\n{"tool_call":{', True),
        ):
            with self.subTest(wire=wire):
                self.assertEqual(AkaneMemoryEngine._raw_tool_call_is_pending(wire), pending)
        self.assertTrue(
            AkaneMemoryEngine._raw_json_has_external_text(
                "验收结论如下。\n" + json.dumps({"emotion": "happy", "speech": "好的"}, ensure_ascii=False)
            )
        )
        self.assertTrue(
            AkaneMemoryEngine._raw_json_has_external_text(
                json.dumps({"emotion": "happy", "speech": "好的"}, ensure_ascii=False) + "\n另外补一句。"
            )
        )


class RepairKeepsTheClientContractTests(unittest.TestCase):
    """The repair turn must not trade the client's fields away."""

    def test_external_text_retry_asks_for_the_client_fields_not_only_speech(self):
        report = "项目路径：/work/plugin；测试 42/42；端口 8899。"
        llm = FakeLLM([
            {"parsed": {"speech": "验收通过。"}, "raw_text": report + "\n" + units_json(UNITS)},
            {"parsed": {"emotion": "happy", "utterances": [dict(unit) for unit in UNITS]},
             "raw_text": units_json(UNITS)},
        ])
        frame = run_nonstream(build_engine(llm))

        self.assertEqual(len(llm.calls), 2, "one same-context repair turn")
        retry_prompt = llm.calls[1]["user_prompt"]
        self.assertTrue(retry_prompt.endswith(FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK))
        self.assertIn("沿用当前客户端的字段说明", retry_prompt)
        self.assertNotIn("把所有要发给用户的文字都放进 speech", retry_prompt)

        self.assertEqual(frame["emotion"], "happy")
        self.assertEqual([unit["say"] for unit in frame["utterances"]], [unit["say"] for unit in UNITS])
        self.assertEqual([unit["style"] for unit in frame["utterances"]], [unit["style"] for unit in UNITS])
        self.assertEqual(frame["speech"], "".join(unit["text"] for unit in UNITS))
        segments = voice_segments(frame["utterances"], default_emotion=frame["emotion"])
        self.assertTrue(segments)
        # Every sentence of every say is spoken, in order, with its own display
        # text and style attached; nothing is dropped or invented.
        self.assertEqual("".join(segment["text"] for segment in segments),
                         "".join(unit["say"] for unit in UNITS))
        self.assertEqual([segment["display_text"] for segment in segments],
                         [UNITS[0]["text"], UNITS[0]["text"], UNITS[1]["text"]])
        self.assertEqual([segment["style"] for segment in segments],
                         [UNITS[0]["style"], UNITS[0]["style"], UNITS[1]["style"]])
        self.assertEqual(segments[0]["emotion"], "happy")

    def test_damaged_units_reply_is_delivered_once_with_its_voice(self):
        raw = '{"emotion":"happy","utterances":[' + json.dumps(UNITS[0], ensure_ascii=False) + ',{"text":"报告放在工作区里了。","s'
        llm = FakeLLM([streamed_reply(UNITS, raw=raw)])
        for label, runner in (("nonstream", run_nonstream), ("stream", lambda e: run_stream(e)[1])):
            with self.subTest(transport=label):
                llm.calls.clear()
                frame = runner(build_engine(llm))
                self.assertEqual(len(llm.calls), 1, "no repair turn for a damaged but usable reply")
                self.assertEqual([unit["say"] for unit in frame["utterances"]], [UNITS[0]["say"], None])
                self.assertGreaterEqual(len(voice_segments(frame["utterances"])), 1)


class ToolTailAndPartialDeliveryTests(unittest.TestCase):
    """After tool work and after a partly delivered reply, voice still matches."""

    def test_after_tool_tail_reply_keeps_its_speech_units(self):
        llm = FakeLLM([
            {"parsed": {"tool_call": None}, "raw_text": '{"speech":"工具都做完了。","tool_call":{"type":"pending"'},
            {"parsed": {"emotion": "happy", "utterances": [dict(unit) for unit in UNITS]},
             "raw_text": units_json(UNITS)},
        ])
        context = _default_context(
            post_user_turns=[
                {"role": "tool", "content": "exec_run finished"},
                {"role": "user", "content": "工具结果已返回。"},
            ],
        )
        frame = run_nonstream(build_engine(llm, context=context))

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual([unit["say"] for unit in frame["utterances"]], [unit["say"] for unit in UNITS])
        self.assertEqual(frame["emotion"], "happy")
        self.assertNotIn("_transient_final_failure", frame)

    def test_partly_delivered_reply_keeps_the_tail_voice_and_does_not_repeat_text(self):
        llm = FakeLLM([
            {"parsed": {"speech": "查完了，结论是这样。"}, "raw_text": '{"speech":"查完了，结论是这样。"',
             "error": "connection reset", "stream_events": [
                 {"type": "speech_chunk", "text": "查完了，结论是这样。"},
                 {"type": "speech_segment", "index": 0, "text": "查完了，结论是这样。"},
             ]},
            {"parsed": {"emotion": "happy", "utterances": [dict(unit) for unit in TAIL_UNITS]},
             "raw_text": units_json(TAIL_UNITS)},
        ])
        events, frame = run_stream(build_engine(llm))

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("已经提交到本轮交付通道", llm.calls[1]["user_prompt"])
        self.assertEqual([unit["say"] for unit in frame["utterances"]],
                         [None, *[unit["say"] for unit in TAIL_UNITS]])
        self.assertTrue(frame["speech"].startswith("查完了，结论是这样。"))
        # The already delivered display sentence stays in the canonical body once.
        self.assertEqual(frame["speech"].count("查完了，结论是这样。"), 1)

        reply_messages = list(frame["speech_segments"])
        unsent = _filter_unsent_reply_messages(reply_messages, ["查完了，结论是这样。"])
        self.assertNotIn("查完了，结论是这样。", unsent)
        self.assertTrue(unsent)

    def test_recovery_retains_the_entire_voice_and_freezes_request_prefix(self):
        llm, events, frame = recovered_reply()
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(frame["utterances"], [UNITS[0], {**TAIL_UNITS[0], "emotion": "happy"}])
        self.assertEqual("".join(v["text"] for v in voice_segments(frame["utterances"])),
                         UNITS[0]["say"] + TAIL_UNITS[0]["say"])
        self.assertEqual(frame["speech"].count(UNITS[0]["text"]), 1)
        for key in llm.calls[0].keys() - {"user_prompt", "early_tool_call_validator"}:
            self.assertEqual(llm.calls[0][key], llm.calls[1][key], key)
        self.assertTrue(llm.calls[1]["user_prompt"].startswith(llm.calls[0]["user_prompt"]))
        self.assertEqual([e["text"] for e in events if e["type"] == "speech_segment"],
                         [UNITS[0]["text"], TAIL_UNITS[0]["text"]])

    def test_retry_that_replays_prefix_does_not_duplicate_voice_or_text(self):
        llm = FakeLLM([streamed_reply([UNITS[0]], error="connection reset"),
                       streamed_reply([UNITS[0], *TAIL_UNITS])])
        _events, frame = run_stream(build_engine(llm))
        self.assertEqual([u["text"] for u in frame["utterances"]], [UNITS[0]["text"], TAIL_UNITS[0]["text"]])
        self.assertEqual([u["say"] for u in frame["utterances"]], [UNITS[0]["say"], TAIL_UNITS[0]["say"]])

    def test_multiple_failures_retain_legacy_text_and_each_committed_voice(self):
        llm = FakeLLM([
            {"parsed": {"speech": "开头的文字。"}, "raw_text": '{"speech":"开头的文字。"',
             "error": "connection reset", "stream_events": [{"type": "speech_segment", "text": "开头的文字。"}]},
            streamed_reply([UNITS[0]], error="connection reset"),
            streamed_reply(TAIL_UNITS),
        ])
        _events, frame = run_stream(build_engine(llm), user_images=[{"url": "https://example.test/image.png"}])
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual([u["say"] for u in frame["utterances"]], [None, UNITS[0]["say"], TAIL_UNITS[0]["say"]])
        self.assertEqual(frame["speech"].count("开头的文字。"), 1)
        self.assertEqual(llm.calls[0]["user_images"], llm.calls[2]["user_images"])

    def test_plain_text_recovery_keeps_available_voice_without_translating_new_text(self):
        llm = FakeLLM([streamed_reply([UNITS[0]], error="connection reset")])
        llm.call_chat_text = lambda **_kwargs: ChatTextResult(text="补充说明。", raw_text="补充说明。")
        _events, frame = run_stream(build_engine(llm))
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual([u["say"] for u in frame["utterances"]], [UNITS[0]["say"], None])
        self.assertEqual(frame["utterances"][-1]["text"], "补充说明。")

    def test_interrupted_say_keeps_committed_audio_without_inventing_its_tail(self):
        raw = '{"utterances":[{"style":"calm","text":"先说这句。","say":"こんにちは。まだ途中'
        llm = FakeLLM([streamed_reply([], raw=raw, error="connection reset"), streamed_reply(TAIL_UNITS)])
        _events, frame = run_stream(build_engine(llm))
        self.assertEqual(frame["utterances"][0]["say"], "こんにちは。")
        self.assertNotIn("まだ途中", "".join(v["text"] for v in voice_segments(frame["utterances"])))

    def test_final_voice_replay_matches_across_attempts_and_preserves_repetition(self):
        replay = VoiceEventReplay()
        preface = voice_segments([{"text": "准备。", "say": "準備します。", "style": "calm"}])
        original = voice_segments([UNITS[0]])
        tail = voice_segments(TAIL_UNITS)
        for stage in (preface, original, tail):
            replay.begin_stage()
            for event in stage:
                self.assertIsNotNone(replay.accept(event))
        final = voice_segments([UNITS[0], *TAIL_UNITS, TAIL_UNITS[0]])
        replay.begin_final(final)
        remaining = [accepted for event in final if (accepted := replay.accept(event)) is not None]
        self.assertEqual([event["text"] for event in remaining], [TAIL_UNITS[0]["say"]])


class QQDeliveryCorrespondenceTests(unittest.TestCase):
    """Text, voice and emotion come from the same frame, once."""

    class Gateway:
        def __init__(self):
            self.text_sends: list[list[str]] = []
            self.voice_sends: list[str] = []
            self.emotion_sends: list[str] = []

        def resolve_reply_mode(self, _session_id: str) -> str:
            return "auto"

        def send_replies(self, _context, messages):
            clean = [str(item).strip() for item in messages if str(item).strip()]
            self.text_sends.append(clean)
            return {"ok": bool(clean), "count": len(clean),
                    "results": [{"ok": True, "message": item} for item in clean]}

        def send_voice(self, _context, *, audio_path: str, name: str = ""):
            self.voice_sends.append(audio_path)
            return {"ok": Path(audio_path).exists(), "file": audio_path}

        def send_reply(self, _context, message: str):
            clean = str(message or "").strip()
            self.text_sends.append([clean] if clean else [])
            return {"ok": bool(clean), "message": clean}

    def delivery(self, frame, *, mode="both", gateway=None, recorded=None, context=None):
        gateway = gateway or self.Gateway()
        messages = [unit["text"] for unit in frame.get("utterances") or []] or [frame["speech"]]
        patcher = (
            patch("companion_v01.channels.qq.turns.reply_audio._synthesize_qq_voice_file", side_effect=recorded)
            if recorded is not None else patch("companion_v01.channels.qq.turns.reply_audio._synthesize_qq_voice_file")
        )
        with patcher as synth:
            result = _send_qq_delivery(
                engine=SimpleNamespace(),
                qq_gateway=gateway,
                context=context or SimpleNamespace(profile_user_id="qq_group_shared_87",
                                                   session_id="qq-session",
                                                   character_pack_id="cecilia", reply_mode=mode),
                frame=frame,
                reply_messages=messages,
                unsent_reply_messages=messages,
                streamed_messages=[],
                config_module=SimpleNamespace(),
                settings=None,
            )
        return result, gateway, synth

    def test_normal_reply_sends_one_text_and_one_voice_from_the_same_units(self):
        with tempfile.TemporaryDirectory() as directory:
            calls: list[dict] = []
            audio = Path(directory) / "reply.wav"
            audio.write_bytes(b"RIFF-wav")

            def synthesize(**kwargs):
                calls.append(kwargs)
                return {"ok": True, "path": str(audio), "media_type": "audio/wav",
                        "provider": "gpt_sovits", "emotion": kwargs.get("emotion", ""),
                        "tts_profile_user_id": "master"}

            frame = reply_frame()
            result, gateway, _ = self.delivery(frame, recorded=synthesize)

        self.assertTrue(result["ok"], result)
        self.assertEqual(gateway.text_sends, [[unit["text"] for unit in UNITS]])
        self.assertEqual(len(gateway.voice_sends), 1)
        self.assertEqual(result["delivery"]["voice_reason"], "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["utterances"], frame["utterances"])
        self.assertEqual(calls[0]["emotion"], "happy")

    def test_after_tool_tail_frame_ships_text_voice_and_emotion_once(self):
        llm = FakeLLM([
            {"parsed": {"speech": "工具都做完了。"}, "raw_text": '{"speech":"工具都做完了。","tool_call":{"type":"pending"'},
            {"parsed": {"emotion": "happy", "utterances": [dict(unit) for unit in UNITS]},
             "raw_text": units_json(UNITS)},
        ])
        frame = run_nonstream(build_engine(llm, context=_default_context(post_user_turns=[
            {"role": "tool", "content": "exec_run finished"},
        ])))
        with tempfile.TemporaryDirectory() as directory:
            calls: list[dict] = []
            audio = Path(directory) / "reply.wav"
            audio.write_bytes(b"RIFF-wav")

            def synthesize(**kwargs):
                calls.append(kwargs)
                return {"ok": True, "path": str(audio), "media_type": "audio/wav", "provider": "gpt_sovits",
                        "emotion": kwargs.get("emotion", ""), "tts_profile_user_id": "master"}

            result, gateway, _ = self.delivery(frame, recorded=synthesize)

        self.assertTrue(result["ok"], result)
        self.assertEqual(gateway.text_sends, [[unit["text"] for unit in UNITS]])
        self.assertEqual(len(gateway.voice_sends), 1)
        self.assertEqual([unit["say"] for unit in calls[0]["utterances"]], [unit["say"] for unit in UNITS])
        self.assertEqual(calls[0]["emotion"], frame["emotion"])

    def test_partly_delivered_reply_sends_only_tail_text_but_the_whole_voice(self):
        _llm, _events, frame = recovered_reply()
        tail_messages = [unit["text"] for unit in TAIL_UNITS]
        with tempfile.TemporaryDirectory() as directory:
            calls: list[dict] = []
            audio = Path(directory) / "reply.wav"
            audio.write_bytes(b"RIFF-wav")

            def synthesize(**kwargs):
                calls.append(kwargs)
                return {"ok": True, "path": str(audio), "media_type": "audio/wav", "provider": "gpt_sovits",
                        "emotion": kwargs.get("emotion", ""), "tts_profile_user_id": "master"}

            gateway = self.Gateway()
            messages = [unit["text"] for unit in TAIL_UNITS]
            with patch("companion_v01.channels.qq.turns.reply_audio._synthesize_qq_voice_file", side_effect=synthesize):
                result = _send_qq_delivery(
                    engine=SimpleNamespace(),
                    qq_gateway=gateway,
                    context=SimpleNamespace(profile_user_id="qq_group_shared_87", session_id="qq-session",
                                            character_pack_id="cecilia", reply_mode="both"),
                    frame=frame,
                    reply_messages=list(frame["speech_segments"]),
                    unsent_reply_messages=tail_messages,
                    streamed_messages=["查完了，结论是这样。"],
                    config_module=SimpleNamespace(),
                    settings=None,
                )

        self.assertTrue(result["ok"], result)
        self.assertEqual(gateway.text_sends, [tail_messages])
        self.assertEqual(len(gateway.voice_sends), 1)
        self.assertEqual([unit["say"] for unit in calls[0]["utterances"]],
                         [UNITS[0]["say"], TAIL_UNITS[0]["say"]])

    def test_frame_without_the_client_fields_reports_the_real_reason_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cecilia").mkdir()
            (root / "cecilia/character.json").write_text(json.dumps({"speech_output": {
                "enabled": True, "language": "日语", "default_style": "calm",
                "styles": {"calm": {"profile_id": "cecilia_calm"}}}}), encoding="utf-8")
            engine = SimpleNamespace(desktop_pet_character_resources=SimpleNamespace(characters_dir=root))
            for mode in ("voice", "both"):
                with self.subTest(mode=mode), patch(
                    "companion_v01.channels.qq.turns.reply_audio.synthesize_tts_resolution", new_callable=AsyncMock
                ) as synth:
                    gateway = self.Gateway()
                    result = _send_qq_delivery(
                        engine=engine,
                        qq_gateway=gateway,
                        context=SimpleNamespace(profile_user_id="master", session_id="group",
                                                character_pack_id="cecilia", reply_mode=mode),
                        frame={"emotion": "happy", "speech": "这次只送出了文字。"},
                        reply_messages=["这次只送出了文字。"],
                        unsent_reply_messages=["这次只送出了文字。"],
                        streamed_messages=[],
                        config_module=SimpleNamespace(DATA_DIR=root),
                        settings=None,
                    )
                    synth.assert_not_awaited()
                    self.assertEqual(gateway.text_sends, [["这次只送出了文字。"]])
                    self.assertEqual(gateway.voice_sends, [])
                    self.assertEqual(result["delivery"]["voice_reason"], "speech_units_required")
                    self.assertEqual(result["voice_result"]["count"], 0)
                    self.assertFalse(result["voice_result"]["ok"])


if __name__ == "__main__":
    unittest.main()
