from __future__ import annotations

import inspect
import re
from dataclasses import replace
from typing import Any, Mapping

from capcore_adapter_mcp import (
    McpClientError,
    McpClientProtocol,
    McpExposurePolicy,
    McpStdioCapabilityAdapter as CoreMcpStdioCapabilityAdapter,
    McpStdioServerConfig,
    McpToolOverride,
    McpToolRecord,
)

from companion_v01.local_capability_config import capability_approval_mode
from companion_v01.mcp_stdio_discoverer import McpStdioDiscoveryError, McpStdioToolCaller

from .types import CapabilityDescriptor, CapabilityProtocolError, CapabilityResult, HealthStatus, InvocationContext


_PUBLIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class McpStdioCapabilityAdapter:
    """Akane compatibility wrapper around capcore-adapter-mcp.

    Akane keeps ownership of profile config, prompt exposure, approval policy,
    and its existing MCP argv/env hydration. The reusable adapter package owns
    descriptor conversion, capability id collision handling, and JSON-safe MCP
    invocation arguments.
    """

    type = "mcp_stdio"

    def __init__(
        self,
        *,
        provider_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
        tool_configs: tuple[Mapping[str, Any], ...],
        caller: Any | None = None,
        client: McpClientProtocol | None = None,
    ) -> None:
        self.provider_id = _safe_token(provider_id) or f"provider.mcp.{_safe_token(server_id)}"
        self.server_id = _safe_token(server_id)
        self.server_config = dict(server_config)
        self.tool_configs = tuple(dict(item) for item in tool_configs if isinstance(item, Mapping))
        self.caller = caller or McpStdioToolCaller(timeout_seconds=20)
        self._client = client or _AkaneMcpClient(
            server_config=self.server_config,
            tool_configs=self.tool_configs,
            caller=self.caller,
        )
        self._core = CoreMcpStdioCapabilityAdapter(
            provider_id=self.provider_id,
            server=_server_config(self.server_id, self.server_config),
            tool_overrides=_tool_overrides(self.server_config, self.tool_configs),
            exposure_policy=McpExposurePolicy(default_visible_in=("base", "web", "desktop", "qq")),
            client=self._client,
        )

    async def health(self) -> HealthStatus:
        return await self._core.health()

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(
            _with_akane_raw_metadata(descriptor, self.server_config)
            for descriptor in await self._core.list_capabilities()
        )

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        return await self._core.invoke(capability_id, args, ctx)

    async def aclose(self) -> None:
        await self._core.aclose()

    def descriptor_for_tool(self, tool: Mapping[str, Any]) -> CapabilityDescriptor:
        descriptor = self._core.descriptor_for_tool(_tool_record(tool))
        return _with_akane_raw_metadata(descriptor, self.server_config)

    def is_live(self) -> bool:
        """M66-F: Return True when the MCP server is configured and considered reachable.
        Uses a lightweight sync check: command must be present and server enabled.
        The WebSearchToolHandler uses a deeper background probe for web_search;
        for general MCP adapters this covers the common 'server disabled/removed' case.
        """
        if not bool(self.server_config.get("enabled")):
            return False
        command = str(self.server_config.get("command") or "").strip()
        return bool(command)

    def _risk_and_confirm(self, tool: Mapping[str, Any]) -> tuple[str, str]:
        override = _tool_override(self.server_config, tool)
        return override.risk or "medium", override.confirm or "first_time"


