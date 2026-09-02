"""JSON projections for the versioned PluginHost process boundary.

Only public CapCore values cross this boundary.  The codec deliberately does
not pickle adapters, plugin objects, paths, or host internals.
"""

from __future__ import annotations

import json
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
    PluginEventEnvelope,
    PluginExternalEvent,
    PluginReasoningRequest,
    PluginReasoningResult,
)
from .plugin_events import PluginEventDispatchResult
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
    }


def invocation_context_from_wire(value: object) -> InvocationContext:
    record = _mapping(value, "invocation_context_invalid")
    return InvocationContext(
        profile_user_id=_string(record.get("profile_user_id"), "invocation_context_invalid"),
        session_id=_string(record.get("session_id"), "invocation_context_invalid"),
        client_mode=_string(record.get("client_mode"), "invocation_context_invalid"),
    )


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


def reasoning_request_to_wire(request: PluginReasoningRequest) -> dict[str, Any]:
    if not isinstance(request, PluginReasoningRequest):
        raise PluginGenerationCodecError("reasoning_request_required")
    if not isinstance(request.timestamp, int) or isinstance(request.timestamp, bool):
        raise PluginGenerationCodecError("reasoning_request_invalid")
    external_event = request.external_event
    if external_event is not None and not isinstance(external_event, PluginExternalEvent):
        raise PluginGenerationCodecError("reasoning_request_invalid")
    external_event_wire = None
    if external_event is not None:
        if not isinstance(external_event.fields, tuple):
            raise PluginGenerationCodecError("reasoning_request_invalid")
        fields: list[list[str]] = []
        for item in external_event.fields:
            if not isinstance(item, tuple) or len(item) != 2:
                raise PluginGenerationCodecError("reasoning_request_invalid")
            fields.append(
                [
                    _string(item[0], "reasoning_request_invalid"),
                    _string(item[1], "reasoning_request_invalid"),
                ]
            )
        external_event_wire = {
            "event_type": _string(
                external_event.event_type,
                "reasoning_request_invalid",
            ),
            "source": _string(
                external_event.source,
                "reasoning_request_invalid",
            ),
            "fields": fields,
        }
    return _json_snapshot(
        {
            "trace_id": _string(request.trace_id, "reasoning_request_invalid"),
            "profile_user_id": _string(
                request.profile_user_id,
                "reasoning_request_invalid",
            ),
            "session_id": _string(request.session_id, "reasoning_request_invalid"),
            "message": _string(request.message, "reasoning_request_invalid"),
            "extra_context": _string(
                request.extra_context,
                "reasoning_request_invalid",
            ),
            "character_pack_id": _string(
                request.character_pack_id,
                "reasoning_request_invalid",
            ),
            "timestamp": request.timestamp,
            "stable_system_context": _string(
                request.stable_system_context,
                "reasoning_request_invalid",
            ),
            "memory_idempotency_key": _string(
                request.memory_idempotency_key,
                "reasoning_request_invalid",
            ),
            "external_event": external_event_wire,
        }
    )


def reasoning_request_from_wire(value: object) -> PluginReasoningRequest:
    record = _mapping(value, "reasoning_request_invalid")
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise PluginGenerationCodecError("reasoning_request_invalid")
    external_event_record = record.get("external_event")
    external_event = None
    if external_event_record is not None:
        event = _mapping(external_event_record, "reasoning_request_invalid")
        raw_fields = event.get("fields")
        if not isinstance(raw_fields, list):
            raise PluginGenerationCodecError("reasoning_request_invalid")
        fields: list[tuple[str, str]] = []
        for item in raw_fields:
            if not isinstance(item, list) or len(item) != 2:
                raise PluginGenerationCodecError("reasoning_request_invalid")
            fields.append(
                (
                    _string(item[0], "reasoning_request_invalid"),
                    _string(item[1], "reasoning_request_invalid"),
                )
            )
        external_event = PluginExternalEvent(
            event_type=_string(event.get("event_type"), "reasoning_request_invalid"),
            source=_string(event.get("source"), "reasoning_request_invalid"),
            fields=tuple(fields),
        )
    return PluginReasoningRequest(
        trace_id=_string(record.get("trace_id"), "reasoning_request_invalid"),
        profile_user_id=_string(
            record.get("profile_user_id"),
            "reasoning_request_invalid",
        ),
        session_id=_string(record.get("session_id"), "reasoning_request_invalid"),
        message=_string(record.get("message"), "reasoning_request_invalid"),
        extra_context=_string(
            record.get("extra_context"),
            "reasoning_request_invalid",
        ),
        character_pack_id=_string(
            record.get("character_pack_id"),
            "reasoning_request_invalid",
        ),
        timestamp=timestamp,
        stable_system_context=_string(
            record.get("stable_system_context"),
            "reasoning_request_invalid",
        ),
        memory_idempotency_key=_string(
            record.get("memory_idempotency_key"),
            "reasoning_request_invalid",
        ),
        external_event=external_event,
    )


def reasoning_result_to_wire(result: PluginReasoningResult) -> dict[str, Any]:
    if not isinstance(result, PluginReasoningResult):
        raise PluginGenerationCodecError("reasoning_result_required")
    if not isinstance(result.ok, bool):
        raise PluginGenerationCodecError("reasoning_result_invalid")
    evidence_events = _json_snapshot(result.evidence_events)
    if not isinstance(evidence_events, list) or any(
        not isinstance(item, dict) for item in evidence_events
    ):
        raise PluginGenerationCodecError("reasoning_result_invalid")
    return {
        "ok": result.ok,
        "status": _string(result.status, "reasoning_result_invalid"),
        "text": _string(result.text, "reasoning_result_invalid"),
        "reason": _string(result.reason, "reasoning_result_invalid"),
        "evidence_events": evidence_events,
    }


def reasoning_result_from_wire(value: object) -> PluginReasoningResult:
    record = _mapping(value, "reasoning_result_invalid")
    ok = record.get("ok")
    if not isinstance(ok, bool):
        raise PluginGenerationCodecError("reasoning_result_invalid")
    evidence_value = record.get("evidence_events")
    if not isinstance(evidence_value, list) or any(
        not isinstance(item, Mapping)
        or any(not isinstance(key, str) for key in item)
        for item in evidence_value
    ):
        raise PluginGenerationCodecError("reasoning_result_invalid")
    evidence_events = _json_snapshot(evidence_value)
    return PluginReasoningResult(
        ok=ok,
        status=_string(record.get("status"), "reasoning_result_invalid"),
        text=_string(record.get("text"), "reasoning_result_invalid"),
        reason=_string(record.get("reason"), "reasoning_result_invalid"),
        evidence_events=tuple(dict(item) for item in evidence_events),
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
    "plugin_event_dispatch_result_from_wire",
    "plugin_event_dispatch_result_to_wire",
    "plugin_event_envelope_from_wire",
    "plugin_event_envelope_to_wire",
    "reasoning_request_from_wire",
    "reasoning_request_to_wire",
    "reasoning_result_from_wire",
    "reasoning_result_to_wire",
]
