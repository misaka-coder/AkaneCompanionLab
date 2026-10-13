"""Ordered output capture, credential redaction, persisted pages, and private log ownership."""
from __future__ import annotations

import codecs
import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

if os.name == "nt":
    import ctypes
    import msvcrt

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.PeekNamedPipe.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    )
    _KERNEL32.PeekNamedPipe.restype = ctypes.c_int
else:
    import select

from ..execution_run import (
    ExecutionRunOwner, ExecRunStart, ExecRunStatus, make_cursor, parse_cursor,
)
from ..execution_specs import EXEC_STATUS_RUNNING, EXEC_STATUS_UNKNOWN


_RUN_LOG_RE = re.compile(r"^execrun_[a-f0-9]{32}\.log$")


_READ_CHUNK_BYTES = 4096


# A command that starts a detached child leaves the inherited stdout/stderr
# handles open after its own process exits. A blocking read would park a reader on
# that handle for the child's whole lifetime, so readers poll for data instead.
# That keeps real end-of-stream detection, lets a reader retire and close its own
# pipe without another thread closing a handle it is reading, and never spins
# while the pipe is quiet.
_READER_IDLE_SECONDS = 0.02
_DETACHED_READER_IDLE_SECONDS = 0.2


_CREDENTIAL_OUTPUT_MARKER = "[credential value hidden]"


def _pipe_data_waiting(pipe: Any) -> bool | None:
    """Whether a raw read can complete now, or None once the pipe is closed.

    A broken Windows pipe is EOF; other probe errors must reach the reader's
    capture-failure handler. POSIX readiness covers pending data and EOF.
    """

    if os.name != "nt":
        ready, _, _ = select.select([pipe], [], [], 0.0)
        return bool(ready)
    available = ctypes.c_uint32(0)
    ok = _KERNEL32.PeekNamedPipe(
        ctypes.c_void_p(msvcrt.get_osfhandle(pipe.fileno())),
        None,
        0,
        None,
        ctypes.byref(available),
        None,
    )
    if ok:
        return bool(available.value)
    error = ctypes.get_last_error()
    if error == 109:  # ERROR_BROKEN_PIPE: all inherited write handles closed.
        return None
    raise ctypes.WinError(error)


class _SecretStreamRedactor:
    """Redact exact configured values without losing chunk-boundary matches."""

    def __init__(self, values: Sequence[str]) -> None:
        self.values = tuple(sorted({str(value) for value in values if str(value)}, key=len, reverse=True))
        self.pattern = re.compile("|".join(re.escape(value) for value in self.values)) if self.values else None
        self.buffer = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        if not self.values:
            return str(text or "")
        self.buffer += str(text or "")
        if final:
            ready, self.buffer = self.buffer, ""
            return self._redact(ready)
        keep = 0
        for value in self.values:
            max_prefix = min(len(value) - 1, len(self.buffer))
            for size in range(max_prefix, keep, -1):
                if self.buffer.endswith(value[:size]):
                    keep = size
                    break
        cut = len(self.buffer) - keep
        ready, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return self._redact(ready)

    def _redact(self, text: str) -> str:
        return self.pattern.sub(_CREDENTIAL_OUTPUT_MARKER, text) if self.pattern is not None else text


def _unknown_status(run_id: str) -> ExecRunStatus:
    return ExecRunStatus(status=EXEC_STATUS_UNKNOWN, run_id=str(run_id or ""), reason="run_not_found")


def _bounded_utf8_prefix(data: bytes, *, max_bytes: int, max_lines: int) -> int:
    end = min(len(data), max(1, int(max_bytes)))
    while end > 0 and end < len(data) and data[end] & 0xC0 == 0x80:
        end -= 1
    bounded = data[:end]
    position = -1
    for _ in range(max(1, int(max_lines))):
        position = bounded.find(b"\n", position + 1)
        if position < 0:
            return end
    next_newline = bounded.find(b"\n", position + 1)
    return end if next_newline < 0 else position + 1


