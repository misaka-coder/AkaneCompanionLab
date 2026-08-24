"""Generic provider-native access to Akane's explicit OneBot chat surface."""

from __future__ import annotations

import json
from typing import Any

from ..onebot_model_actions import MODEL_ONEBOT_ACTION_NAMES, model_onebot_capabilities
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


ONEBOT_MODEL_PARAMS_MAX_BYTES = 512 * 1024


class OneBotActionToolHandler(BaseToolHandler):
    tool_type = "onebot_action"
    policy_accepted_native_tool = True

    def __init__(self, *, delivery_port: Any | None = None) -> None:
        self.delivery_port = delivery_port

    def bind_delivery_port(self, delivery_port: Any | None) -> None:
        self.delivery_port = delivery_port

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = str(value.get("action") or "").strip().lstrip("/")
        if action not in MODEL_ONEBOT_ACTION_NAMES:
            return None
        params = value.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return None
        return {"type": self.tool_type, "action": action, "params": dict(params)}

    def build_prompt_instruction(self) -> str:
        return (
            "- onebot_action：在 QQ 会话中调用显式开放的 OneBot 交互。简单动作直接调用；"
            "不确定 action 参数时先用 action=capabilities。普通参与者只能操作当前群或当前私聊，"
            "语义明确时可省略当前群、当前私聊对象、当前发送者或当前消息 ID；"
            "跨会话动作与消息撤回需要主人；工具会返回真实状态和完整可用结果。"
        )

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "").strip()
        params = dict(call.get("params") or {})
        if str(context.client_mode or "").strip().lower() != "qq_text":
            return self._result(
                {"ok": False, "status": "unavailable", "reason": "qq_only", "action": action}
            )
        try:
            params_bytes = len(json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            return self._result(
                {"ok": False, "status": "invalid", "reason": "params_not_json", "action": action}
            )
        if params_bytes > ONEBOT_MODEL_PARAMS_MAX_BYTES:
            return self._result(
                {
                    "ok": False,
                    "status": "invalid",
                    "reason": "params_too_large",
                    "action": action,
                    "max_bytes": ONEBOT_MODEL_PARAMS_MAX_BYTES,
                }
            )
        if action == "capabilities":
            return self._result(model_onebot_capabilities())
        if self.delivery_port is None:
            return self._result(
                {
                    "ok": False,
                    "status": "unavailable",
                    "reason": "qq_delivery_port_unavailable",
                    "action": action,
                }
            )
        try:
            outcome = dict(
                self.delivery_port.call_onebot_action(
                    request_context=context.request_context,
                    action=action,
                    params=params,
                )
            )
        except Exception:
            outcome = {
                "ok": False,
                "status": "failed",
                "reason": "qq_onebot_transport_exception",
                "action": action,
            }
        return self._result(outcome)

    def _result(self, payload: dict[str, Any]) -> ToolExecutionResult:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content = serialized if bool(payload.get("ok")) else f"<tool_use_error>{serialized}</tool_use_error>"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=False,
                complete=True,
            ),
            trace_receipt={
                "action": str(payload.get("action") or "unknown"),
                "status": str(payload.get("status") or ("success" if payload.get("ok") else "failed")),
            },
        )
