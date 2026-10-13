"""Execution policy, exact-argument grants, and approval receipts."""

from __future__ import annotations

from typing import (
    Any,
)

import config

from capcore import (
    PermissionDecision,
)

from ...capcore_runtime import (
    authorization_profile_user_id,
    approval_required_event,
    manual_permission_request,
    resolve_permission_for_profile,
)

from ...capability_approval import (
    build_approval_request_fingerprint,
)

from ..core import (
    ToolExecutionContext,
    ToolExecutionResult,
)


class ExecutionApprovalPromptMixin:
    """Shared approval receipts and group-member explanations."""

    def _approval_required(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        *,
        request_id: str = "",
        fingerprint: str = "",
    ) -> ToolExecutionResult:
        approval_reason, title, summary, followup = self._approval_explanation(context)
        event = approval_required_event(
            capability_id=self.tool_type,
            action_id=self.tool_type,
            title=title,
            summary=summary,
            client_mode=str(context.client_mode or ""),
            decision=decision,
        )
        event["approvalReason"] = approval_reason
        if request_id:
            event["requestId"] = request_id
        if fingerprint:
            event["requestFingerprint"] = fingerprint
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=followup,
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "approval_required",
                    "reason": approval_reason,
                }
            },
        )

    @staticmethod
    def _approval_explanation(context: ToolExecutionContext) -> tuple[str, str, str, str]:
        profile_user_id = str(context.profile_user_id or "").strip()
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        actor_profile_user_id = str(request_context.get("actor_profile_user_id") or "").strip()
        shared_group_actor = (
            profile_user_id.startswith("qq_group_shared_")
            and actor_profile_user_id
            and actor_profile_user_id != profile_user_id
        )
        if shared_group_actor:
            return (
                "group_actor_host_execution_requires_confirmation",
                "群成员触发的宿主命令需要确认",
                "这条命令由当前群成员触发；群聊上下文不会让该成员继承设备主人的宿主权限。",
                "[approval required: 当前群成员没有自动执行宿主命令的授权；设备主人可重新发起，或批准这次请求]",
            )
        return (
            "command_execution_requires_confirmation",
            "执行命令需要确认",
            "Akane 想以宿主用户权限运行一条命令。",
            "[approval required: 请在能力审批中允许这次宿主命令]",
        )


class ExecutionApprovalMixin:
    """Command policy and one-shot grants; virtual hooks serve input too."""

    def _ask_or_redeem(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        call: dict[str, Any],
        *,
        workspace_id: str = "",
    ) -> ToolExecutionResult:
        """Redeem a previously approved grant, else create an approval request.

        The grant is bound to capability/action, resource (cwd), device
        (provider) and a request fingerprint of the exact arguments, so an
        approval for one command never silently covers a different one.
        """
        fingerprint = self._fingerprint(call)
        resource = str(call.get("cwd") or "").strip()
        device = str(getattr(self.execution_provider, "provider_id", "local") or "local")
        if self.approval_store is not None:
            grant = self.approval_store.resolve_grant(
                profile_user_id=str(context.profile_user_id or ""),
                session_id=str(context.session_id or ""),
                capability_id=self.tool_type,
                action_id=self.tool_type,
                resource=resource,
                device=device,
                fingerprint=fingerprint,
                authorization_profile_user_id=authorization_profile_user_id(context),
            )
            if grant is not None:
                return self._execute_command(
                    self.execution_provider,
                    call,
                    context,
                    workspace_id=workspace_id,
                )
        request_id = self._create_approval_request(context, decision, fingerprint, resource, device)
        return self._approval_required(context, decision, request_id=request_id, fingerprint=fingerprint)

    def _fingerprint(self, call: dict[str, Any]) -> str:
        arguments: dict[str, Any] = {}
        for key in ("command", "cwd", "timeout_seconds", "initial_wait_seconds", "interactive"):
            if call.get(key) is not None:
                arguments[key] = call.get(key)
        if call.get("input_resources") is not None:
            arguments["input_resources"] = call.get("input_resources")
        if call.get("output_globs") is not None:
            arguments["output_globs"] = call.get("output_globs")
        return build_approval_request_fingerprint(arguments)

    def _create_approval_request(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        fingerprint: str,
        resource: str,
        device: str,
    ) -> str:
        if self.approval_store is None:
            return ""
        approval_reason, title, summary, _followup = self._approval_explanation(context)
        request = decision.request
        preview = dict(getattr(request, "args_preview", None) or {}) if request is not None else {}
        result = self.approval_store.create_request(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
            payload={
                "capabilityId": self.tool_type,
                "actionId": self.tool_type,
                "risk": "high",
                "approvalMode": "ask_each_time",
                "title": title,
                "summary": summary,
                "approvalReason": approval_reason,
                "payloadPreview": preview,
                "requestFingerprint": fingerprint,
                "resource": resource,
                "deviceId": device,
                "authorizationProfileUserId": authorization_profile_user_id(context),
            },
        )
        if not result.get("ok"):
            return ""
        return str(result.get("requestId") or "")

    def _permission_decision(self, context: ToolExecutionContext, args_preview: dict[str, Any]) -> PermissionDecision:
        request = manual_permission_request(
            context=context,
            required=True,
            capability_id=self.tool_type,
            display_name=self.tool_type,
            risk="high",
            confirm="always",
            effects=("command_exec",),
            reason="command_execution_requires_confirmation",
            args_preview=args_preview,
        )
        return resolve_permission_for_profile(
            request,
            base_dir=self.config_base_dir or getattr(config, "DATA_DIR", "users_data"),
            profile_user_id=authorization_profile_user_id(context),
            family_id="ops",
        )
