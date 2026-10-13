"""Shared provider request preparation, cache routing, and output provenance."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any, Mapping

from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest
from ..prompt_blocks import CURRENT_ASSISTANT_STATE_MARKER
from ..tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from .final_response_constants import FINAL_RESPONSE_TEMPERATURE, FINAL_PROMPT_CACHE_LAYOUT_VERSION
from .final_response_contracts import FinalResponseRequest


class FinalResponseRequestMixin:
    """Build requests from the actual context projection and frozen turn input."""

    def _prepare_final_response_request(
        self,
        *,
        session_id: str,
        profile_user_id: str,
        user_message: str,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        confirmed_snippets: list[str],
        now_ts: int,
        current_visual_payload: Any = None,
        extra_user_context: str = "",
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        request_projection_state: dict[str, Any] | None = None,
    ) -> FinalResponseRequest:
        generation_context = self._prepare_final_response_context(
            session_id=session_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            current_visual_payload=current_visual_payload,
            native_user_images=user_images,
            profile_user_id=profile_user_id,
            extra_user_context=extra_user_context,
            stable_system_context=stable_system_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=True,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            post_user_turns=post_user_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            prompt_scope=prompt_scope,
            current_user_source_id=str((request_projection_state or {}).get("current_user_source_id") or "").strip(),
            current_input_transient=bool((request_projection_state or {}).get("current_input_transient")),
            current_actor_relation=str((request_projection_state or {}).get("current_actor_relation") or "").strip(),
            authorization_profile_user_id=str(
                (request_projection_state or {}).get("authorization_profile_user_id") or ""
            ).strip(),
            actor_stable_id=str((request_projection_state or {}).get("actor_stable_id") or "").strip(),
            actor_profile_user_id=str((request_projection_state or {}).get("actor_profile_user_id") or "").strip(),
            request_projection_state=request_projection_state,
        )
        projection_failure = generation_context.get("memcore_projection_failure")
        failure_output = (
            self._memcore_projection_failure_output(projection_failure)
            if isinstance(projection_failure, dict)
            else None
        )
        request_observer = None
        if failure_output is None:
            request_observer = self._build_memcore_request_observer(
                generation_context=generation_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                request_projection_state=request_projection_state,
            )
        return FinalResponseRequest(
            generation_context=generation_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            user_message=user_message,
            client_context=client_context,
            resource_manifest=resource_manifest,
            domain_profile_id=domain_profile_id,
            request_projection_state=request_projection_state,
            user_images=user_images,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            request_observer=request_observer,
            allow_tool_call=bool(generation_context.get("allow_tool_call", allow_tool_call)),
            failure_output=failure_output,
        )

    def _build_final_response_request_kwargs(
        self,
        *,
        generation_context: dict[str, Any],
        request_projection_state: dict[str, Any] | None,
        user_images: list[dict[str, Any]] | None,
        chat_model_override: str,
        execution_target: Any,
        prompt_cache_key: str,
        retry_note: str,
        request_observer: Any,
        allow_tool_call: bool,
    ) -> dict[str, Any]:
        """Keep native schemas stable; retry feedback only changes the tail."""
        kwargs = self._build_final_response_base_request_kwargs(
            generation_context=generation_context,
            request_projection_state=request_projection_state,
            user_images=user_images,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            prompt_cache_key=prompt_cache_key,
            retry_note=retry_note,
            request_observer=request_observer,
        )
        kwargs["fallback"] = dict(generation_context["fallback"])
        # Keep the native tool schema byte-stable for the whole open turn.
        # A consumed hard budget changes only the decision policy; removing
        # schemas here would invalidate the provider cache prefix and switch
        # the model onto a different wire contract at the most fragile point.
        native_tools = generation_context.get("native_tools")
        kwargs["native_tools"] = native_tools
        kwargs["native_tool_choice"] = (
            "none" if native_tools and not allow_tool_call else generation_context.get("native_tool_choice", "")
        )
        return kwargs

    def _build_final_response_base_request_kwargs(
        self,
        *,
        generation_context: dict[str, Any],
        request_projection_state: dict[str, Any] | None,
        user_images: list[dict[str, Any]] | None,
        chat_model_override: str,
        execution_target: Any,
        prompt_cache_key: str,
        retry_note: str,
        request_observer: Any,
    ) -> dict[str, Any]:
        """Shared structured/plain-text provider inputs from one projection."""
        user_prompt = self._request_projection_user_prompt(
            request_projection_state,
            str(generation_context["user_prompt"]),
        )
        if retry_note and request_observer is None:
            user_prompt = f"{user_prompt}\n\n{retry_note}"
        kwargs = {
            "system_prompt": str(generation_context["system_prompt"]),
            "user_prompt": user_prompt,
            "temperature": FINAL_RESPONSE_TEMPERATURE,
            "prompt_cache_key": prompt_cache_key,
            "user_images": user_images,
            "system_extra_blocks": generation_context.get("system_extra_blocks"),
            "history_turns": generation_context.get("history_turns"),
            "ephemeral_turns": self._final_response_retry_ephemeral_turns(
                generation_context=generation_context,
                retry_note=retry_note,
                request_observer=request_observer,
            ),
            "post_user_turns": generation_context.get("post_user_turns"),
            "prompt_audit_sections": generation_context.get("prompt_audit_sections"),
            "chat_model_override": chat_model_override,
            "execution_target": execution_target,
        }
        if request_observer is not None:
            kwargs["request_observer"] = request_observer
        return kwargs

    @staticmethod
    def _final_response_retry_ephemeral_turns(
        *,
        generation_context: dict[str, Any],
        retry_note: str,
        request_observer: Any,
    ) -> list[dict[str, Any]] | None:
        original = generation_context.get("ephemeral_turns")
        turns = [dict(turn) for turn in list(original or []) if isinstance(turn, dict)]
        # A MemCore request observer freezes the persistent current user
        # message.  Mutating that message on retry would make the provider
        # request diverge from the recorded projection, but omitting the repair
        # instruction entirely just repeats the same failed request.  Put the
        # repair instruction in a request-scoped tail turn instead: the
        # append-only history and cacheable prefix stay identical, while the
        # model can see exactly what must be repaired.
        if retry_note and request_observer is not None:
            turns.append({"role": "user", "content": retry_note, "_placement": "tail"})
        if turns:
            return turns
        return [] if isinstance(original, list) else None

    @staticmethod
    def _final_prompt_cache_key(generation_context: dict[str, Any]) -> str:
        """Bucket prompts only by stable routing identity.

        Persona reference/state text may change within one character as the
        current message selects context-library material. Keep those volatile
        suffixes out of the routing key. Tool readiness, selected schemas and
        capability disclosures are also request-time state: putting them in the
        routing key sent consecutive turns to different provider cache buckets.
        ``prompt_scope`` remains available to delivery, retry and audit logic,
        but it does not define a second provider cache family. Ordinary turns
        and proactive external events in one conversation share one MemCore
        timeline, so the provider remains authoritative for their exact common
        prefix matching.
        """

        system_prompt = str(generation_context.get("system_prompt") or "")
        stable_system_prefix = system_prompt.split(CURRENT_ASSISTANT_STATE_MARKER, 1)[0].rstrip()
        fallback = generation_context.get("fallback")
        persona = fallback.get("persona") if isinstance(fallback, dict) else None
        layout_version = getattr(
            sys.modules.get("companion_v01.engine"),
            "FINAL_PROMPT_CACHE_LAYOUT_VERSION",
            FINAL_PROMPT_CACHE_LAYOUT_VERSION,
        )
        stable_payload = {
            "cache_layout_version": layout_version,
            "system_prefix": stable_system_prefix,
            "conversation_scope": str(generation_context.get("prompt_cache_scope_hash") or ""),
            "persona_active": str(persona.get("active") or "") if isinstance(persona, dict) else "",
            "prompt_profile": generation_context.get("prompt_profile") or {},
            "domain_profile": generation_context.get("domain_profile") or {},
            "stable_system_context_hash": str(generation_context.get("stable_system_context_hash") or ""),
        }
        canonical = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8", errors="ignore")).hexdigest()[:20]
        return f"chat:final:{digest}"

    def _register_cache_usage_scope(self, cache_key, generation_context, client_context):
        register = getattr(self.llm, "register_cache_usage_scope", None)
        if callable(register):
            register(
                cache_key,
                str(generation_context.get("prompt_cache_scope_hash") or ""),
                str(getattr(getattr(client_context, "effective_mode", None), "value", "")),
            )

    def _prepare_final_response_context(
        self,
        *,
        session_id: str,
        user_message: str,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        confirmed_snippets: list[str],
        now_ts: int,
        profile_user_id: str,
        current_visual_payload: Any = None,
        extra_user_context: str = "",
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        enable_native_tools: bool = False,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        current_user_source_id: str = "",
        current_input_transient: bool = False,
        current_actor_relation: str = "",
        authorization_profile_user_id: str = "",
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
        request_projection_state: Mapping[str, Any] | None = None,
        native_user_images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from .response_builder import prepare_context as _fn

        return _fn(
            self,
            session_id=session_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            profile_user_id=profile_user_id,
            current_visual_payload=current_visual_payload,
            extra_user_context=extra_user_context,
            stable_system_context=stable_system_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=enable_native_tools,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            post_user_turns=post_user_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            prompt_scope=prompt_scope,
            current_user_source_id=current_user_source_id,
            current_input_transient=current_input_transient,
            current_actor_relation=current_actor_relation,
            authorization_profile_user_id=authorization_profile_user_id,
            actor_stable_id=actor_stable_id,
            actor_profile_user_id=actor_profile_user_id,
            request_projection_state=request_projection_state,
            native_user_images=native_user_images,
        )

    def _normalize_final_response_attempt(
        self,
        *,
        request: FinalResponseRequest,
        raw_result: Any,
        result: Any,
        parse_fallback: bool = False,
    ) -> dict[str, Any]:
        """Normalize an attempt while preserving raw reasoning and memory truth."""
        context = request.generation_context
        normalized = self._normalize_final_output(
            result=raw_result,
            visual_defaults=dict(context["visual_defaults"]),
            profile_user_id=request.profile_user_id,
            session_id=request.session_id,
            client_context=request.client_context,
            resource_manifest=request.resource_manifest,
            allow_tool_call=request.allow_tool_call,
            debug_enabled=bool(context["debug_enabled"]),
            user_message=request.user_message,
            domain_profile_id=request.domain_profile_id,
            capability_selection=context.get(TOOL_CAPABILITY_SELECTION_FIELD),
            allow_deliberate_silence=bool(context.get("allow_deliberate_silence")),
        )
        self._attach_native_reasoning_content(normalized, raw_result=raw_result)
        if parse_fallback:
            normalized["_provider_fallback"] = True
        self._attach_memory_annotation_truth(normalized, result=result, raw_result=raw_result)
        return normalized
