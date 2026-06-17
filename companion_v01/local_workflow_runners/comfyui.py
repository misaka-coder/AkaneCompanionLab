from __future__ import annotations

import copy
import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import requests

from ..local_capability_config import (
    load_capability_config,
    normalize_local_http_endpoint,
    resolve_workflow_config_file_path,
)
from ..local_workflow_execution import (
    WorkflowExecutionAsset,
    WorkflowExecutionRequest,
    WorkflowExecutionResult,
    detect_workflow_image_extension,
)


COMFYUI_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
COMFYUI_INPUT_SLOT_PATH_RE = re.compile(
    r"^(?P<node_id>[A-Za-z0-9_-]{1,80})\.inputs\.(?P<input_path>[A-Za-z0-9_.-]{1,120})$"
)
COMFYUI_IMAGE_TYPES = {"input", "output", "temp"}


class ComfyUiClientError(RuntimeError):
    """Raised when the ComfyUI HTTP boundary returns an unusable response."""


class ComfyUiSlotMappingError(ValueError):
    """Raised when a configured slot path cannot be applied to workflow JSON."""


@dataclass(frozen=True)
class ComfyUiImageRef:
    filename: str
    subfolder: str = ""
    image_type: str = "output"


@dataclass(frozen=True)
class ComfyUiImageBytes:
    data: bytes
    content_type: str


