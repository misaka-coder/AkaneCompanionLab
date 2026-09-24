"""Completion withdrawal is checked at execution and delivery, not queue admission."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.bot_runtime import _dispatch_host_job_completion
from tests import test_plugin_background_followup as helpers


class QQCompletionRevocationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helpers.QQCompletionDeliveryTests.asyncSetUp
    prepare_job = helpers.QQCompletionDeliveryTests.prepare_job
    wait_receipt = helpers.QQCompletionDeliveryTests.wait_receipt
    artifact = helpers.QQCompletionDeliveryTests.artifact

    async def test_withdrawn_input_is_removed_after_waiting_for_session_lock(self):
        await self.check_waiting_revocation()

    async def test_all_withdrawn_while_waiting_run_no_model_or_transport(self):
        await self.check_waiting_revocation(revoke_all=True)

    async def test_withdrawn_direct_file_does_not_enter_remaining_model_or_transport(self):
        await self.check_waiting_revocation(direct=True)

    async def test_revocation_during_model_stops_reply_and_keeps_model_participation(self):
        job = self.prepare_job(1, followup="required")
        self.release.clear()
        try:
            self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 3))
            self.jobs.revoke_job_capability(job.job_id, owner=job.owner)
            self.release.set()
            terminal = await self.wait_receipt(job)
            self.assertEqual(len(self.engine.turns), 1)
            self.network.assert_not_called()
            self.assertEqual(terminal.delivery_receipt["model_status"], "stopped")
            self.assertEqual(terminal.delivery_receipt["status"], "failed")
            self.assertEqual(terminal.status, "succeeded")
        finally:
            self.release.set()

    async def test_revoked_direct_file_during_other_model_does_not_stop_that_model(self):
        gate = asyncio.Event()
        async def deferred(coroutine):
            await gate.wait()
            await coroutine
        self.queue._schedule_task = lambda coroutine: asyncio.create_task(deferred(coroutine))
        direct = self.prepare_job(1, artifact=self.artifact(1))
        required = self.prepare_job(2, followup="required")
        self.release.clear()
        try:
            for job in (direct, required):
                self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
            gate.set()
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 3))
            self.jobs.revoke_job_capability(direct.job_id, owner=direct.owner)
            self.release.set()
            required = await self.wait_receipt(required)
            self.assertEqual(required.delivery_receipt["model_status"], "completed")
            self.assertFalse(required.scope_revoked_reason)
            self.assertEqual(self.network.call_count, 1)  # Only the surviving model reply.
        finally:
            gate.set()
            self.release.set()

    async def test_revocation_in_final_delivery_hook_keeps_completed_model_but_sends_nothing(self):
        from companion_v01.plugin_hooks import BEFORE_OUTBOUND_PLAN_HOOK

        job = self.prepare_job(1, followup="required")
        def revoke(_envelope):
            token = self.coordinator.active_token(job.owner.profile_user_id, job.owner.session_id)
            self.assertEqual(self.coordinator.begin_finalization(token)["status"], "finalizing")
            self.jobs.revoke_job_capability(job.job_id, owner=job.owner)
            return SimpleNamespace(outbound_decorations=())
        with patch.object(self.gateway, "_plugin_hook_observes", lambda kind: kind == BEFORE_OUTBOUND_PLAN_HOOK), \
             patch.object(self.gateway, "_dispatch_plugin_hook", revoke):
            self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
            terminal = await self.wait_receipt(job)
        self.network.assert_not_called()
        self.assertEqual(terminal.delivery_receipt["model_status"], "completed")
        self.assertEqual(terminal.delivery_receipt["status"], "failed")

    async def test_revocation_between_direct_files_preserves_first_transport_and_blocks_second(self):
        gate = asyncio.Event()
        async def deferred(coroutine):
            await gate.wait()
            await coroutine
        self.queue._schedule_task = lambda coroutine: asyncio.create_task(deferred(coroutine))
        first = self.prepare_job(1, artifact=self.artifact(1))
        second = self.prepare_job(2, artifact=self.artifact(2))
        sent = []
        response = self.network.return_value
        def transport(*args, **kwargs):
            sent.append((args, kwargs))
            self.jobs.revoke_job_capability(second.job_id, owner=second.owner)
            return response
        self.network.side_effect = transport
        try:
            for job in (first, second):
                self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
            gate.set()
            first, second = await self.wait_receipt(first), await self.wait_receipt(second)
            file_calls = [call for call in sent if "/upload_" in call[0][1]]
            self.assertEqual(len(file_calls), 1)
            self.assertIn("check-1", str(file_calls[0]))
            self.assertEqual(self.engine.turns, [])
            self.assertFalse(first.scope_revoked_reason)
            self.assertEqual(first.delivery_receipt["status"], "completed")
            self.assertEqual(second.delivery_receipt["status"], "failed")
        finally:
            gate.set()

    async def test_audio_both_keeps_sent_voice_and_blocks_file_and_upload_fallback(self):
        import wave
        from companion_v01.qq_gateway import QQMessageContext

        path = Path(self.temp.name) / "sample.wav"
        with wave.open(str(path), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00\x00" * 160)
        permitted = True
        response = self.network.return_value
        def sent(*_args, **_kwargs):
            nonlocal permitted
            permitted = False
            return response
        self.network.side_effect = sent
        context = QQMessageContext(True, "test", target_id=300, user_id=300)
        result = await asyncio.to_thread(self.gateway._send_generated_file_targets, context, [{
            "generated_id": "generated::audio", "path": str(path), "name": "sample.wav",
            "is_audio": True, "delivery_mode": "both"}], target_allowed=lambda target: permitted)
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["results"][0]["voice_result"]["ok"])
        self.assertFalse(result["results"][0]["file_result"]["ok"])

    async def check_waiting_revocation(self, *, revoke_all=False, direct=False):
        queued, at_lock, release_lock = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def deferred(coroutine):
            await queued.wait()
            await coroutine
        self.queue._schedule_task = lambda coroutine: asyncio.create_task(deferred(coroutine))
        original_hold = self.coordinator.hold
        @asynccontextmanager
        async def delayed_hold(*args, **kwargs):
            at_lock.set()
            await release_lock.wait()
            async with original_hold(*args, **kwargs) as token:
                yield token
        revoked = self.prepare_job(1, followup="none" if direct else "required",
                                   artifact=self.artifact(1) if direct else None)
        remaining = self.prepare_job(2, followup="required")
        try:
            with patch.object(self.coordinator, "hold", delayed_hold):
                for job in (revoked, remaining):
                    self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
                queued.set()
                await asyncio.wait_for(at_lock.wait(), 3)
                self.jobs.revoke_job_capability(revoked.job_id, owner=revoked.owner)
                if revoke_all:
                    self.jobs.revoke_job_capability(remaining.job_id, owner=remaining.owner)
                release_lock.set()
                revoked, remaining = await self.wait_receipt(revoked), await self.wait_receipt(remaining)
            self.assertEqual(revoked.delivery_receipt["model_status"], "not_requested")
            if revoke_all:
                self.assertEqual(self.engine.turns, [])
                self.network.assert_not_called()
                self.assertEqual(remaining.delivery_receipt["model_status"], "not_requested")
                return
            self.assertEqual(len(self.engine.turns), 1)
            self.assertEqual(self.network.call_count, 1)
            prompt = self.engine.turns[0]["message"]
            self.assertNotIn(revoked.job_id, prompt)
            self.assertIn(remaining.job_id, prompt)
            self.assertEqual(remaining.delivery_receipt["model_status"], "completed")
            self.assertFalse(remaining.scope_revoked_reason)
        finally:
            queued.set()
            release_lock.set()


class DesktopCompletionRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_batch_uses_live_jobs_and_delivers_only_remaining_reply(self):
        await self.check_waiting_revocation(revoke_all=False)

    async def test_all_withdrawn_while_waiting_deliver_no_desktop_frame(self):
        await self.check_waiting_revocation(revoke_all=True)

    async def test_model_return_then_revocation_keeps_completed_model_and_blocks_frame(self):
        await self.check_waiting_revocation(revoke_all=False, during_model=True)

    async def check_waiting_revocation(self, *, revoke_all, during_model=False):
        from companion_v01.durable_session_queue import DurableSessionWorkQueue
        from companion_v01.host_jobs import HostJobOwner, HostJobStore
        from companion_v01.routes.think import build_think_router
        from companion_v01.session_inbox import SessionInboxStore
        from companion_v01.turn_coordination import TurnCoordinator
        from tests.test_host_completion_batch import terminal_request
        from tests.test_plugin_events import _RouteEngine

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine, coordinator = _RouteEngine(), TurnCoordinator()
            jobs = engine.job_store = HostJobStore(root / "jobs.db")
            queued, at_lock, release_lock = asyncio.Event(), asyncio.Event(), asyncio.Event()
            async def deferred(coroutine):
                await queued.wait()
                await coroutine
            queue = DurableSessionWorkQueue(SessionInboxStore(root / "inbox.db"),
                schedule_task=lambda coroutine: asyncio.create_task(deferred(coroutine)))
            original_hold = coordinator.hold
            @asynccontextmanager
            async def delayed_hold(*args, **kwargs):
                at_lock.set()
                await release_lock.wait()
                async with original_hold(*args, **kwargs) as token:
                    yield token
            frames, handlers = [], {}
            async def deliver(frame):
                frames.append(frame)
                return {"ok": True, "status": "queued"}
            build_think_router(engine=engine, public_guard=SimpleNamespace(), runtime_metrics=SimpleNamespace(),
                log_event=lambda *args, **kwargs: None, session_work_queue=queue, turn_coordinator=coordinator,
                plugin_agent_event_handler_registrar=handlers.__setitem__, desktop_agent_frame_delivery=deliver,
                desktop_agent_event_available=lambda: True)
            requests = [terminal_request(jobs, n, channel="desktop_pet") for n in (1, 2)]
            owner = HostJobOwner("master", "desk")
            job_ids = [request.trace_id.removeprefix("job-completed:") for request in requests]
            if during_model:
                original_process = engine.process_turn
                def process(payload):
                    frame = original_process(payload)
                    jobs.revoke_job_capability(job_ids[0], owner=owner)
                    return frame
                engine.process_turn = process
            try:
                with patch.object(coordinator, "hold", delayed_hold):
                    for request in requests:
                        accepted = await handlers["desktop_pet"](request, {"channel": "desktop_pet", "kind": "direct",
                            "recipient": "desktop:desk", "session": "desk", "profile": "master", "character": "reimu"})
                        self.assertTrue(accepted.ok, accepted)
                    queued.set()
                    await asyncio.wait_for(at_lock.wait(), 3)
                    for job_id in [] if during_model else job_ids if revoke_all else job_ids[:1]:
                        jobs.revoke_job_capability(job_id, owner=owner)
                    release_lock.set()
                    async with asyncio.timeout(5):
                        while not all(jobs.get(job_id, owner=owner).delivery_receipt.get("status") in {"completed", "failed"}
                                      for job_id in job_ids):
                            await asyncio.sleep(0.01)
                self.assertEqual(len(engine.turns), 0 if revoke_all else 1)
                self.assertEqual(len(frames), 0 if revoke_all or during_model else 1)
                if during_model:
                    self.assertEqual(jobs.get(job_ids[0], owner=owner).delivery_receipt["model_status"], "completed")
                    self.assertEqual(jobs.get(job_ids[0], owner=owner).delivery_receipt["status"], "failed")
                    self.assertFalse(jobs.get(job_ids[1], owner=owner).scope_revoked_reason)
                    return
                if not revoke_all:
                    self.assertNotIn(job_ids[0], engine.turns[0]["message"])
                    self.assertIn(job_ids[1], engine.turns[0]["message"])
                    self.assertEqual(frames[0]["speech"], "收到事件")
                self.assertEqual(jobs.get(job_ids[0], owner=owner).delivery_receipt["model_status"], "not_requested")
                self.assertEqual(jobs.get(job_ids[1], owner=owner).delivery_receipt["model_status"],
                                 "not_requested" if revoke_all else "completed")
            finally:
                queued.set()
                release_lock.set()
                await queue.close()


class CompletionModelScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from companion_v01.host_jobs import HostJobOwner, HostJobStore
        from companion_v01.turn_coordination import TurnCoordinator
        from tests.test_host_completion_batch import terminal_request

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = HostJobStore(self.root / "jobs.db")
        request = terminal_request(self.jobs, 1, channel="desktop_pet")
        self.job = self.jobs.get(request.trace_id.removeprefix("job-completed:"),
                                 owner=HostJobOwner("master", "desk"))
        self.coordinator = TurnCoordinator()

    def revoke(self):
        from companion_v01.host_jobs import HostJobStore
        # A separately opened store proves there is no object-local grant cache.
        HostJobStore(self.root / "jobs.db").revoke_job_capability(self.job.job_id, owner=self.job.owner)

    def completion(self, payload):
        from companion_v01.host_completion_batch import CompletionTurn
        return CompletionTurn(payload, None, (self.job,), frozenset((self.job.job_id,)), self.jobs)

    async def test_real_engine_stops_between_side_effects_and_does_not_generate_again(self):
        from tests.test_turn_mainline_contract import _Harness, _tool_round_output
        from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD
        from companion_v01.tool_runtime import ToolExecutionResult

        output = _tool_round_output("", "write_fixture", "write-1")
        output[NATIVE_TOOL_CALLS_FIELD].append({"type": "write_fixture", "id": "write-2", "arguments": {}})
        harness = _Harness([output])
        harness.engine.turn_coordinator = self.coordinator
        def execute(**kwargs):
            call_id = kwargs["tool_call"]["id"]
            (self.root / call_id).write_text("actual side effect", encoding="utf-8")
            self.revoke()
            return ToolExecutionResult(tool_type="write_fixture", followup_context="write succeeded")
        harness.engine._execute_tool_call = execute
        async with self.coordinator.hold("master", "desk") as token:
            payload = harness.payload(real_user_id="master", user_id="desk", _turn_control_id=token)
            with self.completion(payload).processing(self.coordinator, token):
                frame = await asyncio.to_thread(harness.run_sync, payload)
        self.assertEqual(frame["status"], "stopped")
        self.assertTrue((self.root / "write-1").is_file())
        self.assertFalse((self.root / "write-2").exists())
        self.assertEqual(len(harness.script.generation_calls), 1)
        self.assertTrue(any(event.get("type") == "tool_execution_cancelled" for event in frame["tool_events"]))

    async def test_real_engine_checks_after_before_tool_hook(self):
        from tests.test_turn_mainline_contract import _Harness, _tool_round_output
        from companion_v01.plugin_hooks import BEFORE_TOOL_CALL_HOOK

        harness = _Harness([_tool_round_output("", "write_fixture", "write-1")])
        harness.engine.turn_coordinator = self.coordinator
        harness.engine._plugin_hook_observes = lambda kind: kind == BEFORE_TOOL_CALL_HOOK
        harness.engine._dispatch_plugin_hook = lambda _envelope: self.revoke()
        called = []
        harness.engine._execute_tool_call = lambda **kwargs: called.append(kwargs)
        async with self.coordinator.hold("master", "desk") as token:
            payload = harness.payload(real_user_id="master", user_id="desk", _turn_control_id=token)
            with self.completion(payload).processing(self.coordinator, token):
                frame = await asyncio.to_thread(harness.run_sync, payload)
        self.assertEqual(called, [])
        self.assertEqual(frame["status"], "stopped")
        self.assertEqual(len(harness.script.generation_calls), 1)

    async def test_completion_revocation_cancels_its_admitted_child_job(self):
        from companion_v01.background_tasks import BackgroundTaskRunner
        from companion_v01.host_tool_jobs import HostToolJobRuntime
        from companion_v01.tool_runtime import ToolExecutionContext
        from tests.test_host_tool_jobs import _Handler, _Engine

        release = threading.Event()
        handler = _Handler(started=threading.Event(), release=release)
        engine = _Engine(handler)
        engine.turn_coordinator = self.coordinator
        runner = BackgroundTaskRunner({"host-jobs": 1})
        runner.submit(fn=release.wait, args=(5,), lane="host-jobs", name="hold-worker")
        runtime = HostToolJobRuntime(engine=engine, store=self.jobs, background_tasks=runner,
                                     conversation_ref_issuer=lambda _context: "test-ref")
        try:
            async with self.coordinator.hold("master", "desk") as token:
                with self.completion({}).processing(self.coordinator, token):
                    result = runtime.submit(capability_id=handler.tool_type, invocation_id="child-call",
                        call={"type": handler.tool_type, "arguments": {}},
                        context=ToolExecutionContext("master", "desk", 1, {}, client_mode="desktop_pet"))
                    child_id = result.stream_events[0]["job_id"]
                    self.assertEqual(self.jobs.get(child_id, owner=self.job.owner).status, "queued")
                    self.revoke()
                    self.assertTrue(self.coordinator.drain(token)["stop_requested"])
                    child = self.jobs.get(child_id, owner=self.job.owner)
                    self.assertEqual(child.status, "cancelled")
                    self.assertEqual(child.scope_revoked_reason, "parent_turn_stopped")
                    release.set()
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                    self.assertEqual(handler.calls, [])
            self.assertEqual(self.jobs.get(self.job.job_id, owner=self.job.owner).status, "succeeded")
        finally:
            release.set()
            runner.close(timeout=3)

    async def test_real_engine_checks_before_initial_model_request(self):
        from tests.test_turn_mainline_contract import _Harness

        harness = _Harness([])
        harness.engine.turn_coordinator = self.coordinator
        async with self.coordinator.hold("master", "desk") as token:
            payload = harness.payload(real_user_id="master", user_id="desk", _turn_control_id=token)
            with self.completion(payload).processing(self.coordinator, token):
                self.revoke()
                frame = await asyncio.to_thread(harness.run_sync, payload)
        self.assertEqual(frame["status"], "stopped")
        self.assertEqual(harness.script.generation_calls, [])

    async def test_chunk_upload_stops_after_withdrawal_without_finalize_request(self):
        from companion_v01.onebot_transport import OneBotActionTransport
        from tests.test_plugin_events import _Response

        path = self.root / "large.bin"
        path.write_bytes(b"x" * (150 * 1024))
        transport = OneBotActionTransport(SimpleNamespace(
            onebot_http_url="http://127.0.0.1:3001", onebot_headers=lambda: {}))
        def sent(*args, **kwargs):
            self.revoke()
            return _Response()
        async with self.coordinator.hold("master", "desk") as token:
            with self.completion({}).processing(self.coordinator, token), \
                 patch("companion_v01.onebot_transport.requests.Session.request", side_effect=sent) as network:
                staged = await asyncio.to_thread(transport.stage_file, path, chunk_bytes=64 * 1024)
        self.assertFalse(staged.ok)
        self.assertEqual(staged.code, "turn_scope_revoked")
        self.assertEqual(network.call_count, 1)
        self.assertEqual(network.call_args.kwargs["json"]["chunk_index"], 0)
        self.assertEqual(network.call_args.kwargs["json"]["total_chunks"], 3)

    async def test_real_tool_orchestration_cancels_running_adapter_and_waits_for_cleanup(self):
        from tests import test_plugin_engine_bridge as bridge_helpers
        from companion_v01.tool_invocation import ToolInvocation
        from companion_v01.tool_orchestration_engine import execute_tool_invocation

        await bridge_helpers.PluginEngineBridgeTests.asyncSetUp(self)
        self.addAsyncCleanup(bridge_helpers.PluginEngineBridgeTests.asyncTearDown, self)
        entered, cleaning, release = (threading.Event() for _ in range(3))
        async def invoke(_capability, _args, _context):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
                raise
        self.adapter.invoke = invoke
        async with self.coordinator.hold("master", "desk") as token:
            with self.completion({}).processing(self.coordinator, token):
                execution = asyncio.create_task(asyncio.to_thread(execute_tool_invocation, self.engine,
                    invocation=ToolInvocation(bridge_helpers.CAPABILITY_ID, {"arguments": {"query": "test"}}, id="scope-adapter"),
                    profile_user_id="master", session_id="desk", visual_payload={}, now_ts=1))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3), execution.result() if execution.done() else "adapter not entered")
                    self.revoke()
                    self.assertTrue(await asyncio.to_thread(cleaning.wait, 3))
                    self.assertFalse(execution.done(), "actual cleanup must finish before recording cancellation")
                    release.set()
                    result, _envelope = await asyncio.wait_for(execution, 3)
                    self.assertEqual(result.state_updates["adapter_capability_status"], "cancelled")
                finally:
                    release.set()
                    await execution

    async def test_completion_revocation_blocks_queued_desktop_action(self):
        import contextvars
        import time
        from companion_v01.capability_registry import ExecutorBroker
        from companion_v01.desktop_satellite import DesktopSatelliteService, _SatelliteConnection
        from companion_v01.desktop_satellite_specs import SYSTEM_MEDIA_CONTROL_TOOL_SPEC as spec

        service = DesktopSatelliteService(instance_id="instance", token="test")
        connection = _SatelliteConnection("connection", "lease", "offer", asyncio.get_running_loop(),
            asyncio.Queue(), time.time() + 60, time.time(), "instance", frozenset((spec.capability_id,)))
        self.assertTrue(service._install_connection(connection))
        receipt = service.resolve_receipt(spec)
        sent = []
        async def send(payload):
            sent.append(payload)
        sender = None
        async with self.coordinator.hold("master", "desk") as token:
            with self.completion({}).processing(self.coordinator, token):
                dispatch = asyncio.create_task(asyncio.to_thread(ExecutorBroker(service).execute,
                    spec=spec, receipt_value=receipt.as_dict(), invocation_id="completion-media",
                    arguments={"action": "play"}, timeout_seconds=3))
                try:
                    queued = await asyncio.wait_for(connection.outbound.get(), 3)
                    connection.outbound.put_nowait(queued)
                    self.revoke()
                    sender = asyncio.create_task(service._send_loop(SimpleNamespace(send_json=send), connection),
                                                 context=contextvars.Context())
                    result = await asyncio.wait_for(dispatch, 3)
                    self.assertEqual(result.status, "unavailable_before_dispatch")
                    self.assertEqual(result.reason, "turn_scope_revoked")
                    self.assertFalse(sent)
                    self.assertEqual(service.diagnostics()["pendingInvocationCount"], 0)
                finally:
                    service._remove_connection(connection.connection_id)
                    if sender is not None:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
                    await asyncio.gather(dispatch, return_exceptions=True)

    async def test_desktop_checks_scope_after_enqueue_before_websocket_send(self):
        import contextvars
        import time
        from companion_v01.desktop_satellite import DesktopSatelliteService, _SatelliteConnection

        service = DesktopSatelliteService(instance_id="instance", token="test")
        connection = _SatelliteConnection("connection", "lease", "offer", asyncio.get_running_loop(),
            asyncio.Queue(), time.time() + 60, time.time(), "instance", frozenset())
        service._connection = connection
        sent = []
        async def send(payload):
            sent.append(payload)
        sender = None
        async with self.coordinator.hold("master", "desk") as token:
            with self.completion({}).processing(self.coordinator, token):
                delivery = asyncio.create_task(service.deliver_agent_frame({"speech": "completed"}, bot_id="instance"))
                try:
                    async with asyncio.timeout(3):
                        while connection.outbound.empty():
                            await asyncio.sleep(0.01)
                    self.revoke()
                    sender = asyncio.create_task(service._send_loop(SimpleNamespace(send_json=send), connection),
                                                 context=contextvars.Context())
                    result = await asyncio.wait_for(delivery, 3)
                    self.assertEqual(result["status"], "cancelled")
                    self.assertEqual(result["reason"], "turn_scope_revoked")
                    self.assertEqual(sent, [])
                finally:
                    if sender is not None:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
                    if not delivery.done():
                        delivery.cancel()
                        await asyncio.gather(delivery, return_exceptions=True)
