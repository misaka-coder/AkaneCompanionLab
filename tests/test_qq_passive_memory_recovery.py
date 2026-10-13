from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from companion_v01.durable_session_queue import DurableSessionWorkQueue, RetryableSessionWorkError
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.qq_passive_messages import QQPassiveMessageHandler
from companion_v01.qq_session_work import passive_batch_key, passive_retry_can_yield
from companion_v01.qq_turn_runtime import QQTurnRunner
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.turn_coordination import TurnCoordinator
from tests import test_qq_passive_memory_record as fixtures
from tests.test_qq_passive_memory_record import _Attention, _MemcoreManager, _PassiveEngine, _engine, _passive_payload


class QQPassiveHandlerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, receipt, *, source_message_id="1001", record_error=None, enqueue_error=None):
        context = SimpleNamespace(
            should_record=True, profile_user_id="profile", session_id="session", group_id=123,
            user_id=10001, character_pack_id="cecilia", reason="not_mentioned", forward_refs=(), attachments=[],
        )
        context.to_turn_payload = lambda: _passive_payload(
            user_id="session", real_user_id="profile", source_message_id=source_message_id,
        )
        recorder = Mock(return_value=receipt, side_effect=record_error)
        enqueue = AsyncMock(return_value={"ok": True, "status": "queued"}, side_effect=enqueue_error)
        attention = _Attention()
        attention.supersede = Mock()
        handler = QQPassiveMessageHandler(
            engine=SimpleNamespace(record_passive_qq_message=recorder), config_module=SimpleNamespace(),
            runtime_metrics=SimpleNamespace(observe_request=Mock()), log_event=Mock(),
            turn_coordinator=SimpleNamespace(is_busy=lambda *_args: False),
            session_queue=SimpleNamespace(queue=SimpleNamespace(has_work=lambda _key: False), enqueue=enqueue),
            attention=attention,
        )
        return handler, context, recorder, enqueue, attention

    async def run_handler(self, handler, context, *, record_only=False):
        response = await handler.handle(
            context=context, event={"time": 100}, started_at=time.perf_counter(), record_only_command=record_only,
        )
        self.assertEqual(response.status_code, 200)
        return json.loads(response.body)

    async def test_idle_memcore_failure_is_durably_enqueued_with_original_source_and_time(self):
        receipt = {"ok": False, "status": "record_failed", "source_id": "first-record", "reason": "locked"}
        handler, context, _recorder, enqueue, attention = self.make_handler(receipt)
        result = await self.run_handler(handler, context)
        self.assertEqual(result["status"], "buffered")
        self.assertFalse(result["record_result"]["ok"])
        retry = enqueue.await_args.kwargs
        self.assertEqual(retry["kind"], "passive")
        self.assertEqual(retry["turn_payload"]["passive_memory_source_id"], "first-record")
        self.assertEqual(retry["turn_payload"]["timestamp"], 100)
        self.assertEqual(attention.scheduled, [])

    async def test_success_keeps_fast_direct_recording_and_attention(self):
        handler, context, _recorder, enqueue, attention = self.make_handler({"ok": True, "source_id": "stored"})
        result = await self.run_handler(handler, context)
        self.assertEqual(result["status"], "recorded")
        enqueue.assert_not_awaited()
        self.assertEqual(attention.scheduled[0]["projection_anchor_source_id"], "stored")

    async def test_local_write_exception_without_qq_id_keeps_one_identity_for_recovery(self):
        handler, context, recorder, enqueue, _attention = self.make_handler(
            {}, source_message_id="", record_error=sqlite3.OperationalError("database is locked"),
        )
        result = await self.run_handler(handler, context)
        self.assertEqual(result["status"], "buffered")
        self.assertEqual(result["record_result"]["reason"], "OperationalError")
        first = recorder.call_args.args[0]["passive_memory_source_id"]
        self.assertTrue(first.startswith("qq-passive-host:"))
        self.assertEqual(enqueue.await_args.kwargs["turn_payload"]["passive_memory_source_id"], first)

    async def test_failed_recovery_remains_visible_instead_of_claiming_success(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                handler, context, _recorder, enqueue, attention = self.make_handler(
                    {"ok": False, "status": "record_failed"},
                    enqueue_error=sqlite3.OperationalError("database is locked") if raises else None,
                )
                enqueue.return_value = {"ok": False, "status": "stopping"}
                result = await self.run_handler(handler, context)
                self.assertEqual(result["status"], "record_failed")
                self.assertFalse(result["recovery"]["ok"])
                self.assertEqual(attention.scheduled, [])

    async def test_record_only_failure_recovers_without_triggering_attention(self):
        handler, context, _recorder, enqueue, attention = self.make_handler({"ok": False, "status": "record_failed"})
        result = await self.run_handler(handler, context, record_only=True)
        self.assertEqual(result["status"], "buffered")
        self.assertTrue(enqueue.await_args.kwargs["record_only"])
        self.assertEqual(attention.scheduled, [])


class QQPassiveQueueRecoveryTests(unittest.IsolatedAsyncioTestCase):
    _owner = fixtures.QQPassiveMemoryQueueTests._owner
    _enqueue = fixtures.QQPassiveMemoryQueueTests._enqueue

    async def test_real_memcore_recovers_a_partly_written_media_batch_without_duplicates(self):
        with ExitStack() as cleanup:
            temp_dir = cleanup.enter_context(tempfile.TemporaryDirectory())
            inbox = SessionInboxStore(Path(temp_dir) / "inbox.db")
            manager = MemcoreManager(
                backend="memcore", storage_path=Path(temp_dir) / "core.db", visible_scope="conversation",
                enable_flavor=False, shadow_compare=False, llm=fixtures._FakeLLM(),
                embedding_provider=fixtures._FakeEmbeddingProvider(),
            )
            cleanup.callback(manager.close)
            engine = _engine(str(Path(temp_dir) / "host"), manager)
            owner, queue, _context = self._owner(inbox, engine)
            original = manager.append_standalone_message
            count = 0

            def append(record, **kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    return {"ok": False, "reason": "injected_write_failure"}
                return original(record, **kwargs)

            async def prepare(*, context, event, base_payload):
                prepared = dict(base_payload)
                prepared["message"] = prepared["memory_message"] = base_payload["message"] + "\nqq.attachments: img_first"
                return prepared, {}, []

            owner._prepare_passive_payload = AsyncMock(side_effect=prepare)
            for i in range(2):
                self._enqueue(inbox, event_id=str(i), turn_payload=_passive_payload(
                    user_id="session", real_user_id="profile", source_message_id=str(i),
                    message=f"媒体消息{i}", actor_stable_id=f"qq:{i}", timestamp=100 + i,
                ))
            with patch.object(manager, "append_standalone_message", append), patch(
                "companion_v01.qq_session_queue._PASSIVE_RECORD_RETRY_DELAY_SECONDS", 0.01,
            ):
                await asyncio.wait_for(queue._drain("profile\0session"), 5)
            self.assertEqual(count, 4)
            self.assertEqual(owner._prepare_passive_payload.await_count, 2)
            system = manager._get_system(profile_user_id="profile", session_id="session", character_pack_id="cecilia")
            entries = system.store.list_prompt_visible_entries(namespace=system.namespace)
            self.assertEqual(len(entries), 2)
            self.assertEqual([entry.semantic_text for entry in entries], [
                "媒体消息0\nqq.attachments: img_first", "媒体消息1\nqq.attachments: img_first",
            ])
            self.assertEqual(len({entry.source_id for entry in entries}), 2)
            self.assertEqual(inbox.pending_count("profile\0session"), 0)
            self.assertEqual(len(owner.attention.scheduled), 1)

    async def test_partial_media_write_reuses_first_record_across_retry_and_restart(self):
        for source_message_id in ("1002", ""):
            with self.subTest(source_message_id=source_message_id), tempfile.TemporaryDirectory() as temp_dir:
                now = [100.0]
                inbox = SessionInboxStore(Path(temp_dir) / "inbox.db", clock=lambda: now[0])
                manager = _MemcoreManager({"ok": False, "reason": "locked"})
                engine = _engine(str(Path(temp_dir) / "host"), manager)
                owner, queue, _context = self._owner(inbox, engine)
                enriched = []
                text = '【群友】一张图片\nqq.attachments:\n  - handle: "img_first"'

                async def prepare(*, context, event, base_payload):
                    enriched.append(dict(base_payload))
                    return {
                        **base_payload, "message": text, "memory_message": text, "timestamp": 100,
                        "forward_references": [{"forward_id": "forward-first", "status": "resolved", "nodes": [
                            {"actor_id": "qq:other", "text": "首次转发内容", "timestamp": 90},
                        ]}],
                    }, {}, []

                owner._prepare_passive_payload = prepare
                queued = self._enqueue(
                    inbox, event_id=source_message_id, turn_payload=_passive_payload(
                        user_id="session", real_user_id="profile", source_message_id=source_message_id,
                        actor_stable_id="qq:10001", actor_display_name="群友", timestamp=100,
                    ),
                )
                first = inbox.claim_next("profile\0session", worker_id="first", batch_key=queue._batch_key)
                with self.assertRaises(RetryableSessionWorkError) as raised:
                    await owner.handle("profile\0session", first["items"])
                inbox.settle_claims(first["items"], status="queued", error=raised.exception.reason, retry_delay_seconds=5)
                self.assertEqual(len(enriched), 1)

                now[0] = 106
                reopened = SessionInboxStore(inbox.database_path, clock=lambda: now[0])
                restored, resumed, _context = self._owner(reopened, engine)
                restored._prepare_passive_payload = AsyncMock(side_effect=AssertionError("must not re-enrich"))
                await asyncio.wait_for(resumed._drain("profile\0session"), 2)

                self.assertEqual(reopened.get(queued["item_id"]).status, "committed")
                self.assertEqual(reopened.get(queued["item_id"]).attempts, 2)
                restored._prepare_passive_payload.assert_not_awaited()
                self.assertEqual(len(manager.calls), 2)
                self.assertEqual(manager.calls[0], manager.calls[1])
                record = manager.calls[-1]["record"]
                self.assertEqual(record["content"], text)
                self.assertEqual(record["timestamp"], 100)
                self.assertEqual(record["forward_references"][0]["nodes"][0]["text"], "首次转发内容")
                with engine.store._connect() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0], 1)
                self.assertEqual(len(restored.attention.scheduled), 1)

    async def test_record_lookup_lock_retries_but_invalid_scope_fails_once(self):
        for error in (sqlite3.OperationalError("database is locked"), None):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temp_dir:
                inbox = SessionInboxStore(Path(temp_dir) / "inbox.db")
                engine = _PassiveEngine([])
                record = {"profile_user_id": "wrong", "session_id": "session", "character_pack_id": "cecilia", "role": "user"}
                read = Mock(side_effect=[error, None] if error else None, return_value=record)
                engine.store = SimpleNamespace(get_message_by_source_id=read)
                owner, queue, _context = self._owner(inbox, engine)
                queued = self._enqueue(inbox, event_id="1001", turn_payload={
                    "message": "hello", "source_message_id": "1001", "passive_memory_source_id": "frozen",
                })
                with patch("companion_v01.qq_session_queue._PASSIVE_RECORD_RETRY_DELAY_SECONDS", 0.01):
                    await asyncio.wait_for(queue._drain("profile\0session"), 2)
                item = inbox.get(queued["item_id"])
                self.assertEqual(item.status, "committed" if error else "failed")
                self.assertEqual(item.attempts, 2 if error else 1)
                if not error:
                    self.assertEqual(item.last_error, "passive_record_scope_mismatch")
                    self.assertEqual(engine.batches, [])

    async def test_production_registration_batches_senders_and_keeps_addressed_turn_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([])
            owner, queue, _context = self._owner(inbox, engine)
            for i in range(12):
                self._enqueue(inbox, event_id=str(i), turn_payload={
                    "message": f"第{i}条", "source_message_id": str(i), "actor_stable_id": f"qq:{i}",
                })
            inbox.enqueue(
                session_key="profile\0session", profile_user_id="profile", session_id="session",
                source="qq", kind="turn", source_event_id="addressed", payload={"turn_payload": {"message": "@塞西"}},
            )
            self._enqueue(inbox, event_id="later", turn_payload={"message": "后来的观察", "source_message_id": "later"})
            claim = inbox.claim_next("profile\0session", worker_id="test", batch_key=queue._batch_key)
            self.assertEqual(len(claim["items"]), 12)
            await owner.handle("profile\0session", claim["items"])
            inbox.settle_claims(claim["items"], status="committed")
            self.assertEqual([len(batch) for batch in engine.batches], [12])
            next_claim = inbox.claim_next("profile\0session", worker_id="test", batch_key=queue._batch_key)
            self.assertEqual([item.source_event_id for item in next_claim["items"]], ["addressed"])
            self.assertEqual(len(owner.attention.scheduled), 1)

    async def test_production_passive_hold_rejects_owner_steering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([])
            owner, queue, _context = self._owner(inbox, engine)
            owner.turn_coordinator = TurnCoordinator()
            original = engine.record_passive_qq_messages
            recorded = []

            def record(payloads):
                recorded.append(owner.turn_coordinator.offer_steer(
                    profile_user_id="profile", session_id="session", actor_id="qq:owner",
                    content="现在回复我", allow_other_actor=True,
                ))
                return original(payloads)

            engine.record_passive_qq_messages = record
            self._enqueue(inbox, event_id="one", turn_payload={"message": "hi", "source_message_id": "one"})
            await asyncio.wait_for(queue._drain("profile\0session"), 2)
            self.assertEqual(recorded[0]["status"], "busy_maintenance")


class PassiveRetrySchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_chat_wakes_waiting_retry_and_retry_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [100.0]
            path = Path(temp_dir) / "inbox.db"
            store = SessionInboxStore(path, clock=lambda: now[0])
            deferred, replied = asyncio.Event(), asyncio.Event()
            observed = []
            original = store.claim_next

            loop = asyncio.get_running_loop()

            def observed_claim(*args, **kwargs):
                result = original(*args, **kwargs)
                if result.get("status") == "deferred":
                    loop.call_soon_threadsafe(deferred.set)
                return result

            store.claim_next = observed_claim
            queue = DurableSessionWorkQueue(store)

            async def handler(_key, items):
                observed.append([item.source_event_id for item in items])
                if items[0].kind == "turn":
                    replied.set()

            queue.register_handler("qq", handler, can_yield_retry=passive_retry_can_yield)
            fields = dict(session_key="profile\0session", profile_user_id="profile", session_id="session", source="qq")
            first = await queue.enqueue(**fields, kind="passive", source_event_id="retry", payload={}, schedule=False)
            initial = original("profile\0session", worker_id="earlier")
            store.settle_claims(initial["items"], status="queued", error="passive_memory_record_failed", retry_delay_seconds=10)
            await queue.schedule_session("profile\0session")
            await asyncio.wait_for(deferred.wait(), 1)
            await asyncio.sleep(0)  # Let the worker enter the deferred wait.
            chat = await queue.enqueue(**fields, kind="turn", source_event_id="chat", payload={})
            await asyncio.wait_for(replied.wait(), 1)
            await queue.close(timeout=2)
            self.assertEqual(observed, [["chat"]])
            self.assertEqual(store.get(chat["item_id"]).status, "committed")
            self.assertEqual(store.get(first["item_id"]).status, "queued")
            self.assertEqual(store.get(first["item_id"]).attempts, 1)

            now[0] = 111
            reopened = SessionInboxStore(path, clock=lambda: now[0])
            resumed = DurableSessionWorkQueue(reopened)
            resumed.register_handler("qq", handler, can_yield_retry=passive_retry_can_yield)
            await resumed.recover()
            async with asyncio.timeout(2):
                while resumed.has_work("profile\0session"):
                    await asyncio.sleep(0.01)
            await resumed.close(timeout=2)
            self.assertEqual(observed, [["chat"], ["retry"]])
            self.assertEqual(reopened.get(first["item_id"]).status, "committed")

    async def test_owner_message_during_real_maintenance_is_requeued_and_delivered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = SessionInboxStore(Path(temp_dir) / "inbox.db")
            queue = DurableSessionWorkQueue(inbox)
            coordinator = TurnCoordinator()
            delivered = []
            helper = fixtures.QQPassiveMemoryQueueTests()
            owner, _unused, context = helper._owner(inbox, _PassiveEngine([]))
            owner.queue = queue
            context.is_group = True
            context.user_id = 12345
            context.clean_message = "在吗"

            async def handle(_key, items):
                async with coordinator.hold("profile", "session", actor_id="qq:12345", channel="qq"):
                    delivered.extend(item.source_event_id for item in items)

            queue.register_handler("qq", handle)
            runner = QQTurnRunner(
                engine=object(), config_module=object(), qq_gateway=SimpleNamespace(master_qq="12345"),
                log_event=Mock(), turn_coordinator=coordinator, tts_client=None, settings=None,
                plugin_conversation_ref_issuer=None, plugin_inputs=SimpleNamespace(defer_payload=lambda _inbound, p: p),
                plugin_turn_router=None, attention=None, session_queue=owner, stream_processor=None,
            )
            async with coordinator.hold("profile", "session", turn_kind="private_memory_import", accepts_steering=False) as token:
                result = await runner._run_admitted(context=context, event={"time": 100}, turn_payload={"message": "在吗"})
                self.assertEqual(result["send_result"]["status"], "queued")
                self.assertEqual(coordinator.drain(token)["steers"], [])
            async with asyncio.timeout(2):
                while queue.has_work("profile\0session"):
                    await asyncio.sleep(0.01)
            await queue.close(timeout=2)
            self.assertEqual(delivered, ["1001"])
            self.assertEqual(inbox.pending_count("profile\0session"), 0)


class PassiveRetryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 100.0
        self.store = SessionInboxStore(Path(self.temp.name) / "inbox.db", clock=lambda: self.now)

    def enqueue(self, event, *, kind="passive", payload=None, source="qq", record_only=False, **kwargs):
        return self.store.enqueue(
            session_key="profile\0session", profile_user_id="profile", session_id="session", kind=kind,
            source=source, source_event_id=event,
            payload={"turn_payload": payload or {"character_pack_id": "cecilia"}, "record_only": record_only}, **kwargs,
        )

    def claim(self, **kwargs):
        return self.store.claim_next(
            "profile\0session", worker_id="test", batch_key=passive_batch_key, can_yield_retry=passive_retry_can_yield, **kwargs,
        )

    def test_waiting_batch_yields_as_a_whole_and_keeps_retry_membership(self):
        self.enqueue("a")
        self.enqueue("b")
        first = self.claim()["items"]
        self.store.settle_claims(first, status="queued", error="passive_memory_record_failed", retry_delay_seconds=10)
        self.enqueue("chat", kind="turn")
        next_claim = self.claim()
        self.assertEqual([item.source_event_id for item in next_claim["items"]], ["chat"])
        self.store.settle_claims(next_claim["items"], status="committed")
        waiting = self.claim()
        self.assertEqual(waiting["status"], "deferred")
        self.assertEqual(waiting["retry_after"], 10)
        self.now = 111
        retried = self.claim(max_batch_items=1)["items"]
        self.assertEqual([item.item_id for item in retried], [item.item_id for item in first])
        self.assertEqual(retried[0].batch_id, first[0].batch_id)
        self.store.settle_claims(retried, status="committed")
        self.assertEqual(self.store.pending_count("profile\0session"), 0)

    def test_waiting_passive_retry_does_not_allow_crossing_a_deferred_real_input(self):
        self.enqueue("retry")
        first = self.claim()["items"]
        self.store.settle_claims(first, status="queued", error="passive_memory_record_failed", retry_delay_seconds=5)
        self.enqueue("delayed-chat", kind="turn", available_at=110)
        self.enqueue("later-chat", kind="turn")
        waiting = self.claim()
        self.assertEqual(waiting["status"], "deferred")
        self.assertEqual(waiting["retry_after"], 5)
        self.now = 105
        retry = self.claim()["items"]
        self.assertEqual(retry[0].source_event_id, "retry")
        self.store.settle_claims(retry, status="committed")
        self.assertEqual(self.claim()["status"], "deferred")
        self.now = 110
        self.assertEqual(self.claim()["item"].source_event_id, "delayed-chat")

    def test_unfailed_passive_delay_still_preserves_fifo(self):
        self.enqueue("deferred-observation", available_at=110)
        self.enqueue("chat", kind="turn")
        self.assertEqual(self.claim()["status"], "deferred")
        self.now = 110
        self.assertEqual(self.claim()["item"].source_event_id, "deferred-observation")

    def test_passive_batches_stop_at_character_mode_and_record_only_boundaries(self):
        variants = [
            ({"character_pack_id": "other"}, False),
            ({"character_pack_id": "cecilia", "client_mode": "other"}, False),
            ({"character_pack_id": "cecilia"}, True),
        ]
        for index, (payload, record_only) in enumerate(variants):
            with self.subTest(index=index):
                store = SessionInboxStore(Path(self.temp.name) / f"scope-{index}.db", clock=lambda: self.now)
                original = self.store
                try:
                    self.store = store
                    self.enqueue("first")
                    self.enqueue("different", payload=payload, record_only=record_only)
                    self.enqueue("tail")
                    self.assertEqual([item.source_event_id for item in self.claim()["items"]], ["first"])
                finally:
                    self.store = original


if __name__ == "__main__":
    unittest.main()
