"""JSON projections for the versioned PluginHost process boundary.

Only public CapCore values cross this boundary.  The codec deliberately does
not pickle adapters, plugin objects, paths, or host internals.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping
from akane_plugin.results import Result, validate_followup

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
    PluginInvocationContext,
    PluginDeliverySnapshot,
    PluginHookEnvelope,
    PluginOutboundDecoration,
    PluginOutboundPlanSnapshot,
    PluginQQCommandResult,
    PluginToolCallSnapshot,
    PluginToolResultSnapshot,
)
from .plugin_hooks import PluginHookDispatchResult


def __getattr__(name):
    # Historical aliases are not used by the generic generation codec. Loading
    # an audio service must not initialize an unrelated channel implementation.
    if name not in {"PluginGenerationEventPayloadError", "event_payload_from_wire", "event_payload_to_wire"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import plugin_generation_event_payload
    value = getattr(plugin_generation_event_payload, name)
    globals()[name] = value
    return value


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
            "input_schema": descriptor.input_schema,
            "output_schema": descriptor.output_schema,
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
    if type(raw.get("owner_only", False)) is not bool:
        raise PluginGenerationCodecError("tool_owner_only_invalid")
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
        input_schema=_mapping(record["input_schema"], "capability_descriptor_invalid") if record.get("input_schema") is not None else None,
        output_schema=_mapping(record["output_schema"], "capability_descriptor_invalid") if record.get("output_schema") is not None else None,
    )


def invocation_context_to_wire(context: InvocationContext) -> dict[str, Any]:
    if not isinstance(context, InvocationContext):
        raise PluginGenerationCodecError("invocation_context_required")
    return {
        "profile_user_id": context.profile_user_id,
        "session_id": context.session_id,
        "client_mode": context.client_mode,
        "conversation_ref": str(getattr(context, "conversation_ref", "") or ""),
        "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
        **({"authorization_profile_user_id": str(context.authorization_profile_user_id)}
           if getattr(context, "authorization_profile_user_id", "") else {}),
        **({"global_scope": True} if getattr(context, "global_scope", False) else {}),
    }


def invocation_context_from_wire(value: object) -> InvocationContext:
    record = _mapping(value, "invocation_context_invalid")
    values = {
        "profile_user_id": _string(record.get("profile_user_id"), "invocation_context_invalid"),
        "session_id": _string(record.get("session_id"), "invocation_context_invalid"),
        "client_mode": _string(record.get("client_mode"), "invocation_context_invalid"),
    }
    conversation_ref = _string(record.get("conversation_ref"), "invocation_context_invalid")
    character_pack_id = _string(record.get("character_pack_id"), "invocation_context_invalid")
    global_scope = record.get("global_scope", False)
    authorization_profile = record.get("authorization_profile_user_id", "")
    if not isinstance(authorization_profile, str) or len(authorization_profile) > 160:
        raise PluginGenerationCodecError("invocation_context_invalid")
    if not isinstance(global_scope, bool) or (global_scope and (values["profile_user_id"] or values["session_id"])):
        raise PluginGenerationCodecError("invocation_context_invalid")
    if global_scope and (character_pack_id or authorization_profile):
        raise PluginGenerationCodecError("invocation_context_invalid")
    if conversation_ref or global_scope or character_pack_id or authorization_profile:
        return PluginInvocationContext(**values, conversation_ref=conversation_ref, global_scope=global_scope,
                                       character_pack_id=character_pack_id,
                                       authorization_profile_user_id=authorization_profile)
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
    try:
        followup = validate_followup(record.get("followup"), optional=True)
    except ValueError:
        raise PluginGenerationCodecError("capability_result_followup_invalid") from None
    result_type = Result if followup is not None else CapabilityResult
    result = result_type(
        is_error=is_error,
        content=record.get("content"),
        status=_string(record.get("status"), "capability_result_invalid"),
        reason=_string(record.get("reason"), "capability_result_invalid"),
        **({"followup": followup} if followup is not None else {}),
    )
    if "value" in record:
        from dataclasses import replace
        result = replace(result, value=_json_snapshot(record["value"]))
    return result


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
    "plugin_hook_dispatch_result_from_wire",
    "plugin_hook_dispatch_result_to_wire",
    "plugin_hook_envelope_from_wire",
    "plugin_hook_envelope_to_wire",
    "plugin_qq_command_result_from_wire",
    "plugin_qq_command_result_to_wire",
    "qq_command_dispatch_from_wire",
    "qq_command_dispatch_to_wire",
]
