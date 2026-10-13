"""Focus rendering for AttachmentInboxService."""

from __future__ import annotations

from typing import Any
from .definitions import DEFAULT_WORKSPACE_CHAR_BUDGET, WORKSPACE_ITEM_CHAR_BUDGET, INTERNAL_ATTACHMENT_DETAIL_KEYS


class AttachmentFocusRenderingMixin:
    def _render_focus_items_with_budget(
        self,
        items: list[dict[str, Any]],
        *,
        total_budget: int,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        remaining = max(1200, int(total_budget or DEFAULT_WORKSPACE_CHAR_BUDGET))
        lines: list[str] = []
        overflow: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if remaining < 800 and lines:
                overflow.append(item)
                continue
            item_budget = min(WORKSPACE_ITEM_CHAR_BUDGET, max(800, remaining))
            item_lines = self._render_detail_item(index, item, char_budget=item_budget)
            rendered = "\n".join(item_lines)
            cost = len(rendered)
            if cost > remaining and lines:
                overflow.append(item)
                continue
            lines.extend(item_lines)
            remaining -= min(cost, remaining)
        return lines, overflow

    def _render_detail_item(
        self, index: int, item: dict[str, Any], *, char_budget: int = WORKSPACE_ITEM_CHAR_BUDGET
    ) -> list[str]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        lines = [f"{index}. {self._compact_item_label(item)}"]
        time_anchor = self._item_time_anchor(item)
        if time_anchor:
            lines.append(f"   时间：{time_anchor}")
        if item.get("status") == "pending_observation":
            lines.append("   状态：系统正在生成描述，还没有完整内容。")
            return lines
        if item.get("status") == "failed":
            error = str(item.get("error_message") or "").strip()
            readable_error = self._readable_failure_message(item) or error
            lines.append("   状态：处理失败。" + (f"原因：{readable_error}" if readable_error else ""))
            return lines
        lines.append("   状态：已放在当前工作台，可直接基于下面内容讨论。")
        source_label = self._source_label(item)
        if source_label:
            lines.append(f"   来源：{source_label}")
        sender_label = self._sender_label(item)
        if sender_label:
            lines.append(f"   发送者：{sender_label}")
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "image":
            lines.extend(self._render_image_focus_detail(detail, fallback_hint=str(item.get("short_hint") or "")))
            return self._clip_rendered_lines(lines, char_budget)

        media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
        if media_info:
            lines.extend(self._render_media_info_lines(media_info))
            remote_source = detail.get("remote_source") if isinstance(detail.get("remote_source"), dict) else {}
            if remote_source:
                lines.extend(self._render_remote_source_lines(remote_source))
            lines.append(
                "   说明：这是轻量媒体规格卡，只表示材料可用，不代表存在待处理任务；只有用户当前明确提出处理目标时才调用对应工具。"
            )
            return self._clip_rendered_lines(lines, char_budget)

        tags = self._normalize_text_list(detail.get("mood_tags") or detail.get("tags") or detail.get("keywords") or [])
        if tags:
            lines.append(f"   标签：{', '.join(tags[:8])}")

        entities = self._normalize_text_list(detail.get("entities") or detail.get("objects") or [])
        if entities:
            lines.append(f"   要素：{', '.join(entities[:8])}")

        content_budget = max(600, char_budget - len("\n".join(lines)) - 160)
        original = self._extract_original_file_result(item, section="全文", max_chars=content_budget)
        content = original.text
        partial_preview = False
        if not content:
            content = str(detail.get("text_preview") or detail.get("content_preview") or "").strip()[:content_budget]
            partial_preview = bool(detail.get("preview_is_truncated"))
        if content:
            lines.append("   内容：")
            lines.append(self._indent_block(content, prefix="   "))
            if partial_preview:
                lines.append("   注意：这里只展开了预览；需要完整内容时，用 read_attachment_section 指定页、行或 sheet 继续读取。")
            if len(content) >= content_budget - 20:
                lines.append(
                    "   注意：当前工作台内容已接近预算边界；如需更具体位置，用 read_attachment_section 指定页、行或 sheet 展开。"
                )
        else:
            hint = str(item.get("short_hint") or detail.get("summary") or detail.get("description") or "").strip()
            if hint:
                lines.append(f"   说明：{hint[:360]}")
        return self._clip_rendered_lines(lines, char_budget)

    def _render_image_focus_detail(self, detail: dict[str, Any], *, fallback_hint: str = "") -> list[str]:
        lines: list[str] = []
        title = str(detail.get("summary_title") or "").strip()
        if title:
            lines.append(f"   临时标题：{title}")
        description_parts = []
        for key in ("summary", "description", "scene_description", "appearance", "composition"):
            text = str(detail.get(key) or "").strip()
            if text and text not in description_parts:
                description_parts.append(text)
        fallback = str(fallback_hint or "").strip()
        if fallback and fallback not in description_parts:
            description_parts.append(fallback)
        if description_parts:
            lines.append("   视觉描述：" + "\n   ".join(description_parts))
        visible_text = self._normalize_text_list(detail.get("visible_text") or [])
        if visible_text:
            lines.append(f"   可见文字：{', '.join(visible_text[:12])}")
        concrete_details = self._normalize_text_list(detail.get("concrete_details") or detail.get("details") or [])
        if concrete_details:
            lines.append(f"   具体细节：{', '.join(concrete_details[:16])}")
        entities = self._normalize_text_list(detail.get("entities") or detail.get("objects") or [])
        if entities:
            lines.append(f"   要素：{', '.join(entities[:24])}")
        tags = self._normalize_text_list(detail.get("mood_tags") or detail.get("tags") or detail.get("keywords") or [])
        if tags:
            lines.append(f"   标签：{', '.join(tags[:24])}")
        uncertainty_items = self._normalize_text_list(detail.get("uncertainty") or [])
        if uncertainty_items:
            lines.append(f"   不确定处：{', '.join(uncertainty_items[:12])}")
        extra_keys = [
            key
            for key in sorted(detail.keys())
            if key
            not in {
                "summary_title",
                "summary",
                "description",
                "scene_description",
                "appearance",
                "composition",
                "visible_text",
                "concrete_details",
                "details",
                "entities",
                "objects",
                "mood_tags",
                "tags",
                "keywords",
                "uncertainty",
                *INTERNAL_ATTACHMENT_DETAIL_KEYS,
            }
        ]
        for key in extra_keys[:8]:
            value = detail.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                lines.append(f"   {key}: {str(value).strip()[:500]}")
            elif isinstance(value, list) and value:
                values = self._normalize_text_list(value)
                if values:
                    lines.append(f"   {key}: {', '.join(values[:16])}")
        return lines or ["   视觉描述：当前图片已在工作台，但观察卡没有提供更多细节。"]
