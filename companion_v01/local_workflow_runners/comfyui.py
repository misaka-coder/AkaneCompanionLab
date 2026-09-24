from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Mapping

from capcore_adapter_comfyui import (
    ComfyUiCapabilityAdapter,
    ComfyUiClient,
    ComfyUiClientError,
    ComfyUiImageBytes,
    ComfyUiImageRef,
    ComfyUiSlotMappingError,
    ComfyUiWorkflowCapability,
    WorkflowExecutionAsset,
    WorkflowExecutionRequest,
    WorkflowExecutionResult,
    apply_comfyui_input_slots,
    detect_workflow_image_extension,
    extract_comfyui_output_images,
)

from ..local_capability_config import (
    load_capability_config,
    resolve_workflow_config_file_path,
)


class ComfyUiWorkflowRunner:
    """Run a configured Akane workflow through the extracted ComfyUI adapter."""

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


def _run_adapter_invoke(adapter: Any, capability_id: str, args: Mapping[str, Any]) -> Any:
    return asyncio.run(adapter.invoke(capability_id, args, ctx=None))


def _comfyui_upload_filename(request: WorkflowExecutionRequest, asset: WorkflowExecutionAsset) -> str:
    extension = detect_workflow_image_extension(asset.data) or "png"
    suffix = str(request.job_id or "job")[-12:] or "job"
    return f"akane_{suffix}_input.{extension}"


def _comfyui_client_id(request: WorkflowExecutionRequest) -> str:
    suffix = str(request.job_id or "job")[-16:] or "job"
    return f"akane-{suffix}"


__all__ = [
    "ComfyUiClient",
    "ComfyUiClientError",
    "ComfyUiImageBytes",
    "ComfyUiImageRef",
    "ComfyUiSlotMappingError",
    "ComfyUiWorkflowRunner",
    "apply_comfyui_input_slots",
    "extract_comfyui_output_images",
]
