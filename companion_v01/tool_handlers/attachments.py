"""Attachment / file material tool handlers."""

from __future__ import annotations

import re
from typing import Any

import config
from ..capability_registry import (
    INSPECT_ATTACHMENT_TOOL_SPEC,
    LOAD_MATERIAL_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
    SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC,
)
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    operation_tool_result,
)

class InspectAttachmentToolHandler(BaseToolHandler):
    tool_type = "inspect_attachment"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def tool_spec(self):  # M66-C
        return INSPECT_ATTACHMENT_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- inspect_attachment：当你需要列出当前材料，或展开查看其中一张图片/一个文件时使用。"
            '格式为 {"type":"inspect_attachment","target":"all|附件id|标题|文件名|latest","kind":"any|image|file|document|audio"}。'
            "群聊中的 latest 只指本轮 QQ 消息明确绑定的材料；本轮没有材料时会要求先列出工作台或使用精确 handle，"
            "不会把其他群友或更早的材料冒充成本轮图片。"
            "工作台材料只是临时上下文，不是礼物、角色资源或长期记忆；单独查看某个材料时使用。"
            "如果要同时对比多份材料，优先使用 sync_attachment_workspace。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "latest").strip()[
                :120
            ],
            "kind": self._normalize_kind(value.get("kind") or value.get("asset_type") or "any"),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        requested_target = str(call.get("target") or "").strip()
        effective_target = requested_target
        if self._is_latest_alias(requested_target) and self._is_qq_group_turn(context):
            bound_ids = self._current_qq_attachment_ids(context)
            if not bound_ids:
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    followup_context=(
                        "本轮 QQ 群消息没有绑定任何附件，因此不能把共享工作台中的历史 latest 当成本轮图片或文件。"
                        "如果用户指的是历史材料，请先用 inspect_attachment(target=\"all\") 查看发送者、时间和 handle，"
                        "再用精确 handle 打开；不要猜测。"
                    ),
                )
            result = {}
            for bound_id in reversed(bound_ids):
                candidate = self.attachment_service.inspect_attachment(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    target=bound_id,
                    kind=str(call.get("kind") or "any"),
                    timestamp=context.now_ts,
                )
                if bool(candidate.get("ok")):
                    result = candidate
                    break
            if not result:
                result = {
                    "ok": False,
                    "status": "current_attachment_kind_unavailable",
                    "followup_context": "本轮绑定的材料中没有符合 kind 条件的项目；不要改用历史 latest 猜测。",
                }
        else:
            result = self.attachment_service.inspect_attachment(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                target=effective_target,
                kind=str(call.get("kind") or "any"),
                timestamp=context.now_ts,
            )
        item = result.get("item") if isinstance(result, dict) else None
        events = []
        if isinstance(item, dict):
            events.append(
                {
                    "type": "attachment_inspected",
                    "attachment": item,
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    @staticmethod
    def _is_latest_alias(value: Any) -> bool:
        return str(value or "").strip().lower() in {"", "latest", "current", "最近", "当前", "最后一张", "最后一个"}

    @staticmethod
    def _is_qq_group_turn(context: ToolExecutionContext) -> bool:
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        delivery_context = (
            request_context.get("qq_delivery_context")
            if isinstance(request_context.get("qq_delivery_context"), dict)
            else {}
        )
        return str(context.client_mode or request_context.get("client_mode") or "").strip() == "qq_text" and bool(
            delivery_context.get("is_group")
        )

    @staticmethod
    def _current_qq_attachment_ids(context: ToolExecutionContext) -> list[str]:
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        values = request_context.get("qq_current_attachment_ids")
        if not isinstance(values, list):
            return []
        return [str(value or "").strip() for value in values if str(value or "").strip()]

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"


class LoadMaterialToolHandler(BaseToolHandler):
    tool_type = "load_material"

    def __init__(self, *, image_material_resolver) -> None:
        self.image_material_resolver = image_material_resolver

    def tool_spec(self):  # M66-C
        return LOAD_MATERIAL_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- load_material：需要重新观察当前会话工作区里较早的原图或生成图时使用。"
            '格式为 {"type":"load_material","targets":["img_001","gen_002"],'
            '"purpose":"重新比较细节"}。'
            "它会把原图通过模型原生多模态通道送入下一轮；当前消息已经带图、或摘要足够时不必调用。"
            "只能填写工作区 handle，不能填写路径、URL 或 base64。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        raw_targets = value.get("targets")
        if raw_targets is None:
            raw_targets = value.get("target") or value.get("image_ids") or value.get("images")
        if isinstance(raw_targets, str):
            candidates = [part.strip() for part in raw_targets.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(raw_targets, (list, tuple, set)):
            candidates = [str(item or "").strip() for item in raw_targets]
        else:
            candidates = []
        targets = list(dict.fromkeys(item[:120] for item in candidates if item))[:5]
        if not targets:
            return None
        return {
            "type": self.tool_type,
            "targets": targets,
            "purpose": str(value.get("purpose") or value.get("reason") or "").strip()[:240],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.image_material_resolver.build_model_image_inputs(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            targets=list(call.get("targets") or []),
            max_count=5,
            max_bytes_per_image=int(getattr(config, "VISION_MAX_IMAGE_BYTES", 8 * 1024 * 1024) or 0),
            max_total_bytes=20 * 1024 * 1024,
        )
        images = [dict(item) for item in list(result.get("images") or []) if isinstance(item, dict)]
        handles = [str(item.get("attachment_handle") or "").strip() for item in images]
        handles = [item for item in handles if item]
        unresolved = [
            {
                "target": str(item.get("target") or "")[:120],
                "reason": str(item.get("reason") or "unavailable")[:120],
            }
            for item in list(result.get("unresolved") or [])
            if isinstance(item, dict)
        ]
        if not images:
            unresolved_labels = ", ".join(item["target"] for item in unresolved if item["target"])
            return ToolExecutionResult(
                tool_type=self.tool_type,
                stream_events=[
                    {
                        "type": "material_load_failed",
                        "status": "unavailable",
                        "targets": list(call.get("targets") or []),
                    }
                ],
                followup_context=(
                    "<tool_use_error>没有加载到可用原图。"
                    f"未解析目标：{unresolved_labels or '未知'}。"
                    "请基于已有摘要继续，或自然请用户重新发送图片；不要假装看到了原图。</tool_use_error>"
                ),
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "material_images_loaded",
                    "status": "ready" if not unresolved else "partial",
                    "handles": handles,
                    "image_count": len(images),
                    "unresolved_count": len(unresolved),
                }
            ],
            followup_context=(
                f"已把当前会话材料 {', '.join(handles)} 的原始图片通过原生多模态通道加载到下一轮。"
                "请直接观察图片完成用户任务；不要只复述旧摘要，也不要声称看到了未加载的材料。"
            ),
            model_image_inputs=images,
        )

class ReadAttachmentSectionToolHandler(BaseToolHandler):
    tool_type = "read_attachment_section"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def tool_spec(self):  # M66-C
        return READ_ATTACHMENT_SECTION_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- read_attachment_section：当工作台材料较长、你需要展开某一页/某几行/某个表/某个 sheet 的内容时使用。"
            '格式为 {"type":"read_attachment_section","target":"file_001|标题|文件名|latest",'
            '"section":"第2页|第10-30行|第1个表|Sheet1","kind":"any|file|document"}。'
            "它只展开当前已解析出的可用文本片段；如果文件本身没有文本层或还没解析好，系统会告诉你。"
            "不要用它处理图片礼物或长期记忆。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "latest").strip()[
                :120
            ],
            "section": str(value.get("section") or value.get("range") or value.get("page") or "当前可用片段").strip()[
                :120
            ],
            "kind": self._normalize_kind(value.get("kind") or "document"),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.read_section(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or ""),
            section=str(call.get("section") or ""),
            kind=str(call.get("kind") or "document"),
            timestamp=context.now_ts,
        )
        item = result.get("item") if isinstance(result, dict) else None
        events = []
        if isinstance(item, dict):
            events.append(
                {
                    "type": "attachment_section_read",
                    "attachment": item,
                    "section": str(call.get("section") or ""),
                }
            )
        content = str(result.get("content") or "") if isinstance(result, dict) else ""
        execution = operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )
        if bool(result.get("ok")):
            # The section extraction is the tool's own paging unit; its output is
            # bounded by the extraction budget, so it must bypass the global
            # 8000-char insurance instead of being silently cut there.
            diagnostics = {"shown_chars": len(content)}
            extra_note = ""
            if content and len(content) >= 11_500:
                extra_note = (
                    "\n该片段达到单次展开上限；如需其它位置，用 section 指定页、行或 sheet 继续读取。"
                )
            envelope = ToolFollowupEnvelope(
                content=(
                    str(execution.followup_context or "").strip() + extra_note
                ),
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics=diagnostics,
            )
            execution.followup_context = envelope.content
            execution.followup_envelope = envelope
        return execution

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "document").strip().lower()
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"any", "file", "document"}:
            return kind
        return "document"


