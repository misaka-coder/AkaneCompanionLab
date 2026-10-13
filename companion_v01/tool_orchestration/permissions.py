"""Trusted channel admission and argument-bound approval for desktop actions."""

from __future__ import annotations

from typing import Any, Mapping

import config

from ..client_protocol import ClientProtocolContext
from ..tool_handlers.core import ToolExecutionContext, ToolExecutionResult
from ..tool_invocation import ToolInvocation, ToolResultEnvelope


def _satellite_channel_rejection(
    spec: Any,
    invocation: ToolInvocation,
    request_context: dict[str, Any] | None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope] | None:
    """Allow QQ device access only for the authenticated owner sender.

    Group storage identity is not an authorization principal. The delivery
    context is supplied by the QQ ingress, never by model tool arguments.
    Owners may request access in either conversation kind; other members and
    actorless continuations fail closed. Existing capability gates still apply.
    """
    delivery = request_context.get("qq_delivery_context") if isinstance(request_context, dict) else None
    if not isinstance(delivery, dict):
        return None
    is_group = bool(delivery.get("is_group"))
    owner_qq = str(getattr(config, "MASTER_QQ", "") or "").strip()
    sender_qq = str(delivery.get("user_id") or "").strip()
    if owner_qq.isdigit() and sender_qq == owner_qq:
        return None
    tool_id = spec.capability_id
    reason = "device_action_requires_owner"
    event = {
        "type": "capability_execution_result",
        "tool_type": tool_id,
        "status": "blocked",
        "reason": reason,
    }
    feedback = (
        "这条群聊消息不是来自已配置的主人账号，不能读取或操控绑定电脑；其他群成员不能继承主人的权限，本次没有执行任何操作。"
        if is_group
        else "这条私聊消息不是来自已配置的主人账号，不能读取或操控绑定电脑；本次没有执行任何操作。"
    )
    result = ToolExecutionResult(
        tool_type=tool_id,
        stream_events=[event],
        followup_context=feedback,
        state_updates={
            "capability_execution": {
                "tool_type": tool_id,
                "status": "blocked",
                "reason": reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="error",
        model_feedback=feedback,
        data={"code": reason, "tool": tool_id, "status": "blocked"},
        events=[event],
    )
    return result, envelope


def _satellite_permission_gate(
    engine: Any,
    *,
    spec: Any,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    client_context: ClientProtocolContext | None,
    request_context: dict[str, Any] | None = None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope] | None:
    """Return an ask/blocked result for high-risk satellite specs, else None.

    Only high-risk satellite specs (currently ``system_process_terminate``) are
    gated. The grant is bound to capability/action plus a request fingerprint of
    the exact arguments and redeemed through the shared engine approval store,
    so an approved termination never silently covers a different pid.
    """
    if str(getattr(spec, "risk", "") or "").strip().lower() != "high":
        return None
    from ..capability_approval import build_approval_request_fingerprint
    from ..capcore_runtime import (
        authorization_profile_user_id,
        manual_permission_request,
        resolve_permission_for_profile,
    )

    client_mode = ""
    if client_context is not None:
        client_mode = str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or "")
    context = ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode=client_mode,
        request_context=dict(request_context or {}),
    )
    arguments = dict(invocation.arguments or {})
    authorization_profile = authorization_profile_user_id(context)
    request = manual_permission_request(
        context=context,
        required=True,
        capability_id=spec.capability_id,
        display_name=str(getattr(spec, "display_name", "") or spec.capability_id),
        risk="high",
        confirm="always",
        effects=tuple(getattr(spec, "effects", ()) or ()),
        reason="high_risk_satellite_requires_confirmation",
        args_preview=arguments,
    )
    base_dir = getattr(engine, "capability_config_base_dir", None) or getattr(config, "DATA_DIR", "users_data")
    decision = resolve_permission_for_profile(
        request,
        base_dir=base_dir,
        profile_user_id=authorization_profile,
        family_id="ops",
    )
    if decision.allowed:
        return None
    if not decision.requires_user_decision:
        return _satellite_blocked_result(spec, invocation, str(decision.reason or "capability_disabled_by_policy"))

    fingerprint = build_approval_request_fingerprint(arguments)
    receipt = invocation.execution_receipt if isinstance(invocation.execution_receipt, Mapping) else {}
    device_id = str(receipt.get("instance_id") or "").strip()
    if not device_id:
        # No live receipt means no device action can be dispatched. Let the
        # broker return its structured unavailable result without bothering
        # the user with an approval request that cannot be executed.
        return None
    approval_store = getattr(engine, "approval_store", None)
    if approval_store is not None:
        grant = approval_store.resolve_grant(
            profile_user_id=profile_user_id,
            session_id=session_id,
            capability_id=spec.capability_id,
            action_id=spec.capability_id,
            resource="",
            device=device_id,
            fingerprint=fingerprint,
            authorization_profile_user_id=authorization_profile,
        )
        if grant is not None:
            return None
        request_id = _create_satellite_approval_request(
            approval_store,
            spec=spec,
            decision=decision,
            fingerprint=fingerprint,
            device_id=device_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            authorization_profile_user_id=authorization_profile,
        )
        return _satellite_ask_result(spec, invocation, decision, request_id=request_id, fingerprint=fingerprint)
    return _satellite_ask_result(spec, invocation, decision, request_id="", fingerprint=fingerprint)


