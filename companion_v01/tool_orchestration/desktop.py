"""Broker dispatch and honest feedback for bound desktop capabilities."""

from __future__ import annotations

import json
from typing import Any

from ..capability_registry import OPEN_BROWSER_TOOL_SPEC
from ..client_protocol import ClientProtocolContext
from ..tool_handlers.core import ToolExecutionResult
from ..tool_invocation import ToolInvocation, ToolResultEnvelope
from .permissions import _satellite_channel_rejection, _satellite_permission_gate
from .results import broker_constraint_result


def _execute_open_browser_with_broker(
    engine: Any,
    *,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    client_context: ClientProtocolContext | None = None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    broker = getattr(engine, "executor_broker", None)
    if broker is None:
        broker_result = None
    else:
        broker_result = broker.execute(
            spec=OPEN_BROWSER_TOOL_SPEC,
            receipt_value=invocation.execution_receipt,
            invocation_id=invocation.id,
            arguments=invocation.arguments,
            ledger_scope=f"{profile_user_id}\x1f{session_id}",
            policy_client_mode=getattr(getattr(client_context, "effective_mode", None), "value", ""),
        )
    constrained = broker_constraint_result(invocation=invocation, broker_result=broker_result)
    if constrained is not None:
        return constrained
    status = str(getattr(broker_result, "status", "unavailable_before_dispatch") or "").strip()
    reason = str(getattr(broker_result, "reason", "executor_broker_unavailable") or "").strip()
    model_feedback = str(getattr(broker_result, "model_feedback", "") or "").strip()
    if not model_feedback:
        model_feedback = (
            "当前没有可用的桌面执行器，请直接说明这次没有打开网页。"
            if status != "succeeded"
            else "已在用户绑定的电脑上真实打开公开网页；不要声称读取了页面内容。"
        )
    event = {
        "type": "capability_execution_result",
        "tool_type": OPEN_BROWSER_TOOL_SPEC.capability_id,
        "status": status,
    }
    if reason:
        event["reason"] = reason
    result = ToolExecutionResult(
        tool_type=OPEN_BROWSER_TOOL_SPEC.capability_id,
        stream_events=[event],
        followup_context=model_feedback,
        state_updates={
            "capability_execution": {
                "tool_type": OPEN_BROWSER_TOOL_SPEC.capability_id,
                "status": status,
                "reason": reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok" if status == "succeeded" else "error",
        model_feedback=model_feedback,
        data={
            "code": reason or status,
            "tool": OPEN_BROWSER_TOOL_SPEC.capability_id,
            "status": status,
        },
        events=[event],
    )
    return result, envelope


def _execute_satellite_with_broker(
    engine: Any,
    *,
    spec: Any,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    client_context: ClientProtocolContext | None = None,
    request_context: dict[str, Any] | None = None,
    preauthorized: bool = False,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    rejected = None if preauthorized else _satellite_channel_rejection(spec, invocation, request_context)
    if rejected is not None:
        return rejected
    gated = None if preauthorized else _satellite_permission_gate(
        engine,
        spec=spec,
        invocation=invocation,
        profile_user_id=profile_user_id,
        session_id=session_id,
        client_context=client_context,
        request_context=request_context,
    )
    if gated is not None:
        return gated
    broker_result, stopped = _dispatch_satellite(
        engine,
        spec=spec,
        invocation=invocation,
        profile_user_id=profile_user_id,
        session_id=session_id,
        client_context=client_context,
        request_context=request_context,
    )
    if stopped is not None:
        return stopped
    constrained = broker_constraint_result(invocation=invocation, broker_result=broker_result)
    if constrained is not None:
        return constrained
    return _satellite_result(
        engine,
        spec=spec,
        invocation=invocation,
        broker_result=broker_result,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )


def _dispatch_satellite(
    engine: Any,
    *,
    spec: Any,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    client_context: ClientProtocolContext | None,
    request_context: dict[str, Any] | None,
) -> tuple[Any, tuple[ToolExecutionResult, ToolResultEnvelope] | None]:
    """Restore the dispatch scope even when device execution stops or raises."""
    broker = getattr(engine, "executor_broker", None)
    from ..computer_use.session import dispatch_scope, scope_key

    scope_token = dispatch_scope.set(scope_key(profile_user_id, session_id))
    try:
        if spec.capability_id == "computer_use" and broker is not None:
            from ..computer_use.dispatch import dispatch

            broker_result, stopped = dispatch(
                engine,
                broker=broker,
                spec=spec,
                invocation=invocation,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                request_context=request_context,
            )
            if stopped is not None:
                return None, stopped
        else:
            broker_result = (
                broker.execute(
                    spec=spec,
                    receipt_value=invocation.execution_receipt,
                    invocation_id=invocation.id,
                    arguments=invocation.arguments,
                    ledger_scope=f"{profile_user_id}\x1f{session_id}",
                    policy_client_mode=getattr(getattr(client_context, "effective_mode", None), "value", ""),
                )
                if broker is not None
                else None
            )
    finally:
        dispatch_scope.reset(scope_token)
    return broker_result, None


def _satellite_result(
    engine: Any,
    *,
    spec: Any,
    invocation: ToolInvocation,
    broker_result: Any,
    profile_user_id: str,
    session_id: str,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    """Project actual device data, screenshots, and completion evidence."""
    status = str(getattr(broker_result, "status", "unavailable_before_dispatch") or "").strip()
    reason = str(getattr(broker_result, "reason", "executor_broker_unavailable") or "").strip()
    data = dict(getattr(broker_result, "data", {}) or {}) if broker_result is not None else {}
    model_feedback = str(getattr(broker_result, "model_feedback", "") or "").strip()
    artifact_events = []
    model_image_inputs = []
    if spec.capability_id == "computer_use":
        from ..computer_use.media import extract_images
        from ..computer_use.outcome import complete_outcome

        data, status, reason = complete_outcome(
            data, status=status, reason=reason, action=invocation.arguments.get("action")
        )
        try:
            model_image_inputs = extract_images(data)
        except (ValueError, TypeError, OSError):
            data["ok"] = False
            data["observation_state"] = "failed"
            data["reason"] = "invalid_screenshot_payload"
            status, reason = "failed", "invalid_screenshot_payload"
        model_feedback = "桌面工具实际结果如下；动作执行状态不代表任务完成。"
    if spec.capability_id == "desktop_screenshot":
        if status == "succeeded":
            try:
                from ..desktop_screenshot import register_desktop_screenshot

                artifact_event, model_image_inputs = register_desktop_screenshot(
                    engine, data, profile_user_id=profile_user_id, session_id=session_id
                )
                artifact_events.append(artifact_event)
                model_feedback = (
                    "已截取绑定电脑主屏幕。图片已登记，若用户要求接收图片，请用 send_file 发送返回的 generated_handle。"
                )
                if not model_image_inputs:
                    model_feedback += "本轮未附加可视图片，不能声称已经看清内容；可以使用 inspect_image 查看该句柄。"
            except Exception:
                status, reason = "failed", "screenshot_registration_failed"
                data = {}
                model_feedback = "桌面截图未能登记为可用图片，本次没有完成截图交付。"
        data.pop("imageBase64", None)
    if not model_feedback:
        if status == "succeeded":
            model_feedback = f"已从用户绑定电脑读取或执行了 {spec.display_name}，以下是实际返回结果。"
        else:
            model_feedback = f"当前无法使用用户电脑上的{spec.display_name}，请如实说明没有完成。"
    if data and (status in {"succeeded", "execution_unknown"} or spec.capability_id == "computer_use"):
        if spec.capability_id == "computer_use":
            from ..computer_use.presentation import feedback

            model_feedback = feedback(data)
        else:
            model_feedback = f"{model_feedback}\n实际返回数据：{json.dumps(data, ensure_ascii=False, sort_keys=True)}"
    event = {
        "type": "capability_execution_result",
        "tool_type": spec.capability_id,
        "status": status,
    }
    if reason:
        event["reason"] = reason
    result = ToolExecutionResult(
        tool_type=spec.capability_id,
        stream_events=[*artifact_events, event],
        followup_context=model_feedback,
        model_image_inputs=model_image_inputs,
        state_updates={
            "capability_execution": {
                "tool_type": spec.capability_id,
                "status": status,
                "reason": reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok" if status == "succeeded" else "error",
        model_feedback=model_feedback,
        data={
            "code": reason or status,
            "tool": spec.capability_id,
            "status": status,
            "result": data,
        },
        events=[*artifact_events, event],
    )
    return result, envelope
