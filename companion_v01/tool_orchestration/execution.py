"""Admission and dispatch to the selected desktop or server implementation."""

from __future__ import annotations

from typing import Any

from ..capability_registry import ExecutorBroker, OPEN_BROWSER_TOOL_SPEC
from ..client_protocol import ClientProtocolContext
from ..desktop_satellite_specs import desktop_satellite_spec
from ..tool_handlers.core import TaskExecutionScope, ToolExecutionContext, ToolExecutionResult
from ..tool_invocation import ToolInvocation, ToolResultEnvelope, ValidationResult, invocation_to_legacy_tool_call
from .desktop import _execute_open_browser_with_broker, _execute_satellite_with_broker
from .normalization import parse_tool_call
from .validation import PreparedToolInvocation, prepare_tool_invocation, validate_tool_invocation
from .results import _final_exec_envelope, validation_failure_result, server_local_broker_result


def execute_tool_call(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    tool_call: dict[str, Any],
    visual_payload: dict[str, Any],
    now_ts: int,
    current_user_source_id: str = "",
    client_context: ClientProtocolContext | None = None,
    memory_exclude_source_ids: list[str] | None = None,
    request_context: dict[str, Any] | None = None,
    domain_profile_id: str = "",
) -> Any | None:
    invocation = parse_tool_call(tool_call)
    if invocation is None:
        return None
    result, _envelope = execute_tool_invocation(
        engine,
        invocation=invocation,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        visual_payload=visual_payload,
        now_ts=now_ts,
        current_user_source_id=current_user_source_id,
        client_context=client_context,
        memory_exclude_source_ids=memory_exclude_source_ids,
        request_context=request_context,
        domain_profile_id=domain_profile_id,
        raw_tool_call=tool_call,
    )
    return result


