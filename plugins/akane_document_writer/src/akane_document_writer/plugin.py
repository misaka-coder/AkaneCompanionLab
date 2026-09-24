"""Optional document capabilities, bound only to the public plugin SDK."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginResourceResult,
    PluginResultExperience,
    PluginResultPayload,
)
from companion_v01.plugin_subprocess import PluginProcessRunner, drain

from .validation import DocumentError, FORMATS

PLUGIN_ID = "akane.document-writer"
CAPABILITIES = {name: f"{PLUGIN_ID}.{name}.v1" for name in ("compose", "revise", "style")}
PERMISSIONS = ("capability.prompt.invoke", "resource.read", "artifact.write")
MAX_BYTES = 50_000_000
MIMES = {
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "json": "application/json",
    "csv": "text/csv",
    "srt": "application/x-subrip",
    "lrc": "text/plain",
    "vtt": "text/vtt",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
STYLE_DESCRIPTION = (
    "严格样式规则；bold/italic 用布尔，font_color/fill_color/highlight_color 用 RGB 或颜色名。"
    "支持 header，columns[{column_index或match_header,...}]，rows[{row或row_start/row_end,...}]，"
    "cells[{row,column_index,...}]，paragraphs[{paragraph_index或contains,...}]，highlights[{text,...}]。"
    "条件行示例 row_rules:[{where:{match_header:'数量',gte:1},font_color:'red'}]；"
    "where 用 column 或 match_header 加一个 eq/ne/lt/lte/gt/gte/contains 条件。"
    "序号从 1 开始；多表 Word 指定 table_index，多工作表 Excel 指定 sheet_name；Excel 可设 auto_width。"
    "highlights 修改整个匹配段落/单元格，不是子串；paragraphs 仅用于 Word。"
)


def descriptors():
    common = (
        CapabilityIOSlot("output_title", "string", raw={"maxLength": 80}),
        CapabilityIOSlot("formatting", "object", raw={"description": STYLE_DESCRIPTION}),
        CapabilityIOSlot(
            "send_to_user", "boolean", raw={"description": "默认 false，只登记；true 请求文件交付，不代表已发送。"}
        ),
    )
    content = (
        CapabilityIOSlot(
            "content_markdown",
            "string",
            raw={"maxLength": 80000, "description": "完整最终正文；Word/PDF 为小型 Markdown 子集。"},
        ),
        CapabilityIOSlot(
            "table_rows",
            "array",
            raw={
                "maxItems": 1000,
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": ["string", "number", "boolean", "null"]},
                },
                "description": "完整表格，数字/布尔值保留类型；字符串不是公式。",
            },
        ),
    )
    for operation, label, hint, inputs in (
        (
            "compose",
            "创建文档或表格",
            "把已经整理好的完整 content_markdown/table_rows 渲染成新文件；不执行总结、改写或代码。"
            "来源用 source_ids，reference 只关联来源，full_text 导出来源的完整可提取文本（此时不填正文/表格）。"
            "full_text 不保证原版式，检测到图片/编号等未提取内容时失败，不能用短预览补全；多表转 CSV/XLSX 必须先明确选择并提供表格。"
            "JSON 必须合法，字幕内容须自行提供真实时间；复杂代码项目应走已有 Shell/编码能力并验证。",
            (
                *content,
                CapabilityIOSlot(
                    "source_ids",
                    "array",
                    raw={"maxItems": 20, "uniqueItems": True, "items": {"type": "string", "maxLength": 512}},
                ),
                CapabilityIOSlot("source_mode", "string", raw={"enum": ("reference", "full_text")}),
                CapabilityIOSlot("output_format", "string", required=True, raw={"enum": sorted(FORMATS)}),
            ),
        ),
        (
            "revise",
            "修订生成文件",
            "为明确的 gen_* 生成文件创建新版本，旧版本保留；必须传完整最终 content_markdown/table_rows，"
            "不是补丁或一条修改指令，不从短预览猜全文。output_format 省略时继承原格式；重新渲染不保证旧版式。"
            "只改样式请用样式能力。不会执行代码或验证程序正确性。",
            (
                CapabilityIOSlot("target", "string", required=True, raw={"maxLength": 128}),
                *content,
                CapabilityIOSlot("output_format", "string", raw={"enum": sorted(FORMATS)}),
            ),
        ),
        (
            "style",
            "修改现有 Word 或 Excel 样式",
            "只修改当前 docx/xlsx 的实际格式规则，另存新文件，保留正文和已有公式；不改内容。"
            "target 只用会话材料句柄；复杂签名、宏/嵌入对象或无法保留的扩展明确失败，不冒充保真。",
            (CapabilityIOSlot("target", "string", required=True, raw={"maxLength": 512}),),
        ),
    ):
        yield CapabilityDescriptor(
            id=CAPABILITIES[operation],
            display_name=label,
            short_hint=hint,
            visible_in=("desktop", "qq"),
            prompt_exposed=True,
            risk="low",
            confirm="never",
            effects=("filesystem",),
            trigger=None,
            inputs=(*inputs, *common),
            outputs=(CapabilityIOSlot("file", "file", required=True, max_bytes=MAX_BYTES, delivery="generated_file"),),
            raw={
                "execution_class": "long_task",
                "followup": "required",
                "memory_mode": "timeline",
                "idempotency": "effectful",
            },
        )


def label(value, maximum=512):
    if not isinstance(value, str) or len(value) > maximum or any(char in value for char in "\x00\r\n/\\"):
        raise DocumentError("document_label_invalid")
    return value.strip()


class DocumentWriter:
    provider_id = "provider.akane.document-writer"

    def __init__(self, resources):
        self.resources = resources
        self.python = shutil.which(os.environ.get("AKANE_DOCUMENT_PYTHON") or sys.executable)
        self._active = {}
        self._closed = False

    async def _worker(self, argument, runner):
        if not self.python:
            raise DocumentError("document_python_unavailable")
        code, wire = await runner.run(
            [self.python, Path(__file__).with_name("worker.py"), argument], capture=True, timeout=120
        )
        try:
            result = json.loads(wire)
        except (ValueError, UnicodeError):
            raise DocumentError("document_worker_response_invalid") from None
        if not isinstance(result, dict):
            raise DocumentError("document_worker_response_invalid")
        if code or result.get("ok") is not True:
            reason = result.get("reason")
            raise DocumentError(
                reason
                if isinstance(reason, str) and re.fullmatch(r"document_[a-z0-9_]{1,100}", reason)
                else "document_execution_failed"
            )
        if not isinstance(result.get("result"), dict):
            raise DocumentError("document_worker_response_invalid")
        return result["result"]

    async def health(self):
        if self._closed:
            return HealthStatus(False, "unavailable", "document_plugin_closed")
        if "representation" not in PluginResourceResult.__dataclass_fields__ or not callable(
            getattr(self.resources, "work_directory", None)
        ):
            return HealthStatus(False, "unavailable", "document_resource_sdk_required")
        runner, task = PluginProcessRunner(), asyncio.current_task()
        self._active[task] = runner
        try:
            await self._worker("health", runner)
            return HealthStatus(True, "ready")
        except DocumentError as error:
            return HealthStatus(False, "unavailable", str(error))
        except (OSError, asyncio.TimeoutError):
            return HealthStatus(False, "unavailable", "document_runtime_unavailable")
        finally:
            await runner.aclose()
            self._active.pop(task, None)

    async def list_capabilities(self):
        return tuple(descriptors())

    async def invoke(self, capability_id, args, context):
        operation = next((name for name, value in CAPABILITIES.items() if value == capability_id), None)
        if operation is None:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        runner, task = PluginProcessRunner(), asyncio.current_task()
        self._active[task] = runner
        try:
            if self._closed:
                raise DocumentError("document_plugin_closed")
            if not context.profile_user_id or not context.session_id:
                raise DocumentError("document_context_required")
            title = label(args.get("output_title", ""), 80)
            send = args.get("send_to_user", False)
            if type(send) is not bool:
                raise DocumentError("document_delivery_invalid")
            mode = args.get("source_mode", "reference")
            if mode not in {"reference", "full_text"}:
                raise DocumentError("document_source_mode_invalid")
            targets = args.get("source_ids", []) if operation == "compose" else [args.get("target", "")]
            if not isinstance(targets, list) or len(targets) > 20 or any(not label(value) for value in targets):
                raise DocumentError("document_sources_invalid")
            if len(set(targets)) != len(targets):
                raise DocumentError("document_sources_invalid")
            content, rows = args.get("content_markdown", ""), args.get("table_rows")
            if operation == "revise" and not (targets[0].startswith("gen_") or targets[0].startswith("generated::")):
                raise DocumentError("document_generated_target_required")
            if operation != "style" and mode == "reference" and not content and not rows:
                raise DocumentError("document_content_required")
            if mode == "full_text" and (not targets or content or rows):
                raise DocumentError("document_source_content_ambiguous")
            sources = []
            for target in targets:
                source = await self.resources.open(
                    target, representation="document" if mode == "full_text" else "original"
                )
                if not source.ok:
                    return CapabilityResult(is_error=True, status=source.status, reason=source.reason)
                sources.append(source)
            handles = tuple(dict.fromkeys(source.handle for source in sources))
            if len(handles) != len(sources):
                raise DocumentError("document_duplicate_source")
            if sources:
                title = title or Path(sources[0].name).stem[:80]
            title = title or "文档"
            fmt = args.get("output_format") or (
                Path(sources[0].name).suffix.removeprefix(".").lower() if operation != "compose" else ""
            )
            if fmt not in FORMATS or operation == "style" and fmt not in {"docx", "xlsx"}:
                raise DocumentError("document_format_invalid")
            root = await self.resources.work_directory()
            output = root / f"document-{uuid4().hex}.{fmt}"
            request = {
                "operation": "style" if operation == "style" else "sources" if mode == "full_text" else "render",
                "output_path": str(output),
                "output_format": fmt,
                # A source export must not invent a second document heading
                # merely because its filename supplies an artifact label.
                "output_title": title if mode != "full_text" or args.get("output_title") else "",
                "content_markdown": content,
                "table_rows": rows,
                "formatting": args.get("formatting"),
            }
            if operation == "style":
                request["source_path"] = str(sources[0].path)
            elif mode == "full_text":
                request["material_paths"] = [str(source.path) for source in sources]
            request_path = root / f"request-{uuid4().hex}.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            result = await self._worker(request_path, runner)
            if result.get("status") != "ok" or not output.is_file() or not 0 < output.stat().st_size <= MAX_BYTES:
                raise DocumentError("document_output_invalid")
            revision = sources[0].handle if operation != "compose" and sources[0].handle.startswith("gen_") else ""
            notices = result.get("notices", [])
            summary = {"compose": "已生成文档", "revise": "已生成修订版", "style": "已另存样式修改版"}[
                operation
            ] + f"（{fmt}）。"
            facts = ["原始文件未修改；文件登记与发送成功是不同状态。"]
            if "source_text_export_not_original_layout" in notices:
                facts.append("这是完整可提取文本的重新排版，不是原版式复制。")
            if "source_formulas_exported_as_text_not_evaluated" in notices:
                facts.append("原公式已作为文字导出，没有计算，也不会作为公式执行。")
            if "source_dates_exported_as_iso_text" in notices:
                facts.append("原日期已作为 ISO 日期文字导出。")
            if "csv_formula_like_text_preserved_use_text_import" in notices:
                facts.append("CSV 含公式样文本，使用表格软件打开时应按文本导入。")
            return CapabilityResult(
                is_error=False,
                status="ok",
                content=ManagedArtifactPayload(
                    artifacts=(
                        ManagedArtifactDraft(
                            path=output,
                            title=title,
                            output_format=fmt,
                            mime_type=MIMES[fmt],
                            summary=summary,
                            send_to_user=send,
                            source_handles=handles,
                            revision_of=revision,
                        ),
                    ),
                    content=PluginResultPayload(
                        content={
                            "operation": operation,
                            "output_format": fmt,
                            "source_handles": handles,
                            "revision_of": revision,
                            "notices": notices,
                        },
                        experience=PluginResultExperience(summary=summary, facts=tuple(facts)),
                    ),
                ),
            )
        except DocumentError as error:
            return CapabilityResult(is_error=True, status="error", reason=str(error))
        except asyncio.TimeoutError:
            return CapabilityResult(is_error=True, status="error", reason="document_processing_timeout")
        except (OSError, ValueError, TypeError, KeyError):
            return CapabilityResult(is_error=True, status="error", reason="document_execution_failed")
        finally:
            await runner.aclose()
            self._active.pop(task, None)

    async def aclose(self):
        self._closed = True
        tasks = tuple(self._active)
        for task in tasks:
            task.cancel()
        if tasks:
            await drain(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))


class DocumentWriterPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(DocumentWriter(registrar.get_resource_port()))


def create_plugin():
    return DocumentWriterPlugin()
