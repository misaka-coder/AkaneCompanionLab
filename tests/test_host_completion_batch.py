from __future__ import annotations

import asyncio
import copy
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from companion_v01.bot_runtime import _host_job_completion_request
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.host_completion_batch import completion_batch_key, completion_metadata, completion_turn_payload
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.routes import qq as qq_routes, think as think_routes
from companion_v01.session_inbox import SessionInboxStore
from tests import test_plugin_agent_events as event_helpers


def terminal_request(store, number, *, status="succeeded", turn="parent-turn", channel="qq", reference="ref-master"):
    owner = HostJobOwner("master", "master" if channel == "qq" else "desk")
    created = store.create(
        owner=owner,
        capability_source="tool",
        capability_id="document.compose",
        payload={},
        idempotency_key=f"call-{number}",
        argument_fingerprint=f"args-{number}",
        character_pack_id="reimu",
        channel=channel,
        delivery_target=reference,
        turn_id=turn,
        tool_call_id=f"call-{number}",
        completion_mode="agent",
        memory_mode="timeline",
    )
    claimed = store.claim(created["job_id"], worker_id="test")
    if status == "succeeded":
        store.succeed(
            created["job_id"],
            claim_token=claimed["claim_token"],
            result_summary=f"File {number}",
            artifacts=[{"handle": f"gen_{number:03d}", "send_to_user": False}],
        )
    else:
        store.fail(created["job_id"], claim_token=claimed["claim_token"], error="generator_failed", retryable=False)
    return _host_job_completion_request(store.get(created["job_id"], owner=owner))


class CompletionProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jobs = HostJobStore(Path(self.temp.name) / "jobs.db")
        self.inbox = SessionInboxStore(Path(self.temp.name) / "inbox.db")
        self.reference = {"channel": "desktop_pet", "profile": "master", "session": "desk", "character": "reimu"}

    def put(self, number, **changes):
        request = terminal_request(self.jobs, number, channel="desktop_pet")
        payload = {
            "message": request.message,
            "memory_message": request.message,
            "timestamp": 100 + number,
            "turn_kind": "plugin_event",
            "character_pack_id": "reimu",
            "client_mode": "desktop_pet",
            "plugin_external_event": {
                "event_type": request.event_type,
                "source": request.source,
                "data": dict(request.data),
            },
            "memory_idempotency_key": request.idempotency_key,
            **changes,
        }
        result = self.inbox.enqueue(
            session_key="master\0desk",
            profile_user_id="master",
            session_id="desk",
            source="desktop_pet",
            kind="turn",
            source_event_id=request.idempotency_key,
            payload={"turn_payload": payload, "host_completion": completion_metadata(request, self.reference)},
        )
        return self.inbox.get(result["item_id"])

    def test_event_identity_and_every_result_survive_real_engine_and_memcore_rendering(self):
        from companion_v01.engine import AkaneMemoryEngine
        from memcore import render_external_event_text

        first, second = self.put(1), self.put(2)
        claim = self.inbox.claim_next("master\0desk", worker_id="test", batch_key=completion_batch_key)
        result = completion_turn_payload(claim["items"])
        event = AkaneMemoryEngine._pop_plugin_external_event(dict(result), event_allowed=True)
        self.assertIsNotNone(event)
        rendered = render_external_event_text(
            event_type=event["event_type"],
            fields=event["data"],
            source=event["source"],
        )
        for item in (first, second):
            self.assertIn(item.source_event_id, rendered)
        for token in ("gen_001", "gen_002", "call-1", "call-2", "available_not_delivered"):
            self.assertIn(token, rendered)
        self.assertNotIn("host_completion", result)
        self.assertIn("未列出的任务状态未知", result["message"])

    def test_new_authority_or_presentation_fields_default_to_isolation(self):
        first = self.put(1)
        for changes in (
            {"actor_stable_id": "qq:other"},
            {"character_pack_id": "cecilia"},
            {"transient_user_message": True},
            {"chat_model_override": "different"},
            {"future_authority_field": "different"},
        ):
            altered = copy.deepcopy(first.payload)
            altered["turn_payload"].update(changes)
            self.assertNotEqual(completion_batch_key(first), completion_batch_key(replace(first, payload=altered)))

    def test_unrelated_plugin_or_missing_causal_identity_is_not_batchable(self):
        first = self.put(1)
        for source in ("plugin.jobs", ""):
            altered = copy.deepcopy(first.payload)
            altered["turn_payload"]["plugin_external_event"]["source"] = source
            self.assertEqual(completion_batch_key(replace(first, payload=altered)), "")
        altered = copy.deepcopy(first.payload)
        altered["host_completion"] = {}
        self.assertEqual(completion_batch_key(replace(first, payload=altered)), "")


class ChannelCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionInboxStore(Path(self.temp.name) / "inbox.db")
        self.jobs = HostJobStore(Path(self.temp.name) / "jobs.db")
        self.pending = []

        def defer(coroutine):
            self.pending.append(coroutine)
            return types.SimpleNamespace(done=lambda: False)

        self.queue = DurableSessionWorkQueue(self.store, schedule_task=defer)

    async def asyncTearDown(self):
        for coroutine in self.pending:
            coroutine.close()

    async def run_pending(self):
        while self.pending:
            coroutine = self.pending.pop(0)
            await asyncio.wait_for(coroutine, 5)

    async def test_qq_eleven_real_job_projections_enter_one_normal_delivery_turn(self):
        helper = event_helpers.QQTurnRouterTests()
        port = helper._build(session_work_queue=self.queue)
        requests = [terminal_request(self.jobs, n) for n in range(11)]
        for request in requests:
            self.assertTrue((await port.submit(request)).ok)
        calls = []

        def process(**kwargs):
            calls.append(kwargs)
            return {
                "frame": {"speech": "集中交付", "emotion": "happy"},
                "send_result": {"ok": True, "status": "sent"},
                "file_send_result": {"ok": True, "count": 11, "results": []},
            }

        with patch.object(qq_routes, "_process_qq_turn_streaming", process):
            await self.run_pending()
        self.assertEqual(len(calls), 1)
        payload = calls[0]["turn_payload"]
        for n in range(11):
            self.assertIn(f"gen_{n:03d}", payload["message"])
        self.assertEqual(payload["plugin_external_event"]["data"]["job_count"], "11")
        self.assertEqual(calls[0]["context"].source_message_id, payload["source_message_id"])
        # The same completion callback may be retried after delivery/host restart.
        with patch.object(qq_routes.time, "time", return_value=2_000_000_000):
            self.assertTrue((await port.submit(requests[0])).ok)
        self.assertEqual(self.pending, [])
        self.assertEqual(self.store.pending_count("master\0master"), 0)

    async def test_qq_partial_delivery_is_failed_not_committed_or_automatically_replayed(self):
        port = event_helpers.QQTurnRouterTests()._build(session_work_queue=self.queue)
        for n in range(2):
            await port.submit(terminal_request(self.jobs, n))

        def process(**kwargs):
            return {
                "frame": {"speech": "部分失败"},
                "send_result": {"ok": True},
                "file_send_result": {"ok": False, "count": 1, "status": "partial"},
            }

        with patch.object(qq_routes, "_process_qq_turn_streaming", process):
            await self.run_pending()
        self.assertEqual(self.store.pending_count("master\0master"), 0)
        self.assertEqual(self.queue._store.recover_abandoned_claims(), 0)
        with self.store._connect() as connection:
            rows = connection.execute("SELECT status, last_error FROM session_inbox_items").fetchall()
        self.assertEqual([row["status"] for row in rows], ["failed", "failed"])
        self.assertTrue(all(row["last_error"] == "host_completion_turn_or_delivery_incomplete" for row in rows))

    async def test_duplicate_changed_instruction_reuses_frozen_payload_but_changed_facts_reject(self):
        port = event_helpers.QQTurnRouterTests()._build(session_work_queue=self.queue)
        request = terminal_request(self.jobs, 1)
        self.assertTrue((await port.submit(request)).ok)
        changed_message = replace(request, message="A revised instruction after restart")
        self.assertTrue((await port.submit(changed_message)).ok)
        changed_fields = dict(request.data)
        changed_fields["status"] = "failed"
        collision = replace(request, data=changed_fields)
        self.assertFalse((await port.submit(collision)).ok)
        claimed = self.store.claim_next("master\0master", worker_id="test")
        self.assertEqual(claimed["item"].payload["turn_payload"]["message"], request.message)

    async def test_combined_handles_use_real_send_file_handler_and_normal_gateway_once(self):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01.attachment_inbox import AttachmentInboxService
        from companion_v01.store import MemoryStore
        from companion_v01.tool_runtime import SendFileToolHandler, ToolExecutionContext
        from companion_v01.qq_gateway import NapCatQQGateway
        from tests.generated_artifact_fixtures import register_text_artifact

        memory = MemoryStore(Path(self.temp.name) / "memory")
        service = GeneratedFileService(
            base_dir=Path(self.temp.name) / "files",
            store=memory,
            attachment_service=AttachmentInboxService(store=memory, base_dir=Path(self.temp.name) / "attachments"),
        )
        for n in range(1, 12):
            register_text_artifact(
                service,
                profile_user_id="master",
                session_id="master",
                output_title=f"result{n}",
                output_format="txt",
                content=f"real fixture {n}",
                timestamp=n,
            )
        port = event_helpers.QQTurnRouterTests()._build(session_work_queue=self.queue)
        for n in range(1, 12):
            await port.submit(terminal_request(self.jobs, n))
        invocations, deliveries = [], []
        gateway = object.__new__(NapCatQQGateway)

        def send_targets(context, targets):
            deliveries.append(targets)
            return {"ok": True, "count": len(targets), "results": []}

        gateway._send_generated_file_targets = send_targets

        def process(**kwargs):
            event_fields = kwargs["turn_payload"]["plugin_external_event"]["data"]
            handles = [value for key, value in event_fields.items() if key.endswith("_artifact_handles")]
            handler = SendFileToolHandler(generated_file_service=service)
            call = handler.normalize_call({"type": "send_file", "targets": handles})
            invocations.append(call)
            result = handler.execute(
                call=call,
                context=ToolExecutionContext(
                    profile_user_id="master",
                    session_id="master",
                    now_ts=100,
                    visual_payload={},
                    client_mode="qq_text",
                ),
            )
            sent = gateway.send_generated_files(kwargs["context"], result.stream_events)
            return {"frame": {"speech": "批量交付"}, "send_result": {"ok": True}, "file_send_result": sent}

        with patch.object(qq_routes, "_process_qq_turn_streaming", process):
            await self.run_pending()
        self.assertEqual(len(invocations), 1)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual([item["handle"] for item in deliveries[0]], [f"gen_{n:03d}" for n in range(1, 12)])
        self.assertTrue(all(Path(item["path"]).is_file() for item in deliveries[0]))

    async def test_desktop_batch_reaches_one_full_frame_with_mixed_results(self):
        handlers, payloads, frames = {}, [], []

        class Engine:
            def process_turn(self, payload):
                payloads.append(copy.deepcopy(payload))
                return {
                    "speech": "两项成功，一项失败",
                    "speech_segments": ["两项成功，一项失败"],
                    "emotion": "thinking",
                    "activity": {"action": "pause"},
                    "_debug": {"secret": "private"},
                }

        async def deliver(frame):
            frames.append(frame)
            return {"ok": True, "status": "queued"}

        think_routes.build_think_router(
            engine=Engine(),
            public_guard=types.SimpleNamespace(),
            runtime_metrics=types.SimpleNamespace(),
            log_event=lambda *a, **k: None,
            session_work_queue=self.queue,
            plugin_agent_event_handler_registrar=handlers.__setitem__,
            desktop_agent_frame_delivery=deliver,
            desktop_agent_event_available=lambda: True,
        )
        reference = {
            "channel": "desktop_pet",
            "kind": "direct",
            "recipient": "desktop:desk",
            "session": "desk",
            "profile": "master",
            "character": "reimu",
        }
        for n in range(3):
            request = terminal_request(self.jobs, n, channel="desktop_pet", status="failed" if n == 1 else "succeeded")
            self.assertTrue((await handlers["desktop_pet"](request, reference)).ok)
        await self.run_pending()
        self.assertEqual(len(payloads), 1)
        self.assertIn("generator_failed", payloads[0]["message"])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["emotion"], "thinking")
        self.assertEqual(frames[0]["speech_segments"], ["两项成功，一项失败"])
        self.assertNotIn("_debug", frames[0])
