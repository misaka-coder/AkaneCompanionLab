"""JSON projections for the versioned PluginHost process boundary.

Only public CapCore values cross this boundary.  The codec deliberately does
not pickle adapters, plugin objects, paths, or host internals.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from capcore import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityResult,
    InvocationContext,
    TriggerConfig,
)

from .plugin_api import (
    NotificationIntent,
    NotificationResult,
    PluginAgentEventRequest,
    PluginAgentEventResult,
    PluginInvocationContext,
    PluginEventEnvelope,
    PluginExternalEvent,
    PluginDeliverySnapshot,
    PluginHookEnvelope,
    PluginOutboundDecoration,
    PluginOutboundPlanSnapshot,
    PluginQQCommandResult,
    PluginToolCallSnapshot,
    PluginToolResultSnapshot,
)
from .plugin_events import PluginEventDispatchResult
from .plugin_hooks import PluginHookDispatchResult
from .plugin_generation_event_payload import (
    PluginGenerationEventPayloadError,
    event_payload_from_wire,
    event_payload_to_wire,
)


class PluginGenerationCodecError(ValueError):
    """The public value cannot be represented by the JSON protocol."""


def capability_descriptor_to_wire(descriptor: CapabilityDescriptor) -> dict[str, Any]:
    if not isinstance(descriptor, CapabilityDescriptor):
        raise PluginGenerationCodecError("capability_descriptor_required")
    return _json_snapshot(
        {
            "id": descriptor.id,
            "display_name": descriptor.display_name,
            "short_hint": descriptor.short_hint,
            "visible_in": list(descriptor.visible_in),
            "prompt_exposed": descriptor.prompt_exposed,
            "risk": descriptor.risk,
            "confirm": descriptor.confirm,
            "effects": list(descriptor.effects),
            "trigger": _trigger_to_wire(descriptor.trigger),
            "inputs": [_slot_to_wire(slot) for slot in descriptor.inputs],
            "outputs": [_slot_to_wire(slot) for slot in descriptor.outputs],
            "raw": dict(descriptor.raw),
        }
    )


def capability_descriptor_from_wire(value: object) -> CapabilityDescriptor:
    record = _mapping(value, "capability_descriptor_invalid")
    risk = _string(record.get("risk"), "capability_descriptor_invalid")
    confirm = _string(record.get("confirm"), "capability_descriptor_invalid")
    if risk not in {"low", "medium", "high"}:
        raise PluginGenerationCodecError("capability_descriptor_invalid")
    if confirm not in {"never", "first_time", "always"}:
        raise PluginGenerationCodecError("capability_descriptor_invalid")
    raw = _optional_mapping(record.get("raw"))
    return CapabilityDescriptor(
        id=_required_string(record.get("id"), "capability_descriptor_invalid"),
        display_name=_string(record.get("display_name"), "capability_descriptor_invalid"),
        short_hint=_string(record.get("short_hint"), "capability_descriptor_invalid"),
        visible_in=_string_tuple(record.get("visible_in")),
        prompt_exposed=_boolean(record.get("prompt_exposed")),
        risk=risk,  # type: ignore[arg-type]
        confirm=confirm,  # type: ignore[arg-type]
        effects=_string_tuple(record.get("effects")),
        trigger=_trigger_from_wire(record.get("trigger")),
        inputs=_slots_from_wire(record.get("inputs")),
        outputs=_slots_from_wire(record.get("outputs")),
        raw=raw,
    )


def invocation_context_to_wire(context: InvocationContext) -> dict[str, str]:
    if not isinstance(context, InvocationContext):
        raise PluginGenerationCodecError("invocation_context_required")
    return {
        "profile_user_id": context.profile_user_id,
        "session_id": context.session_id,
        "client_mode": context.client_mode,
        "conversation_ref": str(getattr(context, "conversation_ref", "") or ""),
    }


def invocation_context_from_wire(value: object) -> InvocationContext:
    record = _mapping(value, "invocation_context_invalid")
    values = {
        "profile_user_id": _string(record.get("profile_user_id"), "invocation_context_invalid"),
        "session_id": _string(record.get("session_id"), "invocation_context_invalid"),
        "client_mode": _string(record.get("client_mode"), "invocation_context_invalid"),
    }
    conversation_ref = _string(record.get("conversation_ref"), "invocation_context_invalid")
    if conversation_ref:
        return PluginInvocationContext(**values, conversation_ref=conversation_ref)
    return InvocationContext(**values)


def capability_result_to_wire(result: CapabilityResult) -> dict[str, Any]:
    if not isinstance(result, CapabilityResult):
        raise PluginGenerationCodecError("capability_result_required")
    return _json_snapshot(result.as_dict())


def capability_result_from_wire(value: object) -> CapabilityResult:
    record = _mapping(value, "capability_result_invalid")
    is_error = record.get("is_error")
    if not isinstance(is_error, bool):
        raise PluginGenerationCodecError("capability_result_invalid")
    return CapabilityResult(
        is_error=is_error,
        content=record.get("content"),
        status=_string(record.get("status"), "capability_result_invalid"),
        reason=_string(record.get("reason"), "capability_result_invalid"),
    )


def notification_intent_to_wire(intent: NotificationIntent) -> dict[str, str]:
    if not isinstance(intent, NotificationIntent):
        raise PluginGenerationCodecError("notification_intent_required")
    return {
        "channel": _string(intent.channel, "notification_intent_invalid"),
        "recipient_id": _string(intent.recipient_id, "notification_intent_invalid"),
        "text": _string(intent.text, "notification_intent_invalid"),
        "idempotency_key": _string(
            intent.idempotency_key,
            "notification_intent_invalid",
        ),
    }


def notification_intent_from_wire(value: object) -> NotificationIntent:
    record = _mapping(value, "notification_intent_invalid")
    return NotificationIntent(
        channel=_string(record.get("channel"), "notification_intent_invalid"),
        recipient_id=_string(record.get("recipient_id"), "notification_intent_invalid"),
        text=_string(record.get("text"), "notification_intent_invalid"),
        idempotency_key=_string(
            record.get("idempotency_key"),
            "notification_intent_invalid",
        ),
    )


def notification_result_to_wire(result: NotificationResult) -> dict[str, Any]:
    if not isinstance(result, NotificationResult):
        raise PluginGenerationCodecError("notification_result_required")
    if not isinstance(result.ok, bool):
        raise PluginGenerationCodecError("notification_result_invalid")
    return {
        "ok": result.ok,
        "status": _string(result.status, "notification_result_invalid"),
        "reason": _string(result.reason, "notification_result_invalid"),
    }


def notification_result_from_wire(value: object) -> NotificationResult:
    record = _mapping(value, "notification_result_invalid")
    ok = record.get("ok")
    if not isinstance(ok, bool):
        raise PluginGenerationCodecError("notification_result_invalid")
    return NotificationResult(
        ok=ok,
        status=_string(record.get("status"), "notification_result_invalid"),
        reason=_string(record.get("reason"), "notification_result_invalid"),
    )


def agent_event_request_to_wire(request: PluginAgentEventRequest) -> dict[str, Any]:
    if not isinstance(request, PluginAgentEventRequest):
        raise PluginGenerationCodecError("agent_event_request_required")
    return _json_snapshot(
        {
            "trace_id": _string(request.trace_id, "agent_event_request_invalid"),
            "conversation_ref": _string(request.conversation_ref, "agent_event_request_invalid"),
            "message": _string(request.message, "agent_event_request_invalid"),
            "event": _external_event_to_wire(request.event, "agent_event_request_invalid"),
            "memory_idempotency_key": _string(
                request.memory_idempotency_key,
                "agent_event_request_invalid",
            ),
            "delivery": _string(request.delivery, "agent_event_request_invalid"),
            "text_delivery": _string(request.text_delivery, "agent_event_request_invalid"),
            "text_prefix": _string(request.text_prefix, "agent_event_request_invalid"),
            "text_suffix": _string(request.text_suffix, "agent_event_request_invalid"),
        }
    )


def agent_event_request_from_wire(value: object) -> PluginAgentEventRequest:
    record = _mapping(value, "agent_event_request_invalid")
    return PluginAgentEventRequest(
        trace_id=_string(record.get("trace_id"), "agent_event_request_invalid"),
        conversation_ref=_string(record.get("conversation_ref"), "agent_event_request_invalid"),
        message=_string(record.get("message"), "agent_event_request_invalid"),
        event=_external_event_from_wire(record.get("event"), "agent_event_request_invalid"),
        memory_idempotency_key=_string(
            record.get("memory_idempotency_key"),
            "agent_event_request_invalid",
        ),
        delivery=_string(record.get("delivery") or "timeline", "agent_event_request_invalid"),
        text_delivery=_string(record.get("text_delivery") or "default", "agent_event_request_invalid"),
        text_prefix=_string(record.get("text_prefix") or "", "agent_event_request_invalid"),
        text_suffix=_string(record.get("text_suffix") or "", "agent_event_request_invalid"),
    )


def agent_event_result_to_wire(result: PluginAgentEventResult) -> dict[str, Any]:
    if not isinstance(result, PluginAgentEventResult) or not isinstance(result.ok, bool):
        raise PluginGenerationCodecError("agent_event_result_invalid")
    return {
        "ok": result.ok,
        "status": _string(result.status, "agent_event_result_invalid"),
        "reason": _string(result.reason, "agent_event_result_invalid"),
        "delivery_status": _string(result.delivery_status, "agent_event_result_invalid"),
    }


def agent_event_result_from_wire(value: object) -> PluginAgentEventResult:
    record = _mapping(value, "agent_event_result_invalid")
    ok = record.get("ok")
    if not isinstance(ok, bool):
        raise PluginGenerationCodecError("agent_event_result_invalid")
    return PluginAgentEventResult(
        ok=ok,
        status=_string(record.get("status"), "agent_event_result_invalid"),
        reason=_string(record.get("reason"), "agent_event_result_invalid"),
        delivery_status=_string(record.get("delivery_status"), "agent_event_result_invalid"),
    )


def plugin_event_envelope_to_wire(event: PluginEventEnvelope) -> dict[str, Any]:
    if not isinstance(event, PluginEventEnvelope):
        raise PluginGenerationCodecError("plugin_event_envelope_required")
    if not isinstance(event.occurred_at, int) or isinstance(event.occurred_at, bool):
        raise PluginGenerationCodecError("plugin_event_envelope_invalid")
    try:
        payload = event_payload_to_wire(event.payload)
    except PluginGenerationEventPayloadError as exc:
        raise PluginGenerationCodecError(str(exc)) from exc
    return _json_snapshot(
        {
            "event_id": _string(event.event_id, "plugin_event_envelope_invalid"),
            "event_type": _string(event.event_type, "plugin_event_envelope_invalid"),
            "source": _string(event.source, "plugin_event_envelope_invalid"),
            "occurred_at": event.occurred_at,
            "subject": _string(event.subject, "plugin_event_envelope_invalid"),
            "fields": _string_pairs_to_wire(
                event.fields,
                "plugin_event_envelope_invalid",
            ),
            "material_handles": list(
                _string_tuple_value(
                    event.material_handles,
                    "plugin_event_envelope_invalid",
                )
            ),
            "payload": payload,
        }
    )


def plugin_event_envelope_from_wire(value: object) -> PluginEventEnvelope:
    record = _mapping(value, "plugin_event_envelope_invalid")
    occurred_at = record.get("occurred_at")
    if not isinstance(occurred_at, int) or isinstance(occurred_at, bool):
        raise PluginGenerationCodecError("plugin_event_envelope_invalid")
    try:
        payload = event_payload_from_wire(record.get("payload"))
    except PluginGenerationEventPayloadError as exc:
        raise PluginGenerationCodecError(str(exc)) from exc
    return PluginEventEnvelope(
        event_id=_string(record.get("event_id"), "plugin_event_envelope_invalid"),
        event_type=_string(record.get("event_type"), "plugin_event_envelope_invalid"),
        source=_string(record.get("source"), "plugin_event_envelope_invalid"),
        occurred_at=occurred_at,
        subject=_string(record.get("subject"), "plugin_event_envelope_invalid"),
        fields=_string_pairs_from_wire(
            record.get("fields"),
            "plugin_event_envelope_invalid",
        ),
        material_handles=_string_tuple_from_wire(
            record.get("material_handles"),
            "plugin_event_envelope_invalid",
        ),
        payload=payload,
    )


def plugin_event_dispatch_result_to_wire(
    result: PluginEventDispatchResult,
) -> dict[str, Any]:
    if not isinstance(result, PluginEventDispatchResult):
        raise PluginGenerationCodecError("plugin_event_result_required")
    if not isinstance(result.ok, bool) or not isinstance(result.request_agent_turn, bool):
        raise PluginGenerationCodecError("plugin_event_result_invalid")
    return _json_snapshot(
        {
            "ok": result.ok,
            "status": _string(result.status, "plugin_event_result_invalid"),
            "current_turn_events": [
                _external_event_to_wire(item, "plugin_event_result_invalid")
                for item in result.current_turn_events
            ],
            "timeline_events": [
                _external_event_to_wire(item, "plugin_event_result_invalid")
                for item in result.timeline_events
            ],
            "request_agent_turn": result.request_agent_turn,
            "failures": _string_pairs_to_wire(
                result.failures,
                "plugin_event_result_invalid",
            ),
        }
    )


def plugin_event_dispatch_result_from_wire(
    value: object,
) -> PluginEventDispatchResult:
    record = _mapping(value, "plugin_event_result_invalid")
    ok = record.get("ok")
    request_agent_turn = record.get("request_agent_turn")
    if not isinstance(ok, bool) or not isinstance(request_agent_turn, bool):
        raise PluginGenerationCodecError("plugin_event_result_invalid")
    current_turn = record.get("current_turn_events")
    timeline = record.get("timeline_events")
    if not isinstance(current_turn, list) or not isinstance(timeline, list):
        raise PluginGenerationCodecError("plugin_event_result_invalid")
    return PluginEventDispatchResult(
        ok=ok,
        status=_string(record.get("status"), "plugin_event_result_invalid"),
        current_turn_events=tuple(
            _external_event_from_wire(item, "plugin_event_result_invalid")
            for item in current_turn
        ),
        timeline_events=tuple(
            _external_event_from_wire(item, "plugin_event_result_invalid")
            for item in timeline
        ),
        request_agent_turn=request_agent_turn,
        failures=_string_pairs_from_wire(
            record.get("failures"),
            "plugin_event_result_invalid",
        ),
    )


def plugin_hook_envelope_to_wire(hook: PluginHookEnvelope) -> dict[str, Any]:
    if not isinstance(hook, PluginHookEnvelope):
        raise PluginGenerationCodecError("plugin_hook_envelope_required")
    if not isinstance(hook.occurred_at, int) or isinstance(hook.occurred_at, bool):
        raise PluginGenerationCodecError("plugin_hook_envelope_invalid")
    payload = hook.payload
    if isinstance(payload, PluginToolCallSnapshot):
        payload_type = "tool_call"
        payload_wire = {
            "invocation_id": _string(payload.invocation_id, "plugin_hook_payload_invalid"),
            "tool_name": _string(payload.tool_name, "plugin_hook_payload_invalid"),
            "source": _string(payload.source, "plugin_hook_payload_invalid"),
            "profile_user_id": _string(payload.profile_user_id, "plugin_hook_payload_invalid"),
            "session_id": _string(payload.session_id, "plugin_hook_payload_invalid"),
            "character_pack_id": _string(payload.character_pack_id, "plugin_hook_payload_invalid"),
            "arguments_json": _string(payload.arguments_json, "plugin_hook_payload_invalid"),
        }
    elif isinstance(payload, PluginToolResultSnapshot):
        payload_type = "tool_result"
        payload_wire = {
            "invocation_id": _string(payload.invocation_id, "plugin_hook_payload_invalid"),
            "tool_name": _string(payload.tool_name, "plugin_hook_payload_invalid"),
            "status": _string(payload.status, "plugin_hook_payload_invalid"),
            "duration_ms": _number(payload.duration_ms, "plugin_hook_payload_invalid"),
            "reason": _string(payload.reason, "plugin_hook_payload_invalid"),
            "model_feedback": _string(payload.model_feedback, "plugin_hook_payload_invalid"),
            "event_types": list(
                _string_tuple_value(payload.event_types, "plugin_hook_payload_invalid")
            ),
        }
    elif isinstance(payload, PluginOutboundPlanSnapshot):
        payload_type = "outbound_plan"
        payload_wire = {
            "delivery_id": _string(payload.delivery_id, "plugin_hook_payload_invalid"),
            "channel": _string(payload.channel, "plugin_hook_payload_invalid"),
            "action": _string(payload.action, "plugin_hook_payload_invalid"),
            "conversation_kind": _string(payload.conversation_kind, "plugin_hook_payload_invalid"),
            "target_id": _string(payload.target_id, "plugin_hook_payload_invalid"),
            "segment_types": list(
                _string_tuple_value(payload.segment_types, "plugin_hook_payload_invalid")
            ),
            "text": _string(payload.text, "plugin_hook_payload_invalid"),
            "reply_to_message_id": _string(
                payload.reply_to_message_id,
                "plugin_hook_payload_invalid",
            ),
            "text_decoratable": _boolean_value(
                payload.text_decoratable,
                "plugin_hook_payload_invalid",
            ),
        }
    elif isinstance(payload, PluginDeliverySnapshot):
        payload_type = "delivery"
        payload_wire = {
            "delivery_id": _string(payload.delivery_id, "plugin_hook_payload_invalid"),
            "channel": _string(payload.channel, "plugin_hook_payload_invalid"),
            "action": _string(payload.action, "plugin_hook_payload_invalid"),
            "conversation_kind": _string(payload.conversation_kind, "plugin_hook_payload_invalid"),
            "target_id": _string(payload.target_id, "plugin_hook_payload_invalid"),
            "segment_types": list(
                _string_tuple_value(payload.segment_types, "plugin_hook_payload_invalid")
            ),
            "status": _string(payload.status, "plugin_hook_payload_invalid"),
            "duration_ms": _number(payload.duration_ms, "plugin_hook_payload_invalid"),
            "reason": _string(payload.reason, "plugin_hook_payload_invalid"),
            "message_id": _string(payload.message_id, "plugin_hook_payload_invalid"),
        }
    else:
        raise PluginGenerationCodecError("plugin_hook_payload_invalid")
    return _json_snapshot(
        {
            "hook_id": _string(hook.hook_id, "plugin_hook_envelope_invalid"),
            "hook_type": _string(hook.hook_type, "plugin_hook_envelope_invalid"),
            "occurred_at": hook.occurred_at,
            "subject": _string(hook.subject, "plugin_hook_envelope_invalid"),
            "payload_type": payload_type,
            "payload": payload_wire,
        }
    )


def plugin_hook_envelope_from_wire(value: object) -> PluginHookEnvelope:
    record = _mapping(value, "plugin_hook_envelope_invalid")
    occurred_at = record.get("occurred_at")
    if not isinstance(occurred_at, int) or isinstance(occurred_at, bool):
        raise PluginGenerationCodecError("plugin_hook_envelope_invalid")
    payload_type = _string(record.get("payload_type"), "plugin_hook_envelope_invalid")
    payload_record = _mapping(record.get("payload"), "plugin_hook_payload_invalid")
    if payload_type == "tool_call":
        payload = PluginToolCallSnapshot(
            invocation_id=_string(payload_record.get("invocation_id"), "plugin_hook_payload_invalid"),
            tool_name=_string(payload_record.get("tool_name"), "plugin_hook_payload_invalid"),
            source=_string(payload_record.get("source"), "plugin_hook_payload_invalid"),
            profile_user_id=_string(payload_record.get("profile_user_id"), "plugin_hook_payload_invalid"),
            session_id=_string(payload_record.get("session_id"), "plugin_hook_payload_invalid"),
            character_pack_id=_string(payload_record.get("character_pack_id"), "plugin_hook_payload_invalid"),
            arguments_json=_string(payload_record.get("arguments_json"), "plugin_hook_payload_invalid"),
        )
    elif payload_type == "tool_result":
        payload = PluginToolResultSnapshot(
            invocation_id=_string(payload_record.get("invocation_id"), "plugin_hook_payload_invalid"),
            tool_name=_string(payload_record.get("tool_name"), "plugin_hook_payload_invalid"),
            status=_string(payload_record.get("status"), "plugin_hook_payload_invalid"),
            duration_ms=_number(payload_record.get("duration_ms"), "plugin_hook_payload_invalid"),
            reason=_string(payload_record.get("reason"), "plugin_hook_payload_invalid"),
            model_feedback=_string(payload_record.get("model_feedback"), "plugin_hook_payload_invalid"),
            event_types=_string_tuple_from_wire(payload_record.get("event_types"), "plugin_hook_payload_invalid"),
        )
    elif payload_type == "outbound_plan":
        text_decoratable = payload_record.get("text_decoratable")
        if not isinstance(text_decoratable, bool):
            raise PluginGenerationCodecError("plugin_hook_payload_invalid")
        payload = PluginOutboundPlanSnapshot(
            delivery_id=_string(payload_record.get("delivery_id"), "plugin_hook_payload_invalid"),
            channel=_string(payload_record.get("channel"), "plugin_hook_payload_invalid"),
            action=_string(payload_record.get("action"), "plugin_hook_payload_invalid"),
            conversation_kind=_string(payload_record.get("conversation_kind"), "plugin_hook_payload_invalid"),
            target_id=_string(payload_record.get("target_id"), "plugin_hook_payload_invalid"),
            segment_types=_string_tuple_from_wire(payload_record.get("segment_types"), "plugin_hook_payload_invalid"),
            text=_string(payload_record.get("text"), "plugin_hook_payload_invalid"),
            reply_to_message_id=_string(payload_record.get("reply_to_message_id"), "plugin_hook_payload_invalid"),
            text_decoratable=text_decoratable,
        )
    elif payload_type == "delivery":
        payload = PluginDeliverySnapshot(
            delivery_id=_string(payload_record.get("delivery_id"), "plugin_hook_payload_invalid"),
            channel=_string(payload_record.get("channel"), "plugin_hook_payload_invalid"),
            action=_string(payload_record.get("action"), "plugin_hook_payload_invalid"),
            conversation_kind=_string(payload_record.get("conversation_kind"), "plugin_hook_payload_invalid"),
            target_id=_string(payload_record.get("target_id"), "plugin_hook_payload_invalid"),
            segment_types=_string_tuple_from_wire(payload_record.get("segment_types"), "plugin_hook_payload_invalid"),
            status=_string(payload_record.get("status"), "plugin_hook_payload_invalid"),
            duration_ms=_number(payload_record.get("duration_ms"), "plugin_hook_payload_invalid"),
            reason=_string(payload_record.get("reason"), "plugin_hook_payload_invalid"),
            message_id=_string(payload_record.get("message_id"), "plugin_hook_payload_invalid"),
        )
    else:
        raise PluginGenerationCodecError("plugin_hook_payload_invalid")
    return PluginHookEnvelope(
        hook_id=_string(record.get("hook_id"), "plugin_hook_envelope_invalid"),
        hook_type=_string(record.get("hook_type"), "plugin_hook_envelope_invalid"),
        occurred_at=occurred_at,
        subject=_string(record.get("subject"), "plugin_hook_envelope_invalid"),
        payload=payload,
    )


def plugin_hook_dispatch_result_to_wire(result: PluginHookDispatchResult) -> dict[str, Any]:
    if not isinstance(result, PluginHookDispatchResult) or not isinstance(result.ok, bool):
        raise PluginGenerationCodecError("plugin_hook_result_invalid")
    if not isinstance(result.outbound_decorations, tuple):
        raise PluginGenerationCodecError("plugin_hook_result_invalid")
    decorations: list[list[Any]] = []
    for plugin_id, decoration in result.outbound_decorations:
        if not isinstance(decoration, PluginOutboundDecoration):
            raise PluginGenerationCodecError("plugin_hook_result_invalid")
        decorations.append(
            [
                _string(plugin_id, "plugin_hook_result_invalid"),
                {
                    "text_prefix": _string(decoration.text_prefix, "plugin_hook_result_invalid"),
                    "text_suffix": _string(decoration.text_suffix, "plugin_hook_result_invalid"),
                },
            ]
        )
    return _json_snapshot(
        {
            "ok": result.ok,
            "status": _string(result.status, "plugin_hook_result_invalid"),
            "diagnostics": _string_pairs_to_wire(result.diagnostics, "plugin_hook_result_invalid"),
            "failures": _string_pairs_to_wire(result.failures, "plugin_hook_result_invalid"),
            "outbound_decorations": decorations,
        }
    )


def plugin_hook_dispatch_result_from_wire(value: object) -> PluginHookDispatchResult:
    record = _mapping(value, "plugin_hook_result_invalid")
    ok = record.get("ok")
    if not isinstance(ok, bool):
        raise PluginGenerationCodecError("plugin_hook_result_invalid")
    raw_decorations = record.get("outbound_decorations")
    if not isinstance(raw_decorations, list):
        raise PluginGenerationCodecError("plugin_hook_result_invalid")
    decorations: list[tuple[str, PluginOutboundDecoration]] = []
    for item in raw_decorations:
        if not isinstance(item, list) or len(item) != 2:
            raise PluginGenerationCodecError("plugin_hook_result_invalid")
        decoration = _mapping(item[1], "plugin_hook_result_invalid")
        decorations.append(
            (
                _string(item[0], "plugin_hook_result_invalid"),
                PluginOutboundDecoration(
                    text_prefix=_string(decoration.get("text_prefix"), "plugin_hook_result_invalid"),
                    text_suffix=_string(decoration.get("text_suffix"), "plugin_hook_result_invalid"),
                ),
            )
        )
    return PluginHookDispatchResult(
        ok=ok,
        status=_string(record.get("status"), "plugin_hook_result_invalid"),
        diagnostics=_string_pairs_from_wire(record.get("diagnostics"), "plugin_hook_result_invalid"),
        failures=_string_pairs_from_wire(record.get("failures"), "plugin_hook_result_invalid"),
        outbound_decorations=tuple(decorations),
    )


def qq_command_dispatch_to_wire(
    *,
    command: object,
    args: object,
    qq_number: object,
    group_id: object,
    is_group: object,
    idempotency_key: object = "",
    sender_role: object = "",
    profile_user_id: object = "",
    session_id: object = "",
    character_pack_id: object = "",
    conversation_ref: object = "",
) -> dict[str, Any]:
    reason = "plugin_qq_command_request_invalid"
    payload = {
        "command": _string(command, reason),
        "args": _string(args, reason),
        "qq_number": _integer(qq_number, reason),
        "group_id": _integer(group_id, reason),
        "is_group": _boolean_value(is_group, reason),
        "idempotency_key": _string(idempotency_key, reason),
        "sender_role": _string(sender_role, reason),
        "profile_user_id": _string(profile_user_id, reason),
        "session_id": _string(session_id, reason),
        "character_pack_id": _string(character_pack_id, reason),
    }
    normalized_conversation_ref = _string(conversation_ref, reason)
    if normalized_conversation_ref:
        payload["conversation_ref"] = normalized_conversation_ref
    return _json_snapshot(payload)


def qq_command_dispatch_from_wire(value: object) -> dict[str, Any]:
    reason = "plugin_qq_command_request_invalid"
    record = _mapping(value, reason)
    payload = {
        "command": _string(record.get("command"), reason),
        "args": _string(record.get("args"), reason),
        "qq_number": _integer(record.get("qq_number"), reason),
        "group_id": _integer(record.get("group_id"), reason),
        "is_group": _boolean_value(record.get("is_group"), reason),
        "idempotency_key": _string(record.get("idempotency_key"), reason),
        "sender_role": _string(record.get("sender_role"), reason),
        "profile_user_id": _string(record.get("profile_user_id"), reason),
        "session_id": _string(record.get("session_id"), reason),
        "character_pack_id": _string(record.get("character_pack_id"), reason),
    }
    conversation_ref = _string(record.get("conversation_ref") or "", reason)
    if conversation_ref:
        payload["conversation_ref"] = conversation_ref
    return payload


def plugin_qq_command_result_to_wire(result: PluginQQCommandResult) -> dict[str, Any]:
    reason = "plugin_qq_command_result_invalid"
    if not isinstance(result, PluginQQCommandResult):
        raise PluginGenerationCodecError(reason)
    return _json_snapshot(
        {
            "handled": _boolean_value(result.handled, reason),
            "reply_text": _string(result.reply_text, reason),
            "reason": _string(result.reason, reason),
        }
    )


def plugin_qq_command_result_from_wire(value: object) -> PluginQQCommandResult:
    reason = "plugin_qq_command_result_invalid"
    record = _mapping(value, reason)
    return PluginQQCommandResult(
        handled=_boolean_value(record.get("handled"), reason),
        reply_text=_string(record.get("reply_text"), reason),
        reason=_string(record.get("reason"), reason),
    )


def json_snapshot(value: Any) -> Any:
    """Return an independent JSON value without imposing a size policy."""

    return _json_snapshot(value)


def _slot_to_wire(slot: CapabilityIOSlot) -> dict[str, Any]:
    return {
        "name": slot.name,
        "kind": slot.kind,
        "required": slot.required,
        "max_bytes": slot.max_bytes,
        "delivery": slot.delivery,
        "raw": dict(slot.raw) if isinstance(slot.raw, Mapping) else None,
    }


def _slot_from_wire(value: object) -> CapabilityIOSlot:
    record = _mapping(value, "capability_descriptor_invalid")
    max_bytes = record.get("max_bytes")
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0
    ):
        raise PluginGenerationCodecError("capability_descriptor_invalid")
    raw_value = record.get("raw")
    raw = None if raw_value is None else _optional_mapping(raw_value)
    return CapabilityIOSlot(
        name=_required_string(record.get("name"), "capability_descriptor_invalid"),
        kind=_required_string(record.get("kind"), "capability_descriptor_invalid"),
        required=_boolean(record.get("required")),
        max_bytes=max_bytes,
        delivery=_string(record.get("delivery"), "capability_descriptor_invalid"),
        raw=raw,
    )


def _slots_from_wire(value: object) -> tuple[CapabilityIOSlot, ...]:
    if not isinstance(value, list):
        raise PluginGenerationCodecError("capability_descriptor_invalid")
    return tuple(_slot_from_wire(item) for item in value)


def _trigger_to_wire(trigger: TriggerConfig | None) -> dict[str, Any] | None:
    if trigger is None:
        return None
    return {"kind": trigger.kind, "raw": dict(trigger.raw)}


def _trigger_from_wire(value: object) -> TriggerConfig | None:
    if value is None:
        return None
    record = _mapping(value, "capability_descriptor_invalid")
    return TriggerConfig(
        kind=_required_string(record.get("kind"), "capability_descriptor_invalid"),
        raw=_optional_mapping(record.get("raw")),
    )


def _json_snapshot(value: Any) -> Any:
    try:
        _require_json_object_keys(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PluginGenerationCodecError("json_value_required") from exc


def _require_json_object_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PluginGenerationCodecError("json_value_required")
        for item in value.values():
            _require_json_object_keys(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _require_json_object_keys(item)


def _mapping(value: object, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise PluginGenerationCodecError(reason)
    return value


def _optional_mapping(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    return dict(_mapping(value, "capability_descriptor_invalid"))


def _string(value: object, reason: str) -> str:
    if not isinstance(value, str):
        raise PluginGenerationCodecError(reason)
    return value


def _number(value: object, reason: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PluginGenerationCodecError(reason)
    number = float(value)
    if not math.isfinite(number):
        raise PluginGenerationCodecError(reason)
    return number


def _integer(value: object, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PluginGenerationCodecError(reason)
    return value


def _boolean_value(value: object, reason: str) -> bool:
    if not isinstance(value, bool):
        raise PluginGenerationCodecError(reason)
    return value


def _required_string(value: object, reason: str) -> str:
    text = _string(value, reason).strip()
    if not text:
        raise PluginGenerationCodecError(reason)
    return text


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PluginGenerationCodecError("capability_descriptor_invalid")
    return tuple(value)


def _string_tuple_value(value: object, reason: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        raise PluginGenerationCodecError(reason)
    return value


def _string_tuple_from_wire(value: object, reason: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PluginGenerationCodecError(reason)
    return tuple(value)


def _string_pairs_to_wire(value: object, reason: str) -> list[list[str]]:
    if not isinstance(value, tuple):
        raise PluginGenerationCodecError(reason)
    pairs: list[list[str]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise PluginGenerationCodecError(reason)
        pairs.append([_string(item[0], reason), _string(item[1], reason)])
    return pairs


def _string_pairs_from_wire(value: object, reason: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise PluginGenerationCodecError(reason)
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise PluginGenerationCodecError(reason)
        pairs.append((_string(item[0], reason), _string(item[1], reason)))
    return tuple(pairs)


def _external_event_to_wire(value: PluginExternalEvent, reason: str) -> dict[str, Any]:
    if not isinstance(value, PluginExternalEvent):
        raise PluginGenerationCodecError(reason)
    return {
        "event_type": _string(value.event_type, reason),
        "source": _string(value.source, reason),
        "fields": _string_pairs_to_wire(value.fields, reason),
    }


def _external_event_from_wire(value: object, reason: str) -> PluginExternalEvent:
    record = _mapping(value, reason)
    return PluginExternalEvent(
        event_type=_string(record.get("event_type"), reason),
        source=_string(record.get("source"), reason),
        fields=_string_pairs_from_wire(record.get("fields"), reason),
    )


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise PluginGenerationCodecError("capability_descriptor_invalid")
    return value


__all__ = [
    "PluginGenerationCodecError",
    "capability_descriptor_from_wire",
    "capability_descriptor_to_wire",
    "capability_result_from_wire",
    "capability_result_to_wire",
    "invocation_context_from_wire",
    "invocation_context_to_wire",
    "json_snapshot",
    "notification_intent_from_wire",
    "notification_intent_to_wire",
    "notification_result_from_wire",
    "notification_result_to_wire",
    "agent_event_request_from_wire",
    "agent_event_request_to_wire",
    "agent_event_result_from_wire",
    "agent_event_result_to_wire",
    "plugin_event_dispatch_result_from_wire",
    "plugin_event_dispatch_result_to_wire",
    "plugin_event_envelope_from_wire",
    "plugin_event_envelope_to_wire",
    "plugin_hook_dispatch_result_from_wire",
    "plugin_hook_dispatch_result_to_wire",
    "plugin_hook_envelope_from_wire",
    "plugin_hook_envelope_to_wire",
    "plugin_qq_command_result_from_wire",
    "plugin_qq_command_result_to_wire",
    "qq_command_dispatch_from_wire",
    "qq_command_dispatch_to_wire",
]
