from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class SummaryTask:
    profile_user_id: str
    session_id: str
    character_pack_id: str
    generation: int


class SummaryTaskQueue:
    def __init__(self, handler: Callable[[SummaryTask], None]):
        self._handler = handler
        self._queue: queue.Queue[SummaryTask | None] = queue.Queue()
        self._queued_keys: set[tuple[str, str, str, int]] = set()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="akane-summary-queue",
            daemon=True,
        )
        self._thread.start()

    def enqueue(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        generation: int,
    ) -> bool:
        task = SummaryTask(
            profile_user_id=str(profile_user_id),
            session_id=str(session_id),
            character_pack_id=str(character_pack_id or ""),
            generation=int(generation),
        )
        task_key = self._task_key(task)
        with self._lock:
            if task_key in self._queued_keys:
                return False
            self._queued_keys.add(task_key)
        self._queue.put(task)
        return True

    def clear_pending(self) -> None:
        with self._lock:
            self._queued_keys.clear()
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                self._queue.task_done()

    def close(self, timeout: float = 2.0) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._queue.put(None)
        self._thread.join(timeout=max(0.1, float(timeout)))

    def _worker_loop(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                with self._lock:
                    self._queued_keys.discard(self._task_key(task))
                self._handler(task)
            except Exception:
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def _task_key(self, task: SummaryTask) -> tuple[str, str, str, int]:
        return (task.profile_user_id, task.session_id, task.character_pack_id, task.generation)
