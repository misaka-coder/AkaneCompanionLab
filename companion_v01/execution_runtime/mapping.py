"""Validate provider snapshots and map command outcomes into bounded receipts."""

from __future__ import annotations

import math
import time

from typing import (
    Any,
)

from ..execution_specs import (
    EXEC_RUN_INNER_STATUSES,
    EXEC_REASON_OUTPUT_OPEN_AFTER_EXIT,
    EXEC_STATUS_QUERY_STATUSES,
    EXEC_STATUS_TOOL_NAME,
    EXEC_STATUS_OUTPUT_MAX_BYTES,
    EXEC_STATUS_OUTPUT_MAX_LINES,
    EXEC_STATUS_UNKNOWN,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_INITIAL_OUTPUT_MAX_BYTES,
    EXEC_INITIAL_OUTPUT_MAX_LINES,
    EXEC_RUN_TOOL_NAME,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNAVAILABLE,
)

from .models import (
    ExecRunStatus,
    ExecRunStart,
    ExecMappedResult,
)

from .output import (
    _RUN_ID_RE,
    parse_cursor,
    _normalize_output_ref,
)

from .feedback import (
    _status_unknown_result,
    _OPEN_OUTPUT_MARKER,
    _sanitize_model_text,
    _unknown_mapped,
    _SILENT_RUNNING_HINT,
    _SILENT_COMPLETED_HINT,
    _format_execution_output,
    _append_execution_markers,
)


def map_exec_run_outcome(run_start: ExecRunStart) -> ExecMappedResult:
    if not isinstance(run_start, ExecRunStart):
        return _unknown_mapped("invalid_provider_result")
    status = str(run_start.status or "").strip()
    run_id = str(run_start.run_id or "").strip()
    reason = _sanitize_model_text(str(run_start.reason or ""))
    if status not in EXEC_RUN_INNER_STATUSES:
        return _unknown_mapped("invalid_execution_status", run_start=run_start)
    # A command can fail before the provider allocates a run (for example, cwd resolution).
    pre_start_failure = status == EXEC_STATUS_FAILED and not run_id
    if status != EXEC_STATUS_UNAVAILABLE and not pre_start_failure and not _RUN_ID_RE.fullmatch(run_id):
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
    timestamps = _normalize_execution_timestamps(run_start.started_at, run_start.finished_at)
    if timestamps is None:
        return _unknown_mapped("invalid_execution_timestamps", run_start=run_start)
    started_at, finished_at = timestamps

    data: dict[str, Any] = {
        "status": status,
        "run_id": run_id,
        "exit_code": run_start.exit_code,
        "stdout": _sanitize_model_text(run_start.stdout),
        "stderr": _sanitize_model_text(run_start.stderr),
        "next_cursor": run_start.next_cursor,
        "output_ref": output_ref,
        "reason": reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "observed_at": time.time(),
    }
    event = {"type": "capability_execution_result", "tool_type": EXEC_RUN_TOOL_NAME, "status": status}

    if status == EXEC_STATUS_COMPLETED:
        output = _format_execution_output(stdout=data["stdout"], stderr=data["stderr"])
        markers = [f"[exit code: 0; run_id={run_id}]"]
        if not (data["stdout"].strip() or data["stderr"].strip()):
            markers.append(_SILENT_COMPLETED_HINT)
        if reason == "output_compacted":
            markers.append(f"[output compacted; output_ref={output_ref or 'unavailable'}]")
        if reason == EXEC_REASON_OUTPUT_OPEN_AFTER_EXIT:
            markers.append(_OPEN_OUTPUT_MARKER)
        if run_start.next_cursor:
            markers.append(
                f"[output continues: exec_status(run_id={run_id}, cursor={run_start.next_cursor})]"
            )
        feedback = _append_execution_markers(output, markers)
        return ExecMappedResult("ok", status, reason, feedback, data, event)
    if status == EXEC_STATUS_RUNNING:
        output = _format_execution_output(stdout=data["stdout"], stderr=data["stderr"])
        marker = f"[running: run_id={run_id}; cursor={run_start.next_cursor}"
        if output_ref:
            marker += f"; output_ref={output_ref}"
        marker += "]"
        feedback = _append_execution_markers(
            output,
            [
                marker,
                *([_SILENT_RUNNING_HINT] if not (data["stdout"].strip() or data["stderr"].strip()) else []),
                f"[progress: exec_status(run_id={run_id}, cursor={run_start.next_cursor}); cancel: exec_cancel(run_id={run_id})]",
            ],
        )
        return ExecMappedResult("ok", status, reason, feedback, data, event)
    if status == EXEC_STATUS_EXECUTION_UNKNOWN:
        clean_reason = reason or "termination_unconfirmed"
        feedback = _append_execution_markers(
            _format_execution_output(stdout=data["stdout"], stderr=data["stderr"]),
            [f"[command status: unknown; reason={clean_reason}]"],
        )
        return ExecMappedResult(
            "error", status, clean_reason, feedback,
            {**data, "reason": clean_reason}, {**event, "reason": clean_reason},
        )
    if status == EXEC_STATUS_UNAVAILABLE:
        clean_reason = reason or "execution_unavailable"
        event = {**event, "reason": clean_reason}
        feedback = f"[command status: unavailable; reason={clean_reason}]"
        return ExecMappedResult("unavailable", status, clean_reason, feedback, data, event)

    default_reasons = {
        EXEC_STATUS_FAILED: "execution_failed",
        EXEC_STATUS_TIMED_OUT: "execution_timed_out",
        EXEC_STATUS_CANCELLED: "execution_cancelled",
    }
    clean_reason = reason or default_reasons[status]
    if status == EXEC_STATUS_FAILED:
        marker = (
            f"[command failed to start; reason={clean_reason}]"
            if pre_start_failure
            else f"[exit code: {run_start.exit_code!r}; reason={clean_reason}]"
        )
        feedback = _append_execution_markers(
            _format_execution_output(stdout=data["stdout"], stderr=data["stderr"]),
            [marker],
        )
    elif status == EXEC_STATUS_TIMED_OUT:
        feedback = _append_execution_markers(
            _format_execution_output(stdout=data["stdout"], stderr=data["stderr"]),
            [f"[timed out; reason={clean_reason}; process_group=terminated]"],
        )
    else:
        feedback = _append_execution_markers(
            _format_execution_output(stdout=data["stdout"], stderr=data["stderr"]),
            [f"[cancelled; reason={clean_reason}]"],
        )
    return ExecMappedResult("error", status, clean_reason, feedback, data, {**event, "reason": clean_reason})