class ComfyUiWorkflowRunner:
    """Run a configured Akane workflow through a local ComfyUI executor."""

    def __init__(
        self,
        *,
        config_base_dir: Path | str | None,
        client_factory: Any = None,
        sleep: Any = None,
        poll_interval_seconds: float = 1.0,
        max_poll_seconds: float = 60.0,
    ) -> None:
        self.config_base_dir = Path(config_base_dir) if config_base_dir is not None else None
        self.client_factory = client_factory or ComfyUiClient
        self.sleep = sleep or time.sleep
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds or 1.0))
        self.max_poll_seconds = max(self.poll_interval_seconds, float(max_poll_seconds or 60.0))

    def execute_workflow(self, request: WorkflowExecutionRequest) -> WorkflowExecutionResult:
        runtime_config = self._load_runtime_config(request)
        if not runtime_config.get("ok"):
            return WorkflowExecutionResult(
                ok=False,
                status="failed",
                reason=str(runtime_config.get("reason") or "workflow_runtime_config_invalid"),
            )

        input_handle = str(request.inputs.get("inputImageHandle") or "")
        output_handle = str(request.inputs.get("outputImageHandle") or "")
        input_asset = request.input_assets.get(input_handle)
        if input_asset is None:
            return WorkflowExecutionResult(ok=False, status="failed", reason="input_image_bytes_required")

        try:
            from ..capability_adapters.comfyui import ComfyUiCapabilityAdapter, ComfyUiWorkflowCapability

            adapter = ComfyUiCapabilityAdapter(
                provider_id="provider.comfyui.local",
                endpoint=runtime_config["endpoint"],
                capabilities=(
                    ComfyUiWorkflowCapability(
                        capability_id=request.capability_id,
                        display_name=request.workflow_id,
                        workflow_path=runtime_config["workflowPath"],
                        slot_mapping=runtime_config["slotMapping"],
                    ),
                ),
                client_factory=self.client_factory,
                sleep=self.sleep,
                poll_interval_seconds=self.poll_interval_seconds,
                max_poll_seconds=self.max_poll_seconds,
            )
            result = _run_adapter_invoke(
                adapter,
                request.capability_id,
                {
                    "image": input_asset.data,
                    "output_handle": output_handle,
                    "content_type": input_asset.content_type,
                    "upload_filename": _comfyui_upload_filename(request, input_asset),
                    "client_id": _comfyui_client_id(request),
                },
            )
        except ComfyUiSlotMappingError:
            return WorkflowExecutionResult(ok=False, status="failed", reason="workflow_slot_mapping_invalid")
        except ComfyUiClientError:
            return WorkflowExecutionResult(ok=False, status="failed", reason="comfyui_request_failed")
        except RuntimeError as exc:
            return WorkflowExecutionResult(ok=False, status="failed", reason=str(exc) or "workflow_runner_failed")
        except ValueError:
            return WorkflowExecutionResult(ok=False, status="failed", reason="workflow_runtime_config_invalid")
        except Exception:
            return WorkflowExecutionResult(ok=False, status="failed", reason="workflow_runner_failed")

        content = result.content if isinstance(result.content, Mapping) else {}
        raw_assets = content.get("outputAssets") if isinstance(content.get("outputAssets"), list) else []
        output_asset = next((asset for asset in raw_assets if isinstance(asset, WorkflowExecutionAsset)), None)
        if output_asset is None:
            return WorkflowExecutionResult(ok=False, status="failed", reason="workflow_runner_invalid_result")
        return WorkflowExecutionResult(
            ok=True,
            status="completed",
            reason="workflow_completed",
            outputs=({"handle": output_handle, "kind": "image", "contentType": output_asset.content_type},),
            output_assets=(output_asset,),
        )

    def _load_runtime_config(self, request: WorkflowExecutionRequest) -> dict[str, Any]:
        config = load_capability_config(
            base_dir=self.config_base_dir,
            profile_user_id=request.profile_user_id,
        )
        provider = config.get("providers", {}).get("provider.comfyui.local")
        workflow = config.get("workflows", {}).get(request.workflow_id)
        if not isinstance(provider, Mapping) or not provider.get("enabled") or not provider.get("endpoint"):
            return {"ok": False, "reason": "comfyui_provider_missing"}
        if not isinstance(workflow, Mapping) or not workflow.get("enabled"):
            return {"ok": False, "reason": "workflow_binding_missing"}
        workflow_path = resolve_workflow_config_file_path(
            base_dir=self.config_base_dir,
            profile_user_id=request.profile_user_id,
            workflow_path=str(workflow.get("workflowPath") or ""),
        )
        slot_mapping = workflow.get("slotMapping") if isinstance(workflow.get("slotMapping"), Mapping) else {}
        if workflow_path is None or not slot_mapping:
            return {"ok": False, "reason": "workflow_binding_missing"}
        return {
            "ok": True,
            "endpoint": provider["endpoint"],
            "workflowPath": workflow_path,
            "slotMapping": dict(slot_mapping),
        }

    def _read_workflow_json(self, workflow_path: Path) -> dict[str, Any]:
        if not workflow_path.is_file():
            raise ValueError("workflow_file_missing")
        if workflow_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("workflow_file_too_large")
        payload = json.loads(workflow_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("workflow_json_invalid")
        return dict(payload)

    def _wait_for_first_output_image(self, client: "ComfyUiClient", prompt_id: str) -> ComfyUiImageRef:
        deadline = time.monotonic() + self.max_poll_seconds
        last_history: dict[str, Any] = {}
        while time.monotonic() <= deadline:
            last_history = client.get_history(prompt_id)
            images = extract_comfyui_output_images(last_history, prompt_id=prompt_id)
            if images:
                return images[0]
            self.sleep(self.poll_interval_seconds)
        images = extract_comfyui_output_images(last_history, prompt_id=prompt_id) if last_history else []
        if images:
            return images[0]
        raise ComfyUiClientError("comfyui_output_timeout")


def extract_comfyui_output_images(
    history: Mapping[str, Any],
    *,
    prompt_id: str | None = None,
) -> list[ComfyUiImageRef]:
    """Extract safe ComfyUI image refs from a history response."""

    if not isinstance(history, Mapping):
        raise ComfyUiClientError("history_invalid_json")
    history_entry = _select_comfyui_history_entry(history, prompt_id=prompt_id)
    if history_entry is None:
        return []

    outputs = history_entry.get("outputs")
    if outputs in (None, ""):
        return []
    if not isinstance(outputs, Mapping):
        raise ComfyUiClientError("history_outputs_invalid")

    images: list[ComfyUiImageRef] = []
    for node_output in outputs.values():
        if not isinstance(node_output, Mapping):
            continue
        node_images = node_output.get("images")
        if not isinstance(node_images, list):
            continue
        for raw_image in node_images:
            image_ref = _parse_comfyui_history_image_ref(raw_image)
            if image_ref is not None:
                images.append(image_ref)
    return images


class ComfyUiClient:
    """Tiny loopback-only client for the public ComfyUI HTTP API."""

    def __init__(
        self,
        endpoint: str,
        *,
        session: Any | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        normalized = normalize_local_http_endpoint(endpoint)
        if not normalized.get("ok"):
            raise ValueError(str(normalized.get("reason") or "invalid_comfyui_endpoint"))
        self.endpoint = str(normalized["endpoint"]).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = max(0.5, float(timeout_seconds or 30.0))

    def upload_image(
        self,
        image_bytes: bytes,
        *,
        filename: str,
        subfolder: str = "akane",
        image_type: str = "input",
        overwrite: bool = True,
        content_type: str | None = None,
    ) -> ComfyUiImageRef:
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("image_bytes_required")
        safe_filename = _safe_comfyui_value(filename, "filename")
        safe_subfolder = _safe_comfyui_value(subfolder, "subfolder") if subfolder else ""
        safe_type = _safe_comfyui_image_type(image_type)
        response = self.session.post(
            self._url("/upload/image"),
            data={
                "subfolder": safe_subfolder,
                "type": safe_type,
                "overwrite": "true" if overwrite else "false",
            },
            files={
                "image": (
                    safe_filename,
                    image_bytes,
                    content_type or _guess_image_content_type(safe_filename),
                )
            },
            timeout=self.timeout_seconds,
        )
        payload = _json_response(response, "upload_image")
        return ComfyUiImageRef(
            filename=_safe_response_value(payload.get("name"), safe_filename),
            subfolder=_safe_response_value(payload.get("subfolder"), safe_subfolder),
            image_type=_safe_comfyui_image_type(payload.get("type") or safe_type),
        )

    def queue_prompt(self, workflow: Mapping[str, Any], *, client_id: str = "") -> str:
        if not isinstance(workflow, Mapping) or not workflow:
            raise ValueError("workflow_prompt_required")
        payload: dict[str, Any] = {"prompt": dict(workflow)}
        if client_id:
            payload["client_id"] = _safe_comfyui_value(client_id, "client_id")
        response = self.session.post(
            self._url("/prompt"),
            json=payload,
            timeout=self.timeout_seconds,
        )
        data = _json_response(response, "queue_prompt")
        prompt_id = str(data.get("prompt_id") or "").strip()
        if not COMFYUI_SAFE_VALUE_RE.match(prompt_id):
            raise ComfyUiClientError("queue_prompt_missing_prompt_id")
        return prompt_id

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        safe_prompt_id = _safe_comfyui_value(prompt_id, "prompt_id")
        response = self.session.get(
            self._url(f"/history/{safe_prompt_id}"),
            timeout=self.timeout_seconds,
        )
        return dict(_json_response(response, "get_history"))

    def get_image(
        self,
        image: ComfyUiImageRef | str,
        *,
        subfolder: str = "",
        image_type: str = "output",
    ) -> ComfyUiImageBytes:
        if isinstance(image, ComfyUiImageRef):
            filename = image.filename
            subfolder = image.subfolder
            image_type = image.image_type
        else:
            filename = str(image or "")
        safe_filename = _safe_comfyui_value(filename, "filename")
        safe_subfolder = _safe_comfyui_value(subfolder, "subfolder") if subfolder else ""
        safe_type = _safe_comfyui_image_type(image_type)
        response = self.session.get(
            self._url("/view"),
            params={"filename": safe_filename, "subfolder": safe_subfolder, "type": safe_type},
            timeout=self.timeout_seconds,
        )
        _raise_for_status(response, "get_image")
        content = bytes(getattr(response, "content", b"") or b"")
        if not content:
            raise ComfyUiClientError("get_image_empty_response")
        headers = getattr(response, "headers", {}) or {}
        return ComfyUiImageBytes(
            data=content,
            content_type=str(headers.get("content-type") or headers.get("Content-Type") or "application/octet-stream"),
        )

    def _url(self, path: str) -> str:
        suffix = "/" + str(path or "").strip().lstrip("/")
        return f"{self.endpoint}{suffix}"


def apply_comfyui_input_slots(
    workflow: Mapping[str, Any],
    slot_mapping: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a workflow copy with Akane input slot values applied.

    Slot paths intentionally support only ComfyUI input fields:
    ``node_id.inputs.field`` or ``node_id.inputs.nested.field``.
    Output extraction is handled separately from ComfyUI history.
    """

    if not isinstance(workflow, Mapping) or not workflow:
        raise ComfyUiSlotMappingError("workflow_json_required")
    if not isinstance(slot_mapping, Mapping) or not slot_mapping:
        raise ComfyUiSlotMappingError("slot_mapping_required")
    if not isinstance(values, Mapping) or not values:
        raise ComfyUiSlotMappingError("slot_values_required")

    next_workflow = copy.deepcopy(dict(workflow))
    for raw_slot, value in values.items():
        slot = _safe_comfyui_value(raw_slot, "slot")
        raw_path = slot_mapping.get(slot)
        if raw_path in (None, ""):
            raise ComfyUiSlotMappingError(f"slot_mapping_missing:{slot}")
        node_id, input_path = _parse_comfyui_input_slot_path(raw_path)
        _set_comfyui_input_value(next_workflow, node_id=node_id, input_path=input_path, value=value)
    return next_workflow


def _json_response(response: Any, action: str) -> Mapping[str, Any]:
    _raise_for_status(response, action)
    try:
        payload = response.json()
    except Exception as exc:
        raise ComfyUiClientError(f"{action}_invalid_json") from exc
    if not isinstance(payload, Mapping):
        raise ComfyUiClientError(f"{action}_invalid_json")
    return payload


def _run_adapter_invoke(adapter: Any, capability_id: str, args: Mapping[str, Any]) -> Any:
    return asyncio.run(adapter.invoke(capability_id, args, ctx=None))


def _raise_for_status(response: Any, action: str) -> None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code >= 400:
        raise ComfyUiClientError(f"{action}_http_{status_code}")


def _safe_comfyui_value(value: Any, field: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if (
        not COMFYUI_SAFE_VALUE_RE.match(text)
        or "://" in text
        or "/" in text
        or "\\" in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        raise ValueError(f"{field}_must_be_safe_opaque_id")
    return text


def _safe_response_value(value: Any, fallback: str) -> str:
    try:
        return _safe_comfyui_value(value, "response_value")
    except ValueError:
        return fallback


def _safe_comfyui_image_type(value: Any) -> str:
    image_type = str(value or "").strip().lower()
    if image_type not in COMFYUI_IMAGE_TYPES:
        raise ValueError("image_type_invalid")
    return image_type


def _select_comfyui_history_entry(
    history: Mapping[str, Any],
    *,
    prompt_id: str | None = None,
) -> Mapping[str, Any] | None:
    if prompt_id:
        safe_prompt_id = _safe_comfyui_value(prompt_id, "prompt_id")
        entry = history.get(safe_prompt_id)
        if entry is None:
            return None
        if not isinstance(entry, Mapping):
            raise ComfyUiClientError("history_entry_invalid")
        return entry

    if "outputs" in history:
        return history

    entries = [
        entry
        for entry in history.values()
        if isinstance(entry, Mapping) and "outputs" in entry
    ]
    if not entries:
        return None
    if len(entries) > 1:
        raise ComfyUiClientError("history_prompt_id_required")
    return entries[0]


def _parse_comfyui_history_image_ref(value: Any) -> ComfyUiImageRef | None:
    if not isinstance(value, Mapping):
        return None
    try:
        filename = _safe_comfyui_value(value.get("filename"), "filename")
        raw_subfolder = value.get("subfolder")
        subfolder = _safe_comfyui_value(raw_subfolder, "subfolder") if raw_subfolder else ""
        image_type = _safe_comfyui_image_type(value.get("type") or "output")
    except ValueError:
        return None
    return ComfyUiImageRef(filename=filename, subfolder=subfolder, image_type=image_type)


def _parse_comfyui_input_slot_path(value: Any) -> tuple[str, list[str]]:
    text = str(value or "").strip()
    match = COMFYUI_INPUT_SLOT_PATH_RE.match(text)
    if not match:
        raise ComfyUiSlotMappingError("slot_path_must_target_input")
    input_path = [part for part in match.group("input_path").split(".") if part]
    if not input_path:
        raise ComfyUiSlotMappingError("slot_path_must_target_input")
    return match.group("node_id"), input_path


def _set_comfyui_input_value(
    workflow: dict[str, Any],
    *,
    node_id: str,
    input_path: list[str],
    value: Any,
) -> None:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise ComfyUiSlotMappingError(f"workflow_node_missing:{node_id}")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise ComfyUiSlotMappingError(f"workflow_node_inputs_missing:{node_id}")
    target = inputs
    for part in input_path[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            raise ComfyUiSlotMappingError(f"workflow_input_path_missing:{node_id}")
        target = child
    target[input_path[-1]] = copy.deepcopy(value)


def _guess_image_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _comfyui_upload_filename(request: WorkflowExecutionRequest, asset: WorkflowExecutionAsset) -> str:
    extension = detect_workflow_image_extension(asset.data) or "png"
    suffix = str(request.job_id or "job")[-12:] or "job"
    return f"akane_{suffix}_input.{extension}"


def _comfyui_client_id(request: WorkflowExecutionRequest) -> str:
    suffix = str(request.job_id or "job")[-16:] or "job"
    return f"akane-{suffix}"
