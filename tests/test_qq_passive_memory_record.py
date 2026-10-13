from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.channels.qq.inbound.record_ids import _passive_record_source_id, _passive_replay_identity
from companion_v01.channels.qq.turns.queue import QQSessionQueue
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.store import MemoryStore


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, _text: str) -> list[float]:
        return [0.0] * self.dimension


class _MemcoreManager:
    """Record one MemCore standalone append per call and return scripted receipts."""

    def __init__(self, *results, enabled=True, available=False):
        self.enabled = bool(enabled)
        self.available = bool(available)
        self.results = [dict(item) for item in results]
        self.calls: list[dict] = []

    def append_standalone_message(self, record, **kwargs):
        self.calls.append({"record": dict(record), "kwargs": dict(kwargs)})
        return dict(self.results.pop(0)) if self.results else {"ok": True, "status": "recorded"}


def _engine(temp_dir: str, manager, *, compaction_calls=None):
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.store = MemoryStore(Path(temp_dir))
    engine.memcore_manager = manager
    compaction_calls = [] if compaction_calls is None else compaction_calls
    engine._schedule_memcore_compaction = lambda **kwargs: (
        compaction_calls.append(dict(kwargs)) or {"ok": True, "status": "scheduled"}
    )
    return engine


def _passive_payload(**overrides):
    payload = {
        "user_id": "qq-group-1",
        "real_user_id": "qq-group-1",
        "character_pack_id": "cecilia",
        "message": "【群友】今晚一起打游戏吗",
        "memory_message": "【群友】今晚一起打游戏吗",
        "source_message_id": "1001",
        "client_mode": "qq_text",
        "timestamp": 1_777_000_000,
    }
    payload.update(overrides)
    return payload


class QQPassiveMemoryReceiptTests(unittest.TestCase):
    def test_memcore_write_failure_is_not_reported_as_a_successful_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager({"ok": False, "status": "failed", "reason": "database is locked"})
            engine = _engine(temp_dir, manager)

            result = engine.record_passive_qq_message(_passive_payload())

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["status"], "record_failed")
            self.assertEqual(result["reason"], "database is locked")
            self.assertFalse(result["memcore"]["ok"])
            self.assertEqual(len(manager.calls), 1)

    def test_rejected_memcore_write_reports_a_reason_instead_of_a_bare_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager({"ok": False, "status": "conflict"})
            engine = _engine(temp_dir, manager)

            result = engine.record_passive_qq_message(_passive_payload())

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["reason"], "memcore_write_rejected")

    def test_successful_memcore_write_keeps_the_recorded_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager({"ok": True, "status": "recorded", "source_id": "memcore-1"})
            engine = _engine(temp_dir, manager)

            result = engine.record_passive_qq_message(_passive_payload())

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "recorded")
            self.assertEqual(result["reason"], "")
            self.assertEqual(result["source_id"], "qq-passive:qq-group-1:cecilia:1001")

    def test_message_without_memcore_authority_stays_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager({"ok": False, "status": "failed"}, enabled=False)
            engine = _engine(temp_dir, manager)

            result = engine.record_passive_qq_message(_passive_payload())

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["memcore"], {})
            self.assertEqual(manager.calls, [])

    def test_batch_reports_the_failed_message_and_keeps_one_maintenance_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager(
                {"ok": True, "status": "recorded"},
                {"ok": False, "status": "failed", "reason": "timeline_entry_source_id_conflict"},
            )
            compaction_calls: list[dict] = []
            manager.available = True
            engine = _engine(temp_dir, manager, compaction_calls=compaction_calls)

            result = engine.record_passive_qq_messages(
                [
                    _passive_payload(source_message_id="2001", message="第一句", memory_message="第一句"),
                    _passive_payload(source_message_id="2002", message="第二句", memory_message="第二句"),
                ]
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["status"], "partially_recorded")
            self.assertEqual((result["recorded_count"], result["failed_count"]), (1, 1))
            self.assertFalse(result["results"][1]["ok"])
            self.assertEqual(result["results"][1]["reason"], "timeline_entry_source_id_conflict")
            self.assertEqual(len(compaction_calls), 1)
            self.assertEqual(result["maintenance"]["status"], "scheduled")

    def test_batch_does_not_claim_maintenance_when_memcore_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager({"ok": True, "status": "recorded"}, available=False)
            compaction_calls: list[dict] = []
            engine = _engine(temp_dir, manager, compaction_calls=compaction_calls)
            result = engine.record_passive_qq_messages([_passive_payload()])
            self.assertTrue(result["ok"])
            self.assertEqual(result["recorded_count"], 1)
            self.assertEqual(result["maintenance"], {})
            self.assertEqual(compaction_calls, [])

    def test_plain_group_message_keeps_one_replay_stable_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = _MemcoreManager(enabled=True, available=True)
            engine = _engine(temp_dir, manager)

            first = engine.record_passive_qq_message(_passive_payload())
            replay = engine.record_passive_qq_message(_passive_payload())

            self.assertEqual(first["source_id"], replay["source_id"])
            with engine.store._connect() as connection:
                stored = connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE source_id = ?",
                    (first["source_id"],),
                ).fetchone()[0]
            self.assertEqual(stored, 1)
            self.assertEqual(len(manager.calls), 2)

    def test_replay_identity_covers_text_media_and_host_record_ids(self) -> None:
        plain = _passive_payload()
        self.assertEqual(_passive_replay_identity(plain), "1001")
        self.assertEqual(
            _passive_record_source_id(plain, profile_user_id="qq-group-1", character_pack_id="cecilia"),
            "qq-passive:qq-group-1:cecilia:1001",
        )
        self.assertEqual(_passive_replay_identity(_passive_payload(source_message_id="")), "")
        self.assertEqual(
            _passive_replay_identity(
                _passive_payload(
                    message="【群友】发来了一张图片\nqq.attachments:\n  - handle: \"img_001\"",
                )
            ),
            "1001",
        )
        self.assertEqual(
            _passive_replay_identity(_passive_payload(forward_references=[{"forward_id": "forward-1"}])),
            "1001",
        )
        frozen = _passive_payload(passive_memory_source_id="qq-passive-inbox:host-id")
        self.assertEqual(_passive_replay_identity(frozen), "qq-passive-inbox:host-id")
        self.assertEqual(
            _passive_record_source_id(frozen, profile_user_id="qq-group-1", character_pack_id="cecilia"),
            "qq-passive-inbox:host-id",
        )

    def test_replay_of_one_group_message_stores_a_single_memcore_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(
                backend="memcore",
                storage_path=Path(temp_dir) / "memcore_v01.db",
                visible_scope="conversation",
                enable_flavor=True,
                shadow_compare=False,
                llm=_FakeLLM(),
                embedding_provider=_FakeEmbeddingProvider(),
            )
            try:
                chat_dir = Path(temp_dir) / "chat"
                chat_dir.mkdir()
                engine = _engine(str(chat_dir), manager)
                payload = _passive_payload()

                first = engine.record_passive_qq_message(payload)
                replay = engine.record_passive_qq_message(payload)

                self.assertTrue(first["ok"], first)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(first["source_id"], replay["source_id"])
                self.assertIsNotNone(manager._store.get_record_by_source_id(first["source_id"]))
            finally:
                manager.close()


