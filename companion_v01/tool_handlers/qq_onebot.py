"""Generic provider-native access to Akane's explicit OneBot chat surface."""

from __future__ import annotations

import json
from typing import Any

from ..onebot_model_actions import (
    MODEL_ONEBOT_ACTION_NAMES,
    model_onebot_action_is_user_visible,
    model_onebot_capabilities,
)
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope
from ..tool_continuation import bind_result_followup


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
        selector = value.get("message_selector")
        if selector is not None and not isinstance(selector, dict):
            return None
        normalized = {"type": self.tool_type, "action": action, "params": dict(params)}
        if isinstance(selector, dict):
            normalized["message_selector"] = dict(selector)
        if "finish_turn" in value:
            if not isinstance(value["finish_turn"], bool):
                return None
            normalized["finish_turn"] = value["finish_turn"]
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "").strip()
        params = dict(call.get("params") or {})
        message_selector = call.get("message_selector")
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
                    message_selector=dict(message_selector) if isinstance(message_selector, dict) else None,
                )
            )
        except Exception:
            outcome = {
                "ok": False,
                "status": "failed",
                "reason": "qq_onebot_transport_exception",
                "action": action,
            }
        result = self._result(outcome)
        return bind_result_followup(result, context=context, default="auto",
            allow_end=call.get("finish_turn") is True,
            delivery_managed=outcome.get("ok") is True and outcome.get("status") == "success"
                and model_onebot_action_is_user_visible(action))

    def _result(self, payload: dict[str, Any]) -> ToolExecutionResult:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content = serialized if bool(payload.get("ok")) else f"<tool_use_error>{serialized}</tool_use_error>"
        action = str(payload.get("action") or "unknown")
        status = str(payload.get("status") or ("success" if payload.get("ok") else "failed"))
        visible_receipts: list[dict[str, Any]] = []
        if bool(payload.get("ok")) and model_onebot_action_is_user_visible(action):
            receipt: dict[str, Any] = {
                "type": "qq_visible_action_receipt",
                "action": action,
                "status": status,
                "ok": True,
            }
            data = payload.get("data")
            if isinstance(data, dict) and data.get("message_id") not in (None, ""):
                receipt["message_id"] = data.get("message_id")
            visible_receipts.append(receipt)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=visible_receipts,
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=False,
                complete=True,
            ),
            trace_receipt={
                "action": action,
                "status": status,
            },
        )
