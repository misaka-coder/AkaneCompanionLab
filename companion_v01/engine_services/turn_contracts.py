"""Explicit request, prompt and memory state shared by turn stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest

if TYPE_CHECKING:
    from ..owner_private_session import PrivateTurnScope


@dataclass(frozen=True)
class TurnIdentity:
    session_id: str
    profile_user_id: str
    character_pack_id: str
    domain_profile_id: str
    actor_stable_id: str
    actor_display_name: str
    trace_id: str


@dataclass(frozen=True)
class TurnRequest:
    mode: str
    payload: dict[str, Any]
    client_context: ClientProtocolContext
    user_message: str
    now_ts: int
    date_label: str
    time_of_day: str
    prompt_scope: str
    turn_control_id: str
    plugin_external_event: dict[str, Any] | None

    @property
    def streaming(self) -> bool:
        return str(self.mode or "").strip() == "stream"

    @property
    def external_event_turn(self) -> bool:
        return self.plugin_external_event is not None


@dataclass(frozen=True)
class TurnPersistence:
    transient_user_turn: bool
    current_input_transient: bool
    externally_managed_memcore_turn: bool
    persist_assistant_turn: bool
    speculative_voice_candidate: bool
    private_scope: PrivateTurnScope | None
    memory_channel: str


@dataclass
class TurnPromptState:
    resource_manifest: ResourceManifest | None
    chat_model_override: str
    extra_user_context: str
    user_images: list[dict[str, Any]]
    execution_target: Any
    final_debug_enabled: bool | None
    recent_raw: list[dict[str, Any]]
    recent_episodic_summaries: list[dict[str, Any]]
    recent_semantic_summaries: list[dict[str, Any]]
    confirmed_snippets: list[str]
    exclude_source_ids: list[str]
    request_projection_state: dict[str, Any]


@dataclass
class TurnMemoryState:
    user_record: dict[str, Any]
    source_id: str
    turn_id: str
    failure: dict[str, Any] | None


@dataclass(frozen=True)
class TurnDiagnostics:
    router_output: dict[str, Any]
    router_timing: dict[str, Any]
    retrieval_result: dict[str, Any]
    verifier_output: dict[str, Any]
    verifier_timing: dict[str, Any]


@dataclass(frozen=True)
class PreparedTurn:
    identity: TurnIdentity
    request: TurnRequest
    persistence: TurnPersistence
    prompt: TurnPromptState
    memory: TurnMemoryState
    diagnostics: TurnDiagnostics


@dataclass(frozen=True)
class DeferredTurn:
    """Preparation ended before opening or writing a dialogue turn."""

    output: dict[str, Any]
