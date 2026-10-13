"""Followups for AttachmentInboxService."""

from __future__ import annotations

from typing import Any
from .definitions import DEFAULT_WORKSPACE_CHAR_BUDGET, WORKSPACE_MAX_TARGETS


class AttachmentFollowupsMixin:
    def _build_inspect_followup(self, item: dict[str, Any]) -> str:
        lines = [
            "你刚刚查看了一份工作台材料：",
            self._compact_item_label(item),
        ]
        source_label = self._source_label(item)
        if source_label:
            lines.append(f"来源：{source_label}")
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        hint = str(item.get("short_hint") or detail.get("summary") or detail.get("description") or "").strip()
        if hint:
            lines.append(f"印象：{hint}")
        if item.get("status") == "failed":
            failure_message = self._readable_failure_message(item)
            if failure_message:
                lines.append(f"失败原因：{failure_message}")
            source_access = item.get("source_access") if isinstance(item.get("source_access"), dict) else {}
            if source_access.get("managed_copy_available"):
                lines.append("重试条件：受管工作区里仍有原文件，可用 retry_attachment 重新处理。")
            elif source_access.get("locator_available"):
                lines.append(
                    "重试条件：宿主仍保留这个附件的私有来源定位，可用 retry_attachment 重试原始来源；"
                    "定位值本身不会进入对话。"
                )
        tags = self._normalize_text_list(detail.get("mood_tags") or detail.get("tags") or [])
        if tags:
            lines.append(f"标签：{', '.join(tags[:10])}")
        media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
        if media_info:
            lines.append("媒体规格：")
            lines.extend(self._render_media_info_lines(media_info, prefix="  "))
        remote_source = detail.get("remote_source") if isinstance(detail.get("remote_source"), dict) else {}
        if remote_source:
            lines.extend(self._render_remote_source_lines(remote_source, prefix="  "))
        preview = str(detail.get("text_preview") or detail.get("content_preview") or "").strip()
        if preview:
            excerpt_limit = 600
            lines.append(f"文本摘录：{preview[:excerpt_limit]}")
            if detail.get("preview_is_truncated"):
                lines.append(
                    "注意：这只是预览，不是全文；如果要处理完整文件，应继续用 read_attachment_section 展开指定范围。"
                )
            elif len(preview) > excerpt_limit:
                lines.append(
                    "注意：这只是本次提示词节选，不是全文；忠实转换/导出原附件时不要复制这段节选，应让文件工具读取原始附件。"
                )
        if str(item.get("kind") or "").strip().lower() == "image":
            handle = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
            target = handle or "该图片的精确 handle"
            lines.append(
                "查看边界：本次 inspect_attachment 结果不包含原图像素。"
                f"如果需要实际看图、识别文字或核对视觉细节，请调用 load_material(targets=[\"{target}\"])；"
                "已有描述只能作为摘要使用。"
            )
        lines.append("请基于当前真实可见的信息自然回应；如果用户聊完了，可以稍后用 clear_attachment_focus 移除它。")
        return "\n".join(lines)

    def _build_workspace_followup(
        self,
        *,
        focused: list[dict[str, Any]],
        unresolved: list[str],
        ambiguous_targets: list[str],
        overflow: list[dict[str, Any]],
        reason: str,
    ) -> str:
        if not focused:
            lines = ["你刚刚同步了材料工作台，但没有成功放入任何材料。"]
        else:
            lines = [f"你刚刚把 {len(focused)} 个材料放到了当前工作台，接下来请只细看这些材料："]
            rendered, budget_overflow = self._render_focus_items_with_budget(
                focused,
                total_budget=max(12000, DEFAULT_WORKSPACE_CHAR_BUDGET // 2),
            )
            lines.extend(rendered)
            if budget_overflow:
                lines.append(
                    "以下材料已经进入工作台，但本次工具回执空间不够完整展开："
                    + "、".join(self._compact_item_label(item) for item in budget_overflow[:8])
                    + "。下一轮系统提示词会继续按工作台预算挂载；必要时可用 read_attachment_section 精确展开。"
                )
        if unresolved:
            lines.append(f"没有找到这些目标：{', '.join(unresolved[:5])}。")
        if ambiguous_targets:
            lines.append("这些目标不够明确，存在多个候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。")
        if overflow:
            lines.append(
                f"本次目标超过工作台软上限 {WORKSPACE_MAX_TARGETS} 个，以下目标没有展开："
                + "、".join(self._compact_item_label(item) for item in overflow[:5])
            )
        if str(reason or "").strip():
            lines.append(f"本次同步原因：{str(reason).strip()[:160]}")
        lines.append("请基于当前材料工作台自然回应，不要再次重复同步同一批材料。")
        return "\n".join(lines)
