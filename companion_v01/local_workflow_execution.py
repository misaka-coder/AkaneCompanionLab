from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from capcore_adapter_comfyui.execution import (
    WorkflowExecutionAsset,
    WorkflowExecutionRequest,
    WorkflowExecutionResult,
    decode_workflow_image_bytes,
    detect_workflow_image_extension,
    normalize_workflow_asset,
    normalize_workflow_asset_handle,
    normalize_workflow_execution_result,
    sanitize_workflow_asset_list,
    sanitize_workflow_outputs,
    workflow_image_content_type,
)


@runtime_checkable
class WorkflowExecutionRunner(Protocol):
    def execute_workflow(self, request: WorkflowExecutionRequest) -> WorkflowExecutionResult | Mapping[str, Any]: ...


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


__all__ = [
    "WorkflowExecutionAsset",
    "WorkflowExecutionCallable",
    "WorkflowExecutionRequest",
    "WorkflowExecutionResult",
    "WorkflowExecutionRunner",
    "call_workflow_execution_runner",
    "decode_workflow_image_bytes",
    "detect_workflow_image_extension",
    "normalize_workflow_asset",
    "normalize_workflow_asset_handle",
    "normalize_workflow_execution_result",
    "sanitize_workflow_asset_list",
    "sanitize_workflow_outputs",
    "workflow_image_content_type",
]
