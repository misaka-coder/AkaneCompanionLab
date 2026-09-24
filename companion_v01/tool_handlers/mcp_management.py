"""Owner-controlled MCP Host lifecycle tool."""

from __future__ import annotations

import json
from typing import Any

import config

from ..mcp_specs import INVOKE_MCP_TOOL_SPEC, LOAD_MCP_TOOL_SPEC, MCP_MANAGE_TOOL_SPEC
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope
from .management_permissions import gate_extension_mutation


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


class LoadMcpToolHandler(BaseToolHandler):
    tool_type = "load_mcp"

    def __init__(self, *, service: Any) -> None:
        self.service = service

    def tool_spec(self):
        return LOAD_MCP_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": self.service is not None, "status": "ready" if self.service is not None else "unavailable"}

    def build_prompt_instruction(self) -> str:
        return "- load_mcp：按 server_id 返回当前可见工具的完整契约与 contract_ref；使用 capability_invoke 执行。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        raw_ids = value.get("server_ids")
        if not isinstance(raw_ids, list):
            return None
        server_ids = sorted({str(item or "").strip() for item in raw_ids if str(item or "").strip()})
        if not server_ids:
            return None
        return {"type": self.tool_type, "server_ids": server_ids}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        from ..capability_contracts import contract_failure
        catalog = getattr(context.capability_selection, "capability_catalog", None)
        if catalog is None:
            return contract_failure(self.tool_type, "capability_catalog_unavailable")
        current = catalog.current()
        servers = set(call.get("server_ids") or [])
        ids = [name for name in current.capability_ids
               if str(getattr(getattr(current.handler(name), "adapter", None), "server_id", "")) in servers]
        payload = current.load(ids)
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return ToolExecutionResult(tool_type=self.tool_type, followup_context=content,
            followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True))



class InvokeMcpToolHandler(BaseToolHandler):
    """Model-facing schema for the engine's thin MCP dispatch router.

    Successful calls are rewritten to the exact adapter capability before the
    execution boundary, so this handler never invokes MCP or bypasses the
    selected tool's validation and approval policy.
    """

    tool_type = "invoke_mcp"

    def tool_spec(self):
        return INVOKE_MCP_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": True, "status": "ready"}

    def build_prompt_instruction(self) -> str:
        return (
            "- invoke_mcp：旧名称兼容入口，需要精确 server_id、tool_name、仍有效的 contract_ref 和业务参数；"
            "缺少当前契约时用 capability_load 或 load_mcp 读取，再通过 capability_invoke 执行。"
            "历史结果不授予权限；调用沿用目标工具的校验、权限和审批。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        server_id = str(value.get("server_id") or "").strip()
        tool_name = str(value.get("tool_name") or "").strip()
        arguments = value.get("arguments")
        if not server_id or not tool_name or not isinstance(arguments, dict):
            return None
        return {
            "type": self.tool_type,
            "server_id": server_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "contract_ref": str(value.get("contract_ref") or ""),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        del call, context
        content = json.dumps(
            {
                "ok": False,
                "status": "unavailable",
                "reason": "invoke_mcp_target_not_resolved",
                "recommendedAction": "Use capability_list to find an available exact ID, then capability_load and capability_invoke with its current contract_ref.",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "mcp_invocation",
                    "status": "unavailable",
                    "reason": "invoke_mcp_target_not_resolved",
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
        )


class McpManageToolHandler(BaseToolHandler):
    tool_type = "mcp_manage"

    def __init__(self, *, service: Any, approval_store: Any = None, config_base_dir: Any = None) -> None:
        self.service = service
        self.approval_store = approval_store
        self.config_base_dir = config_base_dir

    def tool_spec(self):
        return MCP_MANAGE_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": self.service is not None, "status": "ready" if self.service is not None else "unavailable"}

    def build_prompt_instruction(self) -> str:
        return (
            "- mcp_manage：主人可管理本 Host 的 MCP 连接。本地包先用 exec_run 安装，再 configure；"
            "安装或配置第三方 MCP 前，先用实时工具打开官方仓库或 registry，核对当前包/二进制/镜像、"
            "启动命令、认证方式和维护状态；不要只凭训练记忆或搜索摘要。"
            "configure 会先真实启动候选服务，成功才替换旧配置。remove 仅移除 Akane 连接并停止会话，"
            "不会卸载外部 npm/Python 包或删除源码。新连接默认按需加载；只有明确设置 pinned 才常驻指定工具。"
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
        for key in ("display_name", "catalog_description", "activation_mode", "transport", "command", "cwd", "url"):
            if key in value:
                normalized[key] = str(value.get(key) or "")
        for key in ("args", "pinned_tools", "low_risk_allowlist"):
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
        if action != "discover":
            gated = gate_extension_mutation(
                tool_type=self.tool_type,
                action=action,
                display_name="MCP 能力管理",
                effects=("mcp_config_mutation",),
                call=call,
                context=context,
                approval_store=self.approval_store,
                config_base_dir=self.config_base_dir,
            )
            if gated is not None:
                return gated
        if action == "configure":
            payload = {
                "displayName": call.get("display_name") or server_id,
                "catalogDescription": call.get("catalog_description") or "",
                "activationMode": call.get("activation_mode") or "on_demand",
                "pinnedTools": call.get("pinned_tools") or [],
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
