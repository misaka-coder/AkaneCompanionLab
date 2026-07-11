from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from .public_retry import call_with_transient_retry, normalize_public_retry_policy


EASTMONEY_FAST_NEWS_ENDPOINT = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
EASTMONEY_FAST_NEWS_PAGE = "https://kuaixun.eastmoney.com/7_24.html"


class PublicNewsSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class PublicNewsItem:
    adapter_id: str
    item_id: str
    title: str
    summary: str
    published_at: int
    fetched_at: int
    source: str
    url: str
    labels: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "item_id": self.item_id,
            "title": self.title,
            "summary": self.summary,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "url": self.url,
            "labels": list(self.labels),
        }


@dataclass(frozen=True)
class PublicNewsFetchResult:
    ok: bool
    status: str
    adapter_id: str
    source: str
    items: tuple[PublicNewsItem, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items or ()))


PublicNewsLoader = Callable[..., Any]


class EastmoneyFastNewsAdapter:
    """Bounded adapter for Eastmoney's public 7x24 fast-news page.

    The adapter is intentionally reusable outside proactive push. A future model-facing
    search tool may consume the same normalized items without changing this boundary.
    """

    adapter_id = "eastmoney_fast_news"
    source_name = "东方财富 7×24 全球财经快讯"

    def __init__(
        self,
        *,
        loader: PublicNewsLoader | None = None,
        timeout_seconds: float = 6.0,
        page_size: int = 100,
        retry_max_attempts: int = 2,
        retry_backoff_seconds: float = 0.3,
        circuit_failure_threshold: int = 3,
        circuit_open_seconds: int = 60,
        retry_sleeper=time.sleep,
        clock=time.time,
    ) -> None:
        self._loader = loader or _default_eastmoney_fast_news_loader
        self.timeout_seconds = max(1.0, min(20.0, float(timeout_seconds)))
        self.page_size = max(10, min(200, int(page_size)))
        self.retry_max_attempts, self.retry_backoff_seconds = normalize_public_retry_policy(
            max_attempts=retry_max_attempts,
            backoff_seconds=retry_backoff_seconds,
        )
        self.circuit_failure_threshold = max(1, min(20, int(circuit_failure_threshold)))
        self.circuit_open_seconds = max(10, min(30 * 60, int(circuit_open_seconds)))
        self._retry_sleeper = retry_sleeper
        self._clock = clock
        self._state_lock = threading.RLock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0

    def fetch_latest(self, *, limit: int = 100) -> PublicNewsFetchResult:
        now = max(1, int(self._clock()))
        with self._state_lock:
            if now < self._circuit_open_until:
                return PublicNewsFetchResult(
                    ok=False,
                    status="circuit_open",
                    adapter_id=self.adapter_id,
                    source=self.source_name,
                    reason=f"circuit_open_until:{self._circuit_open_until}",
                )
        bounded_limit = max(1, min(self.page_size, int(limit)))
        try:
            payload = call_with_transient_retry(
                lambda: self._loader(
                    timeout_seconds=self.timeout_seconds,
                    page_size=self.page_size,
                    now_ts=now,
                ),
                max_attempts=self.retry_max_attempts,
                backoff_seconds=self.retry_backoff_seconds,
                sleeper=self._retry_sleeper,
            )
            items = self._normalize_payload(payload, fetched_at=now)[:bounded_limit]
        except PublicNewsSchemaError as exc:
            self._record_failure(now)
            return PublicNewsFetchResult(
                ok=False,
                status="unavailable",
                adapter_id=self.adapter_id,
                source=self.source_name,
                reason=f"upstream_schema_changed:eastmoney_fast_news:{exc}",
            )
        except Exception as exc:
            self._record_failure(now)
            return PublicNewsFetchResult(
                ok=False,
                status="unavailable",
                adapter_id=self.adapter_id,
                source=self.source_name,
                reason=f"upstream_unavailable:eastmoney_fast_news:{type(exc).__name__}",
            )
        with self._state_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0
        return PublicNewsFetchResult(
            ok=True,
            status="ok" if items else "empty",
            adapter_id=self.adapter_id,
            source=self.source_name,
            items=items,
        )

    def _record_failure(self, now: int) -> None:
        with self._state_lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.circuit_failure_threshold:
                self._circuit_open_until = now + self.circuit_open_seconds

    def _normalize_payload(self, payload: Any, *, fetched_at: int) -> tuple[PublicNewsItem, ...]:
        if not isinstance(payload, Mapping):
            raise PublicNewsSchemaError("response_not_object")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise PublicNewsSchemaError("missing_data_object")
        rows = data.get("fastNewsList")
        if not isinstance(rows, list):
            raise PublicNewsSchemaError("missing_fast_news_list")
        items: list[PublicNewsItem] = []
        seen: set[str] = set()
        for row in rows[: self.page_size]:
            if not isinstance(row, Mapping):
                continue
            title = _clean_text(row.get("title"), max_length=1000)
            summary = _clean_text(row.get("summary"), max_length=4000)
            if not title and not summary:
                continue
            published_at = _parse_eastmoney_time(row.get("showTime"))
            raw_code = _clean_text(row.get("code"), max_length=300)
            item_material = raw_code or f"{published_at}|{title}|{summary}"
            item_id = hashlib.sha256(item_material.encode("utf-8")).hexdigest()[:40]
            if item_id in seen:
                continue
            seen.add(item_id)
            url = _eastmoney_article_url(raw_code)
            items.append(
                PublicNewsItem(
                    adapter_id=self.adapter_id,
                    item_id=item_id,
                    title=title or summary[:500],
                    summary=summary,
                    published_at=published_at,
                    fetched_at=fetched_at,
                    source=self.source_name,
                    url=url,
                    labels=("eastmoney_fast_news", "source_report_only", "needs_official_verification"),
                )
            )
        return tuple(sorted(items, key=lambda item: (item.published_at, item.item_id), reverse=True))


