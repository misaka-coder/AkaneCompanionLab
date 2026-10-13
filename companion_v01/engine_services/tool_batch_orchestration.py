"""Batch dispatch, cancellation, outcome collection and memory continuation coordination."""
from __future__ import annotations

from typing import Any, Mapping
from ..client_protocol import ClientProtocolContext
from ..tool_batch import execute_tool_batch
from ..tool_invocation import (
    NATIVE_ANTHROPIC,
    NATIVE_OPENAI,
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_PARSE_ERROR_FIELD,
    TOOL_SOURCE_FIELD,
)
from ..tool_runtime import ToolExecutionResult
from ..turn_coordination import current_cancellation_check
from .. import tool_orchestration_engine


class ToolBatchOrchestrationMixin:
    def _execute_and_record_tool_round(
        self,
        *,
        tool_call: dict[str, Any],
        final_output: dict[str, Any],
        tool_results: list[ToolExecutionResult],
        tool_events: list[dict[str, Any]],
        tool_followups: list[str],
        tool_turns: list[dict[str, Any]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        client_context: ClientProtocolContext,
        memory_exclude_source_ids: list[str],
        request_context: dict[str, Any],
        tool_history_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        memcore_turn_id: str = "",
        execution_target: Any = None,
    ) -> tuple[ToolExecutionResult | None, list[dict[str, Any]]]:
        results, current_events = self._execute_and_record_tool_batch(
            tool_calls=[tool_call],
            final_output=final_output,
            tool_results=tool_results,
            tool_events=tool_events,
            tool_followups=tool_followups,
            tool_turns=tool_turns,
            recent_raw_for_turn=recent_raw_for_turn,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            client_context=client_context,
            memory_exclude_source_ids=memory_exclude_source_ids,
            request_context=request_context,
            tool_history_turns=tool_history_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            memcore_turn_id=memcore_turn_id,
            execution_target=execution_target,
        )
        return (results[-1] if results else None), current_events

    def _execute_and_record_tool_batch(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        final_output: dict[str, Any],
        provider_output_raw: str = "",
        tool_results: list[ToolExecutionResult],
        tool_events: list[dict[str, Any]],
        tool_followups: list[str],
        tool_turns: list[dict[str, Any]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        client_context: ClientProtocolContext,
        memory_exclude_source_ids: list[str],
        request_context: dict[str, Any],
        tool_history_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        recorded_tool_call_ids: set[str] | None = None,
        domain_profile_id: str = "",
        memcore_turn_id: str = "",
        execution_target: Any = None,
        native_reasoning_by_call_id: Mapping[str, str] | None = None,
        provider_continuations: dict | None = None,
    ) -> tuple[list[ToolExecutionResult], list[dict[str, Any]]]:
        calls = [dict(call) for call in tool_calls if isinstance(call, dict) and call]
        if not calls:
            return [], []

        def execute(call: dict[str, Any]) -> ToolExecutionResult:
            return self._execute_tool_call_with_hooks(
                call=call,
                final_output=final_output,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
                current_user_source_id=current_user_source_id,
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context={**request_context, "_model_execution_target": execution_target,
                                 "_host_visible_tool_history": [*request_context.get("_host_visible_tool_history", []), *list(tool_history_turns or [])],
                                 "_host_origin_turn_id": memcore_turn_id},
                domain_profile_id=domain_profile_id,
            )

        handler_sets = {}
        def handler_for(call):
            if str(call.get(TOOL_PARSE_ERROR_FIELD) or "").strip():
                return None
            selection = call.get(TOOL_CAPABILITY_SELECTION_FIELD)
            key = id(selection)
            if key not in handler_sets:
                handler_sets[key] = self._resolve_tool_handlers(
                    client_context=client_context,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    domain_profile_id=domain_profile_id,
                    capability_selection=selection,
                )
            return handler_sets[key].get(str(call.get("type") or ""))

        executed = execute_tool_batch(calls, execute=execute, handler_for=handler_for, scope_id=session_id,
                                      cancelled=current_cancellation_check())
        completed = list(executed)

        batch_events: list[dict[str, Any]] = []
        followup_start = len(tool_followups)
        history_items: list[tuple[dict[str, Any], ToolExecutionResult, str]] = []
        for call, result in zip(calls, executed):
            assert result is not None
            current_events, shaped_followup = self._record_tool_round_result(
                tool_call=call,
                tool_result=result,
                tool_results=tool_results,
                tool_events=tool_events,
                tool_followups=tool_followups,
                tool_turns=tool_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
            )
            batch_events.extend(current_events)
            history_items.append((call, result, shaped_followup))
        trace_source_ids, trace_record_failure = self._record_memcore_tool_batch(
            items=history_items,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            memcore_turn_id=memcore_turn_id,
            recorded_tool_call_ids=recorded_tool_call_ids,
        )
        batch_model_images = self._merge_tool_model_image_inputs(
            [],
            [result for _call, result, _shaped in history_items],
        )
        chat_model_override = str((request_context or {}).get("chat_model_override") or "").strip()
        # The next request may be sent to a different provider when this batch
        # produced real image inputs.  Build the temporary tool-history
        # projection for that next target, rather than inheriting the provider
        # shape that happened to emit the previous tool call.  MemCore itself
        # stores provider-neutral entries; this is only the active-turn wire
        # projection.
        projection_target = execution_target
        if batch_model_images and str(getattr(execution_target, "role", "") or "") != "vision":
            projection_target = self._resolve_turn_execution_target(
                has_real_images=False,
                tool_image_upgrade=True,
                chat_model_override=chat_model_override,
            )
        projection_provider_profile = str(getattr(projection_target, "protocol", "") or "").strip().lower()
        if projection_provider_profile:
            projection_model_images = batch_model_images
        else:
            native_vision_status = self.native_chat_vision_status(
                chat_model_override=chat_model_override,
            )
            projection_model_images = batch_model_images if bool(native_vision_status.get("enabled")) else []
        media_source_ids = self._record_memcore_tool_media_input(
            model_image_inputs=batch_model_images,
            related_source_ids=trace_source_ids,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            now_ts=now_ts,
            memcore_turn_id=memcore_turn_id,
        )
        if prompt_exclude_source_ids is not None:
            for source_id in [*trace_source_ids, *media_source_ids]:
                if source_id not in prompt_exclude_source_ids:
                    prompt_exclude_source_ids.append(source_id)
        tool_projection = self._append_tool_history_batch(
            tool_history_turns=tool_history_turns,
            items=history_items,
            trace_source_ids=trace_source_ids,
            media_source_ids=media_source_ids,
            model_image_inputs=projection_model_images,
            provider_output_raw=provider_output_raw,
            provider_profile=projection_provider_profile,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            memcore_turn_id=memcore_turn_id,
            current_user_source_id=current_user_source_id,
            native_reasoning_by_call_id=native_reasoning_by_call_id,
            provider_continuations=provider_continuations,
        )
        manager = getattr(self, "memcore_manager", None)
        memcore_required = bool(
            str(memcore_turn_id or "").strip() and manager is not None and getattr(manager, "enabled", False)
        )
        memcore_failure: dict[str, Any] | None = None
        if memcore_required and trace_record_failure is not None:
            memcore_failure = dict(trace_record_failure)
        elif memcore_required and len(trace_source_ids) != 2 * len(history_items):
            memcore_failure = {
                "status": "failed",
                "reason": "tool_trace_record_failed",
                "detail": "tool_trace_incomplete",
            }
        elif memcore_required and batch_model_images and not media_source_ids:
            memcore_failure = {
                "status": "failed",
                "reason": "tool_media_record_failed",
                "detail": "tool_media_unavailable",
            }
        elif memcore_required and not tool_projection.get("ok"):
            memcore_failure = {
                "status": "failed",
                "reason": "tool_projection_build_failed",
                "detail": self._safe_memcore_failure_code(
                    tool_projection.get("reason"),
                    fallback="tool_projection_unavailable",
                ),
            }
        if memcore_failure is not None:
            for result in completed:
                result.state_updates["_memcore_failure"] = dict(memcore_failure)
        has_native_calls = any(
            str(call.get(TOOL_SOURCE_FIELD) or "").strip() in {NATIVE_ANTHROPIC, NATIVE_OPENAI} for call in calls
        )
        if tool_projection.get("ok") and tool_projection.get("status") == "projected" and not has_native_calls:
            del tool_followups[followup_start:]
        if not tool_projection.get("ok") and has_native_calls:
            for _call, result, shaped_followup in history_items:
                feedback = str(shaped_followup or "").strip()
                if feedback:
                    tool_followups.append(
                        "【本轮真实工具结果】\n"
                        f"工具：{result.tool_type}\n"
                        f"{feedback}\n"
                        "以上是本轮已经返回的真实结果；请据此继续，不要把上下文存储状态误认为工具执行失败。"
                    )
        return completed, batch_events

    @staticmethod
    def _tool_batch_memcore_failure(
        tool_results: list[ToolExecutionResult],
    ) -> dict[str, Any] | None:
        for result in list(tool_results or []):
            state_updates = getattr(result, "state_updates", None)
            failure = state_updates.get("_memcore_failure") if isinstance(state_updates, dict) else None
            if isinstance(failure, dict):
                return dict(failure)
        return None

    def _execute_tool_call(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        tool_call: dict[str, Any],
        visual_payload: dict[str, Any],
        now_ts: int,
        current_user_source_id: str = "",
        client_context: ClientProtocolContext | None = None,
        memory_exclude_source_ids: list[str] | None = None,
        request_context: dict[str, Any] | None = None,
        domain_profile_id: str = "",
    ) -> ToolExecutionResult | None:
        return tool_orchestration_engine.execute_tool_call(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            tool_call=tool_call,
            visual_payload=visual_payload,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            client_context=client_context,
            memory_exclude_source_ids=memory_exclude_source_ids,
            request_context=request_context,
            domain_profile_id=domain_profile_id,
        )
