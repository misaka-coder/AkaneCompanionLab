"""Web / browser / search domain tool handlers."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import ipaddress
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote_plus, urlparse

import config

from ..anysearch_rest_client import AnySearchRestClient, AnySearchRestError
from ..browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from ..capability_registry import OPEN_BROWSER_TOOL_SPEC, WEB_SEARCH_TOOL_SPEC
from ..capcore_runtime import (
    approval_required_event as capcore_approval_required_event,
    manual_permission_request as capcore_manual_permission_request,
    resolve_permission_for_profile as capcore_resolve_permission_for_profile,
)
from ..local_capability_config import get_mcp_server_runtime_config
from ..mcp_stdio_discoverer import McpStdioDiscoveryError, McpToolCaller
from ..text_utils import normalize_text
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolMetadata,
)

class OpenBrowserToolHandler(BaseToolHandler):
    tool_type = "open_browser"
    policy_accepted_native_tool = True

    def tool_spec(self):
        return OPEN_BROWSER_TOOL_SPEC

    def tool_metadata(self) -> ToolMetadata:
        spec = self.tool_spec()
        return ToolMetadata(
            family="browser_control",
            operation="control",
            risk=spec.risk,
            default_round_budget=6,
            input_schema=spec.input_schema,
            requires_confirmation=spec.confirm != "never",
        )

    def build_prompt_instruction(self) -> str:
        spec = self.tool_spec()
        return (
            f"- {spec.capability_id}：{spec.description}"
            f"调用参数遵循：{json.dumps(spec.input_schema, ensure_ascii=False, sort_keys=True)}"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        url = self._normalize_public_url(value.get("url") or value.get("link") or value.get("href"))
        if not url:
            return None
        label = normalize_text(str(value.get("label") or value.get("title") or "")).strip()
        reason = normalize_text(str(value.get("reason") or "")).strip()
        return {
            "type": self.tool_type,
            "url": url,
            "label": label[:80],
            "reason": reason[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        del call, context
        raise RuntimeError("open_browser_requires_executor_broker")

    def _normalize_public_url(self, value: Any) -> str:
        url = str(value or "").strip()
        if len(url) > 1600:
            url = url[:1600]
        if any(ord(ch) < 32 for ch in url) or re.search(r"\s", url):
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        if parsed.username or parsed.password:
            return ""
        hostname = parsed.hostname or ""
        if not hostname or self._is_private_or_local_host(hostname):
            return ""
        return url

    def _is_private_or_local_host(self, hostname: str) -> bool:
        host = str(hostname or "").strip().lower().strip("[]")
        if not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)


class OpenMusicSearchToolHandler(BaseToolHandler):
    tool_type = "open_music_search"

    PLATFORM_URLS: dict[str, tuple[str, str]] = {
        "qq_music": ("QQ 音乐", "https://y.qq.com/n/ryqq/search?w={query}"),
        "netease_music": ("网易云音乐", "https://music.163.com/#/search/m/?s={query}&type=1"),
        "bilibili": ("哔哩哔哩", "https://search.bilibili.com/all?keyword={query}"),
        "youtube": ("YouTube", "https://www.youtube.com/results?search_query={query}"),
    }

    PLATFORM_ALIASES: dict[str, str] = {
        "qq": "qq_music",
        "qqmusic": "qq_music",
        "qq_music": "qq_music",
        "yqq": "qq_music",
        "qq音乐": "qq_music",
        "qq 音乐": "qq_music",
        "netease": "netease_music",
        "netease_music": "netease_music",
        "163": "netease_music",
        "网易": "netease_music",
        "网易云": "netease_music",
        "网易云音乐": "netease_music",
        "b站": "bilibili",
        "bili": "bilibili",
        "bilibili": "bilibili",
        "哔哩哔哩": "bilibili",
        "youtube": "youtube",
        "yt": "youtube",
        "油管": "youtube",
    }

    SECRET_MARKERS = ("api_key", "apikey", "authorization", "bearer", "cookie", "password", "secret", "token")

    def build_prompt_instruction(self) -> str:
        return (
            "- open_music_search：桌宠模式下，当用户明确要“点歌/放一首歌/搜一首歌给我听”时使用。"
            "它只会把歌名歌手变成公开音乐平台搜索页并请求桌宠打开浏览器，不会自动点击播放、登录、下载或控制播放器。"
            '格式为 {"type":"open_music_search","title":"歌名","artist":"歌手","platform":"qq_music"}。'
            "platform 可选 qq_music、netease_music、bilibili、youtube；用户没指定平台时默认 qq_music。"
            "如果用户要你继续在页面里点击或输入，应在打开后按 browser_page 的授权边界继续操作；"
            "不要声称歌曲已经开始播放，除非后续页面状态明确显示已播放。"
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
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "browser_open_requested",
                    "url": url,
                    "label": label,
                    "reason": "music_search_request",
                    "client_mode": context.client_mode,
                    "requires_confirmation": False,
                }
            ],
            followup_context=(
                f"你刚刚为用户在{platform_label}打开了公开音乐搜索页：{url}。"
                "这只是搜索/打开入口，不代表歌曲已经开始播放。"
                "如果用户还要求你继续点进结果或尝试播放，需要按 browser_page 的授权边界继续操作；"
                "不能登录、下载、绕过会员/版权限制，也不要声称已经播放成功。"
            ),
            state_updates={
                "music_request_status": "opened_search",
                "music_request_platform": platform,
                "music_request_query": query[:160],
                "music_request_url": url,
            },
        )

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


class BrowserPageToolHandler(BaseToolHandler):
    tool_type = "browser_page"

    ALLOWED_ACTIONS = {"navigate", "read_text", "current", "snapshot", "scroll", "elements", "click", "fill", "press"}
    CONTROL_ACTIONS = {"click", "fill", "press"}
    CONTROL_ACTION_ID_PREFIX = "browser_page"
    ALLOWED_PRESS_KEYS = {
        "Enter",
        "Escape",
        "Tab",
        "ArrowDown",
        "ArrowUp",
        "ArrowLeft",
        "ArrowRight",
        "PageDown",
        "PageUp",
        "Home",
        "End",
    }
    SECRET_MARKERS = ("api_key", "apikey", "authorization", "bearer", "cookie", "password", "secret", "token")
    MAX_TEXT_CHARS = 5000
    MAX_ELEMENT_LIMIT = 40
    MAX_SELECTOR_CHARS = 220
    MAX_FILL_TEXT_CHARS = 500

    def __init__(
        self,
        *,
        browser_runner: Any = None,
        config_base_dir: Path | str | None = None,
        approval_checker: Callable[..., bool] | None = None,
    ) -> None:
        self.browser_runner = browser_runner or ManagedBrowserPageRunner()
        self.config_base_dir = config_base_dir if config_base_dir is not None else getattr(config, "DATA_DIR", None)
        self.approval_checker = approval_checker

    def build_prompt_instruction(self) -> str:
        return (
            "- browser_page：仅在桌宠模式下，当用户明确要你打开并读取、滚动或操作一个公开网页，"
            "或继续处理 Akane 托管浏览器窗口的当前页面时使用。"
            "它会操作 Akane 自己启动的可见托管浏览器窗口，不会接管用户手动打开的 Edge/Chrome 标签页。"
            '打开并读取托管窗口格式为 {"type":"browser_page","action":"navigate","url":"https://...","max_chars":3000}；'
            '一般不需要 open_for_user；只有用户还要求额外用系统浏览器打开同一链接给人看时，才加 "open_for_user":true；'
            '读取当前页格式为 {"type":"browser_page","action":"read_text","max_chars":3000}；'
            '观察当前页面状态格式为 {"type":"browser_page","action":"snapshot","max_chars":3000}，'
            "返回 accessibility snapshot 和元素 ref；"
            '滚动当前页格式为 {"type":"browser_page","action":"scroll","scroll_delta":800,"max_chars":3000}；'
            '查看当前页可见链接/按钮/输入框摘要格式为 {"type":"browser_page","action":"elements","element_limit":20}；'
            "如果用户已经明确给出多步浏览目标，例如“打开某站、滚动、点第一个视频/链接、告诉我当前页”，"
            "不要每完成一步就询问用户；在工具轮次预算和授权边界内继续调用下一步 browser_page，"
            "直到任务完成、候选不存在、页面不可用、需要登录/支付/上传/下载等真实阻塞，或控制动作缺少批准。"
            "高风险控制动作只有在用户已批准或能力策略为完全访问时才会执行："
            "snapshot 返回的 Visible link/video candidates 可直接按序号点击，"
            '例如 {"type":"browser_page","action":"click","candidate_index":1}；'
            '优先先 snapshot，再用 ref 点击/输入，例如 {"type":"browser_page","action":"click","ref":"e3"}；'
            'CSS selector 仅作兼容，点击格式为 {"type":"browser_page","action":"click","selector":"button:has-text(\'搜索\')"}；'
            '输入格式为 {"type":"browser_page","action":"fill","ref":"e4","text":"搜索词"}；'
            '按键格式为 {"type":"browser_page","action":"press","ref":"e4","key":"Enter"}。'
            '查看当前页状态格式为 {"type":"browser_page","action":"current"}。'
            "如果用户只要求“打开给我看/在普通浏览器打开”且不需要你读取或操作，使用 open_browser；"
            "只有用户要你自己读取、总结、核对页面正文时才使用 browser_page。"
            "navigate/read_text/current/snapshot/scroll 会返回当前页面状态，不等于整站完整阅读；"
            "scroll 只滚动并返回滚动后的页面状态，elements 只列出候选元素；不要声称已经点击或输入。"
            "click/fill/press 不可用于登录、支付、下单、授权、删除、发布、下载、上传、文件选择或私密表单；"
            "不要用它执行脚本、读取 localhost/内网/file 路径或用户私密链接。"
            "如果只是搜索资料，优先用 web_search；web_search 只返回结果，不会打开或滚动浏览器，"
            "需要打开某条搜索结果时再用 browser_page.navigate 或 open_browser。"
        )

    def capability_status(self) -> dict[str, Any]:
        status_fn = getattr(self.browser_runner, "capability_status", None)
        if callable(status_fn):
            try:
                status = status_fn()
            except Exception:
                return {"enabled": False, "status": "unavailable", "reason": "browser_runner_status_failed"}
            if isinstance(status, Mapping):
                return {
                    "enabled": bool(status.get("enabled")),
                    "status": str(status.get("status") or "unavailable").strip() or "unavailable",
                    "reason": str(status.get("reason") or "").strip()[:160],
                }
        return {"enabled": True, "status": "ready", "reason": ""}

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = self._normalize_action(value)
        url = ""
        if action in {"navigate", "read_text"} and (value.get("url") or value.get("link") or value.get("href")):
            url = self._normalize_public_url(value.get("url") or value.get("link") or value.get("href"))
            if not url:
                return None
        if action == "navigate" and not url:
            return None
        raw_target = value.get("target")
        selector = self._normalize_selector(value.get("selector") or raw_target)
        ref = self._normalize_ref(value.get("ref") or value.get("element_ref") or value.get("target_ref") or raw_target)
        candidate_index = self._normalize_candidate_index(
            value.get("candidate_index") or value.get("candidateIndex") or value.get("candidate") or value.get("index")
        )
        if action != "click":
            candidate_index = 0
        if action in {"click", "fill"} and not selector and not ref and candidate_index <= 0:
            return None
        text = self._normalize_fill_text(value.get("text") or value.get("value") or value.get("query"))
        if action == "fill" and not text:
            return None
        key = self._normalize_press_key(value.get("key") or value.get("press"))
        if action == "press" and not key:
            return None
        return {
            "type": self.tool_type,
            "action": action,
            "url": url,
            "max_chars": self._coerce_int(
                value.get("max_chars"), minimum=500, maximum=self.MAX_TEXT_CHARS, default=3000
            ),
            "open_for_user": self._coerce_bool(value.get("open_for_user") or value.get("openForUser")),
            "scroll_delta": self._coerce_int(
                value.get("scroll_delta") or value.get("delta"), minimum=-2400, maximum=2400, default=800
            ),
            "element_limit": self._coerce_int(
                value.get("element_limit") or value.get("limit"), minimum=1, maximum=self.MAX_ELEMENT_LIMIT, default=20
            ),
            "selector": selector,
            "ref": ref,
            "text": text,
            "key": key,
            "candidate_index": candidate_index,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "current").strip() or "current"
        url = str(call.get("url") or "").strip()
        open_for_user = bool(call.get("open_for_user"))
        max_chars = self._coerce_int(call.get("max_chars"), minimum=500, maximum=self.MAX_TEXT_CHARS, default=3000)
        scroll_delta = self._coerce_int(call.get("scroll_delta"), minimum=-2400, maximum=2400, default=800)
        element_limit = self._coerce_int(
            call.get("element_limit"), minimum=1, maximum=self.MAX_ELEMENT_LIMIT, default=20
        )
        selector = str(call.get("selector") or "").strip()
        ref = str(call.get("ref") or "").strip()
        text = str(call.get("text") or "").strip()
        key = str(call.get("key") or "").strip()
        candidate_index = self._coerce_int(call.get("candidate_index"), minimum=0, maximum=30, default=0)
        if action in self.CONTROL_ACTIONS:
            authorization = self._authorize_control_action(action=action, call=call, context=context)
            if not authorization.get("ok"):
                return self._approval_required(action=action, call=call, context=context, authorization=authorization)
        run_kwargs: dict[str, Any] = {"action": action, "url": url, "max_chars": max_chars}
        if action == "scroll":
            run_kwargs["scroll_delta"] = scroll_delta
        if action == "elements":
            run_kwargs["element_limit"] = element_limit
        if action in self.CONTROL_ACTIONS:
            run_kwargs["selector"] = selector
            run_kwargs["ref"] = ref
            run_kwargs["text"] = text
            run_kwargs["key"] = key
            if action == "click":
                run_kwargs["candidate_index"] = candidate_index
        try:
            result = self.browser_runner.run(**run_kwargs)
        except Exception:
            result = BrowserPageResult(
                ok=False,
                status="unavailable",
                action=action,
                reason="browser_runner_failed",
            )
        normalized = self._normalize_result(result, fallback_action=action)
        open_event_url = url or str(normalized.url or "").strip()
        open_event = (
            self._build_open_event(open_event_url, normalized.title, client_mode=context.client_mode)
            if open_for_user
            else None
        )
        if not normalized.ok:
            return self._failure(normalized, open_event=open_event)

        safe_url = self._sanitize_output(normalized.url)[:800]
        safe_title = self._clip(self._sanitize_output(normalized.title), 180)
        safe_text = self._clip(self._sanitize_output(normalized.text), max_chars)
        lines = ["【Akane 托管浏览器窗口】", f"动作：{normalized.action}"]
        if safe_url:
            lines.append(f"URL: {safe_url}")
        if safe_title:
            lines.append(f"标题：{safe_title}")
        if safe_text:
            lines.append("元素摘要：" if normalized.action == "elements" else "页面状态快照：")
            lines.append(safe_text)
            if normalized.action == "elements":
                lines.append("这些只是可见候选元素摘要，不表示已经点击或输入。需要实际操作时必须等待后续确认能力。")
            elif normalized.action in self.CONTROL_ACTIONS:
                lines.append("高风险浏览器控制动作已在授权边界内执行；请基于当前页面状态继续，不要追加未授权动作。")
            else:
                lines.append("请只基于这份公开页面状态回答；没读到或不确定的内容要明确说明。")
        else:
            lines.append("当前页没有拿到可用正文。不要声称已经读取到未出现在这里的内容。")
        if open_event:
            lines.append("同时已请求桌宠把该公开网页交给系统浏览器打开给用户看。")
        else:
            lines.append("页面已在 Akane 托管浏览器窗口中处理；这不是用户手动打开的系统浏览器标签页。")
        next_hint = self._build_browser_next_hint(normalized.action, safe_text)
        if next_hint:
            lines.append(next_hint)
        events = []
        if open_event:
            events.append(open_event)
        events.append(
            {
                "type": "browser_page_read",
                "provider": "managed_browser",
                "action": normalized.action,
                "status": normalized.status,
                "url": safe_url,
                "title": safe_title,
                "client_mode": context.client_mode,
                "scroll_delta": scroll_delta if normalized.action == "scroll" else 0,
                "element_count": self._count_element_summary_lines(safe_text) if normalized.action == "elements" else 0,
                "requires_confirmation": False,
            }
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=self._clip("\n".join(lines), self.MAX_TEXT_CHARS + 700),
            state_updates={
                "browser_page_status": normalized.status,
                "browser_page_url": safe_url,
                "browser_page_title": safe_title,
                "browser_open_requested": bool(open_event),
                "browser_page_element_count": self._count_element_summary_lines(safe_text)
                if normalized.action == "elements"
                else 0,
                "browser_page_next_hint": next_hint,
                "browser_control_status": normalized.status if normalized.action in self.CONTROL_ACTIONS else "",
            },
        )

    def _failure(self, result: BrowserPageResult, *, open_event: dict[str, Any] | None = None) -> ToolExecutionResult:
        status = str(result.status or "unavailable").strip()[:120] or "unavailable"
        reason = self._clip(self._sanitize_output(result.reason), 180)
        events = []
        if open_event:
            events.append(open_event)
        events.append(
            {
                "type": "browser_page_read",
                "provider": "managed_browser",
                "action": str(result.action or "current").strip() or "current",
                "status": "unavailable",
                "reason": reason or status,
            }
        )
        open_note = "已另外请求桌宠打开该公开网页给用户看；" if open_event else ""
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=(
                f"{open_note}Akane 托管浏览器页面暂时不可用：{status}"
                f"{' / ' + reason if reason else ''}。请自然告诉用户这次没有读取到网页内容，不要编造页面结果。"
            ),
            state_updates={
                "browser_page_status": "unavailable",
                "browser_page_reason": reason or status,
                "browser_open_requested": bool(open_event),
            },
        )

    def _normalize_result(self, value: Any, *, fallback_action: str) -> BrowserPageResult:
        if isinstance(value, BrowserPageResult):
            return value
        if isinstance(value, Mapping):
            return BrowserPageResult(
                ok=bool(value.get("ok")),
                status=str(value.get("status") or ("available" if value.get("ok") else "unavailable")),
                action=str(value.get("action") or fallback_action),
                url=str(value.get("url") or ""),
                title=str(value.get("title") or ""),
                text=str(value.get("text") or ""),
                reason=str(value.get("reason") or ""),
            )
        return BrowserPageResult(ok=False, status="unavailable", action=fallback_action, reason="invalid_runner_result")

    def _build_browser_next_hint(self, action: str, text: str) -> str:
        clean_action = str(action or "").strip()
        clean_text = str(text or "")
        has_visible_candidates = "Visible link/video candidates:" in clean_text
        has_refs = "[ref=" in clean_text
        if clean_action in {"navigate", "snapshot", "current", "read_text", "scroll"}:
            if has_visible_candidates:
                return (
                    "下一步提示：如果用户目标需要进入某个可见链接/视频，可继续调用 "
                    "browser_page click 并使用 candidate_index；如果只是要总结当前可见内容，就停止工具并回答。"
                )
            if has_refs:
                return (
                    "下一步提示：如果用户目标需要操作当前可见控件，可继续调用 browser_page click/fill/press 并使用 ref；"
                    "如果只是阅读当前页，就基于已有内容回答。"
                )
            return (
                "下一步提示：如果用户明确还要继续查看后续内容，可继续调用 browser_page scroll；"
                "如果当前内容已经足够，就停止工具并回答。"
            )
        if clean_action == "elements":
            return (
                "下一步提示：如果元素摘要里有目标，可继续用 ref 或 candidate_index 操作；"
                "如果没有目标，先 snapshot 或 scroll 获取更多上下文。"
            )
        if clean_action in self.CONTROL_ACTIONS:
            return (
                "下一步提示：控制动作后应先 snapshot 或 read_text 观察页面变化；"
                "不要假设点击、输入或按键已经产生了未返回的新内容。"
            )
        return ""

    def _normalize_action(self, value: Mapping[str, Any]) -> str:
        raw = str(value.get("action") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not raw:
            return "navigate" if (value.get("url") or value.get("link") or value.get("href")) else "current"
        aliases = {
            "open": "navigate",
            "go": "navigate",
            "goto": "navigate",
            "visit": "navigate",
            "read": "read_text",
            "read_page": "read_text",
            "extract": "read_text",
            "extract_text": "read_text",
            "list_elements": "elements",
            "inspect_elements": "elements",
            "interactive_elements": "elements",
            "visible_elements": "elements",
            "tap": "click",
            "type": "fill",
            "input": "fill",
            "press_key": "press",
            "status": "current",
            "current_page": "current",
            "info": "current",
            "state": "snapshot",
            "observe": "snapshot",
            "snapshot_page": "snapshot",
            "page_snapshot": "snapshot",
            "accessibility_snapshot": "snapshot",
        }
        action = aliases.get(raw, raw)
        return action if action in self.ALLOWED_ACTIONS else "current"

    def _normalize_public_url(self, value: Any) -> str:
        url = str(value or "").strip()
        if len(url) > 1600:
            url = url[:1600]
        if any(ord(ch) < 32 for ch in url) or re.search(r"\s", url):
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        if parsed.username or parsed.password:
            return ""
        if re.search(r"(?i)(api[_-]?key|password|secret|token)=", parsed.query or ""):
            return ""
        hostname = parsed.hostname or ""
        if not hostname or self._is_private_or_local_host(hostname):
            return ""
        return url

    def _is_private_or_local_host(self, hostname: str) -> bool:
        host = str(hostname or "").strip().lower().strip("[]")
        if not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)

    def _coerce_int(self, value: Any, *, minimum: int, maximum: int, default: int) -> int:
        try:
            number = int(value)
        except Exception:
            number = default
        return max(minimum, min(maximum, number))

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on", "打开", "是", "需要"}

    def _normalize_selector(self, value: Any) -> str:
        selector = str(value or "").strip()
        if not selector or len(selector) > self.MAX_SELECTOR_CHARS:
            return ""
        if any(ord(ch) < 32 for ch in selector):
            return ""
        lowered = selector.lower()
        if any(marker in lowered for marker in self.SECRET_MARKERS):
            return ""
        if re.search(
            r"(?i)(login|signin|sign-in|checkout|payment|delete|remove|publish|post|upload|download|logout)", selector
        ):
            return ""
        return selector

    def _normalize_ref(self, value: Any) -> str:
        ref = str(value or "").strip()
        if not ref:
            return ""
        if ref.startswith("[ref=") and ref.endswith("]"):
            ref = ref[5:-1].strip()
        if ref.startswith("ref="):
            ref = ref[4:].strip()
        return ref if re.fullmatch(r"e\d{1,6}", ref) else ""

    def _normalize_candidate_index(self, value: Any) -> int:
        if value in (None, ""):
            return 0
        try:
            number = int(value)
        except Exception:
            return 0
        return max(0, min(30, number))

    def _normalize_fill_text(self, value: Any) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text or len(text) > self.MAX_FILL_TEXT_CHARS:
            return ""
        lowered = text.lower()
        if any(
            marker in lowered for marker in ("authorization:", "bearer ", "password=", "api_key=", "token=", "secret=")
        ):
            return ""
        return text

    def _normalize_press_key(self, value: Any) -> str:
        raw = str(value or "Enter").strip()
        aliases = {
            "return": "Enter",
            "esc": "Escape",
            "escape": "Escape",
            "enter": "Enter",
            "tab": "Tab",
            "down": "ArrowDown",
            "up": "ArrowUp",
            "left": "ArrowLeft",
            "right": "ArrowRight",
            "pagedown": "PageDown",
            "pageup": "PageUp",
            "home": "Home",
            "end": "End",
        }
        key = aliases.get(raw.lower().replace(" ", ""), raw)
        return key if key in self.ALLOWED_PRESS_KEYS else ""

    def _authorize_control_action(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        action_id = f"{self.CONTROL_ACTION_ID_PREFIX}.{action}"
        request = self._browser_control_permission_request(action=action, call=call, context=context)
        if self._approval_checker_allows(action_id=action_id, call=call, context=context):
            return {"ok": True, "mode": "approval_grant"}
        decision = capcore_resolve_permission_for_profile(
            request,
            base_dir=self.config_base_dir,
            profile_user_id=context.profile_user_id,
        )
        if decision.allowed:
            return {"ok": True, "mode": decision.mode, "reason": decision.reason}
        return {
            "ok": False,
            "status": "approval_required",
            "approvalMode": decision.mode,
            "capabilityId": "tool.browser_page",
            "actionId": action_id,
            "risk": "high",
            "reason": str(decision.reason or "browser_control_requires_approval"),
        }

    def _browser_control_permission_request(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        return capcore_manual_permission_request(
            context=context,
            required=True,
            capability_id="tool.browser_page",
            display_name="Browser Page",
            risk="high",
            confirm="always",
            effects=("browser_action",),
            reason="browser_control_requires_approval",
            args_preview={
                "action": str(action or ""),
                **self._safe_control_preview(call),
            },
        )

    def _approval_checker_allows(
        self, *, action_id: str, call: Mapping[str, Any], context: ToolExecutionContext
    ) -> bool:
        if not callable(self.approval_checker):
            return False
        try:
            return bool(
                self.approval_checker(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    capability_id="tool.browser_page",
                    action_id=action_id,
                    call=dict(call),
                    request_context=dict(context.request_context or {}),
                )
            )
        except Exception:
            return False

    def _safe_control_preview(self, call: Mapping[str, Any]) -> dict[str, Any]:
        preview: dict[str, Any] = {}
        selector = self._clip(self._sanitize_output(str(call.get("selector") or "")), 120)
        ref = self._clip(self._sanitize_output(str(call.get("ref") or "")), 40)
        candidate_index = self._coerce_int(call.get("candidate_index"), minimum=0, maximum=30, default=0)
        if selector:
            preview["selector"] = selector
        if ref:
            preview["ref"] = ref
        if candidate_index > 0:
            preview["candidateIndex"] = candidate_index
        if call.get("key"):
            preview["key"] = str(call.get("key") or "")
        if call.get("text"):
            preview["textLength"] = len(str(call.get("text") or ""))
        return preview

    def _approval_required(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        context: ToolExecutionContext,
        authorization: Mapping[str, Any],
    ) -> ToolExecutionResult:
        preview = {
            "action": action,
            **self._safe_control_preview(call),
        }
        event = capcore_approval_required_event(
            capability_id="tool.browser_page",
            action_id=str(authorization.get("actionId") or f"browser_page.{action}"),
            title="浏览器控制需要确认",
            summary="Akane 想对托管网页执行点击、输入或按键动作。",
            risk="high",
            approval_mode="ask_each_time",
            approval_reason=str(authorization.get("reason") or "browser_control_requires_approval"),
            payload_preview=preview,
            client_mode=context.client_mode,
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=(
                "浏览器控制动作需要用户确认：当前没有有效授权或“完全访问”策略。"
                "请自然告诉用户需要在能力审批中允许后再执行；不要声称已经点击、输入或按键。"
            ),
            state_updates={
                "browser_control_status": "approval_required",
                "browser_control_action": action,
            },
        )

    def _build_open_event(self, url: str, title: str = "", *, client_mode: str = "") -> dict[str, Any] | None:
        safe_url = self._normalize_public_url(url)
        if not safe_url:
            return None
        return {
            "type": "browser_open_requested",
            "url": safe_url,
            "label": self._clip(self._sanitize_output(title), 80),
            "reason": "browser_page_open_for_user",
            "client_mode": str(client_mode or ""),
            "requires_confirmation": False,
        }

    def _count_element_summary_lines(self, text: str) -> int:
        return sum(1 for line in str(text or "").splitlines() if re.match(r"^\d+\.\s+", line.strip()))

    def _sanitize_output(self, value: str) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(?i)authorization:\s*bearer\s+[^\s]+", "Authorization: Bearer [redacted]", text)
        text = re.sub(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
        text = re.sub(r"(?i)([?&](?:api[_-]?key|password|secret|token)=)[^&#\s]+", r"\1[redacted]", text)
        text = re.sub(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
        return text.strip()

    def _clip(self, value: str, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]"


def _mcp_server_has_transport_config(server: Mapping[str, Any] | None) -> bool:
    if not isinstance(server, Mapping) or not bool(server.get("enabled")):
        return False
    transport = str(server.get("transport") or "stdio").strip().lower().replace("-", "_")
    if transport in {"http", "streamablehttp", "streamable_http"}:
        return bool(str(server.get("url") or "").strip())
    return transport == "stdio" and bool(str(server.get("command") or "").strip())


class WebSearchToolHandler(BaseToolHandler):
    tool_type = "web_search"

    ALLOWED_ACTIONS = {"search", "batch_search", "extract", "get_sub_domains"}
    MAX_QUERY_LENGTH = 240
    MAX_QUERIES = 4
    MAX_RESULTS = 10
    MAX_FOLLOWUP_CHARS = 6000
    MAX_EXTRACT_CHARS = 5000

    def tool_spec(self):  # M66-B: canonical ToolSpec authority
        return WEB_SEARCH_TOOL_SPEC

    def __init__(
        self,
        *,
        config_base_dir: Path | str | None = None,
        server_id: str = "anysearch",
        mcp_tool_caller: Any = None,
        readiness_mcp_tool_caller: Any = None,
        anysearch_rest_client: Any = None,
        readiness_ready_ttl_seconds: float = 30 * 60,
        readiness_failure_ttl_seconds: float = 60.0,
        readiness_probe_in_background: bool = True,
        readiness_clock=time.monotonic,
    ) -> None:
        self.config_base_dir = config_base_dir if config_base_dir is not None else getattr(config, "DATA_DIR", None)
        self.server_id = str(server_id or "anysearch").strip() or "anysearch"
        mcp_timeout_seconds = float(getattr(config, "WEB_SEARCH_MCP_TIMEOUT_SECONDS", 35.0) or 35.0)
        self.mcp_tool_caller = mcp_tool_caller or McpToolCaller(
            timeout_seconds=mcp_timeout_seconds
        )
        self.readiness_mcp_tool_caller = readiness_mcp_tool_caller or self.mcp_tool_caller
        self.anysearch_rest_client = anysearch_rest_client or AnySearchRestClient(
            timeout_seconds=mcp_timeout_seconds
        )
        self._readiness_ready_ttl_seconds = max(30.0, float(readiness_ready_ttl_seconds))
        self._readiness_failure_ttl_seconds = max(5.0, float(readiness_failure_ttl_seconds))
        self._readiness_probe_in_background = bool(readiness_probe_in_background)
        self._readiness_clock = readiness_clock
        self._readiness_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._readiness_probes_inflight: set[tuple[str, str]] = set()
        self._readiness_lock = threading.RLock()

    def capability_status(
        self,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
    ) -> dict[str, Any]:
        del session_id
        runtime_profile_user_id = self._resolve_runtime_profile_user_id_values(
            profile_user_id=profile_user_id,
            client_mode=client_mode,
        )
        server = get_mcp_server_runtime_config(
            base_dir=self.config_base_dir,
            profile_user_id=runtime_profile_user_id,
            server_id=self.server_id,
        )
        if server and not bool(server.get("enabled")):
            return {"enabled": False, "status": "disabled", "reason": "anysearch_disabled"}

        if not _mcp_server_has_transport_config(server):
            return self._rest_capability_status(runtime_profile_user_id)

        fingerprint = self._server_readiness_fingerprint(server)
        cache_key = (runtime_profile_user_id, fingerprint)
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached = self._readiness_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return self._stable_configured_status(cached[1])
            if self._readiness_probe_in_background:
                if cache_key not in self._readiness_probes_inflight:
                    self._readiness_probes_inflight.add(cache_key)
                    threading.Thread(
                        target=self._probe_readiness_background,
                        kwargs={"cache_key": cache_key, "server": dict(server)},
                        name="akane-anysearch-readiness",
                        daemon=True,
                    ).start()
                return {
                    "enabled": True,
                    "status": "checking",
                    "reason": "anysearch_probe_pending",
                    "cache_ttl_seconds": 1.0,
                }
        return self._stable_configured_status(self._probe_readiness(cache_key=cache_key, server=server))

    def _rest_capability_status(self, profile_user_id: str) -> dict[str, Any]:
        endpoint = str(getattr(self.anysearch_rest_client, "endpoint", "anysearch-rest") or "anysearch-rest")
        fingerprint = "rest:" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        cache_key = (profile_user_id, fingerprint)
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached = self._readiness_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return self._stable_configured_status(cached[1])
            if self._readiness_probe_in_background:
                if cache_key not in self._readiness_probes_inflight:
                    self._readiness_probes_inflight.add(cache_key)
                    threading.Thread(
                        target=self._probe_rest_readiness_background,
                        kwargs={"cache_key": cache_key},
                        name="akane-anysearch-rest-readiness",
                        daemon=True,
                    ).start()
                return {
                    "enabled": True,
                    "status": "checking",
                    "reason": "anysearch_rest_probe_pending",
                    "cache_ttl_seconds": 1.0,
                }
        return self._stable_configured_status(self._probe_rest_readiness(cache_key=cache_key))

    def _probe_rest_readiness(self, *, cache_key: tuple[str, str]) -> dict[str, Any]:
        try:
            self.anysearch_rest_client.call(action="search", arguments={"query": "OpenAI", "max_results": 1})
            status = {"enabled": True, "status": "ready", "reason": "", "transport": "rest"}
        except AnySearchRestError as exc:
            status = {"enabled": False, "status": "unavailable", "reason": exc.reason, "transport": "rest"}
        except Exception:
            status = {
                "enabled": False,
                "status": "unavailable",
                "reason": "anysearch_rest_probe_failed",
                "transport": "rest",
            }
        self._remember_readiness(cache_key, status)
        return status

    def _probe_rest_readiness_background(self, *, cache_key: tuple[str, str]) -> None:
        try:
            self._probe_rest_readiness(cache_key=cache_key)
        finally:
            with self._readiness_lock:
                self._readiness_probes_inflight.discard(cache_key)

    def _probe_readiness(
        self,
        *,
        cache_key: tuple[str, str],
        server: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            result = self._run_coro_blocking(
                self._call_readiness_mcp(
                    server=server,
                    tool_name="search",
                    arguments={"query": "OpenAI", "max_results": 1},
                )
            )
            if self._mcp_result_is_error(result):
                status = {"enabled": False, "status": "unavailable", "reason": "anysearch_probe_error"}
            else:
                status = {"enabled": True, "status": "ready", "reason": "", "cache_ttl_seconds": 5.0}
        except McpStdioDiscoveryError as exc:
            return self._probe_rest_fallback(
                cache_key=cache_key,
                mcp_reason=self._safe_mcp_failure_reason(exc),
            )
        except Exception as exc:
            status = {
                "enabled": False,
                "status": "unavailable",
                "reason": f"anysearch_probe_failed:{type(exc).__name__}",
            }
        self._remember_readiness(cache_key, status)
        return status

    def _probe_rest_fallback(self, *, cache_key: tuple[str, str], mcp_reason: str) -> dict[str, Any]:
        endpoint = str(getattr(self.anysearch_rest_client, "endpoint", "anysearch-rest") or "anysearch-rest")
        rest_cache_key = (cache_key[0], "rest:" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest())
        rest_status = self._probe_rest_readiness(cache_key=rest_cache_key)
        if bool(rest_status.get("enabled")):
            status = {
                **rest_status,
                "fallback_from": "mcp",
                "mcp_reason": str(mcp_reason or "mcp_call_failed")[:120],
            }
        else:
            status = {
                "enabled": False,
                "status": "unavailable",
                "reason": str(mcp_reason or "mcp_call_failed")[:120],
                "fallback_reason": str(rest_status.get("reason") or "anysearch_rest_failed")[:120],
            }
        self._remember_readiness(cache_key, status)
        return status

    def _probe_readiness_background(self, *, cache_key: tuple[str, str], server: Mapping[str, Any]) -> None:
        try:
            self._probe_readiness(cache_key=cache_key, server=server)
        finally:
            with self._readiness_lock:
                self._readiness_probes_inflight.discard(cache_key)

    def build_prompt_instruction(self) -> str:
        return (
            "- web_search：当回答依赖公开网页、公开来源核对、最新/当前/实时/近期信息或高变化事实时使用；"
            "不需要用户显式说“联网”“搜索”“查询”。例：日经指数现在多少、七月新番有哪些、最新模型价格、今天上海天气 -> web_search。"
            '搜索格式为 {"type":"web_search","action":"search","query":"搜索词","max_results":5}；'
            '多目标/时间范围检索格式为 {"type":"web_search","action":"batch_search","queries":["查询1","查询2"],"max_results":3}；'
            '网页提取格式为 {"type":"web_search","action":"extract","url":"https://...","max_chars":3000}。'
            "最近一周、时间范围、新闻汇总或多来源核验通常优先 batch_search；如果结果只覆盖一个日期或单一来源，"
            "继续换日期、语言或来源检索，并对关键结果 extract，不要把一次搜索当成完整覆盖。"
            "只搜索或提取公开网页；不要用它访问 localhost、内网地址、file 路径、登录页、付费页或用户私密链接。"
            "web_search 不会打开浏览器窗口、滚动网页或点击链接；如果用户要看页面或需要你继续操作某条结果，"
            "再调用 browser_page.navigate 或 open_browser。"
            "稳定常识、普通闲聊、创作或主观建议直接回复，不要为了展示能力而搜索。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = self._normalize_action(value)
        if action == "extract":
            url = self._normalize_public_url(value.get("url") or value.get("link"))
            if not url:
                return None
            return {
                "type": self.tool_type,
                "action": "extract",
                "url": url,
                "max_chars": self._coerce_int(
                    value.get("max_chars"), minimum=500, maximum=self.MAX_EXTRACT_CHARS, default=3000
                ),
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

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        runtime_profile_user_id = self._resolve_runtime_profile_user_id(context)
        server = get_mcp_server_runtime_config(
            base_dir=self.config_base_dir,
            profile_user_id=runtime_profile_user_id,
            server_id=self.server_id,
        )
        if server and not bool(server.get("enabled")):
            return self._failure("disabled", "AnySearch MCP 当前是关闭状态。")

        action = str(call.get("action") or "search").strip()
        arguments = self._build_mcp_arguments(call)
        use_mcp = _mcp_server_has_transport_config(server)
        if use_mcp and server:
            cached_status = self._cached_server_readiness(runtime_profile_user_id, server)
            if bool(cached_status and cached_status.get("enabled")) and cached_status.get("transport") == "rest":
                use_mcp = False
        redaction_terms = self._redaction_terms_for_server(server) if server else []
        try:
            if use_mcp:
                result = self._run_coro_blocking(self._call_mcp(server=server, tool_name=action, arguments=arguments))
            else:
                result = self.anysearch_rest_client.call(action=action, arguments=arguments)
        except AnySearchRestError as exc:
            return self._failure(exc.reason, "AnySearch 官方 HTTPS 搜索调用失败。")
        except McpStdioDiscoveryError as exc:
            mcp_reason = self._safe_mcp_failure_reason(exc)
            if use_mcp and server:
                self._remember_server_failure(runtime_profile_user_id, server, reason=mcp_reason)
            if action not in {"search", "batch_search"}:
                return self._failure(mcp_reason, "AnySearch MCP 调用失败或超时。")
            try:
                result = self.anysearch_rest_client.call(action=action, arguments=arguments)
            except AnySearchRestError as rest_exc:
                return self._failure(rest_exc.reason, "AnySearch MCP 与官方 HTTPS 搜索均不可用。")
            except Exception:
                return self._failure("anysearch_rest_failed", "AnySearch MCP 与官方 HTTPS 搜索均不可用。")
            use_mcp = False
            if server:
                self._remember_server_rest_fallback(
                    runtime_profile_user_id,
                    server,
                    mcp_reason=mcp_reason,
                )
        except Exception:
            if use_mcp and server:
                self._remember_server_failure(runtime_profile_user_id, server, reason="mcp_call_failed")
                return self._failure("mcp_call_failed", "AnySearch MCP 调用失败。")
            return self._failure("anysearch_rest_failed", "AnySearch 官方 HTTPS 搜索调用失败。")
        if self._mcp_result_is_error(result):
            if use_mcp and server:
                # A valid MCP error result proves the transport and server are
                # reachable.  It may only mean that one URL could not be
                # extracted, so it must not poison readiness for the next
                # search or another public source.
                self._remember_server_ready(runtime_profile_user_id, server)
            return self._mcp_tool_failure(
                action=action,
                result=result,
                redaction_terms=redaction_terms,
                profile_user_id=runtime_profile_user_id,
            )
        if use_mcp and server:
            self._remember_server_ready(runtime_profile_user_id, server)

        followup = self._format_followup(
            action=action,
            call=call,
            result=result if isinstance(result, dict) else {},
            redaction_terms=redaction_terms,
        )
        query_label = str(call.get("query") or " / ".join(str(item) for item in call.get("queries") or [])).strip()
        coverage_status = "unverified" if self._search_needs_broader_coverage(query_label) else "not_required"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "web_search_completed",
                    "provider": "anysearch",
                    "action": action,
                    "status": "ok",
                    "coverage_status": coverage_status,
                }
            ],
            followup_context=followup,
            state_updates={
                "web_search_status": "ok",
                "web_search_provider": "anysearch",
                "web_search_profile_user_id": runtime_profile_user_id,
                "web_search_coverage_status": coverage_status,
            },
        )

    def _resolve_runtime_profile_user_id(self, context: ToolExecutionContext) -> str:
        return self._resolve_runtime_profile_user_id_values(
            profile_user_id=context.profile_user_id,
            client_mode=context.client_mode,
        )

    def _resolve_runtime_profile_user_id_values(self, *, profile_user_id: str, client_mode: str) -> str:
        if str(client_mode or "").strip().lower() != "qq_text":
            return str(profile_user_id or "").strip() or "master"
        raw_value = str(getattr(config, "QQ_WEB_SEARCH_PROFILE_USER_ID", "") or "").strip()
        if not raw_value:
            raw_value = str(getattr(config, "WEB_OWNER_PROFILE_USER_ID", "") or "master").strip()
        if raw_value.lower() in {"conversation", "context", "current"}:
            raw_value = str(profile_user_id or "").strip()
        if not raw_value or not re.fullmatch(r"[A-Za-z0-9_.-]+", raw_value):
            return "master"
        return raw_value

    async def _call_mcp(
        self,
        *,
        server: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self.mcp_tool_caller(server=server, tool_name=tool_name, arguments=arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}

    async def _call_readiness_mcp(
        self,
        *,
        server: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self.readiness_mcp_tool_caller(server=server, tool_name=tool_name, arguments=arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _mcp_result_is_error(result: Any) -> bool:
        return isinstance(result, Mapping) and bool(result.get("isError") or result.get("is_error"))

    def _mcp_tool_failure(
        self,
        *,
        action: str,
        result: Mapping[str, Any],
        redaction_terms: list[str],
        profile_user_id: str,
    ) -> ToolExecutionResult:
        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        detail = self._clip(
            self._payload_to_text(payload, redaction_terms=redaction_terms),
            600,
        )
        reason = self._mcp_tool_error_reason(result=result, detail=detail)
        detail_text = f"\n服务返回：{detail}" if detail else ""
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "web_search_completed",
                    "provider": "anysearch",
                    "action": action,
                    "status": "failed",
                    "reason": reason,
                }
            ],
            followup_context=(
                f"AnySearch MCP 连接正常，但本次 {action} 请求没有拿到结果。{detail_text}\n"
                "这只表示当前调用或来源失败，不代表整个搜索服务不可用。"
                "可以根据错误内容调整查询、换公开来源，或继续使用其它安全的只读检索路径；"
                "不要编造结果。"
            ),
            state_updates={
                "web_search_status": "failed",
                "web_search_reason": reason,
                "web_search_provider": "anysearch",
                "web_search_profile_user_id": profile_user_id,
            },
        )

    @staticmethod
    def _mcp_tool_error_reason(*, result: Mapping[str, Any], detail: str) -> str:
        candidates = [
            result.get("code"),
            result.get("error_code"),
            result.get("reason"),
            *(str(detail or "").splitlines()[:2]),
        ]
        for candidate in candidates:
            reason = str(candidate or "").strip().lower()
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
                return reason
        return "mcp_tool_result_error"

    @staticmethod
    def _safe_mcp_failure_reason(value: Any) -> str:
        reason = str(value or "").partition(":")[0].strip().lower()
        return reason if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) else "mcp_call_failed"

    @staticmethod
    def _server_readiness_fingerprint(server: Mapping[str, Any]) -> str:
        stable = {
            "enabled": bool(server.get("enabled")),
            "transport": str(server.get("transport") or "stdio"),
            "command": str(server.get("command") or ""),
            "args": [str(item or "") for item in server.get("args") or []],
            "cwd": str(server.get("cwd") or ""),
            "url": str(server.get("url") or ""),
            "headers": {
                str(key): str(value)
                for key, value in sorted(
                    (server.get("headers") or {}).items(),
                    key=lambda item: str(item[0]),
                )
            }
            if isinstance(server.get("headers"), Mapping)
            else {},
        }
        serialized = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_configured_status(status: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(status)
        raw_enabled = bool(result.get("enabled"))
        result["enabled"] = True
        if str(result.get("status") or "") != "checking":
            result["healthy"] = raw_enabled
        return result

    def _remember_readiness(self, cache_key: tuple[str, str], status: Mapping[str, Any]) -> None:
        ttl = self._readiness_ready_ttl_seconds if bool(status.get("enabled")) else self._readiness_failure_ttl_seconds
        with self._readiness_lock:
            self._readiness_cache[cache_key] = (float(self._readiness_clock()) + ttl, dict(status))
            if len(self._readiness_cache) > 32:
                now = float(self._readiness_clock())
                self._readiness_cache = {key: value for key, value in self._readiness_cache.items() if value[0] > now}

    def _remember_server_ready(self, profile_user_id: str, server: Mapping[str, Any]) -> None:
        self._remember_readiness(
            (profile_user_id, self._server_readiness_fingerprint(server)),
            {"enabled": True, "status": "ready", "reason": "", "cache_ttl_seconds": 5.0},
        )

    def _cached_server_readiness(
        self,
        profile_user_id: str,
        server: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        cache_key = (profile_user_id, self._server_readiness_fingerprint(server))
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached = self._readiness_cache.get(cache_key)
            if cached is None or cached[0] <= now:
                return None
            return dict(cached[1])

    def _remember_server_failure(self, profile_user_id: str, server: Mapping[str, Any], *, reason: str) -> None:
        self._remember_readiness(
            (profile_user_id, self._server_readiness_fingerprint(server)),
            {"enabled": False, "status": "unavailable", "reason": str(reason or "mcp_call_failed")[:120]},
        )

    def _remember_server_rest_fallback(
        self,
        profile_user_id: str,
        server: Mapping[str, Any],
        *,
        mcp_reason: str,
    ) -> None:
        self._remember_readiness(
            (profile_user_id, self._server_readiness_fingerprint(server)),
            {
                "enabled": True,
                "status": "ready",
                "reason": "",
                "transport": "rest",
                "fallback_from": "mcp",
                "mcp_reason": str(mcp_reason or "mcp_call_failed")[:120],
            },
        )

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

    def _format_followup(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        result: Mapping[str, Any],
        redaction_terms: list[str],
    ) -> str:
        if bool(result.get("isError") or result.get("is_error")):
            return "AnySearch 返回了错误状态；请自然告诉用户这次联网检索没有拿到可靠结果。"
        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        if action == "extract":
            return self._format_extract_followup(call=call, payload=payload, redaction_terms=redaction_terms)
        if action == "get_sub_domains":
            return self._format_sub_domains_followup(call=call, payload=payload, redaction_terms=redaction_terms)
        return self._format_search_followup(action=action, call=call, payload=payload, redaction_terms=redaction_terms)

    def _format_search_followup(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        payload: Any,
        redaction_terms: list[str],
    ) -> str:
        results = self._coerce_search_results(payload)
        query_label = str(call.get("query") or " / ".join(str(item) for item in call.get("queries") or [])).strip()
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
        if not results:
            text = self._payload_to_text(payload, redaction_terms=redaction_terms)
            if text:
                lines.append(self._clip(text, self.MAX_FOLLOWUP_CHARS - 120))
            else:
                lines.append("没有拿到可用搜索结果。")
        else:
            for index, item in enumerate(results[: self.MAX_RESULTS], start=1):
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
                lines.append(f"{index}. {title}")
                if url:
                    lines.append(f"   URL: {url}")
                source_date = self._search_result_date_hint(item)
                if source_date:
                    lines.append(
                        "   来源日期字段（需结合正文判断含义）: "
                        + self._sanitize_output(source_date, redaction_terms=redaction_terms)[:160]
                    )
                if snippet:
                    lines.append(f"   摘要: {self._clip(snippet, 420)}")
        lines.append("请只基于这些公开搜索结果回答；没查到或不确定的部分要明确说明。")
        return self._clip("\n".join(lines), self.MAX_FOLLOWUP_CHARS)

    def _format_extract_followup(
        self,
        *,
        call: Mapping[str, Any],
        payload: Any,
        redaction_terms: list[str],
    ) -> str:
        max_chars = int(call.get("max_chars") or 3000)
        data = self._first_mapping(payload)
        title = self._sanitize_output(str(data.get("title") or data.get("name") or ""), redaction_terms=redaction_terms)
        text = self._sanitize_output(
            str(data.get("text") or data.get("content") or data.get("markdown") or data.get("body") or ""),
            redaction_terms=redaction_terms,
        )
        if not text:
            text = self._payload_to_text(payload, redaction_terms=redaction_terms)
        lines = [
            "【AnySearch 网页内容提取结果】",
            f"URL: {self._sanitize_output(str(call.get('url') or ''), redaction_terms=redaction_terms)[:500]}",
        ]
        if title:
            lines.append(f"标题：{self._clip(title, 160)}")
        lines.append(
            "证据口径：本轮网页提取发生时间不等于正文事实日期；涉及时效性事实时，以正文明确的日期、更新字段和来源时区为准。"
        )
        lines.append("正文摘录：")
        lines.append(self._clip(text or "没有拿到可用正文。", max_chars))
        return self._clip("\n".join(lines), self.MAX_FOLLOWUP_CHARS)

    def _format_sub_domains_followup(
        self,
        *,
        call: Mapping[str, Any],
        payload: Any,
        redaction_terms: list[str],
    ) -> str:
        text = self._payload_to_text(payload, redaction_terms=redaction_terms)
        domains = ", ".join(str(item) for item in call.get("domains") or [])
        lines = [
            "【AnySearch 域名能力结果】",
            f"域名：{self._sanitize_output(domains, redaction_terms=redaction_terms)[:240]}",
            self._clip(text or "没有拿到可用结果。", 3000),
        ]
        return self._clip("\n".join(lines), self.MAX_FOLLOWUP_CHARS)

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
        return []

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

    def _failure(self, status: str, message: str) -> ToolExecutionResult:
        reason = str(status or "unavailable").strip()[:120]
        transient = reason in {
            "mcp_tool_call_timeout",
            "mcp_call_failed",
            "mcp_result_error",
            "anysearch_network_unavailable",
            "anysearch_rate_limited",
            "anysearch_http_error",
            "anysearch_upstream_error",
            "anysearch_rest_failed",
        }
        next_step = (
            "如果仍有工具预算和其它安全的只读路径，可以换查询词、拆小批次、换公开来源或改用其它检索工具继续核验；"
            "如果没有可用路径，再基于现有证据降级回答。"
            if transient
            else "这是配置、启用状态或其它当前不可恢复的问题，不要反复调用同一工具。"
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {"type": "web_search_completed", "provider": "anysearch", "status": "unavailable", "reason": reason}
            ],
            followup_context=(
                f"AnySearch 联网能力暂时不可用：{message} 状态：{reason}。{next_step}"
                "不要编造搜索结果，也不要把一次来源失败说成所有公开信息渠道都不可用。"
            ),
            state_updates={"web_search_status": "unavailable", "web_search_reason": reason},
        )

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
            parsed = urlparse(text)
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
        url = str(value or "").strip()
        if len(url) > 1600:
            url = url[:1600]
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        hostname = parsed.hostname or ""
        if not hostname or self._is_private_or_local_host(hostname):
            return ""
        return url

    def _is_private_or_local_host(self, hostname: str) -> bool:
        host = str(hostname or "").strip().lower().strip("[]")
        if not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)

    def _coerce_int(self, value: Any, *, minimum: int, maximum: int, default: int) -> int:
        try:
            number = int(value)
        except Exception:
            number = default
        return max(minimum, min(maximum, number))

    def _redaction_terms_for_server(self, server: Mapping[str, Any]) -> list[str]:
        terms: list[str] = []
        args = [str(item or "") for item in server.get("args") or []]
        wanted = {match.group(1) for arg in args for match in re.finditer(r"\$\{([A-Z_][A-Z0-9_]{0,79})\}", arg)}
        raw_env = server.get("env") if isinstance(server.get("env"), Mapping) else {}
        for key in wanted:
            env_value = os.environ.get(key)
            if env_value:
                terms.append(env_value)
        for key, value in raw_env.items():
            if any(marker in str(key).lower() for marker in ("api_key", "password", "secret", "token")):
                terms.append(str(value or ""))
        for env_path in self._candidate_env_files(str(server.get("cwd") or "").strip() or None):
            try:
                if not env_path.is_file() or env_path.stat().st_size > 128 * 1024:
                    continue
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() in wanted:
                        terms.append(value.strip().strip("\"'"))
            except OSError:
                continue
        return [term for term in terms if len(term) >= 4]

    def _candidate_env_files(self, cwd: str | None) -> list[Path]:
        paths: list[Path] = []
        if cwd:
            paths.append(Path(cwd) / ".env")
        paths.append(Path.cwd() / ".env")
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _sanitize_output(self, value: str, *, redaction_terms: list[str]) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        for term in redaction_terms:
            if term:
                text = text.replace(term, "[redacted]")
        text = re.sub(r"(?i)authorization:\s*bearer\s+[^\s]+", "Authorization: Bearer [redacted]", text)
        text = re.sub(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
        text = re.sub(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
        return text.strip()

    def _clip(self, value: str, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]"

    def _run_coro_blocking(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result_box["value"] = asyncio.run(coro)
            except BaseException as exc:
                error_box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in error_box:
            raise error_box["error"]
        return result_box.get("value")
