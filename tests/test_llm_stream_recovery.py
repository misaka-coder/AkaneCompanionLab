from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from companion_v01.llm_runtime import LLMRuntime


class LLMStreamRecoveryTests(unittest.TestCase):
    def test_completed_speech_survives_a_malformed_json_tail(self) -> None:
        runtime = LLMRuntime.__new__(LLMRuntime)
        runtime._metrics_lock = threading.RLock()
        runtime._metrics = {}
        runtime._build_completion_kwargs = lambda **_kwargs: {}
        runtime._record_cache_metrics = lambda _response, **_kwargs: None
        runtime._close_stream = lambda _response: None
        runtime._create_completion = lambda **_kwargs: [object()]
        runtime._extract_stream_text = lambda _chunk: (
            '{"emotion":"happy","speech":"这是已经完整生成的正常回复。",'
            '"speech_segments":["这是已经完整生成的正常回复。"],"tool_call":null'
        )
        runtime._extract_json = lambda _text: None

        generator = runtime._stream_chat_json(
            bundle=SimpleNamespace(),
            system_prompt="system",
            user_prompt="user",
            fallback={"speech": "我在认真听你说，要不要再多告诉我一点？", "tool_call": None},
            temperature=0.0,
            early_tool_call_validator=None,
            prompt_cache_key="test:malformed_stream_tail",
        )
        events = []
        while True:
            try:
                events.append(next(generator))
            except StopIteration as exc:
                result = exc.value
                break

        self.assertIn(
            {"type": "speech_segment", "index": 0, "text": "这是已经完整生成的正常回复。"},
            events,
        )
        self.assertEqual(result.parsed["speech"], "这是已经完整生成的正常回复。")
        self.assertNotIn("我在认真听你说", str(result.parsed))
        self.assertIsNone(runtime.snapshot_metrics().get("chat_json_fallbacks"))


if __name__ == "__main__":
    unittest.main()