def _unavailable_mapped(reason: str) -> ExecMappedResult:
    clean_reason = str(reason or "execution_unavailable")
    return map_exec_run_outcome(ExecRunStart(status=EXEC_STATUS_UNAVAILABLE, reason=clean_reason))


def _normalize_execution_timestamps(started_at: Any, finished_at: Any) -> tuple[float | None, float | None] | None:
    """Preserve numeric/zero semantics, rejecting values that cannot form JSON receipts."""

    try:
        start = float(started_at or 0.0)
        finish = float(finished_at or 0.0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(start) and math.isfinite(finish)):
        return None
    return max(0.0, start) or None, max(0.0, finish) or None


def _map_exec_status_outcome(status_result: Any, *, run_id: str) -> ExecMappedResult:
    """Validate one provider status page after dispatch and optional polling."""

    clean_run_id = run_id
    if not isinstance(status_result, ExecRunStatus) or status_result.run_id != clean_run_id:
        return _status_unknown_result(clean_run_id, "invalid_status_result", execution_unknown=True)
    status = str(status_result.status or "").strip()
    if status not in EXEC_STATUS_QUERY_STATUSES:
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
    timestamps = _normalize_execution_timestamps(status_result.started_at, status_result.finished_at)
    if timestamps is None:
        return _status_unknown_result(clean_run_id, "invalid_status_timestamps", execution_unknown=True)
    started_at, finished_at = timestamps

    safe_tail = _sanitize_model_text(status_result.tail)
    safe_reason = _sanitize_model_text(str(status_result.reason or ""))
    observed_at = time.time()
    data = {
        "status": status,
        "run_id": clean_run_id,
        "exit_code": status_result.exit_code,
        "tail": safe_tail,
        "next_cursor": status_result.next_cursor,
        "output_ref": output_ref,
        "reason": safe_reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "observed_at": observed_at,
    }
    event = {"type": "capability_execution_result", "tool_type": EXEC_STATUS_TOOL_NAME, "status": status}
    if status == EXEC_STATUS_RUNNING:
        marker = f"[running: run_id={clean_run_id}; cursor={status_result.next_cursor}"
        if output_ref:
            marker += f"; output_ref={output_ref}"
        marker += "]"
        feedback = _append_execution_markers(
            safe_tail,
            [
                marker,
                *([_SILENT_RUNNING_HINT] if not safe_tail.strip() else []),
                f"[progress: exec_status(run_id={clean_run_id}, cursor={status_result.next_cursor}); cancel: exec_cancel(run_id={clean_run_id})]",
            ],
        )
        return ExecMappedResult("ok", status, safe_reason, feedback, data, event)
    if status == EXEC_STATUS_COMPLETED:
        markers = [f"[exit code: 0; run_id={clean_run_id}]"]
        if not safe_tail.strip():
            markers.append(_SILENT_COMPLETED_HINT)
        if safe_reason == EXEC_REASON_OUTPUT_OPEN_AFTER_EXIT:
            markers.append(_OPEN_OUTPUT_MARKER)
        if status_result.next_cursor:
            markers.append(
                f"[output continues: exec_status(run_id={clean_run_id}, cursor={status_result.next_cursor})]"
            )
        feedback = _append_execution_markers(safe_tail, markers)
        return ExecMappedResult("ok", status, safe_reason, feedback, data, event)
    if status == EXEC_STATUS_UNKNOWN:
        return _status_unknown_result(clean_run_id, safe_reason or "run_not_found")
    if status == EXEC_STATUS_EXECUTION_UNKNOWN:
        return ExecMappedResult(
            "error",
            status,
            safe_reason or "termination_unconfirmed",
            f"[command status: unknown; reason={safe_reason or 'termination_unconfirmed'}]",
            data,
            {**event, "reason": safe_reason or "termination_unconfirmed"},
        )
    if status == EXEC_STATUS_UNAVAILABLE:
        clean_reason = safe_reason or "execution_unavailable"
        return ExecMappedResult(
            "unavailable",
            status,
            clean_reason,
            f"[command status: unavailable; reason={clean_reason}]",
            data,
            {**event, "reason": clean_reason},
        )
    clean_reason = safe_reason or f"execution_{status}"
    if status == EXEC_STATUS_FAILED:
        feedback = _append_execution_markers(
            safe_tail,
            [f"[exit code: {status_result.exit_code!r}; reason={clean_reason}]"],
        )
    elif status == EXEC_STATUS_TIMED_OUT:
        feedback = _append_execution_markers(
            safe_tail,
            [f"[timed out; reason={clean_reason}; process_group=terminated]"],
        )
    else:
        feedback = _append_execution_markers(
            safe_tail,
            [f"[cancelled; reason={clean_reason}]"],
        )
    return ExecMappedResult(
        "error",
        status,
        clean_reason,
        feedback,
        data,
        {**event, "reason": clean_reason},
    )
