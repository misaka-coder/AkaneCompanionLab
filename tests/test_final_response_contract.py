"""Behavior contracts across final-response preparation and transports."""

from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

import config
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.model_request_control import ModelRequestCancelled
from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD


def build_engine(llm, *, context=None):
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.llm = llm
    engine._prepare_final_response_context = lambda **_kwargs: (
        context
        or {
            "system_prompt": "stable system",
            "user_prompt": "current input",
            "fallback": {"speech": "", "tool_call": None},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": True,
        }
    )
    engine._build_memcore_request_observer = lambda **_kwargs: None
    engine._resolve_turn_speaker_identity = lambda *_args: {"assistant_name": "Akane"}
    engine._normalize_final_output = lambda *, result, **_kwargs: dict(result or {})
    engine._attach_memory_annotation_truth = lambda *_args, **_kwargs: None
    engine._attach_tool_execution_receipts = lambda *_args: None
    engine._final_response_max_attempts = lambda _context: 1
    return engine


def generation_args():
    return {
        "session_id": "session",
        "profile_user_id": "owner",
        "user_message": "current input",
        "recent_raw": [],
        "recent_episodic_summaries": [],
        "recent_semantic_summaries": [],
        "confirmed_snippets": [],
        "now_ts": 0,
    }


def drain(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


class FinalResponseCancellationTests(unittest.TestCase):
    def test_plain_text_recovery_propagates_cancellation_in_both_transports(self):
        for mode in ("sync", "stream"):
            with self.subTest(mode=mode):

                class LLM:
                    def __init__(self):
                        self.metrics = []
                        self.text_calls = 0

                    def call_chat_json_result(self, **_kwargs):
                        return SimpleNamespace(parsed={}, raw_text='{"speech":', fallback_used=True)

                    def stream_chat_json(self, **kwargs):
                        yield from ()
                        return self.call_chat_json_result(**kwargs)

                    def call_chat_text(self, **_kwargs):
                        self.text_calls += 1
                        raise ModelRequestCancelled("turn_scope_revoked")

                    def record_metric(self, name):
                        self.metrics.append(name)

                llm = LLM()
                engine = build_engine(llm)
                with self.assertRaises(ModelRequestCancelled):
                    if mode == "stream":
                        drain(engine._stream_final_response(**generation_args()))
                    else:
                        engine._build_final_response(**generation_args())
                self.assertEqual(llm.text_calls, 1)
                self.assertNotIn("chat_final_service_failures", llm.metrics)
                self.assertNotIn("chat_final_plain_text_recoveries", llm.metrics)


class FinalResponsePreparationTests(unittest.TestCase):
    def test_real_context_delegate_reaches_provider_with_text_and_images(self):
        for transport in ("sync", "stream"):
            for request_mode in ("independent", "chat_prefix"):
                for client_mode in (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET):
                    for images in (None, [{"url": "data:image/png;base64,fixture-image"}]):
                        with self.subTest(transport=transport, request_mode=request_mode,
                                          client_mode=client_mode, images=bool(images)):
                            requests = []

                            class LLM:
                                def call_chat_json_result(self, **kwargs):
                                    requests.append(kwargs)
                                    return SimpleNamespace(parsed={"speech": "done"}, fallback_used=False)

                                def stream_chat_json(self, **kwargs):
                                    kwargs.pop("early_tool_call_validator")
                                    yield from ()
                                    return self.call_chat_json_result(**kwargs)

                            engine = build_engine(LLM())
                            context = engine._prepare_final_response_context()
                            # Keep the production request/context boundary. Only
                            # replace the downstream prompt stages and provider.
                            del engine._prepare_final_response_context
                            with (
                                patch.object(config, "MEMCORE_COMPACTION_REQUEST_MODE", request_mode),
                                patch("companion_v01.engine_services.response_builder.prepare_context",
                                      autospec=True, return_value=context) as prepare,
                            ):
                                args = {**generation_args(), "user_images": images,
                                        "client_context": ClientProtocolContext(effective_mode=client_mode)}
                                if transport == "sync":
                                    output = engine._build_final_response(**args)
                                else:
                                    _, output = drain(engine._stream_final_response(**args))
                            self.assertEqual(output["speech"], "done")
                            prepare.assert_called_once()
                            self.assertIs(prepare.call_args.kwargs["native_user_images"], images)
                            self.assertEqual(len(requests), 1)
                            self.assertIs(requests[0]["user_images"], images)

    def test_both_transports_keep_projection_identity_and_provider_inputs(self):
        observer = object()
        contexts = []
        requests = []
        projection = {
            "current_user_prompt": "frozen current input",
            "current_user_source_id": "current-source",
            "current_input_transient": True,
            "current_actor_relation": "group_member",
            "authorization_profile_user_id": "owner",
            "actor_stable_id": "actor",
            "actor_profile_user_id": "member",
        }

        class LLM:
            def call_chat_json_result(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(parsed={"speech": "done"}, fallback_used=False)

            def stream_chat_json(self, **kwargs):
                kwargs.pop("early_tool_call_validator")
                yield from ()
                return self.call_chat_json_result(**kwargs)

        for mode in ("sync", "stream"):
            engine = build_engine(LLM())
            original_prepare = engine._prepare_final_response_context

            def prepare(**kwargs):
                contexts.append(kwargs)
                return original_prepare(**kwargs)

            engine._prepare_final_response_context = prepare
            engine._build_memcore_request_observer = lambda **_kwargs: observer
            args = {
                **generation_args(),
                "request_projection_state": projection,
                "user_images": [{"url": "test-image"}],
                "chat_model_override": "test-model",
                "execution_target": {"provider": "test"},
                "character_pack_id": "character",
                "domain_profile_id": "domain",
                "prompt_scope": "group-event",
                "post_user_turns": [{"role": "tool", "content": "finished tool"}],
                "prompt_exclude_source_ids": ["excluded-source"],
            }
            if mode == "sync":
                engine._build_final_response(**args)
            else:
                drain(engine._stream_final_response(**args))
        self.assertEqual(contexts[0], contexts[1])
        self.assertEqual(requests[0], requests[1])
        for name in projection:
            if name != "current_user_prompt":
                self.assertEqual(contexts[0][name], projection[name])
        self.assertIs(contexts[0]["request_projection_state"], projection)
        self.assertTrue(contexts[0]["enable_native_tools"])
        self.assertEqual(requests[0]["user_prompt"], "frozen current input")
        self.assertIs(requests[0]["request_observer"], observer)
        self.assertEqual(requests[0]["user_images"], [{"url": "test-image"}])
        self.assertEqual(requests[0]["chat_model_override"], "test-model")

    def test_stream_prepares_on_first_advance_and_registers_cache_after_turn_start(self):
        trace = []

        class LLM:
            def register_cache_usage_scope(self, *_args):
                trace.append("cache")

            def stream_chat_json(self, **_kwargs):
                trace.append("provider")
                yield from ()
                return SimpleNamespace(parsed={"speech": "done"}, fallback_used=False)

        engine = build_engine(LLM())
        original_prepare = engine._prepare_final_response_context

        def prepare(**kwargs):
            trace.append("context")
            return original_prepare(**kwargs)

        def observe(**_kwargs):
            trace.append("observer")
            return None

        engine._prepare_final_response_context = prepare
        engine._build_memcore_request_observer = observe
        stream = engine._stream_final_response(**generation_args())
        self.assertEqual(trace, [])
        self.assertEqual(next(stream), {"type": "turn_start", "speaker": "Akane"})
        self.assertEqual(trace, ["context", "observer"])
        events, output = drain(stream)
        self.assertEqual(events, [])
        self.assertEqual(output["speech"], "done")
        self.assertEqual(trace, ["context", "observer", "cache", "provider"])

    def test_projection_failure_stops_before_observer_cache_or_provider(self):
        for mode in ("sync", "stream"):
            with self.subTest(mode=mode):
                engine = build_engine(
                    object(),
                    context={"memcore_projection_failure": {"reason": "projection_unavailable"}},
                )
                engine._build_memcore_request_observer = lambda **_kwargs: self.fail("unexpected observer")
                if mode == "sync":
                    output = engine._build_final_response(**generation_args())
                else:
                    events, output = drain(engine._stream_final_response(**generation_args()))
                    self.assertEqual(events, [])
                self.assertEqual(output["_memcore_failure"]["reason"], "projection_unavailable")
                self.assertTrue(output["_transient_final_failure"])


class FinalResponseStreamLifetimeTests(unittest.TestCase):
    def test_close_and_cancel_release_a_retained_provider_stream(self):
        for action in ("close", "cancel"):
            with self.subTest(action=action):

                class LLM:
                    def __init__(self):
                        self.closed = False
                        self.iterator = None

                    def stream_chat_json(self, **_kwargs):
                        def events():
                            try:
                                yield {"type": "ui_state", "emotion": "normal"}
                                yield {"type": "speech_chunk", "text": "next"}
                            finally:
                                self.closed = True

                        self.iterator = events()
                        return self.iterator

                llm = LLM()
                stream = build_engine(llm)._stream_final_response(**generation_args())
                self.assertEqual(next(stream)["type"], "turn_start")
                self.assertEqual(next(stream)["type"], "ui_state")
                self.assertFalse(llm.closed)
                if action == "close":
                    stream.close()
                else:
                    with self.assertRaises(ModelRequestCancelled):
                        stream.throw(ModelRequestCancelled("turn_scope_revoked"))
                closed_by_parent = llm.closed
                llm.iterator.close()
                self.assertTrue(closed_by_parent)


class FinalResponseFallbackProvenanceTests(unittest.TestCase):
    def test_transport_error_cannot_publish_a_nonempty_host_fallback(self):
        class LLM:
            def __init__(self):
                self.calls = []
                self.metrics = []

            def stream_chat_json(self, **_kwargs):
                yield from ()
                return SimpleNamespace(parsed={}, error="connection closed", fallback_used=True)

            def call_chat_json_result(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    parsed={"speech": "host default", "tool_call": None},
                    error="502 Bad Gateway",
                    fallback_used=False,
                )

            def record_metric(self, name):
                self.metrics.append(name)

        llm = LLM()
        events, output = drain(build_engine(llm)._stream_final_response(**generation_args()))
        self.assertEqual(output["_service_failure"]["reason"], "provider_unreachable")
        self.assertNotEqual(output["speech"], "host default")
        self.assertEqual(events[-1]["type"], "stream_error")
        self.assertEqual(len(llm.calls), 2)
        self.assertNotEqual(llm.calls[0]["prompt_cache_key"], "")
        self.assertEqual(llm.calls[1]["prompt_cache_key"], "")
        self.assertNotIn("chat_stream_nonstream_recoveries", llm.metrics)
        self.assertNotIn("chat_stream_uncached_recoveries", llm.metrics)

    def test_fallback_uses_the_same_external_text_validation_as_other_attempts(self):
        class LLM:
            def __init__(self):
                self.calls = 0

            def stream_chat_json(self, **_kwargs):
                yield from ()
                return SimpleNamespace(parsed={}, error="connection closed", fallback_used=True)

            def call_chat_json_result(self, **_kwargs):
                self.calls += 1
                parsed = {"speech": "short summary", "tool_call": None}
                return SimpleNamespace(
                    parsed=parsed, raw_text="complete report\n" + json.dumps(parsed), fallback_used=False
                )

        llm = LLM()
        events, output = drain(build_engine(llm)._stream_final_response(**generation_args()))
        self.assertTrue(output.get("_transient_final_failure"))
        self.assertNotIn("_provider_output_raw", output)
        self.assertEqual(events[-1]["type"], "stream_error")
        self.assertEqual(llm.calls, 1)

    def test_fallback_delivers_proven_model_text_and_preserves_native_tool_decisions(self):
        cases = (
            (
                {"speech": "complete model text", "tool_call": None},
                '{"speech":"complete model text","tool_call":null,"emotion":',
                True,
            ),
            ({}, "plain model answer", True),
            ({"speech": "tool preface", NATIVE_TOOL_CALLS_FIELD: [{"name": "lookup", "arguments": {}}]}, "", False),
        )
        for parsed, raw, parse_fallback in cases:
            with self.subTest(raw=raw, native=NATIVE_TOOL_CALLS_FIELD in parsed):

                class LLM:
                    def __init__(self):
                        self.metrics = []

                    def stream_chat_json(self, **_kwargs):
                        yield from ()
                        return SimpleNamespace(parsed={}, error="connection closed", fallback_used=True)

                    def call_chat_json_result(self, **_kwargs):
                        return SimpleNamespace(parsed=parsed, raw_text=raw, fallback_used=parse_fallback)

                    def record_metric(self, name):
                        self.metrics.append(name)

                llm = LLM()
                events, output = drain(build_engine(llm)._stream_final_response(**generation_args()))
                self.assertEqual(events, [
                    {"type": "turn_start", "speaker": "Akane"},
                    {"type": "turn_progress", "phase": "retrying_model", "transport": "nonstream"},
                ])
                self.assertNotIn("_transient_final_failure", output)
                self.assertNotIn("_provider_fallback", output)
                self.assertIn("chat_stream_nonstream_recoveries", llm.metrics)
                if NATIVE_TOOL_CALLS_FIELD in parsed:
                    self.assertEqual(output[NATIVE_TOOL_CALLS_FIELD], parsed[NATIVE_TOOL_CALLS_FIELD])
                    self.assertEqual(output["_native_preface_text"], "tool preface")
                    self.assertNotIn("_final_recovery", output)
                else:
                    self.assertEqual(output["speech"], parsed.get("speech") or raw)
                    self.assertEqual(output["_provider_output_raw"], raw)


class FinalResponseStreamIsolationTests(unittest.TestCase):
    def test_interleaved_requests_do_not_share_delivered_prefixes_or_recovery_feedback(self):
        class LLM:
            def __init__(self):
                self.attempts = {}
                self.requests = []

            def stream_chat_json(self, **kwargs):
                self.requests.append(kwargs)
                identity = "B" if kwargs["user_prompt"].startswith("B") else "A"
                attempt = self.attempts.get(identity, 0) + 1
                self.attempts[identity] = attempt
                prefix, tail = f"{identity} prefix.", f"{identity} tail."
                yield {"type": "speech_segment", "text": prefix}
                if attempt == 1:
                    return SimpleNamespace(parsed={}, raw_text='{"speech":', fallback_used=True)
                yield {"type": "speech_segment", "text": tail}
                return SimpleNamespace(parsed={"speech": prefix + "\n" + tail}, fallback_used=False)

        llm = LLM()
        engine = build_engine(llm)
        original_prepare = engine._prepare_final_response_context
        engine._prepare_final_response_context = lambda **kwargs: {
            **original_prepare(**kwargs),
            "user_prompt": kwargs["user_message"],
        }
        engine._final_response_max_attempts = lambda _context: 2
        streams = {
            name: engine._stream_final_response(**{**generation_args(), "user_message": name}) for name in ("A", "B")
        }
        for name, stream in streams.items():
            self.assertEqual(next(stream)["type"], "turn_start")
            self.assertEqual(next(stream), {"type": "speech_segment", "text": f"{name} prefix."})
        for name in ("B", "A"):
            events, output = drain(streams[name])
            self.assertEqual(events, [
                {"type": "turn_progress", "phase": "retrying_model", "attempt": 2},
                {"type": "speech_segment", "text": f"{name} tail."},
            ])
            self.assertEqual(output["speech"], f"{name} prefix.\n{name} tail.")
        for request in llm.requests[2:]:
            name = request["user_prompt"][0]
            other = "B" if name == "A" else "A"
            self.assertIn(f"- {name} prefix.", request["user_prompt"])
            self.assertNotIn(f"- {other} prefix.", request["user_prompt"])


if __name__ == "__main__":
    unittest.main()
