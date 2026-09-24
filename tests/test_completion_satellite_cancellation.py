"""Desktop invocation dispatch respects withdrawal at the actual send boundary."""
import asyncio
import contextvars
import threading
import time
from types import SimpleNamespace
import unittest

from companion_v01.desktop_satellite import DesktopSatelliteService, _SatelliteConnection
from companion_v01.desktop_satellite_specs import SYSTEM_MEDIA_CONTROL_TOOL_SPEC as SPEC
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.turn_coordination import cancellation_scope


class CompletionSatelliteCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.revoked = threading.Event()
        self.service = DesktopSatelliteService(instance_id="instance", token="test")
        self.connection = _SatelliteConnection("connection", "lease", "offer", asyncio.get_running_loop(),
            asyncio.Queue(), time.time() + 60, time.time(), "instance", frozenset((SPEC.capability_id,)))
        self.assertTrue(self.service._install_connection(self.connection))
        self.receipt = self.service.resolve_receipt(SPEC)
        self.broker = ExecutorBroker(self.service)
        self.sent = []
        self.tasks = []

    async def asyncTearDown(self):
        self.service._remove_connection(self.connection.connection_id)
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def start_dispatch(self):
        with cancellation_scope(self.revoked.is_set):
            task = asyncio.create_task(asyncio.to_thread(self.broker.execute,
                spec=SPEC, receipt_value=self.receipt.as_dict(), invocation_id="media-control",
                arguments={"action": "play"}, timeout_seconds=1))
        self.tasks.append(task)
        payload = await asyncio.wait_for(self.connection.outbound.get(), 3)
        self.connection.outbound.put_nowait(payload)
        return task

    def start_sender(self, send):
        task = asyncio.create_task(self.service._send_loop(SimpleNamespace(send_json=send), self.connection),
                                   context=contextvars.Context())
        self.tasks.append(task)
        return task

    async def send(self, payload):
        self.sent.append(payload)

    async def test_withdrawal_after_enqueue_prevents_actual_websocket_send(self):
        dispatch = await self.start_dispatch()
        self.revoked.set()
        self.start_sender(self.send)
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "unavailable_before_dispatch")
        self.assertEqual(result.reason, "turn_scope_revoked")
        self.assertFalse(self.sent)
        self.assertFalse(self.service._pending)

    async def test_revoked_scope_never_enqueues_at_entry(self):
        self.revoked.set()
        with cancellation_scope(self.revoked.is_set):
            result = await asyncio.to_thread(self.service.dispatch,
                spec=SPEC, receipt=self.receipt, invocation_id="revoked-before-entry",
                arguments={"action": "play"}, timeout_seconds=1)
        self.assertEqual(result.reason, "turn_scope_revoked")
        self.assertTrue(self.connection.outbound.empty())

    async def test_timed_out_queued_invocation_cannot_be_sent_later(self):
        dispatch = await self.start_dispatch()
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "unavailable_before_dispatch")
        drained = asyncio.Event()
        async def send(payload):
            if payload.get("type") == "test_drain":
                drained.set()
            else:
                self.sent.append(payload)
        self.connection.outbound.put_nowait({"type": "test_drain"})
        self.start_sender(send)
        await asyncio.wait_for(drained.wait(), 3)
        self.assertFalse(self.sent)

    async def test_send_without_ack_is_unknown_even_if_scope_is_withdrawn(self):
        dispatch = await self.start_dispatch()
        async def send(payload):
            self.sent.append(payload)
            self.revoked.set()
        self.start_sender(send)
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "execution_unknown")
        self.assertEqual(result.reason, "executor_ack_timeout")
        self.assertEqual(len(self.sent), 1)

    async def test_disconnect_after_send_without_ack_is_unknown(self):
        dispatch = await self.start_dispatch()
        async def send(payload):
            self.sent.append(payload)
            self.service._remove_connection(self.connection.connection_id)
        self.start_sender(send)
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "execution_unknown")
        self.assertEqual(result.reason, "executor_disconnected_after_dispatch")

    async def test_result_after_send_survives_revocation(self):
        dispatch = await self.start_dispatch()
        async def send(payload):
            self.sent.append(payload)
            self.revoked.set()
            await self.service._handle_client_message(self.connection, {
                **payload, "type": "result", "status": "succeeded", "data": {"ok": True}})
        self.start_sender(send)
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.data["ok"])
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("cancelled", self.sent[0])

    async def test_failed_scope_check_does_not_send_or_hang_dispatch(self):
        dispatch = await self.start_dispatch()
        def broken_check():
            raise RuntimeError("scope unavailable")
        with self.service._lock:
            self.service._pending["media-control"].cancelled = broken_check
        self.start_sender(self.send)
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "unavailable_before_dispatch")
        self.assertEqual(result.reason, "turn_scope_check_failed")
        self.assertFalse(self.sent)

    async def test_disconnect_before_send_remains_unavailable(self):
        dispatch = await self.start_dispatch()
        self.service._remove_connection(self.connection.connection_id)
        result = await asyncio.wait_for(dispatch, 3)
        self.assertEqual(result.status, "unavailable_before_dispatch")
        self.assertEqual(result.reason, "executor_disconnected_before_accept")
        self.assertFalse(self.sent)

    async def test_frame_receipt_expiring_during_scope_check_prevents_late_send(self):
        delivery = asyncio.create_task(self.service.deliver_agent_frame({"speech": "stale"}, bot_id="instance"))
        self.tasks.append(delivery)
        frame = await asyncio.wait_for(self.connection.outbound.get(), 3)
        pending = self.service._pending_agent_frames[frame["delivery_id"]]
        def expire_receipt():
            # wait_for expiry/cancellation cancels this same cross-loop Future.
            pending.result.cancel()
            return False
        pending.cancelled = expire_receipt
        self.connection.outbound.put_nowait(frame)
        self.connection.outbound.put_nowait({"type": "test_drain"})
        drained = asyncio.Event()
        async def send(payload):
            if payload.get("type") == "test_drain":
                drained.set()
            else:
                self.sent.append(payload)
        self.start_sender(send)
        await asyncio.wait_for(drained.wait(), 3)
        await asyncio.gather(delivery, return_exceptions=True)
        self.assertFalse(self.sent)
        self.assertFalse(self.service._pending_agent_frames)
