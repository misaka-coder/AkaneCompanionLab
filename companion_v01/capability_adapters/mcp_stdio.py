from __future__ import annotations

import inspect
import re
from typing import Any, Mapping

from companion_v01.local_capability_config import capability_approval_mode
from companion_v01.mcp_stdio_discoverer import McpStdioDiscoveryError, McpStdioToolCaller

from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityProtocolError,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
)


_PUBLIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class McpStdioCapabilityAdapter:
    type = "mcp_stdio"

    def __init__(
        self,
        *,
        provider_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
        tool_configs: tuple[Mapping[str, Any], ...],
        caller: Any | None = None,
    ) -> None:
        self.provider_id = _safe_token(provider_id) or f"provider.mcp.{_safe_token(server_id)}"
        self.server_id = _safe_token(server_id)
        self.server_config = dict(server_config)
        self.tool_configs = tuple(dict(item) for item in tool_configs if isinstance(item, Mapping))
        self.caller = caller or McpStdioToolCaller(timeout_seconds=20)
        self._tool_by_capability_id = {
            self._capability_id(str(tool.get("name") or "")): tool
            for tool in self.tool_configs
            if str(tool.get("name") or "").strip()
        }

    async def health(self) -> HealthStatus:
        if not bool(self.server_config.get("enabled")):
            return HealthStatus(ok=False, status="disabled", reason="mcp_server_disabled")
        if not str(self.server_config.get("command") or "").strip():
            return HealthStatus(ok=False, status="missing_config", reason="mcp_server_command_missing")
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(self._descriptor_for_tool(tool) for tool in self.tool_configs)

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        clean_capability_id = str(capability_id or "").strip()
        tool = self._tool_by_capability_id.get(clean_capability_id)
        if tool is None:
            raise CapabilityProtocolError("unknown_capability")
        tool_name = str(tool.get("name") or "").strip()
        if not tool_name:
            raise CapabilityProtocolError("unknown_capability")
        try:
            result = self.caller(
                server=self.server_config,
                tool_name=tool_name,
                arguments=dict(args or {}),
            )
            if inspect.isawaitable(result):
                result = await result
        except McpStdioDiscoveryError as exc:
            raise CapabilityProtocolError(str(exc) or "mcp_tool_call_failed") from exc
        except Exception as exc:
            raise CapabilityProtocolError("mcp_tool_call_failed") from exc
        result = result if isinstance(result, Mapping) else {}
        is_error = bool(result.get("isError") or result.get("is_error"))
        return CapabilityResult(
            is_error=is_error,
            content=dict(result),
            status="error" if is_error else "ok",
            reason="mcp_tool_error" if is_error else "",
        )

    async def aclose(self) -> None:
        return None

    def _descriptor_for_tool(self, tool: Mapping[str, Any]) -> CapabilityDescriptor:
        tool_name = str(tool.get("name") or "").strip()
        risk, confirm = self._risk_and_confirm(tool)
        input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), Mapping) else {}
        return CapabilityDescriptor(
            id=self._capability_id(tool_name),
            display_name=tool_name,
            short_hint=str(tool.get("description") or "").strip(),
            visible_in=("base", "web", "desktop", "qq"),
            prompt_exposed=bool(tool.get("promptExposed") or tool.get("prompt_exposed")),
            risk=risk,
            confirm=confirm,
            effects=(),
            trigger=None,
            inputs=self._io_slots_from_schema(input_schema),
            outputs=(),
            raw={
                "server_id": self.server_id,
                "tool_name": tool_name,
                "inputSchema": dict(input_schema),
                "approvalMode": capability_approval_mode(
                    enabled=bool(self.server_config.get("enabled", True)),
                    status="ready",
                    risk=risk,
                    requires_confirmation=confirm in {"first_time", "always"},
                ),
            },
        )

    def _risk_and_confirm(self, tool: Mapping[str, Any]) -> tuple[str, str]:
        risk = str(tool.get("risk") or "medium").strip().lower()
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        allowlist = {
            str(item or "").strip()
            for item in self.server_config.get("lowRiskAllowlist") or self.server_config.get("low_risk_allowlist") or []
        }
        tool_name = str(tool.get("name") or "").strip()
        if risk == "low" and tool_name not in allowlist:
            risk = "medium"
        confirm = str(tool.get("confirm") or "first_time").strip().lower()
        if confirm not in {"never", "first_time", "always"}:
            confirm = "first_time"
        if risk == "high":
            confirm = "always"
        return risk, confirm

    def _capability_id(self, tool_name: str) -> str:
        return f"mcp.{self.server_id}.{_safe_token(tool_name)}"

    @staticmethod
    def _io_slots_from_schema(input_schema: Any) -> tuple[CapabilityIOSlot, ...]:
        if not isinstance(input_schema, Mapping):
            return ()
        properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), Mapping) else {}
        required = set(input_schema.get("required") or []) if isinstance(input_schema.get("required"), list) else set()
        slots: list[CapabilityIOSlot] = []
        for name, prop in list(properties.items())[:24]:
            clean_name = _safe_token(name)
            if not clean_name:
                continue
            prop = prop if isinstance(prop, Mapping) else {}
            slots.append(
                CapabilityIOSlot(
                    name=clean_name,
                    kind=str(prop.get("type") or "string").strip() or "string",
                    required=clean_name in required,
                    raw=prop,
                )
            )
        return tuple(slots)


def _safe_token(value: Any) -> str:
    text = str(value or "").strip()
    return text if _PUBLIC_TOKEN_RE.fullmatch(text) else ""
