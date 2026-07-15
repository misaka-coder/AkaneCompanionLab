from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.async_task_supervisor import AsyncTaskSupervisor
from companion_v01.vector_store import VectorStore


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self, **_kwargs):
        self.closed = True
        return True


class InstanceWriterShutdownTests(unittest.TestCase):
    def test_engine_close_stops_reindex_before_vector_store(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._close_lock = threading.RLock()
        engine._closed = False
        engine._close_status = None
        engine._embedding_reindex_stop = threading.Event()
        engine.background_tasks = _Closable()
        engine.desktop_screen_vision = _Closable()
        engine.vision_service = _Closable()
        engine.compaction_service = _Closable()
        engine.memcore_manager = _Closable()
        engine.vector_store = _Closable()
        thread = threading.Thread(target=engine._embedding_reindex_stop.wait, name="test-reindex")
        engine._embedding_reindex_thread = thread
        thread.start()

        result = engine.close()

        self.assertEqual(result["status"], "stopped")
        self.assertFalse(thread.is_alive())
        self.assertTrue(engine.vector_store.closed)
        self.assertEqual(engine.close(), result)

    def test_vector_store_close_releases_persistent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chroma_dir = Path(temp_dir) / "chroma"
            store = VectorStore(chroma_dir)
            store.count_entries()
            store.close()
            store.close()
            shutil.rmtree(chroma_dir)
            self.assertFalse(chroma_dir.exists())


class AsyncTaskSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_cancels_and_joins_followup_tasks(self) -> None:
        supervisor = AsyncTaskSupervisor(name="test-followups")
        started = asyncio.Event()

        async def followup() -> None:
            started.set()
            await asyncio.Event().wait()

        task = supervisor.create_task(followup())
        await started.wait()
        result = await supervisor.close(timeout=1.0)

        self.assertEqual(result["status"], "stopped")
        self.assertTrue(task.cancelled())


if __name__ == "__main__":
    unittest.main()
