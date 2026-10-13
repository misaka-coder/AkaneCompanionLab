"""Fingerprint-checked extracted article and domain result pages."""

from __future__ import annotations

from typing import Any, Mapping

from ..core import ToolExecutionResult


class SearchDocumentsMixin:
    def _paged_extract_result(
        self,
        *,
        call: Mapping[str, Any],
        result: Mapping[str, Any],
        redaction_terms: list[str],
        start_offset: int,
        expected_fingerprint: str,
        owner_binding: str,
        profile_user_id: str = "",
    ) -> ToolExecutionResult:
        from ...paged_reading import (
            FIRST_PAGE_BUDGET_CHARS,
            FIRST_PAGE_BUDGET_LINES,
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            slice_page,
        )

        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        data = self._first_mapping(payload)
        title = self._sanitize_output(str(data.get("title") or data.get("name") or ""), redaction_terms=redaction_terms)
        text = self._sanitize_output(
            str(data.get("text") or data.get("content") or data.get("markdown") or data.get("body") or ""),
            redaction_terms=redaction_terms,
        )
        if not text:
            text = self._payload_to_text(payload, redaction_terms=redaction_terms)
        url = self._sanitize_output(str(call.get("url") or ""), redaction_terms=redaction_terms)[:500]
        fingerprint = self._web_evidence_fingerprint(text)
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._cursor_failure('stale_cursor', '该网页重新提取后的内容与上一页不一致，可能已被更新')

        header_lines = [
            "【AnySearch 网页内容提取结果】",
            f"URL: {url}",
        ]
        if title:
            header_lines.append(f"标题：{self._clip(title, 160)}")
        header_lines.append(
            "证据口径：本轮网页提取发生时间不等于正文事实日期；涉及时效性事实时，以正文明确的日期、更新字段和来源时区为准。"
        )
        budget_chars, budget_lines = (
            (FIRST_PAGE_BUDGET_CHARS, FIRST_PAGE_BUDGET_LINES)
            if not expected_fingerprint
            else (NEXT_PAGE_BUDGET_CHARS, NEXT_PAGE_BUDGET_LINES)
        )
        header_size = sum(len(line) + 1 for line in header_lines) + len("正文摘录：") + 1
        page_text, next_offset, total_chars = slice_page(
            text or "没有拿到可用正文。",
            start=start_offset,
            budget_chars=max(500, budget_chars - header_size),
            budget_lines=budget_lines,
        )
        complete = next_offset >= total_chars
        lines = [*header_lines, "正文摘录：", page_text]
        continuation = None
        if not complete and text:
            cursor = self._issue_web_cursor(
                action='extract', call=call, owner_binding=owner_binding,
                fingerprint=fingerprint, offset=next_offset,
            )
            continuation = {"type": self.tool_type, "cursor": cursor}
            lines.append(
                "正文还有未展示部分。如果当前内容已经足够回答，可以直接回答；"
                f"只有确实需要后续正文时，才调用 web_search(cursor=\"{cursor}\")。"
            )
        else:
            lines.append(f"已读完本次提取到的全部正文（共 {total_chars} 字）。")
        content = "\n".join(lines)
        return self._web_page_result(
            content=content,
            complete=complete,
            continuation=continuation,
            diagnostics={
                "action": "extract",
                "shown_chars": len(page_text),
                "total_chars": total_chars,
                "complete": complete,
            },
            profile_user_id=profile_user_id,
        )

    def _paged_sub_domains_result(
        self,
        *,
        call: Mapping[str, Any],
        result: Mapping[str, Any],
        redaction_terms: list[str],
        start_offset: int,
        expected_fingerprint: str,
        owner_binding: str,
        profile_user_id: str = "",
    ) -> ToolExecutionResult:
        from ...paged_reading import (
            FIRST_PAGE_BUDGET_CHARS,
            slice_page,
        )

        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        text = self._sanitize_output(
            self._payload_to_text(payload, redaction_terms=redaction_terms), redaction_terms=redaction_terms
        )
        fingerprint = self._web_evidence_fingerprint(text)
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._cursor_failure('content_changed', '重新查询得到的结果与上一页不一致')
        domains = ", ".join(str(item) for item in call.get("domains") or [])
        header_lines = [
            "【AnySearch 域名能力结果】",
            f"域名：{self._sanitize_output(domains, redaction_terms=redaction_terms)[:240]}",
        ]
        header_size = sum(len(line) + 1 for line in header_lines)
        page_text, next_offset, total_chars = slice_page(
            text or "没有拿到可用结果。",
            start=start_offset,
            budget_chars=max(500, FIRST_PAGE_BUDGET_CHARS - header_size),
            budget_lines=2000,
        )
        complete = next_offset >= total_chars
        lines = [*header_lines, page_text]
        continuation = None
        if not complete:
            cursor = self._issue_web_cursor(
                action='get_sub_domains', call=call, owner_binding=owner_binding,
                fingerprint=fingerprint, offset=next_offset,
            )
            continuation = {"type": self.tool_type, "cursor": cursor}
            lines.append(
                "结果还有未展示部分。如果当前内容已经足够回答，可以直接回答；"
                f"只有确实需要后续内容时，才调用 web_search(cursor=\"{cursor}\")。"
            )
        content = "\n".join(lines)
        return self._web_page_result(
            content=content,
            complete=complete,
            continuation=continuation,
            diagnostics={
                "action": "get_sub_domains",
                "shown_chars": len(page_text),
                "total_chars": total_chars,
                "complete": complete,
            },
            profile_user_id=profile_user_id,
        )
