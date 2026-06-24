from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


logger = logging.getLogger("akane.background_tasks")


@dataclass(frozen=True)
class BackgroundTaskHandle:
    task_id: str
    lane: str
    name: str
    submitted_at: float


@dataclass(frozen=True)
class _BackgroundTask:
    handle: BackgroundTaskHandle
    fn: Callable[..., Any]
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)


class _TaskLane:
    def __init__(self, *, name: str, workers: int) -> None:
        self.name = str(name or "default")
        self.worker_count = max(1, int(workers or 1))
        self.queue: queue.Queue[_BackgroundTask | None] = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.closed = threading.Event()
        for index in range(self.worker_count):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"akane-bg-{self.name}-{index + 1}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def submit(self, task: _BackgroundTask) -> None:
        if self.closed.is_set():
            raise RuntimeError(f"后台任务队列 {self.name} 已关闭。")
        self.queue.put(task)

    def close(self, *, timeout: float) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        for _ in self.threads:
            self.queue.put(None)
        deadline = time.time() + max(0.1, float(timeout))
        for thread in self.threads:
            remaining = max(0.1, deadline - time.time())
            thread.join(timeout=remaining)

    def wait_idle(self, *, timeout: float) -> bool:
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            if self.queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self.queue.unfinished_tasks == 0

    def _worker_loop(self) -> None:
        while True:
            task = self.queue.get()
            try:
                if task is None:
                    return
                try:
                    task.fn(*task.args, **task.kwargs)
                except Exception:
                    logger.exception(
                        "background task failed: lane=%s name=%s task_id=%s",
                        task.handle.lane,
                        task.handle.name,
                        task.handle.task_id,
                    )
                    traceback.print_exc()
            finally:
                self.queue.task_done()


class BackgroundTaskRunner:
    """Small named-lane background task runner.

    It deliberately stays simple: bounded worker lanes, daemon threads, explicit
    close, and central exception logging. This replaces ad-hoc one-thread-per-job
    call sites without forcing the whole engine into asyncio.
    """

    def __init__(
        self,
        lane_workers: dict[str, int] | None = None,
        *,
        default_workers: int = 1,
    ) -> None:
        self._lane_workers = dict(lane_workers or {})
        self._default_workers = max(1, int(default_workers or 1))
        self._lanes: dict[str, _TaskLane] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()

    def submit(
        self,
        *,
        lane: str,
        name: str,
        fn: Callable[..., Any],
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> BackgroundTaskHandle:
        if self._closed.is_set():
            raise RuntimeError("后台任务调度器已关闭。")
        lane_name = str(lane or "default").strip() or "default"
        task_name = str(name or lane_name).strip() or lane_name
        handle = BackgroundTaskHandle(
            task_id=uuid.uuid4().hex,
            lane=lane_name,
            name=task_name,
            submitted_at=time.time(),
        )
        task = _BackgroundTask(
            handle=handle,
            fn=fn,
            args=tuple(args or ()),
            kwargs=dict(kwargs or {}),
        )
        self._get_lane(lane_name).submit(task)
        return handle

    def close(self, *, timeout: float = 2.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            lanes = list(self._lanes.values())
        for lane in lanes:
            lane.close(timeout=timeout)

    def wait_idle(self, *, lane: str | None = None, timeout: float = 2.0) -> bool:
        with self._lock:
            lanes = [self._lanes[str(lane)]] if lane and str(lane) in self._lanes else list(self._lanes.values())
        if not lanes:
            return True
        deadline = time.time() + max(0.1, float(timeout))
        for item in lanes:
            remaining = max(0.1, deadline - time.time())
            if not item.wait_idle(timeout=remaining):
                return False
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            lanes = dict(self._lanes)
        return {
            "closed": self._closed.is_set(),
            "lanes": {
                name: {
                    "workers": lane.worker_count,
                    "queued_or_running": int(lane.queue.unfinished_tasks),
                    "queue_size": int(lane.queue.qsize()),
                }
                for name, lane in lanes.items()
            },
        }

    def _get_lane(self, lane_name: str) -> _TaskLane:
        with self._lock:
            existing = self._lanes.get(lane_name)
            if existing is not None:
                return existing
            workers = int(self._lane_workers.get(lane_name, self._default_workers))
            lane = _TaskLane(name=lane_name, workers=workers)
            self._lanes[lane_name] = lane
            return lane
