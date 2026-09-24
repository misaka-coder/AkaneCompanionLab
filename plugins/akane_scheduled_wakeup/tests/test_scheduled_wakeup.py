"""Tests for the scheduled wake-up plugin (event-subscription design, v0.2.0)."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from akane_scheduled_wakeup import (
    PLUGIN_ID,
    SERVICE_ID,
    SUBSCRIPTION_ID,
    WAKEUP_EVENT,
    DueTimer,
    TimerStateError,
    TimerStore,
    _status_text,
    _timer_command,
    _timer_service,
    _verdict,
    _wakeup_handler,
    _wakeup_tool,
    create_plugin,
)


class _Clock:
    def __init__(self, start: int = 1_700_000_000) -> None:
        self.value = start

    def __call__(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += seconds


class _Binding:
    def __init__(self, scope_id: str = "scope-1", status: str = "bound", reason: str = "") -> None:
        self.scope_id = scope_id
        self.status = status
        self.reason = reason


class _Delivery:
    def __init__(self, scope_id: str, *, status: str = "queued", value=None, reason: str = "") -> None:
        self.subscription_id = SUBSCRIPTION_ID
        self.scope_id = scope_id
        self.status = status
        self.reason = reason
        self.value = value
        self.has_value = value is not None
        self.cancel_requested = False
        self.linked_dispatches: tuple[str, ...] = ()
        self.linked_turn_requests: tuple[str, ...] = ()


class _Receipt:
    def __init__(self, *, status: str = "accepted", dispatch_id: str = "dispatch-1",
                 deliveries=(), complete: bool = False, reason: str = "") -> None:
        self.dispatch_id = dispatch_id
        self.event_id = "event-1"
        self.status = status
        self.complete = complete
        self.reason = reason
        self.deliveries = tuple(deliveries)


class _TurnReceipt:
    def __init__(self, *, status: str = "queued", request_id: str = "req-1", reason: str = "") -> None:
        self.request_id = request_id
        self.status = status
        self.reason = reason

    def as_dict(self):
        return {"request_id": self.request_id, "status": self.status, "reason": self.reason}


class _FakeEvents:
    """Only the three ports the plugin actually uses."""

    def __init__(self, *, binding: _Binding | None = None, receipts=()) -> None:
        self.binding = binding if binding is not None else _Binding()
        self.bound: list[str] = []
        self.emitted: list[dict] = []
        self._receipts = list(receipts)
        self.status_calls: list[str] = []

    async def bind(self, subscription_id: str) -> _Binding:
        self.bound.append(subscription_id)
        return self.binding

    async def emit(self, event_type: str, data, *, event_key: str = "", occurred_at_ms=None) -> _Receipt:
        self.emitted.append({"event_type": event_type, "data": data, "event_key": event_key})
        if not self._receipts:
            raise AssertionError("unexpected emit")
        return self._receipts.pop(0)

    async def status(self, dispatch_id: str) -> _Receipt:
        self.status_calls.append(dispatch_id)
        if not self._receipts:
            raise AssertionError("unexpected status poll")
        return self._receipts.pop(0)


class _ToolContext:
    def __init__(self, *, session_id: str = "session-1", events=None) -> None:
        self.invocation = types.SimpleNamespace(
            session_id=session_id, profile_user_id="user-1", character_pack_id="cecilia",
            conversation_ref="ref-1",
        )
        self.events = events


class _BackgroundContext:
    """Runs a bounded number of loop passes, then asks for shutdown."""

    def __init__(self, events, clock: _Clock, *, passes: int = 1) -> None:
        self.events = events
        self.shutdown_requested = False
        self._clock = clock
        self._passes = passes
        self._sleeps = 0

    async def sleep(self, seconds: float) -> bool:
        self._clock.advance(int(round(seconds)))
        # Only the long loop sleep ends a pass; the short verification poll
        # inside one wake-up must not be mistaken for shutdown.
        if seconds >= 1.0:
            self._sleeps += 1
            if self._sleeps >= self._passes:
                self.shutdown_requested = True
                return False
        return True


class _HandlerContext:
    def __init__(self, scope_id: str, *, receipt: _TurnReceipt | None = None) -> None:
        self.scope_id = scope_id
        self.calls: list[dict] = []
        self._receipt = receipt if receipt is not None else _TurnReceipt()

    async def request_turn(self, reason, data=None, **kwargs):
        self.calls.append({"reason": reason, "data": data, **kwargs})
        return self._receipt


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.clock = _Clock()
        self.store = TimerStore(Path(self._temp.name), clock=self.clock)
        self.key = "conversation-key"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _schedule(self, *, delay: int = 60, binding: str = "scope-1") -> dict:
        return self.store.schedule(key=self.key, binding=binding, delay_seconds=delay, note="喝水")

    def test_schedule_then_status_reports_pending(self) -> None:
        self._schedule()
        item = self.store.status(key=self.key)
        self.assertIsNotNone(item)
        self.assertTrue(item["enabled"])
        self.assertEqual(item["fired_at"], 0)
        self.assertIn("约 1 分钟", _status_text(item, now=self.clock()))

    def test_claim_due_only_returns_arrived_timers(self) -> None:
        self._schedule(delay=60)
        self.assertEqual(self.store.claim_due(), ())
        self.clock.advance(61)
        due = self.store.claim_due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].binding, "scope-1")
        self.assertEqual(due[0].note, "喝水")
        self.assertEqual(due[0].late_seconds, 1)

    def test_delivered_timer_is_not_claimed_again(self) -> None:
        self._schedule(delay=60)
        self.clock.advance(61)
        due = self.store.claim_due()[0]
        self.assertTrue(self.store.is_current(due))
        self.store.finish(due, delivered=True, status="turn_requested")
        self.assertFalse(self.store.is_current(due))
        self.assertEqual(self.store.claim_due(), ())
        self.assertEqual(self.store.status(key=self.key)["fired_at"], due.due_at)

    def test_failed_wake_keeps_retrying_after_backoff(self) -> None:
        self._schedule(delay=60)
        self.clock.advance(61)
        due = self.store.claim_due()[0]
        self.store.finish(due, delivered=False, status="conversation_not_bound")
        item = self.store.status(key=self.key)
        self.assertEqual(item["fired_at"], 0)
        self.assertEqual(item["attempts"], 1)
        # Retry is held back instead of hammering the host every pass.
        self.assertEqual(self.store.claim_due(), ())
        self.clock.advance(61)
        self.assertEqual(len(self.store.claim_due()), 1)

    def test_unbound_timer_says_so_instead_of_pretending(self) -> None:
        self._schedule(delay=60, binding="")
        self.clock.advance(61)
        due = self.store.claim_due()[0]
        self.store.finish(due, delivered=False, status="conversation_not_bound")
        text = _status_text(self.store.status(key=self.key), now=self.clock())
        self.assertIn("没登记到唤醒通道", text)

    def test_expired_and_cancel_and_clear(self) -> None:
        self._schedule(delay=60)
        self.clock.advance(61)
        due = self.store.claim_due()[0]
        self.store.mark_expired(due)
        self.assertIn("过期作废", _status_text(self.store.status(key=self.key), now=self.clock()))
        self._schedule(delay=60)
        self.assertTrue(self.store.cancel(key=self.key))
        self.assertFalse(self.store.cancel(key=self.key))
        self.assertTrue(self.store.clear(key=self.key))
        self.assertIsNone(self.store.status(key=self.key))

    def test_set_binding_refreshes_only_pending_timers(self) -> None:
        self._schedule(delay=60)
        self.assertTrue(self.store.set_binding(key=self.key, binding="scope-2"))
        self.assertFalse(self.store.set_binding(key=self.key, binding="scope-2"))
        self.clock.advance(61)
        due = self.store.claim_due()[0]
        self.assertEqual(due.binding, "scope-2")
        self.store.finish(due, delivered=True, status="turn_requested")
        self.assertFalse(self.store.set_binding(key=self.key, binding="scope-3"))

    def test_corrupt_ledger_is_reported_not_ignored(self) -> None:
        (Path(self._temp.name) / "timers.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(TimerStateError) as caught:
            self.store.status(key=self.key)
        self.assertEqual(caught.exception.reason, "state_invalid")


class VerdictTestCase(unittest.TestCase):
    def test_no_delivery_means_this_conversation_is_not_bound(self) -> None:
        self.assertEqual(_verdict(_Receipt(deliveries=()), "scope-1"), (False, "conversation_not_bound"))

    def test_a_delivery_for_another_binding_is_stale(self) -> None:
        receipt = _Receipt(deliveries=[_Delivery("scope-9")])
        self.assertEqual(_verdict(receipt, "scope-1"), (False, "subscription_binding_stale"))

    def test_another_conversation_claiming_the_event_is_stale(self) -> None:
        receipt = _Receipt(deliveries=[
            _Delivery("scope-1", status="completed", value={"outcome": "other_conversation"}),
        ])
        self.assertEqual(_verdict(receipt, "scope-1"), (False, "subscription_binding_stale"))

    def test_failed_delivery_keeps_the_real_reason(self) -> None:
        receipt = _Receipt(deliveries=[_Delivery("scope-1", status="failed", reason="agent_turn_scope_expired")])
        self.assertEqual(_verdict(receipt, "scope-1"), (False, "agent_turn_scope_expired"))

    def test_completed_delivery_with_a_queued_turn_counts_as_woken(self) -> None:
        value = {"outcome": "requested", "receipt": {"request_id": "req-1", "status": "queued", "reason": ""}}
        receipt = _Receipt(deliveries=[_Delivery("scope-1", status="completed", value=value)])
        self.assertEqual(_verdict(receipt, "scope-1"), (True, "turn_requested"))

    def test_completed_delivery_with_a_refused_turn_is_not_woken(self) -> None:
        value = {"outcome": "requested", "receipt": {"request_id": "", "status": "rejected", "reason": "context_unbound"}}
        receipt = _Receipt(deliveries=[_Delivery("scope-1", status="completed", value=value)])
        self.assertEqual(_verdict(receipt, "scope-1"), (False, "context_unbound"))

    def test_still_running_is_unconfirmed_so_the_caller_keeps_polling(self) -> None:
        receipt = _Receipt(status="running", deliveries=[_Delivery("scope-1", status="running")])
        self.assertEqual(_verdict(receipt, "scope-1"), (False, "unconfirmed"))


class HandlerTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_handler_claims_only_its_own_conversation(self) -> None:
        handler = _wakeup_handler()
        ctx = _HandlerContext("scope-1")
        event = types.SimpleNamespace(data={"binding": "scope-2", "note": "别人的"})
        result = await handler(event, ctx)
        self.assertEqual(result, {"outcome": "other_conversation"})
        self.assertEqual(ctx.calls, [])

    async def test_handler_requests_one_turn_through_its_signed_scope(self) -> None:
        handler = _wakeup_handler()
        ctx = _HandlerContext("scope-1")
        event = types.SimpleNamespace(data={"binding": "scope-1", "note": "喝水", "due_at": 123, "late_seconds": 4})
        result = await handler(event, ctx)
        self.assertEqual(result["outcome"], "requested")
        self.assertEqual(result["receipt"]["status"], "queued")
        self.assertEqual(len(ctx.calls), 1)
        self.assertEqual(ctx.calls[0]["data"]["note"], "喝水")
        self.assertEqual(ctx.calls[0]["coalesce_key"], f"{SUBSCRIPTION_ID}:scope-1")


class ToolTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.clock = _Clock()
        self.store = TimerStore(Path(self._temp.name), clock=self.clock)
        self.tool = _wakeup_tool(lambda: self.store)

    def tearDown(self) -> None:
        self._temp.cleanup()

    async def test_schedule_binds_the_conversation_and_records_it(self) -> None:
        events = _FakeEvents()
        ctx = _ToolContext(events=events)
        result = await self.tool(action="schedule", delay_minutes=5, note="跑校园跑", ctx=ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(events.bound, [SUBSCRIPTION_ID])
        item = self.store.status(key=result.value and next(iter(self.store._read_locked()["timers"])))
        self.assertEqual(item["binding"], "scope-1")
        self.assertEqual(item["note"], "跑校园跑")
        self.assertEqual(item["due_at"], self.clock() + 300)

    async def test_schedule_without_a_binding_is_refused_honestly(self) -> None:
        events = _FakeEvents(binding=_Binding(scope_id="", status="rejected", reason="context_unbound"))
        result = await self.tool(action="schedule", delay_minutes=5, note="", ctx=_ToolContext(events=events))
        self.assertTrue(result.is_error)
        self.assertEqual(result.reason, "context_unbound")

    async def test_status_and_cancel_go_through_the_tool(self) -> None:
        events = _FakeEvents()
        ctx = _ToolContext(events=events)
        await self.tool(action="schedule", delay_minutes=5, note="", ctx=ctx)
        state = await self.tool(action="status", ctx=ctx)
        self.assertIn("约 5 分钟", state.content)
        cancelled = await self.tool(action="cancel", ctx=ctx)
        self.assertEqual(cancelled.content, "已取消定时唤醒。")
        again = await self.tool(action="cancel", ctx=ctx)
        self.assertEqual(again.content, "没有可取消的定时唤醒。")

    async def test_out_of_range_delay_is_rejected(self) -> None:
        result = await self.tool(action="schedule", delay_minutes=0, note="", ctx=_ToolContext(events=_FakeEvents()))
        self.assertTrue(result.is_error)
        self.assertEqual(result.reason, "delay_minutes_out_of_range")

    async def test_test_action_emits_a_targeted_event(self) -> None:
        value = {"outcome": "requested", "receipt": {"request_id": "req-1", "status": "queued", "reason": ""}}
        receipt = _Receipt(deliveries=[_Delivery("scope-1", status="completed", value=value)], complete=True)
        events = _FakeEvents(receipts=[receipt])
        ctx = _ToolContext(events=events)
        result = await self.tool(action="test", note="试试", ctx=ctx)
        self.assertFalse(result.is_error)
        self.assertTrue(result.value["wakeup_requested"])
        self.assertEqual(events.emitted[0]["event_type"], WAKEUP_EVENT)
        self.assertEqual(events.emitted[0]["data"]["binding"], "scope-1")


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.clock = _Clock()
        self.store = TimerStore(Path(self._temp.name), clock=self.clock)
        self.key = "conversation-key"
        self.service = _timer_service(lambda: self.store)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _schedule_due(self, *, binding: str = "scope-1", delay: int = 60) -> None:
        self.store.schedule(key=self.key, binding=binding, delay_seconds=delay, note="起床")
        self.clock.advance(delay + 1)

    async def test_service_publishes_one_event_and_marks_it_fired(self) -> None:
        self._schedule_due()
        value = {"outcome": "requested", "receipt": {"request_id": "req-1", "status": "queued", "reason": ""}}
        events = _FakeEvents(receipts=[
            _Receipt(status="accepted", deliveries=[_Delivery("scope-1", status="completed", value=value)],
                     complete=True),
        ])
        ctx = _BackgroundContext(events, self.clock)
        await self.service(ctx)
        self.assertEqual(len(events.emitted), 1)
        self.assertEqual(events.emitted[0]["data"]["binding"], "scope-1")
        item = self.store.status(key=self.key)
        self.assertEqual(item["fired_at"], item["due_at"])
        self.assertEqual(item["last_status"], "turn_requested")

    async def test_service_keeps_retrying_when_nothing_is_bound(self) -> None:
        self._schedule_due()
        events = _FakeEvents(receipts=[_Receipt(status="unobserved", deliveries=(), complete=True)])
        ctx = _BackgroundContext(events, self.clock)
        await self.service(ctx)
        item = self.store.status(key=self.key)
        self.assertEqual(item["fired_at"], 0)
        self.assertEqual(item["last_status"], "conversation_not_bound")
        self.assertTrue(item["retry_after"] > self.clock())

    async def test_service_polls_until_the_delivery_resolves(self) -> None:
        self._schedule_due()
        value = {"outcome": "requested", "receipt": {"request_id": "req-1", "status": "queued", "reason": ""}}
        events = _FakeEvents(receipts=[
            _Receipt(status="accepted", dispatch_id="dispatch-1",
                     deliveries=[_Delivery("scope-1", status="running")]),
            _Receipt(status="completed", dispatch_id="dispatch-1",
                     deliveries=[_Delivery("scope-1", status="completed", value=value)], complete=True),
        ])
        ctx = _BackgroundContext(events, self.clock)
        await self.service(ctx)
        self.assertEqual(events.status_calls, ["dispatch-1"])
        self.assertEqual(self.store.status(key=self.key)["last_status"], "turn_requested")

    async def test_stale_timer_is_expired_instead_of_waking_anyone(self) -> None:
        self.store.schedule(key=self.key, binding="scope-1", delay_seconds=60, note="")
        self.clock.advance(60 + 4000)
        events = _FakeEvents()
        await self.service(_BackgroundContext(events, self.clock))
        self.assertEqual(events.emitted, [])
        item = self.store.status(key=self.key)
        self.assertEqual(item["last_status"], "expired")
        self.assertEqual(item["fired_at"], 0)

    async def test_unbound_timer_never_reaches_the_event_port(self) -> None:
        self.store.schedule(key=self.key, binding="", delay_seconds=60, note="")
        self.clock.advance(61)
        events = _FakeEvents()
        await self.service(_BackgroundContext(events, self.clock))
        self.assertEqual(events.emitted, [])
        self.assertEqual(self.store.status(key=self.key)["last_status"], "conversation_not_bound")


class CommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.clock = _Clock()
        self.store = TimerStore(Path(self._temp.name), clock=self.clock)
        self.command = _timer_command(lambda: self.store)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _request(self, args: str, *, session_id: str = "session-1"):
        return types.SimpleNamespace(
            args=args, session_id=session_id, conversation_ref="ref-1",
            profile_user_id="user-1", character_pack_id="cecilia",
        )

    async def test_status_and_off_are_answered_without_the_model(self) -> None:
        first = await self.command(self._request("status"))
        self.assertTrue(first.handled)
        self.assertIn("还没有设定", first.reply_text)
        self.store.schedule(key=self.store_key(), binding="scope-1", delay_seconds=600, note="")
        self.assertIn("约 10 分钟", (await self.command(self._request("status"))).reply_text)
        off = await self.command(self._request("off"))
        self.assertEqual(off.reply_text, "已取消定时唤醒。")

    def store_key(self) -> str:
        from akane_scheduled_wakeup import _conversation_key

        return _conversation_key(self._request("status"))

    async def test_setting_a_timer_is_handed_to_the_turn_that_can_bind(self) -> None:
        result = await self.command(self._request("10 喝水"))
        self.assertFalse(result.handled)
        self.assertEqual(result.reason, "defer_to_turn_for_binding")
        self.assertIsNone(self.store.status(key=self.store_key()))

    async def test_bad_arguments_get_usage(self) -> None:
        result = await self.command(self._request("wat"))
        self.assertIn("/timer", result.reply_text)


class WiringTestCase(unittest.TestCase):
    def test_plugin_declares_subscription_service_and_permissions(self) -> None:
        plugin = create_plugin()
        permissions = set(plugin.manifest.permissions)
        self.assertLessEqual(
            {"event.emit", "event.subscribe", "storage.write", "agent.turn.request", "job.run",
             "qq.command.register"},
            permissions,
        )
        subscription = plugin._subscriptions[0][0]
        self.assertEqual(subscription.subscription_id, SUBSCRIPTION_ID)
        self.assertEqual(subscription.event_type, WAKEUP_EVENT)
        self.assertEqual(subscription.scope, "conversation")
        self.assertFalse(subscription.request_turn)
        self.assertEqual([item[0] for item in plugin._background_services], [SERVICE_ID])
        self.assertEqual(plugin.manifest.plugin_id, PLUGIN_ID)


if __name__ == "__main__":
    unittest.main()
