"""Observation hooks around tool execution and public argument snapshots."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Mapping
from ..client_protocol import ClientProtocolContext
from ..plugin_api import (
    AFTER_TOOL_CALL_HOOK,
    BEFORE_TOOL_CALL_HOOK,
    PluginHookEnvelope,
    PluginToolCallSnapshot,
    PluginToolResultSnapshot,
)
from ..tool_batch import cancelled_tool_result
from ..tool_invocation import TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD
from ..tool_runtime import ToolExecutionResult
from ..turn_coordination import cancellation_requested

logger = logging.getLogger("akane.engine")


class ToolHooksMixin:
    def bind_plugin_hook_broker(self, broker: Any | None) -> None:
        """Bind the generation-aware Hook broker without changing tool schemas."""

        if broker is not None and not callable(getattr(broker, "dispatch_from_consumer", None)):
            raise TypeError("invalid_plugin_hook_broker")
        self.plugin_hook_broker = broker

    def _dispatch_plugin_hook(self, hook: PluginHookEnvelope) -> Any:
        broker = getattr(self, "plugin_hook_broker", None)
        dispatch = getattr(broker, "dispatch_from_consumer", None)
        if not callable(dispatch):
            return None
        try:
            return dispatch(hook)
        except Exception:
            logger.warning("plugin hook dispatch failed hook_type=%s", hook.hook_type)
            return None

    def _plugin_hook_observes(self, hook_type: str) -> bool:
        broker = getattr(self, "plugin_hook_broker", None)
        observes = getattr(broker, "observes", None)
        if not callable(observes):
            return False
        try:
            return bool(observes(hook_type))
        except Exception:
            return False

    @staticmethod
    def _tool_hook_result_status(result: ToolExecutionResult) -> tuple[str, str]:
        capability = result.capability_result
        if capability is not None and capability.is_error:
            status = str(capability.status or "")
            return ("cancelled" if capability.status == "cancelled" else "failed"), str(
                capability.reason or (status if status not in {"ok", "success", "succeeded", "completed"} else "")
                or "adapter_business_error")
        reason = ""
        for event in list(result.stream_events or []):
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or "").strip().lower()
            if event.get("is_error") is True:
                reason = str(event.get("reason") or status or "adapter_business_error").strip()
                return ("cancelled" if status == "cancelled" else "failed"), reason
            if status in {"failed", "error", "rejected", "unavailable", "cancelled", "blocked",
                          "unavailable_before_dispatch", "execution_unknown", "timed_out"}:
                reason = str(event.get("reason") or status).strip()
                return status, reason
        if "<tool_use_error>" in str(result.followup_context or ""):
            return "failed", "tool_use_error"
        return "succeeded", ""

    def _execute_tool_call_with_hooks(
        self,
        *,
        call: dict[str, Any],
        final_output: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        client_context: ClientProtocolContext,
        memory_exclude_source_ids: list[str],
        request_context: dict[str, Any],
        domain_profile_id: str = "",
    ) -> ToolExecutionResult:
        """Execute one tool while publishing observation-only lifecycle Hooks."""

        tool_type = str(call.get("type") or "unknown").strip() or "unknown"
        if cancellation_requested():
            return cancelled_tool_result(tool_type)
        observes_before = self._plugin_hook_observes(BEFORE_TOOL_CALL_HOOK)
        observes_after = self._plugin_hook_observes(AFTER_TOOL_CALL_HOOK)
        hook_trace_id = ""
        if observes_before or observes_after:
            hook_trace_id = str(call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
            if not hook_trace_id:
                hook_trace_id = f"tool_{uuid.uuid4().hex[:20]}"
        if observes_before:
            tool_source = str(call.get(TOOL_SOURCE_FIELD) or "legacy_json").strip() or "legacy_json"
            arguments = {
                str(key): value
                for key, value in call.items()
                if key != "type" and not str(key).startswith("_tool_")
            }
            arguments_json = self._plugin_hook_arguments_json(arguments)
            self._dispatch_plugin_hook(
                PluginHookEnvelope(
                    hook_id=f"{hook_trace_id}:before",
                    hook_type=BEFORE_TOOL_CALL_HOOK,
                    occurred_at=max(now_ts, int(time.time())),
                    subject=f"tool:{tool_type}",
                    payload=PluginToolCallSnapshot(
                        invocation_id=hook_trace_id,
                        tool_name=tool_type,
                        source=tool_source,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=character_pack_id,
                        arguments_json=arguments_json,
                    ),
                )
            )
        started_at = time.monotonic()
        try:
            result = cancelled_tool_result(tool_type) if cancellation_requested() else self._execute_tool_call(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                tool_call=call,
                visual_payload=final_output,
                now_ts=now_ts,
                current_user_source_id=current_user_source_id,
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context=request_context,
                domain_profile_id=domain_profile_id,
            )
        except Exception as exc:
            result = ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[
                    {
                        "type": "tool_execution_failed",
                        "tool_type": tool_type,
                        "status": "failed",
                        "reason": f"tool_exception:{type(exc).__name__}",
                    }
                ],
                followup_context=(
                    f"<tool_use_error>工具 {tool_type} 执行失败（{type(exc).__name__}）；"
                    "请结合本批其它结果继续处理，不要假设该工具已经成功。</tool_use_error>"
                ),
            )
        if result is None:
            result = ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[
                    {
                        "type": "tool_execution_failed",
                        "tool_type": tool_type,
                        "status": "failed",
                        "reason": "empty_tool_result",
                    }
                ],
                followup_context="<tool_use_error>工具执行没有返回结果。</tool_use_error>",
            )
        if observes_after:
            hook_status, hook_reason = self._tool_hook_result_status(result)
            self._dispatch_plugin_hook(
                PluginHookEnvelope(
                    hook_id=f"{hook_trace_id}:after",
                    hook_type=AFTER_TOOL_CALL_HOOK,
                    occurred_at=int(time.time()),
                    subject=f"tool:{tool_type}",
                    payload=PluginToolResultSnapshot(
                        invocation_id=hook_trace_id,
                        tool_name=str(result.tool_type or tool_type),
                        status=hook_status,
                        duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                        reason=hook_reason,
                        model_feedback=str(result.followup_context or ""),
                        event_types=tuple(
                            str(event.get("type") or "").strip()
                            for event in result.stream_events
                            if isinstance(event, dict) and str(event.get("type") or "").strip()
                        ),
                    ),
                )
            )
        return result

    @staticmethod
    def _plugin_hook_arguments_json(arguments: Mapping[str, Any]) -> str:
        """Freeze public arguments while keeping credentials out of snapshots."""

        def sanitize(value: Any, *, field_name: str = "") -> Any:
            normalized_name = re.sub(r"[^a-z0-9]", "", field_name.lower())
            if normalized_name.endswith(
                ("accesstoken", "apikey", "authorization", "cookie", "password", "secret", "token")
            ):
                return "<configured>"
            if isinstance(value, Mapping):
                return {
                    str(key): sanitize(item, field_name=str(key))
                    for key, item in value.items()
                }
            if isinstance(value, (list, tuple)):
                return [sanitize(item) for item in value]
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return f"<non_json:{type(value).__name__}>"

        return json.dumps(
            sanitize(dict(arguments)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