class LocalOutputMixin:
    def status(
        self,
        *,
        owner: ExecutionRunOwner,
        run_id: str,
        cursor: str | None = None,
    ) -> ExecRunStatus:
        if not self._owner_matches(owner):
            return _unknown_status(run_id)
        result = self.store.read(run_id, cursor, owner=owner)
        record = self.store.get(run_id, owner=owner)
        if record is None:
            return result
        supplied = str(cursor or "").strip()
        parsed = parse_cursor(supplied, run_id=record.run_id) if supplied else None
        should_reload = bool(
            record.output_ref
            and run_id not in self._unusable_logs
            and (
                (not supplied and record.window_start_bytes > 0)
                or (parsed is not None and parsed < record.window_start_bytes)
            )
        )
        if should_reload:
            reloaded = self._persisted_status(record, 0 if parsed is None else parsed)
            if reloaded is not None:
                return reloaded
        return self._decorate_status(result)

    def _window(self, run_id: str, owner: ExecutionRunOwner) -> ExecRunStart | None:
        result = self.store.window_snapshot(run_id, owner=owner)
        record = self.store.get(run_id, owner=owner)
        if result is None or record is None:
            return result
        if record.window_start_bytes > 0 and record.output_ref and run_id not in self._unusable_logs:
            page = self._read_persisted_page(
                run_id,
                offset=0,
                absolute_end=record.absolute_end_bytes,
                max_bytes=self.store.initial_output_bytes,
                max_lines=self.store.initial_output_lines,
            )
            if page is not None:
                text, page_end = page
                return ExecRunStart(
                    status=record.status,
                    run_id=record.run_id,
                    exit_code=record.exit_code,
                    stdout=text,
                    stderr="",
                    next_cursor=(
                        make_cursor(record.run_id, page_end)
                        if page_end < record.absolute_end_bytes or record.status == EXEC_STATUS_RUNNING
                        else None
                    ),
                    output_ref=record.output_ref,
                    reason="output_reloaded",
                )
        reason = self._visible_reason(run_id, result.reason)
        output_ref = None if run_id in self._unusable_logs else result.output_ref
        return replace(result, reason=reason, output_ref=output_ref)

    def _open_run_log(self, run_id: str) -> str | None:
        try:
            handle = open(self._run_log_path(run_id), "xb")
        except OSError:
            return None
        with self._lock:
            self._logs[run_id] = handle
        return f"runlog:{run_id}"

    def _read_pipe(self, run_id: str, owner: ExecutionRunOwner, pipe: Any, stream: str) -> None:
        # Decode with an incremental UTF-8 decoder so a multi-byte character
        # split across two read chunks is never replaced by U+FFFD (the decoder
        # buffers incomplete sequences internally). The store and the run log
        # must keep the same byte stream, otherwise cursor offsets and the
        # persisted output_ref diverge from the visible text.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._open_reader_stream(run_id, stream)
        with self._lock:
            redactor = _SecretStreamRedactor(self._run_secret_values.get(run_id, ()))
        # BufferedReader.read(n) can wait for n bytes or EOF. Use one underlying
        # read so flushed progress is visible while the process is still running,
        # and read only once the pipe reports data: a command that starts a
        # detached child leaves the inherited write end open, so a blocking read
        # would park this reader for the child's whole lifetime.
        read_chunk = getattr(pipe, "read1", pipe.read)
        try:
            while True:
                if self._reader_should_retire(run_id):
                    break
                waiting = _pipe_data_waiting(pipe)
                if waiting is None:
                    break
                if not waiting:
                    time.sleep(self._reader_idle_seconds(run_id))
                    continue
                chunk = read_chunk(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                text = decoder.decode(chunk, final=False)
                if text:
                    visible = redactor.feed(text)
                    if visible:
                        self._record_output(run_id, owner, stream, visible)
            tail = redactor.feed(decoder.decode(b"", final=True), final=True)
            if tail:
                self._record_output(run_id, owner, stream, tail)
        except Exception:
            with self._lock:
                self._capture_failures.add(run_id)
        finally:
            # Only this reader ever closes its own pipe. Closing a pipe another
            # thread is reading can block until the writer exits, which is why a
            # parked reader retires itself instead of being closed from outside.
            self._close_reader_stream(run_id, stream, pipe)

    def _open_reader_stream(self, run_id: str, stream: str) -> None:
        """Register one output stream and its retire signal."""

        with self._lock:
            if run_id not in self._reader_retire:
                self._reader_retire[run_id] = threading.Event()
            self._open_streams.setdefault(run_id, set()).add(stream)

    def _close_reader_stream(self, run_id: str, stream: str, pipe: Any) -> None:
        """Close one reader's own pipe and drop its per-run reader state."""

        try:
            pipe.close()
        except Exception:
            pass
        with self._lock:
            streams = self._open_streams.get(run_id)
            if streams is None:
                return
            streams.discard(stream)
            if streams:
                return
            self._open_streams.pop(run_id, None)
            self._reader_retire.pop(run_id, None)
            # Both readers left their read loop, so this pipe pair is drained.
            self._detached_readers.pop(run_id, None)
            if run_id not in self._procs:
                self._capture_failures.discard(run_id)

    def _reader_should_retire(self, run_id: str) -> bool:
        """Whether a reader whose run already ended must stop draining its pipe.

        Only a run that has been reported terminal can be retired, so a live
        command never loses output. Retirement covers a requested shutdown, the
        parked-pair bound (oldest first) and the run retention window.
        """

        with self._lock:
            event = self._reader_retire.get(run_id)
            if event is None:
                return False
            if event.is_set():
                return True
            entry = self._detached_readers.get(run_id)
            if entry is None:
                # The run is still live: its reader keeps the pipe until EOF.
                return False
            older = sum(
                1 for other in self._detached_readers.values() if other.detached_at < entry.detached_at
            )
            parked = len(self._detached_readers)
        if entry.detached_at <= time.monotonic() - self.store.retention_seconds:
            return True
        return older < parked - self.detached_reader_limit

    def _reader_idle_seconds(self, run_id: str) -> float:
        """Idle wait between polls: short while output can still be expected."""

        with self._lock:
            detached = run_id in self._detached_readers
        return _DETACHED_READER_IDLE_SECONDS if detached else _READER_IDLE_SECONDS

    def _record_output(self, run_id: str, owner: ExecutionRunOwner, stream: str, text: str) -> None:
        data = text.encode("utf-8")
        with self._lock:
            try:
                # Store and file writes share one ordering lock, so a cursor's
                # absolute byte offset means the same thing in both places.
                self.store.append_output(run_id, stream, text, owner=owner)
            except Exception:
                self._capture_failures.add(run_id)
                return
            handle = self._logs.get(run_id)
            if handle is None or run_id in self._unusable_logs:
                return
            try:
                written = handle.write(data)
                handle.flush()
                if written != len(data):
                    raise OSError("short execution log write")
            except Exception:
                self._unusable_logs.add(run_id)
                self._logs.pop(run_id, None)
                try:
                    handle.close()
                except Exception:
                    pass

    def _persisted_status(self, record: Any, offset: int) -> ExecRunStatus | None:
        page = self._read_persisted_page(
            record.run_id,
            offset=offset,
            absolute_end=record.absolute_end_bytes,
            max_bytes=self.store.status_output_bytes,
            max_lines=self.store.status_output_lines,
        )
        if page is None:
            return None
        text, page_end = page
        return ExecRunStatus(
            status=record.status,
            run_id=record.run_id,
            exit_code=record.exit_code,
            tail=text,
            next_cursor=(
                make_cursor(record.run_id, page_end)
                if page_end < record.absolute_end_bytes or record.status == EXEC_STATUS_RUNNING
                else None
            ),
            output_ref=record.output_ref,
            reason="output_reloaded",
            started_at=float(getattr(record, "created_at", 0.0) or 0.0),
            finished_at=float(getattr(record, "finished_at", 0.0) or 0.0),
        )

    def _read_persisted_page(
        self,
        run_id: str,
        *,
        offset: int,
        absolute_end: int,
        max_bytes: int,
        max_lines: int,
    ) -> tuple[str, int] | None:
        if offset < 0 or offset > absolute_end:
            return None
        path = self._run_log_path(run_id)
        try:
            with self._lock:
                handle = self._logs.get(run_id)
                if handle is not None:
                    handle.flush()
                with open(path, "rb") as source:
                    size = min(os.fstat(source.fileno()).st_size, absolute_end)
                    if offset > size:
                        return None
                    source.seek(offset)
                    data = source.read(min(max_bytes + 4, size - offset))
        except OSError:
            return None
        if data and data[0] & 0xC0 == 0x80:
            return None
        take = _bounded_utf8_prefix(data, max_bytes=max_bytes, max_lines=max_lines)
        page = data[:take]
        try:
            text = page.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        return text, offset + len(page)

    def _decorate_status(self, result: ExecRunStatus) -> ExecRunStatus:
        return replace(
            result,
            reason=self._visible_reason(result.run_id, result.reason),
            output_ref=None if result.run_id in self._unusable_logs else result.output_ref,
        )

    def _visible_reason(self, run_id: str, fallback: str) -> str:
        with self._lock:
            pending = self._pending_reasons.get(run_id, "")
            unusable = run_id in self._unusable_logs
        if pending:
            return pending
        if unusable:
            return "output_compacted_without_persistence" if fallback == "output_compacted" else "output_persistence_failed"
        return fallback

    def _set_pending_reason(self, run_id: str, reason: str) -> None:
        with self._lock:
            self._pending_reasons[run_id] = reason

    def _capture_failed(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._capture_failures

    def _log_failed(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._unusable_logs

    def _run_log_path(self, run_id: str) -> Path:
        return self.run_log_dir / f"{run_id}.log"

    def _discard_unregistered_log(self, run_id: str) -> None:
        with self._lock:
            handle = self._logs.pop(run_id, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        try:
            self._run_log_path(run_id).unlink(missing_ok=True)
        except OSError:
            pass

    def prune_run_logs(self) -> int:
        """Delete expired executor-owned log files; active logs are never touched."""

        cutoff = time.time() - self.run_log_retention_seconds
        removed = 0
        with self._lock:
            active = set(self._logs)
        try:
            candidates = list(self.run_log_dir.iterdir())
        except OSError:
            return 0
        for path in candidates:
            if path.stem in active or not _RUN_LOG_RE.fullmatch(path.name):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    run_id = path.stem
                    with self._lock:
                        self._unusable_logs.discard(run_id)
                    removed += 1
            except OSError:
                continue
        return removed
