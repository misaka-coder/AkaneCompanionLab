from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .client_protocol import ClientMode


DOCUMENT_ATTACHMENT_FORMATS = {
    "txt",
    "md",
    "markdown",
    "log",
    "lrc",
    "srt",
    "vtt",
    "json",
    "toml",
    "yaml",
    "yml",
    "csv",
    "ini",
    "cfg",
    "conf",
    "py",
    "js",
    "ts",
    "tsx",
    "jsx",
    "html",
    "css",
    "xml",
    "sql",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "cs",
    "go",
    "rs",
    "pdf",
    "docx",
    "xlsx",
}

DOCUMENT_GENERATED_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mov", "mkv", "webm", "avi"}

COMMON_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D, ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)
WEB_SCENE_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D)
CHAT_FILE_CLIENT_MODES = (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)

COMMON_TOOL_NAMES = (
    "retrieve_memory",
    "read_memory_timeline",
    "load_character_context",
    "set_reminder",
    "list_reminders",
    "cancel_reminder",
    "manage_persona",
    "manage_task_workspace",
    "delegate_task",
)

WEB_SEARCH_TOOL_NAMES = ("web_search",)
DESKTOP_BROWSER_TOOL_NAMES = ("open_browser", "browser_page")
DESKTOP_MUSIC_REQUEST_TOOL_NAMES = ("open_music_search",)
DESKTOP_WORKSPACE_TOOL_NAMES = (
    "list_workspace",
    "read_workspace",
    "focus_workspace",
    "register_workspace_items",
)

WEB_SCENE_TOOL_NAMES = (
    "call_npc",
    "check_inventory",
    "manage_gift",
    "manage_artifact",
)

REMOTE_MEDIA_TOOL_NAMES = ("fetch_media_from_url",)

ATTACHMENT_WORKSPACE_TOOL_NAMES = (
    "sync_attachment_workspace",
    "inspect_attachment",
    "retry_attachment",
    "clear_attachment_focus",
)

DOCUMENT_WORKBENCH_TOOL_NAMES = (
    "read_attachment_section",
    "compose_file",
    "revise_generated_file",
    "apply_style_to_existing_file",
)

MEDIA_WORKBENCH_TOOL_NAMES = (
    "inspect_media_info",
    "separate_audio_stems",
    "clean_voice_track",
    "transcribe_media",
    "prepare_voice_dataset",
    "convert_media_file",
)

GENERATED_FILE_MANAGEMENT_TOOL_NAMES = (
    "inspect_generated_file",
    "manage_generated_file",
)

FILE_HANDOFF_TOOL_NAMES = ("send_file",)
CONVERSATION_FILE_AUTHORING_TOOL_NAMES = ("compose_file",)
QQ_STICKER_TOOL_NAMES = ("send_sticker",)


@dataclass(frozen=True)
class CapabilitySnapshot:
    client_mode: ClientMode
    has_any_attachment: bool = False
    has_document_attachment: bool = False
    has_media_attachment: bool = False
    has_generated_file: bool = False
    has_document_generated_file: bool = False
    has_media_generated_file: bool = False
    has_pending_gift: bool = False


@dataclass(frozen=True)
class CapabilityModule:
    name: str
    layer: str
    modes: tuple[ClientMode, ...]
    tools: tuple[str, ...]
    light_hint: str
    trigger: Callable[[CapabilitySnapshot], bool]

    def applies_to_mode(self, mode: ClientMode) -> bool:
        return mode in self.modes


@dataclass(frozen=True)
class CapabilitySelection:
    light_hints: tuple[str, ...]
    tool_names: tuple[str, ...]
    module_names: tuple[str, ...]
    layer_names: tuple[str, ...] = ()


def _always(_: CapabilitySnapshot) -> bool:
    return True


def _never(_: CapabilitySnapshot) -> bool:
    return False


