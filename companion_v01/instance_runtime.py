"""Fail-closed process and data-root binding for one Akane instance."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .instance_profile import InstanceContext, LOCAL_DEFAULT_INSTANCE_ID


INSTANCE_BINDING_SCHEMA_VERSION = 1
INSTANCE_BINDING_FILENAME = "instance-binding.json"
INSTANCE_LOCK_FILENAME = "instance.lock"

_ACTIVE_PROCESS_LOCKS: set[Path] = set()
_ACTIVE_PROCESS_LOCKS_GUARD = threading.RLock()


class InstanceRuntimeError(RuntimeError):
    """Structured startup failure that never includes a local filesystem path."""

    def __init__(self, *, status: str, reason: str) -> None:
        self.status = str(status or "unavailable")
        self.reason = str(reason or "instance_runtime_unavailable")
        super().__init__(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "status": self.status, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class InstanceRuntimeLayout:
    """Host-owned paths for one process-bound Akane instance."""

    instance_id: str
    data_root: Path
    binding_path: Path
    run_dir: Path
    lock_path: Path
    explicit_data_root: bool


class InstanceRuntimeLease:
    """Exclusive process lease for one validated instance root."""

    def __init__(
        self,
        *,
        layout: InstanceRuntimeLayout,
        lock_fd: int,
        lock_kind: str,
    ) -> None:
        self.layout = layout
        self._lock_fd = lock_fd
        self._lock_kind = lock_kind
        self._released = False
        self._guard = threading.RLock()

    @property
    def instance_id(self) -> str:
        return self.layout.instance_id

    @property
    def root_binding_status(self) -> str:
        with self._guard:
            return "released" if self._released else "valid"

    def public_health_snapshot(self) -> dict[str, str]:
        return {
            "status": "ok" if self.root_binding_status == "valid" else "unavailable",
            "instance_id": self.instance_id,
            "root_binding": self.root_binding_status,
        }

    def release(self) -> None:
        with self._guard:
            if self._released:
                return
            self._released = True
            try:
                try:
                    _release_os_lock(self._lock_fd, self._lock_kind)
                except OSError:
                    pass
            finally:
                try:
                    os.close(self._lock_fd)
                finally:
                    with _ACTIVE_PROCESS_LOCKS_GUARD:
                        _ACTIVE_PROCESS_LOCKS.discard(self.layout.lock_path)


def bind_instance_runtime(
    instance_context: InstanceContext,
    *,
    data_root: Path,
    explicit_data_root: bool,
) -> InstanceRuntimeLease:
    """Bind one immutable instance id to one exclusive physical data root.

    The binding and lock are established before callers open application
    databases or load root-owned mutable configuration.
    """

    if not isinstance(instance_context, InstanceContext):
        _fail("instance_context_invalid")
    instance_id = instance_context.instance_id
    if instance_id != LOCAL_DEFAULT_INSTANCE_ID and not explicit_data_root:
        _fail("named_instance_requires_explicit_data_root", status="invalid_config")

    try:
        resolved_root = Path(data_root).expanduser().resolve()
        run_dir = (resolved_root / "run").resolve()
        binding_path = (resolved_root / INSTANCE_BINDING_FILENAME).resolve()
        lock_path = (run_dir / INSTANCE_LOCK_FILENAME).resolve()
        run_dir.relative_to(resolved_root)
        binding_path.relative_to(resolved_root)
        lock_path.relative_to(resolved_root)
        run_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        _fail("instance_root_unavailable")

    layout = InstanceRuntimeLayout(
        instance_id=instance_id,
        data_root=resolved_root,
        binding_path=binding_path,
        run_dir=run_dir,
        lock_path=lock_path,
        explicit_data_root=bool(explicit_data_root),
    )
    lock_fd, lock_kind = _acquire_exclusive_lock(lock_path)
    try:
        _verify_or_create_binding(layout)
    except Exception:
        try:
            _release_os_lock(lock_fd, lock_kind)
        finally:
            os.close(lock_fd)
            with _ACTIVE_PROCESS_LOCKS_GUARD:
                _ACTIVE_PROCESS_LOCKS.discard(lock_path)
        raise
    return InstanceRuntimeLease(layout=layout, lock_fd=lock_fd, lock_kind=lock_kind)


def _verify_or_create_binding(layout: InstanceRuntimeLayout) -> None:
    expected = {
        "schema_version": INSTANCE_BINDING_SCHEMA_VERSION,
        "instance_id": layout.instance_id,
    }
    if layout.binding_path.exists():
        try:
            payload = json.loads(layout.binding_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            _fail("instance_binding_unreadable", status="invalid_config")
        if not isinstance(payload, dict):
            _fail("instance_binding_invalid", status="invalid_config")
        if set(payload) != {"schema_version", "instance_id"}:
            _fail("instance_binding_invalid", status="invalid_config")
        if payload.get("schema_version") != INSTANCE_BINDING_SCHEMA_VERSION:
            _fail("instance_binding_incompatible", status="incompatible")
        if payload.get("instance_id") != layout.instance_id:
            _fail("instance_root_bound_to_other_instance", status="conflict")
        return

    temp_path = layout.binding_path.with_name(
        f".{INSTANCE_BINDING_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as binding_file:
            json.dump(expected, binding_file, ensure_ascii=False, sort_keys=True)
            binding_file.write("\n")
            binding_file.flush()
            os.fsync(binding_file.fileno())
        os.replace(temp_path, layout.binding_path)
    except OSError:
        _fail("instance_binding_write_failed")
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_exclusive_lock(lock_path: Path) -> tuple[int, str]:
    with _ACTIVE_PROCESS_LOCKS_GUARD:
        if lock_path in _ACTIVE_PROCESS_LOCKS:
            _fail("instance_root_locked", status="conflict")
        _ACTIVE_PROCESS_LOCKS.add(lock_path)

    lock_fd = -1
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        if os.fstat(lock_fd).st_size == 0:
            os.write(lock_fd, b"\0")
            os.fsync(lock_fd)
        os.lseek(lock_fd, 0, os.SEEK_SET)
        lock_kind = _acquire_os_lock(lock_fd)
        return lock_fd, lock_kind
    except (OSError, ImportError):
        if lock_fd >= 0:
            os.close(lock_fd)
        with _ACTIVE_PROCESS_LOCKS_GUARD:
            _ACTIVE_PROCESS_LOCKS.discard(lock_path)
        _fail("instance_root_locked", status="conflict")


def _acquire_os_lock(lock_fd: int) -> str:
    os.lseek(lock_fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        return "windows"
    if os.name == "posix":
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return "posix"
    raise OSError(f"unsupported platform: {sys.platform}")


def _release_os_lock(lock_fd: int, lock_kind: str) -> None:
    os.lseek(lock_fd, 0, os.SEEK_SET)
    if lock_kind == "windows":
        import msvcrt

        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
        return
    if lock_kind == "posix":
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _fail(reason: str, *, status: str = "unavailable") -> None:
    raise InstanceRuntimeError(status=status, reason=reason)


__all__ = [
    "INSTANCE_BINDING_FILENAME",
    "INSTANCE_BINDING_SCHEMA_VERSION",
    "INSTANCE_LOCK_FILENAME",
    "InstanceRuntimeError",
    "InstanceRuntimeLayout",
    "InstanceRuntimeLease",
    "bind_instance_runtime",
]
