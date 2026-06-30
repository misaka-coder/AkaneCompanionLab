from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from capcore import (
    ApprovalPolicy,
    InvocationContext,
    PermissionDecision,
    PermissionRequest,
    permission_request_from_mapping,
    sanitize_permission_preview,
)
from capcore import resolve_permission as capcore_resolve_permission

from .local_capability_config import get_approval_policy_config


APPROVAL_POLICY_MODES = {"ask_each_time", "trusted_auto_allow", "disabled"}


def invocation_context_from_execution(context: Any) -> InvocationContext:
    return InvocationContext(
        profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
        session_id=str(getattr(context, "session_id", "") or ""),
        client_mode=str(getattr(context, "client_mode", "") or ""),
    )


def approval_policy_for_profile(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> ApprovalPolicy:
    try:
        payload = get_approval_policy_config(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
        )
    except Exception:
        return ApprovalPolicy(default_mode="ask_each_time")
    policy = payload.get("approvalPolicy") if isinstance(payload, Mapping) else {}
    mode = str((policy or {}).get("defaultMode") or "").strip()
    if mode not in APPROVAL_POLICY_MODES:
        mode = "ask_each_time"
    return ApprovalPolicy(default_mode=mode)


def resolve_permission_for_profile(
    request: PermissionRequest,
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> PermissionDecision:
    return capcore_resolve_permission(
        request,
        approval_policy_for_profile(base_dir=base_dir, profile_user_id=profile_user_id),
    )


def manual_permission_request(
    *,
    context: Any,
    required: bool,
    capability_id: str,
    display_name: str,
    risk: str,
    confirm: str,
    effects: tuple[str, ...],
    reason: str,
    args_preview: Mapping[str, Any] | None = None,
) -> PermissionRequest:
    invocation_context = invocation_context_from_execution(context)
    request = permission_request_from_mapping(
        {
            "id": str(capability_id or ""),
            "name": str(display_name or ""),
            "risk": str(risk or "medium"),
            "confirm": str(confirm or "first_time"),
            "effects": [str(effect or "") for effect in effects],
        },
        args_preview or {},
        invocation_context,
        default_risk="medium",
        default_confirm="first_time",
        default_visible_in=("base",),
        default_prompt_exposed=False,
    )
    return replace(
        request,
        required=bool(required),
        reason=str(reason or request.reason or ""),
    )


def approval_required_event(
    *,
    capability_id: str,
    action_id: str,
    title: str,
    summary: str,
    client_mode: str,
    decision: PermissionDecision | None = None,
    risk: str = "",
    approval_mode: str = "",
    approval_reason: str = "",
    payload_preview: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request = decision.request if decision is not None else None
    resolved_risk = str(risk or getattr(request, "risk", "") or "medium").strip().lower()
    if resolved_risk != "high":
        resolved_risk = "medium"
    preview = payload_preview
    if preview is None:
        preview = getattr(request, "args_preview", None) if request is not None else None
    return {
        "type": "capability_approval_required",
        "capabilityId": str(capability_id or ""),
        "actionId": str(action_id or capability_id or ""),
        "title": str(title or "能力需要确认"),
        "summary": str(summary or "Akane 想执行一个需要确认的能力动作。"),
        "risk": resolved_risk,
        "approvalMode": str(approval_mode or getattr(decision, "mode", "") or "ask_each_time"),
        "approvalReason": str(approval_reason or getattr(decision, "reason", "") or "requires_confirmation"),
        "payloadPreview": sanitize_permission_preview(preview),
        "client_mode": str(client_mode or ""),
    }
