"""Request-scoped inputs shared by final-response transports and recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest


@dataclass(frozen=True)
class FinalResponseRequest:
    """Keep routing, output identity, and projection tied to one generation."""

    generation_context: dict[str, Any]
    profile_user_id: str
    session_id: str
    user_message: str
    client_context: ClientProtocolContext | None
    resource_manifest: ResourceManifest | None
    domain_profile_id: str
    request_projection_state: dict[str, Any] | None
    user_images: list[dict[str, Any]] | None
    chat_model_override: str
    execution_target: Any
    request_observer: Any
    allow_tool_call: bool
    failure_output: dict[str, Any] | None = None

    def output_inputs(self) -> dict[str, Any]:
        return {
            "generation_context": self.generation_context,
            "profile_user_id": self.profile_user_id,
            "session_id": self.session_id,
            "client_context": self.client_context,
            "resource_manifest": self.resource_manifest,
            "user_message": self.user_message,
            "domain_profile_id": self.domain_profile_id,
        }

    def provider_inputs(self, *, prompt_cache_key: str, retry_note: str) -> dict[str, Any]:
        return {
            "generation_context": self.generation_context,
            "request_projection_state": self.request_projection_state,
            "user_images": self.user_images,
            "chat_model_override": self.chat_model_override,
            "execution_target": self.execution_target,
            "prompt_cache_key": prompt_cache_key,
            "retry_note": retry_note,
            "request_observer": self.request_observer,
            "allow_tool_call": self.allow_tool_call,
        }

    def recovery_inputs(self, *, prompt_cache_key: str) -> dict[str, Any]:
        return {
            **self.output_inputs(),
            "request_projection_state": self.request_projection_state,
            "user_images": self.user_images,
            "chat_model_override": self.chat_model_override,
            "execution_target": self.execution_target,
            "request_observer": self.request_observer,
            "prompt_cache_key": prompt_cache_key,
        }


@dataclass
class FinalResponseStreamState:
    """Mutable delivery and failure state owned by one streaming request."""

    normalized: dict[str, Any] = field(default_factory=dict)
    delivered_segments: list[str] = field(default_factory=list)
    delivered_utterances: list[dict[str, Any]] = field(default_factory=list)
    had_speech_units: bool = False
    stream_error: str = ""
    stream_partial: dict[str, str] = field(default_factory=dict)
    provider_output_raw: str = ""
    retry_feedback: str = ""
    transport_failures: int = 0
    attempts_made: int = 0
    nonstream_transport_failure: bool = False


@dataclass(frozen=True)
class FinalResponseStreamAttempt:
    result: Any
    had_speech_chunk: bool
    delivered_utterances: list[dict[str, Any]] = field(default_factory=list)
    had_speech_units: bool = False


@dataclass(frozen=True)
class FinalResponseJSONAttempt:
    result: Any
    parsed: Any
    error: str
    provider_output_raw: str
    parse_fallback: bool
    transport_failure: bool


@dataclass(frozen=True)
class FinalResponseStreamFallback:
    normalized: dict[str, Any]
    provider_output_raw: str
    transport_failure: bool
    recovered: bool
    failure_output: dict[str, Any] | None = None
