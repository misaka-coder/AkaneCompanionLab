"""Per-result choices through durable jobs and the existing channel delivery."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from akane_plugin import Plugin, Result
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.bot_runtime import _dispatch_host_job_completion
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.tool_continuation import job_followup
from tests import test_plugin_sdk as sdk_helpers
from tests import test_plugin_qq_inbound_turns as qq_helpers
from tests.test_host_tool_jobs import _Engine, _context


class BackgroundResultTests(unittest.IsolatedAsyncioTestCase):
    start = sdk_helpers.PublicSdkTests.start

    async def test_parent_stop_drains_running_public_tool_and_retains_actual_result(self):
        import threading
        from companion_v01.turn_coordination import TurnCoordinator

        for suppress in (False, True):
            with self.subTest(suppress=suppress), tempfile.TemporaryDirectory() as directory:
                entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()
                plugin = Plugin("example.stoppable")

                @plugin.tool(execution_class="long_task")
                async def check() -> Result:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cleaning.set()
                        while not release.is_set():
                            await asyncio.sleep(0.01)
                        if suppress:
                            return Result(value={"actually_finished": True}, followup="required")
                        raise

                host, sdk_engine = await self.start(plugin)
                handler = sdk_engine._resolve_tool_handlers()["example.stoppable.check"]
                engine = _Engine(handler)
                coordinator = engine.turn_coordinator = TurnCoordinator()
                engine.executor_broker = ExecutorBroker(None)
                engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
                store = HostJobStore(Path(directory) / "jobs.db")
                runner = BackgroundTaskRunner({"host-jobs": 1})
                runtime = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                    conversation_ref_issuer=lambda context: "ref")
                context = _context()
                try:
                    async with coordinator.hold(context.profile_user_id, context.session_id, actor_id="actor"):
                        accepted = runtime.submit(capability_id=handler.tool_type, invocation_id="running",
                            call={"type": handler.tool_type, "arguments": {}}, context=context)
                        job_id = accepted.stream_events[0]["job_id"]
                        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                        coordinator.request_stop(profile_user_id=context.profile_user_id,
                            session_id=context.session_id, actor_id="actor")
                        self.assertTrue(await asyncio.to_thread(cleaning.wait, 3))
                        owner = HostJobOwner(context.profile_user_id, context.session_id)
                        self.assertEqual(store.get(job_id, owner=owner).status, "running")
                        release.set()
                        self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                    job = HostJobStore(Path(directory) / "jobs.db").get(job_id, owner=owner)
                    self.assertEqual(job.status, "succeeded" if suppress else "cancelled")
                    self.assertEqual(job.scope_revoked_reason, "parent_turn_stopped")
                    self.assertFalse(job_followup(job).requires_model)
                    if suppress:
                        self.assertEqual(job.result["capability_result"]["value"], {"actually_finished": True})
                finally:
                    release.set()
                    await asyncio.to_thread(runner.close)
                    await host.stop()

    async def test_recovery_keeps_admitted_default_after_descriptor_change(self):
        from unittest.mock import patch
        from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler

        with tempfile.TemporaryDirectory() as directory:
            plugin = Plugin("example.frozen")

            @plugin.tool(execution_class="long_task", followup="none")
            def check() -> Result:
                return Result(value=42)

            host, sdk_engine = await self.start(plugin)
            handler = sdk_engine._resolve_tool_handlers()["example.frozen.check"]
            engine = _Engine(handler)
            engine.executor_broker = ExecutorBroker(None)
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            path = Path(directory) / "jobs.db"
            runner = BackgroundTaskRunner({"host-jobs": 1})
            runtime = HostToolJobRuntime(engine=engine, store=HostJobStore(path), background_tasks=runner,
                conversation_ref_issuer=lambda context: "ref")
            try:
                with patch.object(runtime, "_schedule", return_value={"ok": True}):
                    accepted = runtime.submit(capability_id=handler.tool_type, invocation_id="recover",
                        call={"type": handler.tool_type, "arguments": {}}, context=_context())
                engine.handler = PluginCapabilityToolHandler(capability_id=handler.tool_type,
                    adapter=handler.adapter, descriptor=replace(handler.descriptor,
                        raw={**handler.descriptor.raw, "followup": "required"}), config_base_dir=Path(directory))
                reopened = HostJobStore(path)
                recovered = HostToolJobRuntime(engine=engine, store=reopened, background_tasks=runner,
                    conversation_ref_issuer=lambda context: "ref")
                self.assertGreaterEqual(recovered.recover(), 1)
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                job = reopened.get(accepted.stream_events[0]["job_id"], owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(job.status, "succeeded", job.last_error)
                self.assertEqual(job.result["capability_result"]["value"], 42)
                self.assertFalse(job_followup(job).requires_model)
                self.assertEqual(job.result["followup"]["mode"], "none")
            finally:
                await asyncio.to_thread(runner.close)
                await host.stop()

    async def test_terminal_result_override_persists_and_uses_canonical_dispatch(self):
        for default, override, error, consumer, expected in (
            ("required", "none", False, "model", False),
            ("none", "required", False, "model", True),
            ("none", None, True, "model", True),
            ("required", "none", True, "program", False),
        ):
            with self.subTest(default=default, override=override, error=error, consumer=consumer), tempfile.TemporaryDirectory() as directory:
                plugin = Plugin("example.background")
                value = {"changed": False, "rows": list(range(1500)), "zero": 0, "empty": None}
                calls = []

                @plugin.tool(execution_class="long_task", followup=default)
                def check() -> Result:
                    calls.append(True)
                    return Result(value=value, followup=override, is_error=error, reason="check_failed" if error else "")

                host, sdk_engine = await self.start(plugin)
                handler = sdk_engine._resolve_tool_handlers()["example.background.check"]
                engine = _Engine(handler)
                engine.executor_broker = ExecutorBroker(None)
                engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
                timeline = []
                engine.record_plugin_timeline_event = lambda payload: timeline.append(payload) or {"ok": True}
                router = SimpleNamespace(submit=AsyncMock(return_value=SimpleNamespace(ok=True, status="accepted", delivery_status="queued")))
                runner = BackgroundTaskRunner({"host-jobs": 1})
                store = HostJobStore(Path(directory) / "jobs.db")
                loop = asyncio.get_running_loop()
                runtime = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                    conversation_ref_issuer=lambda context: "ref",
                    terminal_callback=lambda job: asyncio.run_coroutine_threadsafe(
                        _dispatch_host_job_completion(engine, router, job), loop).result(5))
                try:
                    context = replace(_context(), result_consumer=consumer)
                    accepted = runtime.submit(capability_id=handler.tool_type, invocation_id="check-1",
                        call={"type": handler.tool_type, "arguments": {}}, context=context, turn_id="origin")
                    self.assertEqual(accepted.stream_events[0]["type"], "background_job_accepted", accepted)
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=5))
                    self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
                    job = HostJobStore(Path(directory) / "jobs.db").get(accepted.stream_events[0]["job_id"], owner=HostJobOwner("profile-a", "session-a"))
                    self.assertEqual(job.result["capability_result"]["value"], value)
                    self.assertEqual(job_followup(job).requires_model, expected)
                    self.assertEqual(router.submit.await_count, int(expected))
                    self.assertEqual(len(timeline), int(not expected))
                    self.assertEqual(job.delivery_receipt["model_status"], "pending" if expected else "not_requested")
                    self.assertEqual(job.status, "failed" if error else "succeeded")
                    self.assertEqual(runtime.recover(), 0)
                    runtime.submit(capability_id=handler.tool_type, invocation_id="check-1",
                        call={"type": handler.tool_type, "arguments": {}}, context=context, turn_id="origin")
                    self.assertEqual(calls, [True])
                finally:
                    await asyncio.to_thread(runner.close)
                    await host.stop()

    async def test_parent_stop_revokes_queued_and_late_jobs_without_cancelling_unrelated_work(self):
        import threading
        from companion_v01.turn_coordination import TurnCoordinator
        from tests.test_host_tool_jobs import _Handler

        with tempfile.TemporaryDirectory() as directory:
            release = threading.Event()
            immediate = threading.Event()
            immediate.set()
            handler = _Handler(started=threading.Event(), release=immediate)
            engine = _Engine(handler)
            coordinator = engine.turn_coordinator = TurnCoordinator()
            store = HostJobStore(Path(directory) / "jobs.db")
            runner = BackgroundTaskRunner({"host-jobs": 1})
            runtime = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                conversation_ref_issuer=lambda context: "ref")
            context = _context()
            def submit(key):
                return runtime.submit(capability_id=handler.tool_type, invocation_id=key,
                    call={"type": handler.tool_type}, context=context).stream_events[0]["job_id"]
            try:
                runner.submit(lane="host-jobs", name="hold", fn=release.wait, args=(5,))
                independent = submit("unrelated")
                async with coordinator.hold(context.profile_user_id, context.session_id, actor_id="actor"):
                    before = submit("before-stop")
                    self.assertFalse(coordinator.request_stop(profile_user_id=context.profile_user_id,
                        session_id=context.session_id, actor_id="other")["ok"])
                    self.assertTrue(coordinator.request_stop(profile_user_id=context.profile_user_id,
                        session_id=context.session_id, actor_id="actor")["ok"])
                    after = submit("after-stop")
                release.set()
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=3))
                reopened = HostJobStore(Path(directory) / "jobs.db")
                owner = HostJobOwner(context.profile_user_id, context.session_id)
                for job_id in (before, after):
                    job = reopened.get(job_id, owner=owner)
                    self.assertEqual(job.status, "cancelled")
                    self.assertEqual(job.scope_revoked_reason, "parent_turn_stopped")
                    self.assertFalse(job_followup(job).requires_model)
                self.assertEqual(reopened.get(independent, owner=owner).status, "succeeded")
                self.assertEqual(len(handler.calls), 1)
            finally:
                release.set()
                await asyncio.to_thread(runner.close)


class QQCompletionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = qq_helpers.QQInboundTurnsTests.asyncSetUp

    def prepare_job(self, number, *, followup="none", artifact=None, failed=False):
        if not hasattr(self, "jobs"):
            self.jobs = self.engine.job_store = HostJobStore(Path(self.temp.name) / "jobs.db")
        session, profile = self.gateway.resolve_identity(user_id=300, group_id=0)
        owner = HostJobOwner(profile, session)
        reference = self.refs.issue_qq(profile_user_id=profile, session_id=session, character_pack_id="reimu", user_id=300)
        created = self.jobs.create(owner=owner, capability_source="tool", capability_id="fixture.register",
            payload={"followup": {"default": "required", "consumer": "model"}},
            idempotency_key=str(number), argument_fingerprint=str(number), channel="qq_text",
            character_pack_id="reimu", delivery_target=reference, turn_id="origin", completion_mode="agent", memory_mode="timeline")
        claim = self.jobs.claim(created["job_id"], worker_id="test")
        result = {"followup": {"mode": followup}, "capability_result": Result(value={"changed": False},
            content={"managed_artifacts": [artifact]} if artifact else {}, followup=followup).as_dict()}
        kwargs = {"claim_token": claim["claim_token"], "result": result, "result_summary": "unchanged"}
        if failed:
            self.jobs.fail(created["job_id"], error="check_failed", retryable=False, **kwargs)
        else:
            self.jobs.succeed(created["job_id"], **kwargs)
        return self.jobs.get(created["job_id"], owner=owner)

    async def wait_receipt(self, job):
        async with asyncio.timeout(6):
            while True:
                current = self.jobs.get(job.job_id, owner=job.owner)
                if current.delivery_receipt.get("status") in {"completed", "failed"}:
                    return current
                await asyncio.sleep(0.01)

    def artifact(self, number):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01.attachment_inbox import AttachmentInboxService
        from companion_v01.store import MemoryStore
        from tests.generated_artifact_fixtures import register_text_artifact

        if not hasattr(self, "files"):
            root = Path(self.temp.name)
            memory = MemoryStore(root / "memory")
            self.files = GeneratedFileService(base_dir=root / "files", store=memory,
                attachment_service=AttachmentInboxService(store=memory, base_dir=root / "attachments"))
            self.engine._get_generated_file_service = lambda: self.files
            self.engine.mark_generated_file_delivery = self.files.mark_delivery_status
        session, profile = self.gateway.resolve_identity(user_id=300, group_id=0)
        generated = register_text_artifact(self.files, profile_user_id=profile, session_id=session,
            output_title=f"check-{number}", output_format="txt", content=f"real result {number}", timestamp=number)
        return {**{key: generated[key] for key in ("generated_id", "generated_handle", "output_title", "output_format",
            "mime_type", "file_size", "created_by_tool")}, "send_to_user": True, "delivery_mode": "file"}

    async def test_none_files_use_real_gateway_without_model_and_keep_send_failure(self):
        for number, fail in ((1, False), (2, True)):
            with self.subTest(fail=fail):
                artifact = self.artifact(number)
                job = self.prepare_job(number, artifact=artifact)
                if fail:
                    self.network.side_effect = RuntimeError("test_transport_failed")
                accepted = await _dispatch_host_job_completion(self.engine, self.turns, job)
                self.assertTrue(accepted.ok, accepted)
                terminal = await self.wait_receipt(job)
                self.assertEqual(terminal.status, "succeeded")
                self.assertEqual(terminal.delivery_receipt["status"], "failed" if fail else "completed")
                self.assertEqual(terminal.delivery_receipt["model_status"], "not_requested")
                self.assertEqual(terminal.delivery_receipt["files"]["ok"], not fail)
                sent_before = self.network.call_count
                await _dispatch_host_job_completion(self.engine, self.turns, job)
                await asyncio.sleep(0)
                self.assertEqual(self.network.call_count, sent_before)
        self.assertEqual(self.engine.turns, [])

    async def test_none_without_artifact_records_fact_without_empty_message_or_model(self):
        job = self.prepare_job(1)
        result = await _dispatch_host_job_completion(self.engine, self.turns, job)
        self.assertEqual(result.status, "recorded")
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(len(self.engine.timeline), 1)
        self.network.assert_not_called()

    async def test_required_and_failure_run_normal_model_and_store_actual_delivery(self):
        for number, followup, failed in ((1, "required", False), (2, "none", True)):
            job = self.prepare_job(number, followup=followup, failed=failed)
            result = await _dispatch_host_job_completion(self.engine, self.turns, job)
            self.assertTrue(result.ok, result)
            terminal = await self.wait_receipt(job)
            self.assertEqual(terminal.delivery_receipt["model_status"], "completed")
            self.assertEqual(terminal.delivery_receipt["delivery_status"], "sent")
        self.assertEqual(len(self.engine.turns), 2)
        self.assertEqual(self.network.call_count, 2)

    async def test_mixed_batch_keeps_one_model_turn_and_delivers_none_artifact_once(self):
        gate = asyncio.Event()
        async def deferred(coroutine):
            await gate.wait()
            await coroutine
        self.queue._schedule_task = lambda coroutine: asyncio.create_task(deferred(coroutine))
        self.addCleanup(gate.set)
        direct = self.prepare_job(1, artifact=self.artifact(1))
        required = self.prepare_job(2, followup="required")
        for job in (direct, required):
            self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
        gate.set()
        direct, required = await self.wait_receipt(direct), await self.wait_receipt(required)
        self.assertEqual(len(self.engine.turns), 1)
        self.assertEqual(self.engine.turns[0]["plugin_external_event"]["data"]["job_id"], required.job_id)
        self.assertNotIn(direct.job_id, self.engine.turns[0]["message"])
        self.assertEqual(direct.delivery_receipt["model_status"], "not_requested")
        self.assertEqual(required.delivery_receipt["model_status"], "completed")
        self.assertEqual(self.network.call_count, 2)  # One model reply and one file transport.

    async def test_model_failure_has_failed_receipt_and_execution_is_not_replayed(self):
        self.model_error = True
        job = self.prepare_job(1, followup="required")
        self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
        terminal = await self.wait_receipt(job)
        self.assertEqual(terminal.status, "succeeded")
        self.assertEqual(terminal.delivery_receipt["status"], "failed")
        self.assertEqual(terminal.delivery_receipt["model_status"], "unknown")
        self.assertEqual(terminal.delivery_receipt["reason"], "RuntimeError")
        await _dispatch_host_job_completion(self.engine, self.turns, job)
        await asyncio.sleep(0)
        self.assertEqual(len(self.engine.turns), 1)

    async def test_disabling_then_reenabling_plugin_does_not_revive_queued_completion(self):
        from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
        from companion_v01.instance_profile import PluginSelection
        from tests.test_plugin_active_generation import FakeGeneration

        gate = asyncio.Event()
        async def deferred(coroutine):
            await gate.wait()
            await coroutine
        self.queue._schedule_task = lambda coroutine: asyncio.create_task(deferred(coroutine))
        self.addCleanup(gate.set)
        job = self.prepare_job(1, artifact=self.artifact(1), followup="required")
        active = ActivePluginGeneration()
        active.bind_capability_revocation_listener(self.jobs.revoke_capabilities)
        selection = (PluginSelection("example.fixture", True),)
        try:
            await active.publish(PluginGenerationSnapshot(selection, (FakeGeneration("example.fixture", "fixture.register", "one"),)))
            self.assertTrue((await _dispatch_host_job_completion(self.engine, self.turns, job)).ok)
            await active.publish(PluginGenerationSnapshot(selection, (FakeGeneration("example.fixture", "fixture.register", "upgrade"),)))
            self.assertEqual(self.jobs.get(job.job_id, owner=job.owner).scope_revoked_reason, "")
            await active.publish(PluginGenerationSnapshot((PluginSelection("example.fixture", False),), ()))
            await active.publish(PluginGenerationSnapshot(selection, (FakeGeneration("example.fixture", "fixture.register", "two"),)))
            gate.set()
            terminal = await self.wait_receipt(job)
            self.assertEqual(terminal.status, "succeeded")
            self.assertEqual(terminal.scope_revoked_reason, "plugin_capability_revoked")
            self.assertEqual(terminal.result["capability_result"]["value"], {"changed": False})
            self.assertEqual(terminal.delivery_receipt["model_status"], "not_requested")
            self.assertEqual(self.engine.turns, [])
            self.network.assert_not_called()
        finally:
            gate.set()
            await active.stop()


class DesktopCompletionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_completion_uses_existing_desktop_frame_without_speech_and_records_rejection(self):
        from companion_v01.durable_session_queue import DurableSessionWorkQueue
        from companion_v01.session_inbox import SessionInboxStore
        from companion_v01.routes.think import build_think_router
        from companion_v01.bot_runtime import _host_job_completion_request
        from tests.test_plugin_events import _RouteEngine

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = _RouteEngine()
            jobs = engine.job_store = HostJobStore(root / "jobs.db")
            queue = DurableSessionWorkQueue(SessionInboxStore(root / "inbox.db"))
            handlers, frames = {}, []
            fail = False
            async def deliver(frame):
                frames.append(frame)
                return {"ok": not fail, "status": "rejected" if fail else "queued"}
            build_think_router(engine=engine, public_guard=SimpleNamespace(), runtime_metrics=SimpleNamespace(),
                log_event=lambda *args, **kwargs: None, session_work_queue=queue,
                plugin_agent_event_handler_registrar=handlers.__setitem__, desktop_agent_frame_delivery=deliver,
                desktop_agent_event_available=lambda: True)
            owner = HostJobOwner("master", "desk")
            try:
                for number, fail in ((1, False), (2, True)):
                    artifact = {"generated_id": f"generated::{number}", "generated_handle": f"gen_{number:03d}",
                        "created_by_tool": "fixture.register", "send_to_user": True, "output_title": "check", "output_format": "txt"}
                    created = jobs.create(owner=owner, capability_source="tool", capability_id="fixture.register",
                        payload={"followup": {"default": "required", "consumer": "model"}},
                        idempotency_key=str(number), argument_fingerprint=str(number), channel="desktop_pet",
                        character_pack_id="reimu", delivery_target="desktop-ref", turn_id="origin", completion_mode="agent")
                    claim = jobs.claim(created["job_id"], worker_id="test")
                    jobs.succeed(created["job_id"], claim_token=claim["claim_token"],
                        result={"followup": {"mode": "none"}, "capability_result": Result(
                            value=False, content={"managed_artifacts": [artifact]}, followup="none").as_dict()})
                    job = jobs.get(created["job_id"], owner=owner)
                    accepted = await handlers["desktop_pet"](_host_job_completion_request(job), {
                        "channel": "desktop_pet", "kind": "direct", "recipient": "desktop:desk",
                        "session": "desk", "profile": "master", "character": "reimu"})
                    self.assertTrue(accepted.ok, accepted)
                    async with asyncio.timeout(5):
                        while not (job := jobs.get(job.job_id, owner=owner)).delivery_receipt:
                            await asyncio.sleep(0.01)
                    self.assertEqual(job.delivery_receipt["status"], "failed" if fail else "completed")
                    self.assertEqual(job.delivery_receipt["delivery_status"], "rejected" if fail else "queued")
                    self.assertEqual(job.delivery_receipt["model_status"], "not_requested")
                self.assertEqual(engine.turns, [])
                self.assertEqual(len(frames), 2)
                for frame in frames:
                    self.assertEqual(frame["speech"], "")
                    self.assertEqual(frame["speech_segments"], [])
                    self.assertEqual(frame["tool_events"][0]["type"], "generated_file_ready")
                    self.assertNotIn("activity", frame)
            finally:
                await queue.close()


class JobResultQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_query_retains_full_result_and_excludes_execution_credentials(self):
        import httpx
        from fastapi import FastAPI
        from companion_v01.routes.plugins import build_plugins_router

        with tempfile.TemporaryDirectory() as directory:
            store = HostJobStore(Path(directory) / "private-host-jobs.db")
            owner = HostJobOwner("owner", "session")
            created = store.create(owner=owner, capability_source="tool", capability_id="example.check",
                payload={"private_host_path": "C:/private-host-location", "request_token": "private-request-token"},
                idempotency_key="check", argument_fingerprint="args", delivery_target="private-conversation-reference")
            claim = store.claim(created["job_id"], worker_id="private-worker")
            value = {"rows": list(range(10000)), "token_count": 0, "business_path": "C:/project/report", "empty": None}
            store.succeed(created["job_id"], claim_token=claim["claim_token"], result={"capability_result": Result(value=value, followup="none").as_dict(),
                "followup": {"mode": "none"}})
            job = store.get(created["job_id"], owner=owner)
            receipt = {"stage": "delivery", "status": "failed", "model_status": "not_requested", "delivery_status": "failed"}
            store.record_delivery_receipt(job.job_id, owner=owner, completion_event_id=job.completion_event_id, receipt=receipt)
            # A callback acknowledging the queue after the actual channel result
            # must not replace it, even when using a separate DB connection.
            reopened = HostJobStore(Path(directory) / "private-host-jobs.db")
            reopened.record_delivery_receipt(job.job_id, owner=owner, completion_event_id=job.completion_event_id,
                receipt={"stage": "admission", "status": "accepted"}, admission_only=True)
            app = FastAPI()
            app.include_router(build_plugins_router(extension_management_service=SimpleNamespace(), job_store=reopened))
            url = f"/admin/plugins/jobs/{job.job_id}"
            params = {"profile_user_id": owner.profile_user_id, "session_id": owner.session_id}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://test") as client:
                response = await client.get(url, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["result"]["value"], value)
                self.assertEqual(payload["result"]["followup"], "none")
                self.assertEqual(payload["delivery_receipt"], receipt)
                self.assertFalse(payload["followup"]["requires_model"])
                self.assertEqual((await client.get(url)).status_code, 400)
                self.assertEqual((await client.get(url, params={**params, "session_id": "foreign"})).status_code, 404)
                for forbidden in ("private-host-location", "private-request-token", "private-conversation-reference", "private-host-jobs.db", claim["claim_token"]):
                    self.assertNotIn(forbidden, response.text)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 1)), base_url="http://test") as client:
                self.assertEqual((await client.get(url, params=params)).status_code, 403)

    async def test_public_job_control_pause_resume_stop_is_owner_scoped(self):
        import httpx
        from fastapi import FastAPI
        from companion_v01.routes.plugins import build_plugins_router

        with tempfile.TemporaryDirectory() as directory:
            store = HostJobStore(Path(directory) / "control.db")
            owner = HostJobOwner("owner", "session")
            created = store.create(
                owner=owner, capability_source="tool", capability_id="example.control",
                payload={}, idempotency_key="control", argument_fingerprint="args",
            )
            app = FastAPI()
            app.include_router(build_plugins_router(extension_management_service=SimpleNamespace(), job_store=store))
            url = f"/admin/plugins/jobs/{created['job_id']}/control"
            params = {"profile_user_id": owner.profile_user_id, "session_id": owner.session_id}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://test") as client:
                paused = await client.post(url, params=params, json={"action": "pause"})
                self.assertEqual(paused.status_code, 200, paused.text)
                self.assertEqual(paused.json()["status"], "paused")
                self.assertEqual((await client.post(url, params={**params, "session_id": "other"}, json={"action": "resume"})).status_code, 404)
                resumed = await client.post(url, params=params, json={"action": "resume"})
                self.assertEqual(resumed.json()["status"], "queued")
                stopped = await client.post(url, params=params, json={"action": "stop"})
                self.assertEqual(stopped.json()["status"], "cancelled")
                self.assertEqual((await client.post(url, params=params, json={"action": "resume"})).json()["status"], "cancelled")