class _Coordinator:
    @asynccontextmanager
    async def hold(self, *_args, **_kwargs):
        yield "turn-control"


class _Attention:
    def __init__(self):
        self.staged: list = []
        self.scheduled: list = []

    def stage_media(self, *args, **kwargs):
        self.staged.append((args, kwargs))

    def schedule(self, _context, _event, **kwargs):
        self.scheduled.append(dict(kwargs))
        return {"scheduled": True, "reason": "stage_countdown"}

    @staticmethod
    def actor_key(_context):
        return "qq:10001"


class _PassiveEngine:
    def __init__(self, results):
        self.results = [dict(item) for item in results]
        self.batches: list[list[dict]] = []

    def record_passive_qq_messages(self, payloads):
        self.batches.append([dict(item) for item in payloads])
        if self.results:
            return dict(self.results.pop(0))
        return {
            "ok": True,
            "status": "recorded",
            "count": len(payloads),
            "recorded_count": len(payloads),
            "failed_count": 0,
            "results": [{"ok": True, "source_id": "recorded"} for _ in payloads],
        }


def _failure(count: int = 1, *, status: str = "partially_recorded") -> dict:
    return {
        "ok": False,
        "status": status,
        "count": count,
        "recorded_count": 0,
        "failed_count": count,
        "results": [{"ok": False, "status": "record_failed"} for _ in range(count)],
    }


