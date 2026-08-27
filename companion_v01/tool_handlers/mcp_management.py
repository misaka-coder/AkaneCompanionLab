"""Owner-controlled MCP Host lifecycle tool."""

from __future__ import annotations

import json
from typing import Any

import config

from ..mcp_specs import MCP_MANAGE_TOOL_SPEC
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


def _can_manage(context: ToolExecutionContext) -> bool:
    request = context.request_context if isinstance(context.request_context, dict) else {}
    delivery = request.get("qq_delivery_context")
    if isinstance(delivery, dict):
        owner = str(getattr(config, "MASTER_QQ", "") or "").strip()
        sender = str(delivery.get("user_id") or "").strip()
        return bool(owner.isdigit() and sender == owner)
    return str(context.client_mode or "").strip() == "desktop_pet"


def _result(payload: dict[str, Any]) -> ToolExecutionResult:
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ToolExecutionResult(
        tool_type="mcp_manage",
        stream_events=[
            {
                "type": "mcp_management",
                "status": str(payload.get("status") or "error"),
                "server_id": str(payload.get("serverId") or ""),
                "reason": str(payload.get("reason") or ""),
            }
        ],
        followup_context=content,
        followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
    )


class McpManageToolHandler(BaseToolHandler):
    tool_type = "mcp_manage"

    def __init__(self, *, service: Any) -> None:
        self.service = service

    def tool_spec(self):
        return MCP_MANAGE_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": self.service is not None, "status": "ready" if self.service is not None else "unavailable"}

    def build_prompt_instruction(self) -> str:
        return (
            "- mcp_manage：主人可管理本 Host 的 MCP 连接。本地包先用 exec_run 安装，再 configure；"
            "configure 会先真实启动候选服务，成功才替换旧配置。remove 仅移除 Akane 连接并停止会话，"
            "不会卸载外部 npm/Python 包或删除源码。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        if action not in {"list", "configure", "discover", "enable", "disable", "restart", "remove"}:
            return None
        server_id = str(value.get("server_id") or "").strip()
        if action != "list" and not server_id:
            return None
        normalized = {"type": self.tool_type, "action": action}
        if server_id:
            normalized["server_id"] = server_id
        for key in ("display_name", "transport", "command", "cwd", "url"):
            if key in value:
                normalized[key] = str(value.get(key) or "")
        for key in ("args", "prompt_exposed_tools", "low_risk_allowlist"):
            if isinstance(value.get(key), list):
                normalized[key] = [str(item) for item in value[key]]
        for key in ("env", "headers"):
            if isinstance(value.get(key), dict):
                normalized[key] = {str(k): str(v) for k, v in value[key].items()}
        if "enabled" in value:
            normalized["enabled"] = bool(value.get("enabled"))
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if not _can_manage(context):
            return _result(
                {
                    "ok": False,
                    "status": "permission_denied",
                    "reason": "mcp_management_requires_owner",
                }
            )
        action = str(call.get("action") or "")
        profile_user_id = str(context.profile_user_id or "")
        server_id = str(call.get("server_id") or "")
        if action == "list":
            return _result(self.service.list(profile_user_id=profile_user_id))
        if action == "configure":
            payload = {
                "displayName": call.get("display_name") or server_id,
                "transport": call.get("transport") or "stdio",
                "command": call.get("command") or "",
                "args": call.get("args") or [],
                "cwd": call.get("cwd") or "",
                "env": call.get("env") or {},
                "url": call.get("url") or "",
                "headers": call.get("headers") or {},
                "enabled": bool(call.get("enabled", True)),
                "lowRiskAllowlist": call.get("low_risk_allowlist") or [],
            }
            result = self.service.configure(
                profile_user_id=profile_user_id,
                server_id=server_id,
                payload=payload,
                prompt_exposed_tools=call.get("prompt_exposed_tools"),
            )
        elif action == "discover":
            result = self.service.discover(profile_user_id=profile_user_id, server_id=server_id)
        elif action in {"enable", "disable"}:
            result = self.service.set_enabled(
                profile_user_id=profile_user_id,
                server_id=server_id,
                enabled=action == "enable",
            )
        elif action == "restart":
            result = self.service.restart(profile_user_id=profile_user_id, server_id=server_id)
        else:
            result = self.service.remove(profile_user_id=profile_user_id, server_id=server_id)
        return _result(dict(result))
