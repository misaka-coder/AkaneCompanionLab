"""Public music search requests without playback claims."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote_plus

from ...text_utils import normalize_text
from ..core import ToolExecutionContext, ToolExecutionResult


class MusicSearchMixin:
    def build_prompt_instruction(self) -> str:
        return (
            "- open_music_search：桌宠模式下，当用户明确要“点歌/放一首歌/搜一首歌给我听”时使用。"
            "它只会打开公开音乐平台搜索页，不会自动点击播放。"
            '格式为 {"type":"open_music_search","title":"歌名","artist":"歌手","platform":"qq_music"}。'
            "platform 可选 qq_music、netease_music、bilibili、youtube；用户没指定平台时默认 qq_music。"
            "按桌面执行回执说明结果；不要声称歌曲已经开始播放，除非后续页面状态明确显示已播放。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        title = self._normalize_query_part(
            value.get("title") or value.get("song") or value.get("name") or value.get("query") or value.get("keyword")
        )
        artist = self._normalize_query_part(value.get("artist") or value.get("singer") or value.get("author"))
        if not title:
            return None
        platform = self._normalize_platform(value.get("platform") or value.get("provider") or value.get("site"))
        query = " ".join(part for part in (title, artist) if part).strip()
        if not query:
            return None
        label = f"{title}{' - ' + artist if artist else ''}"
        return {
            "type": self.tool_type,
            "title": title,
            "artist": artist,
            "platform": platform,
            "query": query[:160],
            "label": label[:100],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        platform = self._normalize_platform(call.get("platform"))
        platform_label, template = self.PLATFORM_URLS[platform]
        query = self._normalize_query_part(call.get("query")) or self._normalize_query_part(call.get("title"))
        artist = self._normalize_query_part(call.get("artist"))
        if artist and artist not in query:
            query = f"{query} {artist}".strip()
        url = template.format(query=quote_plus(query))
        title = str(call.get("title") or query or "").strip()[:80]
        label = str(call.get("label") or title or platform_label).strip()[:100]
        from ...capability_registry import OPEN_BROWSER_TOOL_SPEC
        from ...tool_invocation import ToolInvocation
        from ...tool_orchestration.desktop import _execute_satellite_with_broker

        receipt = self._offer_source.resolve_receipt(OPEN_BROWSER_TOOL_SPEC) if self._offer_source is not None else None
        invocation = ToolInvocation(
            name=OPEN_BROWSER_TOOL_SPEC.capability_id,
            arguments={"url": url, "label": label[:80], "reason": "music_search_request"},
            id=f"{context.invocation_id}:music_search_browser" if context.invocation_id else "",
            execution_receipt=receipt.as_dict() if receipt is not None else None,
        )
        result, envelope = _execute_satellite_with_broker(
            self,
            spec=OPEN_BROWSER_TOOL_SPEC,
            invocation=invocation,
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            client_context=SimpleNamespace(effective_mode=SimpleNamespace(value=context.client_mode)),
            request_context=context.request_context,
        )
        execution = dict(result.state_updates.get("capability_execution") or next(
            (event for event in result.stream_events if event.get("type") == "capability_execution_result"),
            {},
        ))
        status = str(execution.get("status") or envelope.data.get("status") or "failed")
        reason = str(execution.get("reason") or envelope.data.get("code") or "")
        if status == "succeeded":
            reason = ""
            result.followup_context = (
                f"系统已接收打开 {platform_label}《{title}》搜索页的请求。"
                "尚未确认页面加载或歌曲播放；继续操作需检查页面状态。"
            )
        result.tool_type = self.tool_type
        for event in result.stream_events:
            if event.get("tool_type") == OPEN_BROWSER_TOOL_SPEC.capability_id:
                event["tool_type"] = self.tool_type
        result.state_updates.update({
            "capability_execution": {"tool_type": self.tool_type, "status": status, "reason": reason},
            "music_request_status": status,
            "music_request_platform": platform,
            "music_request_query": query[:160],
            "music_request_url": url,
        })
        return result

    def _normalize_platform(self, value: Any) -> str:
        raw = normalize_text(str(value or "")).strip().lower().replace("-", "_").replace(" ", "_")
        if not raw:
            return "qq_music"
        return self.PLATFORM_ALIASES.get(raw, raw if raw in self.PLATFORM_URLS else "qq_music")

    def _normalize_query_part(self, value: Any) -> str:
        text = normalize_text(str(value or "")).strip()
        if not text or len(text) > 160:
            return ""
        lowered = text.lower()
        if any(marker in lowered for marker in self.SECRET_MARKERS):
            return ""
        if re.search(r"(?i)\bhttps?://|file://|localhost|127\.0\.0\.1", text):
            return ""
        if any(ord(ch) < 32 for ch in text):
            return ""
        return re.sub(r"\s+", " ", text)[:120]
