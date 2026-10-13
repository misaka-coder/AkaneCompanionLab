"""Search call and cursor orchestration through actual providers."""

from __future__ import annotations

from typing import Any, Mapping

from ...local_capability_config import get_mcp_server_runtime_config
from ..core import ToolExecutionContext, ToolExecutionResult


class SearchExecutionMixin:
    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        runtime_profile_user_id = self._resolve_runtime_profile_user_id(context)
        cursor_payload: dict[str, Any] | None = None
        start_entry = 0
        start_offset = 0
        if str(call.get("cursor") or "").strip():
            resolved = self._resolve_cursor_call(call=call, context=context)
            if not isinstance(resolved, dict):
                return resolved
            cursor_payload = resolved
            cursor_args = dict(resolved.get("arguments") or {}) if isinstance(resolved.get("arguments"), Mapping) else {}
            call = {
                "type": self.tool_type,
                "action": str(resolved.get("action") or "search"),
                **{key: value for key, value in cursor_args.items() if value not in (None, "")},
            }
            start_entry = int(resolved.get("start_entry") or 0)
            start_offset = int(resolved.get("start_offset") or 0)
            expected_fingerprint = str(resolved.get("fingerprint") or "")
        else:
            expected_fingerprint = ""

        server = get_mcp_server_runtime_config(
            base_dir=self.config_base_dir,
            profile_user_id=runtime_profile_user_id,
            server_id=self.server_id,
        )
        if server and not bool(server.get("enabled")):
            return self._failure("disabled", "AnySearch MCP 当前是关闭状态。")

        action = str(call.get("action") or "search").strip()
        arguments = self._build_mcp_arguments(call)
        redaction_terms = self._redaction_terms_for_server(server) if server else []
        fetched = self._fetch_web_result(
            server=server, profile_user_id=runtime_profile_user_id, action=action,
            arguments=arguments, redaction_terms=redaction_terms,
        )
        if isinstance(fetched, ToolExecutionResult):
            return fetched
        result, providers_attempted, fallback_used = fetched

        owner_binding = self._web_cursor_owner_binding(context)
        query_label = str(call.get("query") or " / ".join(str(item) for item in call.get("queries") or [])).strip()
        coverage_status = "unverified" if self._search_needs_broader_coverage(query_label) else "not_required"
        if action in {"search", "batch_search"}:
            return self._paged_search_result(
                action=action,
                call=call,
                result=result if isinstance(result, dict) else {},
                redaction_terms=redaction_terms,
                start_entry=start_entry,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=runtime_profile_user_id,
                coverage_status=coverage_status,
                providers_attempted=providers_attempted,
                fallback_used=fallback_used,
            )
        if action == "extract":
            return self._paged_extract_result(
                call=call,
                result=result if isinstance(result, dict) else {},
                redaction_terms=redaction_terms,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=runtime_profile_user_id,
            )
        if action == "get_sub_domains":
            return self._paged_sub_domains_result(
                call=call,
                result=result if isinstance(result, dict) else {},
                redaction_terms=redaction_terms,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=runtime_profile_user_id,
            )
        return self._failure("unsupported_action", f"AnySearch 不支持的动作：{action}。")
