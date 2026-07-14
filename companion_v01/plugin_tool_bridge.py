"""Thin Engine bridge for policy-accepted PluginHost capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from capcore import CapabilityResult, InvocationContext, filter_capabilities

from .client_protocol import ClientMode, ClientProtocolContext
from .plugin_host import PluginHost
from .tool_runtime import AdapterCapabilityToolHandler, ToolMetadata


class PluginCapabilitySource(Protocol):
    def build_tool_handlers(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
    ) -> Mapping[str, Any]: ...


class _PluginHostInvocationProxy:
    """Expose invocation only; never reveal a plugin's raw in-process adapter."""

    type = "plugin"

    def __init__(self, host: PluginHost) -> None:
        self._host = host

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        return await self._host.invoke_from_consumer(
            capability_id,
            args,
            context=ctx,
        )


class PluginCapabilityToolHandler(AdapterCapabilityToolHandler):
    """A policy-accepted plugin tool eligible for provider-native projection."""

    policy_accepted_plugin_capability = True
    policy_accepted_native_tool = True

    def tool_metadata(self) -> ToolMetadata:
        base = super().tool_metadata()
        return ToolMetadata(
            family="plugin_capability",
            operation="read",
            risk=base.risk,
            default_round_budget=base.default_round_budget,
            background=False,
            aliases=base.aliases,
            input_schema=base.input_schema,
            requires_confirmation=False,
        )


class PluginCapabilityToolBridge:
    """Project the host's immutable descriptor snapshot into Engine handlers."""

    def __init__(self, host: PluginHost, *, config_base_dir: Path | str | None = None) -> None:
        self._host = host
        self._config_base_dir = config_base_dir
        self._proxy = _PluginHostInvocationProxy(host)

    def build_tool_handlers(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
    ) -> Mapping[str, PluginCapabilityToolHandler]:
        surface = _surface_for_client(client_context)
        descriptors = filter_capabilities(
            self._host.capability_descriptors.values(),
            surface=surface,
            prompt_exposed=True,
        )
        return {
            descriptor.id: PluginCapabilityToolHandler(
                capability_id=descriptor.id,
                adapter=self._proxy,
                descriptor=descriptor,
                config_base_dir=self._config_base_dir,
            )
            for descriptor in descriptors
        }


def _surface_for_client(client_context: ClientProtocolContext | None) -> str:
    if client_context is None:
        return "base"
    mode = client_context.effective_mode
    if mode == ClientMode.DESKTOP_PET:
        return "desktop"
    if mode == ClientMode.QQ_TEXT:
        return "qq"
    return "web"


__all__ = [
    "PluginCapabilitySource",
    "PluginCapabilityToolBridge",
    "PluginCapabilityToolHandler",
]
