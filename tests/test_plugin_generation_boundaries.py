"""Failure checks for the managed process, control lane and callback scopes."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, Mock, patch

from capcore import InvocationContext
from companion_v01.plugin_api import NotificationResult
from companion_v01.plugin_generation import (
    PLUGIN_GENERATION_PROTOCOL,
    PluginGenerationError,
    PluginGenerationProcess,
    PluginGenerationQQCommandBroker,
)
from tests.test_plugin_generation import PROJECT_ROOT, _write_capability_plugin_site


class PluginGenerationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.generations = []

    def tearDown(self) -> None:
        for generation in self.generations:
            if isinstance(generation._process, subprocess.Popen):
                # Also clean up the old implementation's unstarted reader.
                if generation._reader is not None and generation._reader.ident is None:
                    generation._reader = None
                generation._terminate()
            else:
                generation._callback_router.close()
        self.temporary.cleanup()

    def generation(self) -> PluginGenerationProcess:
        generation = PluginGenerationProcess(
            project_root=PROJECT_ROOT,
            site_dir=self.root / "site",
            plugin_id="test.generation",
            work_dir=self.root / f"work-{len(self.generations)}",
            start_timeout_seconds=5.0,
            stop_timeout_seconds=1.0,
        )
        self.generations.append(generation)
        return generation

    def transport(self):
        generation = self.generation()
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        self.addCleanup(stream.close)
        generation._process = SimpleNamespace(stdin=stream, poll=lambda: None)
        return generation, buffer

    def worker(self) -> PluginGenerationProcess:
        generation = self.generation()
        generation.site_dir = _write_capability_plugin_site(self.root)
        generation.start()
        return generation

    def assert_rejected_frame(self, payload) -> None:
        generation, buffer = self.transport()
        with self.assertRaises(PluginGenerationError) as rejected:
            generation._send_request("invoke", {"args": payload})
        self.assertEqual(rejected.exception.reason, "plugin_generation_invocation_invalid")
        self.assertEqual(generation._pending, {})
        self.assertEqual(generation._active_invocations, 0)
        self.assertEqual(buffer.getvalue(), b"")

    def test_non_json_request_does_not_register_a_waiter(self) -> None:
        self.assert_rejected_frame({"value": object()})

    def test_public_broker_type_hints_still_resolve(self) -> None:
        hints = get_type_hints(PluginGenerationQQCommandBroker.__init__)
        self.assertIs(hints["generation"], PluginGenerationProcess)
        factory = get_type_hints(PluginGenerationProcess.build_qq_command_broker)
        self.assertIs(factory["return"], PluginGenerationQQCommandBroker)

    def test_cyclic_request_does_not_register_a_waiter(self) -> None:
        payload = {}
        payload["cycle"] = payload
        self.assert_rejected_frame(payload)

    def test_non_finite_request_does_not_reach_the_pipe(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=str(value)):
                self.assert_rejected_frame({"value": value})

    def test_invalid_stop_request_does_not_close_admission(self) -> None:
        generation, buffer = self.transport()
        with self.assertRaises(PluginGenerationError):
            generation._send_request("stop", {"invalid": object()})
        self.assertFalse(generation._stopping)
        self.assertEqual(generation._pending, {})
        self.assertEqual(buffer.getvalue(), b"")
        request_id, responses = generation._send_request("health")
        self.assertEqual(json.loads(buffer.getvalue())["request_id"], request_id)
        generation._discard_pending(request_id, responses)

    def test_closed_input_stream_reports_unavailable_without_a_waiter(self) -> None:
        generation, _ = self.transport()
        generation._process.stdin.close()
        with self.assertRaises(PluginGenerationError) as rejected:
            generation._send_request("invoke", {"args": {"value": "never sent"}})
        self.assertEqual(rejected.exception.reason, "plugin_generation_unavailable")
        self.assertEqual(generation._pending, {})
        self.assertEqual(generation._active_invocations, 0)

    async def test_invalid_utf8_invocation_does_not_poison_real_worker(self) -> None:
        generation = self.worker()
        with self.assertRaises(PluginGenerationError) as rejected:
            await generation.invoke(
                "test.generation.echo.v1", {"value": "\ud800"},
                context=InvocationContext(session_id="invalid-utf8"),
            )
        self.assertEqual(rejected.exception.reason, "plugin_generation_invocation_invalid")
        self.assertEqual(generation._pending, {})
        self.assertEqual(generation._callback_router._invocations, {})
        self.assertEqual(generation._active_invocations, 0)
        result = await generation.invoke(
            "test.generation.echo.v1", {"value": "你好，伙伴 🌸"},
            context=InvocationContext(session_id="valid-utf8"),
        )
        self.assertEqual(result.content["value"], "你好，伙伴 🌸")
        self.assertTrue((await asyncio.to_thread(generation.health))["ok"])

    async def test_legacy_module_runs_real_worker(self) -> None:
        generation = self.generation()
        generation.site_dir = _write_capability_plugin_site(self.root)
        original_popen = subprocess.Popen

        def launch(command, **kwargs):
            command = list(command)
            module_index = command.index("-m") + 1
            self.assertEqual(command[module_index], "companion_v01.plugin_generation_worker")
            command[module_index] = "companion_v01.plugin_generation"
            return original_popen(command, **kwargs)

        with patch("companion_v01.plugin_generation_process.lifecycle.subprocess.Popen", side_effect=launch):
            ready = generation.start()
        self.assertTrue(ready["ok"])
        self.assertEqual(ready["generation_id"], generation.generation_id)
        result = await generation.invoke(
            "test.generation.echo.v1", {"value": "legacy worker entry"},
            context=InvocationContext(session_id="legacy-worker"),
        )
        self.assertEqual(result.content["value"], "legacy worker entry")
        stopped = await asyncio.to_thread(generation.stop)
        self.assertTrue(stopped["ok"])
        self.assertFalse(generation.running)

    def test_invalid_callback_result_returns_correlated_failure(self) -> None:
        for invalid in (object(), "\ud800"):
            with self.subTest(value_type=type(invalid).__name__):
                generation, buffer = self.transport()
                generation._write_callback_response({
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "callback_response",
                    "generation_id": generation.generation_id,
                    "callback_id": "callback-1",
                    "ok": True,
                    "result": {"invalid": invalid},
                })
                response = json.loads(buffer.getvalue())
                self.assertEqual(response["callback_id"], "callback-1")
                self.assertEqual(response["generation_id"], generation.generation_id)
                self.assertFalse(response["ok"])
                self.assertEqual(response["reason"], "callback_result_invalid")
                self.assertNotIn("result", response)

    async def test_invalid_host_result_releases_real_worker_callback(self) -> None:
        generation = self.generation()
        generation.site_dir = _write_capability_plugin_site(self.root)
        port = SimpleNamespace(send=AsyncMock(return_value=NotificationResult(
            ok=True, status="delivered", reason="\ud800",
        )))
        generation.bind_notification_port(port)
        generation.start()
        invocation = asyncio.create_task(generation.invoke(
            "test.generation.notification.v1",
            {"recipient_id": "test-owner", "text": "test only", "idempotency_key": "once"},
            context=InvocationContext(session_id="invalid-host-result"),
        ))
        done, _ = await asyncio.wait({invocation}, timeout=3.0)
        if not done:
            await asyncio.to_thread(generation._terminate)
            await asyncio.gather(invocation, return_exceptions=True)
            self.fail("invalid callback result left the worker waiting")
        result = invocation.result()
        self.assertFalse(result.content["delivery_ok"])
        self.assertEqual(result.content["delivery_reason"], "callback_result_invalid")
        self.assertEqual(port.send.await_count, 1)
        self.assertEqual(generation._pending, {})
        self.assertEqual(generation._active_invocations, 0)
        self.assertTrue((await asyncio.to_thread(generation.health))["ok"])

    def assert_failed_start(self, target: str) -> None:
        generation = self.generation()
        generation.site_dir = _write_capability_plugin_site(self.root)
        with patch(target, side_effect=RuntimeError("private reader detail")):
            with self.assertRaises(PluginGenerationError) as rejected:
                generation.start(prepare_only=True)
        self.assertEqual(rejected.exception.reason, "plugin_generation_unavailable")
        self.assertIsNotNone(generation._process.poll())
        self.assertTrue(generation._process.stdin.closed)
        self.assertTrue(generation._process.stdout.closed)
        self.assertFalse(generation.running)
        self.assertTrue(generation._closed)
        self.assertEqual(generation._pending, {})

    def test_reader_start_failure_terminates_owned_child(self) -> None:
        self.assert_failed_start(
            "companion_v01.plugin_generation_process.lifecycle.threading.Thread.start"
        )

    def test_reader_construction_failure_terminates_owned_child(self) -> None:
        self.assert_failed_start(
            "companion_v01.plugin_generation_process.lifecycle.threading.Thread"
        )

    async def assert_scope_initialization_failure(self, error: BaseException) -> None:
        generation = self.generation()
        provider = SimpleNamespace(request=AsyncMock(), initialize_scope=Mock(side_effect=error))
        generation.bind_events_provider(provider)
        with self.assertRaises(type(error)):
            await generation.invoke(
                "test.generation.echo.v1", {"value": "never dispatched"},
                context=InvocationContext(session_id="initialization-failure"),
            )
        provider.initialize_scope.assert_called_once()
        provider.request.assert_not_called()
        self.assertEqual(generation._callback_router._invocations, {})
        self.assertEqual(generation._pending, {})
        self.assertEqual(generation._active_invocations, 0)

    async def test_scope_initialization_failure_releases_registered_grant(self) -> None:
        await self.assert_scope_initialization_failure(RuntimeError("private scope detail"))

    async def test_scope_initialization_cancellation_releases_registered_grant(self) -> None:
        await self.assert_scope_initialization_failure(asyncio.CancelledError())

    async def test_scope_cleanup_failure_balances_real_invocation(self) -> None:
        generation = self.worker()
        original_finish = generation._callback_router.finish_invocation
        for error in (RuntimeError("private cleanup detail"), asyncio.CancelledError()):
            with self.subTest(error_type=type(error).__name__):
                async def finish(request_id):
                    await original_finish(request_id)
                    raise error

                with patch.object(generation._callback_router, "finish_invocation", side_effect=finish):
                    with self.assertRaises(type(error)):
                        await generation.invoke(
                            "test.generation.echo.v1", {"value": "actual result"},
                            context=InvocationContext(session_id="cleanup-failure"),
                        )
                self.assertEqual(generation._pending, {})
                self.assertEqual(generation._callback_router._invocations, {})
                self.assertEqual(generation._active_invocations, 0)
                self.assertTrue(generation._wait_for_invocations(time.monotonic() + 0.1))
        stopped = await asyncio.to_thread(generation.stop)
        self.assertTrue(stopped["ok"])
        self.assertFalse(generation.running)
