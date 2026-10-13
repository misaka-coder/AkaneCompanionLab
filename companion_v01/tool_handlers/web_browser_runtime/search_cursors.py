"""Owner-bound search cursors and shared result envelopes."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from ..core import ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class SearchCursorsMixin:
    def _resolve_cursor_call(
        self, *, call: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any] | ToolExecutionResult:
        from ...paged_reading import parse_json_payload, parse_paged_cursor

        cursor = str(call.get("cursor") or "").strip()
        payload = parse_json_payload(
            parse_paged_cursor(cursor, tool="we", binding=self._web_cursor_owner_binding(context))
        )
        if not isinstance(payload, dict):
            return self._cursor_failure("cursor_invalid", "cursor 不属于当前用户/会话，或已损坏")
        action = str(payload.get("a") or "").strip()
        if action not in self.ALLOWED_ACTIONS:
            return self._cursor_failure("cursor_invalid", "cursor 引用的动作无效")
        arguments = payload.get("args")
        if not isinstance(arguments, Mapping) or "cursor" in arguments:
            return self._cursor_failure("cursor_invalid", "cursor 参数无效")
        fingerprint = str(payload.get("f") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            return self._cursor_failure("cursor_invalid", "cursor 没有有效内容指纹")
        positions = []
        for key in ("s", "o"):
            value = payload.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"\d+", str(value)):
                return self._cursor_failure("cursor_invalid", "cursor 偏移无效")
            try:
                positions.append(int(value))
            except ValueError:
                return self._cursor_failure("cursor_invalid", "cursor 偏移无效")
        normalized = self.normalize_call({**arguments, "type": self.tool_type, "action": action})
        if not normalized:
            return self._cursor_failure("cursor_invalid", "cursor 引用的调用参数无效")
        return {
            "action": action,
            "arguments": {key: value for key, value in normalized.items() if key not in {"type", "action"}},
            "start_entry": positions[0] if action in {"search", "batch_search"} else 0,
            "start_offset": positions[1],
            "fingerprint": fingerprint,
        }

    def _issue_web_cursor(
        self, *, action: str, call: Mapping[str, Any], owner_binding: str,
        fingerprint: str, start_entry: int | None = None, offset: int | None = None,
    ) -> str:
        from ...paged_reading import json_payload, make_paged_cursor

        payload = {
            "a": action,
            "args": {str(key): value for key, value in call.items() if key != "type" and value not in (None, "")},
            "f": fingerprint,
        }
        if start_entry is not None:
            payload["s"] = start_entry
        if offset is not None:
            payload["o"] = offset
        return make_paged_cursor(tool="we", binding=owner_binding, payload=json_payload(payload))

    def _cursor_failure(self, status: str, reason: str) -> ToolExecutionResult:
        from ...paged_reading import page_failure_feedback

        content = page_failure_feedback(status=status, tool=self.tool_type, detail=reason)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {"type": "web_search_completed", "provider": "anysearch", "status": status, "reason": reason}
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics={"status": status},
            ),
        )

    def _web_cursor_owner_binding(self, context: ToolExecutionContext) -> str:
        from ...paged_reading import cursor_binding

        return cursor_binding(
            "web_search",
            self._resolve_runtime_profile_user_id(context),
            context.session_id,
        )

    @staticmethod
    def _web_evidence_fingerprint(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _web_page_result(
        *,
        content: str,
        complete: bool,
        continuation: Mapping[str, Any] | None,
        diagnostics: Mapping[str, Any],
        profile_user_id: str = "",
        coverage_status: str = "not_required",
        status: str = "ok",
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_type="web_search",
            stream_events=[
                {
                    "type": "web_search_completed",
                    "provider": "anysearch",
                    "status": str(status or "ok"),
                    "complete": bool(complete),
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=bool(complete),
                continuation=dict(continuation) if continuation else None,
                diagnostics=dict(diagnostics),
            ),
            state_updates={
                "web_search_status": str(status or "ok"),
                "web_search_provider": "anysearch",
                "web_search_profile_user_id": str(profile_user_id or ""),
                "web_search_coverage_status": str(coverage_status or ""),
                "web_search_complete": bool(complete),
            },
        )
