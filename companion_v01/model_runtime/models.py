"""Request targets and result contracts shared by the model runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class _CompletionPayload(dict):
    """Wire fields plus request-local source positions, never sent as JSON keys."""

    persistent_message_indexes: tuple[int, ...] = ()


@dataclass
class ModelBundle:
    client: Any
    model: str


@dataclass(frozen=True)
class ModelExecutionTarget:
    """Immutable per-turn model routing decision.

    Captured once per user turn so concurrent conversations never share or
    mutate each other's model selection.  ``bundle`` already carries the
    resolved client and provider model id; ``role`` marks which route was
    chosen and ``reason`` records the routing evidence for audit.
    """

    role: str
    bundle: ModelBundle
    model: str
    reason: str

    @property
    def protocol(self) -> str:
        client = getattr(self.bundle, "client", self.bundle)
        return str(getattr(client, "_akane_protocol", getattr(client, "protocol", "")) or "").strip().lower()

    def to_public(self) -> dict[str, str]:
        return {
            "role": str(self.role or "").strip(),
            "model": str(self.model or "").strip(),
            "reason": str(self.reason or "").strip(),
            "protocol": self.protocol,
        }


@dataclass(frozen=True)
class ProviderToolProfile:
    supports_native_tools: bool = False
    native_call_shape: str = "openai_tool_calls"
    verified: bool = False
    notes: str = ""


@dataclass
class NDJSONCallResult:
    events: list[dict[str, Any]]
    event_timings: list[dict[str, Any]]
    elapsed_ms: float
    stopped_early: bool
    completed_stream: bool
    stop_event_type: str
    error: str


@dataclass
class ChatJSONResult:
    parsed: dict[str, Any]
    raw_text: str
    error: str = ""
    fallback_used: bool = False
    metadata_status: str = "missing"
    metadata_present: bool = False
    finish_reason: str = ""
    native_finish_reason: str = ""
    request_attempts: int = 1


@dataclass
class ChatJSONStreamResult:
    parsed: dict[str, Any]
    raw_text: str
    elapsed_ms: float
    error: str
    latest_emotion: str
    latest_speech: str
    native_preface_text: str = ""
    stopped_early: bool = False
    early_tool_call: dict[str, Any] | None = None
    fallback_used: bool = False
    metadata_status: str = "missing"
    metadata_present: bool = False
    finish_reason: str = ""
    native_finish_reason: str = ""


@dataclass
class ChatTextResult:
    """Raw model text from a plain-text completion (no JSON requirement)."""

    text: str
    raw_text: str
    error: str = ""
