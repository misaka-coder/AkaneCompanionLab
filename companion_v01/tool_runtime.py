from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import inspect
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote_plus, urlparse

import config
from capcore import build_permission_request as capcore_build_permission_request
from capcore import build_tool_spec as capcore_build_tool_spec
from capcore import validate_invocation_args as capcore_validate_invocation_args

from .browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from .capcore_runtime import (
    approval_required_event as capcore_approval_required_event,
    invocation_context_from_execution as capcore_invocation_context_from_execution,
    manual_permission_request as capcore_manual_permission_request,
    resolve_permission_for_profile as capcore_resolve_permission_for_profile,
)
from .capability_adapters import CapabilityProtocolError, InvocationContext
from .capability_registry import (
    APPLY_STYLE_TO_EXISTING_FILE_TOOL_SPEC,
    BROWSER_PAGE_TOOL_SPEC,
    BROWSE_MEMORY_TOOL_SPEC,
    CALL_NPC_TOOL_SPEC,
    CANCEL_REMINDER_TOOL_SPEC,
    CHECK_INVENTORY_TOOL_SPEC,
    CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    COMPOSE_FILE_TOOL_SPEC,
    CONVERT_MEDIA_FILE_TOOL_SPEC,
    COVER_SONG_TOOL_SPEC,
    CLEAN_VOICE_TRACK_TOOL_SPEC,
    DELEGATE_TASK_TOOL_SPEC,
    FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    FOCUS_WORKSPACE_TOOL_SPEC,
    GENERATE_IMAGE_TOOL_SPEC,
    INSPECT_ATTACHMENT_TOOL_SPEC,
    INSPECT_GENERATED_FILE_TOOL_SPEC,
    INSPECT_MEDIA_INFO_TOOL_SPEC,
    LIST_REMINDERS_TOOL_SPEC,
    LIST_WORKSPACE_TOOL_SPEC,
    LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    LOAD_MATERIAL_TOOL_SPEC,
    MANAGE_ARTIFACT_TOOL_SPEC,
    MANAGE_GENERATED_FILE_TOOL_SPEC,
    MANAGE_GIFT_TOOL_SPEC,
    MANAGE_PERSONA_TOOL_SPEC,
    MANAGE_TASK_WORKSPACE_TOOL_SPEC,
    OPEN_BROWSER_TOOL_SPEC,
    OPEN_MUSIC_SEARCH_TOOL_SPEC,
    PREPARE_VOICE_DATASET_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
    RETRY_ATTACHMENT_TOOL_SPEC,
    REVISE_GENERATED_FILE_TOOL_SPEC,
    SEND_FILE_TOOL_SPEC,
    SEND_GENERATED_FILE_TOOL_SPEC,
    SEND_STICKER_TOOL_SPEC,
    SEPARATE_AUDIO_STEMS_TOOL_SPEC,
    SET_REMINDER_TOOL_SPEC,
    SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC,
    TRANSCRIBE_MEDIA_TOOL_SPEC,
    WEB_SEARCH_TOOL_SPEC,
)
from .local_capability_config import get_mcp_server_runtime_config
from .desktop_satellite_specs import desktop_satellite_spec
from .mcp_stdio_discoverer import McpStdioDiscoveryError, McpToolCaller
from .anysearch_rest_client import AnySearchRestClient, AnySearchRestError
from .npc_runtime import GenericNPCRuntime
from .store import MemoryStore
from .task_workspace import TaskWorkspaceService
from .text_utils import normalize_text, resolve_reminder_due_timestamp, timestamp_to_datetime_label
from .workspace_files import WorkspaceFileService



from .tool_handlers.core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    ToolMetadata,
    operation_tool_result,
    TOOL_METADATA_BY_TYPE,
    TOOL_SPEC_BY_TYPE,
)


from .tool_handlers.adapters import (
    AdapterCapabilityToolHandler,
    DesktopSatelliteToolHandler,
)
from .tool_handlers.memory import (
    BrowseMemoryToolHandler,
    OpenMemoryToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
)
from .tool_handlers.character_world import (
    CallNPCToolHandler,
    CancelReminderToolHandler,
    CheckInventoryToolHandler,
    ListRemindersToolHandler,
    LoadCharacterContextToolHandler,
    ManageArtifactToolHandler,
    ManageGiftToolHandler,
    ManagePersonaToolHandler,
    SetReminderToolHandler,
)

