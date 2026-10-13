"""The single transport branch used by each dialogue generation round."""

from __future__ import annotations

from typing import Any, Generator

from ..client_protocol import ClientProtocolContext
from ..llm_runtime import ModelRequestCancelled
from ..resource_manifest import ResourceManifest
from ..turn_coordination import cancellation_requested


class TurnTransportMixin:
    """Share generation arguments while retaining sync and streaming transports."""

    def _generate_round(
        self,
        *,
        mode: str,
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
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        """Dispatch one model generation to the transport implementation.

        The transport pair stays intentionally separate: non-streaming
        ``call_chat_json`` and streaming ``stream_chat_json`` (with its own
        stream→non-stream→uncached degrade chain) are two legitimate transports,
        not a duplicated state machine. This is the single branch point the turn
        mainline uses for every generation round.
        """
        if cancellation_requested():
            return {}
        try:
            generation_args = {
                "session_id": session_id,
                "profile_user_id": profile_user_id,
                "user_message": user_message,
                "recent_raw": recent_raw,
                "recent_episodic_summaries": recent_episodic_summaries,
                "recent_semantic_summaries": recent_semantic_summaries,
                "confirmed_snippets": confirmed_snippets,
                "now_ts": now_ts,
                "current_visual_payload": current_visual_payload,
                "extra_user_context": extra_user_context,
                "stable_system_context": stable_system_context,
                "client_context": client_context,
                "resource_manifest": resource_manifest,
                "character_pack_id": character_pack_id,
                "user_images": user_images,
                "allow_tool_call": allow_tool_call,
                "final_debug_enabled": final_debug_enabled,
                "chat_model_override": chat_model_override,
                "execution_target": execution_target,
                "post_user_turns": post_user_turns,
                "prompt_exclude_source_ids": prompt_exclude_source_ids,
                "domain_profile_id": domain_profile_id,
                "prompt_scope": prompt_scope,
                "request_projection_state": request_projection_state,
            }
            if str(mode or "").strip() == "stream":
                return (yield from self._stream_final_response(**generation_args))
            return self._build_final_response(**generation_args)
        except ModelRequestCancelled:
            # The core drains stop controls and preserves completed tool receipts.
            return {}
