from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
            self.assertEqual(store.claim_next("profile\0session", worker_id="worker-b")["status"], "idle")

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


if __name__ == "__main__":
    unittest.main()
