"""Read-only Hacker News report plugin using Akane's real capability chain."""

from __future__ import annotations

import html
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from capcore import CapabilityIOSlot, CapabilityResult
from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec

from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    NETWORK_READ_PERMISSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginRegistrar,
    PluginResultExperience,
    PluginResultPayload,
)


PLUGIN_ID = "akane.sample.hacker-news-report"
PLUGIN_VERSION = "0.1.0"
CAPABILITY_ID = f"{PLUGIN_ID}.fetch.v1"
API_ROOT = "https://hacker-news.firebaseio.com/v0"
REQUEST_TIMEOUT_SECONDS = 10.0
NETWORK_ATTEMPTS = 2
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ITEMS = 10
MAX_REPORT_BYTES = 256 * 1024
_FEEDS = {
    "top": "topstories",
    "new": "newstories",
    "best": "beststories",
    "ask": "askstories",
    "show": "showstories",
    "job": "jobstories",
}


class HackerNewsProviderError(RuntimeError):
    def __init__(self, reason: str, *, status: str = "error", content: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.content = dict(content or {})


def _read_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "Akane-Hacker-News-Report/0.1"})
    payload = b""
    for attempt in range(NETWORK_ATTEMPTS):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > MAX_RESPONSE_BYTES:
                    raise HackerNewsProviderError("provider_response_too_large")
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            break
        except HTTPError as exc:
            raise HackerNewsProviderError(
                "provider_http_error",
                status="unavailable",
                content={"http_status": int(exc.code)},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt + 1 >= NETWORK_ATTEMPTS:
                raise HackerNewsProviderError("provider_unavailable", status="unavailable") from exc
        except (TypeError, ValueError) as exc:
            raise HackerNewsProviderError("provider_invalid_response") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise HackerNewsProviderError("provider_response_too_large")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HackerNewsProviderError("provider_invalid_response") from exc


def _clean_text(value: object) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def _load_story(item_id: int) -> dict[str, Any] | None:
    payload = _read_json(f"{API_ROOT}/item/{item_id}.json")
    if not isinstance(payload, dict) or payload.get("deleted") or payload.get("dead"):
        return None
    title = _clean_text(payload.get("title"))
    if not title:
        return None
    story_id = payload.get("id")
    if isinstance(story_id, bool) or not isinstance(story_id, int):
        return None
    score = payload.get("score")
    comments = payload.get("descendants")
    timestamp = payload.get("time")
    return {
        "id": story_id,
        "title": title,
        "author": _clean_text(payload.get("by")),
        "score": score if isinstance(score, int) and not isinstance(score, bool) else 0,
        "comments": comments if isinstance(comments, int) and not isinstance(comments, bool) else 0,
        "published_at": (
            datetime.fromtimestamp(timestamp, UTC).isoformat()
            if isinstance(timestamp, int) and not isinstance(timestamp, bool) and timestamp >= 0
            else ""
        ),
        "url": _clean_text(payload.get("url")) or f"https://news.ycombinator.com/item?id={story_id}",
        "discussion_url": f"https://news.ycombinator.com/item?id={story_id}",
    }


def _load_story_or_none(item_id: int) -> dict[str, Any] | None:
    try:
        return _load_story(item_id)
    except HackerNewsProviderError:
        return None


def _render_report(*, feed: str, retrieved_at: str, stories: list[dict[str, Any]], omitted: int) -> bytes:
    lines = [
        f"# Hacker News {feed} report",
        "",
        f"Retrieved: {retrieved_at}",
        f"Source: {API_ROOT}/{_FEEDS[feed]}.json",
        f"Items: {len(stories)}",
    ]
    if omitted:
        lines.append(f"Unavailable items omitted: {omitted}")
    for index, story in enumerate(stories, start=1):
        lines.extend(
            [
                "",
                f"## {index}. {story['title']}",
                "",
                f"- Score: {story['score']}",
                f"- Comments: {story['comments']}",
                f"- Author: {story['author'] or 'unknown'}",
                f"- Published: {story['published_at'] or 'unknown'}",
                f"- Link: {story['url']}",
                f"- Discussion: {story['discussion_url']}",
            ]
        )
    data = ("\n".join(lines) + "\n").encode("utf-8")
    if len(data) > MAX_REPORT_BYTES:
        raise HackerNewsProviderError("report_too_large")
    return data


def fetch_hacker_news_report(feed: str = "top", max_items: int = 5) -> CapabilityResult:
    clean_feed = str(feed or "top").strip().lower()
    if clean_feed not in _FEEDS:
        return CapabilityResult(is_error=True, status="invalid_input", reason="unsupported_feed")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= MAX_ITEMS:
        return CapabilityResult(is_error=True, status="invalid_input", reason="max_items_out_of_range")

    try:
        ranking = _read_json(f"{API_ROOT}/{_FEEDS[clean_feed]}.json")
        if not isinstance(ranking, list):
            raise HackerNewsProviderError("provider_invalid_response")
        item_ids = [item for item in ranking if isinstance(item, int) and not isinstance(item, bool)][:max_items]
        if not item_ids:
            raise HackerNewsProviderError("provider_empty_feed", status="unavailable")
        with ThreadPoolExecutor(max_workers=min(4, len(item_ids))) as executor:
            results = tuple(executor.map(_load_story_or_none, item_ids))
        stories = [item for item in results if item is not None]
        omitted = len(item_ids) - len(stories)
        if not stories:
            raise HackerNewsProviderError("provider_items_unavailable", status="unavailable")
        retrieved_at = datetime.now(UTC).isoformat()
        report = _render_report(
            feed=clean_feed,
            retrieved_at=retrieved_at,
            stories=stories,
            omitted=omitted,
        )
    except HackerNewsProviderError as exc:
        return CapabilityResult(
            is_error=True,
            status=exc.status,
            reason=exc.reason,
            content=exc.content,
        )

    warnings = (
        (f"{omitted} 个条目在读取时不可用，报告仅包含成功取得的条目。",)
        if omitted
        else ()
    )
    facts = tuple(
        f"第 {index} 名：{story['title']}（{story['score']} 分，{story['comments']} 条评论）"
        for index, story in enumerate(stories, start=1)
    )
    return CapabilityResult(
        is_error=False,
        status="ok" if not omitted else "partial",
        content=ManagedArtifactPayload(
            content=PluginResultPayload(
                content={
                    "feed": clean_feed,
                    "retrieved_at": retrieved_at,
                    "source": f"{API_ROOT}/{_FEEDS[clean_feed]}.json",
                    "items": stories,
                    "omitted_count": omitted,
                },
                experience=PluginResultExperience(
                    summary=f"已读取 Hacker News {clean_feed} 榜的 {len(stories)} 个条目。",
                    facts=facts,
                    as_of=retrieved_at,
                    warnings=warnings,
                    interpretation_notes=("榜单顺序沿用 Hacker News 官方 API 返回顺序。",),
                    suggested_next_actions=("打开报告查看链接", "选择其中一个话题继续检索"),
                ),
            ),
            artifacts=(ManagedArtifactDraft(
                data=report,
                title=f"hacker-news-{clean_feed}-report",
                output_format="md",
                mime_type="text/markdown",
                summary=f"Hacker News {clean_feed} feed report with {len(stories)} items.",
                send_to_user=True,
            ),),
        ),
    )


class HackerNewsReportPlugin:
    manifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            NETWORK_READ_PERMISSION,
            MANAGED_ARTIFACT_WRITE_PERMISSION,
        ),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        spec = PythonCapabilitySpec.from_callable(
            fetch_hacker_news_report,
            capability_id=CAPABILITY_ID,
            display_name="Hacker News report",
            short_hint="Read one official Hacker News feed and create a Markdown report.",
            visible_in=("base", "web", "desktop", "qq"),
            prompt_exposed=True,
            risk="low",
            confirm="never",
            effects=("network", "filesystem"),
            inputs=(
                CapabilityIOSlot(
                    name="feed",
                    kind="string",
                    required=False,
                    raw={
                        "description": "Feed to read: top, new, best, ask, show, or job.",
                        "enum": tuple(_FEEDS),
                        "default": "top",
                    },
                ),
                CapabilityIOSlot(
                    name="max_items",
                    kind="integer",
                    required=False,
                    raw={
                        "description": "Number of ranked items to include.",
                        "minimum": 1,
                        "maximum": MAX_ITEMS,
                        "default": 5,
                    },
                ),
            ),
            outputs=(
                CapabilityIOSlot(
                    name="report",
                    kind="file",
                    required=True,
                    max_bytes=MAX_REPORT_BYTES,
                    delivery="generated_file",
                ),
            ),
            raw={"contract": "akane.sample.hacker-news-report.v1"},
        )
        registrar.add_capability_adapter(
            PythonCapabilityAdapter(
                provider_id="provider.akane.sample.hacker-news-report",
                capabilities=(spec,),
            )
        )


def create_plugin() -> HackerNewsReportPlugin:
    return HackerNewsReportPlugin()


__all__ = [
    "API_ROOT",
    "CAPABILITY_ID",
    "MAX_ITEMS",
    "MAX_REPORT_BYTES",
    "MAX_RESPONSE_BYTES",
    "NETWORK_ATTEMPTS",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "REQUEST_TIMEOUT_SECONDS",
    "HackerNewsReportPlugin",
    "create_plugin",
    "fetch_hacker_news_report",
]