class _AkaneMcpClient:
    def __init__(
        self,
        *,
        server_config: Mapping[str, Any],
        tool_configs: tuple[Mapping[str, Any], ...],
        caller: Any,
    ) -> None:
        self.server_config = dict(server_config)
        self.tool_records = tuple(_tool_record(tool) for tool in tool_configs)
        self.caller = caller

    async def list_tools(self, server: McpStdioServerConfig) -> tuple[McpToolRecord, ...]:
        del server
        return self.tool_records

    async def call_tool(
        self,
        server: McpStdioServerConfig,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del server
        try:
            result = self.caller(
                server=self.server_config,
                tool_name=tool_name,
                arguments=dict(arguments or {}),
            )
            if inspect.isawaitable(result):
                result = await result
        except McpStdioDiscoveryError as exc:
            raise McpClientError(str(exc) or "mcp_tool_call_failed") from exc
        except Exception as exc:
            raise McpClientError("mcp_tool_call_failed") from exc
        return dict(result) if isinstance(result, Mapping) else {}


def _server_config(server_id: str, raw: Mapping[str, Any]) -> McpStdioServerConfig:
    env = raw.get("env") if isinstance(raw.get("env"), Mapping) else None
    return McpStdioServerConfig(
        server_id=server_id,
        command=str(raw.get("command") or "").strip(),
        args=_args_tuple(raw.get("args")),
        env={str(key): str(value) for key, value in env.items()} if env is not None else None,
        cwd=str(raw.get("cwd") or "").strip() or None,
        enabled=bool(raw.get("enabled")),
    )


def _tool_overrides(
    server_config: Mapping[str, Any],
    tool_configs: tuple[Mapping[str, Any], ...],
) -> dict[str, McpToolOverride]:
    overrides: dict[str, McpToolOverride] = {}
    for tool in tool_configs:
        tool_name = str(tool.get("name") or "").strip()
        if tool_name:
            overrides[tool_name] = _tool_override(server_config, tool)
    return overrides


def _tool_override(server_config: Mapping[str, Any], tool: Mapping[str, Any]) -> McpToolOverride:
    risk = str(tool.get("risk") or "medium").strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    allowlist = {
        str(item or "").strip()
        for item in server_config.get("lowRiskAllowlist") or server_config.get("low_risk_allowlist") or []
    }
    tool_name = str(tool.get("name") or "").strip()
    if risk == "low" and tool_name not in allowlist:
        risk = "medium"

    confirm = str(tool.get("confirm") or "first_time").strip().lower()
    if confirm not in {"never", "first_time", "always"}:
        confirm = "first_time"
    if risk == "high":
        confirm = "always"

    return McpToolOverride(
        display_name=tool_name,
        short_hint=str(tool.get("description") or "").strip(),
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=bool(tool.get("promptExposed") or tool.get("prompt_exposed")),
        risk=risk,  # type: ignore[arg-type]
        confirm=confirm,  # type: ignore[arg-type]
        effects=_effects_tuple(tool.get("effects")),
    )


def _tool_record(tool: Mapping[str, Any]) -> McpToolRecord:
    input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), Mapping) else {}
    output_schema = tool.get("outputSchema") if isinstance(tool.get("outputSchema"), Mapping) else None
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), Mapping) else None
    return McpToolRecord(
        name=str(tool.get("name") or "").strip(),
        description=str(tool.get("description") or "").strip(),
        input_schema=input_schema,
        output_schema=output_schema,
        annotations=annotations,
        raw=dict(tool),
    )


def _with_akane_raw_metadata(
    descriptor: CapabilityDescriptor,
    server_config: Mapping[str, Any],
) -> CapabilityDescriptor:
    raw = dict(descriptor.raw or {})
    raw["approvalMode"] = capability_approval_mode(
        enabled=bool(server_config.get("enabled", True)),
        status="ready",
        risk=str(descriptor.risk or "medium"),
        requires_confirmation=str(descriptor.confirm or "") in {"first_time", "always"},
    )
    return replace(descriptor, raw=raw)


def _args_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item or "").strip() for item in value if str(item or "").strip())


def _effects_tuple(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple, set)):
        return None
    result = tuple(str(item or "").strip() for item in value if str(item or "").strip())
    return result or None


def _safe_token(value: Any) -> str:
    text = str(value or "").strip()
    return text if _PUBLIC_TOKEN_RE.fullmatch(text) else ""