class QQPassiveMemoryQueueTests(unittest.IsolatedAsyncioTestCase):
    def _owner(self, store, engine):
        queue = DurableSessionWorkQueue(store)
        owner = QQSessionQueue(
            engine=engine,
            config_module=object(),
            qq_gateway=object(),
            runtime_metrics=SimpleNamespace(observe_request=lambda *_args, **_kwargs: None),
            logger=SimpleNamespace(
                exception=lambda *_args, **_kwargs: None,
                error=lambda *_args, **_kwargs: None,
            ),
            log_event=lambda *_args, **_kwargs: None,
            turn_coordinator=_Coordinator(),
            plugin_inputs=SimpleNamespace(settle=lambda *_args, **_kwargs: None),
            plugin_turn_router=None,
            attention=_Attention(),
            run_unlocked=None,
            queue=queue,
            schedule_task=asyncio.create_task,
        )
        context = SimpleNamespace(
            profile_user_id="profile",
            session_id="session",
            group_id=123,
            user_id=0,
            character_pack_id="cecilia",
            source_message_id="1001",
            chat_model_override="",
        )
        owner.restore_context = lambda *_args, **_kwargs: context

        async def prepare(*, context, event, base_payload):
            return dict(base_payload), {}, []

        owner._prepare_passive_payload = prepare
        return owner, queue, context

    def _enqueue(self, store, *, turn_payload, event_id):
        return store.enqueue(
            session_key="profile\0session",
            profile_user_id="profile",
            session_id="session",
            kind="passive",
            payload={"event": {"time": 100}, "turn_payload": dict(turn_payload), "record_only": False},
            source="qq",
            source_event_id=event_id,
        )

    async def test_failed_passive_batch_is_retried_instead_of_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([_failure()])
            owner, queue, _context = self._owner(store, engine)
            queued = self._enqueue(
                store,
                turn_payload={"message": "【群友】你好", "source_message_id": "1001"},
                event_id="1001",
            )

            with patch("companion_v01.channels.qq.turns.queue._PASSIVE_RECORD_RETRY_DELAY_SECONDS", 0.01):
                await asyncio.wait_for(queue._drain("profile\0session"), 5)

            item = store.get(queued["item_id"])
            self.assertEqual(item.status, "committed", item.last_error)
            self.assertEqual(item.attempts, 2)
            self.assertEqual(len(engine.batches), 2)
            self.assertEqual(len(owner.attention.scheduled), 1)

    async def test_exhausted_passive_record_failure_never_settles_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([_failure(), _failure(), _failure()])
            owner, queue, _context = self._owner(store, engine)
            queued = self._enqueue(
                store,
                turn_payload={"message": "【群友】你好", "source_message_id": "1001"},
                event_id="1001",
            )

            with patch("companion_v01.channels.qq.turns.queue._PASSIVE_RECORD_MAX_ATTEMPTS", 2), patch(
                "companion_v01.channels.qq.turns.queue._PASSIVE_RECORD_RETRY_DELAY_SECONDS", 0.01
            ):
                await asyncio.wait_for(queue._drain("profile\0session"), 5)

            item = store.get(queued["item_id"])
            self.assertEqual(item.status, "failed")
            self.assertEqual(item.last_error, "passive_memory_record_failed")
            self.assertEqual(item.attempts, 2)
            self.assertEqual(len(engine.batches), 2)
            self.assertEqual(owner.attention.scheduled, [])

    async def test_enriched_passive_payload_can_retry_with_a_stable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([_failure()])
            owner, queue, _context = self._owner(store, engine)
            queued = self._enqueue(
                store,
                turn_payload={
                    "message": "【群友】发来了一张图片\nqq.attachments:\n  - handle: \"img_001\"",
                    "source_message_id": "1002",
                },
                event_id="1002",
            )

            with patch("companion_v01.channels.qq.turns.queue._PASSIVE_RECORD_RETRY_DELAY_SECONDS", 0.01):
                await asyncio.wait_for(queue._drain("profile\0session"), 5)

            item = store.get(queued["item_id"])
            self.assertEqual(item.status, "committed")
            self.assertEqual(item.attempts, 2)
            self.assertEqual(len(engine.batches), 2)
            self.assertEqual(engine.batches[0], engine.batches[1])
            self.assertEqual(len(owner.attention.scheduled), 1)

    async def test_invalid_passive_batch_fails_without_replaying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([_failure(status="mixed_batch_scope")])
            owner, queue, _context = self._owner(store, engine)
            queued = self._enqueue(
                store,
                turn_payload={"message": "【群友】你好", "source_message_id": "1001"},
                event_id="1001",
            )

            await asyncio.wait_for(queue._drain("profile\0session"), 5)

            item = store.get(queued["item_id"])
            self.assertEqual(item.status, "failed")
            self.assertEqual(item.last_error, "passive_memory_batch_invalid")
            self.assertEqual(item.attempts, 1)
            self.assertEqual(len(engine.batches), 1)

    async def test_recorded_passive_batch_still_commits_and_keeps_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")
            engine = _PassiveEngine([])
            owner, queue, _context = self._owner(store, engine)
            queued = self._enqueue(
                store,
                turn_payload={"message": "【群友】你好", "source_message_id": "1001"},
                event_id="1001",
            )

            await asyncio.wait_for(queue._drain("profile\0session"), 5)

            item = store.get(queued["item_id"])
            self.assertEqual(item.status, "committed")
            self.assertEqual(item.attempts, 1)
            self.assertEqual(len(engine.batches), 1)
            self.assertEqual(len(owner.attention.scheduled), 1)


if __name__ == "__main__":
    unittest.main()