class SyncAttachmentWorkspaceToolHandler(BaseToolHandler):
    tool_type = "sync_attachment_workspace"

    def __init__(self, *, attachment_service) -> None:
        self.attachment_service = attachment_service

    def tool_spec(self):  # M66-C
        return SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- sync_attachment_workspace：当你需要整理当前材料工作台时使用。新发来的图片/文件通常会自动进入工作台；"
            "这个工具主要用于收起暂时不分析的材料、重新指定重点材料，或切换要对比的对象。"
            '格式为 {"type":"sync_attachment_workspace","focus_targets":["img_001","第2张图","菜单照片"],"kind":"any|image|file|document|audio","reason":"为什么需要这些材料"}。'
            "focus_targets 是整理后的最终工作台清单；可以一次保留多张图片或多个文件进行对比。"
            "未列入的其它材料会留在旁边材料清单，只给识别信息。"
            "系统会按上下文预算尽量展开你选中的材料；如果某些大文件放不下，会提示你用 read_attachment_section 指定页、行或 sheet。"
            "不要用一连串打开/关闭操作；一次性提交整理后的最终清单即可。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        targets = value.get("focus_targets")
        if targets is None:
            targets = value.get("targets") or value.get("attachment_ids") or value.get("target") or []
        normalized_targets = self._normalize_targets(targets)
        return {
            "type": self.tool_type,
            "focus_targets": normalized_targets[:30],
            "kind": self._normalize_kind(value.get("kind") or "any"),
            "reason": str(value.get("reason") or "").strip()[:160],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.sync_workspace(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            focus_targets=list(call.get("focus_targets") or []),
            kind=str(call.get("kind") or "any"),
            reason=str(call.get("reason") or ""),
            timestamp=context.now_ts,
        )
        focused = list(result.get("focused") or []) if isinstance(result, dict) else []
        events = []
        if focused:
            events.append(
                {
                    "type": "attachment_workspace_synced",
                    "items": focused,
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_targets(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        targets: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in targets:
                targets.append(text[:120])
        return targets

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"


class ClearAttachmentFocusToolHandler(BaseToolHandler):
    tool_type = "clear_attachment_focus"

    def __init__(self, *, attachment_service, task_workspace_service=None) -> None:
        self.attachment_service = attachment_service
        self.task_workspace_service = task_workspace_service

    def build_prompt_instruction(self) -> str:
        return (
            "- clear_attachment_focus：当工作台图片/文件已经聊完、用户说发错了、或你判断不需要继续挂在上下文时使用。"
            '格式为 {"type":"clear_attachment_focus","target":"current|latest|all|附件id/标题/文件名","targets":["img_001","第2张图"],"kind":"any|image|file|document|audio","delete_storage":false,"reason":"可选原因"}。'
            "清理多个指定材料时用 targets 数组；清理全部图片或文件时用 target=all 并配合 kind。"
            "默认只让材料退出当前工作台；只有用户明确要求删除原始附件文件时才把 delete_storage 设为 true。"
            "关联这些材料、仍未收尾的任务白板会一并关闭，避免旧任务继续占用上下文；它不删除聊天记忆、生成成果或礼物。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        targets = value.get("targets")
        if targets is None:
            targets = value.get("attachment_ids")
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "current").strip()[
                :120
            ],
            "targets": self._normalize_targets(targets),
            "kind": self._normalize_kind(value.get("kind") or "any"),
            "delete_storage": bool(value.get("delete_storage") or value.get("purge") or value.get("delete_files")),
            "reason": str(value.get("reason") or "").strip()[:160],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_service.clear_focus(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "current"),
            targets=list(call.get("targets") or []),
            kind=str(call.get("kind") or "any"),
            reason=str(call.get("reason") or ""),
            delete_storage=bool(call.get("delete_storage")),
            timestamp=context.now_ts,
        )
        cleared = list(result.get("cleared") or []) if isinstance(result, dict) else []
        events = []
        cleaned_tasks: list[dict[str, Any]] = []
        if cleared:
            events.append(
                {
                    "type": "attachment_focus_cleared",
                    "items": cleared,
                }
            )
            if self.task_workspace_service is not None:
                artifact_ids = {
                    str(value or "").strip()
                    for item in cleared
                    if isinstance(item, dict)
                    for value in (item.get("attachment_id"), item.get("attachment_handle"))
                    if str(value or "").strip()
                }
                cleaned_tasks = self.task_workspace_service.cleanup_tasks_for_artifacts(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    artifact_ids=artifact_ids,
                    reason=str(call.get("reason") or "").strip() or "关联材料已退出当前工作台。",
                    timestamp=context.now_ts,
                )
                if cleaned_tasks:
                    events.append(
                        {
                            "type": "task_workspaces_cleaned",
                            "task_ids": [str(task.get("task_id") or "") for task in cleaned_tasks],
                            "reason": "material_cleared",
                        }
                    )
        followup_context = str(result.get("followup_context") or "") if isinstance(result, dict) else ""
        if cleaned_tasks:
            followup_context += (
                f"\n系统同时关闭了 {len(cleaned_tasks)} 个依赖这些材料的未收尾任务白板；"
                "这些旧任务不再是当前待办，不要主动继续汇报或追问。"
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=followup_context,
        )

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"

    def _normalize_targets(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").replace("、", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        targets: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in targets:
                targets.append(text[:120])
        return targets[:20]

class RetryAttachmentToolHandler(BaseToolHandler):
    tool_type = "retry_attachment"

    def __init__(self, *, attachment_ingest_service) -> None:
        self.attachment_ingest_service = attachment_ingest_service

    def build_prompt_instruction(self) -> str:
        return (
            "- retry_attachment：当工作台图片/文件处理失败，且用户让你再试一次，或你需要重新读取失败材料时使用。"
            '格式为 {"type":"retry_attachment","target":"latest|附件id|img_001|标题|文件名","kind":"any|image|file|document|audio","reason":"可选原因"}。'
            "这个工具只会重新处理工作台材料，不会把它变成礼物、角色资源或长期记忆；成功后材料会回到当前材料工作台。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            "target": str(value.get("target") or value.get("attachment_id") or value.get("query") or "latest").strip()[
                :120
            ],
            "kind": self._normalize_kind(value.get("kind") or "any"),
            "reason": str(value.get("reason") or "").strip()[:160],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_ingest_service.retry_attachment(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=str(call.get("target") or "latest"),
            kind=str(call.get("kind") or "any"),
            timestamp=context.now_ts,
        )
        item = result.get("item") if isinstance(result, dict) else None
        events = []
        if isinstance(item, dict):
            events.append(
                {
                    "type": "attachment_retry_started",
                    "status": str(result.get("status") or ""),
                    "item": item,
                }
            )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_kind(self, value: Any) -> str:
        kind = str(value or "any").strip().lower()
        if kind in {"photo", "picture", "pic", "img"}:
            return "image"
        if kind in {"doc", "text", "txt", "pdf"}:
            return "document"
        if kind in {"music", "song", "voice"}:
            return "audio"
        if kind in {"any", "image", "file", "document", "audio"}:
            return kind
        return "any"


class FetchMediaFromUrlToolHandler(BaseToolHandler):
    tool_type = "fetch_media_from_url"

    def __init__(self, *, attachment_ingest_service) -> None:
        self.attachment_ingest_service = attachment_ingest_service

    def build_prompt_instruction(self) -> str:
        return (
            "- fetch_media_from_url：当用户直接给你公开视频/音频链接，想让你先把素材下载到当前工作台时使用。"
            '格式为 {"type":"fetch_media_from_url","url":"https://...","preferred_title":"可选标题"}，'
            '批量时可用 {"type":"fetch_media_from_url","urls":["https://...","https://..."]}。'
            "在 QQ/桌宠模式里，如果用户只发来一个公开视频或音频链接，或说“下载/拉进来/转写/总结这个链接”，"
            "应优先调用这个工具实际获取素材；不要只凭猜测说链接打不开、需要登录或平台不稳定。"
            "如果用户说“再试一次/重新下载/继续试”，且最近对话里有明确链接，也应带上那个链接重新调用。"
            "它只负责把公开可访问的媒体链接下载成工作台材料，不会直接总结、转写或转码；"
            "下载成功后，这些素材会像普通 audio_001/file_001 一样进入当前材料工作台，之后再继续用 inspect_attachment、inspect_media_info、transcribe_media、convert_media_file 或 send_file。"
            "如果用户只是要原视频/原音频或“把链接里的文件发我”，下载成功后直接 send_file 对应 handle，不要顺手转写、提音频或压缩。"
            "不要用它处理需要登录、付费、会员、DRM 或整条播放列表/合集的链接。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        urls_value = (
            value.get("urls")
            if value.get("urls") is not None
            else value.get("links")
            if value.get("links") is not None
            else value.get("url")
        )
        urls = self._normalize_urls(urls_value)
        if not urls:
            return None
        return {
            "type": self.tool_type,
            "url": urls[0],
            "urls": urls,
            "preferred_title": str(value.get("preferred_title") or value.get("title") or "").strip()[:120],
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.attachment_ingest_service.fetch_media_from_urls(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            urls=list(call.get("urls") or []),
            preferred_title=str(call.get("preferred_title") or ""),
            character_pack_id=context.character_pack_id,
            timestamp=context.now_ts,
        )
        events = []
        if isinstance(result, dict):
            for item in list(result.get("items") or []):
                if not isinstance(item, dict):
                    continue
                events.append(
                    {
                        "type": "attachment_remote_media_ready",
                        "item": item,
                    }
                )
        return operation_tool_result(
            tool_type=self.tool_type,
            operation_result=result,
            success_events=events,
        )

    def _normalize_urls(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = re.split(r"[\s,，;；]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        urls: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text.startswith(("http://", "https://")) and text not in urls:
                urls.append(text[:1000])
        return urls[:8]
