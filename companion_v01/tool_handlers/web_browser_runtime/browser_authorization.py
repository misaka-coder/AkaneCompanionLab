"""Authorization before browser control or accepted dialogs."""

from __future__ import annotations

from typing import Any, Mapping

from ...capcore_runtime import authorization_profile_user_id as capcore_authorization_profile_user_id, approval_required_event as capcore_approval_required_event, manual_permission_request as capcore_manual_permission_request, resolve_permission_for_profile as capcore_resolve_permission_for_profile
from ..core import ToolExecutionContext, ToolExecutionResult


class BrowserAuthorizationMixin:
    def _authorize_control_action(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        action_id = f"{self.CONTROL_ACTION_ID_PREFIX}.{action}"
        request = self._browser_control_permission_request(action=action, call=call, context=context)
        if self._approval_checker_allows(action_id=action_id, call=call, context=context):
            return {"ok": True, "mode": "approval_grant"}
        decision = capcore_resolve_permission_for_profile(
            request,
            base_dir=self.config_base_dir,
            profile_user_id=capcore_authorization_profile_user_id(context),
            family_id="ops",
        )
        if decision.allowed:
            return {"ok": True, "mode": decision.mode, "reason": decision.reason}
        return {
            "ok": False,
            "status": "approval_required",
            "approvalMode": decision.mode,
            "capabilityId": "tool.browser_page",
            "actionId": action_id,
            "risk": "high",
            "reason": str(decision.reason or "browser_control_requires_approval"),
        }

    def _browser_control_permission_request(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        return capcore_manual_permission_request(
            context=context,
            required=True,
            capability_id="tool.browser_page",
            display_name="Browser Page",
            risk="high",
            confirm="always",
            effects=("browser_action",),
            reason="browser_control_requires_approval",
            args_preview={
                "action": str(action or ""),
                **self._safe_control_preview(call),
            },
        )

    def _approval_checker_allows(
        self, *, action_id: str, call: Mapping[str, Any], context: ToolExecutionContext
    ) -> bool:
        if not callable(self.approval_checker):
            return False
        try:
            return bool(
                self.approval_checker(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    capability_id="tool.browser_page",
                    action_id=action_id,
                    call=dict(call),
                    request_context=dict(context.request_context or {}),
                )
            )
        except Exception:
            return False

    def _safe_control_preview(self, call: Mapping[str, Any]) -> dict[str, Any]:
        preview: dict[str, Any] = {}
        selector = self._clip(self._sanitize_output(str(call.get("selector") or "")), 120)
        ref = self._clip(self._sanitize_output(str(call.get("ref") or "")), 40)
        candidate_index = self._coerce_int(call.get("candidate_index"), minimum=0, maximum=30, default=0)
        if selector:
            preview["selector"] = selector
        if ref:
            preview["ref"] = ref
        if candidate_index > 0:
            preview["candidateIndex"] = candidate_index
        if call.get("coordinate"):
            preview["coordinate"] = list(call.get("coordinate"))
        if call.get("screenshot_id"):
            preview["screenshot_id"] = str(call.get("screenshot_id") or "")
        if call.get("key"):
            preview["key"] = str(call.get("key") or "")
        if call.get("text"):
            preview["textLength"] = len(str(call.get("text") or ""))
        return preview

    def _approval_required(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
        authorization: Mapping[str, Any],
    ) -> ToolExecutionResult:
        preview = {
            "action": action,
            **self._safe_control_preview(call),
        }
        event = capcore_approval_required_event(
            capability_id="tool.browser_page",
            action_id=str(authorization.get("actionId") or f"browser_page.{action}"),
            title="浏览器控制需要确认",
            summary="Akane 想对托管网页执行点击、输入或按键动作。",
            risk="high",
            approval_mode="ask_each_time",
            approval_reason=str(authorization.get("reason") or "browser_control_requires_approval"),
            payload_preview=preview,
            client_mode=context.client_mode,
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=(
                "浏览器控制动作需要用户确认：当前没有有效授权或“完全访问”策略。"
                "请自然告诉用户需要在能力审批中允许后再执行；不要声称已经点击、输入或按键。"
            ),
            state_updates={
                "browser_control_status": "approval_required",
                "browser_control_action": action,
            },
        )
