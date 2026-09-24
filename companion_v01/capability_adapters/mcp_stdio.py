from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import re
import threading
import time
from dataclasses import replace
from typing import Any, Mapping

from capcore_adapter_mcp import (
    McpClientError,
    McpClientProtocol,
    McpExposurePolicy,
    McpStdioCapabilityAdapter as CoreMcpStdioCapabilityAdapter,
    McpStdioServerConfig,
    McpStreamableHttpCapabilityAdapter as CoreMcpStreamableHttpCapabilityAdapter,
    McpStreamableHttpServerConfig,
    McpToolOverride,
    McpToolRecord,
)

from companion_v01.local_capability_config import capability_approval_mode
from companion_v01.mcp_stdio_discoverer import (
    McpStdioDiscoveryError,
    McpToolCaller,
    McpToolDiscoverer,
)

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
        liveness_probe: Any | None = None,
        liveness_clock: Any = time.monotonic,
        contract_revision: str = "",
        current_revision=None,
    ) -> None:
        self.provider_id = _safe_token(provider_id) or f"provider.mcp.{_safe_token(server_id)}"
        self.server_id = _safe_token(server_id)
        self.server_config = dict(server_config)
        self.tool_configs = tuple(dict(item) for item in tool_configs if isinstance(item, Mapping))
        self.caller = caller or McpToolCaller(timeout_seconds=20)
        self._liveness_probe = liveness_probe or McpToolDiscoverer(
            timeout_seconds=3.0,
            max_pages=1,
            max_messages=40,
        )
        self._liveness_clock = liveness_clock
        self._liveness_lock = threading.RLock()
        self._liveness_cache: tuple[float, frozenset[str]] | None = None
        self._live_tools: tuple[dict[str, Any], ...] = ()
        self._current_revision = current_revision
        self._captured_revision = contract_revision
        self.contract_revision = hashlib.sha256(json.dumps({
            "runtime": contract_revision,
            "connection": {key: self.server_config.get(key) for key in
                ("transport", "command", "args", "cwd", "env", "url", "headers")},
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        self._capability_tool_names: dict[str, str] = {}
        self._client = client or _AkaneMcpClient(
            server_config=self.server_config,
            tool_configs=self.tool_configs,
            caller=self.caller,
        )
        core_adapter_type = (
            CoreMcpStreamableHttpCapabilityAdapter
            if _normalized_transport(self.server_config.get("transport")) == "streamable_http"
            else CoreMcpStdioCapabilityAdapter
        )
        self._core = core_adapter_type(
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
        if self._current_revision is not None and self._current_revision() != self._captured_revision:
            return CapabilityResult(is_error=True, status="rejected", reason="capability_contract_stale")
        return await self._core.invoke(capability_id, args, ctx)

    def current_tool_configs(self, *, refresh: bool = False) -> tuple[dict[str, Any], ...]:
        """Definitions from the same real tools/list lease as readiness."""
        if not self.is_live(refresh=refresh):
            return ()
        overrides = {str(tool.get("name") or ""): tool for tool in self.tool_configs}
        return tuple({**overrides.get(str(tool.get("name") or ""), {}), **tool,
                      **{key: overrides[str(tool.get("name") or "")][key]
                         for key in ("risk", "confirm", "effects", "modelVisible")
                         if key in overrides.get(str(tool.get("name") or ""), {})}, "promptExposed": True}
                     for tool in self._live_tools
                     if overrides.get(str(tool.get("name") or ""), {}).get("modelVisible") is not False)

    async def aclose(self) -> None:
        await self._core.aclose()

    def descriptor_for_tool(self, tool: Mapping[str, Any]) -> CapabilityDescriptor:
        descriptor = self._core.descriptor_for_tool(_tool_record(tool))
        descriptor = replace(descriptor, short_hint=str(tool.get("description") or descriptor.short_hint))
        tool_name = str(tool.get("name") or "").strip()
        if descriptor.id and tool_name:
            self._capability_tool_names[str(descriptor.id)] = tool_name
        return _with_akane_raw_metadata(descriptor, self.server_config)

    def tool_name_for_capability(self, capability_id: str) -> str:
        """Return the exact MCP protocol tool name for a projected capability."""

        return str(self._capability_tool_names.get(str(capability_id or "").strip()) or "")

    def is_live(self, capability_id: str = "", *, refresh: bool = False) -> bool:
        """Probe a real initialize/tools-list exchange and cache only its lease."""
        if not bool(self.server_config.get("enabled")):
            return False
        if not _server_has_transport_config(self.server_config):
            return False
        now = float(self._liveness_clock())
        with self._liveness_lock:
            cached = self._liveness_cache
            if not refresh and cached is not None and cached[0] > now:
                return self._capability_is_present(capability_id, cached[1])
        try:
            result = self._liveness_probe(server=self.server_config)
            if inspect.isawaitable(result):
                result = _run_awaitable_blocking(result)
            raw_tools = result.get("tools") if isinstance(result, Mapping) else None
            if not isinstance(raw_tools, list):
                raise McpStdioDiscoveryError("mcp_liveness_invalid_response")
            self._live_tools = tuple(dict(tool) for tool in raw_tools if isinstance(tool, Mapping))
            live_tool_names = frozenset(
                str(tool.get("name") or "").strip()
                for tool in raw_tools
                if isinstance(tool, Mapping) and str(tool.get("name") or "").strip()
            )
            expires_at = float(self._liveness_clock()) + 15.0
        except Exception:
            live_tool_names = frozenset()
            self._live_tools = ()
            expires_at = float(self._liveness_clock()) + 5.0
        with self._liveness_lock:
            self._liveness_cache = (expires_at, live_tool_names)
        return self._capability_is_present(capability_id, live_tool_names)

    def _capability_is_present(self, capability_id: str, live_tool_names: frozenset[str]) -> bool:
        clean_capability_id = str(capability_id or "").strip()
        if not clean_capability_id:
            return bool(live_tool_names)
        tool_name = self._capability_tool_names.get(clean_capability_id, "")
        return bool(tool_name and tool_name in live_tool_names)

    def _risk_and_confirm(self, tool: Mapping[str, Any]) -> tuple[str, str]:
        override = _tool_override(self.server_config, tool)
        return override.risk or "medium", override.confirm or "first_time"


def _run_awaitable_blocking(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, Any] = {}
    failure: list[BaseException] = []

    def runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - re-raised below
            failure.append(exc)

    thread = threading.Thread(target=runner, name="akane-mcp-liveness", daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result.get("value")


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

    async def list_tools(
        self,
        server: McpStdioServerConfig | McpStreamableHttpServerConfig,
    ) -> tuple[McpToolRecord, ...]:
        del server
        return self.tool_records

    async def call_tool(
        self,
        server: McpStdioServerConfig | McpStreamableHttpServerConfig,
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

    async def aclose(self) -> None:
        aclose = getattr(self.caller, "aclose", None)
        if callable(aclose):
            result = aclose()
            if inspect.isawaitable(result):
                await result


def _server_config(
    server_id: str,
    raw: Mapping[str, Any],
) -> McpStdioServerConfig | McpStreamableHttpServerConfig:
    if _normalized_transport(raw.get("transport")) == "streamable_http":
        headers = raw.get("headers") if isinstance(raw.get("headers"), Mapping) else None
        return McpStreamableHttpServerConfig(
            server_id=server_id,
            url=str(raw.get("url") or "").strip(),
            headers={str(key): str(value) for key, value in headers.items()} if headers is not None else None,
            enabled=bool(raw.get("enabled")),
        )
    env = raw.get("env") if isinstance(raw.get("env"), Mapping) else None
    return McpStdioServerConfig(
        server_id=server_id,
        command=str(raw.get("command") or "").strip(),
        args=_args_tuple(raw.get("args")),
        env={str(key): str(value) for key, value in env.items()} if env is not None else None,
        cwd=str(raw.get("cwd") or "").strip() or None,
        enabled=bool(raw.get("enabled")),
    )


def _normalized_transport(value: Any) -> str:
    text = str(value or "stdio").strip().lower().replace("-", "_")
    return "streamable_http" if text in {"http", "streamablehttp", "streamable_http"} else text


def _server_has_transport_config(server: Mapping[str, Any]) -> bool:
    transport = _normalized_transport(server.get("transport"))
    if transport == "streamable_http":
        return bool(str(server.get("url") or "").strip())
    return transport == "stdio" and bool(str(server.get("command") or "").strip())


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
