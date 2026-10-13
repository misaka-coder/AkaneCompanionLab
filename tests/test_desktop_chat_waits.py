"""Slow desktop requests stay observable and do not silently multiply timeouts."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import Request
from fastapi.responses import StreamingResponse

from companion_v01.engine import _ContextBoundGenerator
from companion_v01.model_request_control import ModelRequestCancelled, dispatch_model_request
from companion_v01.routes.think import build_think_router
from companion_v01.routes.think_stream import iterate_turn_events
from companion_v01.turn_coordination import current_cancellation_check
from tests.test_backend_route_modules import FakeGuard, FakeRuntimeMetrics
from tests import test_stream_final_nonstream_fallback as fallback_helpers


class DesktopChatTimeoutTests(unittest.TestCase):
    def test_stream_timeout_stops_before_nonstream_or_uncached_replay(self):
        for error in ("Request timed out.", "ReadTimeout", "APITimeoutError: request timeout"):
            with self.subTest(error=error):
                llm = fallback_helpers._FakeLLM(stream_error=error)
                harness = fallback_helpers.StreamFinalNonstreamFallbackTests()
                events, frame = harness._run(harness._build_engine(llm))
                self.assertEqual(len(llm.stream_calls), 1)
                self.assertFalse(llm.nonstream_calls)
                self.assertFalse(any(event["type"] == "turn_progress" for event in events))
                self.assertTrue(frame["_transient_final_failure"])
                self.assertEqual(frame["_service_failure"]["reason"], "provider_timeout")
                self.assertIn("消息已收到", frame["speech"])

    def test_timeout_during_nonstream_recovery_does_not_start_uncached_replay(self):
        llm = fallback_helpers._FakeLLM(stream_error="502 Bad Gateway")

        def timed_out(**kwargs):
            llm.nonstream_calls.append(kwargs)
            return SimpleNamespace(parsed={}, raw_text="", error="Request timed out.")

        llm.call_chat_json_result = timed_out
        harness = fallback_helpers.StreamFinalNonstreamFallbackTests()
        events, frame = harness._run(harness._build_engine(llm))
        self.assertEqual(len(llm.nonstream_calls), 1)
        self.assertEqual(frame["_service_failure"]["reason"], "provider_timeout")
        self.assertEqual(
            [event.get("transport") for event in events if event["type"] == "turn_progress"], ["nonstream"]
        )

    def test_retry_notice_precedes_the_recovery_provider_call(self):
        llm = fallback_helpers._FakeLLM(
            stream_error="502 Bad Gateway", nonstream_result={"speech": "recovered", "tool_call": None}
        )
        harness = fallback_helpers.StreamFinalNonstreamFallbackTests()
        engine = harness._build_engine(llm)
        stream = engine._stream_final_response(
            session_id="s",
            profile_user_id="u",
            user_message="hello",
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            confirmed_snippets=[],
            now_ts=0,
        )
        self.assertEqual(next(stream)["type"], "turn_start")
        self.assertEqual(next(stream)["phase"], "retrying_model")
        self.assertFalse(llm.nonstream_calls)
        try:
            next(stream)
        except StopIteration as end:
            self.assertEqual(end.value["speech"], "recovered")
        else:
            self.fail("unexpected extra stream event")
        self.assertEqual(len(llm.nonstream_calls), 1)


class DesktopChatWaitTests(unittest.IsolatedAsyncioTestCase):
    async def response(self, engine, *, coordinator=None):
        guard = FakeGuard()
        router = build_think_router(
            engine=engine,
            public_guard=guard,
            runtime_metrics=FakeRuntimeMetrics(),
            log_event=lambda *_args, **_kwargs: None,
            turn_coordinator=coordinator,
        )
        endpoint = next(route.endpoint for route in router.routes if route.path == "/think")

        async def receive():
            return {"type": "http.request", "body": b'{"user_id":"isolated","message":"test"}', "more_body": False}

        response = await endpoint(Request({"type": "http", "method": "POST", "path": "/think", "headers": []}, receive))
        return response.body_iterator, guard

    async def test_receipt_is_readable_before_the_session_lock_is_acquired(self):
        entered = asyncio.Event()
        gate = asyncio.Event()

        class Coordinator:
            @asynccontextmanager
            async def hold(self, *_args, **_kwargs):
                entered.set()
                await gate.wait()
                yield "isolated"

        engine = SimpleNamespace(process_turn_stream=lambda payload: iter(()))
        body, guard = await self.response(engine, coordinator=Coordinator())
        try:
            first = json.loads(await asyncio.wait_for(anext(body), 0.5))
            self.assertEqual(first["type"], "stream_start")
            self.assertEqual(first["phase"], "received")
            pending = asyncio.create_task(anext(body))
            await asyncio.wait_for(entered.wait(), 0.5)
            self.assertFalse(pending.done())
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        finally:
            await body.aclose()
        self.assertEqual(guard.released, 1)

    async def test_heartbeats_do_not_advance_a_blocked_model_twice_and_disconnect_closes_it(self):
        entered = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        provider_closed = threading.Event()
        calls = []
        visibility = ContextVar("isolated_desktop_turn", default="caller")

        class ProviderStream:
            def close(self):
                provider_closed.set()

        def create(**payload):
            calls.append(payload)
            entered.set()
            release.wait(3)
            return ProviderStream()

        def engine_events(_payload):
            token = visibility.set("engine")
            try:
                yield {"type": "turn_start"}
                self.assertEqual(visibility.get(), "engine")
                dispatch_model_request(create, {"stream": True}, current_cancellation_check())
            except ModelRequestCancelled:
                return
            finally:
                visibility.reset(token)
                closed.set()

        body, guard = await self.response(
            SimpleNamespace(process_turn_stream=lambda payload: _ContextBoundGenerator(engine_events(payload)))
        )
        try:
            self.assertEqual(json.loads(await anext(body))["type"], "stream_start")
            self.assertEqual(json.loads(await anext(body))["type"], "turn_start")
            with patch("companion_v01.routes.think_stream.STREAM_PROGRESS_INTERVAL_SECONDS", 0.01):
                for _ in range(3):
                    event = json.loads(await asyncio.wait_for(anext(body), 0.5))
                    self.assertEqual(event["type"], "turn_progress")
                    self.assertEqual(event["phase"], "waiting_response")
                    self.assertNotIn("message", event)
            self.assertTrue(entered.is_set())
            self.assertEqual(len(calls), 1)
            self.assertEqual(visibility.get(), "caller")
            await asyncio.wait_for(body.aclose(), 0.5)
            self.assertTrue(closed.is_set())
            self.assertEqual(guard.released, 1)
        finally:
            release.set()
            await body.aclose()
        self.assertTrue(await asyncio.to_thread(provider_closed.wait, 0.5))

    async def test_cancellation_while_waiting_for_an_event_drains_the_step(self):
        started = threading.Event()
        closed = threading.Event()

        def events():
            try:
                check = current_cancellation_check()
                started.set()
                while not check():
                    threading.Event().wait(0.005)
                return
                yield  # Make the deliberately blocked implementation a generator.
            finally:
                closed.set()

        stream = iterate_turn_events(events)
        pending = asyncio.create_task(anext(stream))
        self.assertTrue(await asyncio.to_thread(started.wait, 0.5))
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        self.assertTrue(closed.is_set())

    async def test_asgi_disconnect_revokes_work_and_releases_the_guard(self):
        disconnected = asyncio.Event()
        closed = threading.Event()

        def engine_events(_payload):
            try:
                yield {"type": "turn_start"}
                check = current_cancellation_check()
                while not check():
                    threading.Event().wait(0.005)
            finally:
                closed.set()

        body, guard = await self.response(
            SimpleNamespace(process_turn_stream=lambda payload: _ContextBoundGenerator(engine_events(payload)))
        )

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            chunk = message.get("body", b"")
            if chunk and json.loads(chunk)["type"] == "turn_progress":
                disconnected.set()

        with patch("companion_v01.routes.think_stream.STREAM_PROGRESS_INTERVAL_SECONDS", 0.01):
            await asyncio.wait_for(
                StreamingResponse(body)({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send), 0.5
            )
        self.assertTrue(closed.is_set())
        self.assertEqual(guard.released, 1)


if __name__ == "__main__":
    unittest.main()
