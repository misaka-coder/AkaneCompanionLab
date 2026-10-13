from __future__ import annotations

import asyncio
from concurrent.futures import Future
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from capcore import CapabilityResult, InvocationContext

from companion_v01 import plugin_generation_callbacks as callbacks
from companion_v01.plugin_api import NotificationIntent, PluginResourceResult
from companion_v01.plugin_generation_codec import capability_result_to_wire, notification_intent_to_wire
from companion_v01.plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_resources import ResourceInvocation, generation_request_id


PLUGIN_ID = "test.callback.boundaries"
CONTEXT = InvocationContext("owner", "session", "diagnostics")
INTENT = NotificationIntent("qq_text", "user:123", "fixture", "callback-fixture")


def _request(callback="capability.invoke", **extra):
    return {
        "protocol": PLUGIN_GENERATION_PROTOCOL, "generation_id": "generation",
        "callback_id": "a" * 32, "callback": callback,
        "invocation_id": "request", "capability_id": "test.peer.read", "arguments": {},
        **extra,
    }


def _worker_call(kind, emit, pending, invocation):
    options = {"generation_id": "generation", "emit": emit, "pending": pending}
    if kind == "capability":
        return callbacks.GenerationCapabilityProvider(**options).invoke("test.peer.read", {}, invocation=invocation)
    if kind == "events":
        return callbacks.GenerationEventsProvider(**options).request("emit", {}, invocation=invocation)
    if kind == "tasks":
        return callbacks.GenerationTaskProvider(**options).request("status", {}, invocation=invocation)
    if kind == "connection":
        return callbacks.GenerationConnectionProvider(**options).resolve("image_generation", invocation=invocation)
    if kind == "resource":
        return callbacks.GenerationResourceProvider(**options).open("attachment", invocation=invocation)
    return callbacks.GenerationNotificationPort(**options).send(INTENT)


class CallbackBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def router(self, output=None):
        router = callbacks.GenerationHostCallbackRouter(
            generation_id="generation", start_timeout_seconds=0.1,
            write_response=(output if output is not None else []).append,
        )
        self.addCleanup(router.close)
        return router

    async def test_closed_router_rejects_late_callbacks_and_new_invocations(self):
        output, scheduled = [], []
        router = self.router(output)
        provider = SimpleNamespace(invoke=AsyncMock(), send=AsyncMock())
        router.bind_capability_provider(provider)
        router.bind_notification_port(provider)
        router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT,
                                permissions=("capability.invoke",))
        invocation = router._invocations["request"]
        router.close()
        router._schedule = lambda cid, coroutine, **kwargs: scheduled.append(coroutine)
        try:
            router.dispatch(_request())
            router.dispatch(_request("notification.send", callback_id="b" * 32,
                                     intent=notification_intent_to_wire(INTENT)))
            self.assertFalse(invocation.active)
            self.assertEqual(scheduled, [])
            self.assertEqual([item["reason"] for item in output], ["callback_router_closed"] * 2)
            for begin in (router.begin_invocation, router.begin_service_invocation):
                with self.assertRaisesRegex(RuntimeError, "callback_router_closed"):
                    begin("late", plugin_id=PLUGIN_ID, context=CONTEXT)
        finally:
            for coroutine in scheduled:
                coroutine.close()
            await router.finish_invocation("request")
        self.assertEqual(router._invocations, {})

    async def test_failed_invocation_close_retains_revoked_scope_for_retry(self):
        router = self.router()
        router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT)
        invocation = router._invocations["request"]
        invocation.aclose = AsyncMock(side_effect=RuntimeError("scope_close_failed"))
        with self.assertRaisesRegex(RuntimeError, "scope_close_failed"):
            await router.finish_invocation("request")
        self.assertIs(router._invocations.get("request"), invocation)
        self.assertFalse(invocation.active)
        invocation.aclose.side_effect = None
        await router.finish_invocation("request")
        self.assertEqual(invocation.aclose.await_count, 2)
        self.assertNotIn("request", router._invocations)

    async def test_submission_failure_removes_unstarted_callback_control(self):
        output = []
        router = self.router(output)
        router.bind_capability_provider(SimpleNamespace(invoke=AsyncMock()))
        router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT,
                                permissions=("capability.invoke",))
        try:
            with patch.object(callbacks.asyncio, "run_coroutine_threadsafe", side_effect=RuntimeError("loop_closed")):
                router.dispatch(_request())
            self.assertEqual(output[-1]["reason"], "capability_host_unavailable")
            self.assertEqual(router._capability_callbacks, {})
            self.assertEqual(router._futures, {})
        finally:
            await router.finish_invocation("request")

    async def test_worker_emit_failure_clears_every_port_pending_record(self):
        invocation = ResourceInvocation(PLUGIN_ID, CONTEXT)
        error = RuntimeError("control_lane_closed")
        token = generation_request_id.set("request")
        try:
            for kind in ("capability", "events", "tasks", "connection", "resource", "notification"):
                with self.subTest(kind=kind):
                    pending = {}
                    with self.assertRaises(RuntimeError) as caught:
                        await _worker_call(kind, Mock(side_effect=error), pending, invocation)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(pending, {})
        finally:
            generation_request_id.reset(token)

    async def test_resource_callback_is_drained_before_scope_files_are_removed(self):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        output, scheduled = [], []
        router = self.router(output)
        seen_files = []

        async def open_resource(target, *, invocation):
            invocation.temporary = tempfile.TemporaryDirectory()
            path = Path(invocation.temporary.name) / "input.txt"
            path.write_text("fixture", encoding="utf-8")
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
            seen_files.append(path.is_file())
            return PluginResourceResult(True, "ready", path=path, file_size=7)

        router.bind_resource_provider(SimpleNamespace(open=open_resource))
        router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT,
                                permissions=("resource.read",))
        invocation = router._invocations["request"]
        router._schedule = lambda cid, coroutine, **kwargs: scheduled.append(asyncio.create_task(coroutine))
        router.dispatch(_request("resource.open", target="attachment"))
        await asyncio.wait_for(entered.wait(), 2)
        work_dir = Path(invocation.temporary.name)
        closing = asyncio.create_task(router.finish_invocation("request"))
        try:
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            await asyncio.wait_for(cleaning.wait(), 2)
            self.assertTrue(work_dir.is_dir())
            release.set()
            await asyncio.gather(*scheduled, closing)
            self.assertEqual(seen_files, [True])
            self.assertFalse(work_dir.exists())
            self.assertEqual(output[-1]["result"]["reason"], "resource_invocation_expired")
            self.assertEqual(output[-1]["result"]["path"], "")
        finally:
            release.set()
            for task in scheduled:
                task.cancel()
            await asyncio.gather(*scheduled, closing, return_exceptions=True)

    async def test_duplicate_submission_is_atomic_across_reader_threads(self):
        router = self.router()
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        second_submission = threading.Event()
        calls, errors = [], []
        loop = asyncio.get_running_loop()

        async def work():
            return None

        def submit(coroutine, target_loop):
            calls.append(target_loop)
            if len(calls) > 1:
                second_submission.set()
            entered.set()
            release.wait(timeout=2)
            coroutine.close()
            return Future()

        def schedule(second=False):
            if second:
                second_started.set()
            try:
                router._schedule("a" * 32, work(), loop=loop, unavailable_reason="unavailable")
            except BaseException as error:
                errors.append(type(error).__name__)

        first = threading.Thread(target=schedule)
        second = threading.Thread(target=schedule, args=(True,))
        with patch.object(callbacks.asyncio, "run_coroutine_threadsafe", side_effect=submit):
            first.start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                second.start()
                self.assertTrue(await asyncio.to_thread(second_started.wait, 2))
                await asyncio.to_thread(second_submission.wait, 0.1)
            finally:
                release.set()
                await asyncio.to_thread(first.join, 2)
                if second.ident is not None:
                    await asyncio.to_thread(second.join, 2)
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)

    async def test_duplicate_failure_response_is_written_outside_router_lock(self):
        router = self.router()
        router._futures["a" * 32] = Future()

        def write_response(frame):
            acquired = router._lock.acquire(blocking=False)
            self.assertTrue(acquired)
            if acquired:
                router._lock.release()

        router._write_response = write_response

        async def work():
            return None

        router._schedule("a" * 32, work(), loop=asyncio.get_running_loop(), unavailable_reason="unavailable")

    async def test_starting_fallback_thread_is_reused_before_loop_is_ready(self):
        router = self.router()
        thread = Mock()
        thread.is_alive.return_value = True
        thread.join.side_effect = lambda **kwargs: setattr(thread.is_alive, "return_value", False)
        with patch.object(callbacks.threading, "Thread", return_value=thread) as constructor, \
                patch.object(router._fallback_loop_ready, "wait", return_value=False):
            self.assertIsNone(router._ensure_fallback_loop())
            self.assertIsNone(router._ensure_fallback_loop())
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(thread.start.call_count, 1)

    async def test_fallback_thread_start_failure_returns_truthful_failure(self):
        output = []
        router = self.router(output)
        router.bind_notification_port(SimpleNamespace(send=AsyncMock()))
        router._notification_loop = None
        thread = Mock()
        thread.start.side_effect = RuntimeError("thread_start_failed")
        thread.is_alive.return_value = False
        with patch.object(callbacks.threading, "Thread", return_value=thread):
            router.dispatch(_request("notification.send", intent=notification_intent_to_wire(INTENT)))
        self.assertEqual(output[-1]["reason"], "notification_host_unavailable")
        self.assertIsNone(router._fallback_thread)
        self.assertEqual(router._futures, {})

    async def test_close_timeout_keeps_fallback_ownership_and_retry_stops_once(self):
        router = self.router()
        loop, thread = Mock(), Mock()
        loop.is_closed.return_value = False
        thread.is_alive.return_value = True
        router._fallback_loop, router._fallback_thread = loop, thread
        try:
            with self.assertRaisesRegex(RuntimeError, "callback_cleanup_incomplete"):
                router.close()
            self.assertIs(router._fallback_thread, thread)
            self.assertIs(router._fallback_loop, loop)
            thread.is_alive.return_value = False
            router.close()
            self.assertIsNone(router._fallback_thread)
            self.assertEqual(loop.call_soon_threadsafe.call_count, 1)
        finally:
            thread.is_alive.return_value = False

    async def test_worker_acknowledged_cancellation_retains_real_terminal_result(self):
        invocation = ResourceInvocation(PLUGIN_ID, CONTEXT)
        token = generation_request_id.set("request")
        try:
            for kind in ("capability", "events", "tasks"):
                with self.subTest(kind=kind):
                    pending, frames = {}, []
                    call = asyncio.create_task(_worker_call(kind, frames.append, pending, invocation))
                    await asyncio.sleep(0)
                    callback_id = frames[0]["callback_id"]
                    try:
                        call.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(call.done())
                        self.assertIn(callback_id, pending)
                        self.assertEqual(frames[-1]["type"], "callback_cancel")
                        call.cancel()
                        await asyncio.sleep(0)
                        value = capability_result_to_wire(CapabilityResult(is_error=False, status="ok", content=42)) \
                            if kind == "capability" else {"status": "completed", "complete": True}
                        pending[callback_id].set_result({"ok": True, "result": value})
                        result = await call
                        self.assertEqual(result.content if kind == "capability" else result["status"],
                                         42 if kind == "capability" else "completed")
                        self.assertEqual(pending, {})
                    finally:
                        future = pending.get(callback_id)
                        if future is not None and not future.done():
                            future.set_result({"ok": False, "reason": "fixture_cleanup"})
                        await asyncio.gather(call, return_exceptions=True)
        finally:
            generation_request_id.reset(token)

    async def test_private_worker_cancellation_releases_pending_and_sends_signal(self):
        invocation = ResourceInvocation(PLUGIN_ID, CONTEXT)
        token = generation_request_id.set("request")
        try:
            for kind in ("resource", "connection", "notification"):
                with self.subTest(kind=kind):
                    pending, frames = {}, []
                    call = asyncio.create_task(_worker_call(kind, frames.append, pending, invocation))
                    await asyncio.sleep(0)
                    call.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await call
                    self.assertEqual(frames[-1]["type"], "callback_cancel")
                    self.assertEqual(pending, {})
        finally:
            generation_request_id.reset(token)

    async def test_callback_starting_after_close_cleans_missing_control_without_calling_provider(self):
        for kind in ("capability.invoke", "events.request", "tasks.request"):
            with self.subTest(kind=kind):
                output, scheduled = [], []
                router = self.router(output)
                provider = SimpleNamespace(invoke=AsyncMock(), request=AsyncMock())
                router.bind_capability_provider(provider)
                router.bind_events_provider(provider)
                router.bind_tasks_provider(provider)
                router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT,
                                        permissions=("capability.invoke", "event.emit"))
                invocation = router._invocations["request"]
                router._schedule = lambda cid, coroutine, **kwargs: scheduled.append(coroutine)
                router.dispatch(_request(kind, operation="emit" if kind == "events.request" else "status", payload={}))
                # Model a submitted task that reached registration after its
                # concurrent future was cancelled and discarded by close.
                future = Future()
                router._futures["a" * 32] = future
                future.add_done_callback(lambda done: router._discard("a" * 32, done))
                router.close()
                try:
                    await scheduled[0]
                    provider.invoke.assert_not_called()
                    provider.request.assert_not_called()
                    self.assertEqual(invocation.pending, set())
                    self.assertEqual(router._capability_callbacks, {})
                finally:
                    scheduled[0].close()
                    await router.finish_invocation("request")

    async def test_real_slow_fallback_cleanup_retains_thread_until_confirmed_stopped(self):
        started, release = threading.Event(), threading.Event()
        router = self.router()

        async def send(intent):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    continue
            return callbacks.NotificationResult(True, "delivered")

        router.bind_notification_port(SimpleNamespace(send=send))
        router._notification_loop = None
        router.dispatch(_request("notification.send", intent=notification_intent_to_wire(INTENT)))
        loop, thread = router._fallback_loop, router._fallback_thread
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            with self.assertRaisesRegex(RuntimeError, "callback_cleanup_incomplete"):
                await asyncio.to_thread(router.close)
            self.assertIs(router._fallback_thread, thread)
            self.assertTrue(thread.is_alive())
            self.assertIsNone(router._ensure_fallback_loop())
        finally:
            release.set()
            await asyncio.to_thread(thread.join, 2)
            if not loop.is_closed():
                loop.call_soon_threadsafe(loop.stop)
                await asyncio.to_thread(thread.join, 2)
            router.close()
        self.assertFalse(thread.is_alive())
        self.assertTrue(loop.is_closed())

    async def test_old_scope_cleanup_does_not_remove_new_scope_with_same_request_id(self):
        started, release = asyncio.Event(), asyncio.Event()
        router = self.router()
        router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT)
        old = router._invocations["request"]

        async def slow_close():
            started.set()
            await release.wait()

        old.aclose = slow_close
        closing = asyncio.create_task(router.finish_invocation("request"))
        try:
            await asyncio.wait_for(started.wait(), 2)
            router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT)
            new = router._invocations["request"]
            release.set()
            await closing
            self.assertIs(router._invocations["request"], new)
            self.assertFalse(old.active)
            self.assertTrue(new.active)
        finally:
            release.set()
            await closing
            await router.finish_invocation("request")

    async def test_closed_process_retries_unconfirmed_callback_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = PluginGenerationProcess(
                project_root=Path(__file__).resolve().parents[1], site_dir=root,
                plugin_id=PLUGIN_ID, work_dir=root / "worker",
            )
            process._closed = True  # terminate closes the worker before the router
            with patch.object(process._callback_router, "close", side_effect=RuntimeError("callback_cleanup_incomplete")) as close:
                with self.assertRaisesRegex(RuntimeError, "callback_cleanup_incomplete"):
                    process.stop()
                close.assert_called_once()
                close.side_effect = None
                self.assertEqual(process.stop()["reason"], "already_stopped")
                self.assertEqual(close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
