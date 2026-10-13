"""One owner-scoped run store, ordering lock, retention policy, and terminal observer."""

from __future__ import annotations

import logging

import threading

import time

from typing import (
    Any,
)

from ..execution_specs import (
    EXEC_DEFAULT_MAX_LOG_BYTES,
    EXEC_DEFAULT_MAX_RUNS,
    EXEC_DEFAULT_RUN_RETENTION_SECONDS,
    EXEC_INITIAL_OUTPUT_MAX_BYTES,
    EXEC_INITIAL_OUTPUT_MAX_LINES,
    EXEC_STATUS_OUTPUT_MAX_BYTES,
    EXEC_STATUS_OUTPUT_MAX_LINES,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_UNKNOWN,
    EXEC_TERMINAL_STATUSES,
)

from .models import (
    ExecutionRunOwner,
    ExecRunStart,
    ExecRunStatus,
    ExecutionRunTerminalEvent,
    _OutputSegment,
    _RunRecord,
)

from .output import (
    _RUN_ID_RE,
    parse_cursor,
    _normalize_output_ref,
    RunOutputMixin,
)


logger = logging.getLogger("akane.execution_run")


class ExecutionRunStore(RunOutputMixin):
    """Thread-safe run bookkeeping with owner-scoped, absolute cursors."""

    def __init__(
        self,
        *,
        max_log_bytes: int = EXEC_DEFAULT_MAX_LOG_BYTES,
        initial_output_bytes: int = EXEC_INITIAL_OUTPUT_MAX_BYTES,
        initial_output_lines: int = EXEC_INITIAL_OUTPUT_MAX_LINES,
        status_output_bytes: int = EXEC_STATUS_OUTPUT_MAX_BYTES,
        status_output_lines: int = EXEC_STATUS_OUTPUT_MAX_LINES,
        run_retention_seconds: int = EXEC_DEFAULT_RUN_RETENTION_SECONDS,
        max_runs: int = EXEC_DEFAULT_MAX_RUNS,
        now: Any = None,
    ) -> None:
        self.max_log_bytes = max(1024, int(max_log_bytes))
        self.initial_output_bytes = max(256, min(self.max_log_bytes, int(initial_output_bytes)))
        self.initial_output_lines = max(1, int(initial_output_lines))
        self.status_output_bytes = max(256, min(self.max_log_bytes, int(status_output_bytes)))
        self.status_output_lines = max(1, int(status_output_lines))
        self.retention_seconds = max(1, int(run_retention_seconds))
        self.max_runs = max(1, int(max_runs))
        self._now = now or time.time
        self._lock = threading.RLock()
        self._runs: dict[str, _RunRecord] = {}
        self._terminal_observer: Any = None

    def bind_terminal_observer(self, observer: Any = None) -> None:
        """Bind one host lifecycle observer without changing run authority."""

        if observer is not None and not callable(observer):
            raise TypeError("execution_terminal_observer_not_callable")
        with self._lock:
            self._terminal_observer = observer

    def register(
        self,
        run_id: str,
        *,
        owner: ExecutionRunOwner,
        output_ref: str | None = None,
    ) -> None:
        clean_id = str(run_id or "").strip()
        if not _RUN_ID_RE.fullmatch(clean_id):
            raise ValueError("invalid_run_id")
        if not isinstance(owner, ExecutionRunOwner):
            raise TypeError("execution_run_owner_required")
        now = self._now()
        with self._lock:
            if clean_id in self._runs:
                raise ValueError("duplicate_run_id")
            self._evict_expired_locked()
            if len(self._runs) >= self.max_runs:
                terminal = sorted(
                    (record for record in self._runs.values() if record.status in EXEC_TERMINAL_STATUSES),
                    key=lambda record: record.updated_at,
                )
                if terminal:
                    del self._runs[terminal[0].run_id]
            if len(self._runs) >= self.max_runs:
                raise RuntimeError("execution_run_capacity_reached")
            self._runs[clean_id] = _RunRecord(
                run_id=clean_id,
                owner=owner,
                output_ref=_normalize_output_ref(output_ref, run_id=clean_id),
                created_at=now,
                updated_at=now,
            )

    def append_output(self, run_id: str, stream: str, text: str, *, owner: ExecutionRunOwner) -> None:
        data = str(text or "").encode("utf-8")
        if not data:
            return
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None or record.status != EXEC_STATUS_RUNNING:
                return
            record.segments.append(_OutputSegment("stdout" if stream == "stdout" else "stderr", data))
            record.retained_bytes += len(data)
            record.absolute_end_bytes += len(data)
            record.updated_at = self._now()
            self._trim_locked(record)

    def mark_terminal(
        self,
        run_id: str,
        status: str,
        *,
        owner: ExecutionRunOwner,
        exit_code: int | None = None,
        reason: str = "",
    ) -> bool:
        if status not in EXEC_TERMINAL_STATUSES:
            raise ValueError(f"invalid_terminal_status:{status}")
        event: ExecutionRunTerminalEvent | None = None
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None or record.status != EXEC_STATUS_RUNNING:
                return False
            record.status = status
            record.exit_code = exit_code
            record.reason = str(reason or "")
            record.updated_at = self._now()
            record.finished_at = record.updated_at
            event = self._terminal_event(record)
        self._notify_terminal(event)
        return True

    def request_cancel(self, run_id: str, *, owner: ExecutionRunOwner) -> str:
        """Record intent only; the provider must terminate and confirm it."""

        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None:
                return EXEC_STATUS_UNKNOWN
            if record.status != EXEC_STATUS_RUNNING:
                return "already_ended"
            record.cancel_requested = True
            record.updated_at = self._now()
            return "cancel_requested"

    def request_cancel_all(self, *, provider_id: str) -> int:
        """Signal every active run owned by one provider during Host shutdown."""

        clean_provider_id = str(provider_id or "").strip()
        if not clean_provider_id:
            return 0
        requested = 0
        with self._lock:
            now = self._now()
            for record in self._runs.values():
                if record.status != EXEC_STATUS_RUNNING or record.owner.provider_id != clean_provider_id:
                    continue
                if not record.cancel_requested:
                    record.cancel_requested = True
                    record.updated_at = now
                    requested += 1
        return requested

    def cancel_requested(self, run_id: str, *, owner: ExecutionRunOwner) -> bool:
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            return bool(record and record.cancel_requested)

    def confirm_cancelled(self, run_id: str, *, owner: ExecutionRunOwner) -> bool:
        event: ExecutionRunTerminalEvent | None = None
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None or record.status != EXEC_STATUS_RUNNING or not record.cancel_requested:
                return False
            record.status = EXEC_STATUS_CANCELLED
            record.reason = "cancelled"
            record.updated_at = self._now()
            record.finished_at = record.updated_at
            event = self._terminal_event(record)
        self._notify_terminal(event)
        return True

    @staticmethod
    def _terminal_event(record: _RunRecord) -> ExecutionRunTerminalEvent:
        return ExecutionRunTerminalEvent(
            run_id=record.run_id,
            owner=record.owner,
            status=record.status,
            exit_code=record.exit_code,
            reason=record.reason,
            finished_at=record.finished_at,
        )

    def _notify_terminal(self, event: ExecutionRunTerminalEvent) -> None:
        with self._lock:
            observer = self._terminal_observer
        if observer is None:
            return
        try:
            observer(event)
        except Exception:
            # Process truth is already committed. A failed completion wake-up
            # remains recoverable through the durable Host Job on restart.
            logger.exception("execution terminal observer failed: %s", event.run_id)

    def get(self, run_id: str, *, owner: ExecutionRunOwner) -> _RunRecord | None:
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None:
                return None
            if self._is_expired_locked(record):
                del self._runs[record.run_id]
                return None
            return record

    def window_snapshot(self, run_id: str, *, owner: ExecutionRunOwner) -> ExecRunStart | None:
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None or self._evict_if_expired_locked(record):
                return None
            stdout, stderr, _tail, page_end = self._page_locked(
                record,
                record.window_start_bytes,
                max_bytes=self.initial_output_bytes,
                max_lines=self.initial_output_lines,
            )
            next_cursor = self._next_cursor_locked(record, page_end)
            return ExecRunStart(
                status=record.status,
                run_id=record.run_id,
                exit_code=record.exit_code,
                stdout=stdout,
                stderr=stderr,
                next_cursor=next_cursor,
                output_ref=record.output_ref,
                reason="output_compacted" if record.window_start_bytes else record.reason,
                started_at=record.created_at,
                finished_at=record.finished_at,
            )

    def read(
        self,
        run_id: str,
        cursor: str | None = None,
        *,
        owner: ExecutionRunOwner,
    ) -> ExecRunStatus:
        clean_run_id = str(run_id or "").strip()
        with self._lock:
            record = self._owned_record_locked(clean_run_id, owner)
            if record is None or self._evict_if_expired_locked(record):
                return _unknown_status(clean_run_id)

            supplied_cursor = str(cursor or "").strip()
            parsed = parse_cursor(supplied_cursor, run_id=record.run_id) if supplied_cursor else None
            invalid_cursor = bool(supplied_cursor and parsed is None)
            requested = record.window_start_bytes if parsed is None else parsed
            compacted = requested < record.window_start_bytes or (not supplied_cursor and record.window_start_bytes > 0)
            if compacted or invalid_cursor:
                requested = record.window_start_bytes
            elif requested > record.absolute_end_bytes:
                requested = record.absolute_end_bytes
                invalid_cursor = True

            _stdout, _stderr, tail, page_end = self._page_locked(
                record,
                requested,
                max_bytes=self.status_output_bytes,
                max_lines=self.status_output_lines,
            )
            reason = "invalid_cursor" if invalid_cursor else "output_compacted" if compacted else record.reason
            return ExecRunStatus(
                status=record.status,
                run_id=record.run_id,
                exit_code=record.exit_code,
                tail=tail,
                next_cursor=self._next_cursor_locked(record, page_end),
                output_ref=record.output_ref,
                reason=reason,
                started_at=record.created_at,
                finished_at=record.finished_at,
            )

    def evict_expired(self) -> int:
        with self._lock:
            return self._evict_expired_locked()

    def _evict_expired_locked(self) -> int:
        expired = [run_id for run_id, record in self._runs.items() if self._is_expired_locked(record)]
        for run_id in expired:
            del self._runs[run_id]
        return len(expired)

    def _owned_record_locked(self, run_id: str, owner: ExecutionRunOwner) -> _RunRecord | None:
        if not isinstance(owner, ExecutionRunOwner):
            return None
        record = self._runs.get(str(run_id or "").strip())
        return record if record is not None and record.owner == owner else None

    def _is_expired_locked(self, record: _RunRecord) -> bool:
        return record.status in EXEC_TERMINAL_STATUSES and self._now() - record.updated_at >= self.retention_seconds

    def _evict_if_expired_locked(self, record: _RunRecord) -> bool:
        if not self._is_expired_locked(record):
            return False
        del self._runs[record.run_id]
        return True


def _unknown_status(run_id: str) -> ExecRunStatus:
    return ExecRunStatus(status=EXEC_STATUS_UNKNOWN, run_id=run_id, reason="run_not_found")
