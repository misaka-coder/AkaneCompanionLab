"""Generated media and file handoff tool handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config

from ..capability_registry import GENERATE_IMAGE_TOOL_SPEC
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    operation_tool_result,
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
            "它适合文档、简单网页和短小自包含文件；返回成功只证明文件已生成，不证明内容可运行。"
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
