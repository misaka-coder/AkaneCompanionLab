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
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote_plus, urlparse

import config

from ..anysearch_rest_client import AnySearchRestClient, AnySearchRestError
from ..browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner, ManagedBrowserSessionManager
from ..browser_page_contract import CONTROL_ACTIONS, MANAGED_ACTIONS, OPTION_KEYS, option_error, managed_capabilities
from ..capability_registry import OPEN_BROWSER_TOOL_SPEC, WEB_SEARCH_TOOL_SPEC
from ..capcore_runtime import (
    authorization_profile_user_id as capcore_authorization_profile_user_id,
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
    ToolFollowupEnvelope,
    ToolMetadata,
)

class OpenBrowserToolHandler(BaseToolHandler):
    tool_type = "open_browser"
    policy_accepted_native_tool = True

    def __init__(self, *, offer_source: Any = None) -> None:
        self._offer_source = offer_source

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        if self._offer_source is None:
            return {"enabled": False, "status": "unavailable", "reason": "satellite_not_configured"}
        try:
            ready = self._offer_source.resolve_receipt(self.tool_spec()) is not None
        except Exception:
            ready = False
        return {
            "enabled": ready,
            "status": "ready" if ready else "unavailable",
            "reason": "" if ready else "satellite_offline",
        }

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
                f"已请求桌宠为用户打开 {platform_label} 公开音乐搜索页（{url}）。"
                "桌宠尚未确认浏览器已经打开，这更不代表歌曲已经开始播放。"
                "如果用户还要求你继续点进结果或尝试播放，需要按 browser_page 的授权边界继续操作；"
                "不能登录、下载、绕过会员/版权限制，也不要声称已经打开或播放成功。"
            ),
            state_updates={
                "music_request_status": "requested_open",
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

    ALLOWED_ACTIONS = MANAGED_ACTIONS
    CONTROL_ACTIONS = CONTROL_ACTIONS | {"run_actions"}
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
    MAX_ELEMENT_LIMIT = 40
    MAX_SELECTOR_CHARS = 220
    MAX_FILL_TEXT_CHARS = 500

    def __init__(
        self,
        *,
        browser_runner: Any = None,
        generated_file_service: Any = None,
        attachment_service: Any = None,
        image_material_resolver: Any = None,
        allow_private_network_urls: bool | None = None,
        config_base_dir: Path | str | None = None,
        approval_checker: Callable[..., bool] | None = None,
        personal_offer_source: Any = None,
        executor_broker: Any = None,
        approval_store: Any = None,
    ) -> None:
        self.allow_private_network_urls = (
            bool(getattr(config, "BROWSER_PAGE_PRIVATE_NETWORK_ACCESS", False))
            if allow_private_network_urls is None
            else bool(allow_private_network_urls)
        )
        if browser_runner is None:
            headless = bool(getattr(config, "BROWSER_PAGE_HEADLESS", False))
            self.browser_runner = ManagedBrowserSessionManager(
                runner_factory=lambda: ManagedBrowserPageRunner(
                    headless=headless,
                    attachment_service=attachment_service,
                    allow_private_network_urls=self.allow_private_network_urls,
                ),
            )
        else:
            self.browser_runner = browser_runner
        self.generated_file_service = generated_file_service
        self.attachment_service = attachment_service
        self.image_material_resolver = image_material_resolver
        self.config_base_dir = config_base_dir if config_base_dir is not None else getattr(config, "DATA_DIR", None)
        self.approval_checker = approval_checker
        from ..personal_browser import PersonalBrowserBackend
        self.personal_browser = PersonalBrowserBackend(offer_source=personal_offer_source, broker=executor_broker,
            approval_store=approval_store, config_base_dir=self.config_base_dir)

    def build_prompt_instruction(self) -> str:
        network_scope = (
            "当前宿主已允许访问 localhost 和私网地址，可用于本机 ComfyUI、控制中心或用户明确指定的局域网服务。"
            if self.allow_private_network_urls
            else "当前宿主只允许公开 http(s) 页面；localhost 和私网地址会被拒绝。"
        )
        return (
            "个人已登录 Chrome 使用同一 browser_page，session_source=personal_chrome：先 connect，再 list_tabs/select_tab，"
            "后续携带返回的 browser_session_id/device_epoch。必须先在绑定电脑开启连接并由主人完成 Chrome 许可。"
            "个人模式只使用当前观察的 ref，不接受 CSS/坐标；截图是临时模型输入，不会生成 gen_* 文件。"
            "与 computer_use 切换前调用 handoff，之后重选窗口或标签并重新观察。disconnect 只断开，不关闭用户浏览器。"
            "- browser_page：需要自己读取、总结、核对页面正文或操作页面时才使用 browser_page；"
            "它操作当前会话独立的 Akane 可见托管浏览器窗口，"
            "它不会接管用户手动打开的 Edge/Chrome 标签页。"
            '先用 {"type":"browser_page","action":"navigate","url":"https://..."} 打开页面；'
            "navigate 与 click/fill/press/scroll 会自动重新观察页面，返回最新可访问性树（AX），"
            "并在多模态视觉通道中直接提供当前视口截图像素供你查看（无需额外调用 screenshot）。"
            "click/fill/press 必须携带上一步返回的 observation_id；坐标点击可用 screenshot_id 绑定。"
            "坐标证据失效时先调用 snapshot(observation_mode=hybrid) 获取新图。"
            "用 snapshot 读取 accessibility snapshot 和 ref，或用 elements 读取带 candidate_index 的可见候选；"
            "随后可用 click/fill/press/scroll 完成操作，并根据返回的最新页面证据继续。"
            "显式 screenshot 会生成 gen_* 长期文件句柄；"
            "只有用户明确要收到图片文件时才继续调用 send_file。"
            "工具失败结果会说明状态、是否可恢复和建议的下一动作：按该反馈继续，不要把 browser_closed 当成网络失败，"
            "也不要编造未读取到的页面内容。页面关闭后，navigate 可新建窗口继续；stale_target 后重新 snapshot/elements。"
            "cursor 只续读同一份已捕获快照，不重复截图，也不会再次操作页面。"
            "capabilities 可查询后端差异；托管模式支持 upload(files=材料句柄)、select_option(values)、set_checked(checked)、hover。"
            "frame_selector 指定一个 iframe；先在相同 frame 观察，再操作其中控件。snapshot/read_text 可加 scope_selector 局部读取。"
            "run_actions 可执行至多 6 个同文档 selector 动作，遇导航/弹窗/失败后返回逐步进度；只继续尚未执行部分。"
            "fill 保留原文，空字符串表示清空；dialog 仅指定本次动作首个对话框的响应。个人 Chrome 不支持这些托管扩展。"
            "elements 只是候选，不要声称已经点击或输入。用户给出完整多步目标时持续执行到完成或真实阻塞，"
            "不要每完成一步就询问用户。"
            "click/fill/press 遵循当前能力审批；登录、支付、发布、删除、上传下载和私密表单需要相应授权。"
            '只需替用户打开链接而不读取时使用 open_browser；若还要额外在系统浏览器给用户打开，可加 "open_for_user":true；'
            f"只需检索资料时优先 web_search。{network_scope}"
        )

    def capability_status(self) -> dict[str, Any]:
        if self.personal_browser.available():
            return {"enabled":True, "status":"ready", "reason":"", "personal_chrome":"device_available"}
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

    def close(self, *, timeout: float = 5.0) -> bool:
        shutdown = getattr(self.browser_runner, "shutdown", None)
        if not callable(shutdown):
            return True
        try:
            try:
                shutdown(timeout=timeout)
            except TypeError:
                shutdown()
            return True
        except Exception:
            return False

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        if value.get("action") == "capabilities" and value.get("session_source", "managed") in {"managed", "personal_chrome"}:
            return {"type": self.tool_type, "action": "capabilities", "session_source": value.get("session_source", "managed")}
        if value.get("session_source") == "personal_chrome":
            from ..personal_browser_contract import normalize_personal_call
            normalized = normalize_personal_call(value)
            if normalized and normalized.get("url") and not self._normalize_public_url(normalized["url"]):
                return None
            return normalized
        if value.get("session_source") not in (None, "", "managed"):
            return None
        cursor = str(value.get("cursor") or "").strip()
        if cursor:
            return {"type": self.tool_type, "cursor": cursor}
        action = self._normalize_action(value)
        if not action:
            return None
        if option_error({**value, "action": action}):
            return None
        actions = []
        if action == "run_actions":
            raw_actions = value.get("actions")
            if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 6:
                return None
            from ..browser_page_contract import STEP_SCHEMA
            for step in raw_actions:
                if (not isinstance(step, dict) or step.get("action") not in CONTROL_ACTIONS
                        or set(step) - set(STEP_SCHEMA["properties"]) or not step.get("selector")):
                    return None
                if step.get("frame_selector", "") != value.get("frame_selector", ""):
                    return None
                normalized_step = self.normalize_call({"type": self.tool_type, **step})
                if normalized_step is None:
                    return None
                actions.append({k: normalized_step[k] for k in STEP_SCHEMA["properties"] if k in step})
        url = ""
        raw_url = value.get("url") or value.get("link") or value.get("href")
        if action in {"navigate", "read_text"} and raw_url:
            url = self._normalize_public_url(raw_url)
            if not url:
                return None
        if action == "navigate" and not url:
            return None
        raw_target = value.get("target")
        selector = self._normalize_selector(value.get("selector") or raw_target)
        ref = self._normalize_ref(value.get("ref") or value.get("element_ref") or value.get("target_ref") or raw_target)
        candidate_index = self._normalize_candidate_index(
            value.get("candidate_index")
            or value.get("candidateIndex")
            or value.get("candidate")
            or value.get("index")
        )
        if action != "click":
            candidate_index = 0

        coordinate: tuple[int, int] | None = None
        raw_coord = value.get("coordinate")
        if isinstance(raw_coord, (list, tuple)) and len(raw_coord) == 2:
            try:
                coordinate = (int(raw_coord[0]), int(raw_coord[1]))
            except (ValueError, TypeError):
                pass

        if action in CONTROL_ACTIONS - {"press"} and not selector and not ref and candidate_index <= 0 and coordinate is None:
            return None
        if action != "click" and coordinate is not None:
            return None
        raw_text = next((value[k] for k in ("text", "value", "query") if k in value), None)
        text = self._normalize_fill_text(raw_text)
        if action == "fill" and text is None:
            return None
        key = self._normalize_press_key(value.get("key") or value.get("press"))
        if action == "press" and not key:
            return None
        observation_id = str(value.get("observation_id") or "").strip()[:80]
        raw_obs_mode = str(value.get("observation_mode") or "").strip().lower()
        observation_mode = raw_obs_mode if raw_obs_mode in {"hybrid", "text", "visual"} else ""
        screenshot_id = str(value.get("screenshot_id") or "").strip()[:80]
        download_id = str(value.get("download_id") or "").strip()[:80]
        return {
            "type": self.tool_type,
            "action": action,
            "url": url,
            "open_for_user": self._coerce_bool(value.get("open_for_user") or value.get("openForUser")),
            "scroll_delta": self._coerce_int(
                value.get("scroll_delta") or value.get("delta"), minimum=-2400, maximum=2400, default=800
            ),
            "element_limit": self._coerce_int(
                value.get("element_limit") or value.get("limit"), minimum=1, maximum=self.MAX_ELEMENT_LIMIT, default=20
            ),
            "selector": selector,
            "ref": ref,
            "text": text if text is not None else "",
            "key": key,
            "candidate_index": candidate_index,
            "observation_id": observation_id,
            "observation_mode": observation_mode,
            "coordinate": coordinate,
            "screenshot_id": screenshot_id,
            "download_id": download_id,
            **{k: value[k] for k in OPTION_KEYS if k in value},
            **({"actions": actions} if action == "run_actions" else {}),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if call.get("action") == "capabilities":
            from ..personal_browser_contract import ACTIONS
            facts = managed_capabilities() if call.get("session_source") != "personal_chrome" else {
                "backend": "personal_chrome", "actions": [*ACTIONS, "capabilities"],
                "fill_max_chars": 500, "fill_empty": True, "fill_multiline": False,
                "targets": "current observation refs", "coordinates": False, "upload": False,
                "download_transfer": False, "frames": False, "batch": False,
            }
            return ToolExecutionResult(tool_type=self.tool_type, followup_context=json.dumps(facts, ensure_ascii=False))
        if call.get("session_source") == "personal_chrome":
            return self.personal_browser.execute(call=call, context=context)
        cursor = str(call.get("cursor") or "").strip()
        if cursor:
            return self._execute_snapshot_continuation(cursor=cursor, context=context)
        action = str(call.get("action") or "current").strip() or "current"
        url = str(call.get("url") or "").strip()
        open_for_user = bool(call.get("open_for_user"))
        scroll_delta = self._coerce_int(call.get("scroll_delta"), minimum=-2400, maximum=2400, default=800)
        element_limit = self._coerce_int(
            call.get("element_limit"), minimum=1, maximum=self.MAX_ELEMENT_LIMIT, default=20
        )
        selector = str(call.get("selector") or "").strip()
        ref = str(call.get("ref") or "").strip()
        text = str(call.get("text") or "")
        key = str(call.get("key") or "").strip()
        candidate_index = self._coerce_int(call.get("candidate_index"), minimum=0, maximum=30, default=0)
        observation_id = str(call.get("observation_id") or "").strip()
        raw_obs_mode = str(call.get("observation_mode") or "").strip().lower()
        observation_mode = raw_obs_mode if raw_obs_mode in {"hybrid", "text", "visual"} else ""
        coordinate = call.get("coordinate")
        screenshot_id = str(call.get("screenshot_id") or "").strip()
        download_id = str(call.get("download_id") or "").strip()
        if action in self.CONTROL_ACTIONS or call.get("dialog", {}).get("action") == "accept":
            authorization = self._authorize_control_action(action=action, call=call, context=context)
            if not authorization.get("ok"):
                return self._approval_required(action=action, call=call, context=context, authorization=authorization)
        run_kwargs: dict[str, Any] = {
            "action": action,
            "url": url,
            "max_chars": 50_000,
            "observation_id": observation_id,
            "observation_mode": observation_mode,
        }
        options = {k: call[k] for k in OPTION_KEYS if k in call}
        if action == "upload":
            paths, reason = self._resolve_upload_files(call["files"], context)
            if reason:
                return self._failure(BrowserPageResult(ok=False, status="invalid_request", action=action, reason=reason))
            options["upload_paths"] = paths
        if action == "run_actions":
            steps = []
            for step in call["actions"]:
                prepared = dict(step)
                if step["action"] == "upload":
                    paths, reason = self._resolve_upload_files(step["files"], context)
                    if reason:
                        return self._failure(BrowserPageResult(ok=False, status="invalid_request", action=action, reason=reason))
                    prepared["upload_paths"] = paths
                steps.append(prepared)
            options["actions"] = steps
        if options:
            run_kwargs["options"] = options
        if coordinate is not None:
            run_kwargs["coordinate"] = coordinate
        if screenshot_id:
            run_kwargs["screenshot_id"] = screenshot_id
        if download_id:
            run_kwargs["download_id"] = download_id
        screenshot_output_path: Path | None = None
        if action == "screenshot":
            if self.generated_file_service is None:
                return self._failure(
                    BrowserPageResult(
                        ok=False,
                        status="unavailable",
                        action=action,
                        reason="generated_file_service_unavailable",
                    )
                )
            try:
                screenshot_output_path = self.generated_file_service.allocate_output_path(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    title="browser-screenshot",
                    output_format="png",
                    timestamp=context.now_ts,
                )
                screenshot_output_path.parent.mkdir(parents=True, exist_ok=True)
                run_kwargs["screenshot_path"] = str(screenshot_output_path)
            except Exception:
                return self._failure(
                    BrowserPageResult(
                        ok=False,
                        status="unavailable",
                        action=action,
                        reason="screenshot_output_allocation_failed",
                    )
                )
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
        runner = self._runner_for_context(context)
        runner_fn = getattr(runner, "run", None)
        effective_run_kwargs = dict(run_kwargs)
        if callable(runner_fn):
            try:
                sig = inspect.signature(runner_fn)
                has_var_keyword = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if not has_var_keyword:
                    allowed_params = set(sig.parameters.keys())
                    if "options" in run_kwargs and "options" not in allowed_params:
                        return self._failure(BrowserPageResult(ok=False, status="unavailable", action=action,
                            reason="browser_runner_extensions_unavailable"))
                    effective_run_kwargs = {k: v for k, v in run_kwargs.items() if k in allowed_params}
            except (ValueError, TypeError):
                pass
        try:
            result = runner.run(**effective_run_kwargs)
        except Exception:
            result = BrowserPageResult(
                ok=False,
                status="unavailable",
                action=action,
                reason="browser_runner_failed",
                action_state="unknown" if action in self.CONTROL_ACTIONS or action in {"navigate", "scroll"} else "not_started",
                observation_state="failed",
                next_action="current",
            )
        normalized = self._normalize_result(result, fallback_action=action)
        open_event_url = url or str(normalized.url or "").strip()
        open_event = (
            self._build_open_event(open_event_url, normalized.title, client_mode=context.client_mode)
            if open_for_user
            else None
        )
        if not normalized.ok:
            if screenshot_output_path is not None:
                try:
                    screenshot_output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return self._failure(normalized, open_event=open_event)

        safe_url = self._sanitize_output(normalized.url)[:800]
        safe_title = self._clip(self._sanitize_output(normalized.title), 180)
        safe_text = self._sanitize_output(normalized.text, strip=False)
        lines = ["【Akane 托管浏览器窗口】", f"动作：{normalized.action}"]
        if normalized.step_results:
            lines.append("批次逐步结果：" + json.dumps(normalized.step_results, ensure_ascii=False))
        if safe_url:
            lines.append(f"URL: {safe_url}")
        if safe_title:
            lines.append(f"标题：{safe_title}")
        if normalized.page_revision:
            lines.append(f"页面版本：{normalized.page_revision}")
        if getattr(normalized, "observation_id", ""):
            lines.append(f"观察编号：{normalized.observation_id}")
        if normalized.screenshot_id:
            width, height = normalized.viewport
            image_width, image_height = normalized.screenshot_dimensions
            lines.append(
                f"截图编号 screenshot_id：{normalized.screenshot_id}；"
                f"视口：{width} × {height} CSS 像素；截图：{image_width} × {image_height} 像素。"
                "coordinate=[x,y] 使用视口左上角为原点的 CSS 像素，须绑定本截图编号。"
            )
        if normalized.visual_status == "visual_unavailable":
            lines.append("当前视觉观察不可用；请先使用文本证据或重新观察，不得猜测截图坐标。")
        if normalized.reason:
            lines.append(f"补充状态：{self._clip(self._sanitize_output(normalized.reason), 180)}")
        if normalized.next_action:
            lines.append(f"建议下一动作：browser_page.{normalized.next_action}，不要重放已执行的操作。")
        if getattr(normalized, "download_id", ""):
            dl_status = getattr(normalized, "download_status", "") or "completed"
            dl_line = f"【文件下载】标识: {normalized.download_id} | 状态: {dl_status}"
            if getattr(normalized, "download_filename", ""):
                dl_line += f" | 文件名: {normalized.download_filename}"
            if getattr(normalized, "attachment_handle", ""):
                dl_line += f" | 工作台材料句柄: {normalized.attachment_handle}（可通过 inspect_attachment 查看或 send_file 发送）"
            lines.append(dl_line)
        if getattr(normalized, "dialog_events", ()):
            for dialog in normalized.dialog_events:
                dialog_type = str(dialog.get("type") or "dialog")[:40]
                dialog_message = str(dialog.get("message") or "")[:200]
                lines.append(f"【浏览器对话框】{dialog.get('state', 'unknown')} / {dialog.get('response', 'unknown')} {dialog_type}: {dialog_message}")
        if normalized.action in self.CONTROL_ACTIONS:
            lines.append(
                f"动作状态：{getattr(normalized, 'action_state', 'executed')}；"
                f"观察状态：{getattr(normalized, 'observation_state', 'complete')}；"
                f"页面变化：{getattr(normalized, 'page_changed', 'unknown')}"
            )
        generated_screenshot: dict[str, Any] | None = None
        model_image_inputs: list[dict[str, Any]] = []
        if normalized.action == "screenshot" and screenshot_output_path is not None:
            try:
                generated_screenshot = self.generated_file_service.register_generated_artifact(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    output_path=screenshot_output_path,
                    output_title="browser-screenshot",
                    output_format="png",
                    mime_type="image/png",
                    content_card={"kind": "browser_screenshot", "url": safe_url, "title": safe_title},
                    summary=f"托管浏览器截图：{safe_title or safe_url or '当前页面'}",
                    created_by_tool="browser_page",
                    send_to_user=False,
                    timestamp=context.now_ts,
                )
            except Exception:
                try:
                    screenshot_output_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return self._failure(
                    BrowserPageResult(
                        ok=False,
                        status="unavailable",
                        action=action,
                        reason="screenshot_registration_failed",
                    )
                )
            handle = str(generated_screenshot.get("generated_handle") or "").strip()
            if self.image_material_resolver is not None and handle:
                try:
                    image_result = self.image_material_resolver.build_model_image_inputs(
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        targets=[handle],
                        max_count=1,
                    )
                    model_image_inputs = list(image_result.get("images") or [])
                except Exception:
                    model_image_inputs = []
            if model_image_inputs:
                lines.append(
                    f"已截取当前可见区域并登记为 {handle}；截图像素已随本次工具结果直接交给你查看。"
                    "请先依据图像与页面状态继续；只有用户要收到文件时才调用 send_file。"
                )
            else:
                lines.append(
                    f"已截取当前可见区域并登记为 {handle}，但本次未能直接加载图像像素。"
                    f"需要看图时调用 load_material 加载 {handle}；用户要收到文件时调用 send_file。"
                )
        elif not model_image_inputs and getattr(normalized, "model_image", None):
            model_image_inputs = [normalized.model_image]
            lines.append("【多模态视口截图】当前页面截图像素已随本次工具结果直接交给你查看，请结合界面视觉与可访问性证据继续。")
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
        continuation = None
        diagnostics: dict[str, Any] = {
            "action": normalized.action,
            "complete": bool(normalized.complete),
            "shown_chars": int(normalized.shown_chars or 0),
            "total_chars": int(normalized.total_chars or 0),
            "snapshot_id": str(normalized.snapshot_id or ""),
            "observation_id": str(getattr(normalized, "observation_id", "") or ""),
        }
        if not normalized.complete and normalized.snapshot_id and normalized.next_cursor:
            cursor = self._issue_browser_cursor(
                snapshot_id=normalized.snapshot_id,
                offset=normalized.next_cursor,
                context=context,
            )
            if cursor:
                continuation = {"type": self.tool_type, "cursor": cursor}
                lines.append(
                    f"本页之后还有未展示的快照内容（已展示 {normalized.shown_chars}/{normalized.total_chars} 字）。"
                    "如果当前内容已经足够回答，可以直接回答；只有确实需要同一份快照的后续内容时，才调用："
                    f'browser_page(cursor="{cursor}")'
                )
        events = []
        if open_event:
            events.append(open_event)
        if generated_screenshot is not None:
            public_generated = {
                key: generated_screenshot.get(key)
                for key in (
                    "generated_id",
                    "generated_handle",
                    "output_title",
                    "output_format",
                    "file_ext",
                    "mime_type",
                    "file_size",
                )
                if generated_screenshot.get(key) not in (None, "")
            }
            events.append(
                {
                    "type": "generated_file_ready",
                    # Public event contract: the stable handle is sufficient
                    # for send_file. Managed storage paths remain host-only.
                    "generated_file": public_generated,
                    "send_to_user": False,
                }
            )
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
                "snapshot_id": str(normalized.snapshot_id or ""),
                "observation_id": str(getattr(normalized, "observation_id", "") or ""),
                "screenshot_id": normalized.screenshot_id,
                "action_state": normalized.action_state,
                "observation_state": normalized.observation_state,
                "visual_status": normalized.visual_status,
                "page_revision": str(normalized.page_revision or ""),
                "complete": bool(normalized.complete),
                "shown_chars": int(normalized.shown_chars or 0),
                "total_chars": int(normalized.total_chars or 0),
            }
        )
        content = "\n".join(lines)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=bool(normalized.complete),
                continuation=continuation,
                diagnostics=diagnostics,
            ),
            state_updates={
                "browser_page_status": normalized.status,
                "browser_page_url": safe_url,
                "browser_page_title": safe_title,
                "browser_observation_id": str(getattr(normalized, "observation_id", "") or ""),
                "browser_open_requested": bool(open_event),
                "browser_page_element_count": self._count_element_summary_lines(safe_text)
                if normalized.action == "elements"
                else 0,
                "browser_page_next_hint": next_hint,
                "browser_control_status": normalized.status if normalized.action in self.CONTROL_ACTIONS else "",
                "browser_screenshot_handle": (
                    str(generated_screenshot.get("generated_handle") or "")
                    if generated_screenshot is not None
                    else ""
                ),
            },
            model_image_inputs=model_image_inputs,
        )

    def _execute_snapshot_continuation(
        self, *, cursor: str, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        from ..paged_reading import (
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            page_failure_feedback,
            parse_json_payload,
            parse_paged_cursor,
            slice_page,
        )

        payload = parse_json_payload(
            parse_paged_cursor(cursor, tool="bp", binding=self._browser_cursor_owner_binding(context))
        )
        if not isinstance(payload, dict):
            return self._snapshot_failure("cursor_invalid", "cursor 不属于当前用户/会话，或已损坏")
        snapshot_id = str(payload.get("s") or "").strip()
        if not snapshot_id:
            return self._snapshot_failure("cursor_invalid", "cursor 没有快照标识")
        try:
            offset = max(0, int(payload.get("o") or 0))
        except (TypeError, ValueError):
            return self._snapshot_failure("cursor_invalid", "cursor 偏移无效")
        runner = self._runner_for_context(context)
        record = runner.read_snapshot(snapshot_id) if hasattr(runner, "read_snapshot") else None
        if record is None:
            has_live = bool(getattr(runner, "has_live_page", None) and runner.has_live_page())
            return self._snapshot_failure(
                "page_closed" if not has_live else "snapshot_expired",
                "浏览器页面已关闭" if not has_live else "该快照已过期，需要重新读取当前页面",
            )
        snapshot_text = str(record.get("text") or "")
        if len(snapshot_text) <= offset and offset > 0:
            return self._snapshot_failure("stale_cursor", "快照内容比 cursor 记录的更短")
        page_text, next_offset, total_chars = slice_page(
            snapshot_text,
            start=offset,
            budget_chars=NEXT_PAGE_BUDGET_CHARS,
            budget_lines=NEXT_PAGE_BUDGET_LINES,
        )
        safe_page = self._sanitize_output(page_text, strip=False)
        complete = next_offset >= total_chars
        kind = str(record.get("kind") or "page")
        lines = [
            "【Akane 托管浏览器窗口·同一快照续读】",
            f"动作：snapshot 续读（不会重新滚动或点击页面）",
            f"URL: {self._sanitize_output(str(record.get('url') or ''))[:800]}",
            f"页面版本：{str(record.get('revision') or '')}",
            "元素摘要：" if kind == "elements" else "页面状态快照：",
            safe_page,
        ]
        continuation = None
        if not complete:
            next_cursor = self._issue_browser_cursor(snapshot_id=snapshot_id, offset=next_offset, context=context)
            if next_cursor:
                continuation = {"type": self.tool_type, "cursor": next_cursor}
                lines.append(
                    f"本页之后还有未展示的快照内容（已展示 {len(page_text)}/{total_chars} 字）。"
                    "如果当前内容已经足够回答，可以直接回答；只有确实需要同一份快照的后续内容时，才调用："
                    f'browser_page(cursor="{next_cursor}")'
                )
        else:
            lines.append(f"已读完这份快照（共 {total_chars} 字）。要查看操作后的最新页面状态请重新调用 snapshot。")
        content = "\n".join(lines)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "browser_page_read",
                    "provider": "managed_browser",
                    "action": "snapshot",
                    "status": "available",
                    "url": str(record.get("url") or ""),
                    "snapshot_id": snapshot_id,
                    "page_revision": str(record.get("revision") or ""),
                    "complete": complete,
                    "shown_chars": len(page_text),
                    "total_chars": total_chars,
                    "requires_confirmation": False,
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=complete,
                continuation=continuation,
                diagnostics={
                    "action": "snapshot",
                    "complete": complete,
                    "shown_chars": len(page_text),
                    "total_chars": total_chars,
                    "snapshot_id": snapshot_id,
                },
            ),
            state_updates={
                "browser_page_status": "available",
                "browser_page_url": str(record.get("url") or ""),
                "browser_page_title": str(record.get("title") or ""),
            },
        )

    def _issue_browser_cursor(self, *, snapshot_id: str, offset: Any, context: ToolExecutionContext) -> str:
        from ..paged_reading import json_payload, make_paged_cursor

        try:
            clean_offset = max(0, int(str(offset or "0").split(":")[-1]))
        except (TypeError, ValueError):
            return ""
        return make_paged_cursor(
            tool="bp",
            binding=self._browser_cursor_owner_binding(context),
            payload=json_payload({"s": str(snapshot_id or ""), "o": clean_offset}),
        )

    def _browser_cursor_owner_binding(self, context: ToolExecutionContext) -> str:
        from ..paged_reading import cursor_binding

        return cursor_binding("browser_page", context.profile_user_id, context.session_id)

    def _snapshot_failure(self, status: str, reason: str) -> ToolExecutionResult:
        from ..paged_reading import page_failure_feedback

        content = page_failure_feedback(status=status, tool=self.tool_type, detail=reason)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "browser_page_read",
                    "provider": "managed_browser",
                    "action": "snapshot",
                    "status": status,
                    "reason": reason,
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics={"status": status},
            ),
            state_updates={
                "browser_page_status": status,
                "browser_page_reason": reason,
            },
        )

    def _failure(self, result: BrowserPageResult, *, open_event: dict[str, Any] | None = None) -> ToolExecutionResult:
        status = str(result.status or "unavailable").strip()[:120] or "unavailable"
        reason = self._clip(self._sanitize_output(result.reason), 180)
        events = []
        if open_event:
            events.append(open_event)
        feedback, retryable, next_action = self._browser_failure_feedback(result)
        if result.step_results:
            feedback = "批次逐步结果：" + json.dumps(result.step_results, ensure_ascii=False) + "\n" + feedback
            feedback += "\n不要重放已执行的步骤；重新观察后只继续未执行的部分。"
        events.append(
            {
                "type": "browser_page_read",
                "provider": "managed_browser",
                "action": str(result.action or "current").strip() or "current",
                "status": status,
                "reason": reason or status,
                "retryable": retryable,
                "next_action": next_action,
                "action_state": result.action_state,
                "observation_state": result.observation_state,
            }
        )
        open_note = "已另外请求桌宠打开该公开网页给用户看；" if open_event else ""
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=(
                f"{open_note}{feedback}"
            ),
            state_updates={
                "browser_page_status": status,
                "browser_page_reason": reason or status,
                "browser_page_retryable": retryable,
                "browser_page_next_action": next_action,
                "browser_open_requested": bool(open_event),
            },
        )

    def _normalize_result(self, value: Any, *, fallback_action: str) -> BrowserPageResult:
        if isinstance(value, BrowserPageResult):
            return value
        if isinstance(value, Mapping):
            action = str(value.get("action") or fallback_action)
            return BrowserPageResult(
                ok=bool(value.get("ok")),
                status=str(value.get("status") or ("available" if value.get("ok") else "unavailable")),
                action=action,
                url=str(value.get("url") or ""),
                title=str(value.get("title") or ""),
                text=str(value.get("text") or ""),
                reason=str(value.get("reason") or ""),
                snapshot_id=str(value.get("snapshot_id") or ""),
                complete=bool(value.get("complete", True)),
                next_cursor=str(value.get("next_cursor") or ""),
                shown_chars=int(value.get("shown_chars") or 0),
                total_chars=int(value.get("total_chars") or 0),
                page_revision=str(value.get("page_revision") or ""),
                retryable=bool(value.get("retryable")),
                next_action=str(value.get("next_action") or ""),
                observation_id=str(value.get("observation_id") or ""),
                screenshot_id=str(value.get("screenshot_id") or ""),
                action_state=str(value.get("action_state") or ("executed" if value.get("ok") and action in self.CONTROL_ACTIONS else "not_started")),
                observation_state=str(value.get("observation_state") or "complete"),
                page_changed=str(value.get("page_changed") or "unknown"),
                visual_status=str(value.get("visual_status") or "not_requested"),
                model_image=value.get("model_image") if isinstance(value.get("model_image"), dict) else None,
                viewport=tuple(value.get("viewport") or (0, 0)),
                screenshot_dimensions=tuple(value.get("screenshot_dimensions") or (0, 0)),
                download_id=str(value.get("download_id") or ""),
                download_status=str(value.get("download_status") or ""),
                download_filename=str(value.get("download_filename") or ""),
                attachment_handle=str(value.get("attachment_handle") or ""),
                attachment_id=str(value.get("attachment_id") or ""),
                dialog_events=tuple(item for item in (value.get("dialog_events") or ()) if isinstance(item, Mapping)),
            )
        return BrowserPageResult(ok=False, status="unavailable", action=fallback_action, reason="invalid_runner_result")

    def _runner_for_context(self, context: ToolExecutionContext) -> Any:
        get_runner = getattr(self.browser_runner, "runner_for_session", None)
        if callable(get_runner):
            runner = get_runner(context.profile_user_id, context.session_id)
        else:
            runner = self.browser_runner
        if hasattr(runner, "set_session_identity"):
            runner.set_session_identity(context.profile_user_id, context.session_id)
        if hasattr(runner, "set_attachment_service") and self.attachment_service is not None:
            runner.set_attachment_service(self.attachment_service)
        return runner

    def _browser_failure_feedback(self, result: BrowserPageResult) -> tuple[str, bool, str]:
        status = str(result.status or "unavailable").strip() or "unavailable"
        reason = self._clip(self._sanitize_output(result.reason), 180)
        retryable = bool(result.retryable)
        next_action = str(result.next_action or "").strip()
        if result.action_state in {"executed", "unknown"} and result.action in self.CONTROL_ACTIONS | {"navigate", "scroll"}:
            next_action = "current"
            retryable = True
            state_note = "动作已执行，但后续处理失败" if result.action_state == "executed" else "动作是否生效尚不能确认"
            message = f"{state_note}。只重新观察当前页面核实结果，不要自动重复点击、输入或提交。"
        elif status in {"no_page", "browser_closed"}:
            retryable = True
            next_action = next_action or "navigate"
            message = (
                "当前会话没有可读取的浏览器页面，可能是窗口尚未打开或已被关闭；这不是网页内容或网络结论。"
                "如果任务里已有目标 URL，下一步调用 browser_page.navigate，宿主会创建新窗口继续。"
            )
        elif status in {"stale_target", "target_not_found"} or reason == "candidate_not_found":
            retryable = True
            next_action = next_action or "snapshot"
            message = "目标元素已变化或不存在。下一步重新调用 snapshot 或 elements 获取当前 ref/候选，再继续操作；不要复用旧 ref。"
        elif status in {"navigation_timeout", "site_unreachable"}:
            retryable = True
            next_action = next_action or "navigate"
            message = "页面导航没有完成。可以用同一 URL 再调用一次 navigate；若仍失败，再向用户说明站点或网络暂时不可达。"
        elif status == "invalid_request":
            retryable = False
            message = "浏览器调用参数无效。根据 reason 修正参数后重新调用；不要把这次失败描述成已执行。"
        elif status == "missing_executor" or reason == "playwright_not_installed":
            retryable = False
            message = (
                "浏览器能力暂时不可用：当前宿主没有浏览器执行器，无法在本轮自行恢复；"
                "请明确告诉用户该能力未安装，不要编造页面结果。"
            )
        else:
            message = "浏览器页面暂时不可用，没有产生可用页面证据。不要编造页面结果；可按 next_action 再试一次，仍失败再向用户说明。"
        detail = f"状态：{status}；原因：{reason or '未提供'}；可恢复：{'是' if retryable else '否'}"
        if next_action:
            detail += f"；建议下一动作：browser_page.{next_action}"
        return f"{detail}。{message}", retryable, next_action

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
        if clean_action == "screenshot":
            return (
                "下一步提示：截图像素会直接随工具结果供你观察；结合图像完成判断。"
                "只有用户要求接收截图文件时，才调用 send_file 发送返回的句柄。"
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
        return action if action in self.ALLOWED_ACTIONS else ""

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
        if not hostname or (self._is_private_or_local_host(hostname) and not self.allow_private_network_urls):
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

    def _normalize_fill_text(self, value: Any) -> str | None:
        # Empty text means clear. Content is data, not an authorization signal.
        if not isinstance(value, str) or len(value) > self.MAX_FILL_TEXT_CHARS or "\x00" in value:
            return None
        return value

    def argument_error(self, value: Mapping[str, Any]) -> str:
        """Explain rejected arguments without echoing submitted content."""
        if value.get("session_source") == "personal_chrome":
            from ..personal_browser_contract import ACTIONS
            if value.get("action") not in ACTIONS:
                return "个人 Chrome 后端尚未支持此动作；用 capabilities 查看当前后端可调用动作。"
            if any(k in value for k in OPTION_KEYS):
                return "个人 Chrome 当前不支持 frame/scope、上传、表单扩展或 dialog 策略参数；用 capabilities 查看差异。"
        issue = option_error(value)
        if issue:
            return issue
        if value.get("action") == "run_actions":
            return "run_actions 需要 1–6 个带 selector 的控件动作，必须使用同一 frame_selector；不接受嵌套批次、坐标或旧 ref。"
        if self._normalize_action(value) == "fill":
            raw = next((value[k] for k in ("text", "value", "query") if k in value), None)
            if not isinstance(raw, str):
                return "fill.text 必须是字符串；空字符串表示清空，不能省略。"
            if len(raw) > self.MAX_FILL_TEXT_CHARS:
                return f"fill.text 超过当前后端上限 {self.MAX_FILL_TEXT_CHARS} 字符；未执行，也未截断输入。"
            if "\x00" in raw:
                return "fill.text 不支持 NUL 字符；未执行。"
            if value.get("session_source") == "personal_chrome" and any(ch in raw for ch in "\r\n\t"):
                return "个人 Chrome 当前不支持 fill 输入换行或 Tab；未执行。"
        return ""

    def _resolve_upload_files(self, handles: list[str], context: ToolExecutionContext) -> tuple[list[str], str]:
        resolver = getattr(self.generated_file_service, "resolve_input_resource", None)
        if not callable(resolver):
            return [], "upload_resource_service_unavailable"
        paths = []
        for handle in handles:
            try:
                item = resolver(profile_user_id=context.profile_user_id, session_id=context.session_id,
                                target=handle, timestamp=context.now_ts)
                if not isinstance(item, dict) or str(item.get("handle", "")).casefold() != handle.casefold():
                    return [], "upload_exact_resource_handle_required"
                path = Path(item.get("absolute_path", ""))
                if not path.is_absolute() or not path.is_file():
                    return [], "upload_resource_file_unavailable"
                paths.append(str(path))
            except Exception:
                return [], "upload_resource_resolution_failed"
        return paths, ""

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
            profile_user_id=capcore_authorization_profile_user_id(context),
            family_id="ops",
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
        if call.get("coordinate"):
            preview["coordinate"] = list(call.get("coordinate"))
        if call.get("screenshot_id"):
            preview["screenshot_id"] = str(call.get("screenshot_id") or "")
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

    def _sanitize_output(self, value: str, *, strip: bool = True) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(?i)authorization:\s*bearer\s+[^\s]+", "Authorization: Bearer [redacted]", text)
        text = re.sub(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
        text = re.sub(r"(?i)([?&](?:api[_-]?key|password|secret|token)=)[^&#\s]+", r"\1[redacted]", text)
        return text.strip() if strip else text

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
            return {"offered": False, "enabled": False, "status": "disabled", "reason": "anysearch_disabled"}

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
                    "offered": True,
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
                    "offered": True,
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

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        runtime_profile_user_id = self._resolve_runtime_profile_user_id(context)
        cursor_payload: dict[str, Any] | None = None
        start_entry = 0
        start_offset = 0
        if str(call.get("cursor") or "").strip():
            resolved = self._resolve_cursor_call(call=call, context=context)
            if not isinstance(resolved, dict):
                return resolved
            cursor_payload = resolved
            cursor_args = dict(resolved.get("arguments") or {}) if isinstance(resolved.get("arguments"), Mapping) else {}
            call = {
                "type": self.tool_type,
                "action": str(resolved.get("action") or "search"),
                **{key: value for key, value in cursor_args.items() if value not in (None, "")},
            }
            start_entry = int(resolved.get("start_entry") or 0)
            start_offset = int(resolved.get("start_offset") or 0)
            expected_fingerprint = str(resolved.get("fingerprint") or "")
        else:
            expected_fingerprint = ""

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
        redaction_terms = self._redaction_terms_for_server(server) if server else []
        providers_attempted: list[str] = []
        fallback_used = False
        mcp_failure_reason = ""
        result: Any = None
        if use_mcp and server:
            providers_attempted.append("mcp")
            try:
                result = self._run_coro_blocking(self._call_mcp(server=server, tool_name=action, arguments=arguments))
                if self._mcp_result_is_error(result):
                    payload = self._extract_payload(result, redaction_terms=redaction_terms)
                    detail = self._payload_to_text(payload, redaction_terms=redaction_terms)
                    mcp_failure_reason = self._mcp_tool_error_reason(result=result, detail=detail)
            except McpStdioDiscoveryError as exc:
                mcp_failure_reason = self._safe_mcp_failure_reason(exc)
            except Exception:
                mcp_failure_reason = "mcp_call_failed"
            if mcp_failure_reason:
                self._remember_server_failure(runtime_profile_user_id, server, reason=mcp_failure_reason)
                if action not in {"search", "batch_search"}:
                    return self._mcp_tool_failure(
                        action=action,
                        result=result if isinstance(result, Mapping) else {"reason": mcp_failure_reason},
                        redaction_terms=redaction_terms,
                        profile_user_id=runtime_profile_user_id,
                    )
                fallback_used = True
                providers_attempted.append("rest")
                try:
                    result = self.anysearch_rest_client.call(action=action, arguments=arguments)
                except AnySearchRestError as rest_exc:
                    return self._failure(
                        rest_exc.reason,
                        f"AnySearch MCP（{mcp_failure_reason}）与官方 HTTPS 搜索均不可用。",
                    )
                except Exception:
                    return self._failure(
                        "anysearch_rest_failed",
                        f"AnySearch MCP（{mcp_failure_reason}）与官方 HTTPS 搜索均不可用。",
                    )
                use_mcp = False
                self._remember_server_rest_fallback(
                    runtime_profile_user_id,
                    server,
                    mcp_reason=mcp_failure_reason,
                )
        else:
            providers_attempted.append("rest")
            try:
                result = self.anysearch_rest_client.call(action=action, arguments=arguments)
            except AnySearchRestError as exc:
                return self._failure(exc.reason, "AnySearch 官方 HTTPS 搜索调用失败。")
            except Exception:
                return self._failure("anysearch_rest_failed", "AnySearch 官方 HTTPS 搜索调用失败。")
        if use_mcp and server:
            self._remember_server_ready(runtime_profile_user_id, server)

        owner_binding = self._web_cursor_owner_binding(context)
        query_label = str(call.get("query") or " / ".join(str(item) for item in call.get("queries") or [])).strip()
        coverage_status = "unverified" if self._search_needs_broader_coverage(query_label) else "not_required"
        if action in {"search", "batch_search"}:
            return self._paged_search_result(
                action=action,
                call=call,
                result=result if isinstance(result, dict) else {},
                redaction_terms=redaction_terms,
                start_entry=start_entry,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=runtime_profile_user_id,
                coverage_status=coverage_status,
                providers_attempted=providers_attempted,
                fallback_used=fallback_used,
            )
        if action == "extract":
            return self._paged_extract_result(
                call=call,
                result=result if isinstance(result, dict) else {},
                redaction_terms=redaction_terms,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=runtime_profile_user_id,
            )
        if action == "get_sub_domains":
            return self._paged_sub_domains_result(
                call=call,
                result=result if isinstance(result, dict) else {},
                redaction_terms=redaction_terms,
                start_offset=start_offset,
                expected_fingerprint=expected_fingerprint,
                owner_binding=owner_binding,
                profile_user_id=runtime_profile_user_id,
            )
        return self._failure("unsupported_action", f"AnySearch 不支持的动作：{action}。")

    def _resolve_cursor_call(
        self, *, call: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any] | ToolExecutionResult:
        from ..paged_reading import parse_json_payload, parse_paged_cursor

        cursor = str(call.get("cursor") or "").strip()
        payload = parse_json_payload(
            parse_paged_cursor(cursor, tool="we", binding=self._web_cursor_owner_binding(context))
        )
        if not isinstance(payload, dict):
            return self._cursor_failure("cursor_invalid", "cursor 不属于当前用户/会话，或已损坏")
        action = str(payload.get("a") or "").strip()
        if action not in self.ALLOWED_ACTIONS:
            return self._cursor_failure("cursor_invalid", "cursor 引用的动作无效")
        return {
            "action": action,
            "arguments": dict(payload.get("args") or {}),
            "start_entry": int(payload.get("s") or 0) if action in {"search", "batch_search"} else 0,
            "start_offset": int(payload.get("o") or 0),
            "fingerprint": str(payload.get("f") or ""),
        }

    def _cursor_failure(self, status: str, reason: str) -> ToolExecutionResult:
        from ..paged_reading import page_failure_feedback

        content = page_failure_feedback(status=status, tool=self.tool_type, detail=reason)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {"type": "web_search_completed", "provider": "anysearch", "status": status, "reason": reason}
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics={"status": status},
            ),
        )

    def _web_cursor_owner_binding(self, context: ToolExecutionContext) -> str:
        from ..paged_reading import cursor_binding

        return cursor_binding(
            "web_search",
            self._resolve_runtime_profile_user_id(context),
            context.session_id,
        )

    @staticmethod
    def _web_evidence_fingerprint(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

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
                source = str(urlparse(url).hostname or "")[:160]
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
        from ..paged_reading import (
            make_paged_cursor,
            json_payload,
            page_failure_feedback,
        )

        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        entries = self._search_evidence_entries(payload, redaction_terms=redaction_terms)
        fingerprint = self._web_evidence_fingerprint(json.dumps(entries, ensure_ascii=False, sort_keys=True))

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
        if not entries:
            raw_text = self._payload_to_text(payload, redaction_terms=redaction_terms)
            if self._search_payload_is_explicitly_empty(payload) or not raw_text or raw_text in {"[]", "{}", "null"}:
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
        if start_offset:
            return self._cursor_failure("cursor_invalid", "结构化搜索结果不能使用 raw 文档偏移")
        if expected_fingerprint and fingerprint != expected_fingerprint:
            content = page_failure_feedback(
                status="content_changed",
                tool=self.tool_type,
                detail="重新检索得到的结果与上一页不一致",
            )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {"type": "web_search_completed", "provider": "anysearch", "status": "content_changed"}
                ],
                followup_context=content,
                followup_envelope=ToolFollowupEnvelope(
                    content=content,
                    producer_bounded=True,
                    complete=True,
                    continuation=None,
                    diagnostics={"status": "content_changed"},
                ),
            )
        budget = 32_000
        shown = 0
        chars = sum(len(line) + 1 for line in lines)
        shown_count = 0
        for index in range(start_entry, len(entries)):
            block = self._render_search_entry(index + 1, entries[index])
            if shown > 0 and chars + len(block) + 1 > budget:
                break
            lines.append(block)
            chars += len(block) + 1
            shown += 1
            shown_count += 1
        complete = start_entry + shown_count >= len(entries)
        if not complete:
            cursor = make_paged_cursor(
                tool="we",
                binding=owner_binding,
                payload=json_payload(
                    {
                        "a": action,
                        "args": {str(key): value for key, value in dict(call).items() if key != "type" and value not in (None, "")},
                        "s": start_entry + shown_count,
                        "f": fingerprint,
                    }
                ),
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

    def _paged_raw_search_result(
        self,
        *,
        action: str,
        call: Mapping[str, Any],
        raw_text: str,
        header_lines: list[str],
        start_offset: int,
        expected_fingerprint: str,
        owner_binding: str,
        profile_user_id: str,
        coverage_status: str,
        providers_attempted: list[str],
        fallback_used: bool,
    ) -> ToolExecutionResult:
        from ..paged_reading import (
            FIRST_PAGE_BUDGET_CHARS,
            FIRST_PAGE_BUDGET_LINES,
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            json_payload,
            make_paged_cursor,
            slice_page,
        )

        fingerprint = self._web_evidence_fingerprint(raw_text)
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._cursor_failure("content_changed", "重新检索得到的原始文档与上一页不一致")
        budget_chars, budget_lines = (
            (FIRST_PAGE_BUDGET_CHARS, FIRST_PAGE_BUDGET_LINES)
            if not expected_fingerprint
            else (NEXT_PAGE_BUDGET_CHARS, NEXT_PAGE_BUDGET_LINES)
        )
        page_text, next_offset, total_chars = slice_page(
            raw_text,
            start=start_offset,
            budget_chars=budget_chars,
            budget_lines=budget_lines,
        )
        complete = next_offset >= total_chars
        lines = [*header_lines, "结果格式：markdown_raw（保留服务返回的原始公开文档）", "原始搜索文档：", page_text]
        continuation = None
        if not complete:
            cursor = make_paged_cursor(
                tool="we",
                binding=owner_binding,
                payload=json_payload(
                    {
                        "a": action,
                        "args": {str(key): value for key, value in dict(call).items() if key != "type" and value not in (None, "")},
                        "o": next_offset,
                        "f": fingerprint,
                    }
                ),
            )
            continuation = {"type": self.tool_type, "cursor": cursor}
            lines.append(
                f"原始文档已展示 {next_offset}/{total_chars} 字；够用即可回答，需要后续时调用 "
                f'web_search(cursor="{cursor}")。'
            )
        else:
            lines.append(f"已读完本次返回的原始搜索文档（共 {total_chars} 字）。")
        return self._web_page_result(
            content="\n".join(lines),
            complete=complete,
            continuation=continuation,
            diagnostics={
                "status": "ok",
                "action": action,
                "format": "markdown_raw",
                "shown_chars": len(page_text),
                "total_chars": total_chars,
                "complete": complete,
                "providers_attempted": list(providers_attempted),
                "fallback_used": bool(fallback_used),
            },
            profile_user_id=profile_user_id,
            coverage_status=coverage_status,
        )

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
        from ..paged_reading import (
            FIRST_PAGE_BUDGET_CHARS,
            FIRST_PAGE_BUDGET_LINES,
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            json_payload,
            make_paged_cursor,
            page_failure_feedback,
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
            content = page_failure_feedback(
                status="stale_cursor",
                tool=self.tool_type,
                detail="该网页重新提取后的内容与上一页不一致，可能已被更新",
            )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {"type": "web_search_completed", "provider": "anysearch", "status": "stale_cursor"}
                ],
                followup_context=content,
                followup_envelope=ToolFollowupEnvelope(
                    content=content,
                    producer_bounded=True,
                    complete=True,
                    continuation=None,
                    diagnostics={"status": "stale_cursor"},
                ),
            )

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
            cursor = make_paged_cursor(
                tool="we",
                binding=owner_binding,
                payload=json_payload(
                    {"a": "extract", "args": {"url": str(call.get("url") or "")}, "o": next_offset, "f": fingerprint}
                ),
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
        from ..paged_reading import (
            FIRST_PAGE_BUDGET_CHARS,
            json_payload,
            make_paged_cursor,
            page_failure_feedback,
            slice_page,
        )

        payload = self._extract_payload(result, redaction_terms=redaction_terms)
        text = self._sanitize_output(
            self._payload_to_text(payload, redaction_terms=redaction_terms), redaction_terms=redaction_terms
        )
        fingerprint = self._web_evidence_fingerprint(text)
        if expected_fingerprint and fingerprint != expected_fingerprint:
            content = page_failure_feedback(
                status="content_changed",
                tool=self.tool_type,
                detail="重新查询得到的结果与上一页不一致",
            )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {"type": "web_search_completed", "provider": "anysearch", "status": "content_changed"}
                ],
                followup_context=content,
                followup_envelope=ToolFollowupEnvelope(
                    content=content,
                    producer_bounded=True,
                    complete=True,
                    continuation=None,
                    diagnostics={"status": "content_changed"},
                ),
            )
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
            cursor = make_paged_cursor(
                tool="we",
                binding=owner_binding,
                payload=json_payload(
                    {
                        "a": "get_sub_domains",
                        "args": {"domains": list(call.get("domains") or [])},
                        "o": next_offset,
                        "f": fingerprint,
                    }
                ),
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

    @staticmethod
    def _web_page_result(
        *,
        content: str,
        complete: bool,
        continuation: Mapping[str, Any] | None,
        diagnostics: Mapping[str, Any],
        profile_user_id: str = "",
        coverage_status: str = "not_required",
        status: str = "ok",
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_type="web_search",
            stream_events=[
                {
                    "type": "web_search_completed",
                    "provider": "anysearch",
                    "status": str(status or "ok"),
                    "complete": bool(complete),
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=bool(complete),
                continuation=dict(continuation) if continuation else None,
                diagnostics=dict(diagnostics),
            ),
            state_updates={
                "web_search_status": str(status or "ok"),
                "web_search_provider": "anysearch",
                "web_search_profile_user_id": str(profile_user_id or ""),
                "web_search_coverage_status": str(coverage_status or ""),
                "web_search_complete": bool(complete),
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
        result["offered"] = True
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