def execute_tool_invocation(
    engine: Any,
    *,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    visual_payload: dict[str, Any],
    now_ts: int,
    current_user_source_id: str = "",
    client_context: ClientProtocolContext | None = None,
    memory_exclude_source_ids: list[str] | None = None,
    request_context: dict[str, Any] | None = None,
    domain_profile_id: str = "",
    raw_tool_call: Any = None,
    prepared: PreparedToolInvocation | None = None,
) -> tuple[Any | None, ToolResultEnvelope]:
    from ..tool_scheduling import DIRECT_TASK_TOOLS
    prepared = prepared or prepare_tool_invocation(
        engine,
        invocation,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        raw_tool_call=raw_tool_call,
        domain_profile_id=domain_profile_id,
        for_execution=True,
    )
    invocation = prepared.invocation
    if prepared.terminal_result is not None:
        result = prepared.terminal_result
        execution = result.state_updates.get("capability_execution", {})
        reason = str(execution.get("reason") or "capability_not_available")
        return result, ToolResultEnvelope(
            invocation_id=invocation.id,
            status="unavailable" if reason in {"capability_not_available", "executor_runtime_changed"} else "rejected",
            model_feedback=result.followup_context,
            data={"code": reason, "tool": invocation.name, "executed": False},
            events=result.stream_events,
        )
    rejected = _admit_invocation(
        engine,
        invocation=invocation,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
        prepared=prepared,
    )
    if rejected is not None:
        return rejected

    selected = prepared.handler
    normalized_call = prepared.normalized_call
    if selected is None or normalized_call is None:
        validation = ValidationResult.fail(
            "not_available",
            f"工具「{invocation.name}」没有可执行的已准备调用，本次没有执行。",
        )
        return validation_failure_result(invocation=invocation, validation=validation)
    execution_context = _build_execution_context(
        invocation=invocation, profile_user_id=profile_user_id, session_id=session_id,
        character_pack_id=character_pack_id, visual_payload=visual_payload, now_ts=now_ts,
        current_user_source_id=current_user_source_id, client_context=client_context,
        memory_exclude_source_ids=memory_exclude_source_ids, request_context=request_context)
    runtime = getattr(engine, "host_tool_jobs", None)
    control = getattr(runtime, "task_control", None)
    if callable(control):
        result = control(tool_type=invocation.name, call=normalized_call, context=execution_context)
        if result is not None:
            return result, _final_exec_envelope(invocation=invocation, result=result)
    if invocation.name == OPEN_BROWSER_TOOL_SPEC.capability_id:
        if callable(getattr(runtime, "invoke", None)):
            result = runtime.invoke(handler=selected, capability_id=invocation.name, invocation_id=invocation.id,
                call=normalized_call, context=execution_context, background=invocation.background,
                dispatch=lambda context, call, handler: _execute_open_browser_with_broker(
                    engine, invocation=invocation, profile_user_id=profile_user_id, session_id=session_id,
                    client_context=client_context)[0])
            return result, _final_exec_envelope(invocation=invocation, result=result)
        return _execute_open_browser_with_broker(
            engine,
            invocation=invocation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
        )
    satellite_spec = desktop_satellite_spec(invocation.name)
    if satellite_spec is not None:
        from .permissions import _satellite_channel_rejection, _satellite_permission_gate
        gated = _satellite_channel_rejection(satellite_spec, invocation, request_context)
        if gated is None:
            gated = _satellite_permission_gate(engine, spec=satellite_spec, invocation=invocation,
                profile_user_id=profile_user_id, session_id=session_id, client_context=client_context,
                request_context=request_context)
        if gated is not None:
            return gated
        if callable(getattr(runtime, "invoke", None)):
            result = runtime.invoke(handler=selected, capability_id=invocation.name, invocation_id=invocation.id,
                call=normalized_call, context=execution_context, background=invocation.background,
                dispatch=lambda context, call, handler: _execute_satellite_with_broker(
                    engine, spec=satellite_spec, invocation=invocation,
                    profile_user_id=profile_user_id, session_id=session_id, client_context=client_context,
                    request_context=context.request_context, preauthorized=True)[0])
            return result, _final_exec_envelope(invocation=invocation, result=result)
        return _execute_satellite_with_broker(
            engine,
            spec=satellite_spec,
            invocation=invocation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            request_context=request_context,
            preauthorized=True,
        )
    handler = selected

    if callable(getattr(runtime, "invoke", None)) and invocation.name not in DIRECT_TASK_TOOLS:
        result = runtime.invoke(handler=handler, capability_id=invocation.name, invocation_id=invocation.id,
            call=normalized_call, context=execution_context, domain_profile_id=domain_profile_id,
            background=invocation.background,
            dispatch=lambda context, call, selected: _execute_server_local(
                engine, handler=selected, invocation=invocation, normalized_call=call,
                execution_context=context, domain_profile_id=domain_profile_id,
                request_context=context.request_context, admitted=True)[0])
        return result, _final_exec_envelope(invocation=invocation, result=result)
    return _execute_server_local(
        engine,
        handler=handler,
        invocation=invocation,
        normalized_call=normalized_call,
        execution_context=execution_context,
        domain_profile_id=domain_profile_id,
        request_context=request_context,
    )


def _admit_invocation(
    engine: Any,
    *,
    invocation: ToolInvocation,
    client_context: ClientProtocolContext | None,
    profile_user_id: str,
    session_id: str,
    domain_profile_id: str,
    prepared: PreparedToolInvocation | None = None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope] | None:
    """Validate the resolved current tool and pair every rejection."""
    validation = validate_tool_invocation(
        engine,
        invocation,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
        prepared=prepared,
    )
    if not validation.ok:
        return validation_failure_result(invocation=invocation, validation=validation)
    return None


