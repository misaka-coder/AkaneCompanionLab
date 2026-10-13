"""Active-turn history projection, provider evidence overlays and image merging."""
from __future__ import annotations

from typing import Any, Mapping
from services import provider_continuation as continuation
from ..tool_invocation import NATIVE_ANTHROPIC, NATIVE_OPENAI, TOOL_SOURCE_FIELD
from ..tool_runtime import ToolExecutionResult
from ..model_image_inputs import model_image_source_label


class ToolHistoryProjectionMixin:
    def _build_memcore_request_observer(self, **kwargs):
        """Decorate the freeze observer with request-local completion receipts.

        MemCore still owns binding/freezing. Only the model runtime's final
        provider-shaped history restores the lost pending-consume relation;
        a context projection alone is not evidence that it was sent.
        """
        observer = super()._build_memcore_request_observer(**kwargs)
        runtime = getattr(self, "host_tool_jobs", None)
        registry = getattr(runtime, "completion_work", None)
        if registry is None or not bool(getattr(getattr(self, "llm", None), "supports_request_observer", False)):
            return observer
        from ..host_jobs import HostJobOwner
        from ..host_tool_job_runtime.request_receipts import request_completion_jobs
        context = kwargs["generation_context"]
        state = kwargs.get("request_projection_state") or {}
        turn_id = str(state.get("turn_id") or (context.get("memcore_projection_read") or {}).get("current_turn_id") or "")
        work = registry.request_work(owner=HostJobOwner(kwargs["profile_user_id"], kwargs["session_id"]),
            character_pack_id=kwargs["character_pack_id"], turn_id=turn_id)
        if work is None:
            return observer

        def observe(request):
            request["_pending_task_ids"] = list(work.pending_ids)
            # A maintenance-only fork may freeze its source, but it is not
            # evidence that the main model consumed any background result.
            if request.get("_internal_memory_capture"):
                return observer(request) if observer is not None else {"ok": True, "status": "observed"}
            # Invalidate the previous attempt even when this observation is
            # rejected. A later fallback must never acknowledge stale input.
            work.observe_request([])
            receipt = observer(request) if observer is not None else {"ok": True, "status": "observed"}
            if isinstance(receipt, dict) and receipt.get("ok"):
                work.observe_request(request_completion_jobs(self, work, context, request))
            return receipt
        observe.capture_compaction_context = bool(getattr(observer, "capture_compaction_context", False))
        return observe

    @staticmethod
    def _tool_call_uses_native_history(tool_call: dict[str, Any]) -> bool:
        return str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() in {
            NATIVE_ANTHROPIC,
            NATIVE_OPENAI,
        }

    @staticmethod
    def _merge_model_image_inputs(
        current_images: list[dict[str, Any]],
        additional_images: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw in [*list(current_images or []), *list(additional_images or [])]:
            if not isinstance(raw, dict):
                continue
            data_url = str(raw.get("data_url") or "")
            if not data_url.startswith("data:image/"):
                continue
            source_id = str(raw.get("attachment_id") or "").strip()
            handle = str(raw.get("attachment_handle") or "").strip()
            identity = (source_id, handle)
            if identity != ("", "") and identity in seen:
                continue
            if identity != ("", ""):
                seen.add(identity)
            merged.append(dict(raw))
            if len(merged) >= 5:
                break
        return merged

    @staticmethod
    def _merge_tool_model_image_inputs(
        current_images: list[dict[str, Any]],
        tool_results: list[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        images = [*list(current_images or []), *[
                item
                for result in list(tool_results or [])
                for item in list(getattr(result, "model_image_inputs", None) or [])
            ]]
        observations = [item for item in images if isinstance(item, dict) and item.get("ephemeral_observation")]
        if observations:
            images = [observations[-1], *[item for item in images if not isinstance(item, dict) or not item.get("ephemeral_observation")]]
        return ToolHistoryProjectionMixin._merge_model_image_inputs([], images)

    def _append_tool_history_batch(
        self,
        *,
        tool_history_turns: list[dict[str, Any]] | None,
        items: list[tuple[dict[str, Any], ToolExecutionResult, str]],
        trace_source_ids: list[str],
        media_source_ids: list[str] | None = None,
        model_image_inputs: list[dict[str, Any]] | None = None,
        provider_output_raw: str = "",
        provider_profile: str = "",
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        memcore_turn_id: str = "",
        current_user_source_id: str = "",
        native_reasoning_by_call_id: Mapping[str, str] | None = None,
        provider_continuations: dict | None = None,
        append_only: bool = False,
    ) -> dict[str, Any]:
        if tool_history_turns is None:
            return {"ok": True, "status": "skipped", "reason": "history_target_missing"}
        sources = {
            str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip()
            for tool_call, _result, _shaped in items
            if str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() in {NATIVE_ANTHROPIC, NATIVE_OPENAI}
        }
        media_ids = {
            str(source_id or "").strip() for source_id in list(media_source_ids or []) if str(source_id or "").strip()
        }
        has_legacy_calls = any(
            str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() not in {NATIVE_ANTHROPIC, NATIVE_OPENAI}
            for tool_call, _result, _shaped in items
        )
        if sources and has_legacy_calls:
            return {"ok": False, "status": "failed", "reason": "mixed_tool_history_modes"}
        if not sources and not has_legacy_calls and not media_ids and not trace_source_ids:
            return {"ok": True, "status": "skipped", "reason": "no_tool_calls"}
        if len(sources) != 1:
            if sources:
                return {"ok": False, "status": "failed", "reason": "mixed_native_provider_batch"}
        manager = getattr(self, "memcore_manager", None)
        build_open_projection = getattr(manager, "build_open_turn_projection", None)
        build_full_projection = getattr(manager, "build_context_projection", None)
        selected_ids = set(media_ids)
        selected_ids.update(
            str(source_id or "").strip() for source_id in trace_source_ids if str(source_id or "").strip()
        )
        normalized_turn_id = str(memcore_turn_id or "").strip()
        use_focused_projection = callable(build_open_projection) and bool(normalized_turn_id)
        build_projection = build_open_projection if use_focused_projection else build_full_projection
        if not callable(build_projection) or not selected_ids:
            return {"ok": False, "status": "unavailable", "reason": "memcore_projection_unavailable"}
        provider_profile = str(provider_profile or "").strip().lower()
        if not provider_profile:
            provider_profile = next(iter(sources), "")
        if not provider_profile:
            runtime = getattr(self, "llm", None)
            protocol_getter = getattr(runtime, "chat_provider_protocol", None)
            if callable(protocol_getter):
                provider_profile = str(protocol_getter() or "").strip()
        if not provider_profile:
            return {"ok": False, "status": "unavailable", "reason": "provider_profile_unavailable"}
        projection_kwargs = {
            "provider_profile": provider_profile,
            "profile_user_id": profile_user_id,
            "session_id": session_id,
            "character_pack_id": character_pack_id,
        }
        if use_focused_projection:
            projection_kwargs["turn_id"] = normalized_turn_id
        projection = build_projection(**projection_kwargs)
        if not isinstance(projection, dict) or not projection.get("ok"):
            return {"ok": False, "status": "unavailable", "reason": "memcore_projection_build_failed"}
        projection_messages = [
            message for message in list(projection.get("messages") or []) if isinstance(message, dict)
        ]
        selected_messages = [
            message
            for message in projection_messages
            if selected_ids.intersection(str(source_id or "").strip() for source_id in message.get("source_ids") or [])
        ]
        selected_turn_ids = {
            str(message.get("turn_id") or "").strip()
            for message in selected_messages
            if str(message.get("turn_id") or "").strip()
        }
        if len(selected_turn_ids) > 1 or (
            normalized_turn_id and selected_turn_ids and selected_turn_ids != {normalized_turn_id}
        ):
            return {"ok": False, "status": "failed", "reason": "tool_projection_crosses_turns"}
        current_turn_messages = selected_messages
        if selected_turn_ids and not append_only:
            current_turn_id = next(iter(selected_turn_ids))
            current_turn_messages = [
                message
                for message in projection_messages
                if str(message.get("turn_id") or "").strip() == current_turn_id
            ]
            if not current_turn_messages:
                return {"ok": False, "status": "failed", "reason": "tool_turn_projection_missing"}
            # The runtime inserts a provider-visible current stimulus before
            # ``post_user_turns``. Remove that exact source from the rebuilt
            # open-turn suffix instead of assuming it is always the first
            # projected message. Hidden host turns (for example QQ attention)
            # have a prompt-invisible stimulus, so their first visible message
            # can be the assistant tool call itself; dropping it leaves an
            # orphan ``role=tool`` message that strict providers reject.
            normalized_current_source_id = str(current_user_source_id or "").strip()
            if normalized_current_source_id:
                current_turn_messages = [
                    message
                    for message in current_turn_messages
                    if normalized_current_source_id
                    not in {
                        str(source_id or "").strip()
                        for source_id in message.get("source_ids") or []
                        if str(source_id or "").strip()
                    }
                ]
        projected_messages: list[dict[str, Any]] = []
        media_attached = False
        legacy_assistant_written = False
        legacy_action_ids = set(trace_source_ids[0::2]) if has_legacy_calls else set()
        legacy_result_ids = set(trace_source_ids[1::2]) if has_legacy_calls else set()
        for message in current_turn_messages:
            payload = dict(message.get("payload") or {})
            message_source_ids = {
                str(source_id or "").strip()
                for source_id in message.get("source_ids") or []
                if str(source_id or "").strip()
            }
            if media_ids.intersection(message_source_ids):
                payload = self._attach_model_images_to_projection(
                    payload,
                    model_image_inputs=list(model_image_inputs or []),
                )
                media_attached = True
            elif has_legacy_calls and legacy_action_ids.intersection(message_source_ids):
                raw = str(provider_output_raw or "")
                if not raw or legacy_assistant_written:
                    return {
                        "ok": False,
                        "status": "failed",
                        "reason": "legacy_provider_output_unavailable",
                    }
                payload = {"role": "assistant", "content": raw}
                legacy_assistant_written = True
            elif has_legacy_calls and legacy_result_ids.intersection(message_source_ids):
                payload = self._legacy_tool_result_history_message(payload)
            payload = self._overlay_native_reasoning_content(
                payload,
                native_reasoning_by_call_id=native_reasoning_by_call_id,
            )
            payload = continuation.overlay(payload, provider_continuations, sanitize=self._sanitize_tool_trace_value)
            projected_messages.append(payload)
        covered_ids = {
            str(source_id or "").strip()
            for message in selected_messages
            if isinstance(message, dict)
            for source_id in message.get("source_ids") or []
            if str(source_id or "").strip() in selected_ids
        }
        if not projected_messages or covered_ids != selected_ids:
            return {"ok": False, "status": "failed", "reason": "tool_projection_incomplete"}
        if media_ids and not media_attached:
            return {"ok": False, "status": "failed", "reason": "media_projection_incomplete"}
        if has_legacy_calls and not legacy_assistant_written:
            return {"ok": False, "status": "failed", "reason": "legacy_assistant_projection_missing"}
        if append_only:
            tool_history_turns.extend(projected_messages)
        else:
            tool_history_turns[:] = projected_messages
        return {
            "ok": True,
            "status": "projected",
            "reason": "",
            "provider_profile": str(projection.get("provider_profile") or ""),
            "message_count": len(projected_messages),
            "source_count": len(covered_ids),
        }

    @staticmethod
    def _overlay_native_reasoning_content(
        payload: dict[str, Any],
        *,
        native_reasoning_by_call_id: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        """Decorate an open-turn tool call after MemCore projection.

        The mapping exists only for the active host turn. MemCore remains the
        authority for message order and durable action/result data; private
        reasoning is overlaid only on the provider wire shape that requires it.
        """

        if str(payload.get("role") or "").strip().lower() != "assistant":
            return payload
        reasoning_map = native_reasoning_by_call_id or {}
        tool_calls = payload.get("tool_calls")
        if not isinstance(tool_calls, list):
            return payload
        matched = [
            str(reasoning_map.get(str(call.get("id") or "").strip()) or "").strip()
            for call in tool_calls
            if isinstance(call, dict) and str(call.get("id") or "").strip()
        ]
        reasoning_content = next((value for value in matched if value), "")
        if not reasoning_content:
            return payload
        return {**payload, "reasoning_content": reasoning_content}

    @staticmethod
    def _legacy_tool_result_history_message(payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content")
        nested_call_ids: list[str] = []
        if isinstance(content, list):
            nested_call_ids = [
                str(item.get("tool_use_id") or item.get("tool_call_id") or "").strip()
                for item in content
                if isinstance(item, dict) and str(item.get("tool_use_id") or item.get("tool_call_id") or "").strip()
            ]
            result_text = "\n".join(
                str(item.get("text") or item.get("content") or "").strip()
                for item in content
                if isinstance(item, dict) and str(item.get("text") or item.get("content") or "").strip()
            )
        else:
            result_text = str(content or "").strip()
        call_id = str(payload.get("tool_call_id") or "").strip() or ",".join(nested_call_ids)
        lines = ["[tool.result]"]
        if call_id:
            lines.append(f"call_id: {call_id}")
        lines.extend(("content:", result_text, "请基于以上真实工具结果继续处理当前请求。"))
        return {"role": "user", "content": "\n".join(lines)}

    @staticmethod
    def _attach_model_images_to_projection(
        payload: dict[str, Any],
        *,
        model_image_inputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if str(payload.get("role") or "").strip().lower() != "user":
            return dict(payload)
        content = payload.get("content")
        if isinstance(content, list):
            blocks = [dict(item) for item in content if isinstance(item, dict)]
        else:
            blocks = [{"type": "text", "text": str(content or "")}]
        for item in list(model_image_inputs or [])[:5]:
            if not isinstance(item, dict):
                continue
            data_url = str(item.get("data_url") or "").strip()
            if data_url.startswith("data:image/"):
                label = model_image_source_label(item)
                if label:
                    blocks.append({"type": "text", "text": label})
                blocks.append({"type": "image_url", "image_url": {"url": data_url}})
        return {**dict(payload), "content": blocks}
