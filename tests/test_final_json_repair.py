from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.llm_runtime import ChatJSONResult


def _drain(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


class FinalJSONRepairTests(unittest.TestCase):
    @staticmethod
    def _engine(llm) -> AkaneMemoryEngine:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = llm
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "full system prompt",
            "user_prompt": "full projected user prompt",
            "fallback": {"speech": "", "tool_call": None},
            "visual_defaults": {"emotion": "normal"},
            "debug_enabled": False,
            "allow_tool_call": True,
            "native_tools": [{"type": "function", "function": {"name": "web_search"}}],
            "native_tool_choice": "auto",
            "system_extra_blocks": ["large context"],
            "history_turns": [{"role": "user", "content": "large history"}],
            "ephemeral_turns": [],
            "post_user_turns": [{"role": "tool", "content": "research result"}],
            "prompt_audit_sections": [],
            "prompt_scope": "",
        }
        engine._build_memcore_request_observer = lambda **_kwargs: None
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda *, result, **_kwargs: dict(result or {})
        engine._attach_memory_annotation_truth = lambda *_args, **_kwargs: None
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
        return engine

    def test_stream_parse_failure_uses_small_json_repair_instead_of_full_retry(self) -> None:
        malformed = '{"emotion":"normal","speech":"仓库看完了，建议补 README 和部署文档。"'

        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = []
                self.repair_calls = []
                self.metrics = []

            @staticmethod
            def snapshot_metrics():
                return {}

            def record_metric(self, name):
                self.metrics.append(name)

            def stream_chat_json(self, **kwargs):
                self.stream_calls.append(dict(kwargs))
                if False:
                    yield None
                return SimpleNamespace(
                    parsed={"emotion": "normal", "speech": "仓库看完了，建议补 README 和部署文档。"},
                    raw_text=malformed,
                    error="",
                    fallback_used=True,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_json_result(self, **kwargs):
                self.repair_calls.append(dict(kwargs))
                repaired = {
                    "emotion": "normal",
                    "speech": "仓库看完了，建议补 README 和部署文档。",
                    "tool_call": None,
                }
                return ChatJSONResult(
                    parsed=repaired,
                    raw_text=json.dumps(repaired, ensure_ascii=False),
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        events, result = _drain(
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

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result["speech"], "仓库看完了，建议补 README 和部署文档。")
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(len(llm.stream_calls), 1)
        self.assertEqual(len(llm.repair_calls), 1)
        repair = llm.repair_calls[0]
        self.assertEqual(repair["history_turns"], [])
        self.assertEqual(repair["post_user_turns"], [])
        self.assertEqual(repair["native_tools"], [])
        self.assertIsNone(repair["user_images"])
        self.assertEqual(repair["prompt_cache_key"], "")
        self.assertEqual(repair["max_output_tokens"], 512)
        self.assertNotIn("request_observer", repair)
        self.assertEqual(json.loads(repair["user_prompt"])["malformed_output"], malformed)
        self.assertIn("只修复 JSON", repair["system_prompt"])
        self.assertIn("chat_final_response_json_repair_successes", llm.metrics)
        self.assertNotIn("chat_final_response_retries", llm.metrics)

    def test_failed_json_repair_stops_without_replaying_full_context(self) -> None:
        malformed = '{"speech":"第一次格式坏了"'

        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0
                self.repair_calls = 0
                self.metrics = []

            @staticmethod
            def snapshot_metrics():
                return {}

            def record_metric(self, name):
                self.metrics.append(name)

            def stream_chat_json(self, **_kwargs):
                self.stream_calls += 1
                if False:
                    yield None
                if self.stream_calls == 1:
                    return SimpleNamespace(
                        parsed={"speech": "第一次格式坏了", "tool_call": None},
                        raw_text=malformed,
                        error="",
                        fallback_used=True,
                        latest_emotion="",
                        latest_speech="",
                        latest_reply_medium="",
                        native_preface_text="",
                    )
                return SimpleNamespace(
                    parsed={"speech": "完整上下文重试成功", "tool_call": None},
                    raw_text='{"speech":"完整上下文重试成功","tool_call":null}',
                    error="",
                    fallback_used=False,
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                    native_preface_text="",
                )

            def call_chat_json_result(self, **_kwargs):
                self.repair_calls += 1
                return ChatJSONResult(
                    parsed={"speech": "", "tool_call": None},
                    raw_text="still broken",
                    fallback_used=True,
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        _events, result = _drain(
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

        self.assertEqual(result["speech"], "第一次格式坏了")
        self.assertTrue(result["_transient_final_failure"])
        self.assertEqual(llm.repair_calls, 1)
        self.assertEqual(llm.stream_calls, 1)
        self.assertIn("chat_final_response_json_repair_failures", llm.metrics)
        self.assertNotIn("chat_final_response_retries", llm.metrics)

    def test_complete_recovered_speech_with_explicit_null_tool_call_is_delivered(self) -> None:
        malformed = '{"speech":"工具都做完了，这是最终结论。","tool_call":null'

        class FakeLLM:
            def __init__(self) -> None:
                self.stream_calls = 0
                self.repair_calls = 0
                self.metrics = []

            @staticmethod
            def snapshot_metrics():
                return {}

            def record_metric(self, name):
                self.metrics.append(name)

            def stream_chat_json(self, **_kwargs):
                self.stream_calls += 1
                if False:
                    yield None
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

            def call_chat_json_result(self, **_kwargs):
                self.repair_calls += 1
                raise AssertionError("safe recovered final speech must not call repair")

        llm = FakeLLM()
        engine = self._engine(llm)
        _events, result = _drain(
            engine._stream_final_response(
                session_id="qq_group_shared_1",
                profile_user_id="qq_group_shared_1",
                user_message="继续",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
            )
        )

        self.assertEqual(result["speech"], "工具都做完了，这是最终结论。")
        self.assertNotIn("_transient_final_failure", result)
        self.assertEqual(llm.stream_calls, 1)
        self.assertEqual(llm.repair_calls, 0)
        self.assertIn("chat_final_response_parse_recoveries", llm.metrics)

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
                allow_tool_call=True,
            )
        )

    def test_nonstream_final_response_uses_the_same_bounded_repair_path(self) -> None:
        malformed = '{"speech":"同步答复已经写好，只差一个括号"'

        class FakeLLM:
            def __init__(self) -> None:
                self.calls = []

            @staticmethod
            def snapshot_metrics():
                return {}

            @staticmethod
            def record_metric(_name):
                return None

            def call_chat_json_result(self, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    return ChatJSONResult(
                        parsed={"speech": "同步答复已经写好，只差一个括号", "tool_call": None},
                        raw_text=malformed,
                        fallback_used=True,
                    )
                repaired = {"speech": "同步答复已经写好，只差一个括号", "tool_call": None}
                return ChatJSONResult(
                    parsed=repaired,
                    raw_text=json.dumps(repaired, ensure_ascii=False),
                )

        llm = FakeLLM()
        engine = self._engine(llm)
        result = engine._build_final_response(
            session_id="private:1",
            profile_user_id="1",
            user_message="请回答",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
        )

        self.assertEqual(result["speech"], "同步答复已经写好，只差一个括号")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[1]["history_turns"], [])
        self.assertEqual(llm.calls[1]["native_tools"], [])
        self.assertNotIn("request_observer", llm.calls[1])


if __name__ == "__main__":
    unittest.main()
