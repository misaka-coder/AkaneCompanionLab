from __future__ import annotations

from capcore_adapter_comfyui import (
    ComfyUiCapabilityAdapter,
    ComfyUiClient,
    ComfyUiClientError,
    ComfyUiImageBytes,
    ComfyUiImageRef,
    ComfyUiSlotMappingError,
    ComfyUiWorkflowCapability,
    WorkflowExecutionAsset,
    apply_comfyui_input_slots,
    build_comfyui_adapter_from_manifest,
    detect_workflow_image_extension,
    extract_comfyui_output_images,
)

from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityManifest,
    CapabilityProtocolError,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
)

__all__ = [
    "CapabilityDescriptor",
    "CapabilityIOSlot",
    "CapabilityManifest",
    "CapabilityProtocolError",
    "CapabilityResult",
    "ComfyUiCapabilityAdapter",
    "ComfyUiClient",
    "ComfyUiClientError",
    "ComfyUiImageBytes",
    "ComfyUiImageRef",
    "ComfyUiSlotMappingError",
    "ComfyUiWorkflowCapability",
    "HealthStatus",
    "InvocationContext",
    "WorkflowExecutionAsset",
    "apply_comfyui_input_slots",
    "build_comfyui_adapter_from_manifest",
    "detect_workflow_image_extension",
    "extract_comfyui_output_images",
]
