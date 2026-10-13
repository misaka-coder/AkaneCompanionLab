"""Output normalization, dialogue turn assembly, reasoning attachment, and debug payload services."""
from __future__ import annotations

import logging
from typing import Any, Callable, Generator, Mapping

from services import provider_continuation as continuation

from ..client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from ..resource_manifest import ResourceManifest
from ..tool_invocation import (
    NATIVE_REASONING_CONTENT_FIELD,
    NATIVE_TOOL_CALL_FIELD,
    NATIVE_TOOL_CALLS_FIELD,
)
from ..tool_runtime import ToolExecutionResult
from .. import final_output_engine

logger = logging.getLogger("akane.engine")


class OutputFormattingMixin:
    """Mixin providing final output normalization, dialogue turn generation, and debug payload assembly."""

    def _build_character_context_debug_payload(
        self,
        *,
        character_pack_id: str,
        user_message: str,
        tool_results: list[ToolExecutionResult],
    ) -> dict[str, Any]:
        automatic: dict[str, Any] = {}
        context_library_service = getattr(
            getattr(self, "desktop_pet_character_resources", None),
            "context_libraries",
            None,
        )
        automatic_loader = getattr(
            context_library_service,
            "load_automatic_context",
            None,
        )
        if automatic_loader is not None and character_pack_id:
            try:
                result = automatic_loader(character_pack_id, user_message)
            except Exception as exc:
                logger.warning("automatic character context diagnostics failed: %s", exc)
                result = {}
            matches = [
                {
                    "target": str(item.get("target") or ""),
                    "matched_terms": [str(term) for term in item.get("matched_terms") or [] if str(term).strip()],
                }
                for item in result.get("matches") or []
                if isinstance(item, dict) and str(item.get("target") or "").strip()
            ]
            loaded = [
                str(item.get("target") or "")
                for item in result.get("loaded") or []
                if isinstance(item, dict) and str(item.get("target") or "").strip()
            ]
            failed = [
                {
                    "target": str(item.get("target") or ""),
                    "status": str(item.get("status") or "unavailable"),
                    "reason": str(item.get("reason") or ""),
                }
                for item in result.get("failed") or []
                if isinstance(item, dict)
            ]
            if matches or loaded or failed:
                automatic = {
                    "status": str(result.get("status") or "unavailable"),
                    "matches": matches,
                    "loaded": loaded,
                    "failed": failed,
                }

        tool_rounds = [
            dict(result.state_updates.get("character_context") or {})
            for result in tool_results
            if (
                isinstance(result.state_updates, dict)
                and isinstance(result.state_updates.get("character_context"), dict)
            )
        ]
        if not automatic and not tool_rounds:
            return {}
        return {
            "automatic": automatic,
            "tool_rounds": tool_rounds,
        }

    def _build_retrieval_debug_payload(
        self,
        *,
        router_output: dict[str, Any],
        router_timing: dict[str, Any],
        retrieval_result: dict[str, Any],
        verifier_output: dict[str, Any],
        verifier_timing: dict[str, Any],
        confirmed_snippets: list[str],
    ) -> dict[str, Any]:
        memory_snippets = list(retrieval_result.get("memory_snippets") or [])
        selected_memory_snippets: list[dict[str, Any]] = []
        seen_indexes: set[int] = set()
        for raw_index in verifier_output.get("selected_indexes") or []:
            try:
                index = int(raw_index)
            except Exception:
                continue
            if index < 1 or index > len(memory_snippets) or index in seen_indexes:
                continue
            seen_indexes.add(index)
            selected_memory_snippets.append(
                {
                    "index": index,
                    "snippet": memory_snippets[index - 1],
                }
            )
        if not selected_memory_snippets and confirmed_snippets:
            selected_memory_snippets = [
                {
                    "index": None,
                    "snippet": str(snippet),
                }
                for snippet in confirmed_snippets
                if str(snippet).strip()
            ]
        return {
            "router_output": router_output,
            "router_timing": router_timing,
            "retrieval_result": {
                "filtered_candidate_count": retrieval_result["filtered_candidate_count"],
                "time_filter": retrieval_result["time_filter"],
                "fused_hits": retrieval_result["fused_hits"],
                "memory_snippets": memory_snippets,
                "selected_memory_snippets": selected_memory_snippets,
            },
            "verifier_output": verifier_output,
            "verifier_timing": verifier_timing,
        }


    @staticmethod
    def _multimodal_unavailable_output(reason: str = "") -> dict[str, Any]:
        """Structured non-guessing reply when real images exist but no model
        can consume them.

        Deliberately not a text-model guess: the assistant states the limitation
        instead of pretending to see the image.  The reason stays out of the
        user-facing speech and is only carried in the audit field.
        """
        normalized_reason = str(reason or "multimodal_model_unavailable").strip()
        return {
            "emotion": "neutral",
            "speech": "我这边暂时没有可用的识图模型，直接看不了这张图。你可以先用文字描述一下，或者等识图模型配置好后再发一次。",
            "tool_call": None,
            "memory_metadata": {},
            "_multimodal_unavailable": {
                "status": "unavailable",
                "reason": normalized_reason or "multimodal_model_unavailable",
                "required_modalities": ["image"],
            },
        }

    @staticmethod
    def _is_multimodal_unavailable_target(target: Any) -> bool:
        return isinstance(target, dict) and str(target.get("status") or "") == "unavailable"



    def _normalize_final_output(
        self,
        *,
        result: dict[str, Any] | None,
        visual_defaults: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        allow_tool_call: bool,
        debug_enabled: bool,
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        user_message: str = "",
        domain_profile_id: str = "",
        capability_selection: Any = None,
        allow_deliberate_silence: bool = False,
    ) -> dict[str, Any]:
        return final_output_engine.normalize_final_output(
            self,
            result=result,
            visual_defaults=visual_defaults,
            profile_user_id=profile_user_id,
            session_id=session_id,
            allow_tool_call=allow_tool_call,
            debug_enabled=debug_enabled,
            client_context=client_context,
            resource_manifest=resource_manifest,
            user_message=user_message,
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
            allow_deliberate_silence=allow_deliberate_silence,
        )

    @staticmethod
    def _attach_native_reasoning_content(
        normalized: dict[str, Any],
        *,
        raw_result: Any,
    ) -> None:
        """Carry private reasoning only with a real native tool decision."""

        if not isinstance(raw_result, Mapping):
            return
        if not (
            raw_result.get(NATIVE_TOOL_CALL_FIELD)
            or raw_result.get(NATIVE_TOOL_CALLS_FIELD)
        ):
            return
        reasoning_content = str(raw_result.get(NATIVE_REASONING_CONTENT_FIELD) or "").strip()
        if reasoning_content:
            normalized[NATIVE_REASONING_CONTENT_FIELD] = reasoning_content
        state = raw_result.get(continuation.RESULT_FIELD)
        if isinstance(state, dict):
            normalized[continuation.RESULT_FIELD] = state

    def _normalize_speech_payload(
        self,
        *,
        speech: Any,
        fallback_to_default: bool = True,
    ) -> tuple[str, list[str]]:
        return final_output_engine.normalize_speech_payload(
            speech=speech,
            fallback_to_default=fallback_to_default,
        )

    def _apply_persona_state_to_final_output(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        final_output: dict[str, Any],
        now_ts: int,
        source_id: str = "",
        tool_result: ToolExecutionResult | None = None,
    ) -> dict[str, Any]:
        return final_output_engine.apply_persona_state_to_final_output(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            final_output=final_output,
            now_ts=now_ts,
            source_id=source_id,
            tool_result=tool_result,
        )

    def _normalize_code_snippet(self, value: Any) -> str:
        return final_output_engine.normalize_code_snippet(value)

    def _normalize_activity_action(self, value: Any) -> dict[str, Any] | None:
        return final_output_engine.normalize_activity_action(value)

    def _build_assistant_dialogue_turn(self, speech: Any, *, speaker_name: str | None = None) -> dict[str, str] | None:
        return final_output_engine.build_assistant_dialogue_turn(speech, speaker_name=speaker_name)

    def _build_dialogue_turns(
        self,
        *,
        preface_turn: dict[str, str] | list[dict[str, str]] | None,
        npc_turns: list[dict[str, Any]],
        final_speech: Any,
        final_speech_segments: Any = None,
        speaker_name: str | None = None,
    ) -> list[dict[str, str]]:
        return final_output_engine.build_dialogue_turns(
            preface_turn=preface_turn,
            npc_turns=npc_turns,
            final_speech=final_speech,
            final_speech_segments=final_speech_segments,
            speaker_name=speaker_name,
        )
