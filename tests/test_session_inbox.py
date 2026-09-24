from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from companion_v01.durable_session_queue import DurableSessionWorkQueue, RetryableSessionWorkError
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.store import MemoryStore


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class SessionInboxStoreTests(unittest.TestCase):
    def _enqueue(
        self,
        store: SessionInboxStore,
        *,
        session_key: str = "profile\0session",
        source_event_id: str = "event-1",
        payload: dict | None = None,
    ) -> dict:
        return store.enqueue(
            session_key=session_key,
            profile_user_id="profile",
            session_id=session_key.split("\0")[-1],
            kind="turn",
            payload=payload or {"message": source_event_id},
            source="qq",
            source_event_id=source_event_id,
        )

    def test_item_survives_store_reopen_and_payload_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "akane_memory_v01.db"
            first = SessionInboxStore(database_path)
            queued = self._enqueue(first, payload={"message": "继续任务", "metadata": {"actor": "qq:1"}})

            reopened = SessionInboxStore(database_path)
            stored = reopened.get(queued["item_id"])

            self.assertIsNotNone(stored)
            self.assertEqual(stored.status, "queued")
            self.assertEqual(stored.payload, {"message": "继续任务", "metadata": {"actor": "qq:1"}})

    def test_inbox_uses_the_existing_instance_database_without_disturbing_memcore_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = MemoryStore(Path(temp_dir))
            inbox = SessionInboxStore(memory_store.db_path)
            queued = self._enqueue(inbox)

            session = memory_store.ensure_session(
                profile_user_id="profile",
                session_id="session",
                character_pack_id="reimu",
            )

            self.assertEqual(session["session_id"], "session")
            self.assertEqual(inbox.get(queued["item_id"]).status, "queued")

    def test_source_event_id_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            first = self._enqueue(store, source_event_id="qq-message-42")
            duplicate = self._enqueue(store, source_event_id="qq-message-42")

            self.assertEqual(first["status"], "queued")
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertEqual(duplicate["item_id"], first["item_id"])
            self.assertEqual(store.pending_count("profile\0session"), 1)

    def test_source_event_id_collision_does_not_hide_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            first = self._enqueue(store, source_event_id="qq-message-42")
            collision = self._enqueue(
                store,
                source_event_id="qq-message-42",
                payload={"message": "different content"},
            )

            self.assertEqual(collision["status"], "collision")
            self.assertEqual(collision["item_id"], first["item_id"])
            self.assertEqual(store.pending_count("profile\0session"), 1)

    def test_one_session_has_only_one_live_claim_and_preserves_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            first = self._enqueue(store, source_event_id="event-1")
            second = self._enqueue(store, source_event_id="event-2")

            first_claim = store.claim_next("profile\0session", worker_id="worker-a")
            blocked = store.claim_next("profile\0session", worker_id="worker-b")
            self.assertEqual(first_claim["item"].item_id, first["item_id"])
            self.assertEqual(blocked["status"], "busy")

            self.assertTrue(store.commit(first["item_id"], claim_token=first_claim["claim_token"])["ok"])
            second_claim = store.claim_next("profile\0session", worker_id="worker-b")
            self.assertEqual(second_claim["item"].item_id, second["item_id"])

    def test_expected_item_claim_cannot_jump_over_an_earlier_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            first = self._enqueue(store, source_event_id="event-1")
            second = self._enqueue(store, source_event_id="event-2")

            blocked = store.claim_next(
                "profile\0session",
                worker_id="active-turn",
                expected_item_id=second["item_id"],
            )

            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["next_item_id"], first["item_id"])
            self.assertEqual(store.get(first["item_id"]).status, "queued")
            self.assertEqual(store.get(second["item_id"]).status, "queued")

    def test_different_sessions_can_be_claimed_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            self._enqueue(store, session_key="profile\0session-a", source_event_id="event-a")
            self._enqueue(store, session_key="profile\0session-b", source_event_id="event-b")

            first = store.claim_next("profile\0session-a", worker_id="worker-a")
            second = store.claim_next("profile\0session-b", worker_id="worker-b")

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertNotEqual(first["claim_token"], second["claim_token"])

    def test_expired_claim_is_recovered_and_stale_worker_cannot_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = _Clock()
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db", clock=clock)
            queued = self._enqueue(store)
            first = store.claim_next("profile\0session", worker_id="worker-a", lease_seconds=10)

            clock.value = 111.0
            recovered = store.claim_next("profile\0session", worker_id="worker-b", lease_seconds=10)

            self.assertEqual(recovered["item"].item_id, queued["item_id"])
            self.assertEqual(recovered["item"].attempts, 2)
            stale = store.commit(queued["item_id"], claim_token=first["claim_token"])
            self.assertEqual(stale["status"], "stale")
            self.assertTrue(store.commit(queued["item_id"], claim_token=recovered["claim_token"])["ok"])

    def test_retryable_failure_requeues_without_losing_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = _Clock()
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db", clock=clock)
            queued = self._enqueue(store)
            claimed = store.claim_next("profile\0session", worker_id="worker-a")

            failed = store.fail(
                queued["item_id"],
                claim_token=claimed["claim_token"],
                error="temporary gateway failure",
                retryable=True,
                retry_delay_seconds=5,
            )
            self.assertEqual(failed["status"], "retryable")
            self.assertEqual(store.claim_next("profile\0session", worker_id="worker-b")["status"], "deferred")

            clock.value = 105.0
            retried = store.claim_next("profile\0session", worker_id="worker-b")
            self.assertTrue(retried["ok"])
            self.assertEqual(retried["item"].attempts, 2)
            self.assertEqual(retried["item"].last_error, "temporary gateway failure")

    def test_pending_session_keys_recovers_expired_claims_in_arrival_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = _Clock()
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db", clock=clock)
            self._enqueue(store, session_key="profile\0first", source_event_id="first")
            self._enqueue(store, session_key="profile\0second", source_event_id="second")
            store.claim_next("profile\0first", worker_id="dead-worker", lease_seconds=10)

            self.assertEqual(store.pending_session_keys(), ["profile\0second"])
            clock.value = 111.0
            self.assertEqual(store.pending_session_keys(), ["profile\0first", "profile\0second"])

    def test_host_startup_can_release_a_previous_process_claim_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            self._enqueue(store)
            store.claim_next("profile\0session", worker_id="previous-process", lease_seconds=300)

            self.assertEqual(store.pending_session_keys(), [])
            self.assertEqual(store.recover_abandoned_claims(), 1)
            self.assertEqual(store.pending_session_keys(), ["profile\0session"])

    def test_invalid_payload_is_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "akane_memory_v01.db")
            result = self._enqueue(store, payload={"not_json": object()})

            self.assertEqual(result, {
                "ok": False,
                "status": "invalid",
                "reason": "session_inbox_payload_not_json_safe",
            })
            self.assertEqual(store.pending_count("profile\0session"), 0)


