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

from .plugin_api import NotificationIntent, NotificationResult


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
]
