from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from companion_v01.durable_session_queue import DurableSessionWorkQueue, RetryableSessionWorkError
from companion_v01.session_inbox import SessionInboxStore


def fields(event_id: str, *, group: str = "same", session: str = "session", **overrides) -> dict:
    return {
        "session_key": f"profile\0{session}",
        "profile_user_id": "profile",
        "session_id": session,
        "kind": "turn",
        "source": "test",
        "source_event_id": event_id,
        "payload": {"group": group, "message": event_id},
        **overrides,
    }


def batch_key(item) -> str:
    return str(item.payload.get("group") or "")


class BatchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "inbox.db"
        self.now = 100.0
        self.store = SessionInboxStore(self.path, clock=lambda: self.now)

    def claim(self, **kwargs):
        return self.store.claim_next("profile\0session", worker_id="test", batch_key=batch_key, **kwargs)

    def test_eleven_inputs_claim_and_settle_as_one_envelope(self):
        for i in range(11):
            self.store.enqueue(**fields(str(i)))
        claim = self.claim()
        self.assertEqual([i.source_event_id for i in claim["items"]], [str(i) for i in range(11)])
        self.assertEqual(len({i.batch_id for i in claim["items"]}), 1)
        self.assertEqual(self.claim()["status"], "busy")
        self.assertTrue(self.store.settle_claims(claim["items"], status="committed")["ok"])
        self.assertEqual(self.store.pending_count("profile\0session"), 0)

    def test_user_input_and_identity_changes_are_barriers(self):
        for barrier in (
            {"payload": {"message": "user"}},
            {"profile_user_id": "other"},
            {"source": "other"},
            {"kind": "passive"},
            {"group": "another"},
        ):
            with self.subTest(barrier=barrier):
                store = SessionInboxStore(Path(self.temp.name) / f"case{len(list(Path(self.temp.name).iterdir()))}.db")
                store.enqueue(**fields("a"))
                store.enqueue(**fields("b", **barrier))
                store.enqueue(**fields("c"))
                result = store.claim_next("profile\0session", worker_id="test", batch_key=batch_key)
                self.assertEqual([i.source_event_id for i in result["items"]], ["a"])

    def test_retry_waiting_head_cannot_be_overtaken_including_steer(self):
        first = self.store.enqueue(**fields("a"), available_at=110)
        second = self.store.enqueue(**fields("b"))
        self.assertEqual(self.claim()["status"], "deferred")
        self.assertEqual(self.claim(expected_item_id=second["item_id"])["status"], "deferred")
        self.now = 110
        self.assertEqual(self.claim()["item"].item_id, first["item_id"])

    def test_retry_and_reopen_preserve_membership_and_batch_identity(self):
        for i in range(3):
            self.store.enqueue(**fields(str(i)))
        first = self.claim()
        self.assertTrue(self.store.settle_claims(first["items"], status="queued", retry_delay_seconds=2)["ok"])
        self.store.enqueue(**fields("later"))
        self.now += 2
        self.store = SessionInboxStore(self.path, clock=lambda: self.now)
        retried = self.claim(max_batch_items=1)
        self.assertEqual([i.item_id for i in retried["items"]], [i.item_id for i in first["items"]])
        self.assertEqual(retried["item"].batch_id, first["item"].batch_id)
        self.assertTrue(all(i.attempts == 2 for i in retried["items"]))

    def test_atomic_settlement_rejects_stale_or_partial_claim_without_committing_any(self):
        for i in range(2):
            self.store.enqueue(**fields(str(i)))
        items = self.claim()["items"]
        self.assertFalse(self.store.settle_claims(items[:1], status="committed")["ok"])
        self.assertFalse(self.store.settle_claims([replace(items[0], batch_id="")], status="committed")["ok"])
        self.assertFalse(self.store.commit(items[0].item_id, claim_token=items[0].claim_token)["ok"])
        stale = [items[0], replace(items[1], claim_token="stale")]
        self.assertFalse(self.store.settle_claims(stale, status="committed")["ok"])
        self.assertTrue(all(self.store.get(i.item_id).status == "claimed" for i in items))

    def test_crash_before_ack_recovers_entire_batch_without_changing_ids(self):
        for i in range(2):
            self.store.enqueue(**fields(str(i)))
        first = self.claim()
        self.store = SessionInboxStore(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.recover_abandoned_claims(), 2)
        second = self.claim()
        self.assertEqual(second["item"].batch_id, first["item"].batch_id)
        self.assertFalse(self.store.settle_claims(first["items"], status="committed")["ok"])
        self.assertTrue(self.store.settle_claims(second["items"], status="committed")["ok"])

    def test_chunk_limits_do_not_drop_tail_or_split_oversized_single_input(self):
        for i in range(7):
            self.store.enqueue(**fields(str(i)))
        sizes = []
        while (claim := self.claim(max_batch_items=3)).get("ok"):
            sizes.append(len(claim["items"]))
            self.store.settle_claims(claim["items"], status="committed")
        self.assertEqual(sizes, [3, 3, 1])
        self.store.enqueue(**fields("big", payload={"group": "same", "message": "x" * 1000}))
        self.store.enqueue(**fields("small"))
        self.assertEqual(len(self.claim(max_batch_bytes=10)["items"]), 1)

    def test_old_database_is_upgraded_without_changing_payloads(self):
        row = self.store.enqueue(**fields("old"))
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP INDEX idx_session_inbox_batch")
            conn.execute("ALTER TABLE session_inbox_items DROP COLUMN batch_id")
            conn.execute("ALTER TABLE session_inbox_items DROP COLUMN processing_started_at")
            conn.execute("ALTER TABLE session_inbox_items DROP COLUMN input_fingerprint")
        conn.close()
        reopened = SessionInboxStore(self.path)
        self.assertEqual(reopened.get(row["item_id"]).payload, fields("old")["payload"])
        self.assertEqual(reopened.get(row["item_id"]).batch_id, "")

    def test_started_model_turn_is_not_replayed_after_crash_or_retryable_error(self):
        for i in range(2):
            self.store.enqueue(**fields(str(i)))
        items = self.claim()["items"]
        self.assertTrue(self.store.begin_processing(items)["ok"])
        self.assertFalse(self.store.begin_processing(items)["ok"])
        self.assertEqual(self.store.settle_claims(items, status="queued")["status"], "failed")
        self.assertTrue(all(self.store.get(i.item_id).last_error == "session_turn_outcome_unknown" for i in items))
        self.store.enqueue(**fields("crash"))
        crashed = self.claim()["items"]
        self.store.begin_processing(crashed)
        self.store.recover_abandoned_claims()
        self.assertEqual(self.store.get(crashed[0].item_id).status, "failed")
        self.assertEqual(self.claim()["status"], "idle")


class BatchQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionInboxStore(Path(self.temp.name) / "inbox.db")

    async def drain(self, queue, key="profile\0session"):
        async with asyncio.timeout(5):
            while queue.has_work(key):
                await asyncio.sleep(0.01)

    async def test_real_queue_batches_eleven_and_keeps_user_message_separate(self):
        calls = []
        queue = DurableSessionWorkQueue(self.store)

        async def handler(key, items):
            calls.append([i.source_event_id for i in items])

        queue.register_handler("test", handler, batch_key=batch_key)
        for i in range(11):
            await queue.enqueue(**fields(str(i)), schedule=False)
        await queue.enqueue(**fields("user", group=""), schedule=False)
        await queue.schedule_session("profile\0session")
        await self.drain(queue)
        self.assertEqual(calls, [[str(i) for i in range(11)], ["user"]])

    async def test_direct_active_turn_settlement_wakes_completions_queued_while_busy(self):
        calls = []
        queue = DurableSessionWorkQueue(self.store)

        async def handler(key, items):
            calls.extend(i.source_event_id for i in items)

        queue.register_handler("test", handler, batch_key=batch_key)
        first = await queue.enqueue(**fields("user", group=""), schedule=False)
        active = await queue.claim_for_active_turn("profile\0session", first["item_id"])
        await queue.enqueue(**fields("completion"))
        async with asyncio.timeout(1):
            while queue._workers:
                await asyncio.sleep(0.01)
        self.assertEqual(calls, [])
        await queue.commit_claim(first["item_id"], claim_token=active["claim_token"])
        await self.drain(queue)
        self.assertEqual(calls, ["completion"])

    async def test_retry_batch_is_frozen_and_later_user_does_not_overtake(self):
        calls = []
        queue = DurableSessionWorkQueue(self.store)

        async def handler(key, items):
            calls.append([i.source_event_id for i in items])
            if len(calls) == 1:
                await queue.enqueue(**fields("user", group=""))
                raise RetryableSessionWorkError("offline", retry_delay_seconds=0.02)

        queue.register_handler("test", handler, batch_key=batch_key)
        for i in range(2):
            await queue.enqueue(**fields(str(i)), schedule=False)
        await queue.schedule_session("profile\0session")
        await self.drain(queue)
        self.assertEqual(calls, [["0", "1"], ["0", "1"], ["user"]])

    async def test_long_handler_renews_claim_and_other_sessions_still_progress(self):
        queue = DurableSessionWorkQueue(self.store, lease_seconds=1)
        entered, release, other_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handler(key, items):
            if key.endswith("other"):
                other_done.set()
                return
            entered.set()
            await release.wait()

        queue.register_handler("test", handler, batch_key=batch_key)
        first = await queue.enqueue(**fields("slow"))
        await entered.wait()
        try:
            await queue.enqueue(**fields("other", session="other"))
            await asyncio.wait_for(other_done.wait(), 1)
            await asyncio.sleep(1.1)
            self.assertGreater(self.store.get(first["item_id"]).lease_until, time.time())
            self.assertEqual(self.store.claim_next("profile\0session", worker_id="second")["status"], "busy")
        finally:
            release.set()
            await self.drain(queue)

    async def test_completion_arriving_during_idle_retirement_is_not_stranded(self):
        calls = []
        original = self.store.claim_next
        injected = False

        def claim(*args, **kwargs):
            nonlocal injected
            result = original(*args, **kwargs)
            if result.get("status") == "idle" and not injected:
                injected = True
                self.store.enqueue(**fields("raced"))
            return result

        self.store.claim_next = claim
        queue = DurableSessionWorkQueue(self.store)

        async def handler(key, items):
            calls.extend(i.source_event_id for i in items)

        queue.register_handler("test", handler, batch_key=batch_key)
        await queue.enqueue(**fields("first"))
        await self.drain(queue)
        self.assertEqual(calls, ["first", "raced"])

    async def test_busy_owner_collects_all_ready_results_next_without_waiting_for_slow_jobs(self):
        calls, started, release = [], asyncio.Event(), asyncio.Event()
        queue = DurableSessionWorkQueue(self.store)

        async def handler(key, items):
            calls.append([item.source_event_id for item in items])
            if len(calls) == 1:
                started.set()
                await release.wait()

        queue.register_handler("test", handler, batch_key=batch_key)
        await queue.enqueue(**fields("first"))
        await started.wait()
        for i in range(10):
            await queue.enqueue(**fields(str(i)))
        release.set()
        await self.drain(queue)
        self.assertEqual(calls, [["first"], [str(i) for i in range(10)]])
        # An arbitrarily slow job can settle later; no all-jobs barrier.
        await queue.enqueue(**fields("late"))
        await self.drain(queue)
        self.assertEqual(calls[-1], ["late"])
