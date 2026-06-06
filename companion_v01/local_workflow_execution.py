from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .local_capability_config import normalize_workflow_asset_handle


WORKFLOW_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
WORKFLOW_STATUS_RE = re.compile(r"^[a-z0-9_.-]{1,80}$")
WORKFLOW_CONTENT_TYPE_RE = re.compile(r"^[a-z0-9.+-]{1,80}/[a-z0-9.+-]{1,80}$")
FORBIDDEN_PUBLIC_TEXT_PARTS = ("token", "secret", "password", "api_key", "://", "\\")


@dataclass(frozen=True)
class WorkflowExecutionRequest:
    job_id: str
    workflow_id: str
    capability_id: str
    profile_user_id: str
    session_id: str
    inputs: dict[str, str]
    workflow: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowExecutionResult:
    ok: bool
    status: str
    reason: str = ""
    outputs: tuple[Mapping[str, Any], ...] = ()


@runtime_checkable
class WorkflowExecutionRunner(Protocol):
    def execute_workflow(self, request: WorkflowExecutionRequest) -> WorkflowExecutionResult | Mapping[str, Any]:
        ...


WorkflowExecutionCallable = Callable[[WorkflowExecutionRequest], WorkflowExecutionResult | Mapping[str, Any]]


def call_workflow_execution_runner(
    runner: WorkflowExecutionRunner | WorkflowExecutionCallable,
    request: WorkflowExecutionRequest,
) -> dict[str, Any]:
    if hasattr(runner, "execute_workflow"):
        raw_result = runner.execute_workflow(request)  # type: ignore[attr-defined]
    else:
        raw_result = runner(request)  # type: ignore[misc]
    return normalize_workflow_execution_result(raw_result)


def normalize_workflow_execution_result(value: WorkflowExecutionResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, WorkflowExecutionResult):
        raw: Mapping[str, Any] = {
            "ok": value.ok,
            "status": value.status,
            "reason": value.reason,
            "outputs": list(value.outputs),
        }
    elif isinstance(value, Mapping):
        raw = value
    else:
        return {
            "ok": False,
            "status": "failed",
            "reason": "workflow_runner_invalid_result",
            "outputs": [],
        }

    ok = bool(raw.get("ok"))
    status = _safe_status(raw.get("status"), "completed" if ok else "failed")
    if ok and status in {"", "queued", "running", "failed", "error"}:
        status = "completed"
    if not ok and status in {"", "completed", "ready"}:
        status = "failed"
    return {
        "ok": ok,
        "status": status,
        "reason": _safe_public_reason(raw.get("reason"), "" if ok else "workflow_runner_failed"),
        "outputs": sanitize_workflow_outputs(raw.get("outputs")),
    }


def sanitize_workflow_outputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    outputs: list[dict[str, Any]] = []
    for raw_output in value:
        if not isinstance(raw_output, Mapping):
            continue
        handle = normalize_workflow_asset_handle(
            raw_output.get("handle")
            or raw_output.get("outputHandle")
            or raw_output.get("outputImageHandle")
            or raw_output.get("assetHandle")
        )
        if not handle.get("ok"):
            continue
        output = {
            "handle": handle["handle"],
            "kind": _safe_public_id(raw_output.get("kind"), "image"),
        }
        content_type = _safe_content_type(raw_output.get("contentType") or raw_output.get("mimeType"))
        if content_type:
            output["contentType"] = content_type
        outputs.append(output)
    return outputs


def _safe_public_id(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if not WORKFLOW_PUBLIC_ID_RE.match(text) or any(part in lowered for part in FORBIDDEN_PUBLIC_TEXT_PARTS):
        return fallback
    return text


def _safe_status(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    if not WORKFLOW_STATUS_RE.match(text) or any(part in text for part in FORBIDDEN_PUBLIC_TEXT_PARTS):
        return fallback
    return text


def _safe_content_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or not WORKFLOW_CONTENT_TYPE_RE.match(text):
        return ""
    if any(part in text for part in FORBIDDEN_PUBLIC_TEXT_PARTS):
        return ""
    return text


def _safe_public_reason(value: Any, fallback: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    lowered = text.lower()
    if (
        not text
        or len(text) > 160
        or any(part in lowered for part in FORBIDDEN_PUBLIC_TEXT_PARTS)
        or "/" in text
        or re.search(r"[A-Za-z]:", text)
    ):
        return fallback
    return text
