"""Lossless continuation of raw or oversized search documents."""

from __future__ import annotations

from typing import Any, Mapping

from ..core import ToolExecutionResult


class SearchDocumentPagingMixin:
    def _paged_raw_search_result(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        raw_text: str,
        header_lines: list[str],
        start_offset: int,
        expected_fingerprint: str,
        owner_binding: str,
        profile_user_id: str,
        coverage_status: str,
        providers_attempted: list[str],
        fallback_used: bool,
    ) -> ToolExecutionResult:
        return self._paged_search_document(
            action=action, call=call, raw_text=raw_text, header_lines=header_lines,
            start_offset=start_offset, expected_fingerprint=expected_fingerprint,
            owner_binding=owner_binding, profile_user_id=profile_user_id,
            coverage_status=coverage_status, providers_attempted=providers_attempted,
            fallback_used=fallback_used, document_format="markdown_raw",
        )

    def _paged_search_document(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        raw_text: str,
        header_lines: list[str],
        start_offset: int,
        expected_fingerprint: str,
        owner_binding: str,
        profile_user_id: str,
        coverage_status: str,
        providers_attempted: list[str],
        fallback_used: bool,
        document_format: str,
    ) -> ToolExecutionResult:
        from ...paged_reading import (
            FIRST_PAGE_BUDGET_CHARS,
            FIRST_PAGE_BUDGET_LINES,
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            slice_page,
        )

        fingerprint = self._web_evidence_fingerprint(raw_text)
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._cursor_failure("content_changed", "重新检索得到的原始文档与上一页不一致")
        if start_offset < 0 or (start_offset > 0 and start_offset >= len(raw_text)):
            return self._cursor_failure("cursor_invalid", "文档偏移无效")
        budget_chars, budget_lines = (
            (FIRST_PAGE_BUDGET_CHARS, FIRST_PAGE_BUDGET_LINES)
            if not expected_fingerprint
            else (NEXT_PAGE_BUDGET_CHARS, NEXT_PAGE_BUDGET_LINES)
        )
        page_text, next_offset, total_chars = slice_page(
            raw_text,
            start=start_offset,
            budget_chars=max(500, budget_chars - sum(len(line) + 1 for line in header_lines)),
            budget_lines=budget_lines,
        )
        complete = next_offset >= total_chars
        raw = document_format == "markdown_raw"
        format_note = "结果格式：markdown_raw（保留服务返回的原始公开文档）" if raw else "结果格式：structured_text（长条目按文档分页，保留完整证据）"
        lines = [*header_lines, format_note, "原始搜索文档：" if raw else "搜索结果文档：", page_text]
        continuation = None
        if not complete:
            cursor = self._issue_web_cursor(
                action=action, call=call, owner_binding=owner_binding,
                fingerprint=fingerprint, offset=next_offset,
            )
            continuation = {"type": self.tool_type, "cursor": cursor}
            lines.append(
                f"{'原始文档' if raw else '文档'}已展示 {next_offset}/{total_chars} 字；够用即可回答，需要后续时调用 "
                f'web_search(cursor="{cursor}")。'
            )
        else:
            lines.append(f"已读完本次返回的{'原始搜索文档' if raw else '搜索结果文档'}（共 {total_chars} 字）。")
        return self._web_page_result(
            content="\n".join(lines),
            complete=complete,
            continuation=continuation,
            diagnostics={
                "status": "ok",
                "action": action,
                "format": document_format,
                "shown_chars": len(page_text),
                "total_chars": total_chars,
                "complete": complete,
                "providers_attempted": list(providers_attempted),
                "fallback_used": bool(fallback_used),
            },
            profile_user_id=profile_user_id,
            coverage_status=coverage_status,
        )
