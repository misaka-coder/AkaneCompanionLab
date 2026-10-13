"""Regressions for copied history records and stale stream retry feedback."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from companion_v01.assistant_history_projection import ASSISTANT_HISTORY_RECORD_HEADER
from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD
from tests.test_final_response_contract import build_engine, drain, generation_args


HISTORY_RECORD = (
    f'{ASSISTANT_HISTORY_RECORD_HEADER}（整体表情 smile）：\n'
    '“这是历史显示记录。”'
)
LEGACY_HISTORY_RECORD = (
    '历史回复（显示记录）\n'
    'emotion: "smile"\n'
    '1. emotion="smile"\n'
    '   text: 这是历史显示记录。'
)
BILINGUAL_REPLY = {
    "speech": "新的实际回复。",
    "emotion": "smile",
    "utterances": [
        {"text": "新的实际回复。", "say": "新しい返事です。", "style": "穏やかに", "emotion": "smile"}
    ],
}


def result(parsed, raw_text, *, fallback_used=False):
    return SimpleNamespace(parsed=parsed, raw_text=raw_text, fallback_used=fallback_used)


class ScriptedLLM:
    def __init__(self, results, *, text=HISTORY_RECORD, prefix=""):
        self.results = iter(results)
        self.text = text
        self.prefix = prefix
        self.json_calls = []
        self.text_calls = []

    def call_chat_json_result(self, **kwargs):
        self.json_calls.append(kwargs)
        return next(self.results)

    def stream_chat_json(self, **kwargs):
        current = self.call_chat_json_result(**kwargs)
        if self.prefix and len(self.json_calls) == 1:
            yield {"type": "speech_segment", "text": self.prefix}
        return current

    def call_chat_text(self, **kwargs):
        self.text_calls.append(kwargs)
        return SimpleNamespace(text=self.text, raw_text=self.text)


def run(engine, streaming):
    if streaming:
        return drain(engine._stream_final_response(**generation_args()))
    return [], engine._build_final_response(**generation_args())


class HistoryRecordResponseRecoveryTests(unittest.TestCase):
    def test_successful_stream_retry_preserves_bilingual_reply_without_text_recovery(self):
        for prefix in ("", "先前已交付的句子。"):
            with self.subTest(prefix=prefix):
                llm = ScriptedLLM(
                    [
                        result({"state_request": {}}, '答复正文\n{"state_request":{}}'),
                        result(BILINGUAL_REPLY, json.dumps(BILINGUAL_REPLY, ensure_ascii=False)),
                    ],
                    text="不应请求的纯文本尾段。",
                    prefix=prefix,
                )
                engine = build_engine(llm)
                engine._final_response_max_attempts = lambda _context: 2
                events, output = run(engine, True)
                self.assertEqual(len(llm.json_calls), 2)
                self.assertEqual(llm.text_calls, [])
                expected_units = ([{"text": prefix, "say": None, "style": ""}] if prefix else [])
                self.assertEqual(output["utterances"], expected_units + BILINGUAL_REPLY["utterances"])
                self.assertNotIn("_final_recovery", output)
                self.assertEqual(output["_provider_output_raw"], json.dumps(BILINGUAL_REPLY, ensure_ascii=False))
                if prefix:
                    self.assertEqual(output["speech"].replace("\n", ""), prefix + BILINGUAL_REPLY["speech"])
                    self.assertEqual(sum(event.get("text") == prefix for event in events), 1)

    def test_copied_history_record_requests_same_context_structured_repair(self):
        for streaming in (False, True):
            for raw in (HISTORY_RECORD, "```text\n" + HISTORY_RECORD + "\n```", LEGACY_HISTORY_RECORD,
                        "过往发言：\n“这是历史显示记录。”（表情 smile）"):
                with self.subTest(streaming=streaming, fenced=raw.startswith("```")):
                    llm = ScriptedLLM([
                        result({}, raw, fallback_used=True),
                        result(BILINGUAL_REPLY, json.dumps(BILINGUAL_REPLY, ensure_ascii=False)),
                    ])
                    engine = build_engine(llm)
                    engine._final_response_max_attempts = lambda _context: 2
                    _, output = run(engine, streaming)
                    self.assertEqual(len(llm.json_calls), 2)
                    self.assertEqual(llm.text_calls, [])
                    self.assertEqual(output["utterances"], BILINGUAL_REPLY["utterances"])
                    self.assertNotIn("_transient_final_failure", output)
                    self.assertEqual(llm.json_calls[0]["system_prompt"], llm.json_calls[1]["system_prompt"])
                    self.assertEqual(llm.json_calls[0]["prompt_cache_key"], llm.json_calls[1]["prompt_cache_key"])
                    self.assertNotIn("【宿主反馈】", llm.json_calls[0]["user_prompt"])
                    self.assertIn("历史显示记录", llm.json_calls[1]["user_prompt"])

    def test_exhausted_repair_cannot_deliver_the_history_record_as_plain_text(self):
        for streaming in (False, True):
            for text in (HISTORY_RECORD, "```text\n" + HISTORY_RECORD + "\n```"):
                with self.subTest(streaming=streaming, fenced=text.startswith("```")):
                    llm = ScriptedLLM([result({}, HISTORY_RECORD, fallback_used=True)], text=text)
                    _, output = run(build_engine(llm), streaming)
                    self.assertTrue(output.get("_transient_final_failure"))
                    self.assertNotIn("_final_recovery", output)
                    self.assertNotIn("_provider_output_raw", output)
                    self.assertNotIn(HISTORY_RECORD, output.get("speech", ""))
                    self.assertEqual(len(llm.text_calls), 1)

    def test_native_tool_decision_is_preserved_even_with_history_shaped_preface(self):
        decision = {"speech": "", NATIVE_TOOL_CALLS_FIELD: [{"name": "lookup", "arguments": {}}]}
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                llm = ScriptedLLM([result(decision, HISTORY_RECORD)])
                _, output = run(build_engine(llm), streaming)
                self.assertEqual(output[NATIVE_TOOL_CALLS_FIELD], decision[NATIVE_TOOL_CALLS_FIELD])
                self.assertEqual(len(llm.json_calls), 1)
                self.assertEqual(llm.text_calls, [])

    def test_normal_plain_text_and_json_discussing_history_remain_deliverable(self):
        quoted = {"speech": f'“{ASSISTANT_HISTORY_RECORD_HEADER}”用于阅读过去的回复。'}
        for streaming in (False, True):
            for parsed, raw, fallback in (
                ({}, "普通的纯文本答复。", True),
                (quoted, json.dumps(quoted, ensure_ascii=False), False),
            ):
                with self.subTest(streaming=streaming, raw=raw):
                    llm = ScriptedLLM([result(parsed, raw, fallback_used=fallback)])
                    _, output = run(build_engine(llm), streaming)
                    self.assertEqual(output["speech"], parsed.get("speech", raw))
                    self.assertNotIn("_transient_final_failure", output)
                    self.assertEqual(len(llm.json_calls), 1)
                    self.assertEqual(llm.text_calls, [])


if __name__ == "__main__":
    unittest.main()
