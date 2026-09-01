"""Owner-controlled installed extension lifecycle tool."""

from __future__ import annotations

import json
from typing import Any

import config

from ..extension_specs import MANAGE_EXTENSION_TOOL_SPEC
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
        tool_type="manage_extension",
        stream_events=[
            {
                "type": "extension_management",
                "status": str(payload.get("status") or "error"),
                "plugin_id": str(payload.get("plugin_id") or ""),
                "reason": str(payload.get("reason") or ""),
            }
        ],
        followup_context=content,
        followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
    )


class ManageExtensionToolHandler(BaseToolHandler):
    tool_type = "manage_extension"

    def __init__(self, *, service: Any) -> None:
        self.service = service

    def tool_spec(self):
        return MANAGE_EXTENSION_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": self.service is not None, "status": "ready" if self.service is not None else "unavailable"}

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_extension：主人可查看、持久启停或重启当前 Host 已安装的插件。"
            "它不会搜索市场或下载缺失插件；V1 的 restart 是插件宿主级重启。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        if action not in {"list", "enable", "disable", "restart"}:
            return None
        plugin_id = str(value.get("plugin_id") or "").strip()
        if action in {"enable", "disable"} and not plugin_id:
            return None
        normalized = {"type": self.tool_type, "action": action}
        if plugin_id:
            normalized["plugin_id"] = plugin_id
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if not _can_manage(context):
            return _result(
                {
                    "ok": False,
                    "status": "permission_denied",
                    "reason": "extension_management_requires_owner",
                }
            )
        return _result(
            dict(
                self.service.execute_sync(
                    action=str(call.get("action") or ""),
                    plugin_id=str(call.get("plugin_id") or ""),
                )
            )
        )


__all__ = ["ManageExtensionToolHandler"]
