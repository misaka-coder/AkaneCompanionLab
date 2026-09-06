"""Generated media and file handoff tool handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config

from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    operation_tool_result,
)

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
            "它适合文档、静态展示页和短小自包含文件；返回成功只证明文件已生成，不证明内容可运行。"
            "可执行程序、游戏、多文件项目或需要调试的代码优先加载 coding-project Skill 并用 Shell 真实验证。"
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
            "这个工具只读取媒体信息，不生成新文件；读取结果会告诉你真实规格。"
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
            "成功只代表产生了修改版文件，不代表修改解决了运行问题；复杂代码不建议连续整文件重写，"
            "优先加载 coding-project Skill 局部修改并运行检查。"
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
        sticker = str(value.get("sticker") or "").strip()
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
            "正文还有未展示部分时结果会带 cursor；如果已展示内容足够可以直接回答，只有确实需要后续正文时才用 inspect_generated_file(cursor=\"...\") 继续。"
            "如果只是要把文件再发给用户，用 send_file；如果要修改内容，用 revise_generated_file。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        cursor = str(value.get("cursor") or "").strip()
        if cursor:
            return {"type": self.tool_type, "cursor": cursor}
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
        cursor = str(call.get("cursor") or "").strip()
        if cursor:
            return self._execute_continuation(cursor=cursor, context=context)
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
        execution = ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=str(result.get("followup_context") or "") if isinstance(result, dict) else "",
        )
        if bool(result.get("ok")):
            inspection = result.get("inspection") if isinstance(result.get("inspection"), dict) else {}
            truncated = bool(inspection.get("truncated"))
            generated = result.get("generated") if isinstance(result.get("generated"), dict) else {}
            content = str(inspection.get("content") or "")
            diagnostics = {
                "section": str(inspection.get("section") or ""),
                "source_kind": str(inspection.get("source_kind") or ""),
                "truncated": truncated,
                "shown_chars": len(content),
            }
            continuation = None
            extra_note = ""
            if truncated and str(inspection.get("section") or "") == "content" and generated.get("absolute_path"):
                cursor_value = self._issue_content_cursor(
                    generated=generated,
                    offset=len(content),
                    context=context,
                )
                if cursor_value:
                    continuation = {"type": self.tool_type, "cursor": cursor_value}
                    extra_note = (
                        "\n正文还有未展示部分。如果当前内容已经足够，可以直接回答；"
                        f"只有确实需要后续正文时，才调用 inspect_generated_file(cursor=\"{cursor_value}\")。"
                    )
                else:
                    extra_note = (
                        "\n正文还有未展示部分，但当前文件不满足连续读取条件；"
                        "可用 section=head/tail/summary 或其它读取路径查看。"
                    )
            envelope = ToolFollowupEnvelope(
                content=str(execution.followup_context or "").strip() + extra_note,
                producer_bounded=True,
                complete=continuation is None,
                continuation=continuation,
                diagnostics=diagnostics,
            )
            execution.followup_context = envelope.content
            execution.followup_envelope = envelope
        return execution

    def _execute_continuation(self, *, cursor: str, context: ToolExecutionContext) -> ToolExecutionResult:
        from ..paged_reading import (
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            page_failure_feedback,
            parse_json_payload,
            parse_paged_cursor,
            slice_page,
        )

        owner_binding = self._inspection_owner_binding(context)
        payload = parse_json_payload(parse_paged_cursor(cursor, tool="gf", binding=owner_binding))
        if not isinstance(payload, dict):
            return self._continuation_failure("cursor_invalid", "cursor could not be decoded for this owner/session")
        target = str(payload.get("t") or "").strip()
        section = str(payload.get("s") or "content").strip()
        try:
            offset = max(0, int(payload.get("o") or 0))
        except (TypeError, ValueError):
            return self._continuation_failure("cursor_invalid", "cursor offset is invalid")
        expected_fingerprint = str(payload.get("f") or "")
        if not target:
            return self._continuation_failure("cursor_invalid", "cursor payload has no target")
        result = self.generated_file_service.inspect_generated_file(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            target=target,
            section=section,
            max_chars=40000,
        )
        if not bool(result.get("ok")):
            return self._continuation_failure(
                str((result or {}).get("error") or "generated_file_not_found"),
                "generated file could not be re-read for continuation",
            )
        generated = result.get("generated") if isinstance(result.get("generated"), dict) else {}
        absolute_path = generated.get("absolute_path")
        if not absolute_path:
            return self._continuation_failure("source_missing", "generated file body is missing on disk")
        try:
            resolved_path = Path(str(absolute_path))
            if not resolved_path.exists() or not resolved_path.is_file():
                return self._continuation_failure("source_missing", "generated file body is missing on disk")
            fingerprint = f"{int(resolved_path.stat().st_size)}:{int(resolved_path.stat().st_mtime_ns)}"
        except OSError:
            return self._continuation_failure("source_missing", "generated file body is unreadable")
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return self._continuation_failure("stale_cursor", "generated file changed since the previous page")
        output_format = str(generated.get("output_format") or "").strip()
        service = self.generated_file_service
        text = str(
            service._read_generated_text_material(path=resolved_path, output_format=output_format, max_chars=4_000_000)
            or ""
        )
        if not text:
            return self._continuation_failure("unavailable", "generated file has no paged text material")
        if len(text) <= offset:
            return self._continuation_failure("stale_cursor", "generated file shrank since the previous page")
        page_text, next_offset, total_chars = slice_page(
            text,
            start=offset,
            budget_chars=NEXT_PAGE_BUDGET_CHARS,
            budget_lines=NEXT_PAGE_BUDGET_LINES,
        )
        complete = next_offset >= total_chars
        lines = [
            "【生成文件续读结果】",
            f"目标：{target}（section={section}）",
            page_text,
        ]
        continuation = None
        if not complete:
            next_cursor = self._issue_content_cursor(
                generated=generated,
                offset=next_offset,
                context=context,
            )
            if next_cursor:
                continuation = {"type": self.tool_type, "cursor": next_cursor}
                lines.append(
                    "正文还有未展示部分。如果当前内容已经足够，可以直接回答；"
                    f"只有确实需要后续正文时，才调用 inspect_generated_file(cursor=\"{next_cursor}\")。"
                )
            else:
                lines.append("正文还有未展示部分，但当前文件不满足连续读取条件。")
        else:
            lines.append(f"已读取该 section 的全部可读取正文（共 {total_chars} 字）。")
        content = "\n".join(lines)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "generated_file_inspected",
                    "generated_file": generated,
                    "inspection": {
                        "section": section,
                        "source_kind": output_format,
                        "truncated": not complete,
                        "content": page_text,
                    },
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=complete,
                continuation=continuation,
                diagnostics={
                    "section": section,
                    "shown_chars": len(page_text),
                    "total_chars": total_chars,
                    "complete": complete,
                },
            ),
        )

    def _issue_content_cursor(self, *, generated: dict[str, Any], offset: int, context: ToolExecutionContext) -> str:
        from ..paged_reading import cursor_binding, json_payload, make_paged_cursor

        absolute_path = generated.get("absolute_path")
        if not absolute_path:
            return ""
        try:
            resolved_path = Path(str(absolute_path))
            fingerprint = f"{int(resolved_path.stat().st_size)}:{int(resolved_path.stat().st_mtime_ns)}"
        except OSError:
            return ""
        target = str(generated.get("generated_id") or generated.get("generated_handle") or "")
        if not target:
            return ""
        return make_paged_cursor(
            tool="gf",
            binding=self._inspection_owner_binding(context),
            payload=json_payload({"t": target, "s": "content", "o": int(offset), "f": fingerprint}),
        )

    def _inspection_owner_binding(self, context: ToolExecutionContext) -> str:
        from ..paged_reading import cursor_binding

        return cursor_binding("inspect_generated_file", context.profile_user_id, context.session_id)

    def _continuation_failure(self, status: str, reason: str) -> ToolExecutionResult:
        from ..paged_reading import page_failure_feedback

        content = page_failure_feedback(status=status, tool=self.tool_type, detail=reason)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {"type": "generated_file_inspected", "status": status, "reason": reason}
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

    def _normalize_max_chars(self, value: Any) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = 12000
        return max(500, min(40000, parsed))


class ManageGeneratedFileToolHandler(BaseToolHandler):
    tool_type = "manage_generated_file"

    def __init__(self, *, generated_file_service, project_workspace_service=None) -> None:
        self.generated_file_service = generated_file_service
        self.project_workspace_service = project_workspace_service

    def build_prompt_instruction(self) -> str:
        return (
            '- manage_generated_file(action="register", path="report.md", cwd="可选项目目录")：'
            "把已有项目文件原样登记为 gen_* 产物，返回句柄和 SHA-256；不发送、不重写源文件。\n"
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
        if action == "register":
            path, cwd = value.get("path"), value.get("cwd", "")
            if not isinstance(path, str) or not path.strip() or not isinstance(cwd, str) or "\x00" in path + cwd:
                return None
            return {"type": self.tool_type, "action": action, "path": path.strip(), "cwd": cwd.strip()}
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
        if call.get("action") == "register":
            return self._register(call, context)
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
        if managed:
            events.append(
                {
                    "type": "generated_files_managed",
                    "action": str(result.get("action") or ""),
                    "managed": managed,
                    "unresolved": list(result.get("unresolved") or []),
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
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=events,
            followup_context=followup_context,
        )

    def _register(self, call, context):
        import json
        from pathlib import Path
        from ..project_workspace import ProjectWorkspaceError
        from .project_workspace import _ProjectWorkspaceHandler
        try:
            service = self.project_workspace_service
            if service is None:
                raise ProjectWorkspaceError("project_workspace_unconfigured")
            scope = _ProjectWorkspaceHandler(service=service)._scope(context)
            source = Path(call["path"])
            cwd = call.get("cwd") or ""
            if source.is_absolute():
                if cwd:
                    raise ProjectWorkspaceError("cwd_and_absolute_path_conflict")
                cwd, source = str(source.parent), Path(source.name)
            if not cwd and context.execution_scope:
                cwd = context.execution_scope.working_directory
            if not cwd:
                cwd = str((service.current(scope=scope) or {}).get("working_directory") or service.execution_workspace_root)
            root = service.output_directory(scope=scope, cwd=cwd)
            _relative, source = service._inspection_target(root, str(source), allow_root=False)
            generated = self.generated_file_service.register_workspace_artifact(
                source=source, root=root, profile_user_id=context.profile_user_id, session_id=context.session_id,
                created_by_tool=self.tool_type, timestamp=context.now_ts,
            )
            handle = generated["generated_handle"]
            data = {"status": "succeeded", "handle": handle, "name": source.name,
                    "size_bytes": generated["file_size"], "sha256": generated["sha256"]}
            return ToolExecutionResult(tool_type=self.tool_type,
                followup_context=json.dumps(data, ensure_ascii=False),
                stream_events=[{"type": "generated_file_ready", "generated_file": data}],
                state_updates={"generated_file_handles": [handle]})
        except Exception as exc:
            reason = exc.reason if isinstance(exc, ProjectWorkspaceError) else "artifact_registration_failed"
            return ToolExecutionResult(tool_type=self.tool_type,
                followup_context=f"<tool_use_error>File was not registered: {reason}.</tool_use_error>",
                stream_events=[{"type": "tool_execution_failed", "status": "failed", "reason": reason}])

    def _normalize_action(self, value: Any) -> str:
        action = str(value or "").strip().lower()
        aliases = {
            "register": "register",
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
