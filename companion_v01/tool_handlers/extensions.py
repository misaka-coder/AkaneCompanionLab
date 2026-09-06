"""Owner-controlled installed extension lifecycle tool."""

from __future__ import annotations

import json
from typing import Any

import config

from ..extension_specs import MANAGE_EXTENSION_TOOL_SPEC
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

    def __init__(self, *, service: Any, approval_store: Any = None, config_base_dir: Any = None) -> None:
        self.service = service
        self.approval_store = approval_store
        self.config_base_dir = config_base_dir

    def tool_spec(self):
        return MANAGE_EXTENSION_TOOL_SPEC

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {"enabled": self.service is not None, "status": "ready" if self.service is not None else "unavailable"}

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_extension：market 浏览已配置市场，stage_market 携条目 plugin_id 和 sha256（digest）获取并校验候选；"
            "先查看运行依赖，再确认暂存结果的完整权限安装。源码项目先 test_source，再暂存并探测。安装由宿主发布并激活。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lower()
        if action not in {
            "list",
            "market",
            "stage_market",
            "test_source",
            "stage_source",
            "stage_wheel",
            "install",
            "discard_stage",
            "enable",
            "disable",
            "rollback",
            "uninstall",
        }:
            return None
        plugin_id = str(value.get("plugin_id") or "").strip()
        if action in {"stage_market", "enable", "disable", "rollback", "uninstall"} and not plugin_id:
            return None
        normalized = {"type": self.tool_type, "action": action}
        if plugin_id:
            normalized["plugin_id"] = plugin_id
        if action == "stage_market":
            digest = str(value.get("digest") or "")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                return None
            normalized["digest"] = digest
        if action in {"test_source", "stage_source", "stage_wheel"}:
            path = str(value.get("path") or "").strip()
            if not path:
                return None
            normalized["path"] = path
        if action in {"install", "discard_stage"}:
            stage_id = str(value.get("stage_id") or "").strip()
            if not stage_id:
                return None
            normalized["stage_id"] = stage_id
        if action == "install":
            permissions = value.get("approved_permissions")
            if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
                return None
            normalized["approved_permissions"] = [str(item).strip() for item in permissions]
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
        action = str(call.get("action") or "")
        if action not in {"list", "market"}:
            gated = gate_extension_mutation(
                tool_type=self.tool_type,
                action=action,
                display_name="插件管理",
                effects=("plugin_state_mutation",),
                call=call,
                context=context,
                approval_store=self.approval_store,
                config_base_dir=self.config_base_dir,
            )
            if gated is not None:
                return gated
        return _result(
            dict(
                self.service.execute_sync(
                    action=action,
                    plugin_id=str(call.get("plugin_id") or ""),
                    path=str(call.get("path") or ""),
                    stage_id=str(call.get("stage_id") or ""),
                    approved_permissions=tuple(call.get("approved_permissions") or ()),
                    **({"digest": call["digest"]} if action == "stage_market" else {}),
                )
            )
        )


__all__ = ["ManageExtensionToolHandler"]
