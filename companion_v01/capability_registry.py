from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .client_protocol import ClientMode


DOCUMENT_ATTACHMENT_FORMATS = {
    "txt",
    "md",
    "markdown",
    "log",
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
        seen_tools: set[str] = set()
        seen_hints: set[str] = set()
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
            for tool_name in module.tools:
                if tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
        return CapabilitySelection(
            light_hints=tuple(hints),
            tool_names=tuple(tools),
            module_names=tuple(module_names),
        )

    def _default_modules(self) -> tuple[CapabilityModule, ...]:
        qq_and_desktop = (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)
        return (
            CapabilityModule(
                name="base",
                modes=(ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D, ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET),
                tools=("retrieve_memory", "set_reminder", "list_reminders", "cancel_reminder", "manage_persona", "manage_task_workspace", "delegate_task"),
                light_hint="你可以主动检索长期记忆，也可以设置/查看/取消提醒、维护表达侧面；短任务直接调用工具完成，复杂多步任务可以记录到任务工作区，也可以委派给后台工坊分担。",
                trigger=_always,
            ),
            CapabilityModule(
                name="remote_media_fetch",
                modes=qq_and_desktop,
                tools=("fetch_media_from_url",),
                light_hint="你也可以先把公开音频/视频链接下载进当前工作台；如果用户只要原视频/原音频，下载后直接发送原文件，不要多做转写、转码或净化。",
                trigger=_always,
            ),
            CapabilityModule(
                name="attachment_workspace",
                modes=qq_and_desktop,
                tools=("sync_attachment_workspace", "inspect_attachment", "retry_attachment", "clear_attachment_focus", "send_file"),
                light_hint="你可以接收临时图片和文件，在聊天里整理当前工作台，也可以把已有附件发回给用户。",
                trigger=_has_any_attachment,
            ),
            CapabilityModule(
                name="conversation_file_authoring",
                modes=qq_and_desktop,
                tools=("compose_file",),
                light_hint="即使没有附件，你也可以把当前对话中已经整理好的内容直接生成文件发给用户；用户说开始/直接做/生成时，不要只口头承诺。",
                trigger=_always,
            ),
            CapabilityModule(
                name="document_workbench",
                modes=qq_and_desktop,
                tools=("read_attachment_section", "compose_file", "revise_generated_file", "apply_style_to_existing_file"),
                light_hint="你可以阅读、整理、转换和样式加工文本、Office、PDF 等文档。",
                trigger=_has_document_context,
            ),
            CapabilityModule(
                name="media_workbench",
                modes=qq_and_desktop,
                tools=("inspect_media_info", "separate_audio_stems", "clean_voice_track", "transcribe_media", "prepare_voice_dataset", "convert_media_file"),
                light_hint=(
                    "音频/视频任务按需求自由组合：视频总结通常先 transcribe_media 得到转写稿再 compose_file；"
                    "字幕任务优先 transcribe_media 输出 srt/vtt；训练素材可按需要组合 convert_media_file 提音频、"
                    "separate_audio_stems 分离人声、clean_voice_track 降噪净化、prepare_voice_dataset 切片打包；"
                    "用户只要原文件时只发送原文件，不要额外处理。"
                ),
                trigger=_has_media_context,
            ),
            CapabilityModule(
                name="generated_file_management",
                modes=qq_and_desktop,
                tools=("inspect_generated_file", "send_file", "manage_generated_file"),
                light_hint="你可以回看、继续发送、归档、删除或清理自己刚生成的文件。",
                trigger=_has_generated_file,
            ),
            CapabilityModule(
                name="web_scene_world",
                modes=(ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D),
                tools=("call_npc", "check_inventory", "manage_gift", "manage_artifact"),
                light_hint="你可以围绕当前场景、礼物、藏品和临时 NPC 参与小世界构建。",
                trigger=_is_web_scene,
            ),
            CapabilityModule(
                name="desktop_environment",
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
