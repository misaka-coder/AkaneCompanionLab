"""Reversible JSON projection for authoritative plugin event payloads.

The generation protocol must not flatten channel-owned immutable objects into
text or public summaries.  This codec keeps generic JSON/tuple payloads and the
current channelcore-onebot message authority lossless without using pickle or
importing arbitrary classes named by protocol input.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from channelcore_onebot import (
    ActorRef,
    AttachmentLocator,
    AttachmentPart,
    AttachmentRef,
    ConversationRef,
    ForwardPart,
    ForwardRef,
    InboundMessage,
    MentionPart,
    MentionRef,
    MessageChain,
    MessagePart,
    ReplyPart,
    ReplyRef,
    TextPart,
    UnknownPart,
)


class PluginGenerationEventPayloadError(ValueError):
    """The event payload has no lossless generation protocol projection."""


_CHANNELCORE_TYPES = (
    ActorRef,
    AttachmentLocator,
    AttachmentPart,
    AttachmentRef,
    ConversationRef,
    ForwardPart,
    ForwardRef,
    InboundMessage,
    MentionPart,
    MentionRef,
    MessageChain,
    MessagePart,
    ReplyPart,
    ReplyRef,
    TextPart,
    UnknownPart,
)
_TYPE_NAMES = {
    item: f"channelcore-onebot.{item.__name__}.v1" for item in _CHANNELCORE_TYPES
}
_TYPES_BY_NAME = {name: item for item, name in _TYPE_NAMES.items()}


def event_payload_to_wire(value: object | None) -> dict[str, Any]:
    """Encode one supported immutable payload without invoking user code."""

    return _encode(value)


def event_payload_from_wire(value: object) -> object | None:
    """Decode only built-in containers and explicitly allowlisted authorities."""

    return _decode(value)


def _encode(value: Any) -> dict[str, Any]:
    if value is None or isinstance(value, (bool, int, str)):
        return {"kind": "scalar", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        return {"kind": "scalar", "value": value}
    if isinstance(value, bytes):
        return {
            "kind": "bytes",
            "value": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, tuple):
        return {"kind": "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, list):
        return {"kind": "list", "items": [_encode(item) for item in value]}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        return {
            "kind": "mapping",
            "items": [[key, _encode(item)] for key, item in value.items()],
        }
    payload_type = type(value)
    type_name = _TYPE_NAMES.get(payload_type)
    if type_name is None or not is_dataclass(value):
        raise PluginGenerationEventPayloadError("event_payload_unsupported")
    return {
        "kind": "authority",
        "type": type_name,
        "fields": {
            item.name: _encode(getattr(value, item.name))
            for item in fields(value)
        },
    }


def _decode(value: object) -> Any:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise PluginGenerationEventPayloadError("event_payload_invalid")
    kind = value.get("kind")
    if kind == "scalar":
        scalar = value.get("value")
        if scalar is None or isinstance(scalar, (bool, int, str)):
            return scalar
        if isinstance(scalar, float) and math.isfinite(scalar):
            return scalar
        raise PluginGenerationEventPayloadError("event_payload_invalid")
    if kind == "bytes":
        encoded = value.get("value")
        if not isinstance(encoded, str):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        try:
            return base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as exc:
            raise PluginGenerationEventPayloadError("event_payload_invalid") from exc
    if kind in {"tuple", "list"}:
        items = value.get("items")
        if not isinstance(items, list):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        decoded = [_decode(item) for item in items]
        return tuple(decoded) if kind == "tuple" else decoded
    if kind == "mapping":
        items = value.get("items")
        if not isinstance(items, list):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        decoded_mapping: dict[str, Any] = {}
        for item in items:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or item[0] in decoded_mapping
            ):
                raise PluginGenerationEventPayloadError("event_payload_invalid")
            decoded_mapping[item[0]] = _decode(item[1])
        return decoded_mapping
    if kind == "authority":
        payload_type = _TYPES_BY_NAME.get(value.get("type"))
        raw_fields = value.get("fields")
        if payload_type is None or not isinstance(raw_fields, Mapping):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        expected = {item.name for item in fields(payload_type)}
        if set(raw_fields) != expected or any(
            not isinstance(key, str) for key in raw_fields
        ):
            raise PluginGenerationEventPayloadError("event_payload_invalid")
        try:
            return payload_type(
                **{name: _decode(item) for name, item in raw_fields.items()}
            )
        except (TypeError, ValueError) as exc:
            raise PluginGenerationEventPayloadError("event_payload_invalid") from exc
    raise PluginGenerationEventPayloadError("event_payload_invalid")


__all__ = [
    "PluginGenerationEventPayloadError",
    "event_payload_from_wire",
    "event_payload_to_wire",
]
