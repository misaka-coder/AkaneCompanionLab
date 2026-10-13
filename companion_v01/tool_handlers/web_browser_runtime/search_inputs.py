"""Search, extract and domain argument contracts."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from ...text_utils import normalize_text
from .common import normalize_http_url


class SearchInputsMixin:
    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        cursor = str(value.get("cursor") or "").strip()
        if cursor:
            return {"type": self.tool_type, "cursor": cursor}
        action = self._normalize_action(value)
        if action == "extract":
            url = self._normalize_public_url(value.get("url") or value.get("link"))
            if not url:
                return None
            return {
                "type": self.tool_type,
                "action": "extract",
                "url": url,
            }
        if action == "batch_search":
            queries = self._normalize_queries(value.get("queries") or value.get("query"))
            if not queries:
                return None
            return {
                "type": self.tool_type,
                "action": "batch_search",
                "queries": queries,
                "max_results": self._coerce_int(value.get("max_results"), minimum=1, maximum=5, default=3),
            }
        if action == "get_sub_domains":
            domains = self._normalize_domains(value.get("domains") or value.get("domain"))
            if not domains:
                return None
            return {
                "type": self.tool_type,
                "action": "get_sub_domains",
                "domains": domains,
            }
        query = normalize_text(str(value.get("query") or value.get("keyword") or value.get("prompt") or "")).strip()
        if not query:
            return None
        normalized = {
            "type": self.tool_type,
            "action": "search",
            "query": query[: self.MAX_QUERY_LENGTH],
            "max_results": self._coerce_int(value.get("max_results"), minimum=1, maximum=self.MAX_RESULTS, default=5),
        }
        domain = self._normalize_domain(value.get("domain"))
        if domain:
            normalized["domain"] = domain
        sub_domain = self._normalize_domain(value.get("sub_domain") or value.get("subDomain"))
        if sub_domain:
            normalized["sub_domain"] = sub_domain
        return normalized

    def _build_mcp_arguments(self, call: Mapping[str, Any]) -> dict[str, Any]:
        action = str(call.get("action") or "search")
        if action == "extract":
            return {"url": str(call.get("url") or "")}
        if action == "batch_search":
            max_results = int(call.get("max_results") or 3)
            return {
                "queries": [
                    {"query": query, "max_results": max_results}
                    for query in list(call.get("queries") or [])[: self.MAX_QUERIES]
                ]
            }
        if action == "get_sub_domains":
            domains = list(call.get("domains") or [])
            return {"domain": domains[0]} if len(domains) == 1 else {"domains": domains[: self.MAX_QUERIES]}
        args: dict[str, Any] = {
            "query": str(call.get("query") or ""),
            "max_results": int(call.get("max_results") or 5),
        }
        for key in ("domain", "sub_domain"):
            if call.get(key):
                args[key] = str(call.get(key) or "")
        return args

    def _normalize_action(self, value: Mapping[str, Any]) -> str:
        raw = str(value.get("action") or "").strip().lower().replace("-", "_")
        if not raw:
            if value.get("url") or value.get("link"):
                return "extract"
            if isinstance(value.get("queries"), list):
                return "batch_search"
            return "search"
        aliases = {
            "lookup": "search",
            "web": "search",
            "read": "extract",
            "read_url": "extract",
            "page": "extract",
            "subdomains": "get_sub_domains",
            "domains": "get_sub_domains",
        }
        action = aliases.get(raw, raw)
        return action if action in self.ALLOWED_ACTIONS else "search"

    def _normalize_queries(self, value: Any) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = [part for part in re.split(r"[\n;；]+", value) if part]
        else:
            raw_items = []
        queries: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, Mapping):
                text = str(item.get("query") or item.get("keyword") or "").strip()
            else:
                text = str(item or "").strip()
            text = normalize_text(text)
            dedupe_key = text.lower()
            if not text or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            queries.append(text[: self.MAX_QUERY_LENGTH])
            if len(queries) >= self.MAX_QUERIES:
                break
        return queries

    def _normalize_domains(self, value: Any) -> list[str]:
        raw_items = value if isinstance(value, list) else [value]
        domains: list[str] = []
        for item in raw_items:
            domain = self._normalize_domain(item)
            if domain and domain not in domains:
                domains.append(domain)
            if len(domains) >= self.MAX_QUERIES:
                break
        return domains

    def _normalize_domain(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if "://" in text:
            url = self._normalize_public_url(text)
            if not url:
                return ""
            parsed = urlparse(url)
            text = parsed.hostname or ""
        text = text.strip(".")
        if not re.fullmatch(r"[a-z0-9.-]{1,253}", text):
            return ""
        if text in {"localhost"} or text.endswith(".local"):
            return ""
        if self._is_private_or_local_host(text):
            return ""
        return text[:253]

    def _normalize_public_url(self, value: Any) -> str:
        return normalize_http_url(
            value, is_private_host=self._is_private_or_local_host,
            reject_secret_query=True,
        )