def _build_execution_context(
    *,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    visual_payload: dict[str, Any],
    now_ts: int,
    current_user_source_id: str,
    client_context: ClientProtocolContext | None,
    memory_exclude_source_ids: list[str] | None,
    request_context: dict[str, Any] | None,
) -> ToolExecutionContext:
    """Copy request inputs and carry trusted scope, target, and cancellation."""
    enriched_visual_payload = dict(visual_payload or {})
    enriched_visual_payload["_profile_user_id"] = profile_user_id
    enriched_visual_payload["_character_pack_id"] = str(character_pack_id or "")
    if memory_exclude_source_ids:
        enriched_visual_payload["_memory_retrieval_exclude_source_ids"] = list(memory_exclude_source_ids)
    client_mode = ""
    if client_context is not None:
        client_mode = str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or "")
    from ..turn_coordination import current_cancellation_check

    from ..dialogue_targets import trusted_target
    from ..tool_scheduling import freeze_request_value

    execution_context = ToolExecutionContext(
        invocation_id=invocation.id,
        capability_selection=invocation.capability_selection,
        cancel_requested=current_cancellation_check(),
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=now_ts,
        visual_payload=freeze_request_value(enriched_visual_payload),
        character_pack_id=str(character_pack_id or ""),
        current_user_source_id=current_user_source_id,
        client_mode=client_mode,
        request_context=freeze_request_value(dict(request_context or {})),
        dialogue_target=trusted_target(request_context),
        execution_scope=(
            request_context.get("_task_execution_scope")
            if isinstance((request_context or {}).get("_task_execution_scope"), TaskExecutionScope)
            else None
        ),
    )
    return execution_context


def _execute_server_local(
    engine: Any,
    *,
    handler: Any,
    invocation: ToolInvocation,
    normalized_call: dict[str, Any],
    execution_context: ToolExecutionContext,
    domain_profile_id: str,
    request_context: dict[str, Any] | None,
    admitted: bool = False,
) -> tuple[Any | None, ToolResultEnvelope]:
    """Admit long jobs or retain a binding through policy hooks and dispatch."""
    profile_user_id = execution_context.profile_user_id
    session_id = execution_context.session_id
    host_tool_jobs = getattr(engine, "host_tool_jobs", None)
    if not admitted and host_tool_jobs is not None and host_tool_jobs.accepts(
        handler=handler,
        context=execution_context,
    ):
        result = host_tool_jobs.submit(
            capability_id=invocation.name,
            invocation_id=invocation.id,
            call=normalized_call,
            context=execution_context,
            domain_profile_id=domain_profile_id,
            handler=handler,
            turn_id=str((request_context or {}).get("_host_origin_turn_id") or ""),
        )
        return result, _final_exec_envelope(invocation=invocation, result=result)
    broker = getattr(engine, "executor_broker", None)
    if broker is None:
        broker = ExecutorBroker(None)
        try:
            setattr(engine, "executor_broker", broker)
        except Exception:
            pass
    from ..tool_batch import cancelled_tool_result
    from ..execution_resource_policy import tool_input_arguments
    from ..execution_policies import policy_tool_spec

    # Match long-task admission: keep the selected implementation alive across
    # asynchronous policy hooks and publication, with provider revocation intact.
    retain = getattr(handler, "retain_invocation", None)
    retained = callable(retain)
    if retained:
        handler = retain()
    try:
        broker_result = broker.execute_server_local(
            tool_id=invocation.name,
            invocation_id=invocation.id,
            dispatch=lambda: (
                cancelled_tool_result(invocation.name)
                if (invocation.name != "exec_cancel" and execution_context.cancel_requested is not None
                    and execution_context.cancel_requested())
                else (getattr(handler, "execute_admitted", handler.execute) if admitted else handler.execute)(
                    call=normalized_call,
                    context=execution_context,
                )
            ),
            ledger_scope=(
                f"{profile_user_id}\x1f{session_id}\x1f{execution_context.execution_scope.task_id}"
                if execution_context.execution_scope is not None and execution_context.execution_scope.task_id
                else f"{profile_user_id}\x1f{session_id}"
            ),
            request_data={"arguments": normalized_call},
            input_arguments=tool_input_arguments(handler, normalized_call),
            policy_spec=policy_tool_spec(handler),
            policy_client_mode=execution_context.client_mode,
        )
    finally:
        release = getattr(handler, "release_invocation", None)
        if retained and callable(release):
            release()
    return server_local_broker_result(invocation=invocation, broker_result=broker_result)
