from __future__ import annotations

import re
import base64
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .local_capability_config import normalize_workflow_asset_handle


MAX_WORKFLOW_IMAGE_BYTES = 20 * 1024 * 1024
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
    input_assets: dict[str, "WorkflowExecutionAsset"] = field(default_factory=dict)
    workflow: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowExecutionAsset:
    handle: str
    data: bytes
    content_type: str


@dataclass(frozen=True)
class WorkflowExecutionResult:
    ok: bool
    status: str
    reason: str = ""
    outputs: tuple[Mapping[str, Any], ...] = ()
    output_assets: tuple[WorkflowExecutionAsset, ...] = ()


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
            "outputAssets": list(value.output_assets),
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
        "outputAssets": sanitize_workflow_asset_list(raw.get("outputAssets") or raw.get("output_assets")),
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


def sanitize_workflow_asset_list(value: Any) -> list[WorkflowExecutionAsset]:
    if not isinstance(value, (list, tuple)):
        return []
    assets: list[WorkflowExecutionAsset] = []
    for raw_asset in value:
        asset = normalize_workflow_asset(raw_asset)
        if asset is not None:
            assets.append(asset)
    return assets


def normalize_workflow_asset(value: Any) -> WorkflowExecutionAsset | None:
    if isinstance(value, WorkflowExecutionAsset):
        handle = normalize_workflow_asset_handle(value.handle)
        if not handle.get("ok") or not _valid_image_bytes(value.data):
            return None
        return WorkflowExecutionAsset(
            handle=handle["handle"],
            data=bytes(value.data),
            content_type=_safe_image_content_type(value.content_type, value.data),
        )
    if not isinstance(value, Mapping):
        return None
    handle = normalize_workflow_asset_handle(
        value.get("handle")
        or value.get("inputHandle")
        or value.get("outputHandle")
        or value.get("assetHandle")
        or value.get("outputImageHandle")
        or value.get("inputImageHandle")
    )
    if not handle.get("ok"):
        return None
    data = decode_workflow_image_bytes(
        value.get("data")
        or value.get("bytes")
        or value.get("imageBytes")
        or value.get("inputImageBytes")
        or value.get("outputImageBytes")
    )
    if not data:
        return None
    return WorkflowExecutionAsset(
        handle=handle["handle"],
        data=data,
        content_type=_safe_image_content_type(
            value.get("contentType") or value.get("mimeType") or value.get("inputImageContentType"),
            data,
        ),
    )


def decode_workflow_image_bytes(value: Any) -> bytes:
    if value in (None, ""):
        return b""
    if isinstance(value, bytes):
        data = bytes(value)
    elif isinstance(value, bytearray):
        data = bytes(value)
    elif isinstance(value, memoryview):
        data = value.tobytes()
    elif isinstance(value, list):
        if len(value) > MAX_WORKFLOW_IMAGE_BYTES:
            return b""
        try:
            data = bytes(int(item) & 0xFF for item in value)
        except Exception:
            return b""
    elif isinstance(value, str):
        text = value.strip()
        if "," in text and text.lower().startswith("data:image/"):
            text = text.split(",", 1)[1].strip()
        if len(text) > MAX_WORKFLOW_IMAGE_BYTES * 2:
            return b""
        try:
            data = base64.b64decode(text, validate=True)
        except Exception:
            return b""
    else:
        return b""
    return data if _valid_image_bytes(data) else b""


def detect_workflow_image_extension(data: bytes) -> str:
    if len(data) >= 4 and data[:4] == b"\x89PNG":
        return "png"
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def workflow_image_content_type(data: bytes) -> str:
    extension = detect_workflow_image_extension(data)
    if extension == "jpg":
        return "image/jpeg"
    if extension == "webp":
        return "image/webp"
    if extension == "png":
        return "image/png"
    return ""


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


def _safe_image_content_type(value: Any, data: bytes) -> str:
    hinted = _safe_content_type(value)
    detected = workflow_image_content_type(data)
    if hinted in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        hinted = "image/jpeg" if hinted == "image/jpg" else hinted
        return hinted if not detected or hinted == detected else detected
    return detected or "application/octet-stream"


def _valid_image_bytes(data: bytes) -> bool:
    return bool(data) and len(data) <= MAX_WORKFLOW_IMAGE_BYTES and bool(detect_workflow_image_extension(data))


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
