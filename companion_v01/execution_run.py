"""Execution state, paged output, provider boundary, and outcome mapping.

This Phase 1 kernel contains no process execution. Providers own process
lifecycle; this module owns the contracts that keep their results honest.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from .execution_specs import (
    EXEC_COMMAND_MAX_CHARS,
    EXEC_CWD_MAX_CHARS,
    EXEC_DEFAULT_MAX_LOG_BYTES,
    EXEC_DEFAULT_MAX_RUNS,
    EXEC_DEFAULT_RUN_RETENTION_SECONDS,
    EXEC_MAX_INITIAL_WAIT_SECONDS,
    EXEC_MAX_TIMEOUT_SECONDS,
    EXEC_INITIAL_OUTPUT_MAX_BYTES,
    EXEC_INITIAL_OUTPUT_MAX_LINES,
    EXEC_RUN_TOOL_NAME,
    EXEC_STATUS_TOOL_NAME,
    EXEC_STATUS_OUTPUT_MAX_BYTES,
    EXEC_STATUS_OUTPUT_MAX_LINES,
    EXEC_CANCEL_TOOL_NAME,
    EXEC_CURSOR_MAX_CHARS,
    EXEC_RUN_ID_MAX_CHARS,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNAVAILABLE,
    EXEC_STATUS_UNKNOWN,
    EXEC_TERMINAL_STATUSES,
    normalize_initial_wait_seconds,
    normalize_status_wait_seconds,
    normalize_timeout_seconds,
)

_RUN_ID_PREFIX = "execrun_"
_RUN_ID_RE = re.compile(r"^execrun_[a-f0-9]{32}$")
_CURSOR_RE = re.compile(r"^c1\.([a-f0-9]{16})\.([a-f0-9]+)$")
_READY_AVAILABILITY_STATUSES = frozenset({"ready", "available", "degraded", "ok"})


def _sanitize_model_text(value: Any) -> str:
    """Keep executor output useful without projecting host secrets.

    The provider keeps the original bytes in its private run log.  This copy
    is the model-facing projection: secrets are redacted, but executable
    evidence (commands, discovered paths, output text) stays intact so the
    model can reuse it verbatim in the next round.
    """
    text = str(value or "")
    text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    return text


def new_run_id() -> str:
    return f"{_RUN_ID_PREFIX}{uuid.uuid4().hex}"


def is_valid_run_id(value: Any) -> bool:
    return _RUN_ID_RE.fullmatch(str(value or "").strip()) is not None


def _run_cursor_tag(run_id: str) -> str:
    return hashlib.sha256(str(run_id or "").encode("utf-8")).hexdigest()[:16]


def make_cursor(run_id: str, offset: int) -> str:
    """Return a stable opaque cursor bound to one run and absolute offset."""

    clean_run_id = str(run_id or "").strip()
    if not _RUN_ID_RE.fullmatch(clean_run_id):
        raise ValueError("invalid_run_id")
    return f"c1.{_run_cursor_tag(clean_run_id)}.{max(0, int(offset)):x}"


def parse_cursor(value: Any, *, run_id: str) -> int | None:
    text = str(value or "").strip()
    match = _CURSOR_RE.fullmatch(text)
    if match is None or match.group(1) != _run_cursor_tag(run_id):
        return None
    try:
        return int(match.group(2), 16)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ExecutionRunOwner:
    """Host-only ownership scope; never project this into model-visible data."""

    profile_user_id: str
    session_id: str
    provider_id: str

    def __post_init__(self) -> None:
        for field_name in ("profile_user_id", "session_id", "provider_id"):
            clean = str(getattr(self, field_name) or "").strip()
            if not clean:
                raise ValueError(f"execution_owner_{field_name}_required")
            object.__setattr__(self, field_name, clean)


@dataclass(frozen=True, slots=True)
class ExecutionAvailability:
    enabled: bool
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecRunStart:
    status: str
    run_id: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    next_cursor: str | None = None
    output_ref: str | None = None
    reason: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ExecRunStatus:
    status: str
    run_id: str
    exit_code: int | None = None
    tail: str = ""
    next_cursor: str | None = None
    output_ref: str | None = None
    reason: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ExecCancelResult:
    ok: bool
    status: str
    run_id: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecMappedResult:
    envelope_status: str
    event_status: str
    reason: str
    model_feedback: str
    data: Mapping[str, Any]
    event: Mapping[str, Any]


@runtime_checkable
class ExecutionProvider(Protocol):
    def availability(self) -> ExecutionAvailability: ...

    def run(
        self,
        *,
        owner: ExecutionRunOwner,
        command: str,
        cwd: str = "",
        timeout_seconds: int = EXEC_MAX_TIMEOUT_SECONDS,
        initial_wait_seconds: int = EXEC_MAX_INITIAL_WAIT_SECONDS,
        run_id: str = "",
    ) -> ExecRunStart: ...

    def status(
        self,
        *,
        owner: ExecutionRunOwner,
        run_id: str,
        cursor: str | None = None,
    ) -> ExecRunStatus: ...

    def cancel(self, *, owner: ExecutionRunOwner, run_id: str) -> ExecCancelResult: ...


@dataclass
class _OutputSegment:
    stream: str
    data: bytes


@dataclass
class _RunRecord:
    run_id: str
    owner: ExecutionRunOwner
    output_ref: str | None = None
    status: str = EXEC_STATUS_RUNNING
    exit_code: int | None = None
    reason: str = ""
    cancel_requested: bool = False
    segments: list[_OutputSegment] = field(default_factory=list)
    window_start_bytes: int = 0
    retained_bytes: int = 0
    absolute_end_bytes: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float = 0.0


class ExecutionRunStore:
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
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None or record.status != EXEC_STATUS_RUNNING:
                return False
            record.status = status
            record.exit_code = exit_code
            record.reason = str(reason or "")
            record.updated_at = self._now()
            record.finished_at = record.updated_at
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

    def cancel_requested(self, run_id: str, *, owner: ExecutionRunOwner) -> bool:
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            return bool(record and record.cancel_requested)

    def confirm_cancelled(self, run_id: str, *, owner: ExecutionRunOwner) -> bool:
        with self._lock:
            record = self._owned_record_locked(run_id, owner)
            if record is None or record.status != EXEC_STATUS_RUNNING or not record.cancel_requested:
                return False
            record.status = EXEC_STATUS_CANCELLED
            record.reason = "cancelled"
            record.updated_at = self._now()
            record.finished_at = record.updated_at
            return True

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

    def _trim_locked(self, record: _RunRecord) -> None:
        over = record.retained_bytes - self.max_log_bytes
        while over > 0 and record.segments:
            segment = record.segments[0]
            if len(segment.data) <= over:
                dropped = len(segment.data)
                record.segments.pop(0)
            else:
                dropped = _utf8_prefix_at_least(segment.data, over)
                segment.data = segment.data[dropped:]
            record.window_start_bytes += dropped
            record.retained_bytes -= dropped
            over = record.retained_bytes - self.max_log_bytes

    def _page_locked(
        self,
        record: _RunRecord,
        absolute_offset: int,
        *,
        max_bytes: int,
        max_lines: int,
    ) -> tuple[str, str, str, int]:
        local_skip = max(0, absolute_offset - record.window_start_bytes)
        absolute_position = record.window_start_bytes
        remaining_bytes = max(1, int(max_bytes))
        remaining_lines = max(1, int(max_lines))
        stdout: list[bytes] = []
        stderr: list[bytes] = []
        ordered: list[bytes] = []

        for segment in record.segments:
            segment_end = absolute_position + len(segment.data)
            if local_skip >= len(segment.data):
                local_skip -= len(segment.data)
                absolute_position = segment_end
                continue
            start = _utf8_boundary_at_or_after(segment.data, local_skip)
            available = segment.data[start:]
            take = _utf8_prefix_at_most(available, remaining_bytes)
            take = min(take, _prefix_through_newlines(available[:take], remaining_lines))
            if take <= 0:
                break
            target = stdout if segment.stream == "stdout" else stderr
            target.append(available[:take])
            ordered.append(available[:take])
            absolute_position += start + take
            remaining_bytes -= take
            remaining_lines -= available[:take].count(b"\n")
            local_skip = 0
            if take < len(available) or remaining_bytes <= 0 or remaining_lines <= 0:
                break

        return (
            b"".join(stdout).decode("utf-8", errors="strict"),
            b"".join(stderr).decode("utf-8", errors="strict"),
            b"".join(ordered).decode("utf-8", errors="strict"),
            min(record.absolute_end_bytes, absolute_position),
        )

    @staticmethod
    def _next_cursor_locked(record: _RunRecord, page_end: int) -> str | None:
        if page_end < record.absolute_end_bytes or record.status == EXEC_STATUS_RUNNING:
            return make_cursor(record.run_id, page_end)
        return None


def _utf8_boundary_at_or_after(data: bytes, offset: int) -> int:
    index = max(0, min(len(data), int(offset)))
    while index < len(data) and data[index] & 0xC0 == 0x80:
        index += 1
    return index


def _utf8_prefix_at_most(data: bytes, limit: int) -> int:
    if limit <= 0 or not data:
        return 0
    end = min(len(data), int(limit))
    while end > 0 and end < len(data) and data[end] & 0xC0 == 0x80:
        end -= 1
    return end


def _utf8_prefix_at_least(data: bytes, minimum: int) -> int:
    end = max(0, min(len(data), int(minimum)))
    while end < len(data) and data[end] & 0xC0 == 0x80:
        end += 1
    return end


def _prefix_through_newlines(data: bytes, max_newlines: int) -> int:
    """Return a prefix containing at most ``max_newlines`` line endings."""

    if max_newlines <= 0:
        return 0
    position = -1
    for _ in range(max_newlines):
        position = data.find(b"\n", position + 1)
        if position < 0:
            return len(data)
    next_newline = data.find(b"\n", position + 1)
    return len(data) if next_newline < 0 else position + 1


def _unknown_status(run_id: str) -> ExecRunStatus:
    return ExecRunStatus(status=EXEC_STATUS_UNKNOWN, run_id=run_id, reason="run_not_found")


def _normalize_output_ref(value: str | None, *, run_id: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    expected = f"runlog:{run_id}"
    if text != expected:
        raise ValueError("invalid_execution_output_ref")
    return expected


def _preview(text: str, *, limit: int = 800) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else f"{clean[:limit]}…（输出过长已截断）"


def _unknown_mapped(reason: str, *, run_start: ExecRunStart | None = None) -> ExecMappedResult:
    candidate_run_id = str(getattr(run_start, "run_id", "") or "").strip()
    run_id = candidate_run_id if _RUN_ID_RE.fullmatch(candidate_run_id) else ""
    clean_reason = _sanitize_model_text(str(reason or "execution_unknown"))
    data = {
        "status": EXEC_STATUS_EXECUTION_UNKNOWN,
        "run_id": run_id,
        "exit_code": getattr(run_start, "exit_code", None),
        "stdout": "",
        "stderr": "",
        "next_cursor": None,
        "output_ref": None,
        "reason": clean_reason,
    }
    return ExecMappedResult(
        envelope_status="error",
        event_status=EXEC_STATUS_EXECUTION_UNKNOWN,
        reason=clean_reason,
        model_feedback="命令执行结果无法确认。请明确说明无法确认，不要声称命令已经成功或已经停止。",
        data=data,
        event={
            "type": "capability_execution_result",
            "tool_type": EXEC_RUN_TOOL_NAME,
            "status": EXEC_STATUS_EXECUTION_UNKNOWN,
            "reason": clean_reason,
        },
    )


def _rejected_mapped(
    reason: str,
    *,
    max_chars: int | None = None,
    actual_chars: int | None = None,
    recommended_action: str = "",
) -> ExecMappedResult:
    clean_reason = _sanitize_model_text(str(reason or "execution_request_rejected"))
    data = {
        "status": "rejected",
        "run_id": "",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "next_cursor": None,
        "output_ref": None,
        "reason": clean_reason,
        "max_chars": max_chars,
        "actual_chars": actual_chars,
        "recommended_action": str(recommended_action or "") or None,
    }
    if clean_reason == "command_too_long":
        feedback = (
            f"这次命令没有执行：command_too_long（max_chars={max_chars}，actual_chars={actual_chars}）。"
            "源码或长文本请改用 workspace_write / workspace_patch；Shell 只用于构建、运行和测试。"
        )
    elif clean_reason == "command_required":
        feedback = "这次命令没有执行：command_required。请提供非空 command。"
    elif clean_reason == "cwd_too_long":
        feedback = (
            f"这次命令没有执行：cwd_too_long（max_chars={max_chars}，actual_chars={actual_chars}）。"
            "请使用短的工作区相对路径或挂载别名。"
        )
    else:
        feedback = f"这次命令没有执行：{clean_reason}。请修正参数后再试，不要声称已经执行。"
    return ExecMappedResult(
        envelope_status="error",
        event_status="rejected",
        reason=clean_reason,
        model_feedback=feedback,
        data=data,
        event={
            "type": "capability_execution_result",
            "tool_type": EXEC_RUN_TOOL_NAME,
            "status": "rejected",
            "reason": clean_reason,
        },
    )


def map_exec_run_outcome(run_start: ExecRunStart) -> ExecMappedResult:
    if not isinstance(run_start, ExecRunStart):
        return _unknown_mapped("invalid_provider_result")
    status = str(run_start.status or "").strip()
    run_id = str(run_start.run_id or "").strip()
    reason = _sanitize_model_text(str(run_start.reason or ""))
    if status not in {
        EXEC_STATUS_COMPLETED,
        EXEC_STATUS_FAILED,
        EXEC_STATUS_TIMED_OUT,
        EXEC_STATUS_CANCELLED,
        EXEC_STATUS_RUNNING,
        EXEC_STATUS_UNAVAILABLE,
    }:
        return _unknown_mapped("invalid_execution_status", run_start=run_start)
    if status != EXEC_STATUS_UNAVAILABLE and not _RUN_ID_RE.fullmatch(run_id):
        return _unknown_mapped("invalid_execution_run_id", run_start=run_start)
    if status == EXEC_STATUS_COMPLETED and run_start.exit_code != 0:
        return _unknown_mapped("completed_exit_code_invalid", run_start=run_start)
    output_bytes = len(str(run_start.stdout or "").encode("utf-8")) + len(
        str(run_start.stderr or "").encode("utf-8")
    )
    output_lines = str(run_start.stdout or "").count("\n") + str(run_start.stderr or "").count("\n")
    if output_bytes > EXEC_INITIAL_OUTPUT_MAX_BYTES or output_lines > EXEC_INITIAL_OUTPUT_MAX_LINES:
        return _unknown_mapped("execution_output_page_too_large", run_start=run_start)
    if run_start.next_cursor is not None and parse_cursor(run_start.next_cursor, run_id=run_id) is None:
        return _unknown_mapped("invalid_execution_cursor", run_start=run_start)
    try:
        output_ref = _normalize_output_ref(run_start.output_ref, run_id=run_id)
    except ValueError:
        return _unknown_mapped("invalid_execution_output_ref", run_start=run_start)
    if status == EXEC_STATUS_RUNNING and run_start.next_cursor is None:
        return _unknown_mapped("running_cursor_required", run_start=run_start)

    data: dict[str, Any] = {
        "status": status,
        "run_id": run_id,
        "exit_code": run_start.exit_code,
        "stdout": _sanitize_model_text(run_start.stdout),
        "stderr": _sanitize_model_text(run_start.stderr),
        "next_cursor": run_start.next_cursor,
        "output_ref": output_ref,
        "reason": reason,
        "started_at": max(0.0, float(run_start.started_at or 0.0)) or None,
        "finished_at": max(0.0, float(run_start.finished_at or 0.0)) or None,
        "observed_at": time.time(),
    }
    event = {"type": "capability_execution_result", "tool_type": EXEC_RUN_TOOL_NAME, "status": status}

    if status == EXEC_STATUS_COMPLETED:
        followup = (
            f"输出尚未读完，请调用 exec_status(run_id={run_id}, cursor={run_start.next_cursor}, "
            "wait_seconds=30) 继续读取。"
            if run_start.next_cursor
            else ""
        )
        compacted = (
            f"较早输出不在当前内存窗口，可按 output_ref={output_ref} 重新读取完整日志。"
            if reason == "output_compacted" and output_ref
            else "较早输出已不在当前可读窗口。"
            if reason == "output_compacted"
            else ""
        )
        output = _format_execution_output(stdout=data["stdout"], stderr=data["stderr"])
        feedback = f"命令已执行完成（exit_code=0，run_id={run_id}）。{output}{compacted}{followup}"
        return ExecMappedResult("ok", status, reason, feedback, data, event)
    if status == EXEC_STATUS_RUNNING:
        output = _format_execution_output(stdout=data["stdout"], stderr=data["stderr"])
        continuation = (
            f"当前增量读取位置为 cursor={run_start.next_cursor}。"
            if run_start.next_cursor
            else ""
        )
        output_ref_hint = f"完整日志引用：output_ref={output_ref}。" if output_ref else ""
        feedback = (
            f"命令仍在执行中（run_id={run_id}），尚未完成。请用 exec_status 查询进度、exec_cancel 停止；"
            f"等待型任务优先用 wait_seconds=30 在同一次状态调用内等待，避免频繁轮询；"
            f"不要声称命令已经完成。{continuation}{output_ref_hint}{output}"
        )
        return ExecMappedResult("ok", status, reason, feedback, data, event)
    if status == EXEC_STATUS_UNAVAILABLE:
        clean_reason = reason or "execution_unavailable"
        event = {**event, "reason": clean_reason}
        feedback = (
            f"<capability_unavailable>当前无法执行命令：{clean_reason}。"
            "请直接说明这次不能执行，不要假装已经执行。</capability_unavailable>"
        )
        return ExecMappedResult("unavailable", status, clean_reason, feedback, data, event)

    default_reasons = {
        EXEC_STATUS_FAILED: "execution_failed",
        EXEC_STATUS_TIMED_OUT: "execution_timed_out",
        EXEC_STATUS_CANCELLED: "execution_cancelled",
    }
    clean_reason = reason or default_reasons[status]
    if status == EXEC_STATUS_FAILED:
        detail = _format_execution_output(stdout=data["stdout"], stderr=data["stderr"]) or clean_reason
        feedback = f"命令执行失败（exit_code={run_start.exit_code!r}）。{detail}请明确说明这次没有完成，不要声称成功。"
    elif status == EXEC_STATUS_TIMED_OUT:
        feedback = "命令执行超时，执行器报告进程组已终止。请明确说明这次没有完成，不要声称成功。"
    else:
        feedback = "命令已被执行器确认取消。请明确说明命令没有完成，不要声称成功。"
    return ExecMappedResult("error", status, clean_reason, feedback, data, {**event, "reason": clean_reason})


def execute_exec_run(
    provider: ExecutionProvider,
    *,
    owner: ExecutionRunOwner,
    command: str,
    cwd: str = "",
    timeout_seconds: Any = None,
    initial_wait_seconds: Any = None,
    run_id: str = "",
) -> ExecMappedResult:
    clean_command = str(command or "").strip()
    clean_cwd = str(cwd or "").strip()
    clean_run_id = str(run_id or "").strip()
    if not isinstance(owner, ExecutionRunOwner):
        return _unknown_mapped("execution_owner_required")
    if not clean_command:
        return _rejected_mapped("command_required")
    if len(clean_command) > EXEC_COMMAND_MAX_CHARS:
        return _rejected_mapped(
            "command_too_long",
            max_chars=EXEC_COMMAND_MAX_CHARS,
            actual_chars=len(clean_command),
            recommended_action="workspace_write_or_patch",
        )
    if len(clean_cwd) > EXEC_CWD_MAX_CHARS:
        return _rejected_mapped(
            "cwd_too_long",
            max_chars=EXEC_CWD_MAX_CHARS,
            actual_chars=len(clean_cwd),
        )
    if clean_run_id and not _RUN_ID_RE.fullmatch(clean_run_id):
        return _unknown_mapped("invalid_execution_run_id")
    try:
        availability = provider.availability()
    except Exception:
        return _unavailable_mapped("availability_check_failed")
    if not isinstance(availability, ExecutionAvailability):
        return _unavailable_mapped("invalid_availability_result")
    availability_status = str(availability.status or "").strip().lower()
    if not availability.enabled or availability_status not in _READY_AVAILABILITY_STATUSES:
        return _unavailable_mapped(str(availability.reason or availability_status or "execution_unavailable"))
    try:
        run_kwargs: dict[str, Any] = {
            "owner": owner,
            "command": clean_command,
            "cwd": clean_cwd,
            "timeout_seconds": normalize_timeout_seconds(timeout_seconds),
            "initial_wait_seconds": normalize_initial_wait_seconds(initial_wait_seconds),
        }
        if clean_run_id:
            run_kwargs["run_id"] = clean_run_id
        run_start = provider.run(**run_kwargs)
    except Exception:
        return _unknown_mapped("provider_run_failed")
    return map_exec_run_outcome(run_start)


def _unavailable_mapped(reason: str) -> ExecMappedResult:
    clean_reason = str(reason or "execution_unavailable")
    return map_exec_run_outcome(ExecRunStart(status=EXEC_STATUS_UNAVAILABLE, reason=clean_reason))


def _execution_status_timing_text(*, finished_at: float, observed_at: float) -> str:
    if finished_at <= 0:
        return ""
    finished_label = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(finished_at))
    observed_label = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(observed_at))
    return f"任务完成时间={finished_label}；本次查询时间={observed_label}；"


def execute_exec_status(
    provider: ExecutionProvider,
    *,
    owner: ExecutionRunOwner,
    run_id: str,
    cursor: str | None = None,
    wait_seconds: Any = None,
) -> ExecMappedResult:
    """Read one bounded output page, optionally waiting for terminal or deadline.

    ``wait_seconds`` is intended to save model/tool rounds.  Returning as soon
    as a command prints a warning or progress line defeats that purpose: noisy
    CLIs can otherwise wake the model every few seconds while the same process
    is still running.  Keep observing the same cursor until the run reaches a
    terminal state or the requested bounded wait expires, then return all
    output accumulated from that cursor in one producer-bounded page.
    """

    clean_run_id = str(run_id or "").strip()
    clean_cursor = str(cursor or "").strip() or None
    bounded_wait_seconds = normalize_status_wait_seconds(wait_seconds)
    if (
        not isinstance(owner, ExecutionRunOwner)
        or not _RUN_ID_RE.fullmatch(clean_run_id)
        or (clean_cursor is not None and len(clean_cursor) > EXEC_CURSOR_MAX_CHARS)
    ):
        return _status_unknown_result(clean_run_id, "run_not_found")
    try:
        status_result = provider.status(owner=owner, run_id=clean_run_id, cursor=clean_cursor)
        if bounded_wait_seconds > 0:
            deadline = time.monotonic() + bounded_wait_seconds
            while (
                isinstance(status_result, ExecRunStatus)
                and status_result.run_id == clean_run_id
                and status_result.status == EXEC_STATUS_RUNNING
                and time.monotonic() < deadline
            ):
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                status_result = provider.status(owner=owner, run_id=clean_run_id, cursor=clean_cursor)
    except Exception:
        return _status_unknown_result(clean_run_id, "status_query_failed", execution_unknown=True)
    if not isinstance(status_result, ExecRunStatus) or status_result.run_id != clean_run_id:
        return _status_unknown_result(clean_run_id, "invalid_status_result", execution_unknown=True)
    status = str(status_result.status or "").strip()
    if status not in {
        EXEC_STATUS_RUNNING,
        EXEC_STATUS_COMPLETED,
        EXEC_STATUS_FAILED,
        EXEC_STATUS_TIMED_OUT,
        EXEC_STATUS_CANCELLED,
        EXEC_STATUS_UNAVAILABLE,
        EXEC_STATUS_EXECUTION_UNKNOWN,
        EXEC_STATUS_UNKNOWN,
    }:
        return _status_unknown_result(clean_run_id, "invalid_status_value", execution_unknown=True)
    status_tail = str(status_result.tail or "")
    if (
        len(status_tail.encode("utf-8")) > EXEC_STATUS_OUTPUT_MAX_BYTES
        or status_tail.count("\n") > EXEC_STATUS_OUTPUT_MAX_LINES
    ):
        return _status_unknown_result(clean_run_id, "status_output_page_too_large", execution_unknown=True)
    if status_result.next_cursor is not None and parse_cursor(status_result.next_cursor, run_id=clean_run_id) is None:
        return _status_unknown_result(clean_run_id, "invalid_status_cursor", execution_unknown=True)
    try:
        output_ref = _normalize_output_ref(status_result.output_ref, run_id=clean_run_id)
    except ValueError:
        return _status_unknown_result(clean_run_id, "invalid_status_output_ref", execution_unknown=True)
    if status == EXEC_STATUS_RUNNING and status_result.next_cursor is None:
        return _status_unknown_result(clean_run_id, "running_cursor_required", execution_unknown=True)
    if status == EXEC_STATUS_COMPLETED and status_result.exit_code != 0:
        return _status_unknown_result(clean_run_id, "completed_exit_code_invalid", execution_unknown=True)

    safe_tail = _sanitize_model_text(status_result.tail)
    safe_reason = _sanitize_model_text(str(status_result.reason or ""))
    observed_at = time.time()
    started_at = max(0.0, float(status_result.started_at or 0.0))
    finished_at = max(0.0, float(status_result.finished_at or 0.0))
    data = {
        "status": status,
        "run_id": clean_run_id,
        "exit_code": status_result.exit_code,
        "tail": safe_tail,
        "next_cursor": status_result.next_cursor,
        "output_ref": output_ref,
        "reason": safe_reason,
        "started_at": started_at or None,
        "finished_at": finished_at or None,
        "observed_at": observed_at,
    }
    event = {"type": "capability_execution_result", "tool_type": EXEC_STATUS_TOOL_NAME, "status": status}
    if status == EXEC_STATUS_RUNNING:
        output = f"\n本次增量输出：\n{safe_tail}" if safe_tail else ""
        continuation = (
            f"\n输出尚未读完，请调用 exec_status(run_id={clean_run_id}, "
            f"cursor={status_result.next_cursor}, wait_seconds=30) 等待新输出或终态。"
            if status_result.next_cursor
            else ""
        )
        output_ref_hint = f"\n完整日志引用：output_ref={output_ref}。" if output_ref else ""
        feedback = f"命令仍在执行；这是当前增量输出，不要声称已经完成。{output_ref_hint}{output}{continuation}"
        return ExecMappedResult("ok", status, safe_reason, feedback, data, event)
    if status == EXEC_STATUS_COMPLETED:
        followup = "输出尚未读完，请继续使用 next_cursor。" if status_result.next_cursor else "输出已读完。"
        output = f"\n本次增量输出：\n{safe_tail}" if safe_tail else ""
        continuation = (
            f"\n请调用 exec_status(run_id={clean_run_id}, cursor={status_result.next_cursor}) 继续读取。"
            if status_result.next_cursor
            else ""
        )
        output_ref_hint = f"\n完整日志引用：output_ref={output_ref}。" if output_ref else ""
        timing = _execution_status_timing_text(finished_at=finished_at, observed_at=observed_at)
        feedback = f"命令已完成（exit_code=0）；{timing}{output_ref_hint}{output}{continuation}\n{followup}"
        return ExecMappedResult("ok", status, safe_reason, feedback, data, event)
    if status == EXEC_STATUS_UNKNOWN:
        return _status_unknown_result(clean_run_id, safe_reason or "run_not_found")
    if status == EXEC_STATUS_EXECUTION_UNKNOWN:
        return ExecMappedResult(
            "error",
            status,
            safe_reason or "termination_unconfirmed",
            "无法确认该命令是否仍在执行；不要声称命令已完成或已停止。",
            data,
            {**event, "reason": safe_reason or "termination_unconfirmed"},
        )
    if status == EXEC_STATUS_UNAVAILABLE:
        clean_reason = safe_reason or "execution_unavailable"
        return ExecMappedResult(
            "unavailable",
            status,
            clean_reason,
            "当前无法查询该命令；不要据此声称命令已完成或已停止。",
            data,
            {**event, "reason": clean_reason},
        )
    clean_reason = safe_reason or f"execution_{status}"
    output = f"\n终止前的增量输出：\n{safe_tail}" if safe_tail else ""
    exit_detail = f"，exit_code={status_result.exit_code!r}" if status_result.exit_code is not None else ""
    if status == EXEC_STATUS_FAILED:
        feedback = (
            f"命令执行失败（status=failed{exit_detail}，reason={clean_reason}）。{output}"
            "请依据这份真实结果修正命令或如实说明失败，不要声称成功。"
        )
    elif status == EXEC_STATUS_TIMED_OUT:
        feedback = (
            f"命令执行超时（status=timed_out，reason={clean_reason}），执行器已确认进程组终止。{output}"
            "这次操作没有完成；可缩小范围或修正命令后重试，不要声称成功。"
        )
    else:
        feedback = (
            f"命令已被执行器确认取消（status=cancelled，reason={clean_reason}）。{output}"
            "这次操作没有完成，不要声称成功。"
        )
    return ExecMappedResult(
        "error",
        status,
        clean_reason,
        feedback,
        data,
        {**event, "reason": clean_reason},
    )


def _format_execution_output(*, stdout: Any = "", stderr: Any = "") -> str:
    parts: list[str] = []
    clean_stdout = str(stdout or "")
    clean_stderr = str(stderr or "")
    if clean_stdout:
        parts.append(f"\nstdout：\n{clean_stdout}")
    if clean_stderr:
        parts.append(f"\nstderr：\n{clean_stderr}")
    return "".join(parts)


def execute_exec_cancel(
    provider: ExecutionProvider,
    *,
    owner: ExecutionRunOwner,
    run_id: str,
) -> ExecMappedResult:
    """Request cancellation; only provider-confirmed termination is success."""

    clean_run_id = str(run_id or "").strip()
    if not isinstance(owner, ExecutionRunOwner) or not _RUN_ID_RE.fullmatch(clean_run_id):
        return _cancel_result(clean_run_id, ok=False, status=EXEC_STATUS_UNKNOWN, reason="run_not_found")
    try:
        result = provider.cancel(owner=owner, run_id=clean_run_id)
    except Exception:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="cancel_dispatch_failed")
    if not isinstance(result, ExecCancelResult) or result.run_id != clean_run_id:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="invalid_cancel_result")
    status = str(result.status or "").strip()
    if status not in {EXEC_STATUS_CANCELLED, "already_ended", EXEC_STATUS_UNKNOWN, "cancel_failed"}:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="invalid_cancel_status")
    if status == EXEC_STATUS_CANCELLED and not result.ok:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="cancellation_not_confirmed")
    if status == "cancel_failed" and result.ok:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="cancellation_not_confirmed")
    return _cancel_result(clean_run_id, ok=bool(result.ok), status=status, reason=str(result.reason or ""))


def _status_unknown_result(run_id: str, reason: str, *, execution_unknown: bool = False) -> ExecMappedResult:
    status = EXEC_STATUS_EXECUTION_UNKNOWN if execution_unknown else EXEC_STATUS_UNKNOWN
    visible_run_id = str(run_id or "").strip() if _RUN_ID_RE.fullmatch(str(run_id or "").strip()) else ""
    clean_reason = _sanitize_model_text(reason or "run_not_found")
    data = {
        "status": status,
        "run_id": visible_run_id,
        "exit_code": None,
        "tail": "",
        "next_cursor": None,
        "output_ref": None,
        "reason": clean_reason,
    }
    return ExecMappedResult(
        "error",
        status,
        clean_reason,
        "无法确认该命令的当前状态；不要声称命令已完成或已停止。",
        data,
        {
            "type": "capability_execution_result",
            "tool_type": EXEC_STATUS_TOOL_NAME,
            "status": status,
            "reason": clean_reason,
        },
    )


def _cancel_result(run_id: str, *, ok: bool, status: str, reason: str) -> ExecMappedResult:
    clean_run_id = str(run_id or "").strip() if _RUN_ID_RE.fullmatch(str(run_id or "").strip()) else ""
    clean_reason = _sanitize_model_text(reason or "")
    data = {"ok": ok, "status": status, "run_id": clean_run_id, "reason": clean_reason}
    if status == EXEC_STATUS_CANCELLED and ok:
        envelope, feedback = "ok", "执行器已确认命令停止。"
    elif status == "already_ended" and ok:
        envelope, feedback = "ok", "命令此前已经结束，没有再次执行取消动作。"
    elif status == EXEC_STATUS_UNKNOWN:
        envelope, feedback = "error", "找不到该命令或无权访问；不要声称命令已停止。"
    else:
        envelope, feedback = "error", "无法确认命令已经停止；不要声称取消成功。"
    return ExecMappedResult(
        envelope,
        status,
        clean_reason,
        feedback,
        data,
        {
            "type": "capability_execution_result",
            "tool_type": EXEC_CANCEL_TOOL_NAME,
            "status": status,
            "reason": clean_reason,
        },
    )
