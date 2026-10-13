from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.qq_session_queue import QQSessionQueue
from companion_v01.session_inbox import SessionInboxStore


class _Coordinator:
    @asynccontextmanager
    async def hold(self, *_args, **_kwargs):
        yield "turn-control"


class _Completion:
    def __init__(self):
        self.payload = {}
        self.prepared_frame = None
        self.direct_events = []
        self.jobs = []

    def processing(self, *_args, **_kwargs):
        return nullcontext()

    def allows_file_target(self, *_args, **_kwargs):
        return False


class QQQueueProcessingFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_qq_turn_is_fenced_before_model_handler_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")
            queued = store.enqueue(
                session_key="profile\0session",
                profile_user_id="profile",
                session_id="session",
                kind="message",
                payload={"event": {}, "turn_payload": {}},
                source="qq",
                source_event_id="finance-news:test",
            )
            claim = store.claim_next("profile\0session", worker_id="worker")
            queue = DurableSessionWorkQueue(store)
            completion = _Completion()
            context = SimpleNamespace(
                profile_user_id="profile",
                session_id="session",
                group_id=123,
                user_id=0,
                character_pack_id="",
                source_message_id="finance-news:test",
                chat_model_override="",
            )

            async def run_unlocked(**_kwargs):
                self.assertGreater(store.get(queued["item_id"]).processing_started_at, 0)
                return {
                    "send_result": {"ok": True, "status": "sent"},
                    "frame": {"status": "completed"},
                }

            owner = QQSessionQueue(
                engine=object(),
                config_module=object(),
                qq_gateway=object(),
                runtime_metrics=SimpleNamespace(observe_request=lambda *_args, **_kwargs: None),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
                turn_coordinator=_Coordinator(),
                plugin_inputs=SimpleNamespace(settle=lambda *_args, **_kwargs: None),
                plugin_turn_router=None,
                attention=object(),
                run_unlocked=run_unlocked,
                queue=queue,
                schedule_task=asyncio.create_task,
            )
            owner.restore_context = lambda *_args, **_kwargs: context

            with patch("companion_v01.qq_session_queue.prepare_completion_turn", return_value=completion), \
                 patch("companion_v01.qq_session_queue.record_completion_delivery"):
                await owner._handle_admitted("profile\0session", [claim["item"]])

            self.assertGreater(store.get(queued["item_id"]).processing_started_at, 0)
            self.assertTrue(store.settle_claims(claim["items"], status="committed")["ok"])


if __name__ == "__main__":
    unittest.main()
