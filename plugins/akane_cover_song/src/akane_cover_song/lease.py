"""Cross-process ownership of a dedicated RVC WebUI's mutable model state.

This is a lock and a fail-closed transport fence, not a task/job registry.
An unconfirmed request survives worker termination as a marker. Only an operator
who has restarted the dedicated WebUI may clear that marker.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .errors import CoverSongError


def check_cancelled(cancelled):
    if cancelled():
        raise CoverSongError(
            stage="cancelled",
            reason="operation_cancelled",
            public_message="翻唱任务已取消；已发出的 RVC 请求已确认结束。",
        )


class EndpointLease:
    _guard = threading.Lock()
    _states = {}

    def __init__(self, base_url, *, state_dir=None, timeout=1800, cancelled=lambda: False):
        parsed = urlsplit(base_url)
        # localhost/IPv4/IPv6 loopback aliases share a fence. Conservatively also
        # serialize paths on the same port: they may proxy the same WebUI.
        key = hashlib.sha256(
            f"loopback:{parsed.port or (443 if parsed.scheme == 'https' else 80)}".encode()
        ).hexdigest()
        self.directory = Path(state_dir or Path(tempfile.gettempdir()) / "akane-rvc-leases").resolve()
        self.key = key
        self.marker = self.directory / f"{key}.inflight"
        self.timeout = timeout
        self.cancelled = cancelled
        with self._guard:
            self.state = self._states.setdefault(str(self.directory / key), {"lock": threading.RLock(), "depth": 0})

    @contextmanager
    def hold(self, *, recovery=False):
        deadline = time.monotonic() + self.timeout
        check_cancelled(self.cancelled)
        while not self.state["lock"].acquire(timeout=0.1):
            check_cancelled(self.cancelled)
            self._check_deadline(deadline)
        handle = None
        locked = False
        try:
            if self.state["depth"] == 0:
                self.directory.mkdir(parents=True, exist_ok=True)
                handle = (self.directory / f"{self.key}.lock").open("a+b")
                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
                while True:
                    handle.seek(0)
                    try:
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except OSError:
                        check_cancelled(self.cancelled)
                        self._check_deadline(deadline)
                        time.sleep(0.05)
                if not recovery:
                    self.require_clean()
            self.state["depth"] += 1
            try:
                yield
            finally:
                self.state["depth"] -= 1
        finally:
            try:
                if handle is not None:
                    try:
                        if locked:
                            handle.seek(0)
                            if os.name == "nt":
                                import msvcrt

                                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl

                                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        handle.close()
            finally:
                self.state["lock"].release()

    def _check_deadline(self, deadline):
        if time.monotonic() >= deadline:
            raise CoverSongError(
                stage="provider", reason="rvc_busy", public_message="RVC 正在处理其他任务，请稍后再试。"
            )

    def require_clean(self):
        if self.marker.exists():
            raise CoverSongError(
                stage="provider",
                reason="rvc_remote_completion_unconfirmed",
                public_message="上次 RVC 调用未确认结束，已暂停新推理；请管理员重启专用服务后解除保护。",
            )

    def begin(self, session):
        self.require_clean()
        # Persist before dispatch, including fsync: a killed worker must not
        # permit another process to switch a model still used by the server.
        with self.marker.open("xb") as handle:
            handle.write(session.encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())

    def finish(self):
        self.marker.unlink()

    def acknowledge_restart(self, *, confirmed=False):
        if confirmed is not True:
            raise ValueError("explicit_restart_confirmation_required")
        with self.hold(recovery=True):
            if self.marker.exists():
                self.marker.unlink()
