"""Structured and Markdown provider payload parsing."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping


class SearchPayloadsMixin:
    def _extract_payload(self, result: Mapping[str, Any], *, redaction_terms: list[str]) -> Any:
        for key in ("results", "items", "data", "result"):
            value = result.get(key)
            if value not in (None, "", [], {}):
                return value
        content = result.get("content")
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, Mapping):
                    text = str(item.get("text") or item.get("content") or "").strip()
                else:
                    text = str(item or "").strip()
                if text:
                    parsed = self._try_parse_json_text(text)
                    if parsed is not None:
                        return parsed
                    texts.append(text)
            if texts:
                return "\n".join(self._sanitize_output(text, redaction_terms=redaction_terms) for text in texts)
        return dict(result)

    def _try_parse_json_text(self, text: str) -> Any | None:
        try:
            return json.loads(text)
        except Exception:
            return None

    def _coerce_search_results(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, Mapping):
            for key in ("results", "items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [dict(item) for item in value if isinstance(item, Mapping)]
                if isinstance(value, str):
                    parsed = self._parse_markdown_search_results(value)
                    if parsed:
                        return parsed
            if any(key in payload for key in ("title", "url", "link", "snippet", "content")):
                return [dict(payload)]
        if isinstance(payload, list):
            results = []
            for item in payload:
                if isinstance(item, Mapping):
                    results.append(dict(item))
                elif isinstance(item, str):
                    results.append({"title": item})
            return results
        if isinstance(payload, str):
            return self._parse_markdown_search_results(payload)
        return []

    def _parse_markdown_search_results(self, value: str) -> list[dict[str, Any]]:
        """Parse common Markdown search lists without discarding the raw fallback."""

        entries: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        snippet_lines: list[str] = []
        pending_title = ""

        def flush() -> None:
            nonlocal current, snippet_lines
            if current is None:
                return
            snippet = "\n".join(line for line in snippet_lines if line).strip()
            if snippet:
                current["snippet"] = snippet
            entries.append(current)
            current = None
            snippet_lines = []

        for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if not line or line.startswith("```"):
                continue
            link = re.search(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", line)
            if link:
                flush()
                title = re.sub(r"^\s*(?:[-*+]\s*|\d+[.)]\s*)", "", link.group(1)).strip()
                current = {"title": title or "无标题", "url": link.group(2).strip()}
                tail = (line[: link.start()] + " " + line[link.end() :]).strip(" -*:—")
                tail = re.sub(r"^\d+[.)]\s*", "", tail).strip()
                if tail:
                    snippet_lines.append(tail)
                pending_title = ""
                continue
            url_match = re.match(r"^(?:url|链接|网址)\s*[:：]\s*(https?://\S+)$", line, re.IGNORECASE)
            if url_match:
                flush()
                current = {"title": pending_title or "无标题", "url": url_match.group(1).rstrip(".,)）")}
                pending_title = ""
                continue
            heading = re.sub(r"^#{1,6}\s*", "", line).strip()
            heading = re.sub(r"^\s*(?:[-*+]\s*|\d+[.)]\s*)", "", heading).strip()
            if current is None and (line.startswith("#") or re.match(r"^\d+[.)]\s+", line)):
                pending_title = heading
                continue
            if current is not None:
                cleaned = re.sub(r"^(?:摘要|snippet|description)\s*[:：]\s*", "", line, flags=re.IGNORECASE)
                if cleaned and not cleaned.startswith("---"):
                    snippet_lines.append(cleaned)
        flush()
        return entries

    @staticmethod
    def _search_payload_is_explicitly_empty(payload: Any) -> bool:
        if payload in (None, "", [], {}):
            return True
        if not isinstance(payload, Mapping):
            return False
        for key in ("results", "items", "data"):
            if key in payload:
                return payload.get(key) in (None, "", [], {})
        return False

    def _payload_to_text(self, payload: Any, *, redaction_terms: list[str]) -> str:
        if isinstance(payload, str):
            return self._sanitize_output(payload, redaction_terms=redaction_terms)
        try:
            return self._sanitize_output(
                json.dumps(payload, ensure_ascii=False, default=str), redaction_terms=redaction_terms
            )
        except Exception:
            return self._sanitize_output(str(payload), redaction_terms=redaction_terms)

    def _first_mapping(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            return dict(payload)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, Mapping):
                    return dict(item)
        return {}
