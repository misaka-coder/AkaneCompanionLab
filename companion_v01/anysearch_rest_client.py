"""Bounded AnySearch REST client used when no local MCP runtime is installed."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any


DEFAULT_ANYSEARCH_REST_ENDPOINT = "https://api.anysearch.com/v1/search"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AnySearchRestError(RuntimeError):
    """Structured failure that is safe to project into tool state."""

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(str(reason or "anysearch_rest_failed"))
        self.reason = str(reason or "anysearch_rest_failed")[:120]
        self.retryable = bool(retryable)


class AnySearchRestClient:
    """Call AnySearch's public search endpoint without Node or an MCP proxy."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ANYSEARCH_REST_ENDPOINT,
        timeout_seconds: float = 20.0,
        api_key_provider: Callable[[], str] | None = None,
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        self.endpoint = str(endpoint or DEFAULT_ANYSEARCH_REST_ENDPOINT).strip()
        self.timeout_seconds = max(2.0, min(60.0, float(timeout_seconds)))
        self._api_key_provider = api_key_provider or (lambda: str(os.environ.get("ANYSEARCH_API_KEY") or ""))
        self._urlopen = urlopen or urllib.request.urlopen

    def call(self, *, action: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        normalized_action = str(action or "search").strip().lower()
        if normalized_action == "search":
            return {"results": self._search(arguments)}
        if normalized_action == "batch_search":
            raw_queries = arguments.get("queries")
            queries = raw_queries if isinstance(raw_queries, Sequence) and not isinstance(raw_queries, str) else ()
            merged: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            for raw in list(queries)[:4]:
                query_args = dict(raw) if isinstance(raw, Mapping) else {"query": str(raw or "")}
                for item in self._search(query_args):
                    url = str(item.get("url") or item.get("link") or "").strip()
                    dedupe_key = url or json.dumps(item, ensure_ascii=True, sort_keys=True, default=str)
                    if dedupe_key in seen_urls:
                        continue
                    seen_urls.add(dedupe_key)
                    item.setdefault("search_query", str(query_args.get("query") or "")[:240])
                    merged.append(item)
            return {"results": merged}
        raise AnySearchRestError("anysearch_rest_action_unsupported", retryable=False)

    def _search(self, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = " ".join(str(arguments.get("query") or "").strip().split())[:240]
        if not query:
            raise AnySearchRestError("anysearch_query_missing", retryable=False)
        try:
            max_results = max(1, min(10, int(arguments.get("max_results") or 5)))
        except (TypeError, ValueError, OverflowError):
            max_results = 5
        payload: dict[str, Any] = {"query": query, "max_results": max_results}
        domain = str(arguments.get("domain") or "").strip()[:253]
        if domain:
            payload["domain"] = domain
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        api_key = str(self._api_key_provider() or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AnySearchRestError("anysearch_rate_limited", retryable=True) from None
            if exc.code in {401, 403}:
                raise AnySearchRestError("anysearch_auth_rejected", retryable=False) from None
            raise AnySearchRestError("anysearch_http_error", retryable=500 <= int(exc.code) < 600) from None
        except (TimeoutError, urllib.error.URLError, OSError):
            raise AnySearchRestError("anysearch_network_unavailable", retryable=True) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AnySearchRestError("anysearch_response_too_large", retryable=False)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AnySearchRestError("anysearch_invalid_response", retryable=True) from None
        try:
            response_code = int(decoded.get("code", -1)) if isinstance(decoded, Mapping) else -1
        except (TypeError, ValueError, OverflowError):
            response_code = -1
        if not isinstance(decoded, Mapping) or response_code != 0:
            raise AnySearchRestError("anysearch_upstream_error", retryable=True)
        data = decoded.get("data")
        results = data.get("results") if isinstance(data, Mapping) else None
        if not isinstance(results, list):
            raise AnySearchRestError("anysearch_invalid_response", retryable=True)
        return [dict(item) for item in results[:max_results] if isinstance(item, Mapping)]


__all__ = ["AnySearchRestClient", "AnySearchRestError", "DEFAULT_ANYSEARCH_REST_ENDPOINT"]
