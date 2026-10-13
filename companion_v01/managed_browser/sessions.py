"""Bounded profile/conversation browser sessions and capability probing."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable

from .runner import ManagedBrowserPageRunner


class ManagedBrowserSessionManager:
    """Bounded browser sessions keyed by Akane profile + conversation.

    The manager is intentionally small: it owns lifecycle and isolation while
    ``ManagedBrowserPageRunner`` remains the only page executor. This keeps the
    public tool contract stable and prevents a group task from inheriting a
    private chat's page, cookies, popup, or stale closed-window state.
    """

    def __init__(
        self,
        *,
        runner_factory: Callable[[], ManagedBrowserPageRunner] | None = None,
        max_sessions: int = 8,
        idle_ttl_seconds: float = 30 * 60,
        now: Any = None,
        background_probe: bool = False,
    ) -> None:
        self._runner_factory = runner_factory or ManagedBrowserPageRunner
        self._max_sessions = max(1, int(max_sessions))
        self._idle_ttl_seconds = max(30.0, float(idle_ttl_seconds))
        self._now = now or time.time
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[float, ManagedBrowserPageRunner]] = {}
        self._capability_status_cache: dict[str, Any] | None = None
        self._background_probe = bool(background_probe)
        self._probe_executor: ThreadPoolExecutor | None = None
        self._probe_future = None
        if self._background_probe:
            self._probe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akane-browser-readiness")
            self._probe_future = self._probe_executor.submit(self._probe_capability_status)

    def runner_for_session(self, profile_user_id: str, session_id: str) -> ManagedBrowserPageRunner:
        key = self._session_key(profile_user_id, session_id)
        evicted: list[ManagedBrowserPageRunner] = []
        with self._lock:
            now = float(self._now())
            for stale_key, (last_used, runner) in list(self._sessions.items()):
                if now - last_used > self._idle_ttl_seconds:
                    evicted.append(runner)
                    del self._sessions[stale_key]
            cached = self._sessions.get(key)
            if cached is None:
                runner = self._runner_factory()
                self._bind_runner_identity(runner, profile_user_id, session_id)
                self._sessions[key] = (now, runner)
            else:
                runner = cached[1]
                self._sessions[key] = (now, runner)
            while len(self._sessions) > self._max_sessions:
                oldest_key = min(self._sessions.items(), key=lambda item: item[1][0])[0]
                if oldest_key == key and len(self._sessions) > 1:
                    alternatives = [item for item in self._sessions.items() if item[0] != key]
                    oldest_key = min(alternatives, key=lambda item: item[1][0])[0]
                _last_used, old_runner = self._sessions.pop(oldest_key)
                evicted.append(old_runner)
        for old_runner in evicted:
            old_runner.shutdown()
        return runner

    @staticmethod
    def _bind_runner_identity(runner: Any, profile_user_id: str, session_id: str) -> None:
        setter = getattr(runner, "set_session_identity", None)
        if callable(setter):
            setter(profile_user_id, session_id)

    def capability_status(self) -> dict[str, Any]:
        with self._lock:
            cached = self._capability_status_cache
        if cached is not None:
            return dict(cached)
        if self._background_probe:
            future = self._probe_future
            if future is None or not future.done():
                return {"enabled": False, "status": "pending", "reason": "browser_runtime_probe_pending"}
            status = future.result()
        else:
            status = self._probe_capability_status()
        with self._lock:
            self._capability_status_cache = dict(status)
        return dict(status)

    def _probe_capability_status(self) -> dict[str, Any]:
        runner = None
        try:
            runner = self._runner_factory()
            return dict(runner.capability_status())
        except Exception:
            return {"enabled": False, "status": "missing_executor", "reason": "browser_runner_status_failed"}
        finally:
            if runner is not None:
                try:
                    runner.shutdown()
                except Exception:
                    pass

    def shutdown(self, *, timeout: float = 5.0) -> None:
        if self._probe_executor is not None:
            self._probe_executor.shutdown(wait=False, cancel_futures=False)
        with self._lock:
            runners = [runner for _last_used, runner in self._sessions.values()]
            self._sessions.clear()
        if not runners:
            return
        closer = ThreadPoolExecutor(max_workers=min(4, len(runners)), thread_name_prefix="akane-browser-close")
        futures = [closer.submit(runner.shutdown) for runner in runners]
        wait(futures, timeout=max(0.1, float(timeout)))
        # Bound the caller's wait without dropping cleanup still queued behind
        # slow sessions. Each runner closes its objects on its owning thread.
        closer.shutdown(wait=False, cancel_futures=False)

    @staticmethod
    def _session_key(profile_user_id: str, session_id: str) -> str:
        profile = str(profile_user_id or "").strip() or "anonymous"
        session = str(session_id or "").strip() or profile
        return f"{profile}\x1f{session}"
