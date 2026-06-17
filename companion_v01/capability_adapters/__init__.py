from __future__ import annotations

from .manifest_loader import ALLOWED_ADAPTER_TYPES, load_manifest
from .comfyui import ComfyUiCapabilityAdapter, ComfyUiWorkflowCapability, build_comfyui_adapter_from_manifest
from .mcp_stdio import McpStdioCapabilityAdapter
from .openai_compat_asr import OpenAICompatASRAdapter
from .openai_compat_tts import OpenAICompatTTSAdapter
from .protocol import CapabilityAdapter
from .registry import CapabilityAdapterRegistry
from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityManifest,
    CapabilityManifestError,
    CapabilityProtocolError,
    CapabilityResult,
    ConfirmPolicy,
    EndpointConfig,
    HealthConfig,
    HealthStatus,
    InvalidManifest,
    InvocationContext,
    RiskLevel,
    SourceLayer,
    TierConfig,
    TriggerConfig,
)

__all__ = [
    "ALLOWED_ADAPTER_TYPES",
    "CapabilityAdapter",
    "CapabilityAdapterRegistry",
    "CapabilityDescriptor",
    "CapabilityIOSlot",
    "CapabilityManifest",
    "CapabilityManifestError",
    "CapabilityProtocolError",
    "CapabilityResult",
    "ComfyUiCapabilityAdapter",
    "ComfyUiWorkflowCapability",
    "McpStdioCapabilityAdapter",
    "OpenAICompatASRAdapter",
    "OpenAICompatTTSAdapter",
    "ConfirmPolicy",
    "EndpointConfig",
    "HealthConfig",
    "HealthStatus",
    "InvalidManifest",
    "InvocationContext",
    "RiskLevel",
    "SourceLayer",
    "TierConfig",
    "TriggerConfig",
    "build_comfyui_adapter_from_manifest",
    "load_manifest",
]
