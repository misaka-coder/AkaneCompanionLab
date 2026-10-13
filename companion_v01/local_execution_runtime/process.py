"""Process spawning, confirmed cancellation, timeout watching, and reader completion."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..execution_run import ExecutionRunOwner, ExecCancelResult
from ..execution_specs import (
    EXEC_REASON_OUTPUT_OPEN_AFTER_EXIT,
    EXEC_STATUS_CANCELLED, EXEC_STATUS_COMPLETED, EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED, EXEC_STATUS_RUNNING, EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNKNOWN, EXEC_TERMINAL_STATUSES,
)


_CANCEL_CONFIRM_GRACE_SECONDS = 3.0


_OUTPUT_DRAIN_GRACE_SECONDS = 3.0


# A command that starts a detached child leaves that child holding the inherited
# stdout/stderr pipe handles. Its readers keep draining the pipe (so the child
# never blocks on a full buffer) until the child exits by itself. This bounds how
# many such pipe pairs may stay parked before the oldest ones are released.
_MAX_DETACHED_READER_PAIRS = 8


@dataclass(slots=True)
class _DetachedReaders:
    """Readers that still drain a pipe inherited by a surviving child process."""

    readers: tuple[threading.Thread, ...]
    detached_at: float

    def settled(self) -> bool:
        return not any(reader.is_alive() for reader in self.readers)

    def retire(self, event: threading.Event | None) -> None:
        if event is not None:
            event.set()


class LocalProcessMixin:
    def cancel(self, *, owner: ExecutionRunOwner, run_id: str) -> ExecCancelResult:
        if not self._owner_matches(owner):
            return ExecCancelResult(ok=True, status=EXEC_STATUS_UNKNOWN, run_id=str(run_id or ""), reason="run_not_found")
        clean_run_id = str(run_id or "")
        requested = self.store.request_cancel(clean_run_id, owner=owner)
        if requested == EXEC_STATUS_UNKNOWN:
            return ExecCancelResult(ok=True, status=EXEC_STATUS_UNKNOWN, run_id=clean_run_id, reason="run_not_found")
        record = self.store.get(clean_run_id, owner=owner)
        if record is not None and record.status == EXEC_STATUS_EXECUTION_UNKNOWN:
            return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="termination_unconfirmed")
        if requested == "already_ended":
            return ExecCancelResult(ok=True, status="already_ended", run_id=clean_run_id, reason="already_ended")

        with self._lock:
            proc = self._procs.get(clean_run_id)
        if proc is None:
            return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="process_handle_missing")

        deadline = time.monotonic() + self.cancel_confirm_grace_seconds
        while time.monotonic() < deadline:
            record = self.store.get(clean_run_id, owner=owner)
            if record is not None and record.status != EXEC_STATUS_RUNNING:
                break
            time.sleep(0.02)
        record = self.store.get(clean_run_id, owner=owner)
        if record is not None and record.status == EXEC_STATUS_CANCELLED:
            return ExecCancelResult(ok=True, status=EXEC_STATUS_CANCELLED, run_id=clean_run_id, reason="")
        if record is not None and record.status == EXEC_STATUS_EXECUTION_UNKNOWN:
            return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="termination_unconfirmed")
        if record is not None and record.status in EXEC_TERMINAL_STATUSES:
            return ExecCancelResult(ok=True, status="already_ended", run_id=clean_run_id, reason=record.status)
        return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="termination_not_confirmed")

    def _spawn(self, command: str | Sequence[str], workdir: Path, env: dict[str, str], *, interactive: bool = False) -> subprocess.Popen:
        if os.name == "nt":
            return subprocess.Popen(
                command,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                bufsize=0 if interactive else -1,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=str(workdir),
            env=env,
            stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
            bufsize=0 if interactive else -1,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

    def _kill_process_group(self, proc: subprocess.Popen) -> bool:
        if proc.poll() is not None:
            return True
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return proc.poll() is not None
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return proc.poll() is not None
        except PermissionError:
            return False
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return proc.poll() is not None

    def _watcher(self, run_id: str, owner: ExecutionRunOwner, proc: subprocess.Popen, timeout_seconds: int) -> None:
        readers = [
            threading.Thread(
                target=self._read_pipe,
                args=(run_id, owner, proc.stdout, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_pipe,
                args=(run_id, owner, proc.stderr, "stderr"),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        start = time.monotonic()
        cancellation_triggered = False
        timeout_triggered = False
        record_missing = False
        gave_up = False
        kill_attempts = 0
        try:
            while True:
                if self.store.get(run_id, owner=owner) is None:
                    record_missing = True
                    if proc.poll() is None and not self._kill_process_group(proc):
                        time.sleep(0.1)
                        continue
                    break
                exit_code = proc.poll()
                if exit_code is not None:
                    break

                if not cancellation_triggered and not timeout_triggered:
                    if self.store.cancel_requested(run_id, owner=owner):
                        cancellation_triggered = True
                        self._set_pending_reason(run_id, "cancel_termination_pending")
                    elif timeout_seconds and time.monotonic() - start >= timeout_seconds:
                        timeout_triggered = True
                        self._set_pending_reason(run_id, "timeout_termination_pending")

                if cancellation_triggered or timeout_triggered:
                    if self._kill_process_group(proc):
                        continue
                    kill_attempts += 1
                    if kill_attempts >= self.max_kill_attempts:
                        # Honest give-up: never claim cancelled/timed_out without
                        # confirmation. Record an evictable execution_unknown
                        # terminal and stop retrying.
                        gave_up = True
                        self._set_pending_reason(run_id, "termination_unconfirmed")
                        if self.store.get(run_id, owner=owner) is not None:
                            self.store.mark_terminal(
                                run_id,
                                EXEC_STATUS_EXECUTION_UNKNOWN,
                                owner=owner,
                                reason="termination_unconfirmed",
                            )
                        break
                    time.sleep(0.1)
                    continue
                time.sleep(0.02)

            pipes = (proc.stdout, proc.stderr)
            drain_ok = self._drain_readers(readers, pipes)
            capture_failed = self._capture_failed(run_id)
            log_failed = self._log_failed(run_id)
            if record_missing or gave_up:
                return
            if timeout_triggered:
                reason = "execution_timeout"
                if not drain_ok or capture_failed:
                    reason = "execution_timeout_output_incomplete"
                elif log_failed:
                    reason = "execution_timeout_output_persistence_failed"
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_TIMED_OUT,
                    owner=owner,
                    exit_code=exit_code,
                    reason=reason,
                )
            elif cancellation_triggered:
                if self.store.confirm_cancelled(run_id, owner=owner) and (not drain_ok or capture_failed):
                    # Cancellation is still confirmed, but do not hide a partial transcript.
                    self._set_pending_reason(run_id, "cancelled_output_incomplete")
            elif capture_failed:
                # A real capture failure stays a failure: this transcript cannot
                # be trusted, whatever the process exit code says.
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=exit_code,
                    reason="output_capture_incomplete",
                )
            elif not drain_ok:
                # The command's own process has ended while a surviving child
                # still holds its inherited stdout/stderr handles, so the readers
                # cannot reach end-of-stream on their own. Report the command's
                # real result and the still-open stream as two separate facts
                # instead of turning a finished command into a capture failure.
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_COMPLETED if exit_code == 0 else EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=exit_code,
                    reason="output_persistence_failed" if log_failed else EXEC_REASON_OUTPUT_OPEN_AFTER_EXIT,
                )
            else:
                reason = "output_persistence_failed" if log_failed else ""
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_COMPLETED if exit_code == 0 else EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=exit_code,
                    reason=reason,
                )
        except Exception as exc:
            terminated = self._kill_process_group(proc)
            self._drain_readers(readers, (proc.stdout, proc.stderr))
            if terminated:
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=proc.poll(),
                    reason=f"execution_watcher_failed:{type(exc).__name__}",
                )
            else:
                self._set_pending_reason(run_id, "watcher_failed_termination_not_confirmed")
        finally:
            if proc.poll() is not None or gave_up:
                self._cleanup(run_id)
                # Every terminal branch owns its remaining readers, including
                # failed capture, cancellation and unconfirmed termination.
                self._detach_readers(run_id, readers)

    def _drain_readers(self, readers: Sequence[threading.Thread], pipes: Sequence[Any]) -> bool:
        # Never close the pipes from here: a reader blocked in pipe.read() (the
        # process is still alive, e.g. after an unconfirmed kill) would keep the
        # close() blocked on Windows and deadlock the watcher. Readers close
        # their own pipe in their finally once read() hits EOF.
        del pipes
        deadline = time.monotonic() + self.output_drain_grace_seconds
        for reader in readers:
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        return not any(reader.is_alive() for reader in readers)

    def _detach_readers(self, run_id: str, readers: Sequence[threading.Thread]) -> None:
        """Hand a finished run's open output pipes to the bounded drainer registry.

        The command is over and its own output has been captured, so these
        readers no longer belong to the run. They keep draining the pipe a
        surviving child inherited, and the registry makes sure those readers
        retire instead of accumulating for the child's whole lifetime.
        """

        entry = _DetachedReaders(
            readers=tuple(readers),
            detached_at=time.monotonic(),
        )
        with self._lock:
            if not self._open_streams.get(run_id):
                return
            self._detached_readers[run_id] = entry
            if self._shutdown_requested.is_set():
                entry.retire(self._reader_retire.get(run_id))
        self._reap_detached_readers()

    def _reap_detached_readers(self, *, force: bool = False) -> int:
        """Drop finished parked pairs and ask the rest to retire when told to.

        A parked reader closes its own pipe, so releasing a pair only means
        setting its retire flag; each reader enforces the bound and the retention
        window itself. Never close these pipes from here: closing a pipe another
        thread is reading can block until the writer exits, which would stall the
        watcher that just finished a different run.
        """

        with self._lock:
            settled: list[str] = []
            retiring: list[tuple[str, _DetachedReaders]] = []
            for run_id, entry in self._detached_readers.items():
                if entry.settled():
                    settled.append(run_id)
                elif force:
                    retiring.append((run_id, entry))
            events = {run_id: self._reader_retire.get(run_id) for run_id, _entry in retiring}
            # Signalled readers remain tracked until they close their streams.
            for run_id in settled:
                self._detached_readers.pop(run_id, None)
        for run_id, entry in retiring:
            entry.retire(events.get(run_id))
        return len(retiring) + len(settled)
