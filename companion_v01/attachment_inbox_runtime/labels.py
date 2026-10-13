"""Labels for AttachmentInboxService."""

from __future__ import annotations

import re
import time
from typing import Any
from .definitions import WORKSPACE_ITEM_CHAR_BUDGET


class AttachmentLabelsMixin:
    def _clip_rendered_lines(self, lines: list[str], char_budget: int) -> list[str]:
        budget = max(300, int(char_budget or WORKSPACE_ITEM_CHAR_BUDGET))
        rendered: list[str] = []
        used = 0
        for line in lines:
            text = str(line or "")
            remaining = budget - used
            if remaining <= 0:
                break
            if len(text) + 1 <= remaining:
                rendered.append(text)
                used += len(text) + 1
                continue
            if remaining > 80:
                rendered.append(text[: remaining - 32] + "\n   ...（此附件内容因工作台预算暂时截断）")
            break
        return rendered

    def _indent_block(self, text: str, *, prefix: str) -> str:
        return "\n".join(prefix + line if line else prefix.rstrip() for line in str(text or "").splitlines())

    def _format_size(self, size: int) -> str:
        value = max(0, int(size or 0))
        if value < 1024:
            return f"{value}B"
        if value < 1024 * 1024:
            return f"{value / 1024:.1f}KB"
        return f"{value / (1024 * 1024):.1f}MB"

    def _format_duration_label(self, value: Any) -> str:
        if not isinstance(value, (int, float)):
            return ""
        seconds = max(0, int(round(float(value))))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _format_bitrate(self, value: int) -> str:
        if value <= 0:
            return ""
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f}Mbps"
        return f"{value / 1000:.0f}kbps"

    def _format_time_anchor(self, value: Any) -> str:
        try:
            timestamp = int(value or 0)
        except (TypeError, ValueError):
            return ""
        if timestamp <= 0:
            return ""
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
        except (OSError, OverflowError, ValueError):
            return ""

    def _item_time_anchor(self, item: dict[str, Any]) -> str:
        created_label = self._format_time_anchor(item.get("created_at"))
        updated_label = self._format_time_anchor(item.get("updated_at"))
        if created_label and updated_label and updated_label != created_label:
            return f"加入 {created_label}；摘要/状态更新 {updated_label}"
        if created_label:
            return f"加入 {created_label}"
        if updated_label:
            return f"摘要/状态更新 {updated_label}"
        return ""

    def _source_label(self, item: dict[str, Any]) -> str:
        source = str(item.get("source") or "").strip().lower()
        if not source:
            return ""
        labels = {
            "qq": "QQ",
            "desktop_pet": "桌宠本地",
            "remote_url": "链接下载",
            "web": "Web",
        }
        return labels.get(source, str(item.get("source") or "").strip()[:40])

    def _sender_label(self, item: dict[str, Any]) -> str:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        for key in ("sender_label", "source_sender_label", "qq_sender_label"):
            label = self._safe_prompt_label(detail.get(key))
            if label:
                return label
        return ""

    def _safe_prompt_label(self, value: Any) -> str:
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text).strip()
        return text[:48]

    def _compact_item_label(self, item: dict[str, Any]) -> str:
        attachment_id = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
        kind = self._kind_label(item.get("kind"))
        name = self._display_name(item)
        origin = str(item.get("origin_name") or "").strip()
        seq_label = self._sequence_label(item)
        seq_part = f"（{seq_label}）" if seq_label else ""
        if origin and origin != name:
            return f"[{attachment_id}]{seq_part} {kind}《{name}》({origin})"
        return f"[{attachment_id}]{seq_part} {kind}《{name}》"

    def _display_name(self, item: dict[str, Any]) -> str:
        return (
            str(item.get("summary_title") or "").strip()
            or str(item.get("origin_name") or "").strip()
            or str(item.get("attachment_id") or "").strip()
            or "未命名附件"
        )

    def _activity_display_name(self, item: dict[str, Any]) -> str:
        title = str(item.get("summary_title") or "").strip()
        if not title:
            origin = str(item.get("origin_name") or "").strip().replace("\\", "/")
            title = origin.rsplit("/", 1)[-1]
        return self._safe_prompt_label(title)

    def _item_summary(self, item: dict[str, Any]) -> str:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        if item.get("status") == "failed":
            return self._readable_failure_message(item) or str(item.get("error_message") or "").strip()[:160]
        return str(item.get("short_hint") or detail.get("summary") or detail.get("description") or "").strip()[:160]

    def _readable_failure_message(self, item: dict[str, Any]) -> str:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        failure = detail.get("failure") if isinstance(detail.get("failure"), dict) else {}
        reason = str(failure.get("reason") or failure.get("message") or "").strip()
        if reason:
            return reason[:220]
        error = str(item.get("error_message") or "").strip()
        if not error:
            return ""
        lowered = error.lower()
        if "bad request" in lowered or "400 client error" in lowered:
            return "QQ 临时链接返回 400，请稍后重试，或让系统尝试从 NapCat 本地缓存重新读取。"
        if "视觉模型" in error or "vision" in lowered:
            return "图片已接收，但视觉模型暂时不可用。"
        if "timeout" in lowered or "timed out" in lowered:
            return "读取附件超时，可能是网络或 NapCat 缓存暂时不可用。"
        if "too large" in lowered or "附件过大" in error:
            return "附件超过当前大小限制，请压缩后重发或换一种方式发送。"
        return "附件处理失败，请稍后重试或换一种方式发送。"

    def _kind_label(self, value: Any) -> str:
        kind = str(value or "").strip().lower()
        if kind == "image":
            return "图片"
        if kind == "audio":
            return "音频"
        if kind == "document":
            return "文档"
        return "文件"

    def _sequence_label(self, item: dict[str, Any]) -> str:
        sequence_no = int(item.get("sequence_no") or 0)
        if sequence_no <= 0:
            return ""
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "image":
            return f"第{sequence_no}张图"
        if kind == "audio":
            return f"第{sequence_no}段音频"
        return f"第{sequence_no}个文件"

    def _normalize_text_list(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.replace("，", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(item or "").strip() for item in value]
        else:
            return []
        result: list[str] = []
        for item in raw_items:
            if item and item not in result:
                result.append(item[:40])
        return result
