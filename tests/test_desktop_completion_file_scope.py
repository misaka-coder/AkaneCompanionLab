"""Direct desktop file receipts keep independent completion revocation scopes."""
import asyncio
import contextvars
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from akane_plugin import Result
from companion_v01.bot_runtime import _host_job_completion_request
from companion_v01.desktop_satellite import DesktopSatelliteService, _SatelliteConnection
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.routes.think import build_think_router
from companion_v01.session_inbox import SessionInboxStore
from tests.test_plugin_events import _RouteEngine


class DesktopCompletionFileScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_revoked_direct_file_does_not_suppress_other_file(self):
        await self.check_delivery()

    async def test_revoked_direct_file_does_not_suppress_completed_model_reply(self):
        await self.check_delivery(with_model=True)

    async def test_second_file_revocation_preserves_first_file_receipt(self):
        await self.check_delivery(revoke_number=2)

    async def check_delivery(self, *, with_model=False, revoke_number=1):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = _RouteEngine()
            jobs = engine.job_store = HostJobStore(root / "jobs.db")
            owner = HostJobOwner("master", "desk")
            gate = asyncio.Event()
            async def deferred(coroutine):
                await gate.wait()
                await coroutine
            queue = DurableSessionWorkQueue(SessionInboxStore(root / "inbox.db"),
                schedule_task=lambda coroutine: asyncio.create_task(deferred(coroutine)))
            service = DesktopSatelliteService(instance_id="instance", token="test")
            connection = _SatelliteConnection("connection", "lease", "offer", asyncio.get_running_loop(),
                asyncio.Queue(), time.time() + 60, time.time(), "instance", frozenset())
            self.assertTrue(service._install_connection(connection))
            handlers, sent, job_ids = {}, [], {}
            async def send(payload):
                sent.append(payload["payload"])
                await service._handle_client_message(connection, {**payload,
                    "type": "agent_event_result", "status": "queued"})
            async def deliver(frame):
                delivery = asyncio.create_task(service.deliver_agent_frame(frame, bot_id="instance"))
                sender = None
                try:
                    queued = await asyncio.wait_for(connection.outbound.get(), 3)
                    connection.outbound.put_nowait(queued)
                    ids = [event.get("generated_file", {}).get("generated_id")
                           for event in frame.get("tool_events", [])]
                    if f"generated::{revoke_number}" in ids:
                        HostJobStore(root / "jobs.db").revoke_job_capability(job_ids[revoke_number], owner=owner)
                    sender = asyncio.create_task(service._send_loop(SimpleNamespace(send_json=send), connection),
                                                 context=contextvars.Context())
                    return await asyncio.wait_for(delivery, 3)
                finally:
                    if sender:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
                    if not delivery.done():
                        delivery.cancel()
                    await asyncio.gather(delivery, return_exceptions=True)
            build_think_router(engine=engine, public_guard=SimpleNamespace(), runtime_metrics=SimpleNamespace(),
                log_event=lambda *args, **kwargs: None, session_work_queue=queue,
                plugin_agent_event_handler_registrar=handlers.__setitem__, desktop_agent_frame_delivery=deliver,
                desktop_agent_event_available=lambda: True)
            try:
                for number in range(1, 4 if with_model else 3):
                    followup = "required" if number == 3 else "none"
                    created = jobs.create(owner=owner, capability_source="tool", capability_id="fixture.register",
                        payload={"followup": {"default": "required", "consumer": "model"}},
                        idempotency_key=str(number), argument_fingerprint=str(number), channel="desktop_pet",
                        character_pack_id="reimu", delivery_target="desktop-ref", turn_id="origin", completion_mode="agent")
                    job_ids[number] = created["job_id"]
                    claim = jobs.claim(created["job_id"], worker_id="fixture")
                    artifact = {"generated_id": f"generated::{number}", "generated_handle": f"gen_{number:03d}",
                        "created_by_tool": "fixture.register", "send_to_user": True, "output_title": "check", "output_format": "txt"}
                    jobs.succeed(created["job_id"], claim_token=claim["claim_token"], result_summary="created",
                        result={"followup": {"mode": followup}, "capability_result": Result(
                            value=number, content={"managed_artifacts": [artifact]} if number < 3 else {},
                            followup=followup).as_dict()})
                    job = jobs.get(created["job_id"], owner=owner)
                    accepted = await handlers["desktop_pet"](_host_job_completion_request(job), {
                        "channel": "desktop_pet", "kind": "direct", "recipient": "desktop:desk",
                        "session": "desk", "profile": "master", "character": "reimu"})
                    self.assertTrue(accepted.ok)
                gate.set()
                async with asyncio.timeout(8):
                    while not all(jobs.get(job_id, owner=owner).delivery_receipt.get("status") in {"completed", "failed"}
                                  for job_id in job_ids.values()):
                        await asyncio.sleep(0.01)
                files = [event["generated_file"]["generated_id"] for frame in sent
                         for event in frame.get("tool_events", [])]
                self.assertEqual(files, [f"generated::{3 - revoke_number}"])
                self.assertEqual(len(engine.turns), int(with_model))
                if with_model:
                    self.assertEqual([frame["speech"] for frame in sent if frame.get("speech")], ["收到事件"])
                    receipt = jobs.get(job_ids[3], owner=owner).delivery_receipt
                    self.assertEqual(receipt["model_status"], "completed")
                    self.assertEqual(receipt["status"], "completed")
                    self.assertEqual(receipt["delivery_status"], "queued")
                    self.assertNotIn("files", receipt)
                cancelled = jobs.get(job_ids[revoke_number], owner=owner).delivery_receipt
                self.assertEqual(cancelled["status"], "failed")
                self.assertEqual(cancelled["model_status"], "not_requested")
                kept = jobs.get(job_ids[3 - revoke_number], owner=owner).delivery_receipt
                self.assertEqual(kept["status"], "completed")
                self.assertEqual(kept["delivery_status"], "queued")
                self.assertFalse(service._pending_agent_frames)
            finally:
                gate.set()
                await queue.close()
