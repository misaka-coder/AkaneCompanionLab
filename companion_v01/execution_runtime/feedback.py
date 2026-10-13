"""Model-facing redaction, execution markers, and rejected/unknown receipts."""

from __future__ import annotations

import re

from typing import (
    Any,
)

from ..execution_specs import (
    EXEC_RUN_TOOL_NAME,
    EXEC_STATUS_TOOL_NAME,
    EXEC_CANCEL_TOOL_NAME,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_UNKNOWN,
)

from .models import (
    ExecRunStart,
    ExecMappedResult,
)

from .output import (
    _RUN_ID_RE,
)


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
        model_feedback=f"[command status: unknown; reason={clean_reason}]",
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
    }
    if clean_reason == "command_required":
        feedback = "这次命令没有执行：command_required。请提供非空 command。"
    elif clean_reason == "cwd_too_long":
        feedback = (
            f"这次命令没有执行：cwd_too_long（max_chars={max_chars}，actual_chars={actual_chars}）。"
            "请使用短的工作区相对路径或挂载别名。"
        )
    else:
        feedback = f"[command status: rejected; reason={clean_reason}]"
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


_SILENT_RUNNING_HINT = (
    "[运行中，暂无新输出不代表停滞；不要据此取消或重复启动。]"
)


_SILENT_COMPLETED_HINT = (
    "[命令已结束，本页无输出；退出码 0 不证明文件存在、不存在或安装可用。"
    "先核验目标文件大小/完整性及程序可用性，再报告任务结果。]"
)


_OPEN_OUTPUT_MARKER = (
    "[output still open: the command exited but inherited stdout/stderr remain open; "
    "later child output is not part of this run.]"
)


def _format_execution_output(*, stdout: Any = "", stderr: Any = "") -> str:
    parts: list[str] = []
    clean_stdout = str(stdout or "")
    clean_stderr = str(stderr or "")
    if clean_stdout:
        parts.append(clean_stdout.rstrip("\n"))
    if clean_stderr:
        stderr_body = clean_stderr.rstrip("\r\n")
        parts.append(f"[stderr]\n{stderr_body}")
    return "\n".join(parts) if parts else "(no output)"


def _append_execution_markers(output: str, markers: list[str]) -> str:
    body = str(output or "").rstrip("\n") or "(no output)"
    clean_markers = [str(marker or "").strip() for marker in markers if str(marker or "").strip()]
    return body if not clean_markers else f"{body}\n" + "\n".join(clean_markers)


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
        f"[command status: {status}; reason={clean_reason}]",
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
        envelope, feedback = "ok", "[cancelled]"
    elif status == "already_ended" and ok:
        envelope, feedback = "ok", "[already ended; no cancel action taken]"
    elif status == EXEC_STATUS_UNKNOWN:
        envelope, feedback = "error", f"[cancel status: unknown; reason={clean_reason or 'run_not_found'}]"
    else:
        envelope, feedback = "error", f"[cancel status: unconfirmed; reason={clean_reason or 'cancel_failed'}]"
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