def _default_eastmoney_fast_news_loader(*, timeout_seconds: float, page_size: int, now_ts: int) -> Any:
    import requests

    response = requests.get(
        EASTMONEY_FAST_NEWS_ENDPOINT,
        params={
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": max(10, min(200, int(page_size))),
            "req_trace": str(max(1, int(now_ts)) * 1000),
        },
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": EASTMONEY_FAST_NEWS_PAGE,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AkaneFinanceNews/1.0",
        },
        timeout=max(1.0, min(20.0, float(timeout_seconds))),
    )
    response.raise_for_status()
    return response.json()


def _parse_eastmoney_time(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise PublicNewsSchemaError("missing_show_time")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        parsed = int(numeric)
        if parsed <= 0:
            raise PublicNewsSchemaError("invalid_show_time")
        return parsed
    text = str(value or "").strip()
    if not text:
        raise PublicNewsSchemaError("missing_show_time")
    normalized = text.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed_dt = datetime.fromisoformat(normalized)
    except ValueError:
        parsed_dt = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed_dt = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
        if parsed_dt is None:
            raise PublicNewsSchemaError("invalid_show_time")
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(parsed_dt.timestamp())


def _eastmoney_article_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return EASTMONEY_FAST_NEWS_PAGE
    if text.startswith("https://") and re.fullmatch(r"https://([a-z0-9-]+\.)*eastmoney\.com/[^\s]*", text, re.I):
        return text[:4000]
    safe_code = re.sub(r"[^A-Za-z0-9_-]+", "", text)[:200]
    return f"https://finance.eastmoney.com/a/{safe_code}.html" if safe_code else EASTMONEY_FAST_NEWS_PAGE


def _clean_text(value: Any, *, max_length: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


__all__ = [
    "EASTMONEY_FAST_NEWS_ENDPOINT",
    "EASTMONEY_FAST_NEWS_PAGE",
    "EastmoneyFastNewsAdapter",
    "PublicNewsFetchResult",
    "PublicNewsItem",
    "PublicNewsSchemaError",
]
