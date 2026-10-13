"""Execution tool boundaries: request validation, provider dispatch, and bounded polling."""

from __future__ import annotations

import time

from typing import (
    Any,
)

from ..execution_specs import (
    EXEC_CANCEL_RESULT_STATUSES,
    EXEC_CWD_MAX_CHARS,
    EXEC_CURSOR_MAX_CHARS,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_UNKNOWN,
    normalize_initial_wait_seconds,
    normalize_status_wait_seconds,
    normalize_timeout_seconds,
)

from .models import (
    ExecutionRunOwner,
    ExecutionAvailability,
    ExecRunStart,
    ExecRunStatus,
    ExecCancelResult,
    ExecMappedResult,
    ExecutionProvider,
)

from .output import (
    _RUN_ID_RE,
)

from .feedback import (
    _unknown_mapped,
    _rejected_mapped,
    _status_unknown_result,
    _cancel_result,
)

from .mapping import (
    _map_exec_status_outcome,
    map_exec_run_outcome,
    _unavailable_mapped,
)


_READY_AVAILABILITY_STATUSES = frozenset({"ready", "available", "degraded", "ok"})


def execute_exec_run(
    provider: ExecutionProvider,
    *,
    owner: ExecutionRunOwner,
    command: str,
    cwd: str = "",
    timeout_seconds: Any = None,
    initial_wait_seconds: Any = None,
    run_id: str = "",
    interactive: bool = False,
    background: bool = False,
) -> ExecMappedResult:
    clean_command = str(command or "").strip()
    clean_cwd = str(cwd or "").strip()
    clean_run_id = str(run_id or "").strip()
    if not isinstance(owner, ExecutionRunOwner):
        return _unknown_mapped("execution_owner_required")
    if not clean_command:
        return _rejected_mapped("command_required")
    try:
        execution_timeout = normalize_timeout_seconds(timeout_seconds)
    except ValueError:
        return _rejected_mapped("invalid_execution_timeout")
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
            "timeout_seconds": execution_timeout,
            "initial_wait_seconds": normalize_initial_wait_seconds(initial_wait_seconds),
        }
        if clean_run_id:
            run_kwargs["run_id"] = clean_run_id
        if background:
            run_kwargs["background"] = True
        if interactive:
            if not callable(getattr(provider, "input", None)):
                return _unavailable_mapped("interactive_input_not_supported")
            run_kwargs["interactive"] = True
        run_start = provider.run(**run_kwargs)
    except Exception:
        return _unknown_mapped("provider_run_failed")
    if clean_run_id and isinstance(run_start, ExecRunStart):
        returned_run_id = str(run_start.run_id or "").strip()
        if returned_run_id and returned_run_id != clean_run_id:
            # Keep the host-assigned identity; none of the other run's evidence is ours.
            return _unknown_mapped(
                "execution_run_id_mismatch",
                run_start=ExecRunStart(status=EXEC_STATUS_EXECUTION_UNKNOWN, run_id=clean_run_id),
            )
    return map_exec_run_outcome(run_start)


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
    return _map_exec_status_outcome(status_result, run_id=clean_run_id)


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
    if status not in EXEC_CANCEL_RESULT_STATUSES:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="invalid_cancel_status")
    if status == EXEC_STATUS_CANCELLED and not result.ok:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="cancellation_not_confirmed")
    if status == "cancel_failed" and result.ok:
        return _cancel_result(clean_run_id, ok=False, status="cancel_failed", reason="cancellation_not_confirmed")
    return _cancel_result(clean_run_id, ok=bool(result.ok), status=status, reason=str(result.reason or ""))
