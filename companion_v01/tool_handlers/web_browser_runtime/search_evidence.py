"""Public evidence, source dates and coverage hints."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse


class SearchEvidenceMixin:
    def _search_evidence_entries(self, payload: Any, *, redaction_terms: list[str]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in self._coerce_search_results(payload):
            title = self._sanitize_output(
                str(item.get("title") or item.get("name") or "无标题"), redaction_terms=redaction_terms
            )[:160]
            url = self._sanitize_output(
                str(item.get("url") or item.get("link") or ""), redaction_terms=redaction_terms
            )[:500]
            snippet = self._sanitize_output(
                str(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("description")
                    or item.get("content")
                    or ""
                ),
                redaction_terms=redaction_terms,
            )
            date = self._search_result_date_hint(item)
            if date:
                date = self._sanitize_output(date, redaction_terms=redaction_terms)[:160]
            source = self._sanitize_output(
                str(item.get("source") or item.get("site") or item.get("provider") or ""),
                redaction_terms=redaction_terms,
            )[:160]
            if not source and url:
                try:
                    source = str(urlparse(url).hostname or "")[:160]
                except ValueError:
                    source = ""
            entries.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": source,
                    "published_at": date,
                }
            )
        return entries

    def _render_search_entry(self, index: int, entry: Mapping[str, Any]) -> str:
        lines = [f"{index}. {str(entry.get('title') or '')}"]
        if entry.get("url"):
            lines.append(f"   URL: {str(entry.get('url') or '')}")
        if entry.get("source"):
            lines.append(f"   来源: {str(entry.get('source') or '')}")
        if entry.get("published_at"):
            lines.append(
                "   来源日期字段（需结合正文判断含义）: " + str(entry.get("published_at") or "")
            )
        if entry.get("snippet"):
            lines.append(f"   摘要: {str(entry.get('snippet') or '')}")
        return "\n".join(lines)

    def _search_result_date_hint(self, item: Mapping[str, Any]) -> str:
        for key in (
            "published_at",
            "publishedAt",
            "published_date",
            "publication_date",
            "updated_at",
            "updatedAt",
            "date",
            "datetime",
        ):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""

    def _search_needs_broader_coverage(self, query: str) -> bool:
        text = str(query or "").strip().lower()
        if not text:
            return False
        return bool(
            re.search(
                r"(?:最近|过去|近\s*\d+|本周|上周|一周|周报|月报|新闻|消息|动态|事件|回顾|汇总|梳理|"
                r"weekly|week|news|updates?|between|from\s+.+\s+to)",
                text,
                re.IGNORECASE,
            )
        )
