"""Canonical channel turn intent shared by every admission path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .durable_session_queue import SessionWorkError


class PluginTurnError(SessionWorkError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class HostTurnIntent:
    message: str
    data: Any
    source: str
    event_type: str
    conversation_ref: str
    trace_id: str
    request_id: str = ""
    model_context: str = ""
    requires_queue: bool = False

    def event_payload(self):
        return {"event_type": self.event_type, "source": self.source, "data": self.data}


@dataclass(frozen=True)
class HostJobTurnIntent(HostTurnIntent):
    """Host-only completion metadata kept outside the plugin turn contract."""

    idempotency_key: str = ""
    memory_mode: str = "timeline"
    presentation_mode: str = "default"
    presentation_prefix: str = ""
    presentation_suffix: str = ""
    strip_leading_addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class HostTurnResult:
    ok: bool
    status: str
    reason: str = ""
    delivery_status: str = ""
    item_id: str = ""


def normalize_turn_intent(request, *, source=None):
    if not isinstance(request, HostTurnIntent):
        raise PluginTurnError("invalid_turn_intent")
    return replace(request, source=source) if source is not None else request


def apply_turn_intent(payload, intent):
    """Overlay a coalesced intent after the real session lock was acquired."""
    memory_mode = getattr(intent, "memory_mode", "current_turn")
    presentation_mode = getattr(intent, "presentation_mode", "default")
    presentation_prefix = getattr(intent, "presentation_prefix", "")
    presentation_suffix = getattr(intent, "presentation_suffix", "")
    strip_leading_addresses = getattr(intent, "strip_leading_addresses", ())
    idempotency_key = getattr(intent, "idempotency_key", "")
    payload.update(message=intent.message, memory_message=intent.message,
                   plugin_external_event=intent.event_payload(),
                   transient_user_message=memory_mode == "current_turn",
                   memory_idempotency_key=idempotency_key,
                   plugin_text_delivery=presentation_mode, plugin_text_prefix=presentation_prefix,
                   plugin_text_suffix=presentation_suffix,
                   plugin_text_strip_leading_addresses=list(strip_leading_addresses))
    if intent.model_context:
        payload["extra_context"] = "\n\n".join(filter(None, (
            str(payload.get("extra_context") or ""), intent.model_context,
        )))
    delivery = payload.get("qq_delivery_context")
    if isinstance(delivery, dict):
        delivery.update(clean_message=intent.message, raw_message=intent.message)
    return payload
