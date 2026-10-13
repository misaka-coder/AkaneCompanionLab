"""Frozen turn snapshots, selection records and readiness disclosures."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

from capcore import CapabilityToolSpec
from ..client_protocol import ClientMode


@dataclass(frozen=True)
class CapabilitySnapshot:
    client_mode: ClientMode
    has_any_attachment: bool = False
    has_document_attachment: bool = False
    has_media_attachment: bool = False
    has_image_attachment: bool = False
    has_generated_file: bool = False
    has_document_generated_file: bool = False
    has_media_generated_file: bool = False
    has_image_generated_file: bool = False
    has_workspace_file: bool = False
    has_document_workspace_file: bool = False
    has_media_workspace_file: bool = False
    has_image_workspace_file: bool = False
    has_pending_gift: bool = False
    # Host-frozen execution provider present (host config, not transient readiness).
    execution_enabled: bool = False
    # 当前 QQ 会话是否由主人显式开放执行（另受宿主 EXECUTION_QQ_ENABLED 总闸约束）。
    execution_qq_enabled: bool = False


@dataclass(frozen=True)
class CapabilityModule:
    name: str
    layer: str
    modes: tuple[ClientMode, ...]
    tools: tuple[str, ...]
    light_hint: str
    trigger: Callable[[CapabilitySnapshot], bool]
    latent_reason: str = ""
    activation_hint: str = ""
    unavailable_reason: str = ""
    recovery_hint: str = ""

    def applies_to_mode(self, mode: ClientMode) -> bool:
        return mode in self.modes


@dataclass(frozen=True)
class CapabilitySelection:
    light_hints: tuple[str, ...]
    tool_names: tuple[str, ...]
    module_names: tuple[str, ...]
    schema_tool_names: tuple[str, ...] = ()
    native_tool_names: tuple[str, ...] = ()
    native_tool_aliases: Mapping[str, str] = field(default_factory=dict)
    layer_names: tuple[str, ...] = ()
    disclosures: tuple[CapabilityDisclosure, ...] = ()
    tool_specs: tuple[CapabilityToolSpec, ...] = ()
    execution_receipts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    resolved_handlers: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    # None means normal host resolution; an empty set deliberately allows no
    # execution. Lazy MCP dispatch must preserve this host-owned ceiling.
    execution_allowlist: frozenset[str] | None = None
    # Presentation and published contracts never grant execution permission.
    capability_catalog: Any = field(default=None, compare=False, repr=False)
    exposure_modes: Mapping[str, str] = field(default_factory=dict)
    exposure_preferences: Mapping[str, Any] = field(default_factory=dict)
    published_contracts: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class CapabilityDisclosure:
    """Small model-visible capability fact, separate from callable tool schemas."""

    capability_id: str
    state: str
    summary: str
    reason: str = ""
    activation: str = ""
    tool_names: tuple[str, ...] = ()
    unavailable_reason: str = ""
    recovery_hint: str = ""


def resolve_capability_disclosures(
    selection: CapabilitySelection,
    *,
    available_tool_names: tuple[str, ...] | list[str] | set[str],
) -> tuple[CapabilityDisclosure, ...]:
    """Apply runtime readiness results without exposing a hidden tool schema."""

    available = {str(name or "").strip() for name in available_tool_names if str(name or "").strip()}
    selected = set(selection.tool_names)
    resolved: list[CapabilityDisclosure] = []
    for disclosure in selection.disclosures:
        active_tools = selected.intersection(disclosure.tool_names)
        if disclosure.state != "ready" or not active_tools or active_tools.intersection(available):
            resolved.append(disclosure)
            continue
        resolved.append(
            replace(
                disclosure,
                state="unavailable",
                reason=(disclosure.unavailable_reason or "这项能力依赖的本地组件或外部服务当前没有通过可用性检查。"),
                activation=(disclosure.recovery_hint or "依赖恢复并通过下一次检查后，系统会自动重新开放对应工具。"),
            )
        )
    return tuple(resolved)
