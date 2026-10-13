"""Tool carrier normalization, frozen round validation, receipts and bounded diagnostics."""
from __future__ import annotations

import logging
import re
from typing import Any
from ..client_protocol import ClientProtocolContext
from ..tool_invocation import (
    NATIVE_TOOL_CALL_FIELD,
    NATIVE_TOOL_CALLS_FIELD,
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_EXECUTION_RECEIPT_FIELD,
    TOOL_EXECUTION_RECEIPTS_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_PARSE_ERROR_FIELD,
    invocation_to_legacy_tool_call,
)
from .. import tool_orchestration_engine

logger = logging.getLogger("akane.engine")


class ToolProtocolMixin:
    @staticmethod
    def _attach_tool_execution_receipts(
        output: dict[str, Any],
        generation_context: dict[str, Any],
    ) -> None:
        receipts = generation_context.get(TOOL_EXECUTION_RECEIPTS_FIELD)
        if not isinstance(receipts, dict) or not receipts:
            return
        output[TOOL_EXECUTION_RECEIPTS_FIELD] = {
            str(name): dict(receipt) for name, receipt in receipts.items() if isinstance(receipt, dict)
        }

    @staticmethod
    def _with_tool_execution_receipt(
        call: Any,
        generation_context: dict[str, Any],
    ) -> Any:
        if not isinstance(call, dict):
            return call
        receipts = generation_context.get(TOOL_EXECUTION_RECEIPTS_FIELD)
        receipt = receipts.get(str(call.get("type") or "").strip()) if isinstance(receipts, dict) else None
        if not isinstance(receipt, dict):
            return call
        enriched = dict(call)
        enriched[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
        return enriched

    def _max_tool_rounds(self, *, domain_profile_id: str = "") -> int:
        from .tool_rounds import max_tool_rounds as _fn

        return _fn(domain_profile_id=domain_profile_id)

    def _tool_round_warning_remaining(self, *, hard_limit: int) -> int:
        from .tool_rounds import tool_round_warning_remaining as _fn

        return _fn(hard_limit=hard_limit)

    def _build_tool_round_warning(
        self,
        *,
        used_rounds: int,
        hard_limit: int,
    ) -> str:
        from .tool_rounds import build_tool_round_warning as _fn

        manager = getattr(self, "memcore_manager", None)
        return _fn(
            used_rounds=used_rounds,
            hard_limit=hard_limit,
            memcore_enabled=bool(manager is not None and getattr(manager, "enabled", False)),
        )

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        from .tool_rounds import tool_call_signature as _fn

        return _fn(tool_call)

    def _describe_tool_call_for_prompt(self, tool_call: dict[str, Any]) -> str:
        from .tool_rounds import describe_tool_call_for_prompt as _fn

        return _fn(tool_call)

    def _build_tool_working_stream_event(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        from .tool_rounds import build_tool_working_stream_event as _fn

        return _fn(tool_call)

    def _prepare_tool_round_decision(
        self,
        *,
        final_output: dict[str, Any],
        user_message: str,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
        domain_profile_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        final_output, tool_calls, rejections = self._prepare_tool_round_decisions(
            final_output=final_output,
            user_message=user_message,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )
        return (
            final_output,
            tool_calls[0] if tool_calls else None,
            rejections[0] if rejections else "",
        )

    def _prepare_tool_round_decisions(
        self,
        *,
        final_output: dict[str, Any],
        user_message: str,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
        domain_profile_id: str = "",
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        execution_receipts = final_output.pop(TOOL_EXECUTION_RECEIPTS_FIELD, None)
        # M66-C frozen round: retrieve the CapabilitySelection resolved in
        # prepare_context() so normalize does not re-resolve handlers.
        frozen_capability_selection = final_output.pop(TOOL_CAPABILITY_SELECTION_FIELD, None)
        native_tool_calls = final_output.pop(NATIVE_TOOL_CALLS_FIELD, None)
        native_tool_call = final_output.pop(NATIVE_TOOL_CALL_FIELD, None)
        native_carrier_present = bool(
            (isinstance(native_tool_calls, list) and any(isinstance(call, dict) and call for call in native_tool_calls))
            or (isinstance(native_tool_call, dict) and native_tool_call)
        )
        raw_tool_calls = (
            [dict(call) for call in native_tool_calls if isinstance(call, dict) and call]
            if isinstance(native_tool_calls, list)
            else []
        )
        if not raw_tool_calls and isinstance(native_tool_call, dict) and native_tool_call:
            raw_tool_calls = [native_tool_call]
        if not raw_tool_calls:
            final_output = self._promote_narrated_tool_call(
                final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            raw_tool_call = final_output.get("tool_call")
            if isinstance(raw_tool_call, dict) and raw_tool_call:
                raw_tool_calls = [raw_tool_call]
        else:
            final_output["tool_call"] = None
        tool_calls: list[dict[str, Any]] = []
        rejections: list[str] = []
        resolved_round_handlers = getattr(frozen_capability_selection, "resolved_handlers", {}) or {}
        native_model_names = tuple(
            str(call.get("type") or "").strip()
            for call in raw_tool_calls
            if (
                isinstance(call, dict)
                and not str(call.get(TOOL_PARSE_ERROR_FIELD) or "").strip()
                and str(call.get("type") or "").strip().startswith("mcp_")
                and str(call.get("type") or "").strip() not in resolved_round_handlers
            )
        )
        direct_mcp_aliases: dict[str, str] = {}
        direct_mcp_selection = frozen_capability_selection
        if native_carrier_present and native_model_names:
            from .tool_rounds import resolve_unloaded_mcp_native_aliases

            direct_mcp_aliases, direct_mcp_selection = resolve_unloaded_mcp_native_aliases(
                self,
                model_tool_names=native_model_names,
                profile_user_id=profile_user_id,
                client_context=client_context,
                domain_profile_id=domain_profile_id,
                capability_selection=frozen_capability_selection,
            )
        recovered_native_aliases: dict[str, str] | None = None
        for raw_tool_call in raw_tool_calls:
            raw_tool_name = str(raw_tool_call.get("type") or "").strip()
            call_capability_selection = frozen_capability_selection
            declared_native_names = {
                str(name or "").strip()
                for name in getattr(frozen_capability_selection, "native_tool_names", ()) or ()
                if str(name or "").strip()
            }
            native_aliases = getattr(frozen_capability_selection, "native_tool_aliases", {}) or {}
            canonical_native_name = str(native_aliases.get(raw_tool_name) or "").strip()
            if (
                native_carrier_present
                and raw_tool_name not in resolved_round_handlers
                and raw_tool_name not in declared_native_names
                and not canonical_native_name
                and not str(raw_tool_call.get(TOOL_PARSE_ERROR_FIELD) or "").strip()
            ):
                if recovered_native_aliases is None:
                    from ..native_tool_schema import build_openai_native_tool_specs, native_tool_model_name_map

                    selected_native_names = set(declared_native_names)
                    alias_handlers = dict(resolved_round_handlers)
                    if not alias_handlers and frozen_capability_selection is None:
                        try:
                            alias_handlers = self._resolve_tool_handlers(
                                client_context=client_context,
                                profile_user_id=profile_user_id,
                                session_id=session_id,
                                domain_profile_id=domain_profile_id,
                            )
                        except Exception:
                            alias_handlers = {}
                    if not selected_native_names:
                        selected_native_names = {
                            str(name or "").strip()
                            for name, handler in alias_handlers.items()
                            if str(name or "").strip()
                            and bool(getattr(handler, "policy_accepted_native_tool", False))
                        }
                    selected_native_handlers = {
                        name: handler
                        for name, handler in alias_handlers.items()
                        if name in selected_native_names
                    }
                    recovered_native_aliases = native_tool_model_name_map(
                        build_openai_native_tool_specs(
                            selected_native_handlers,
                            allowed_tool_names=selected_native_names,
                        )
                    )
                canonical_native_name = str(recovered_native_aliases.get(raw_tool_name) or "").strip()
            recovered_native_name = str((recovered_native_aliases or {}).get(raw_tool_name) or "").strip()
            if canonical_native_name and (
                canonical_native_name in resolved_round_handlers
                or recovered_native_name == canonical_native_name
            ):
                raw_tool_call = dict(raw_tool_call)
                raw_tool_call["type"] = canonical_native_name
                raw_tool_call.setdefault(TOOL_MODEL_NAME_FIELD, raw_tool_name)
                raw_tool_name = canonical_native_name
            if raw_tool_name == "capability_invoke":
                from ..capability_exposure import route_invocation
                raw_tool_call, call_capability_selection = route_invocation(
                    raw_tool_call, frozen_capability_selection,
                )
            if raw_tool_name == "invoke_mcp":
                from ..capability_exposure import route_mcp_invocation
                raw_tool_call, call_capability_selection = route_mcp_invocation(
                    raw_tool_call, frozen_capability_selection,
                )
            canonical_mcp_name = direct_mcp_aliases.get(raw_tool_name, "")
            if canonical_mcp_name:
                raw_tool_call = dict(raw_tool_call)
                raw_tool_call["type"] = canonical_mcp_name
                raw_tool_call[TOOL_MODEL_NAME_FIELD] = raw_tool_name
                call_capability_selection = direct_mcp_selection
            receipt = execution_receipts.get(raw_tool_name) if isinstance(execution_receipts, dict) else None
            if isinstance(receipt, dict):
                raw_tool_call = dict(raw_tool_call)
                raw_tool_call[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
            parsed_call = tool_orchestration_engine.parse_tool_call(
                raw_tool_call,
                capability_selection=call_capability_selection,
            )
            tool_call = (
                invocation_to_legacy_tool_call(parsed_call, include_metadata=True)
                if parsed_call is not None
                else None
            )
            if tool_call:
                tool_calls.append(tool_call)
                continue
            rejection = self._describe_tool_call_rejection(
                raw_tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                domain_profile_id=domain_profile_id,
            )
            if rejection:
                rejections.append(rejection)
        return final_output, tool_calls, rejections

    def _record_tool_call_rejection(
        self,
        *,
        final_output: dict[str, Any],
        rejection: str,
        tool_followups: list[str],
        session_id: str,
    ) -> None:
        reason_tool, reason_code = self._tool_call_rejection_log_fields(
            final_output=final_output,
            rejection=rejection,
        )
        logger.warning(
            "tool_call_rejected session=%s reason_tool=%s reason=%s",
            session_id,
            reason_tool or "unknown",
            reason_code,
        )
        tool_followups.append(rejection)

    @staticmethod
    def _tool_protocol_failure_output(*, rejections: list[str]) -> dict[str, Any]:
        """Expose a repeated carrier failure without pretending completion."""

        return {
            "emotion": "",
            "speech": (
                "刚才连续的工具请求没有形成可执行的协议，已经完成的工具结果仍然保留，"
                "但这次任务没有完整结束。"
            ),
            "tool_call": None,
            "memory_metadata": {},
            "_agent_failure": {
                "status": "protocol_error",
                "reason": "tool_call_protocol_invalid",
                "rejection_count": len(list(rejections or [])),
            },
        }

    @staticmethod
    def _tool_call_rejection_log_fields(
        *,
        final_output: dict[str, Any],
        rejection: str,
    ) -> tuple[str, str]:
        """Return bounded diagnostic labels without logging model arguments.

        Rejection feedback is model-facing text and may contain the submitted
        command, URL, query, path, or other user material.  Logs only need the
        attempted capability and rejection class; the full feedback remains in
        the active tool round and MemCore rather than crossing the log boundary.
        """

        reason_tool = str((final_output.get("tool_call") or {}).get("type") or "").strip()
        text = str(rejection or "")
        if not reason_tool:
            match = re.search(r"工具(?:「\s*|\s+)([A-Za-z][A-Za-z0-9_.:-]{0,79})(?:」)?", text)
            if match is not None:
                reason_tool = match.group(1)
        if not text.strip():
            reason_code = "rejection_detail_missing"
        elif "兼容 JSON tool_call" in text or "歧义调用" in text:
            reason_code = "native_tool_wrong_carrier"
        elif "参数不完整" in text or "格式不对" in text or "参数不符合" in text:
            reason_code = "bad_args"
        elif "执行器不可用" in text or "当前不在线" in text or "没有提供" in text:
            reason_code = "not_available"
        elif "本轮不可用" in text or "不存在的工具" in text:
            reason_code = "unknown_tool"
        else:
            reason_code = "validation_rejected"
        return reason_tool[:80], reason_code

    def _normalize_tool_call(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
        capability_selection: Any = None,
    ) -> dict[str, Any] | None:
        return tool_orchestration_engine.normalize_tool_call(
            self,
            value,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
        )

    def _describe_tool_call_rejection(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
    ) -> str:
        return tool_orchestration_engine.classify_tool_call_rejection(
            self,
            value,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )

    def _promote_narrated_tool_call(
        self,
        final_output: dict[str, Any],
        *,
        user_message: str,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        return tool_orchestration_engine.promote_narrated_tool_call(
            self,
            final_output,
            user_message=user_message,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