def _create_satellite_approval_request(
    approval_store: Any,
    *,
    spec: Any,
    decision: Any,
    fingerprint: str,
    device_id: str,
    profile_user_id: str,
    session_id: str,
    authorization_profile_user_id: str,
) -> str:
    preview = dict(getattr(getattr(decision, "request", None), "args_preview", None) or {})
    result = approval_store.create_request(
        profile_user_id=profile_user_id,
        session_id=session_id,
        payload={
            "capabilityId": spec.capability_id,
            "actionId": spec.capability_id,
            "risk": "high",
            "approvalMode": "ask_each_time",
            "title": f"{getattr(spec, 'display_name', '') or spec.capability_id}需要确认",
            "summary": f"Akane 想在你的电脑上执行{getattr(spec, 'display_name', '') or spec.capability_id}。",
            "approvalReason": "high_risk_satellite_requires_confirmation",
            "payloadPreview": preview,
            "requestFingerprint": fingerprint,
            "deviceId": device_id,
            "authorizationProfileUserId": authorization_profile_user_id,
        },
    )
    if not result.get("ok"):
        return ""
    return str(result.get("requestId") or "")


def _satellite_ask_result(
    spec: Any,
    invocation: ToolInvocation,
    decision: Any,
    *,
    request_id: str,
    fingerprint: str,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    from ..capcore_runtime import approval_required_event

    tool_id = spec.capability_id
    event = approval_required_event(
        capability_id=tool_id,
        action_id=tool_id,
        title=f"{getattr(spec, 'display_name', '') or tool_id}需要确认",
        summary=f"Akane 想在你的电脑上执行{getattr(spec, 'display_name', '') or tool_id}。",
        client_mode="",
        decision=decision,
    )
    if request_id:
        event["requestId"] = request_id
    if fingerprint:
        event["requestFingerprint"] = fingerprint
    feedback = (
        f"这个{getattr(spec, 'display_name', '') or tool_id}需要用户确认后才能执行；"
        "请自然说明需要用户在能力审批中允许后再执行，不要声称已经完成。"
    )
    result = ToolExecutionResult(
        tool_type=tool_id,
        stream_events=[event],
        followup_context=feedback,
        state_updates={
            "capability_execution": {
                "tool_type": tool_id,
                "status": "approval_required",
                "reason": "requires_user_decision",
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ask",
        model_feedback=feedback,
        data={"tool": tool_id, "status": "approval_required"},
        events=[event],
    )
    return result, envelope


def _satellite_blocked_result(
    spec: Any,
    invocation: ToolInvocation,
    reason: str,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    tool_id = spec.capability_id
    clean_reason = str(reason or "capability_disabled_by_policy")
    event = {
        "type": "capability_execution_result",
        "tool_type": tool_id,
        "status": "blocked",
        "reason": clean_reason,
    }
    feedback = f"{getattr(spec, 'display_name', '') or tool_id}已被当前能力策略阻止（{clean_reason}）。请自然说明无法执行，不要假装已经完成。"
    result = ToolExecutionResult(
        tool_type=tool_id,
        stream_events=[event],
        followup_context=feedback,
        state_updates={
            "capability_execution": {
                "tool_type": tool_id,
                "status": "blocked",
                "reason": clean_reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="error",
        model_feedback=feedback,
        data={"code": clean_reason, "tool": tool_id, "status": "blocked"},
        events=[event],
    )
    return result, envelope
