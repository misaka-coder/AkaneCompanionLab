"""Missing upstream cache usage is unknown, not a confirmed cache miss."""
import threading
import unittest
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.cache_usage import LOCAL_TZ, read_daily_cache_usage
from services.llm_client import _AnthropicStream, _build_openai_style_response
from services.gemini_native_client import _gemini_usage


class CacheUsageReportingTests(unittest.TestCase):
    def test_request_model_is_recorded_separately_from_upstream_alias(self):
        with TemporaryDirectory() as tmp:
            runtime = LLMRuntime.__new__(LLMRuntime)
            runtime.log_dir = Path(tmp)
            runtime._should_record_prompt_audit = lambda _: False
            runtime.register_cache_usage_scope("chat:final:test", "a" * 64, "qq_text")
            response = SimpleNamespace(model="gemini-pro-default", usage={
                "prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 973}})
            runtime._record_stream_cache_metrics(response, None, prompt_cache_key="chat:final:test",
                                                model="gemini-3.1-pro-high")
            runtime._record_cache_metrics(response, prompt_cache_key="chat:final:test",
                                          requested_model="gemini-3.8-flash")
            report = read_daily_cache_usage(Path(tmp), day=datetime.now(LOCAL_TZ).date(), scope_hash="a" * 64)
            self.assertEqual({r['model'] for r in report['models']}, {"gemini-3.1-pro-high", "gemini-3.8-flash"})
            self.assertEqual(report['requests'], 2)
            self.assertEqual(report['ratio'], .973)
            records = [json.loads(line) for p in (Path(tmp) / "cache_usage").glob("*.jsonl")
                       for line in p.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all(r['model'] == "gemini-pro-default" for r in records))
            self.assertEqual(records[0]['requested_model'], "gemini-3.1-pro-high")

    def test_lightweight_daily_ledger_works_without_request_audit(self):
        with TemporaryDirectory() as tmp:
            runtime = LLMRuntime.__new__(LLMRuntime)
            runtime.log_dir = Path(tmp)
            runtime._should_record_prompt_audit = lambda _: False
            runtime.register_cache_usage_scope("chat:final:test", "a" * 64, "qq_text")
            runtime._record_cache_metrics({"model": "one", "usage": {
                "prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 980}}},
                prompt_cache_key="chat:final:test")
            # A completed response without any usage is also visible as unknown.
            runtime._record_cache_metrics({"model": "one"}, prompt_cache_key="chat:final:test")
            report = read_daily_cache_usage(Path(tmp), day=datetime.now(LOCAL_TZ).date(), scope_hash="a" * 64)
            self.assertEqual((report["requests"], report["reported"], report["unknown"]), (2, 1, 1))
            self.assertEqual(report["ratio"], .98)
            self.assertFalse((Path(tmp) / "llm_prompt_audit").exists())
            self.assertEqual(len(list((Path(tmp) / "cache_usage").glob("*.jsonl"))), 1)

    def test_stream_records_cumulative_usage_once_and_missing_usage_once(self):
        class Stream(list):
            usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 970}
            model = "test"

        for wrapper_usage in (True, False, None):
            with self.subTest(wrapper_usage=wrapper_usage):
                runtime = LLMRuntime.__new__(LLMRuntime)
                runtime._build_completion_kwargs = lambda **_: {}
                runtime._should_record_prompt_audit = lambda _: True
                records = []
                runtime._append_prompt_audit_record = records.append
                runtime._close_stream = lambda _: None
                chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
                                          usage=Stream.usage if wrapper_usage is not None else None, model="test")
                          for text in ('{"speech":"好', '的。"}')]
                stream = Stream(chunks) if wrapper_usage else chunks
                runtime._create_completion = lambda **_: stream
                runtime.register_cache_usage_scope("chat:final:test", "a" * 64, "qq_text")
                list(runtime._stream_chat_json(bundle=SimpleNamespace(model="test"), system_prompt="", user_prompt="",
                     fallback={"speech": ""}, temperature=0, early_tool_call_validator=None, prompt_cache_key="chat:final:test"))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["reported_input_tokens"], 0 if wrapper_usage is None else 1000)
                self.assertEqual(records[0]["cache_read_reported"], wrapper_usage is not None)

    def test_anthropic_total_input_includes_cached_reads_and_writes(self):
        usage = {"input_tokens": 20, "cache_read_input_tokens": 970, "cache_creation_input_tokens": 10}
        response = _build_openai_style_response({"usage": usage}, "test")
        self.assertEqual(response.usage.prompt_tokens, 1000)
        stream = _AnthropicStream(response=None, model="test")
        stream._capture_stream_usage([json.dumps({"message": {"usage": usage}})])
        stream._capture_stream_usage([json.dumps({"usage": {"output_tokens": 42}})])
        self.assertEqual(stream.usage.prompt_tokens, 1000)
        self.assertEqual(stream.usage.completion_tokens, 42)
        self.assertEqual(stream.usage.cache_read_input_tokens, 970)

    def test_usage_audit_distinguishes_missing_null_zero_and_hit(self):
        samples = [
            ({}, False, None),
            ({"prompt_tokens_details": {"cached_tokens": None}}, False, None),
            ({"prompt_tokens_details": {"cached_tokens": 0}}, True, 0.0),
            ({"prompt_cache_hit_tokens": 970}, True, .97),
            ({"input_tokens_details": {"cached_tokens": 980}}, True, .98),
            ({"cache_read_input_tokens": 0, "_akane_cache_read_reported": False}, False, None),
        ]
        for extra, reported, ratio in samples:
            with self.subTest(extra=extra):
                runtime = LLMRuntime.__new__(LLMRuntime)
                runtime._metrics_lock, runtime._metrics = threading.RLock(), {}
                records = []
                runtime._append_prompt_audit_record = records.append
                runtime._should_record_prompt_audit = lambda _: True
                runtime.register_cache_usage_scope("chat:final:test", "a" * 64, "qq_text")
                runtime._record_cache_metrics({"model": "test", "usage": {"prompt_tokens": 1000, **extra}},
                                              prompt_cache_key="chat:final:test")
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["cache_read_reported"], reported)
                self.assertEqual(records[0]["cache_hit_ratio"], ratio)
                self.assertEqual(records[0]["conversation_scope_hash"], "a" * 64)
                self.assertEqual(records[0]["client_mode"], "qq_text")
                self.assertEqual(runtime._metrics.get("cache_unreported_calls", 0), int(not reported))

    def test_native_normalization_preserves_field_presence(self):
        for metadata, reported in (({}, False), ({"cachedContentTokenCount": 0}, True),
                                   ({"cachedContentTokenCount": 97}, True)):
            usage = _gemini_usage({"promptTokenCount": 100, **metadata})
            self.assertEqual(LLMRuntime._cache_read_reported(usage), reported)