class DurableSessionWorkQueueTests(unittest.TestCase):
    @staticmethod
    def _fields(*, session_key: str, event_id: str) -> dict:
        return {
            "session_key": session_key,
            "profile_user_id": "profile",
            "session_id": session_key.split("\0")[-1],
            "kind": "turn",
            "payload": {"message": event_id},
            "source": "qq",
            "source_event_id": event_id,
        }

    def test_enqueue_persists_before_handler_runs_and_then_commits(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            started = asyncio.Event()
            release = asyncio.Event()

            async def handler(_key, _items) -> None:
                started.set()
                await release.wait()

            queue = DurableSessionWorkQueue(store, handler)
            queued = await queue.enqueue(**self._fields(session_key="profile\0session", event_id="event-1"))
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(store.get(queued["item_id"]).status, "claimed")
            release.set()
            while queue.has_work("profile\0session"):
                await asyncio.sleep(0.01)
            self.assertEqual(store.get(queued["item_id"]).status, "committed")

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_unscheduled_item_can_be_claimed_as_active_turn_steer(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            handled: list[str] = []

            async def handler(_key, items) -> None:
                handled.append(items[0].item_id)

            queue = DurableSessionWorkQueue(store, handler)
            queued = await queue.enqueue(
                **self._fields(session_key="profile\0session", event_id="steer-1"),
                schedule=False,
            )
            self.assertEqual(handled, [])
            self.assertEqual(store.get(queued["item_id"]).status, "queued")

            claim = await queue.claim_for_active_turn("profile\0session", queued["item_id"])
            self.assertTrue(claim["ok"])
            self.assertEqual(claim["item"].item_id, queued["item_id"])
            self.assertEqual(handled, [])

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_active_turn_can_claim_next_steer_while_runner_owns_current_item(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            queue = DurableSessionWorkQueue(store, lambda _key, _items: asyncio.sleep(0))
            first = await queue.enqueue(
                **self._fields(session_key="profile\0session", event_id="first"),
                schedule=False,
            )
            second = await queue.enqueue(
                **self._fields(session_key="profile\0session", event_id="steer"),
                schedule=False,
            )
            running = store.claim_next("profile\0session", worker_id="runner")
            self.assertTrue(running["ok"])
            self.assertEqual(running["item"].item_id, first["item_id"])

            steer = await queue.claim_for_active_turn("profile\0session", second["item_id"])

            self.assertTrue(steer["ok"])
            self.assertEqual(steer["item"].item_id, second["item_id"])

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_one_runner_dispatches_mixed_sources_in_session_order(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            handled: list[tuple[str, str]] = []
            queue = DurableSessionWorkQueue(store)

            async def handle_qq(_key, items) -> None:
                handled.append(("qq", items[0].source_event_id))

            async def handle_desktop(_key, items) -> None:
                handled.append(("desktop_pet", items[0].source_event_id))

            queue.register_handler("qq", handle_qq)
            queue.register_handler("desktop_pet", handle_desktop)
            qq_fields = self._fields(session_key="profile\0session", event_id="first")
            desktop_fields = self._fields(session_key="profile\0session", event_id="second")
            desktop_fields["source"] = "desktop_pet"
            await queue.enqueue(**qq_fields, schedule=False)
            await queue.enqueue(**desktop_fields, schedule=False)

            await queue.schedule_session("profile\0session")
            while queue.has_work("profile\0session"):
                await asyncio.sleep(0.01)

            self.assertEqual(handled, [("qq", "first"), ("desktop_pet", "second")])

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_retryable_handler_failure_requeues_and_resumes(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            attempts = 0
            completed = asyncio.Event()

            async def handler(_key, _items) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RetryableSessionWorkError("channel_offline", retry_delay_seconds=0.01)
                completed.set()

            queue = DurableSessionWorkQueue(store, handler)
            queued = await queue.enqueue(
                **self._fields(session_key="profile\0session", event_id="retryable"),
            )

            await asyncio.wait_for(completed.wait(), timeout=1)
            while queue.has_work("profile\0session"):
                await asyncio.sleep(0.01)

            self.assertEqual(attempts, 2)
            self.assertEqual(store.get(queued["item_id"]).status, "committed")

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_recover_drains_items_created_before_runner_start(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            queued = store.enqueue(**self._fields(session_key="profile\0session", event_id="event-1"))
            handled = asyncio.Event()

            async def handler(_key, items) -> None:
                self.assertEqual(items[0].item_id, queued["item_id"])
                handled.set()

            queue = DurableSessionWorkQueue(SessionInboxStore(database_path), handler)
            self.assertEqual(await queue.recover(), 1)
            await asyncio.wait_for(handled.wait(), timeout=1)
            while queue.has_work("profile\0session"):
                await asyncio.sleep(0.01)
            self.assertEqual(store.get(queued["item_id"]).status, "committed")

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_recover_reclaims_an_unexpired_previous_process_lease(self) -> None:
        async def exercise(database_path: Path) -> None:
            original = SessionInboxStore(database_path)
            queued = original.enqueue(**self._fields(session_key="profile\0session", event_id="event-1"))
            original.claim_next("profile\0session", worker_id="dead-process", lease_seconds=300)
            handled = asyncio.Event()

            async def handler(_key, _items) -> None:
                handled.set()

            queue = DurableSessionWorkQueue(SessionInboxStore(database_path), handler)
            self.assertEqual(await queue.recover(), 1)
            await asyncio.wait_for(handled.wait(), timeout=1)
            while queue.has_work("profile\0session"):
                await asyncio.sleep(0.01)
            self.assertEqual(original.get(queued["item_id"]).status, "committed")

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))

    def test_handler_failure_is_recorded_and_does_not_block_next_item(self) -> None:
        async def exercise(database_path: Path) -> None:
            store = SessionInboxStore(database_path)
            errors: list[str] = []
            handled_second = asyncio.Event()

            async def handler(_key, items) -> None:
                if items[0].payload["message"] == "event-1":
                    raise RuntimeError("boom")
                handled_second.set()

            queue = DurableSessionWorkQueue(
                store,
                handler,
                on_error=lambda _key, _items, exc: errors.append(exc.__class__.__name__),
            )
            first = await queue.enqueue(**self._fields(session_key="profile\0session", event_id="event-1"))
            second = await queue.enqueue(**self._fields(session_key="profile\0session", event_id="event-2"))
            await asyncio.wait_for(handled_second.wait(), timeout=1)
            while queue.has_work("profile\0session"):
                await asyncio.sleep(0.01)

            self.assertEqual(store.get(first["item_id"]).status, "failed")
            self.assertEqual(store.get(second["item_id"]).status, "committed")
            self.assertEqual(errors, ["RuntimeError"])

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "akane_memory_v01.db"))


if __name__ == "__main__":
    unittest.main()
