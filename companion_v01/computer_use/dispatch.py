"""Device preflight and one effect dispatch under the existing host policy."""
from dataclasses import replace
from hashlib import sha256
from .contracts import INPUT_ACTIONS, WORKFLOW_ACTIONS, uses_visual_coordinates
from .session import dispatch_control


def dispatch(engine, *, broker, spec, invocation, profile_user_id, session_id, client_context, request_context,
             effect_actions=INPUT_ACTIONS, approval_spec=None):
    """Return (broker result, approval/block tuple). Scope is installed by the caller."""
    from ..tool_orchestration_engine import _satellite_blocked_result, _satellite_ask_result, _create_satellite_approval_request
    from ..tool_handlers.core import ToolExecutionContext
    from ..capability_approval import build_approval_request_fingerprint
    from ..capcore_runtime import (authorization_profile_user_id, manual_permission_request,
                                   resolve_permission_for_profile, approval_policy_for_capability)
    kwargs = dict(spec=spec, receipt_value=invocation.execution_receipt, arguments=invocation.arguments,
                  ledger_scope=f"{profile_user_id}\x1f{session_id}",
                  policy_client_mode=getattr(getattr(client_context, "effective_mode", None), "value", ""))
    is_workflow = spec.capability_id == "computer_use" and invocation.arguments.get("action") in WORKFLOW_ACTIONS
    if is_workflow:
        kwargs["timeout_seconds"] = 30.0
    def execute(operation_id, phase):
        result = broker.execute(invocation_id=operation_id, **kwargs)
        if spec.capability_id != "computer_use":
            return result
        from ..executor_broker import BrokerExecutionResult
        data = dict(result.data)
        data.setdefault("operation_id", operation_id)
        data.setdefault("device_epoch", (invocation.execution_receipt or {}).get("lease_epoch", ""))
        data["dispatch_phase"] = phase
        return BrokerExecutionResult(status=result.status, reason=getattr(result, "reason", ""),
            model_feedback=getattr(result, "model_feedback", ""), data=data)
    if invocation.arguments.get("action") not in effect_actions:
        return execute(invocation.id, "execute"), None
    def has_visual_context():
        target = (request_context or {}).get("_model_execution_target")
        return getattr(target, "role", "") == "vision" or getattr(target, "reason", "") == "chat_supports_images"
    visual_reference = "screenshot_id" in invocation.arguments or (
        spec.capability_id == "computer_use" and "context_ref" in invocation.arguments and uses_visual_coordinates(invocation.arguments))
    if visual_reference and not has_visual_context():
        return None, _satellite_blocked_result(spec, invocation, "coordinate_requires_visual_model")
    checked_spec = approval_spec or spec
    context = ToolExecutionContext(profile_user_id=profile_user_id, session_id=session_id,
        now_ts=0, visual_payload={}, request_context=dict(request_context or {}))
    principal = authorization_profile_user_id(context)
    base_dir = getattr(engine, "capability_config_base_dir", None)
    mode = approval_policy_for_capability(base_dir=base_dir, profile_user_id=principal,
        capability_id=checked_spec.capability_id, family_id="ops").default_mode
    if mode == "disabled":
        return None, _satellite_blocked_result(checked_spec, invocation, "capability_disabled_by_policy")
    # Policy is authenticated transport metadata, never a model argument or a
    # persistent device grant. Every invocation (including resume) resolves it anew.
    policy_metadata = {"permission_mode": mode}
    token = dispatch_control.set({"phase": "prepare", **policy_metadata})
    try:
        prepared = execute("prepare_" + sha256(invocation.id.encode()).hexdigest(), "prepare")
    finally:
        dispatch_control.reset(token)
    prepared.data["permission_mode"] = mode
    if prepared.status != "succeeded":
        return prepared, None
    if prepared.data.get("workflow_kind") == "visual_actions" and not has_visual_context():
        return None, _satellite_blocked_result(spec, invocation, "coordinate_requires_visual_model")
    authorization = prepared.data.get("authorization") or {}
    binding = authorization.get("binding")
    if not isinstance(binding, str) or len(binding) != 64 or not isinstance(authorization.get("required"), bool):
        return None, _satellite_blocked_result(spec, invocation, "device_preflight_invalid")
    def approval_check(authorization):
        binding = authorization.get("binding")
        if not isinstance(binding, str) or len(binding) != 64 or type(authorization.get("required")) is not bool:
            return _satellite_blocked_result(spec, invocation, "device_preflight_invalid")
        preview = {"binding": binding, **dict(authorization.get("preview") or {})}
        request = manual_permission_request(context=context, required=authorization["required"],
            capability_id=checked_spec.capability_id, display_name="这一步桌面操作", risk="high", confirm="always",
            effects=checked_spec.effects, reason="desktop_action_requires_specific_approval", args_preview=preview)
        request = replace(request, args_preview=preview)
        decision = resolve_permission_for_profile(request, base_dir=base_dir, profile_user_id=principal, family_id="ops")
        if not decision.allowed and not decision.requires_user_decision:
            return _satellite_blocked_result(checked_spec, invocation, decision.reason)
        if decision.mode != mode:
            return _satellite_blocked_result(checked_spec, invocation, "desktop_permission_changed")
        if decision.allowed:
            return None
        fingerprint = build_approval_request_fingerprint(preview)
        device = str((invocation.execution_receipt or {}).get("instance_id") or "")
        store = getattr(engine, "approval_store", None)
        grant = store.resolve_grant(profile_user_id=profile_user_id, session_id=session_id,
            capability_id=checked_spec.capability_id, action_id=checked_spec.capability_id, resource="", device=device,
            fingerprint=fingerprint, authorization_profile_user_id=principal) if store is not None else None
        if grant is None:
            request_id = _create_satellite_approval_request(store, spec=checked_spec, decision=decision,
                fingerprint=fingerprint,device_id=device,profile_user_id=profile_user_id,session_id=session_id,
                authorization_profile_user_id=principal) if store is not None else ""
            return _satellite_ask_result(checked_spec,invocation,decision,request_id=request_id,fingerprint=fingerprint)
        return None

    stopped = approval_check(authorization)
    if stopped is not None:
        return None, stopped
    token = dispatch_control.set({"phase":"execute", "approval_binding":binding, **policy_metadata})
    try:
        executed = execute(invocation.id, "execute")
    finally:
        dispatch_control.reset(token)
    executed.data["permission_mode"] = mode
    # A future step is approved only after reaching its real observed state.
    # Keep completed-step facts and the final image when surfacing that approval.
    if is_workflow and executed.data.get("workflow_state") == "awaiting_approval":
        stopped = approval_check(executed.data.get("authorization") or {})
        if stopped is not None:
            from .media import extract_images
            data = dict(executed.data)
            data["screenshots"] = [dict(item) for item in data.get("screenshots", []) if isinstance(item, dict)]
            result, envelope = stopped
            try:
                result.model_image_inputs = extract_images(data)
            except (ValueError, TypeError, OSError):
                data["observation_state"] = "failed"
                data["reason"] = "invalid_screenshot_payload"
            from .presentation import feedback
            result.followup_context += "\n流程已停在具体审批步骤；获批后使用 resume_steps，不要重新 run_steps/run_actions。\n" + feedback(data)
            envelope.data["result"] = data
            return None, stopped
    return executed, None
