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

from .local_capability_config import approval_mode_for_capability, load_capability_config


APPROVAL_POLICY_MODES = {"ask_each_time", "trusted_auto_allow", "disabled"}


def tool_identity_rejection(descriptor: Any, context: Any) -> str:
    """Enforce a declared caller restriction before approval or dispatch.

    The principal is supplied by host ingress, including scoped continuations;
    neither conversation storage identity nor business arguments grant access.
    Approval can authorize an action but cannot change its requesting actor.
    """
    raw = getattr(descriptor, "raw", {})
    if not isinstance(raw, Mapping):
        return "tool_owner_only_invalid"
    owner_only = raw.get("owner_only", False)
    if type(owner_only) is not bool:
        return "tool_owner_only_invalid"
    if not owner_only:
        return ""
    mode = str(getattr(context, "client_mode", "") or "")
    if not isinstance(context, InvocationContext) and mode in {"qq", "qq_text"}:
        request = getattr(context, "request_context", None)
        if not isinstance(request, Mapping) or not request.get("actor_profile_user_id"):
            return "tool_owner_required"
    actor = (str(getattr(context, "authorization_profile_user_id", "") or "").strip()
             if isinstance(context, InvocationContext) else authorization_profile_user_id(context))
    if getattr(context, "global_scope", False) or not actor:
        return "tool_owner_required"
    import config

    if mode in {"qq", "qq_text"}:
        # QQ ingress already maps the configured MASTER_QQ to this principal.
        owner = "master"
    elif mode in {"desktop_pet", "scene_static", "scene_live2d", "web"}:
        owner = str(getattr(config, "WEB_OWNER_PROFILE_USER_ID", "master") or "").strip()
    else:
        return "tool_owner_required"
    return "" if owner and actor == owner else "tool_owner_required"


def authorization_profile_user_id(context: Any) -> str:
    """Resolve the actor principal that owns host capability permissions.

    Shared QQ conversation storage belongs to ``qq_group_shared_*``. Host
    actions, however, must be authorized by the actor that requested them.
    A context without an actor field falls back to its conversation profile.
    An explicitly empty actor remains empty, including composed calls. Caller
    restrictions may require an actor even when ordinary approval can fall back.
    """

    request_context = getattr(context, "request_context", None)
    if isinstance(request_context, Mapping):
        if "actor_profile_user_id" in request_context:
            return str(request_context.get("actor_profile_user_id") or "").strip()
    return str(getattr(context, "profile_user_id", "") or "").strip()


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
    return approval_policy_for_capability(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        capability_id="",
    )


def approval_policy_for_capability(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    capability_id: str,
    family_id: str = "",
) -> ApprovalPolicy:
    """Resolve Akane's effective host policy before entering CapCore's gate."""

    try:
        payload = load_capability_config(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
        )
    except Exception:
        return ApprovalPolicy(default_mode="ask_each_time")
    policy = payload.get("approvalPolicy") if isinstance(payload, Mapping) else {}
    mode = approval_mode_for_capability(
        policy,
        str(capability_id or ""),
        family_id=str(family_id or ""),
    )
    if mode not in APPROVAL_POLICY_MODES:
        mode = "ask_each_time"
    return ApprovalPolicy(default_mode=mode)


def resolve_permission_for_profile(
    request: PermissionRequest,
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    family_id: str = "",
) -> PermissionDecision:
    return capcore_resolve_permission(
        request,
        approval_policy_for_capability(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            capability_id=str(getattr(request, "capability_id", "") or ""),
            family_id=str(family_id or ""),
        ),
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
