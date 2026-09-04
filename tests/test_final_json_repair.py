from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from companion_v01.client_protocol import ClientMode, ClientProtocolContext, default_capabilities_for_mode
from companion_v01.engine import (
    FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK,
    FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK,
    FINAL_RESPONSE_RECOVERY_FEEDBACK,
    AkaneMemoryEngine,
)
from companion_v01.llm_runtime import ChatJSONResult, ChatTextResult
from companion_v01.output_adapters import OutputAdapterRegistry


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
        "native_tools": [
            {"type": "function", "function": {"name": f"tool_{index}"}} for index in range(33)
        ],
        "native_tool_choice": "auto",
        "system_extra_blocks": ["large context"],
        "history_turns": [{"role": "user", "content": "large history"}],
        "ephemeral_turns": [],
        "post_user_turns": [{"role": "tool", "content": "research result"}],
        "prompt_audit_sections": [],
        "prompt_scope": "",
    }
    context.update(overrides)
    return context


class FinalRecoveryTests(unittest.TestCase):
    def test_external_report_before_json_retries_without_losing_report(self) -> None:
        complete_report = "项目路径：/work/plugin；测试 7/7；安装 active；真实调用返回 22。"

        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": "验收通过。", "tool_call": None},
                        raw_text=(
                            complete_report
                            + '\n{"emotion":"happy","speech":"验收通过。","tool_call":null}'
                        ),
                    )
                return ChatJSONResult(
                    parsed={"emotion": "happy", "speech": complete_report, "tool_call": None},
                    raw_text=json.dumps(
                        {"emotion": "happy", "speech": complete_report, "tool_call": None},
                        ensure_ascii=False,
                    ),
                )

        llm = FakeLLM()
        result = self._run_nonstream(self._engine(llm))

        self.assertEqual(result["speech"], complete_report)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[0]["system_prompt"], llm.calls[1]["system_prompt"])
        self.assertEqual(llm.calls[0]["prompt_cache_key"], llm.calls[1]["prompt_cache_key"])
        self.assertEqual(
            llm.calls[0]["user_prompt"] + "\n\n" + FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK,
            llm.calls[1]["user_prompt"],
        )

    def test_json_fence_is_not_treated_as_external_user_text(self) -> None:
        self.assertFalse(
            AkaneMemoryEngine._raw_json_has_external_text(
                '```json\n{"emotion":"normal","speech":"完整回复。"}\n```'
            )
        )

    def test_hard_budget_keeps_native_schema_and_changes_only_tool_choice(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "exec_run",
                    "description": "Run a command.",
                    "parameters": {"type": "object"},
                },
            }
        ]
        context = {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "fallback"},
            "native_tools": tools,
            "native_tool_choice": "auto",
        }

        open_request = engine._build_final_response_request_kwargs(
            generation_context=context,
            request_projection_state=None,
            user_images=None,
            chat_model_override="",
            execution_target=None,
            prompt_cache_key="cache-key",
            retry_note="",
            request_observer=None,
            allow_tool_call=True,
        )
        closed_request = engine._build_final_response_request_kwargs(
            generation_context=context,
            request_projection_state=None,
            user_images=None,
            chat_model_override="",
            execution_target=None,
            prompt_cache_key="cache-key",
            retry_note="",
            request_observer=None,
            allow_tool_call=False,
        )

        self.assertEqual(closed_request["native_tools"], open_request["native_tools"])
        self.assertEqual(closed_request["native_tool_choice"], "none")
        self.assertEqual(open_request["native_tool_choice"], "auto")

    @staticmethod
    def _engine(llm, *, context: dict | None = None) -> AkaneMemoryEngine:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = llm
        engine.resource_manifest = None
        engine._prepare_final_response_context = (
            (lambda **_kwargs: _default_context()) if context is None else (lambda **_kwargs: context)
        )
        engine._build_memcore_request_observer = lambda **_kwargs: None
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda *, result, **_kwargs: dict(result or {})
        engine._attach_memory_annotation_truth = lambda *_args, **_kwargs: None
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
        return engine

    @staticmethod
    def _real_normalize_engine(
        llm,
        *,
        context: dict | None = None,
        client_mode: ClientMode = ClientMode.SCENE_STATIC,
    ) -> AkaneMemoryEngine:
        engine = FinalRecoveryTests._engine(llm, context=context)
        engine._resolve_client_protocol_context = lambda _payload: ClientProtocolContext(
            requested_mode=client_mode,
            effective_mode=client_mode,
            capabilities=default_capabilities_for_mode(client_mode),
        )
        engine._get_persona_card_service = lambda: None
        engine._get_user_runtime_projection = lambda _profile_user_id: {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }
        engine._get_output_adapter_registry = lambda: OutputAdapterRegistry()
        engine._normalize_final_output = AkaneMemoryEngine._normalize_final_output.__get__(
            engine,
            AkaneMemoryEngine,
        )
        return engine

    def _run_stream(self, engine):
        return _drain(
            engine._stream_final_response(
                session_id="qq_group_shared_1",
                profile_user_id="qq_group_shared_1",
                user_message="看看仓库",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
            )
        )

    def _run_nonstream(self, engine):
        return engine._build_final_response(
            session_id="qq_group_shared_1",
            profile_user_id="qq_group_shared_1",
            user_message="看看仓库",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
        )

    def test_explicit_empty_speech_is_a_normal_qq_terminal_decision(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def call_chat_json_result(self, **_kwargs):
                self.calls += 1
                return ChatJSONResult(
                    parsed={"speech": ""},
                    raw_text='{"speech":""}',
                )

        llm = FakeLLM()
        engine = self._real_normalize_engine(
            llm,
            context=_default_context(prompt_scope="qq_attention", post_user_turns=[]),
            client_mode=ClientMode.QQ_TEXT,
        )
        result = engine._build_final_response(
            session_id="qq_group_shared_1",
            profile_user_id="qq_group_shared_1",
            user_message="event.group_attention_review",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
            prompt_scope="qq_attention",
        )

        self.assertEqual(llm.calls, 1)
        self.assertTrue(result["_deliberate_silence"])
        self.assertEqual(result["speech"], "")
        self.assertEqual(result["speech_segments"], [])
        self.assertIsNone(result["tool_call"])
        self.assertNotIn("choices", result)

    def test_malformed_optional_attention_retries_once_and_accepts_standard_reply(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.json_calls = 0
                self.text_calls = 0
                self.requests = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def call_chat_json_result(self, **kwargs):
                self.json_calls += 1
                self.requests.append(kwargs)
                if self.json_calls == 2:
                    return ChatJSONResult(
                        parsed={"speech": "接住了。", "tool_call": None},
                        raw_text='{"speech":"接住了。","tool_call":null}',
                    )
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text='{"speech":"',
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                raise AssertionError("optional attention must not enter plain-text recovery")

        llm = FakeLLM()
        engine = self._engine(
            llm,
            context=_default_context(prompt_scope="qq_attention", post_user_turns=[]),
        )
        result = engine._build_final_response(
            session_id="qq_group_shared_1",
            profile_user_id="qq_group_shared_1",
            user_message="event.group_attention_review",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
            prompt_scope="qq_attention",
        )

        self.assertEqual(llm.json_calls, 2)
        self.assertEqual(llm.text_calls, 0)
        self.assertNotIn("_deliberate_silence", result)
        self.assertEqual(result["speech"], "接住了。")
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(llm.requests[0]["system_prompt"], llm.requests[1]["system_prompt"])
        self.assertEqual(llm.requests[0]["prompt_cache_key"], llm.requests[1]["prompt_cache_key"])
        self.assertEqual(
            llm.requests[0]["user_prompt"] + "\n\n" + FINAL_RESPONSE_RECOVERY_FEEDBACK,
            llm.requests[1]["user_prompt"],
        )

    def test_streaming_malformed_qq_output_retries_and_accepts_empty_speech(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0
                self.text_calls = 0
                self.requests = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def stream_chat_json(self, **kwargs):
                self.stream_calls += 1
                self.requests.append(kwargs)
                if False:
                    yield {}
                if self.stream_calls == 2:
                    return SimpleNamespace(
                        parsed={"speech": ""},
                        raw_text='{"speech":""}',
                        error="",
                        fallback_used=False,
                        latest_emotion="",
                        latest_speech="",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                return SimpleNamespace(
                    parsed={"speech": "", "tool_call": None},
                    raw_text='{"speech":"',
                    error="",
                    fallback_used=True,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                raise AssertionError("optional attention must not enter plain-text recovery")

        llm = FakeLLM()
        engine = self._real_normalize_engine(
            llm,
            context=_default_context(prompt_scope="qq_attention", post_user_turns=[]),
            client_mode=ClientMode.QQ_TEXT,
        )
        _events, result = self._run_stream(engine)

        self.assertEqual(llm.stream_calls, 2)
        self.assertEqual(llm.text_calls, 0)
        self.assertTrue(result["_deliberate_silence"])
        self.assertEqual(result["speech"], "")
        self.assertEqual(llm.requests[0]["system_prompt"], llm.requests[1]["system_prompt"])
        self.assertEqual(llm.requests[0]["prompt_cache_key"], llm.requests[1]["prompt_cache_key"])
        self.assertEqual(
            llm.requests[0]["user_prompt"] + "\n\n" + FINAL_RESPONSE_RECOVERY_FEEDBACK,
            llm.requests[1]["user_prompt"],
        )

    def test_repeated_invalid_attention_output_uses_normal_plain_text_recovery(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.json_calls = 0
                self.text_calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def call_chat_json_result(self, **_kwargs):
                self.json_calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text='{"speech":"',
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                return ChatTextResult(text="格式恢复后的真实说明。", raw_text="格式恢复后的真实说明。")

        llm = FakeLLM()
        engine = self._engine(
            llm,
            context=_default_context(prompt_scope="qq_attention", post_user_turns=[]),
        )
        result = engine._build_final_response(
            session_id="qq_group_shared_1",
            profile_user_id="qq_group_shared_1",
            user_message="event.group_attention_review",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
            prompt_scope="qq_attention",
        )

        self.assertEqual(llm.json_calls, 3)
        self.assertEqual(llm.text_calls, 1)
        self.assertNotIn("_deliberate_silence", result)
        self.assertEqual(result["speech"], "格式恢复后的真实说明。")

    def test_optional_attention_legal_tool_call_still_enters_tool_loop(self) -> None:
        class FakeLLM:
            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def call_chat_json_result(**_kwargs):
                return ChatJSONResult(
                    parsed={
                        "speech": "我先确认一下。",
                        "tool_call": {"type": "web_search", "query": "更新内容"},
                    },
                    raw_text=(
                        '{"speech":"我先确认一下。","tool_call":'
                        '{"type":"web_search","query":"更新内容"}}'
                    ),
                )

        engine = self._engine(
            FakeLLM(),
            context=_default_context(prompt_scope="qq_attention", post_user_turns=[]),
        )
        result = engine._build_final_response(
            session_id="qq_group_shared_1",
            profile_user_id="qq_group_shared_1",
            user_message="event.group_attention_review",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
            prompt_scope="qq_attention",
        )

        self.assertNotIn("_deliberate_silence", result)
        self.assertEqual(result["tool_call"]["type"], "web_search")

    def test_attention_can_stay_silent_after_current_turn_tool_execution(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **_kwargs):
                self.calls += 1
                parsed = {"speech": ""}
                return ChatJSONResult(parsed=parsed, raw_text=json.dumps(parsed, ensure_ascii=False))

        llm = FakeLLM()
        engine = self._real_normalize_engine(
            llm,
            context=_default_context(prompt_scope="qq_attention"),
            client_mode=ClientMode.QQ_TEXT,
        )
        result = engine._build_final_response(
            session_id="qq_group_shared_1",
            profile_user_id="qq_group_shared_1",
            user_message="event.group_attention_review",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
            prompt_scope="qq_attention",
            post_user_turns=[{"role": "tool", "content": "done", "tool_call_id": "call-1"}],
        )

        self.assertEqual(llm.calls, 1)
        self.assertTrue(result["_deliberate_silence"])
        self.assertEqual(result["speech"], "")

    def test_streaming_attention_can_stay_silent_after_material_load(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def stream_chat_json(self, **_kwargs):
                self.calls += 1
                if False:
                    yield {}
                return SimpleNamespace(
                    parsed={"speech": ""},
                    raw_text='{"speech":""}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        llm = FakeLLM()
        engine = self._real_normalize_engine(
            llm,
            context=_default_context(
                prompt_scope="qq_attention",
                post_user_turns=[
                    {"role": "tool", "content": "image loaded", "tool_call_id": "load-image-1"},
                    {"role": "user", "content": "工具为当前模型请求加载了图片：img_001。"},
                ],
            ),
            client_mode=ClientMode.QQ_TEXT,
        )

        _events, result = self._run_stream(engine)

        self.assertEqual(llm.calls, 1)
        self.assertTrue(result["_deliberate_silence"])
        self.assertEqual(result["speech"], "")

    # --- Phase 5 item 1: malformed final with 33 tools -> same-turn recovery ---

    def test_malformed_final_with_33_tools_recovers_same_turn(self) -> None:
        malformed = (
            '{"emotion":"normal","speech":"仓库看完了，建议补 README 和部署文档。",'
            '"tool_call":{"type":"pending"'
        )

        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls: list[dict] = []
                self.text_calls = 0
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def stream_chat_json(self, **kwargs):
                self.stream_calls.append(dict(kwargs))
                if False:
                    yield None
                if len(self.stream_calls) == 1:
                    return SimpleNamespace(
                        parsed={"speech": "仓库看完了，建议补 README 和部署文档。"},
                        raw_text=malformed,
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                return SimpleNamespace(
                    parsed={"speech": "仓库看完了，建议补 README 和部署文档。", "tool_call": None},
                    raw_text='{"speech":"仓库看完了，建议补 README 和部署文档。","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                return ChatTextResult(text="", raw_text="")

        llm = FakeLLM()
        engine = self._engine(llm)
        events, result = self._run_stream(engine)

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result["speech"], "仓库看完了，建议补 README 和部署文档。")
        self.assertNotIn("_transient_final_failure", result)
        # The configured three structured generations are real attempts; the
        # second one succeeds, so the third is never issued.
        self.assertEqual(len(llm.stream_calls), 2)
        self.assertEqual(llm.text_calls, 0)
        first, second = llm.stream_calls
        self.assertEqual(len(first["native_tools"]), 33)
        # A malformed decision does not close the tool surface. The next
        # attempt may still choose a real tool or deliver normally.
        self.assertEqual(second["native_tools"], first["native_tools"])
        self.assertEqual(second["native_tool_choice"], first["native_tool_choice"])
        self.assertEqual(first["prompt_cache_key"], second["prompt_cache_key"])
        self.assertEqual(first["system_prompt"], second["system_prompt"])
        self.assertEqual(first["history_turns"], second["history_turns"])
        self.assertEqual(first["post_user_turns"], second["post_user_turns"])
        # Recovery feedback is appended only at the tail of the same prompt.
        self.assertTrue(second["user_prompt"].startswith(first["user_prompt"]))
        self.assertIn("没有形成有效的工具调用或可交付回复", second["user_prompt"])
        self.assertIn("如果仍需操作，请发出合法工具调用", second["user_prompt"])
        self.assertIn("不要重复已完成的工具", second["user_prompt"])
        self.assertIn("不要讨论这次格式错误", second["user_prompt"])
        self.assertIn("chat_final_response_retries", llm.metrics)

    def test_streamed_speech_sentence_is_not_repeated_during_same_turn_recovery(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    yield {"type": "speech_chunk", "text": "第一句已经说完。"}
                    yield {"type": "speech_segment", "index": 0, "text": "第一句已经说完。"}
                    return SimpleNamespace(
                        parsed={"speech": "第一句已经说完。"},
                        raw_text='{"speech":"第一句已经说完。","tool_call":{"type":"pending"',
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="第一句已经说完。",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                yield {"type": "speech_chunk", "text": "这里补上剩余结论。"}
                yield {"type": "speech_segment", "index": 0, "text": "这里补上剩余结论。"}
                return SimpleNamespace(
                    parsed={"speech": "这里补上剩余结论。", "tool_call": None},
                    raw_text='{"speech":"这里补上剩余结论。","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="这里补上剩余结论。",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        events, result = self._run_stream(engine)

        delivered = [event["text"] for event in events if event.get("type") == "speech_segment"]
        self.assertEqual(delivered, ["第一句已经说完。", "这里补上剩余结论。"])
        self.assertIn("第一句已经说完。", llm.calls[1]["user_prompt"])
        self.assertIn("从 speech 中解析出的完整句子", llm.calls[1]["user_prompt"])
        self.assertIn("用户可能已经看到或听到", llm.calls[1]["user_prompt"])
        self.assertEqual(result["speech"], "第一句已经说完。\n这里补上剩余结论。")

    def test_recovery_suppresses_only_replayed_prefix_and_keeps_intentional_repeat(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield {"type": "speech_chunk", "text": "好。"}
                    yield {"type": "speech_segment", "index": 0, "text": "好。"}
                    return SimpleNamespace(
                        parsed={"speech": "好。"},
                        raw_text='{"speech":"好。","tool_call":{"type":"pending"',
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="好。",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                # The recovery provider repeats the already-emitted prefix,
                # then intentionally uses the same sentence once more.
                yield {"type": "speech_chunk", "text": "好。好。结束。"}
                yield {"type": "speech_segment", "index": 0, "text": "好。"}
                yield {"type": "speech_segment", "index": 1, "text": "好。"}
                yield {"type": "speech_segment", "index": 2, "text": "结束。"}
                return SimpleNamespace(
                    parsed={"speech": "好。好。结束。", "tool_call": None},
                    raw_text='{"speech":"好。好。结束。","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="好。好。结束。",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        events, result = self._run_stream(self._engine(FakeLLM()))

        self.assertEqual(
            [event["text"] for event in events if event.get("type") == "speech_segment"],
            ["好。", "好。", "结束。"],
        )
        self.assertEqual(result["speech"], "好。好。结束。")

    def test_transport_error_after_complete_sentence_recovers_only_missing_tail(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield {"type": "speech_chunk", "text": "先给你第一句。"}
                    yield {"type": "speech_segment", "index": 0, "text": "先给你第一句。"}
                    return SimpleNamespace(
                        parsed={"speech": "先给你第一句。"},
                        raw_text='{"speech":"先给你第一句。',
                        error="upstream connection closed",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="先给你第一句。",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                self.assert_recovery_prompt(kwargs["user_prompt"])
                yield {"type": "speech_chunk", "text": "这里是剩余结论。"}
                yield {"type": "speech_segment", "index": 0, "text": "这里是剩余结论。"}
                return SimpleNamespace(
                    parsed={"speech": "这里是剩余结论。", "tool_call": None},
                    raw_text='{"speech":"这里是剩余结论。","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="这里是剩余结论。",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            @staticmethod
            def assert_recovery_prompt(prompt: str) -> None:
                if "先给你第一句。" not in prompt:
                    raise AssertionError(prompt)

        events, result = self._run_stream(self._engine(FakeLLM()))

        self.assertEqual(
            [event["text"] for event in events if event.get("type") == "speech_segment"],
            ["先给你第一句。", "这里是剩余结论。"],
        )
        self.assertFalse(any(event.get("type") == "stream_error" for event in events))
        self.assertEqual(result["speech"], "先给你第一句。\n这里是剩余结论。")

    def test_repeated_stream_transport_errors_still_reach_plain_text_tail_recovery(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0
                self.text_calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **_kwargs):
                self.stream_calls += 1
                yield {"type": "speech_chunk", "text": "第一句。"}
                yield {"type": "speech_segment", "index": 0, "text": "第一句。"}
                return SimpleNamespace(
                    parsed={"speech": "第一句。"},
                    raw_text='{"speech":"第一句。',
                    error="upstream connection closed",
                    fallback_used=True,
                    latest_emotion="",
                    latest_speech="第一句。",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_text(self, **kwargs):
                self.text_calls += 1
                if "第一句。" not in kwargs["user_prompt"]:
                    raise AssertionError(kwargs["user_prompt"])
                return ChatTextResult(text="这里补上最后结论。", raw_text="这里补上最后结论。")

        llm = FakeLLM()
        events, result = self._run_stream(self._engine(llm))

        self.assertEqual(llm.stream_calls, 3)
        self.assertEqual(llm.text_calls, 1)
        self.assertEqual(
            [event["text"] for event in events if event.get("type") == "speech_segment"],
            ["第一句。"],
        )
        self.assertFalse(any(event.get("type") == "stream_error" for event in events))
        self.assertEqual(result["speech"], "第一句。\n这里补上最后结论。")

    # --- Phase 5 item 2: three damaged JSON -> plain-text recovery ---

    def test_three_damaged_json_then_plain_text_recovery(self) -> None:
        damaged = '{"speech":"'  # truncated inside the string: nothing extractable

        class FakeLLM:
            def __init__(self) -> None:
                self.json_calls: list[dict] = []
                self.text_calls: list[dict] = []
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def call_chat_json_result(self, **kwargs):
                self.json_calls.append(dict(kwargs))
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text=damaged,
                    fallback_used=True,
                )

            def call_chat_text(self, **kwargs):
                self.text_calls.append(dict(kwargs))
                return ChatTextResult(
                    text="连续三次都没有成功，这里用纯文本直接回答。",
                    raw_text="连续三次都没有成功，这里用纯文本直接回答。",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        # All three configured structured generations really run, then one
        # plain-text generation (no JSON requirement, no tools) recovers.
        self.assertEqual(len(llm.json_calls), 3)
        self.assertEqual(len(llm.text_calls), 1)
        self.assertEqual(result["speech"], "连续三次都没有成功，这里用纯文本直接回答。")
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(result["_final_recovery"]["kind"], "plain_text")
        self.assertIs(result["_emotion_model_authored"], False)
        self.assertIn("chat_final_plain_text_recoveries", llm.metrics)
        text_call = llm.text_calls[0]
        self.assertEqual(text_call["prompt_cache_key"], llm.json_calls[0]["prompt_cache_key"])
        self.assertEqual(text_call["system_prompt"], llm.json_calls[0]["system_prompt"])
        self.assertEqual(text_call["history_turns"], llm.json_calls[0]["history_turns"])
        self.assertEqual(text_call["post_user_turns"], llm.json_calls[0]["post_user_turns"])
        self.assertIn("直接用纯文本向用户说明当前实际完成度", text_call["user_prompt"])
        self.assertIn("不要输出 JSON", text_call["user_prompt"])
        self.assertIn("不要调用工具", text_call["user_prompt"])

    # --- Phase 5 item 3: tool executed once; recovery never repeats it ---

    def test_tool_result_round_recovers_without_repeating_tools(self) -> None:
        context = _default_context(
            post_user_turns=[
                {"role": "tool", "tool_call_id": "call_1", "content": "搜索结果：仓库结构"}
            ],
            native_tools=[{"type": "function", "function": {"name": "web_search"}}],
        )

        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": "我先查一下。"},
                        raw_text='{"speech":"我先查一下。","tool_call":{"type":"web_search"',
                        fallback_used=True,
                    )
                return ChatJSONResult(
                    parsed={"speech": "查完了：仓库结构是 monorepo。", "tool_call": None},
                    raw_text='{"speech":"查完了：仓库结构是 monorepo。","tool_call":null}',
                )

        llm = FakeLLM()
        engine = self._engine(llm, context=context)
        result = self._run_nonstream(engine)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(result["speech"], "查完了：仓库结构是 monorepo。")
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(len(llm.calls[0]["native_tools"]), 1)
        self.assertEqual(llm.calls[1]["native_tools"], llm.calls[0]["native_tools"])
        # The completed tool result stays in the retry context unchanged;
        # keeping tools available does not itself repeat any side effect.
        self.assertEqual(llm.calls[1]["post_user_turns"], llm.calls[0]["post_user_turns"])
        self.assertIn("不要重复已完成的工具", llm.calls[1]["user_prompt"])

    # --- Phase 5 item 4: plain refusal/explanation text is not dropped ---

    def test_plain_refusal_text_without_json_is_delivered(self) -> None:
        refusal = "我现在不想回答这个问题。"

        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.text_calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text=refusal,
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                raise AssertionError("complete plain text must not need text recovery")

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(result["speech"], refusal)
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(result["_final_recovery"]["kind"], "plain_text_wrap")
        self.assertIs(result["_emotion_model_authored"], False)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.text_calls, 0)

    # --- Phase 5 item 5: damaged presentation fields keep the speech ---

    def test_damaged_presentation_fields_still_deliver_complete_speech(self) -> None:
        class FakeLLM:
            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **_kwargs):
                return ChatJSONResult(
                    parsed={
                        "speech": "完整答复。",
                        "emotion": "not_a_valid_emotion",
                        "scene": {"major": "???", "minor": None},
                        "tool_call": None,
                    },
                    raw_text=(
                        '{"speech":"完整答复。","emotion":"not_a_valid_emotion",'
                        '"scene":{"major":"???","minor":null},"tool_call":null}'
                    ),
                )

        llm = FakeLLM()
        engine = self._real_normalize_engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(result["speech"], "完整答复。")
        self.assertNotIn("_transient_final_failure", result)
        # The damaged presentation fields do not withhold the text; they are
        # tolerated as-is instead of turning the reply into a failure.
        self.assertEqual(result["emotion"], "not_a_valid_emotion")

    # --- Phase 5 item 6: QQ never sees the generic failure notice after recovery ---

    def test_recovered_final_is_not_transient_so_qq_notice_is_suppressed(self) -> None:
        # The QQ delivery path only sends "这次没有形成可交付的文字结果" for
        # frames carrying _transient_final_failure. A malformed final that
        # recovers in the same turn must therefore never trigger the notice.
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return ChatJSONResult(
                        parsed={"speech": "", "tool_call": None},
                        raw_text='{"emotion":"normal","speech":"第一次格式坏了","tool_call":{"type":"pending"',
                        fallback_used=True,
                    )
                return ChatJSONResult(
                    parsed={"speech": "格式修复后正常交付的答复。", "tool_call": None},
                    raw_text='{"speech":"格式修复后正常交付的答复。","tool_call":null}',
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(result["speech"], "格式修复后正常交付的答复。")
        self.assertFalse(result.get("_transient_final_failure"))

    # --- Phase 5 item 7: streaming and non-streaming share terminal semantics ---

    def test_stream_and_nonstream_share_terminal_semantics(self) -> None:
        malformed = '{"speech":"回复内容","tool_call":{"type":"pending"'

        def build_llm(stream: bool):
            class FakeLLM:
                def __init__(self) -> None:
                    self.calls = 0

                @staticmethod
                def snapshot_metrics() -> dict:
                    return {}

                @staticmethod
                def record_metric(_name: str) -> None:
                    return None

                def stream_chat_json(self, **_kwargs):
                    if False:
                        yield None
                    self.calls += 1
                    if self.calls == 1:
                        return SimpleNamespace(
                            parsed={"speech": "回复内容"},
                            raw_text=malformed,
                            error="",
                            fallback_used=True,
                            latest_emotion="",
                            latest_speech="",
                            latest_reply_medium="",
                            native_preface_text="",
                        )
                    return SimpleNamespace(
                        parsed={"speech": "恢复后的最终答复。", "tool_call": None},
                        raw_text='{"speech":"恢复后的最终答复。","tool_call":null}',
                        error="",
                        fallback_used=False,
                        latest_emotion="",
                        latest_speech="",
                        latest_reply_medium="",
                        native_preface_text="",
                    )

                def call_chat_json_result(self, **_kwargs):
                    self.calls += 1
                    if self.calls == 1:
                        return ChatJSONResult(
                            parsed={"speech": "回复内容"},
                            raw_text=malformed,
                            fallback_used=True,
                        )
                    return ChatJSONResult(
                        parsed={"speech": "恢复后的最终答复。", "tool_call": None},
                        raw_text='{"speech":"恢复后的最终答复。","tool_call":null}',
                    )

            return FakeLLM()

        stream_llm = build_llm(stream=True)
        stream_engine = self._engine(stream_llm)
        _events, stream_result = self._run_stream(stream_engine)

        nonstream_llm = build_llm(stream=False)
        nonstream_engine = self._engine(nonstream_llm)
        nonstream_result = self._run_nonstream(nonstream_engine)

        self.assertEqual(stream_result["speech"], "恢复后的最终答复。")
        self.assertEqual(nonstream_result["speech"], "恢复后的最终答复。")
        self.assertEqual(stream_result.get("_transient_final_failure"), nonstream_result.get("_transient_final_failure"))
        self.assertEqual(stream_llm.calls, nonstream_llm.calls)
        self.assertEqual(stream_result.get("_provider_output_raw"), nonstream_result.get("_provider_output_raw"))

    # --- Phase 2 constraint 2: a legal native tool call continues the tool loop ---

    def test_legal_native_tool_call_continues_tool_loop_not_recovery(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls += 1
                native_call = {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query":"仓库"}'},
                }
                return ChatJSONResult(
                    parsed={
                        "speech": "我先查一下。",
                        "_native_tool_calls": [native_call],
                        "_native_tool_call": native_call,
                        "tool_call": None,
                    },
                    raw_text="我先查一下。",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(llm.calls, 1)  # no recovery was entered
        self.assertIsNotNone(result.get("_native_tool_call"))
        self.assertNotIn("_transient_final_failure", result)

    def test_retry_may_return_native_tool_call_without_speech(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": "", "tool_call": None},
                        raw_text='{"speech":',
                        fallback_used=True,
                    )
                native_call = {
                    "id": "call_retry",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query":"仓库"}'},
                }
                return ChatJSONResult(
                    parsed={
                        "_native_tool_calls": [native_call],
                        "_native_tool_call": native_call,
                        "tool_call": None,
                    },
                    raw_text="",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[1]["native_tools"], llm.calls[0]["native_tools"])
        self.assertNotEqual(llm.calls[1]["native_tool_choice"], "none")
        self.assertTrue(result.get("_native_tool_calls"))
        self.assertFalse(result.get("speech"))

    # --- Phase 5 item 13: all providers unreachable -> structured service failure ---

    def test_all_providers_unreachable_emits_structured_service_failure(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def call_chat_json_result(self, **_kwargs):
                self.calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="",
                    error="connection timeout",
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                return ChatTextResult(text="", raw_text="", error="connection timeout")

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(llm.calls, 3)
        self.assertTrue(result["_transient_final_failure"])
        self.assertEqual(result["_service_failure"]["status"], "unavailable")
        self.assertIn("服务暂时不可用", result["speech"])
        self.assertIn("chat_final_service_failures", llm.metrics)

    def test_stream_all_providers_unreachable_emits_structured_service_failure(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.nonstream_calls = 0
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def stream_chat_json(self, **_kwargs):
                if False:
                    yield None
                return SimpleNamespace(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="",
                    error="502 Bad Gateway",
                    fallback_used=True,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_json_result(self, **_kwargs):
                self.nonstream_calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="",
                    error="502 Bad Gateway",
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                return ChatTextResult(text="", raw_text="", error="502 Bad Gateway")

        llm = FakeLLM()
        engine = self._engine(llm)
        events, result = self._run_stream(engine)

        self.assertEqual(events[-1]["type"], "stream_error")
        self.assertTrue(result["_transient_final_failure"])
        self.assertEqual(result["_service_failure"]["status"], "unavailable")
        self.assertIn("chat_final_service_failures", llm.metrics)

    def test_nonstream_rate_limit_gets_one_same_request_retry_without_format_feedback(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.text_calls = 0
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": "", "tool_call": None},
                        raw_text="",
                        error="Error code: 429 - upstream_rate_limited",
                        fallback_used=True,
                    )
                return ChatJSONResult(
                    parsed={"speech": "重试成功。", "tool_call": None},
                    raw_text='{"speech":"重试成功。","tool_call":null}',
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                return ChatTextResult(text="不应调用", raw_text="不应调用")

        llm = FakeLLM()
        result = self._run_nonstream(self._engine(llm))

        self.assertEqual(result["speech"], "重试成功。")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.text_calls, 0)
        self.assertEqual(llm.calls[0]["prompt_cache_key"], llm.calls[1]["prompt_cache_key"])
        self.assertEqual(llm.calls[0]["user_prompt"], llm.calls[1]["user_prompt"])
        self.assertIn("chat_final_rate_limit_retries", llm.metrics)

    def test_repeated_nonstream_rate_limit_stops_after_two_calls(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0
                self.text_calls = 0
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def call_chat_json_result(self, **_kwargs):
                self.calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="",
                    error="HTTP 429 rate limit exceeded",
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                return ChatTextResult(text="", raw_text="")

        llm = FakeLLM()
        result = self._run_nonstream(self._engine(llm))

        self.assertEqual(llm.calls, 2)
        self.assertEqual(llm.text_calls, 0)
        self.assertEqual(result["_service_failure"]["reason"], "provider_rate_limited")
        self.assertIn("chat_final_rate_limited", llm.metrics)

    def test_stream_rate_limit_and_fallback_rate_limit_stop_without_uncached_or_text_retry(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0
                self.nonstream_calls = 0
                self.text_calls = 0
                self.metrics: list[str] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            def record_metric(self, name: str) -> None:
                self.metrics.append(name)

            def stream_chat_json(self, **_kwargs):
                if False:
                    yield None
                self.stream_calls += 1
                return SimpleNamespace(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="",
                    error="status_code=429 upstream_rate_limited",
                    fallback_used=True,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_json_result(self, **_kwargs):
                self.nonstream_calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="",
                    error="RateLimitError: 429",
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                return ChatTextResult(text="", raw_text="")

        llm = FakeLLM()
        _events, result = self._run_stream(self._engine(llm))

        self.assertEqual(llm.stream_calls, 1)
        self.assertEqual(llm.nonstream_calls, 1)
        self.assertEqual(llm.text_calls, 0)
        self.assertEqual(result["_service_failure"]["reason"], "provider_rate_limited")
        self.assertNotIn("chat_stream_uncached_fallbacks", llm.metrics)

    # --- Phase 5 item 14: stable prefix/cache key; retry info only at the tail ---

    def test_cache_key_and_prefix_stable_across_retries(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": "", "tool_call": None},
                        raw_text='{"speech":"',
                        fallback_used=True,
                    )
                return ChatJSONResult(
                    parsed={"speech": "重试后正常。", "tool_call": None},
                    raw_text='{"speech":"重试后正常。","tool_call":null}',
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(result["speech"], "重试后正常。")
        first, second = llm.calls
        self.assertEqual(first["prompt_cache_key"], second["prompt_cache_key"])
        self.assertEqual(first["system_prompt"], second["system_prompt"])
        self.assertEqual(first["user_images"], second["user_images"])
        self.assertTrue(second["user_prompt"].startswith(first["user_prompt"]))
        self.assertEqual(first["user_prompt"] + "\n\n" + FINAL_RESPONSE_RECOVERY_FEEDBACK, second["user_prompt"])

    # --- A wire tool preface is never delivered as the final answer ---

    def test_wire_tool_preface_is_never_delivered_as_final(self) -> None:
        malformed = '{"speech":"我先查一下。","tool_call":{"type":"web_search","query":"仓库"'

        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **_kwargs):
                if False:
                    yield None
                self.stream_calls += 1
                if self.stream_calls == 1:
                    return SimpleNamespace(
                        parsed={"speech": "我先查一下。"},
                        raw_text=malformed,
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="我先查一下。",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                return SimpleNamespace(
                    parsed={"speech": "查到了：仓库是 monorepo。", "tool_call": None},
                    raw_text='{"speech":"查到了：仓库是 monorepo。","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="查到了：仓库是 monorepo。",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        events, result = self._run_stream(engine)

        self.assertEqual(llm.stream_calls, 2)
        self.assertEqual(result["speech"], "查到了：仓库是 monorepo。")
        self.assertNotEqual(result["speech"], "我先查一下。")
        self.assertNotIn("_transient_final_failure", result)

    # --- Explicit null / omitted tool field still delivers (regression) ---

    def test_complete_recovered_speech_with_explicit_null_tool_call_is_delivered(self) -> None:
        malformed = '{"speech":"工具都做完了，这是最终结论。","tool_call":null'

        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **_kwargs):
                if False:
                    yield None
                self.stream_calls += 1
                return SimpleNamespace(
                    parsed={"speech": "工具都做完了，这是最终结论。", "tool_call": None},
                    raw_text=malformed,
                    error="",
                    fallback_used=True,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        _events, result = self._run_stream(engine)

        self.assertEqual(result["speech"], "工具都做完了，这是最终结论。")
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(llm.stream_calls, 1)

    def test_parse_recovery_never_delivers_a_native_tool_preface(self) -> None:
        engine = self._engine(SimpleNamespace())

        self.assertFalse(
            engine._is_deliverable_parse_recovery(
                {
                    "speech": "我先查一下。",
                    "tool_call": None,
                    "_native_tool_call": {"type": "web_search"},
                },
                parse_fallback=True,
                provider_output_raw='{"speech":"我先查一下。","tool_call":null',
            )
        )

    def test_parse_recovery_delivers_complete_speech_when_optional_tool_field_is_omitted(self) -> None:
        engine = self._engine(SimpleNamespace())

        self.assertTrue(
            engine._is_deliverable_parse_recovery(
                {"speech": "简短答复已经完整。", "tool_call": None},
                parse_fallback=True,
                provider_output_raw='{"speech":"简短答复已经完整。"',
            )
        )

    def test_parse_recovery_rejects_explicit_non_null_tool_call(self) -> None:
        engine = self._engine(SimpleNamespace())

        self.assertFalse(
            engine._is_deliverable_parse_recovery(
                {"speech": "我继续查。", "tool_call": None},
                parse_fallback=True,
                provider_output_raw='{"speech":"我继续查。","tool_call":{"type":"web_search"',
            )
        )

    def test_parse_recovery_rejects_host_fallback_speech_absent_from_wire(self) -> None:
        engine = self._engine(SimpleNamespace())

        self.assertFalse(
            engine._is_deliverable_parse_recovery(
                {
                    "speech": "我在认真听你说，要不要再多告诉我一点？",
                    "tool_call": None,
                    "_provider_fallback": True,
                },
                parse_fallback=True,
                provider_output_raw="   ",
            )
        )

    def test_stream_host_fallback_is_retried_instead_of_delivered(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **_kwargs):
                if False:
                    yield None
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(
                        parsed={
                            "speech": "我在认真听你说，要不要再多告诉我一点？",
                            "tool_call": None,
                        },
                        raw_text="   ",
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                return SimpleNamespace(
                    parsed={
                        "speech": "工具预算已用完；测试仍有失败，任务尚未完成，请发送继续。",
                        "tool_call": None,
                    },
                    raw_text=(
                        '{"speech":"工具预算已用完；测试仍有失败，任务尚未完成，请发送继续。",'
                        '"tool_call":null}'
                    ),
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        _events, result = self._run_stream(engine)

        self.assertEqual(llm.calls, 2)
        self.assertIn("任务尚未完成", result["speech"])
        self.assertNotIn("我在认真听你说", result["speech"])
        self.assertNotIn("_transient_final_failure", result)

    # --- Plain-text recovery rejects still-damaged output ---

    def test_plain_text_recovery_rejects_still_damaged_json(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.json_calls = 0
                self.text_calls = 0

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **_kwargs):
                self.json_calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text='{"speech":"',
                    fallback_used=True,
                )

            def call_chat_text(self, **_kwargs):
                self.text_calls += 1
                return ChatTextResult(
                    text='{"speech":"还是坏的 JSON"',
                    raw_text='{"speech":"还是坏的 JSON"',
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = self._run_nonstream(engine)

        self.assertEqual(llm.json_calls, 3)
        self.assertEqual(llm.text_calls, 1)
        # The host must never fabricate or mangle an answer: still-damaged
        # output stays transient instead of being wrapped.
        self.assertTrue(result["_transient_final_failure"])
        self.assertNotEqual(result.get("speech"), '{"speech":"还是坏的 JSON"')

    def test_textual_dsml_tool_protocol_retries_with_native_tools_enabled(self) -> None:
        protocol = '["qq-tools"]</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>'
        context = _default_context(
            native_tools=[{"type": "function", "function": {"name": "load_mcp"}}],
        )

        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": protocol, "tool_call": None},
                        raw_text=protocol,
                        fallback_used=True,
                    )
                native_call = {
                    "id": "call_load",
                    "type": "function",
                    "function": {
                        "name": "load_mcp",
                        "arguments": '{"server_ids":["qq-tools"]}',
                    },
                }
                return ChatJSONResult(
                    parsed={
                        "speech": "",
                        "tool_call": None,
                        "_native_tool_call": native_call,
                        "_native_tool_calls": [native_call],
                    },
                    raw_text="",
                )

        llm = FakeLLM()
        result = self._run_nonstream(self._engine(llm, context=context))

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(result["_native_tool_call"]["function"]["name"], "load_mcp")
        self.assertEqual(llm.calls[1]["native_tools"], llm.calls[0]["native_tools"])
        self.assertIn("文本化的工具协议", llm.calls[1]["user_prompt"])
        self.assertIn("不要把 DSML", llm.calls[1]["user_prompt"])

    def test_streamed_dsml_protocol_is_not_forwarded_before_retry(self) -> None:
        protocol = '["qq-tools"]</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>'

        class FakeLLM:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            @staticmethod
            def snapshot_metrics() -> dict:
                return {}

            @staticmethod
            def record_metric(_name: str) -> None:
                return None

            def stream_chat_json(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    yield {"type": "speech_chunk", "text": protocol}
                    yield {"type": "speech_segment", "index": 0, "text": protocol}
                    return SimpleNamespace(
                        parsed={"speech": protocol, "tool_call": None},
                        raw_text=protocol,
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech=protocol,
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                yield {"type": "speech_chunk", "text": "已经重新用原生工具入口处理。"}
                yield {"type": "speech_segment", "index": 0, "text": "已经重新用原生工具入口处理。"}
                return SimpleNamespace(
                    parsed={"speech": "已经重新用原生工具入口处理。", "tool_call": None},
                    raw_text='{"speech":"已经重新用原生工具入口处理。","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="已经重新用原生工具入口处理。",
                    latest_reply_medium="",
                    native_preface_text="",
                )

        llm = FakeLLM()
        events, result = self._run_stream(self._engine(llm))

        visible_text = "\n".join(str(event.get("text") or "") for event in events)
        self.assertNotIn("DSML", visible_text)
        self.assertEqual(result["speech"], "已经重新用原生工具入口处理。")
        self.assertIn("文本化的工具协议", llm.calls[1]["user_prompt"])


if __name__ == "__main__":
    unittest.main()
