"""Run-bound cursors, UTF-8 budgets, and pages from the store-owned output window."""

from __future__ import annotations

import hashlib

import re

import uuid

from typing import (
    Any,
)

from ..execution_specs import (
    EXEC_STATUS_RUNNING,
)

from .models import (
    _RunRecord,
)


_RUN_ID_PREFIX = "execrun_"


_RUN_ID_RE = re.compile(r"^execrun_[a-f0-9]{32}$")


_CURSOR_RE = re.compile(r"^c1\.([a-f0-9]{16})\.([a-f0-9]+)$")


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


def _normalize_output_ref(value: str | None, *, run_id: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    expected = f"runlog:{run_id}"
    if text != expected:
        raise ValueError("invalid_execution_output_ref")
    return expected


class RunOutputMixin:
    """Store methods called under its existing RLock on the same run record."""

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