def _has_any_attachment(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment


def _has_document_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_document_attachment or snapshot.has_document_generated_file


def _has_media_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_media_attachment or snapshot.has_media_generated_file


def _has_generated_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_generated_file


def _has_deliverable_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment or snapshot.has_generated_file


def _is_web_scene(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.client_mode in {ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D}


class CapabilityRegistry:
    """Select lightweight ability hints and full tool instructions per turn."""

    def __init__(self, modules: tuple[CapabilityModule, ...] | None = None) -> None:
        self.modules = modules or self._default_modules()

    def select(self, snapshot: CapabilitySnapshot) -> CapabilitySelection:
        hints: list[str] = []
        tools: list[str] = []
        module_names: list[str] = []
        layer_names: list[str] = []
        seen_tools: set[str] = set()
        seen_hints: set[str] = set()
        seen_layers: set[str] = set()
        for module in self.modules:
            if not module.applies_to_mode(snapshot.client_mode):
                continue
            hint = module.light_hint.strip()
            if hint and hint not in seen_hints:
                seen_hints.add(hint)
                hints.append(hint)
            if not module.trigger(snapshot):
                continue
            module_names.append(module.name)
            layer = str(module.layer or "").strip()
            if layer and layer not in seen_layers:
                seen_layers.add(layer)
                layer_names.append(layer)
            for tool_name in module.tools:
                if tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
        return CapabilitySelection(
            light_hints=tuple(hints),
            tool_names=tuple(tools),
            module_names=tuple(module_names),
            layer_names=tuple(layer_names),
        )

    def tool_names_for_mode(self, mode: ClientMode) -> tuple[str, ...]:
        selected: list[str] = []
        seen: set[str] = set()
        for module in self.modules:
            if not module.applies_to_mode(mode):
                continue
            for tool_name in module.tools:
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                selected.append(tool_name)
        return tuple(selected)

    def _default_modules(self) -> tuple[CapabilityModule, ...]:
        return (
            CapabilityModule(
                name="base",
                layer="common",
                modes=COMMON_CLIENT_MODES,
                tools=COMMON_TOOL_NAMES,
                light_hint="你可以按语义检索长期记忆，也可以在用户明确指定日期时读取原始对话时间线；还可以设置/查看/取消提醒、维护表达侧面。短任务直接调用工具完成，复杂多步任务可以记录到任务工作区，也可以委派给后台工坊分担。",
                trigger=_always,
            ),
            CapabilityModule(
                name="internet_access",
                layer="web",
                modes=COMMON_CLIENT_MODES,
                tools=WEB_SEARCH_TOOL_NAMES,
                light_hint="当用户明确要求联网搜索、查询最新信息或读取公开网页时，你可以使用受限的 AnySearch 联网搜索能力；它只返回搜索/提取结果，不会打开或滚动浏览器。不要用它访问私密、内网或需要登录的内容。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_browser_open",
                layer="desktop_browser",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_BROWSER_TOOL_NAMES,
                light_hint="桌宠模式下，open_browser 只把公开网页交给用户的系统浏览器打开；browser_page 会打开并操作 Akane 可见托管浏览器窗口，用于读取、滚动、按可见候选序号打开链接，以及经授权的点击/输入。不要接管用户手动打开的浏览器标签页，不要登录、下载、上传或访问私密/内网内容。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_music_request",
                layer="music_request",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_MUSIC_REQUEST_TOOL_NAMES,
                light_hint="桌宠模式下，当用户明确要点歌或搜索一首歌来听时，可以用 open_music_search 打开公开音乐平台搜索页；它不代表已经播放成功，后续点击/输入仍按浏览器授权边界处理。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_file_workspace",
                layer="desktop_workspace",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_WORKSPACE_TOOL_NAMES,
                light_hint="桌宠模式下，你始终拥有一个可主动查询的 Akane 文件工作区。用户提到刚放入、寻找、处理或清理某个文件时，先从 workspace:/ 调用 list_workspace 查询，不要先让用户提供本机绝对路径；你也可以批量读取、聚焦材料，并把文件原地登记为文档或媒体工具可用的附件 handle。",
                trigger=_always,
            ),
            CapabilityModule(
                name="remote_media_fetch",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=REMOTE_MEDIA_TOOL_NAMES,
                light_hint="你也可以先把公开音频/视频链接下载进当前工作台；如果用户只要原视频/原音频，下载后直接交付原文件，不要多做转写、转码或净化。",
                trigger=_always,
            ),
            CapabilityModule(
                name="attachment_workspace",
                layer="shared_attachment_workspace",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=ATTACHMENT_WORKSPACE_TOOL_NAMES,
                light_hint="你可以接收临时图片和文件，并整理当前工作台；文件交付由当前客户端自己的文件交付层处理。",
                trigger=_has_any_attachment,
            ),
            CapabilityModule(
                name="conversation_file_authoring",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=CONVERSATION_FILE_AUTHORING_TOOL_NAMES,
                light_hint="即使没有附件，你也可以把当前对话中已经整理好的内容直接生成文件并交给当前端；用户说开始/直接做/生成时，不要只口头承诺。",
                trigger=_always,
            ),
            CapabilityModule(
                name="qq_file_delivery",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=FILE_HANDOFF_TOOL_NAMES,
                light_hint="在 QQ 里，你可以把已有工作台材料或生成文件发回给用户；只发送已有文件，不替代生成、转码或修改。",
                trigger=_has_deliverable_file,
            ),
            CapabilityModule(
                name="desktop_file_handoff",
                layer="desktop_workspace",
                modes=(ClientMode.DESKTOP_PET,),
                tools=FILE_HANDOFF_TOOL_NAMES,
                light_hint="在桌宠里，你可以把已有工作台材料或生成文件交给桌宠工作台打开、播放或继续处理；只交付已有文件，不替代生成、转码或修改。",
                trigger=_has_deliverable_file,
            ),
            CapabilityModule(
                name="sticker_pack",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=QQ_STICKER_TOOL_NAMES,
                light_hint="你有一组静态表情包；聊天氛围适合时可以发送一张表情包，但不要为了展示功能而频繁发送。",
                trigger=_always,
            ),
            CapabilityModule(
                name="document_workbench",
                layer="shared_document",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=DOCUMENT_WORKBENCH_TOOL_NAMES,
                light_hint="你可以阅读、整理、转换和样式加工文本、Office、PDF 等文档。",
                trigger=_has_document_context,
            ),
            CapabilityModule(
                name="media_workbench",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=MEDIA_WORKBENCH_TOOL_NAMES,
                light_hint="你可以处理音频/视频任务：转写、转码、降噪、分离人声、切片打包训练素材等。在 QQ 里这些媒体任务容易耗时，优先委派后台工坊；完成后再通知和交付。",
                trigger=_has_media_context,
            ),
            CapabilityModule(
                name="generated_file_management",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=GENERATED_FILE_MANAGEMENT_TOOL_NAMES,
                light_hint="你可以回看、交付、归档、删除或清理自己刚生成的文件。",
                trigger=_has_generated_file,
            ),
            CapabilityModule(
                name="web_scene_world",
                layer="web_scene",
                modes=WEB_SCENE_CLIENT_MODES,
                tools=WEB_SCENE_TOOL_NAMES,
                light_hint="你可以围绕当前场景、礼物、藏品和临时 NPC 参与小世界构建。",
                trigger=_is_web_scene,
            ),
            CapabilityModule(
                name="desktop_environment",
                layer="desktop_environment",
                modes=(ClientMode.DESKTOP_PET,),
                tools=(),
                light_hint="桌宠模式下，你未来可以获得桌面观察、窗口理解和快捷操作能力；当前仅保留能力提示。",
                trigger=_never,
            ),
        )


def is_document_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "document":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return file_kind in DOCUMENT_ATTACHMENT_FORMATS or mime_type.startswith("text/")


def is_media_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "audio":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return bool(detail.get("media_info")) or file_kind in MEDIA_FORMATS or mime_type.startswith(("audio/", "video/"))


def is_document_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in DOCUMENT_GENERATED_FORMATS


def is_media_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in MEDIA_FORMATS
