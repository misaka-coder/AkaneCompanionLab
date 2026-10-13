"""A slow model must not expire an otherwise unchanged, heartbeating device."""
import asyncio
from dataclasses import replace
import unittest

from companion_v01.desktop_satellite import DesktopSatelliteService, _SatelliteConnection
from companion_v01.desktop_satellite_specs import SYSTEM_MEDIA_CONTROL_TOOL_SPEC as SPEC
from companion_v01.capability_registry import ExecutorBroker


class SatelliteReceiptRenewalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.service = DesktopSatelliteService(instance_id="host", token="test", clock=lambda: self.now)
        self.connection = _SatelliteConnection("connection", "lease", "offer", asyncio.get_running_loop(),
            asyncio.Queue(), self.now + 30, self.now, "bot", frozenset((SPEC.capability_id,)))
        self.assertTrue(self.service._install_connection(self.connection))
        self.source = self.service.for_bot(bot_id="bot", memory_space_id="memory")
        self.receipt = self.source.resolve_receipt(SPEC)
        self.broker = ExecutorBroker(self.source, clock=lambda: self.now)

    async def asyncTearDown(self):
        self.service._remove_connection(self.connection.connection_id)

    async def heartbeats_during_generation(self):
        for _ in range(7):
            self.now += 20
            await self.service._handle_client_message(self.connection,
                {"type": "heartbeat", "lease_epoch": self.connection.lease_epoch})
            self.assertEqual((await self.connection.outbound.get())["type"], "heartbeat_ack")

    async def test_slow_generation_dispatches_once_on_the_same_live_scoped_offer(self):
        original = self.receipt.as_dict()
        await self.heartbeats_during_generation()
        self.assertLess(self.receipt.offer_expires_at, self.now)
        self.assertTrue(self.service.diagnostics()["connected"])
        task = asyncio.create_task(asyncio.to_thread(self.broker.execute,
            spec=SPEC, receipt_value=original, invocation_id="slow-model-play", arguments={"action": "play"}, timeout_seconds=1))
        payload = await asyncio.wait_for(self.connection.outbound.get(), 2)
        self.assertEqual(payload["lease_epoch"], self.receipt.lease_epoch)
        self.assertEqual(payload["offer_id"], self.receipt.offer_id)
        await self.service._handle_client_message(self.connection, {
            "type": "result", "instance_id": "host", "lease_epoch": payload["lease_epoch"],
            "offer_id": payload["offer_id"], "invocation_id": payload["invocation_id"],
            "tool_id": SPEC.capability_id, "status": "succeeded", "data": {"ok": True, "action": "play"}})
        result = await asyncio.wait_for(task, 2)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.receipt.as_dict(), original, "Frozen model context stays unchanged")
        replay = self.broker.execute(spec=SPEC, receipt_value=original, invocation_id="slow-model-play", arguments={"action": "play"})
        self.assertEqual(replay, result)
        self.assertTrue(self.connection.outbound.empty())

    async def test_expired_offline_offer_still_refuses_execution(self):
        self.now += 40
        result = self.broker.execute(spec=SPEC, receipt_value=self.receipt.as_dict(), invocation_id="offline", arguments={"action": "play"})
        self.assertEqual(result.reason, "offer_expired")
        self.assertTrue(self.connection.outbound.empty())

    async def test_reconnection_and_contract_or_bot_changes_cannot_rebind_a_frozen_call(self):
        await self.heartbeats_during_generation()
        for receipt in (replace(self.receipt, instance_id="another-bot"),
                        replace(self.receipt, tool_id="open_browser"),
                        replace(self.receipt, schema_hash="changed"),
                        replace(self.receipt, schema_version=self.receipt.schema_version + 1),
                        replace(self.receipt, offer_id="new-offer"),
                        replace(self.receipt, lease_epoch="new-lease")):
            with self.subTest(receipt=receipt):
                self.assertIsNone(self.source.refresh_receipt(SPEC, receipt))
        self.service._remove_connection(self.connection.connection_id)
        self.connection = replace(self.connection, connection_id="new-connection", lease_epoch="new-lease",
            offer_id="new-offer", expires_at=self.now + 30)
        self.assertTrue(self.service._install_connection(self.connection))
        result = self.broker.execute(spec=SPEC, receipt_value=self.receipt.as_dict(), invocation_id="changed-device", arguments={"action": "play"})
        self.assertEqual(result.reason, "offer_expired")
        self.assertTrue(self.connection.outbound.empty())
