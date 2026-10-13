"""Truthful provider failures and recovery suggestions."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..core import ToolExecutionResult


class SearchFeedbackMixin:
    def _mcp_tool_failure(
        self,
        *,
        action: str,
        result: Mapping[str, Any],
        redaction_terms: list[str],
        profile_user_id: str,
    ) -> ToolExecutionResult:
        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        detail = self._clip(
            self._payload_to_text(payload, redaction_terms=redaction_terms),
            600,
        )
        reason = self._mcp_tool_error_reason(result=result, detail=detail)
        detail_text = f"\n服务返回：{detail}" if detail else ""
        recovery = (
            "可用 browser_page 的 navigate/read_text 读取公开正文。"
            if action == "extract"
            else "可根据错误调整查询或更换公开来源。"
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "web_search_completed",
                    "provider": "anysearch",
                    "action": action,
                    "status": "failed",
                    "reason": reason,
                }
            ],
            followup_context=(
                f"AnySearch 本次 {action} 请求没有拿到结果。{detail_text}\n"
                "这只表示当前调用或来源失败，不代表整个搜索服务不可用。"
                f"{recovery}不要编造结果。"
            ),
            state_updates={
                "web_search_status": "failed",
                "web_search_reason": reason,
                "web_search_provider": "anysearch",
                "web_search_profile_user_id": profile_user_id,
            },
        )

    @staticmethod
    def _mcp_tool_error_reason(*, result: Mapping[str, Any], detail: str) -> str:
        candidates = [
            result.get("code"),
            result.get("error_code"),
            result.get("reason"),
            *(str(detail or "").splitlines()[:2]),
        ]
        for candidate in candidates:
            reason = str(candidate or "").strip().lower()
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
                return reason
        return "mcp_tool_result_error"

    def _failure(self, status: str, message: str) -> ToolExecutionResult:
        reason = str(status or "unavailable").strip()[:120]
        transient = reason in {
            "mcp_tool_call_timeout",
            "mcp_call_failed",
            "mcp_result_error",
            "anysearch_network_unavailable",
            "anysearch_rate_limited",
            "anysearch_http_error",
            "anysearch_upstream_error",
            "anysearch_rest_failed",
        }
        next_step = (
            "如果仍有工具预算和其它安全的只读路径，可以换查询词、拆小批次、换公开来源或改用其它检索工具继续核验；"
            "如果没有可用路径，再基于现有证据降级回答。"
            if transient
            else "这是配置、启用状态或其它当前不可恢复的问题，不要反复调用同一工具。"
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {"type": "web_search_completed", "provider": "anysearch", "status": "unavailable", "reason": reason}
            ],
            followup_context=(
                f"AnySearch 联网能力暂时不可用：{message} 状态：{reason}。{next_step}"
                "不要编造搜索结果，也不要把一次来源失败说成所有公开信息渠道都不可用。"
            ),
            state_updates={"web_search_status": "unavailable", "web_search_reason": reason},
        )
