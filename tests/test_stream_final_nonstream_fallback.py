from __future__ import annotations

import unittest
from types import SimpleNamespace

from companion_v01.engine import AkaneMemoryEngine


def exhaust_generator_return(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as exc:
            return events, exc.value


class _FakeLLM:
    def __init__(
        self,
        *,
        stream_events=None,
        stream_parsed=None,
        stream_error="",
        nonstream_result=None,
        nonstream_results=None,
        nonstream_transport_failures=None,
    ):
        self.stream_events = list(stream_events or [])
        self.stream_parsed = dict(stream_parsed or {})
        self.stream_error = stream_error
        self.nonstream_results = [
            dict(item or {})
            for item in (nonstream_results if nonstream_results is not None else [nonstream_result])
        ]
        self.nonstream_transport_failures = set(nonstream_transport_failures or [])
        self.stream_calls = []
        self.nonstream_calls = []
        self.recorded_metrics = []
        self.metrics = {}
        self.nonstream_parse_failure = False

    def stream_chat_json(self, **kwargs):
        self.stream_calls.append(kwargs)
        for event in self.stream_events:
            yield dict(event)
        return SimpleNamespace(
            parsed=dict(self.stream_parsed),
            error=self.stream_error,
            latest_emotion="",
            latest_speech="",
            latest_reply_medium="",
            native_preface_text="",
        )

    def call_chat_json(self, **kwargs):
        call_index = len(self.nonstream_calls)
        self.nonstream_calls.append(kwargs)
        if call_index in self.nonstream_transport_failures:
            self.metrics["errors"] = self.metrics.get("errors", 0) + 1
            self.metrics["chat_json_fallbacks"] = self.metrics.get("chat_json_fallbacks", 0) + 1
        elif self.nonstream_parse_failure:
            self.metrics["chat_json_fallbacks"] = self.metrics.get("chat_json_fallbacks", 0) + 1
        result_index = min(call_index, len(self.nonstream_results) - 1)
        return dict(self.nonstream_results[result_index])

    def snapshot_metrics(self):
        return dict(self.metrics)

    def record_metric(self, name):
        self.recorded_metrics.append(name)


class StreamFinalNonstreamFallbackTests(unittest.TestCase):
    fallback = {
        "emotion": "normal",
        "speech": "我在认真听你说，要不要再多告诉我一点？",
        "speech_segments": [],
        "tool_call": None,
    }

    def _build_engine(self, llm):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.llm = llm
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": dict(self.fallback),
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": True,
            "native_tools": [{"type": "function", "name": "lookup"}],
            "native_tool_choice": "auto",
            "system_extra_blocks": ["stable"],
            "history_turns": [{"role": "user", "content": "old"}],
            "post_user_turns": [],
            "prompt_audit_sections": [],
        }
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda **kwargs: dict(kwargs["result"] or {})
        engine._attach_tool_execution_receipts = lambda *_args, **_kwargs: None
        return engine

    def _run(self, engine):
        return exhaust_generator_return(
            engine._stream_final_response(
                session_id="s",
                profile_user_id="u",
                user_message="hello",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
            )
        )

    def test_stream_transport_error_without_speech_uses_equivalent_nonstream_request(self):
        llm = _FakeLLM(
            stream_parsed=self.fallback,
            stream_error="502 Bad Gateway",
            nonstream_result={"speech": "这是非流式恢复后的正常答复。", "tool_call": None},
        )
        engine = self._build_engine(llm)

        events, result = self._run(engine)

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result["speech"], "这是非流式恢复后的正常答复。")
        self.assertEqual(len(llm.stream_calls), 1)
        self.assertEqual(len(llm.nonstream_calls), 1)
        stream_request = dict(llm.stream_calls[0])
        stream_request.pop("early_tool_call_validator")
        self.assertEqual(stream_request, llm.nonstream_calls[0])
        self.assertIn("chat_stream_nonstream_fallbacks", llm.recorded_metrics)
        self.assertIn("chat_stream_nonstream_recoveries", llm.recorded_metrics)

    def test_stream_error_after_uncommitted_speech_uses_nonstream_recovery(self):
        llm = _FakeLLM(
            stream_events=[{"type": "speech_segment", "text": "已经发给用户的部分文本。"}],
            stream_parsed=self.fallback,
            stream_error="502 Bad Gateway",
            nonstream_result={"speech": "非流式恢复后的正常答复。", "tool_call": None},
        )
        engine = self._build_engine(llm)

        events, result = self._run(engine)

        self.assertEqual(len(llm.nonstream_calls), 1)
        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result["speech"], "非流式恢复后的正常答复。")
        self.assertNotIn("_transient_final_failure", result)

    def test_stream_and_nonstream_failure_keep_structured_fallback(self):
        llm = _FakeLLM(
            stream_parsed=self.fallback,
            stream_error="502 Bad Gateway",
            nonstream_result=self.fallback,
        )
        llm.nonstream_parse_failure = True
        engine = self._build_engine(llm)

        events, result = self._run(engine)

        self.assertEqual(len(llm.stream_calls), 1)
        self.assertEqual(len(llm.nonstream_calls), 1)
        self.assertEqual(events[-1]["type"], "stream_error")
        self.assertTrue(result["_transient_final_failure"])
        self.assertIn("chat_stream_nonstream_fallback_failures", llm.recorded_metrics)

    def test_transport_failure_on_cached_nonstream_request_retries_without_provider_cache_key(self):
        llm = _FakeLLM(
            stream_parsed=self.fallback,
            stream_error="502 Bad Gateway",
            nonstream_results=[
                self.fallback,
                {"speech": "去掉故障缓存桶后恢复的正常答复。", "tool_call": None},
            ],
            nonstream_transport_failures={0},
        )
        engine = self._build_engine(llm)

        events, result = self._run(engine)

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result["speech"], "去掉故障缓存桶后恢复的正常答复。")
        self.assertEqual(len(llm.nonstream_calls), 2)
        self.assertNotEqual(llm.nonstream_calls[0]["prompt_cache_key"], "")
        self.assertEqual(llm.nonstream_calls[1]["prompt_cache_key"], "")
        self.assertIn("chat_stream_uncached_fallbacks", llm.recorded_metrics)
        self.assertIn("chat_stream_uncached_recoveries", llm.recorded_metrics)

    def test_successful_stream_path_is_unchanged(self):
        llm = _FakeLLM(stream_parsed={"speech": "正常流式答复。", "tool_call": None})
        engine = self._build_engine(llm)

        events, result = self._run(engine)

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result["speech"], "正常流式答复。")
        self.assertEqual(len(llm.nonstream_calls), 0)


if __name__ == "__main__":
    unittest.main()
