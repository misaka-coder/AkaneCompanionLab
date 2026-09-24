"""Revoked completion scopes cannot start provider fallback requests."""
import json
import threading
from types import SimpleNamespace
import unittest

from companion_v01.llm_runtime import LLMRuntime, ModelRequestCancelled
from companion_v01.turn_coordination import cancellation_scope
from tests import test_llm_runtime_stream as runtime_helpers
from tests import test_stream_final_nonstream_fallback as engine_helpers
from tests.test_turn_mainline_contract import _Harness, _tool_round_output


def runtime_with_provider(create, *, responses=False):
    runtime = runtime_helpers.TopLevelJSONStreamTapTests._sync_runtime('{"speech":"completed"}')
    del runtime._create_completion  # Exercise the real SDK dispatch/retry path.
    endpoint = SimpleNamespace(create=create)
    bundle = SimpleNamespace(client=SimpleNamespace(
        chat=SimpleNamespace(completions=endpoint), responses=endpoint))
    runtime._request_bundle = lambda **kwargs: bundle
    runtime._is_responses_protocol = lambda _bundle: responses
    runtime._record_responses_request_audit_if_enabled = lambda **kwargs: None
    runtime._adapt_responses_result = lambda value: value
    runtime._build_completion_kwargs = lambda **kwargs: {
        "model": "test", "messages": [{"role": "user", "content": "test"}],
        "stream": kwargs.get("stream", False), "prompt_cache_key": "test-cache"}
    runtime._metrics_lock = threading.RLock()
    runtime._metrics = {}
    del runtime._record_metric
    runtime._capture_runtime_error = lambda *args, **kwargs: None
    return runtime, bundle


class CompletionModelCancellationTests(unittest.TestCase):
    def test_cache_hint_retry_checks_revocation_for_both_provider_protocols(self):
        for responses in (False, True):
            for error in (TypeError, RuntimeError):
                for revoke in (False, True):
                    with self.subTest(responses=responses, error=error, revoke=revoke):
                        revoked = threading.Event()
                        calls = []
                        def create(**payload):
                            calls.append(payload)
                            if len(calls) == 1:
                                if revoke:
                                    revoked.set()
                                raise error("unknown parameter prompt_cache_key")
                            return SimpleNamespace()
                        runtime, bundle = runtime_with_provider(create, responses=responses)
                        payload = runtime._build_completion_kwargs()
                        with cancellation_scope(revoked.is_set):
                            if revoke:
                                with self.assertRaisesRegex(ModelRequestCancelled, "turn_scope_revoked"):
                                    runtime._create_completion(bundle=bundle, payload=payload)
                            else:
                                runtime._create_completion(bundle=bundle, payload=payload)
                        self.assertEqual(len(calls), 1 if revoke else 2)
                        if not revoke:
                            self.assertNotIn("prompt_cache_key", calls[1])

    def test_revoked_scope_propagates_through_public_text_json_and_stream_wrappers(self):
        for method in ("call_chat_text", "call_chat_json_result", "stream_chat_json"):
            with self.subTest(method=method):
                calls = []
                runtime, _ = runtime_with_provider(lambda **payload: calls.append(payload))
                kwargs = {"system_prompt": "system", "user_prompt": "user"}
                if method != "call_chat_text":
                    kwargs["fallback"] = {"speech": "fallback"}
                with cancellation_scope(lambda: True):
                    with self.assertRaises(ModelRequestCancelled):
                        result = getattr(runtime, method)(**kwargs)
                        if method == "stream_chat_json":
                            list(result)
                self.assertFalse(calls)
                self.assertFalse(runtime.snapshot_metrics().get("errors"))
                self.assertFalse(runtime.snapshot_metrics().get("chat_json_fallbacks"))

    def test_json_decode_recovery_cannot_start_a_stream_after_revocation(self):
        revoked = threading.Event()
        calls = []
        def create(**payload):
            calls.append(payload)
            revoked.set()
            raise json.JSONDecodeError("Extra data", "{}{}", 2)
        runtime, _ = runtime_with_provider(create)
        with cancellation_scope(revoked.is_set):
            with self.assertRaises(ModelRequestCancelled):
                runtime.call_chat_json_result(system_prompt="system", user_prompt="user", fallback={})
        self.assertEqual(len(calls), 1)
        self.assertFalse(runtime.snapshot_metrics().get("chat_nonstream_stream_recoveries"))
        self.assertFalse(runtime.snapshot_metrics().get("errors"))

    def test_real_engine_stops_provider_retry_and_stream_to_sync_fallback(self):
        for stream, completed_tool in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(stream=stream, completed_tool=completed_tool):
                revoked = threading.Event()
                calls = []
                def create(**payload):
                    calls.append(payload)
                    revoked.set()
                    raise RuntimeError("502 Bad Gateway") if stream else TypeError("prompt_cache_key")
                runtime, _ = runtime_with_provider(create)
                harness = _Harness([])
                transport = engine_helpers.StreamFinalNonstreamFallbackTests()._build_engine(runtime)
                engine = harness.engine
                engine.llm = runtime
                for name in ("_prepare_final_response_context", "_resolve_turn_speaker_identity",
                             "_normalize_final_output", "_attach_tool_execution_receipts"):
                    setattr(engine, name, getattr(transport, name))
                del engine._build_final_response
                del engine._stream_final_response
                if completed_tool:
                    sync_response = engine._build_final_response
                    stream_response = engine._stream_final_response
                    first = [_tool_round_output("", "lookup", "lookup-before-cancel")]
                    def sync_gen(**kwargs):
                        return first.pop() if first else sync_response(**kwargs)
                    def stream_gen(**kwargs):
                        if first:
                            return first.pop()
                        return (yield from stream_response(**kwargs))
                    engine._build_final_response = sync_gen
                    engine._stream_final_response = stream_gen
                with cancellation_scope(revoked.is_set):
                    if stream:
                        events = harness.run_stream(harness.payload())
                        output = next(e["payload"] for e in events if e["type"] == "turn_stopped")
                        self.assertFalse(any(e["type"] == "final" for e in events))
                    else:
                        output = harness.run_sync(harness.payload())
                self.assertEqual(output["status"], "stopped")
                self.assertEqual(output["speech"], "")
                completed_events = [e for e in output["tool_events"]
                                    if e.get("type") == "tool_execution_start"]
                self.assertEqual(len(completed_events), int(completed_tool))
                self.assertEqual(len(calls), 1)
                self.assertFalse(harness.store.eval_turns)
                self.assertFalse(runtime.snapshot_metrics().get("chat_stream_nonstream_recoveries"))
                self.assertEqual(runtime.snapshot_metrics().get("errors", 0), int(stream))

    def test_auxiliary_provider_cancel_returns_stopped_and_aborts_open_guard(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                harness = _Harness([])
                aborted = []
                harness.engine._abort_open_memcore_turn_guard = lambda **kwargs: aborted.append(kwargs)
                runtime, _ = runtime_with_provider(lambda **kwargs: self.fail("provider called"))
                harness.engine._run_pre_retrieval_pipeline = lambda **kwargs: runtime.call_chat_json_result(
                    system_prompt="system", user_prompt="user", fallback={})
                with cancellation_scope(lambda: True):
                    if stream:
                        events = harness.run_stream(harness.payload())
                        output = next(e["payload"] for e in events if e["type"] == "turn_stopped")
                    else:
                        output = harness.run_sync(harness.payload())
                self.assertEqual(output["status"], "stopped")
                self.assertEqual(aborted, [{"reason": "turn_scope_revoked"}])
