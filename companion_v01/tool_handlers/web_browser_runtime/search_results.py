"""Whole-entry search result pages with a lossless large-entry fallback."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..core import ToolExecutionResult


class SearchResultsMixin:
    def _paged_search_result(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        result: Mapping[str, Any],
        redaction_terms: list[str],
        start_entry: int,
        expected_fingerprint: str,
        owner_binding: str,
        profile_user_id: str,
        coverage_status: str,
        start_offset: int = 0,
        providers_attempted: Sequence[str] = (),
        fallback_used: bool = False,
    ) -> ToolExecutionResult:
        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        entries = self._search_evidence_entries(payload, redaction_terms=redaction_terms)
        fingerprint = self._web_evidence_fingerprint(json.dumps(entries, ensure_ascii=False, sort_keys=True))

        query_label = str(call.get("query") or " / ".join(str(item) for item in call.get("queries") or [])).strip()
        lines = self._search_header_lines(query_label, redaction_terms=redaction_terms)
        if not entries:
            raw_text = self._payload_to_text(payload, redaction_terms=redaction_terms)
            if self._search_payload_is_explicitly_empty(payload) or not raw_text or raw_text in {"[]", "{}", "null"}:
                if expected_fingerprint:
                    return self._cursor_failure("content_changed", "重新检索得到的结果与上一页不一致")
                lines.append("本次搜索真实返回 0 条结果。可以调整查询词、日期范围或来源后重试；不要编造结果。")
                content = "\n".join(lines)
                return self._web_page_result(
                    content=content,
                    complete=True,
                    continuation=None,
                    diagnostics={
                        "status": "no_results",
                        "action": action,
                        "entry_count": 0,
                        "complete": True,
                        "providers_attempted": list(providers_attempted),
                        "fallback_used": bool(fallback_used),
                    },
                    profile_user_id=profile_user_id,
                    coverage_status=coverage_status,
                    status="no_results",
                )
            return self._paged_raw_search_result(
                action=action,
                call=call,
                raw_text=raw_text,
                header_lines=lines,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=profile_user_id,
                coverage_status=coverage_status,
                providers_attempted=providers_attempted,
                fallback_used=fallback_used,
            )
        blocks = [self._render_search_entry(i + 1, entry) for i, entry in enumerate(entries)]
        header_size = sum(len(line) + 1 for line in lines)
        if any(len(block) + header_size > 32_000 for block in blocks):
            if start_entry:
                return self._cursor_failure("content_changed", "结果已转为文档分页，需要重新搜索")
            return self._paged_search_document(
                action=action, call=call, raw_text="\n".join(blocks), header_lines=lines,
                start_offset=start_offset, expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding, profile_user_id=profile_user_id,
                coverage_status=coverage_status, providers_attempted=list(providers_attempted),
                fallback_used=fallback_used, document_format="structured_text",
            )
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._cursor_failure('content_changed', '重新检索得到的结果与上一页不一致')
        if start_entry < 0 or start_entry >= len(entries):
            return self._cursor_failure("cursor_invalid", "结果条目偏移无效")
        if start_offset:
            return self._cursor_failure("cursor_invalid", "结构化搜索结果不能使用 raw 文档偏移")
        budget = 32_000
        chars = sum(len(line) + 1 for line in lines)
        shown_count = 0
        for index in range(start_entry, len(entries)):
            block = blocks[index]
            if shown_count > 0 and chars + len(block) + 1 > budget:
                break
            lines.append(block)
            chars += len(block) + 1
            shown_count += 1
        complete = start_entry + shown_count >= len(entries)
        if not complete:
            cursor = self._issue_web_cursor(
                action=action, call=call, owner_binding=owner_binding,
                fingerprint=fingerprint, start_entry=start_entry + shown_count,
            )
            lines.append(
                f"本页展示了 {start_entry + 1}—{start_entry + shown_count} 条结果，之后还有 {len(entries) - start_entry - shown_count} 条。"
                "如果这些结果已经足够回答，可以直接回答；只有确实需要看后续结果时才调用："
                f'web_search(cursor="{cursor}")'
            )
        else:
            lines.append("请只基于这些公开搜索结果回答；没查到或不确定的部分要明确说明。")
        content = "\n".join(lines)
        return self._web_page_result(
            content=content,
            complete=complete,
            continuation=(
                {"type": self.tool_type, "cursor": cursor} if not complete else None
            ),
            diagnostics={
                "action": action,
                "entry_count": len(entries),
                "shown_entries": shown_count,
                "start_entry": start_entry,
                "complete": complete,
                "providers_attempted": list(providers_attempted),
                "fallback_used": bool(fallback_used),
            },
            profile_user_id=profile_user_id,
            coverage_status=coverage_status,
        )

    def _search_header_lines(self, query_label: str, *, redaction_terms: list[str]) -> list[str]:
        lines = ["【AnySearch 联网搜索结果】"]
        if query_label:
            lines.append(f"查询：{self._sanitize_output(query_label, redaction_terms=redaction_terms)[:240]}")
        if self._search_needs_broader_coverage(query_label):
            lines.append(
                "覆盖提醒：这是时间范围、新闻汇总或多来源核验请求。若当前结果只覆盖单一日期或单一来源，当前任务尚未完成；"
                "请继续 batch_search（拆分日期、语言或来源）并对关键页面 extract。某个查询失败时优先换查询词或来源，不要直接放弃整个检索。"
            )
        lines.extend(
            [
                "证据口径：当前消息时间和本次检索时间只表示何时提问或查询，不能充当网页内容、行情数据或事件本身的日期。",
                "搜索摘要不是规范化行情快照。涉及时效性结论时，应从结果正文明确核对内容日期、来源时区和交易状态；只有时分没有日期、日期冲突或含义不明时，应继续提取正文或交叉搜索，仍不明确就降低置信度，不能擅自称为“今天盘中”或“今天收盘”。",
            ]
        )
        return lines
