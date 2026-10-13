"""Explicit request, environment and material contracts for response preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..client_protocol import ClientProtocolContext
from ..domain_profiles import DomainProfile
from ..prompt_context_lifecycle import MaterializedPromptContexts
from ..prompt_profiles import PromptProfile
from ..resource_manifest import ResourceManifest


@dataclass(frozen=True)
class ResponseRequest:
    """Inputs used by the model-context pipeline, local to one invocation."""

    session_id: str
    user_message: str
    recent_raw: list[dict[str, Any]]
    confirmed_snippets: list[str]
    now_ts: int
    profile_user_id: str
    current_visual_payload: Any
    extra_user_context: str
    stable_system_context: str
    client_context: ClientProtocolContext | None
    resource_manifest: ResourceManifest | None
    character_pack_id: str
    allow_tool_call: bool
    final_debug_enabled: bool | None
    enable_native_tools: bool
    chat_model_override: str
    execution_target: Any
    post_user_turns: list[dict[str, Any]] | None
    prompt_exclude_source_ids: list[str] | None
    domain_profile_id: str
    prompt_scope: str
    current_user_source_id: str
    current_input_transient: bool
    current_actor_relation: str
    authorization_profile_user_id: str
    actor_stable_id: str
    actor_profile_user_id: str
    request_projection_state: dict[str, Any] | None = None
    native_user_images: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class ResponseEnvironment:
    """Resolved host policies and resources shared by the preparation stages."""

    client_context: ClientProtocolContext
    care_enabled: bool
    prompt_builder: Any
    prompt_profile: PromptProfile
    domain_profile: DomainProfile
    domain_profile_context: str
    tool_capability_available: bool
    effective_allow_tool_call: bool
    debug_enabled: bool
    resource_manifest: ResourceManifest | None
    manifest: Any
    runtime_projection: dict[str, Any]
    user_bgm_tracks: list[Any]
    user_scene_groups: list[Any]
    user_character_outfits: list[Any]
    desktop_pet_character_only: bool
    character_pack_persona_enabled: bool


@dataclass(frozen=True)
class ExtraContextSections:
    materialized: MaterializedPromptContexts
    audit_sections: list[dict[str, Any]]
    stable: str
    volatile: str


@dataclass(frozen=True)
class ResponseMaterials:
    """Prepared prompt contributions; budgeting only changes the history layer."""

    memory_text: str
    persona_context: dict[str, Any]
    current_visual_context: str
    resource_context: str
    visual_defaults: dict[str, Any]
    extra: ExtraContextSections
    mode_prompt_override: str
    system_prompt_override: str
