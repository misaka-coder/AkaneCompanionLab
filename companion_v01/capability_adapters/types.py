from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


SourceLayer = Literal["builtin", "profile"]
RiskLevel = Literal["low", "medium", "high"]
ConfirmPolicy = Literal["never", "first_time", "always"]


@dataclass(frozen=True)
class EndpointConfig:
    url: str
    loopback_only: bool = False
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class HealthConfig:
    method: str
    path: str
    timeout_seconds: float
    expect_status: tuple[int, ...]
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class TierConfig:
    id: str
    label: str
    preset: Mapping[str, Any]
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class TriggerConfig:
    kind: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class CapabilityIOSlot:
    name: str
    kind: str
    required: bool = False
    max_bytes: int | None = None
    delivery: str = ""
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    display_name: str
    short_hint: str
    visible_in: tuple[str, ...]
    prompt_exposed: bool
    risk: RiskLevel
    confirm: ConfirmPolicy
    effects: tuple[str, ...]
    trigger: TriggerConfig | None
    inputs: tuple[CapabilityIOSlot, ...]
    outputs: tuple[CapabilityIOSlot, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class CapabilityManifest:
    schema: str
    provider_id: str
    provider_type: str
    display_name: str
    endpoint: EndpointConfig | None
    health: HealthConfig | None
    tiers: tuple[TierConfig, ...]
    capabilities: tuple[CapabilityDescriptor, ...]
    secrets: tuple[str, ...]
    source_path: Path
    source_layer: SourceLayer
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class InvalidManifest:
    source_path: Path
    source_layer: SourceLayer
    reason: str
    detail: str = ""
    provider_id: str = ""


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    status: str
    reason: str = ""


@dataclass(frozen=True)
class InvocationContext:
    profile_user_id: str = ""
    session_id: str = ""
    client_mode: str = ""


@dataclass(frozen=True)
class CapabilityResult:
    is_error: bool
    content: Any = None
    status: str = ""
    reason: str = ""


class CapabilityManifestError(ValueError):
    """Reserved for callers that need exception-style manifest failures."""


class CapabilityProtocolError(RuntimeError):
    """Protocol-level adapter invocation failure."""