from .tool_handlers.attachments import (
    ClearAttachmentFocusToolHandler,
    FetchMediaFromUrlToolHandler,
    InspectAttachmentToolHandler,
    LoadMaterialToolHandler,
    ReadAttachmentSectionToolHandler,
    RetryAttachmentToolHandler,
    SyncAttachmentWorkspaceToolHandler,
)
from .tool_handlers.workspace import (
    FocusWorkspaceToolHandler,
    ListWorkspaceToolHandler,
    ManageTaskWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
)
class GenerateImageToolHandler(BaseToolHandler):
    tool_type = "generate_image"

    def __init__(self, *, image_generation_service) -> None:
        self.image_generation_service = image_generation_service

    def capability_status(self) -> dict[str, Any]:
        status_fn = getattr(self.image_generation_service, "capability_status", None)
        if not callable(status_fn):
            return {"enabled": False, "status": "unavailable", "reason": "image_generation_service_missing"}
        return dict(status_fn() or {})

    def tool_spec(self):  # M66-C
        return GENERATE_IMAGE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- generate_image：用户明确要文生图、图生图、改图、融合多张图片或继续修改生成图时使用。"
            '格式为 {"type":"generate_image","prompt":"完整生成/编辑要求",'
            '"reference_images":["img_001","gen_002"],"mask_image":"可选 img_003",'
            '"size":"auto|1024x1024|1536x1024|1024x1536","quality":"auto|low|medium|high",'
            '"background":"auto|opaque","output_format":"png|jpeg|webp","compression":90,'
            '"input_fidelity":"auto|low|high","n":1,"output_title":"标题","send_to_user":true}。'
            "没有 reference_images 时是文生图；有一到五张时是图生图/多图融合。"
            "提示词由你根据用户意图完整组织，但不能填写路径、URL、base64 或 API key。"
            "生成结果会成为 gen_001 一类当前会话生成文件，并可继续作为参考图。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        prompt = str(value.get("prompt") or value.get("instruction") or value.get("description") or "").strip()[:4000]
        if not prompt:
            return None
        raw_references = value.get("reference_images")
        if raw_references is None:
            raw_references = value.get("image_ids") or value.get("images") or value.get("references")
        references = self._normalize_targets(raw_references, limit=5)
        size = str(value.get("size") or "auto").strip().lower().replace("×", "x") or "auto"
        quality = str(value.get("quality") or "auto").strip().lower()
        background = str(value.get("background") or "auto").strip().lower()
        output_format = str(value.get("output_format") or value.get("format") or "png").strip().lower().lstrip(".")
        if output_format == "jpg":
            output_format = "jpeg"
        input_fidelity = str(value.get("input_fidelity") or "auto").strip().lower()
        return {
            "type": self.tool_type,
            "prompt": prompt,
            "reference_images": references,
            "mask_image": str(value.get("mask_image") or value.get("mask") or "").strip()[:120],
            "size": size,
            "quality": quality if quality in {"auto", "low", "medium", "high"} else "auto",
            "background": background if background in {"auto", "opaque"} else "auto",
            "output_format": output_format if output_format in {"png", "jpeg", "webp"} else "png",
            "compression": self._coerce_int(value.get("compression"), minimum=0, maximum=100, default=90),
            "input_fidelity": input_fidelity if input_fidelity in {"auto", "low", "high"} else "auto",
            "n": self._coerce_int(value.get("n"), minimum=1, maximum=4, default=1),
            "output_title": str(value.get("output_title") or value.get("title") or "生成图片").strip()[:80]
            or "生成图片",
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=True),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if self.image_generation_service is None:
            return self._failure("image_generation_service_unavailable")
        result = self.image_generation_service.generate(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            prompt=str(call.get("prompt") or ""),
            reference_targets=list(call.get("reference_images") or []),
            mask_target=str(call.get("mask_image") or ""),
            size=str(call.get("size") or "auto"),
            quality=str(call.get("quality") or "auto"),
            background=str(call.get("background") or "auto"),
            output_format=str(call.get("output_format") or "png"),
            compression=int(call.get("compression") or 90),
            input_fidelity=str(call.get("input_fidelity") or "auto"),
            n=int(call.get("n") or 1),
            output_title=str(call.get("output_title") or "生成图片"),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated_items = [item for item in list(result.get("generated") or []) if isinstance(item, dict)]
        if not bool(result.get("ok")) or not generated_items:
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {
                        "type": "image_generation_failed",
                        "status": "failed",
                        "reason": str(result.get("reason") or "image_generation_failed")[:120],
                        "retryable": bool(result.get("retryable")),
                    }
                ],
                followup_context=str(result.get("followup_context") or ""),
            )

        events = [
            {
                "type": "generated_file_ready",
                "generated_file": item,
                "send_to_user": bool(result.get("send_to_user")),
            }
            for item in generated_items
        ]
        events.append(
            {
                "type": "image_generation_completed",
                "status": "ready",
                "handles": list(result.get("handles") or []),
                "image_count": len(generated_items),
                "reference_handles": list(result.get("reference_handles") or []),
            }
        )
        preview_result = self.image_generation_service.image_material_resolver.build_model_image_inputs(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            targets=list(result.get("handles") or []),
            max_count=4,
            max_bytes_per_image=int(getattr(config, "VISION_MAX_IMAGE_BYTES", 8 * 1024 * 1024) or 0),
            max_total_bytes=20 * 1024 * 1024,
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or ""),
            state_updates={"generated_image_handles": list(result.get("handles") or [])},
            model_image_inputs=[
                dict(item) for item in list(preview_result.get("images") or []) if isinstance(item, dict)
            ],
        )

    @staticmethod
    def _normalize_targets(value: Any, *, limit: int) -> list[str]:
        if isinstance(value, str):
            raw = [part.strip() for part in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw = [str(item or "").strip() for item in value]
        else:
            raw = []
        return list(dict.fromkeys(item[:120] for item in raw if item))[: max(1, int(limit))]

    @staticmethod
    def _coerce_int(value: Any, *, minimum: int, maximum: int, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = int(default)
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启"}

    def _failure(self, reason: str) -> ToolExecutionResult:
        safe_reason = str(reason or "image_generation_failed")[:120]
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{"type": "image_generation_failed", "status": "unavailable", "reason": safe_reason}],
            followup_context=(
                f"<tool_use_error>图片生成能力当前不可用：{safe_reason}。"
                "请自然告诉用户这次没有生成图片，不要编造结果。</tool_use_error>"
            ),
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


class ComposeFileToolHandler(BaseToolHandler):
    tool_type = "compose_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- compose_file：当用户要你把工作台材料、已生成文件或当前对话内容整理成一个新文件时使用。"
            "如果用户明确要求生成/导出文件，或在已有任务后说“开始/继续/直接做”，不要只口头答应，"
            "应立刻在 tool_call 调用 compose_file。"
            '格式为 {"type":"compose_file","source_ids":["file_001","gen_001"],'
            '"task":"要整理/改写/导出的目标","output_format":"md|txt|docx|xlsx|pdf|json|csv|html",'
            '"output_title":"文件标题","structure":"summary|table|report|notes|custom",'
            '"style":"clean|formal|casual","content_markdown":"你整理好的正文或 Markdown",'
            '"table_rows":[["列1","列2"],["内容1","内容2"]],'
            '"formatting":{"header":{"bold":true},"columns":[{"match_header":"姓名","font_color":"red"}],'
            '"highlights":[{"text":"重点","fill_color":"yellow"}]},"send_to_user":false}。'
            "这个工具只负责把你已经整理好的内容渲染成文件；如果需要提取重点、改写或排版，"
            "请把最终内容写进 content_markdown 或 table_rows，不要只写一句任务就指望工具替你思考。"
            "但如果用户只是要求忠实转换/导出原始附件（例如 TXT 转 PDF/Word、原文导出），"
            "不要把提示词里的短预览复制进 content_markdown；请留空 content_markdown/table_rows，"
            "只填写 source_ids、task、output_format、output_title，后端会从原始附件读取更完整的安全材料。"
            "要生成表格优先用 table_rows；要生成 Word/PDF/Markdown 优先用 content_markdown。"
            "需要标红、加粗、黄色高亮时，把明确规则写进 formatting；后端只执行白名单样式字段。"
            "生成结果会成为 gen_001 这类可继续修改的生成文件，不会覆盖用户原始附件。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        sources = (
            value.get("source_ids")
            if value.get("source_ids") is not None
            else value.get("sources")
            if value.get("sources") is not None
            else value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
        )
        output_format = self._normalize_output_format(value.get("output_format") or value.get("format") or "md")
        table_rows = self._normalize_table_rows(value.get("table_rows") or value.get("rows") or value.get("table"))
        return {
            "type": self.tool_type,
            "source_ids": self._normalize_sources(sources),
            "task": str(value.get("task") or value.get("instruction") or value.get("goal") or "").strip()[:500],
            "output_format": output_format,
            "output_title": str(value.get("output_title") or value.get("title") or value.get("name") or "").strip()[
                :80
            ],
            "structure": str(value.get("structure") or value.get("layout") or "").strip()[:80],
            "style": str(value.get("style") or "").strip()[:80],
            "fidelity": str(value.get("fidelity") or "").strip()[:80],
            "content_markdown": str(
                value.get("content_markdown")
                or value.get("markdown")
                or value.get("content")
                or value.get("body")
                or ""
            ).strip()[:80000],
            "table_rows": table_rows,
            "formatting": self._normalize_formatting(
                value.get("formatting") or value.get("styles") or value.get("style_rules")
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.compose_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_targets=list(call.get("source_ids") or []),
            task=str(call.get("task") or ""),
            output_format=str(call.get("output_format") or "md"),
            output_title=str(call.get("output_title") or ""),
            structure=str(call.get("structure") or ""),
            style=str(call.get("style") or ""),
            fidelity=str(call.get("fidelity") or ""),
            content_markdown=str(call.get("content_markdown") or ""),
            table_rows=list(call.get("table_rows") or []),
            formatting=call.get("formatting") if isinstance(call.get("formatting"), dict) else {},
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_sources(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        sources: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in sources:
                sources.append(text[:120])
        return sources[:20]

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "md").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        return aliases.get(text, text)[:16]

    def _normalize_table_rows(self, value: Any) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        rows: list[list[str]] = []
        for row in value[:1000]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [str(cell or "").strip()[:500] for cell in list(row)[:50]]
            if any(cells):
                rows.append(cells)
        return rows

    def _normalize_formatting(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed_top = {"header", "columns", "rows", "cells", "highlights", "paragraphs", "row_rules", "auto_width"}
        allowed_style = {
            "bold",
            "italic",
            "font_color",
            "fill_color",
            "highlight_color",
            "match_header",
            "header",
            "column",
            "letter",
            "index",
            "row",
            "row_index",
            "start",
            "end",
            "from",
            "to",
            "text",
            "contains",
            "paragraph_index",
            "where",
        }
        normalized: dict[str, Any] = {}
        for key, raw in value.items():
            if key not in allowed_top:
                continue
            if key == "auto_width":
                normalized[key] = self._coerce_bool(raw, default=True)
                continue
            if key == "header" and isinstance(raw, dict):
                normalized[key] = {str(k): v for k, v in raw.items() if str(k) in allowed_style}
                continue
            if not isinstance(raw, list):
                continue
            items = []
            for item in raw[:120]:
                if not isinstance(item, dict):
                    continue
                items.append({str(k): v for k, v in item.items() if str(k) in allowed_style})
            if items:
                normalized[key] = items
        return normalized

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


def _generated_media_capability_status(
    service: Any,
    *,
    getter_name: str,
    missing_reason: str,
) -> dict[str, Any]:
    getter = getattr(service, getter_name, None)
    if not callable(getter):
        return {
            "enabled": False,
            "status": "missing_executor",
            "reason": missing_reason,
        }
    try:
        status = getter()
    except Exception:
        return {
            "enabled": False,
            "status": "unavailable",
            "reason": f"{getter_name}_probe_failed",
        }
    if not isinstance(status, dict):
        return {
            "enabled": False,
            "status": "unavailable",
            "reason": f"{getter_name}_invalid",
        }
    return dict(status)


class ConvertMediaFileToolHandler(BaseToolHandler):
    tool_type = "convert_media_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        return _generated_media_capability_status(
            self.generated_file_service,
            getter_name="media_conversion_status",
            missing_reason="media_conversion_status_missing",
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- convert_media_file：当用户要把普通音频转成常见格式，或从普通视频文件里提取音频时使用。"
            '格式为 {"type":"convert_media_file","source_id":"file_001|audio_001|gen_001",'
            '"output_format":"mp3|wav|flac|m4a|aac|ogg|opus","output_title":"输出文件名",'
            '"start_time":"00:00:35","end_time":"00:01:20","normalize_volume":true,'
            '"volume_gain_db":6,"trim_silence":true,"fade_in_seconds":2,"fade_out_seconds":3,"speed_ratio":1.25,'
            '"bitrate":"192k","sample_rate":44100,"channels":2,"send_to_user":false}。'
            "它适合普通非加密音频转码、压缩体积、截取片段、音量标准化、整体音量增减、自动去掉头尾静音、淡入淡出、调速、从 mp4/mov/mkv/webm 等视频提取音轨；不要用于 kgm/ncm/qmc 等平台加密或专有缓存格式的解密。"
            "start_time、end_time、normalize_volume、volume_gain_db、trim_silence、fade_in_seconds、fade_out_seconds、speed_ratio、bitrate、sample_rate、channels 都是可选项：用户没指定时不要硬填。"
            "如果只是转 mp3，通常只填 source_id、output_format、output_title 即可；如果是语音识别/统一语音规格，可考虑 wav、sample_rate=16000、channels=1；音乐文件通常保留原采样率和声道更自然。"
            "视频任务里只有用户要音频轨、后续人声处理、训练素材或统一媒体规格时才提音频；如果用户只要原视频，改用 send_file 发送原文件。"
            "如果用户说“声音忽大忽小/调正常/更舒服”，优先用 normalize_volume；如果用户说“太小声/放大一点”，用正数 volume_gain_db（如 3 或 6）；如果用户说“太吵/压低一点”，用负数 volume_gain_db（如 -3 或 -6）。"
            "如果用户说“把前后空白切掉/去掉开头结尾静音”，可填 trim_silence=true；如果用户说“截一段/加淡入淡出/放慢或加速”，再填写对应字段。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id") or value.get("source") or value.get("target") or value.get("attachment_id") or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "mp3"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "bitrate": str(value.get("bitrate") or value.get("audio_bitrate") or "").strip()[:20],
            "sample_rate": self._coerce_int(value.get("sample_rate") or value.get("ar") or 0),
            "channels": self._coerce_int(value.get("channels") or value.get("channel") or value.get("ac") or 0),
            "start_time": str(value.get("start_time") or value.get("start") or value.get("ss") or "").strip()[:40],
            "end_time": str(value.get("end_time") or value.get("end") or value.get("to") or "").strip()[:40],
            "normalize_volume": self._coerce_bool(
                value.get("normalize_volume") or value.get("loudnorm") or value.get("normalize_audio"),
                default=False,
            ),
            "volume_gain_db": self._coerce_float(
                value.get("volume_gain_db") or value.get("gain_db") or value.get("volume_db") or 0
            ),
            "trim_silence": self._coerce_bool(
                value.get("trim_silence")
                or value.get("remove_silence")
                or value.get("trim_silence_edges")
                or value.get("strip_silence"),
                default=False,
            ),
            "fade_in_seconds": value.get("fade_in_seconds") or value.get("fade_in") or 0,
            "fade_out_seconds": value.get("fade_out_seconds") or value.get("fade_out") or 0,
            "speed_ratio": self._coerce_float(
                value.get("speed_ratio") or value.get("speed") or value.get("atempo") or 0
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.convert_media_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            output_format=str(call.get("output_format") or "mp3"),
            output_title=str(call.get("output_title") or ""),
            bitrate=str(call.get("bitrate") or ""),
            sample_rate=int(call.get("sample_rate") or 0),
            channels=int(call.get("channels") or 0),
            start_time=str(call.get("start_time") or ""),
            end_time=str(call.get("end_time") or ""),
            normalize_volume=bool(call.get("normalize_volume")),
            volume_gain_db=call.get("volume_gain_db") or 0,
            trim_silence=bool(call.get("trim_silence")),
            fade_in_seconds=call.get("fade_in_seconds") or 0,
            fade_out_seconds=call.get("fade_out_seconds") or 0,
            speed_ratio=call.get("speed_ratio") or 0,
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        aliases = {
            "wave": "wav",
            "waveform": "wav",
            "mpeg3": "mp3",
            "mp4a": "m4a",
            "oga": "ogg",
        }
        return aliases.get(text, text)

    def _coerce_int(self, value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _coerce_float(self, value: Any) -> float:
        try:
            text = str(value or "").strip().lower()
            if not text:
                return 0.0
            text = text.replace("倍速", "").replace("倍", "").replace("分贝", "db").replace("x", "").strip()
            if text.endswith("db"):
                text = text[:-2].strip()
            if text.endswith("%"):
                return float(text[:-1]) / 100.0
            return float(text)
        except Exception:
            return 0.0

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class SeparateAudioStemsToolHandler(BaseToolHandler):
    tool_type = "separate_audio_stems"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        getter = getattr(self.generated_file_service, "audio_separation_status", None)
        if not callable(getter):
            return {
                "enabled": False,
                "status": "missing_executor",
                "reason": "audio_separation_executor_unavailable",
            }
        return dict(getter() or {})

    def build_prompt_instruction(self) -> str:
        return (
            "- separate_audio_stems：当用户想把一首歌、录音或带音轨视频拆成人声和伴奏两轨时使用。"
            '格式为 {"type":"separate_audio_stems","source_id":"file_001|audio_001|gen_001",'
            '"mode":"vocals_instrumental","output_format":"wav|flac|mp3",'
            '"output_title":"输出标题","send_to_user":false}。'
            "当前只支持 vocals_instrumental，也就是分离出人声（vocals）和伴奏（instrumental）两份结果。"
            "用户没有指定格式时默认用 mp3，适合聊天交付；只有明确要无损或后续处理需要时才选 wav/flac。"
            "这个工具负责拆轨，不负责后续精修；如果还要转码、裁剪、统一采样率、去头尾静音或调音量，请对分离后的结果再调用 convert_media_file。"
            "如果来源是普通视频文件，系统会先尝试抽取音轨再分离。不要用于 kgm/ncm/qmc 等平台加密或专有缓存格式的解密。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id") or value.get("source") or value.get("target") or value.get("attachment_id") or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "mode": self._normalize_mode(value.get("mode") or value.get("separation_mode") or "vocals_instrumental"),
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "mp3"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.separate_audio_stems(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            mode=str(call.get("mode") or "vocals_instrumental"),
            output_format=str(call.get("output_format") or "mp3"),
            output_title=str(call.get("output_title") or ""),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated_files = result.get("generated_files") if isinstance(result, dict) else None
        events: list[dict[str, Any]] = []
        if isinstance(generated_files, list):
            for generated in generated_files:
                if not isinstance(generated, dict):
                    continue
                events.append(
                    {
                        "type": "generated_file_ready",
                        "generated_file": generated,
                        "send_to_user": bool(result.get("send_to_user")),
                    }
                )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_mode(self, value: Any) -> str:
        text = str(value or "vocals_instrumental").strip().lower()
        aliases = {
            "vocals": "vocals_instrumental",
            "vocals+instrumental": "vocals_instrumental",
            "vocals_instrumental": "vocals_instrumental",
            "voice_music": "vocals_instrumental",
            "voice_and_music": "vocals_instrumental",
            "人声伴奏": "vocals_instrumental",
            "人声_伴奏": "vocals_instrumental",
        }
        return aliases.get(text, "vocals_instrumental")

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        aliases = {
            "wave": "wav",
            "waveform": "wav",
            "mpeg3": "mp3",
        }
        return aliases.get(text, text)

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class CoverSongToolHandler(BaseToolHandler):
    tool_type = "cover_song"

    def __init__(self, *, cover_song_service) -> None:
        self.cover_song_service = cover_song_service

    def capability_status(self) -> dict[str, Any]:
        status = getattr(self.cover_song_service, "capability_status", None)
        if not callable(status):
            return {"enabled": False, "status": "unavailable", "reason": "cover_song_service_missing"}
        return dict(status() or {})

    def build_prompt_instruction(self) -> str:
        return (
            "- cover_song：当用户要 Akane 用固定角色音色翻唱当前音频/视频，或再次点播已经完成的翻唱缓存时使用。"
            '格式为 {"type":"cover_song","source_id":"audio_001|file_001|gen_001（已有缓存时可省略）",'
            '"song_title":"歌曲名","artist":"可选原唱","voice_model":"auto|模型名","pitch_shift":0,'
            '"index_rate":0.6,"filter_radius":3,"rms_mix_rate":0.25,"protect":0.33,'
            '"vocal_gain_db":0,"instrumental_gain_db":-1,"output_format":"mp3|flac|wav",'
            '"delivery":"auto|voice|file|both|none","force_rebuild":false}。'
            "它会自动做人声/伴奏分离、RVC 音色转换和重新混音；不要先手工调用 separate_audio_stems，除非用户只想要分轨。"
            "没有明确音域证据时 pitch_shift 保持 0，不要只根据男女声标签强制升降八度。"
            "delivery=auto 在 QQ 中会优先作为语音发送，其他客户端保留普通生成文件交付；完整高质量结果始终进入生成区。"
            "如果没有 source_id，只有在用户明确点播此前已翻唱歌曲时才用 song_title 查缓存；缓存不存在时应告诉用户需要歌曲材料。"
            "整首歌即使耗时较长也直接调用本工具；拿到生成结果句柄后，再按用户要求调用 send_file 交付。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id") or value.get("source") or value.get("target") or value.get("attachment_id") or ""
        ).strip()
        song_title = str(value.get("song_title") or value.get("title") or "").strip()
        if not source_id and not song_title:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "song_title": song_title[:120],
            "artist": str(value.get("artist") or value.get("singer") or "").strip()[:80],
            "voice_model": str(value.get("voice_model") or value.get("model") or "auto").strip()[:120] or "auto",
            "pitch_shift": self._coerce_int(value.get("pitch_shift") or value.get("transpose"), -24, 24, 0),
            "index_rate": self._coerce_float(value.get("index_rate"), 0.0, 1.0, 0.6),
            "filter_radius": self._coerce_int(value.get("filter_radius"), 0, 7, 3),
            "rms_mix_rate": self._coerce_float(value.get("rms_mix_rate"), 0.0, 1.0, 0.25),
            "protect": self._coerce_float(value.get("protect"), 0.0, 0.5, 0.33),
            "vocal_gain_db": self._coerce_float(value.get("vocal_gain_db"), -12.0, 12.0, 0.0),
            "instrumental_gain_db": self._coerce_float(value.get("instrumental_gain_db"), -12.0, 6.0, -1.0),
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "mp3"),
            "delivery": self._normalize_delivery(value.get("delivery") or value.get("send_as") or "auto"),
            "force_rebuild": self._coerce_bool(value.get("force_rebuild"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        delivery = str(call.get("delivery") or "auto")
        if delivery == "auto":
            delivery = "voice" if str(context.client_mode or "").strip().lower() == "qq_text" else "file"
        result = self.cover_song_service.cover_song(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            song_title=str(call.get("song_title") or ""),
            artist=str(call.get("artist") or ""),
            voice_model=str(call.get("voice_model") or "auto"),
            pitch_shift=int(call.get("pitch_shift") or 0),
            index_rate=float(call.get("index_rate") if call.get("index_rate") is not None else 0.6),
            filter_radius=int(call.get("filter_radius") if call.get("filter_radius") is not None else 3),
            rms_mix_rate=float(call.get("rms_mix_rate") if call.get("rms_mix_rate") is not None else 0.25),
            protect=float(call.get("protect") if call.get("protect") is not None else 0.33),
            vocal_gain_db=float(call.get("vocal_gain_db") or 0.0),
            instrumental_gain_db=float(
                call.get("instrumental_gain_db") if call.get("instrumental_gain_db") is not None else -1.0
            ),
            output_format=str(call.get("output_format") or "mp3"),
            delivery=delivery,
            force_rebuild=bool(call.get("force_rebuild")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events: list[dict[str, Any]] = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                    "delivery_mode": str(result.get("delivery_mode") or delivery),
                    "delivery_scope": "cover_song",
                    "client_mode": str(context.client_mode or ""),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        text = {"wave": "wav", "mpeg3": "mp3"}.get(text, text)
        return text if text in {"mp3", "flac", "wav"} else "mp3"

    def _normalize_delivery(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        text = {"qq_voice": "voice", "audio": "voice", "不发送": "none"}.get(text, text)
        return text if text in {"auto", "voice", "file", "both", "none"} else "auto"

    def _coerce_int(self, value: Any, lower: int, upper: int, default: int) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = default
        return max(lower, min(upper, parsed))

    def _coerce_float(self, value: Any, lower: float, upper: float, default: float) -> float:
        try:
            parsed = float(str(value).strip())
        except Exception:
            parsed = default
        return max(lower, min(upper, parsed))

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "重新生成", "强制重做"}:
            return True
        if text in {"0", "false", "no", "n", "off", "使用缓存"}:
            return False
        return default


class CleanVoiceTrackToolHandler(BaseToolHandler):
    tool_type = "clean_voice_track"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        return _generated_media_capability_status(
            self.generated_file_service,
            getter_name="voice_cleaning_status",
            missing_reason="voice_cleaning_status_missing",
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- clean_voice_track：当用户想把语音/人声再净化一下时使用，比如降噪、去混响、去回声、让说话更干净。"
            '格式为 {"type":"clean_voice_track","source_id":"file_001|audio_001|gen_001",'
            '"mode":"denoise|dereverb|deecho|voice_focus","quality":"auto|ai|basic",'
            '"output_format":"wav|flac|mp3","output_title":"输出标题","post_filter":false,"send_to_user":false}。'
            "它适合说话录音、直播片段、播客人声、分离后的人声轨；如果只是普通转码、裁剪、统一采样率、去头尾静音或调音量，请继续用 convert_media_file。"
            "quality=auto 会优先尝试本地 AI 语音净化模型（当前设计对接 DeepFilterNet），没装环境时再退回基础净化；quality=basic 表示直接走 ffmpeg 轻净化；quality=ai 表示只接受 AI 净化。"
            "mode 主要是意图提示：denoise 更偏降噪，dereverb/deecho 更偏混响与回声整理，voice_focus 更偏让人声主体更靠前。"
            "post_filter 只在 AI 净化时有意义，适合杂音更重的情况；用户没提时不要硬填。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id") or value.get("source") or value.get("target") or value.get("attachment_id") or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
            "mode": self._normalize_mode(value.get("mode") or value.get("clean_mode") or "denoise"),
            "quality": self._normalize_quality(value.get("quality") or value.get("backend") or "auto"),
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "wav"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "post_filter": self._coerce_bool(value.get("post_filter") or value.get("pf"), default=False),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.clean_voice_track(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            mode=str(call.get("mode") or "denoise"),
            quality=str(call.get("quality") or "auto"),
            output_format=str(call.get("output_format") or "wav"),
            output_title=str(call.get("output_title") or ""),
            post_filter=bool(call.get("post_filter")),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_mode(self, value: Any) -> str:
        text = str(value or "denoise").strip().lower()
        aliases = {
            "denoise": "denoise",
            "noise": "denoise",
            "remove_noise": "denoise",
            "降噪": "denoise",
            "去噪": "denoise",
            "dereverb": "dereverb",
            "reverb": "dereverb",
            "去混响": "dereverb",
            "deecho": "deecho",
            "echo": "deecho",
            "去回声": "deecho",
            "voice_focus": "voice_focus",
            "speech": "voice_focus",
            "focus": "voice_focus",
            "人声聚焦": "voice_focus",
            "净化人声": "voice_focus",
        }
        return aliases.get(text, "denoise")

    def _normalize_quality(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        aliases = {
            "auto": "auto",
            "默认": "auto",
            "ai": "ai",
            "model": "ai",
            "deepfilternet": "ai",
            "basic": "basic",
            "ffmpeg": "basic",
            "基础": "basic",
        }
        return aliases.get(text, "auto")

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "wav").strip().lower().lstrip(".")
        aliases = {
            "wave": "wav",
            "waveform": "wav",
            "mpeg3": "mp3",
        }
        return aliases.get(text, text)

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class TranscribeMediaToolHandler(BaseToolHandler):
    tool_type = "transcribe_media"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        return _generated_media_capability_status(
            self.generated_file_service,
            getter_name="asr_status",
            missing_reason="asr_status_missing",
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- transcribe_media：当用户要给音频/视频配文字稿、生成字幕、把录音转文字，或想总结视频/音频内容前先拿到转写稿时使用。"
            '格式为 {"type":"transcribe_media","source_ids":["audio_001","file_002","gen_003"],'
            '"output_format":"md|txt|srt|vtt|json","output_title":"转写稿标题","language":"zh|en|auto",'
            '"with_timestamps":true,"merge_outputs":true,"model_size":"auto|small|medium|large-v3",'
            '"vad_filter":true,"send_to_user":false}。'
            "V1 支持批量来源：merge_outputs=true 会生成一份合并转写稿；merge_outputs=false 会每个来源各生成一份。"
            "如果用户要字幕文件，优先用 srt 或 vtt；如果要后续总结、会议纪要、内容梳理，优先用 md 并保留时间戳。"
            "音频较吵、歌曲伴奏很重或人声不清时，可先调用 separate_audio_stems / clean_voice_track，再对生成的人声结果调用 transcribe_media。"
            "用户没有明确指定模型大小时保持 model_size=auto，沿用当前执行器的质量配置。"
            "这个工具负责转写，不负责总结；转写完成后如果用户要总结内容，再基于生成的转写稿继续用 compose_file。"
            "如果用户只要原视频/原音频，不要为了回复而转写；直接发送原文件即可。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_ids = self._normalize_source_ids(
            value.get("source_ids")
            or value.get("sources")
            or value.get("targets")
            or value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
        )
        if not source_ids:
            return None
        return {
            "type": self.tool_type,
            "source_ids": source_ids,
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or "md"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "language": self._normalize_language(value.get("language") or value.get("lang") or "zh"),
            "with_timestamps": self._coerce_bool(
                value.get("with_timestamps") if "with_timestamps" in value else value.get("timestamps"),
                default=True,
            ),
            "merge_outputs": self._coerce_bool(
                value.get("merge_outputs") if "merge_outputs" in value else value.get("merge"),
                default=True,
            ),
            "model_size": self._normalize_model_size(value.get("model_size") or value.get("model") or "auto"),
            "device": self._normalize_device(value.get("device") or "auto"),
            "compute_type": self._normalize_compute_type(value.get("compute_type") or "auto"),
            "vad_filter": self._coerce_bool(
                value.get("vad_filter") if "vad_filter" in value else value.get("vad"),
                default=True,
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.transcribe_media(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_targets=list(call.get("source_ids") or []),
            output_format=str(call.get("output_format") or "md"),
            output_title=str(call.get("output_title") or ""),
            language=str(call.get("language") or "zh"),
            with_timestamps=bool(call.get("with_timestamps", True)),
            merge_outputs=bool(call.get("merge_outputs", True)),
            model_size=str(call.get("model_size") or "auto"),
            device=str(call.get("device") or "auto"),
            compute_type=str(call.get("compute_type") or "auto"),
            vad_filter=bool(call.get("vad_filter", True)),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated_files = result.get("generated_files") if isinstance(result, dict) else None
        events: list[dict[str, Any]] = []
        if isinstance(generated_files, list):
            for generated in generated_files:
                if isinstance(generated, dict):
                    events.append(
                        {
                            "type": "generated_file_ready",
                            "generated_file": generated,
                            "send_to_user": bool(result.get("send_to_user")),
                        }
                    )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_source_ids(self, value: Any) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text[:120])
        return normalized[:20]

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "md").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "subtitle": "srt",
            "subtitles": "srt",
            "caption": "srt",
            "captions": "srt",
            "webvtt": "vtt",
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in {"md", "txt", "srt", "vtt", "json"} else "md"

    def _normalize_language(self, value: Any) -> str:
        text = str(value or "zh").strip().lower()
        aliases = {
            "中文": "zh",
            "普通话": "zh",
            "国语": "zh",
            "英文": "en",
            "自动": "auto",
            "detect": "auto",
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in {"zh", "en", "ja", "ko", "auto"} else "zh"

    def _normalize_model_size(self, value: Any) -> str:
        text = str(value or "auto").strip().lower().replace("_", "-")
        aliases = {
            "auto": "auto",
            "default": "auto",
            "tiny": "tiny",
            "base": "base",
            "small": "small",
            "medium": "medium",
            "large": "large-v3",
            "large-v3": "large-v3",
            "large-v2": "large-v2",
        }
        return aliases.get(text, "auto")

    def _normalize_device(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in {"auto", "cuda", "cpu"} else "auto"

    def _normalize_compute_type(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        return text if text in {"auto", "float16", "float32", "int8", "int8_float16"} else "auto"

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户", "是", "需要", "合并"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成", "否", "不需要", "分开"}:
            return False
        return default


class PrepareVoiceDatasetToolHandler(BaseToolHandler):
    tool_type = "prepare_voice_dataset"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        return _generated_media_capability_status(
            self.generated_file_service,
            getter_name="voice_dataset_status",
            missing_reason="voice_dataset_status_missing",
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- prepare_voice_dataset：当用户要把一段或多段人声/语音整理成 GPT-SoVITS、RVC 等训练素材时使用。"
            '格式为 {"type":"prepare_voice_dataset","source_ids":["gen_001","audio_001"],'
            '"profile":"gpt_sovits|rvc|archive","output_title":"训练集名称",'
            '"target_sr":44100,"min_clip_seconds":3,"max_clip_seconds":12,'
            '"silence_threshold_db":-40,"min_silence_ms":300,"max_silence_kept_ms":300,'
            '"clean_first":false,"normalize_volume":false,"send_to_user":false}。'
            "这个工具会把多个来源统一成训练用 wav、按停顿切片、生成 manifest.json 和 zip 批次；摘要会列出过短、过长、音量偏低、可能爆音等片段文件名，方便后续和用户一起筛。"
            "它适合处理已经分离/净化后的人声轨，也可以直接处理普通语音音频或带音轨视频；如果用户还没做人声分离/净化，且需要更干净素材，可先调用 separate_audio_stems 或 clean_voice_track。"
            "训练素材任务可以分多步组合：必要时先 convert_media_file 提音频，再 separate_audio_stems 拿人声，再 clean_voice_track 降噪，最后 prepare_voice_dataset 切片打包；不要把这些步骤用于只要原文件的请求。"
            "用户没指定细节时，profile=gpt_sovits 就够了，不要硬填一堆参数。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_ids = self._normalize_source_ids(
            value.get("source_ids")
            or value.get("sources")
            or value.get("targets")
            or value.get("source_id")
            or value.get("source")
            or value.get("target")
            or value.get("attachment_id")
        )
        if not source_ids:
            return None
        return {
            "type": self.tool_type,
            "source_ids": source_ids,
            "profile": self._normalize_profile(value.get("profile") or value.get("preset") or "gpt_sovits"),
            "output_title": str(value.get("output_title") or value.get("title") or "").strip()[:80],
            "target_sr": self._coerce_int(value.get("target_sr") or value.get("sample_rate") or 0),
            "mono": self._coerce_bool(value.get("mono"), default=True),
            "min_clip_seconds": self._coerce_float(value.get("min_clip_seconds") or value.get("min_seconds") or 0),
            "max_clip_seconds": self._coerce_float(value.get("max_clip_seconds") or value.get("max_seconds") or 0),
            "silence_threshold_db": self._coerce_float_or_none(
                value.get("silence_threshold_db") or value.get("threshold_db")
            ),
            "min_silence_ms": self._coerce_int(value.get("min_silence_ms") or value.get("min_interval_ms") or 0),
            "max_silence_kept_ms": self._coerce_int(
                value.get("max_silence_kept_ms") or value.get("max_sil_kept_ms") or 0
            ),
            "clean_first": self._coerce_bool(value.get("clean_first") or value.get("light_clean"), default=False),
            "normalize_volume": self._coerce_bool(
                value.get("normalize_volume") or value.get("loudnorm"), default=False
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.prepare_voice_dataset(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_targets=list(call.get("source_ids") or []),
            profile=str(call.get("profile") or "gpt_sovits"),
            output_title=str(call.get("output_title") or ""),
            target_sr=int(call.get("target_sr") or 0),
            mono=bool(call.get("mono", True)),
            min_clip_seconds=call.get("min_clip_seconds") or 0,
            max_clip_seconds=call.get("max_clip_seconds") or 0,
            silence_threshold_db=call.get("silence_threshold_db"),
            min_silence_ms=call.get("min_silence_ms") or 0,
            max_silence_kept_ms=call.get("max_silence_kept_ms") or 0,
            clean_first=bool(call.get("clean_first")),
            normalize_volume=bool(call.get("normalize_volume")),
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_source_ids(self, value: Any) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text[:120])
        return normalized[:20]

    def _normalize_profile(self, value: Any) -> str:
        text = str(value or "gpt_sovits").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "gptsovits": "gpt_sovits",
            "gpt_sovits": "gpt_sovits",
            "sovits": "gpt_sovits",
            "rvc": "rvc",
            "archive": "archive",
            "归档": "archive",
        }
        return aliases.get(text, "gpt_sovits")

    def _coerce_int(self, value: Any) -> int:
        try:
            return int(float(str(value or "").strip()))
        except Exception:
            return 0

    def _coerce_float(self, value: Any) -> float:
        try:
            return float(str(value or "").strip())
        except Exception:
            return 0.0

    def _coerce_float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户", "是", "需要"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成", "否", "不需要"}:
            return False
        return default


class InspectMediaInfoToolHandler(BaseToolHandler):
    tool_type = "inspect_media_info"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def capability_status(self) -> dict[str, Any]:
        return _generated_media_capability_status(
            self.generated_file_service,
            getter_name="media_inspection_status",
            missing_reason="media_inspection_status_missing",
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_media_info：当用户问音频/视频的时长、编码、采样率、声道、码率、分辨率、帧率、是否有音轨，"
            "或你在转换/压缩/截取前需要先看媒体规格时使用。"
            '格式为 {"type":"inspect_media_info","source_id":"file_001|audio_001|gen_001"}。'
            "这个工具只读取媒体信息，不生成新文件；读取结果会告诉你真实规格，之后如果要处理文件再调用 convert_media_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        source_id = str(
            value.get("source_id") or value.get("source") or value.get("target") or value.get("attachment_id") or ""
        ).strip()
        if not source_id:
            return None
        return {
            "type": self.tool_type,
            "source_id": source_id[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.inspect_media_info(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            source_target=str(call.get("source_id") or ""),
            timestamp=context.now_ts,
        )
        media_info = result.get("media_info") if isinstance(result, dict) else None
        events = []
        if isinstance(media_info, dict):
            events.append(
                {
                    "type": "media_info_inspected",
                    "source_id": str(call.get("source_id") or ""),
                    "media_info": media_info,
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )


class ReviseGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "revise_generated_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- revise_generated_file：当用户要修改你刚生成的 gen_001/gen_002 文件时使用，默认生成新版本，不覆盖旧文件。"
            '格式为 {"type":"revise_generated_file","target":"gen_001",'
            '"instruction":"用户要求怎么改","output_format":"md|txt|docx|xlsx|pdf|json|csv|html",'
            '"output_title":"修改版标题","content_markdown":"修改后的完整正文或 Markdown",'
            '"table_rows":[["列1","列2"],["内容1","内容2"]],'
            '"formatting":{"rows":[{"index":2,"fill_color":"yellow"}]},"send_to_user":false}。'
            "这个工具不会替你理解“删第二段、加总结”；你需要根据生成文件工作台里的预览先整理出修改后的最终内容，"
            "再把最终内容写进 content_markdown 或 table_rows。"
            "如果只是调整颜色、加粗或高亮，把明确样式规则写进 formatting。"
            "如果用户只是要求继续改文件，优先用这个工具；如果是从原始附件重新整理一份新文件，用 compose_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        table_rows = self._normalize_table_rows(value.get("table_rows") or value.get("rows") or value.get("table"))
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("generated_id") or value.get("file_id") or "latest").strip()[
                :120
            ],
            "instruction": str(value.get("instruction") or value.get("task") or value.get("request") or "").strip()[
                :500
            ],
            "output_format": self._normalize_output_format(value.get("output_format") or value.get("format") or ""),
            "output_title": str(value.get("output_title") or value.get("title") or value.get("name") or "").strip()[
                :80
            ],
            "content_markdown": str(
                value.get("content_markdown")
                or value.get("markdown")
                or value.get("content")
                or value.get("body")
                or ""
            ).strip()[:80000],
            "table_rows": table_rows,
            "formatting": ComposeFileToolHandler._normalize_formatting(
                self, value.get("formatting") or value.get("styles") or value.get("style_rules")
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.revise_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            instruction=str(call.get("instruction") or ""),
            output_format=str(call.get("output_format") or ""),
            output_title=str(call.get("output_title") or ""),
            content_markdown=str(call.get("content_markdown") or ""),
            table_rows=list(call.get("table_rows") or []),
            formatting=call.get("formatting") if isinstance(call.get("formatting"), dict) else {},
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        return aliases.get(text, text)[:16]

    def _normalize_table_rows(self, value: Any) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        rows: list[list[str]] = []
        for row in value[:1000]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [str(cell or "").strip()[:500] for cell in list(row)[:50]]
            if any(cells):
                rows.append(cells)
        return rows

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class ApplyStyleToExistingFileToolHandler(BaseToolHandler):
    tool_type = "apply_style_to_existing_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- apply_style_to_existing_file：当用户只要求给已有 docx/xlsx 文件套样式，而不是重写全文时使用。"
            '格式为 {"type":"apply_style_to_existing_file","target":"file_001|gen_001|最近",'
            '"target_type":"attachment|generated","instruction":"用户的样式要求",'
            '"output_title":"样式版标题",'
            '"formatting":{"header":{"bold":true},"columns":[{"match_header":"姓名","font_color":"red"}],'
            '"rows":[{"index":2,"fill_color":"yellow"}],'
            '"row_rules":[{"where":{"column":"分数","lt":60},"font_color":"red"}],'
            '"highlights":[{"text":"重点","fill_color":"yellow"}]},"send_to_user":false}。'
            "适合“把姓名列标红”“低于60分整行标红”“重点高亮”这类操作；"
            "它会复制原文件并套样式，不需要你把大表格或整篇 Word 重新输出。"
            "如果用户要增删改正文内容，用 revise_generated_file；如果要从附件整理成新文件，用 compose_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("source_id") or value.get("file_id") or "latest").strip()[
                :120
            ],
            "target_type": self._normalize_target_type(value.get("target_type") or value.get("source_type")),
            "instruction": str(value.get("instruction") or value.get("task") or value.get("request") or "").strip()[
                :500
            ],
            "output_title": str(value.get("output_title") or value.get("title") or value.get("name") or "").strip()[
                :80
            ],
            "formatting": ComposeFileToolHandler._normalize_formatting(
                self, value.get("formatting") or value.get("styles") or value.get("style_rules")
            ),
            "send_to_user": self._coerce_bool(value.get("send_to_user"), default=False),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.apply_style_to_existing_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            target_type=str(call.get("target_type") or ""),
            instruction=str(call.get("instruction") or ""),
            output_title=str(call.get("output_title") or ""),
            formatting=call.get("formatting") if isinstance(call.get("formatting"), dict) else {},
            send_to_user=bool(call.get("send_to_user")),
            timestamp=context.now_ts,
        )
        generated = result.get("generated") if isinstance(result, dict) else None
        events = []
        if isinstance(generated, dict):
            events.append(
                {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": bool(result.get("send_to_user")),
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_target_type(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"attachment", "inbox", "file", "qq_file"}:
            return "attachment"
        if text in {"generated", "gen", "generated_file"}:
            return "generated"
        return ""

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "发送", "发给用户"}:
            return True
        if text in {"0", "false", "no", "n", "off", "不发送", "仅生成"}:
            return False
        return default


class SendFileToolHandler(BaseToolHandler):
    tool_type = "send_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- send_file：当用户要你发送已有文件时使用，可发送工作台材料 file_001/img_001/audio_001，"
            "也可发送生成物 gen_001/gen_002。"
            '格式为 {"type":"send_file","targets":["file_001","gen_001"]}，单个文件也可以用 target。'
            "file_/img_/audio_ 等是用户上传或工作台已有的原始材料，gen_ 是工具生成的结果；"
            "根据用户指代和实际需要选择准确目标，用户只要结果时不要顺带发送原始材料，明确要原件和结果时可以一次选择多个。"
            "适合“把刚才那个视频发我”“把原视频和转写稿都发我”“再发一次 gen_002”。"
            "它只发送已有文件，不修改、不转码、不重新生成；如果用户要求修改内容、换格式或重新整理，应使用对应生成/转换工具。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        targets_value = (
            value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
        )
        targets = ComposeFileToolHandler._normalize_sources(self, targets_value)
        return {
            "type": self.tool_type,
            "target": targets[0] if targets else "latest",
            "targets": targets,
            "delivery_action": self._normalize_delivery_action(
                value.get("delivery_action")
                or value.get("desktop_action")
                or value.get("handoff_action")
                or value.get("action")
            ),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.send_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            targets=list(call.get("targets") or []),
            timestamp=context.now_ts,
        )
        events = []
        files = result.get("files") if isinstance(result, dict) else None
        delivery_action = self._normalize_delivery_action(call.get("delivery_action"))
        allow_desktop_delivery = str(context.client_mode or "").strip() == "desktop_pet"
        if bool(result.get("ok")) and isinstance(files, list):
            for file_ref in files:
                if not isinstance(file_ref, dict):
                    continue
                event = {
                    "type": "file_ready",
                    "file": file_ref,
                    "send_to_user": True,
                    "client_mode": context.client_mode,
                }
                if delivery_action and allow_desktop_delivery:
                    event["delivery_action"] = delivery_action
                    event["desktop_delivery"] = {
                        "action": delivery_action,
                        # M66-D: path removed; use handle + /content route for byte transfer.
                        "name": str(file_ref.get("name") or file_ref.get("title") or ""),
                        "handle": str(file_ref.get("handle") or ""),
                    }
                events.append(event)
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_delivery_action(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        if text in {"", "default", "send", "workspace", "hand_off", "handoff"}:
            return ""
        if text in {"open", "open_file", "打开"}:
            return "open"
        if text in {"reveal", "show", "show_in_folder", "show_folder", "folder", "location", "定位", "位置"}:
            return "reveal"
        if text in {"save_desktop", "export_desktop", "desktop", "save_to_desktop", "存桌面", "放桌面"}:
            return "save_desktop"
        if text in {"copy_path", "path", "clipboard", "复制路径"}:
            return "copy_path"
        return ""


class SendGeneratedFileToolHandler(SendFileToolHandler):
    tool_type = "send_generated_file"

    def build_prompt_instruction(self) -> str:
        return (
            "- send_generated_file：兼容旧格式；当用户要重新发送已生成的 gen_001 文件时可用。"
            "优先使用 send_file；只有需要兼容旧调用时才使用本工具。"
            '格式为 {"type":"send_generated_file","targets":["gen_001","gen_002"]}。'
        )

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.send_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            targets=list(call.get("targets") or []),
            timestamp=context.now_ts,
        )
        events = []
        generated_files = result.get("generated_files") if isinstance(result, dict) else None
        delivery_action = self._normalize_delivery_action(call.get("delivery_action"))
        allow_desktop_delivery = str(context.client_mode or "").strip() == "desktop_pet"
        if bool(result.get("ok")) and isinstance(generated_files, list):
            for generated in generated_files:
                if not isinstance(generated, dict):
                    continue
                event = {
                    "type": "generated_file_ready",
                    "generated_file": generated,
                    "send_to_user": True,
                    "client_mode": context.client_mode,
                }
                if delivery_action and allow_desktop_delivery:
                    event["delivery_action"] = delivery_action
                    event["desktop_delivery"] = {
                        "action": delivery_action,
                        # M66-D: path removed; use handle + /content route for byte transfer.
                        "name": str(generated.get("output_title") or generated.get("generated_handle") or ""),
                        "handle": str(generated.get("generated_handle") or ""),
                    }
                events.append(event)
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )


class SendStickerToolHandler(BaseToolHandler):
    tool_type = "send_sticker"

    def __init__(self, *, sticker_service) -> None:
        self.sticker_service = sticker_service

    def build_prompt_instruction(self) -> str:
        sticker_list = self.sticker_service.build_prompt_list()
        return (
            "- send_sticker：当你想给用户发送当前可用表情包图片时使用。"
            f"可用表情：{sticker_list or '（当前没有可用表情）'}。"
            '格式为 {"type":"send_sticker","sticker":"biexiao|haoxingfu|tanshou|turan_chuxian|wainao|zaoba|zhuangsha|zhuangsi"}。'
            "它只负责发表情包，不生成文件、不修改附件；适合开心、吐槽、装傻、装死、突然冒泡等轻量情绪回应。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        target = (
            value.get("sticker")
            if value.get("sticker") is not None
            else value.get("sticker_id")
            if value.get("sticker_id") is not None
            else value.get("name")
            if value.get("name") is not None
            else value.get("label")
        )
        sticker = str(target or "").strip()
        if not sticker:
            return None
        return {
            "type": self.tool_type,
            "sticker": sticker[:80],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        target = str(call.get("sticker") or "").strip()
        resolution = self.sticker_service.resolve(target)
        if not resolution.ok or not isinstance(resolution.sticker, dict):
            candidates = list(resolution.candidates or [])
            if candidates:
                candidate_text = "、".join(f"{item.get('id')}({item.get('display_name')})" for item in candidates[:8])
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context=(
                        f"你刚刚想发送表情包“{target}”，但匹配到多个候选：{candidate_text}。"
                        "请让用户确认具体要哪一个，或改用准确 sticker id。"
                    ),
                )
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你刚刚想发送表情包“{target}”，但没有找到对应资源。"
                    f"当前可用：{self.sticker_service.build_prompt_list() or '无'}。"
                    "请自然告诉用户可以换一个表情名。"
                ),
            )

        sticker = dict(resolution.sticker)
        if not bool(sticker.get("exists")):
            return ToolExecutionResult(
                tool_type=self.tool_type,
                followup_context=(
                    f"你找到了表情包“{sticker.get('display_name') or target}”，"
                    "但本地 PNG 文件不存在，暂时发不出去。请自然告诉用户资源文件缺失。"
                ),
            )

        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "sticker_ready",
                    "sticker": {
                        "id": sticker.get("id"),
                        "display_name": sticker.get("display_name"),
                        "absolute_path": sticker.get("absolute_path"),
                        "public_path": sticker.get("public_path"),
                    },
                    "send_to_user": True,
                }
            ],
            followup_context=(
                f"你刚刚已经选择发送表情包“{sticker.get('display_name') or target}”。"
                "请用很短的一句话自然衔接，不要描述文件路径。"
            ),
        )


class InspectGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "inspect_generated_file"

    def __init__(self, *, generated_file_service) -> None:
        self.generated_file_service = generated_file_service

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_generated_file：当你需要回头查看自己生成过的 gen_001 文件正文、结尾、zip 清单或 manifest 时使用。"
            '格式为 {"type":"inspect_generated_file","target":"gen_001|最近|文件标题",'
            '"section":"content|head|tail|summary|file_list|manifest|file:manifest.json","max_chars":12000}。'
            "它只读取生成物，不会发送、修改或删除文件；适合继续修改前先确认内容、查看转写稿、检查训练集 zip 的 manifest/README。"
            "如果只是要把文件再发给用户，用 send_file；如果要修改内容，用 revise_generated_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        target = (
            value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
            if value.get("file_id") is not None
            else "latest"
        )
        section = str(value.get("section") or value.get("part") or value.get("member") or "content").strip()
        max_chars = self._normalize_max_chars(value.get("max_chars") or value.get("limit"))
        return {
            "type": self.tool_type,
            "target": str(target or "latest").strip()[:120] or "latest",
            "section": section[:260] or "content",
            "max_chars": max_chars,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.inspect_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            section=str(call.get("section") or "content"),
            max_chars=int(call.get("max_chars") or 12000),
        )
        events = []
        if bool(result.get("ok")):
            events.append(
                {
                    "type": "generated_file_inspected",
                    "generated_file": result.get("generated"),
                    "inspection": result.get("inspection"),
                }
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )

    def _normalize_max_chars(self, value: Any) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = 12000
        return max(500, min(40000, parsed))


class ManageGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "manage_generated_file"

    def __init__(self, *, generated_file_service, task_workspace_service=None) -> None:
        self.generated_file_service = generated_file_service
        self.task_workspace_service = task_workspace_service

    def build_prompt_instruction(self) -> str:
        return (
            "- manage_generated_file：当用户要清理、隐藏或删除你生成过的文件时使用，只管理 gen_001 这类生成物。"
            '格式为 {"type":"manage_generated_file","action":"archive|delete|purge",'
            '"targets":["gen_001","gen_002"],"reason":"清理原因"}。'
            "archive 只从生成文件工作台隐藏；delete 会同时删除本地生成文件；purge 会删除本地文件并清空生成物内容卡片。"
            "不要用它清理用户发来的 file_001/img_001，工作台材料应使用 clear_attachment_focus。"
            "用户笼统要求清理整个工作台时，应在同一轮同时调用 clear_attachment_focus(target=all) "
            "和 manage_generated_file(targets=[all])；普通清理用 archive，明确要求彻底删除才用 purge。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        action = self._normalize_action(value.get("action") or value.get("operation"))
        if not action:
            return None
        targets_value = (
            value.get("targets")
            if value.get("targets") is not None
            else value.get("target")
            if value.get("target") is not None
            else value.get("generated_id")
            if value.get("generated_id") is not None
            else value.get("file_id")
        )
        targets = ComposeFileToolHandler._normalize_sources(self, targets_value)
        return {
            "type": self.tool_type,
            "action": action,
            "targets": targets or ["latest"],
            "reason": str(value.get("reason") or value.get("why") or "").strip()[:200],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.generated_file_service.manage_generated_files(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            action=str(call.get("action") or ""),
            targets=list(call.get("targets") or []),
            reason=str(call.get("reason") or ""),
            timestamp=context.now_ts,
        )
        events = []
        managed = [dict(item) for item in list(result.get("managed") or []) if isinstance(item, dict)]
        failures = [dict(item) for item in list(result.get("failures") or []) if isinstance(item, dict)]
        cleaned_tasks: list[dict[str, Any]] = []
        if managed:
            events.append(
                {
                    "type": "generated_files_managed",
                    "action": str(result.get("action") or ""),
                    "managed": managed,
                    "unresolved": list(result.get("unresolved") or []),
                }
            )
            if self.task_workspace_service is not None:
                artifact_ids = {
                    str(value or "").strip()
                    for item in managed
                    for value in (item.get("generated_id"), item.get("generated_handle"))
                    if str(value or "").strip()
                }
                cleaned_tasks = self.task_workspace_service.cleanup_tasks_for_artifacts(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    artifact_ids=artifact_ids,
                    reason=str(call.get("reason") or "").strip() or "关联生成文件已退出当前工作台。",
                    timestamp=context.now_ts,
                )
                if cleaned_tasks:
                    events.append(
                        {
                            "type": "task_workspaces_cleaned",
                            "task_ids": [str(task.get("task_id") or "") for task in cleaned_tasks],
                            "reason": "generated_file_cleared",
                        }
                    )
        if failures:
            events.append(
                {
                    "type": "generated_files_manage_failed",
                    "action": str(result.get("action") or ""),
                    "failures": failures,
                }
            )
        followup_context = str(result.get("followup_context") or "") if isinstance(result, dict) else ""
        if cleaned_tasks:
            followup_context += (
                f"\n系统同时关闭了 {len(cleaned_tasks)} 个依赖这些生成文件的未收尾任务白板；"
                "这些旧任务不再是当前待办，不要主动继续汇报或追问。"
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=followup_context,
        )

    def _normalize_action(self, value: Any) -> str:
        action = str(value or "").strip().lower()
        aliases = {
            "hide": "archive",
            "archive": "archive",
            "remove": "archive",
            "clear": "archive",
            "收起": "archive",
            "归档": "archive",
            "隐藏": "archive",
            "delete": "delete",
            "unlink": "delete",
            "删除": "delete",
            "删掉": "delete",
            "purge": "purge",
            "destroy": "purge",
            "彻底删除": "purge",
            "彻底清理": "purge",
        }
        return aliases.get(action, "")
