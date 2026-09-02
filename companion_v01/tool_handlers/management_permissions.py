"""Shared approval gate for persistent capability-management mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import config

from ..capability_approval import build_approval_request_fingerprint
from ..capcore_runtime import (
    approval_required_event,
    authorization_profile_user_id,
    manual_permission_request,
    resolve_permission_for_profile,
)
from .core import ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


def gate_extension_mutation(
    *,
    tool_type: str,
    action: str,
    display_name: str,
    effects: tuple[str, ...],
    call: Mapping[str, Any],
    context: ToolExecutionContext,
    approval_store: Any = None,
    config_base_dir: Path | str | None = None,
) -> ToolExecutionResult | None:
    """Allow, ask, or block one exact persistent extension mutation.

    ``None`` means execution may continue.  Grants remain bound to the precise
    capability, action, conversation and normalized argument fingerprint.
    """

    action_id = f"{tool_type}.{str(action or '').strip()}"
    arguments = {key: value for key, value in dict(call or {}).items() if key != "type"}
    fingerprint = build_approval_request_fingerprint(arguments)
    request = manual_permission_request(
        context=context,
        required=True,
        capability_id=tool_type,
        display_name=display_name,
        risk="high",
        confirm="always",
        effects=effects,
        reason="extension_management_requires_confirmation",
        args_preview=arguments,
    )
    decision = resolve_permission_for_profile(
        request,
        base_dir=config_base_dir or getattr(config, "DATA_DIR", "users_data"),
        profile_user_id=authorization_profile_user_id(context),
        family_id="extensions",
    )
    if decision.allowed:
        return None
    if not decision.requires_user_decision:
        return _gate_result(
            tool_type=tool_type,
            status="blocked",
            reason=str(decision.reason or "capability_management_disabled"),
            content="能力管理已关闭，本次没有修改已安装能力。主人可用 /access extensions ask 或 /access extensions on 调整。",
        )

    if approval_store is not None:
        grant = approval_store.resolve_grant(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
            capability_id=tool_type,
            action_id=action_id,
            fingerprint=fingerprint,
            authorization_profile_user_id=authorization_profile_user_id(context),
        )
        if grant is not None:
            return None

    request_id = ""
    if approval_store is not None:
        created = approval_store.create_request(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
            payload={
                "capabilityId": tool_type,
                "actionId": action_id,
                "risk": "high",
                "approvalMode": "ask_each_time",
                "title": f"{display_name}需要确认",
                "summary": f"Akane 想执行 {action_id}。",
                "approvalReason": str(decision.reason or "requires_confirmation"),
                "payloadPreview": arguments,
                "requestFingerprint": fingerprint,
                "authorizationProfileUserId": authorization_profile_user_id(context),
            },
        )
        if created.get("ok"):
            request_id = str(created.get("requestId") or "")

    event = approval_required_event(
        capability_id=tool_type,
        action_id=action_id,
        title=f"{display_name}需要确认",
        summary=f"Akane 想执行 {action_id}。",
        client_mode=context.client_mode,
        decision=decision,
        payload_preview=arguments,
    )
    if request_id:
        event["requestId"] = request_id
    content = (
        f"{display_name}的 {action} 操作需要主人批准；审批请求已经创建。"
        "QQ 可用 /approve 批准，控制中心可在待审批卡片处理。批准或拒绝后宿主会自动续接原会话。"
        if request_id
        else f"{display_name}的 {action} 操作需要批准，但当前没有创建出可处理的审批请求。"
    )
    return ToolExecutionResult(
        tool_type=tool_type,
        stream_events=[event],
        followup_context=content,
        followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
        state_updates={
            "capability_execution": {
                "tool_type": tool_type,
                "action_id": action_id,
                "status": "approval_required",
                "request_id": request_id,
            }
        },
    )


def _gate_result(*, tool_type: str, status: str, reason: str, content: str) -> ToolExecutionResult:
    event = {
        "type": "capability_execution_blocked",
        "capabilityId": tool_type,
        "status": status,
        "reason": reason,
    }
    return ToolExecutionResult(
        tool_type=tool_type,
        stream_events=[event],
        followup_context=content,
        followup_envelope=ToolFollowupEnvelope(content=content, producer_bounded=True, complete=True),
        state_updates={
            "capability_execution": {
                "tool_type": tool_type,
                "status": status,
                "reason": reason,
            }
        },
    )


__all__ = ["gate_extension_mutation"]
