"""Context for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import json
from .definitions import ACTIVE_ATTACHMENT_STATUSES, DEFAULT_WORKSPACE_CHAR_BUDGET


class AttachmentContextMixin:
    @private_asset_scope
    def build_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        detail_limit: int = 2,
        index_limit: int = 12,
        pending_limit: int = 3,
    ) -> str:
        items = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=list(ACTIVE_ATTACHMENT_STATUSES),
            limit=max(60, detail_limit + index_limit + pending_limit + 12),
        )
        focused = sorted(
            [item for item in items if int(item.get("focus_rank") or 0) > 0],
            key=lambda item: int(item.get("focus_rank") or 0),
        )
        ready = [item for item in items if item.get("status") == "ready"]
        ready_images = [item for item in ready if str(item.get("kind") or "").strip().lower() == "image"]
        latest_ready_image = (
            max(
                ready_images,
                key=lambda item: (
                    int(item.get("created_at") or 0),
                    int(item.get("updated_at") or 0),
                    int(item.get("sequence_no") or 0),
                ),
            )
            if ready_images
            else None
        )
        pending = [item for item in items if item.get("status") == "pending_observation"]
        ready_remote_sources = {
            str(item.get("source_event_id") or "").strip()
            for item in ready
            if str(item.get("source") or "").strip() == "remote_url" and str(item.get("source_event_id") or "").strip()
        }
        failed = [
            item
            for item in items
            if item.get("status") == "failed"
            and not (
                str(item.get("source") or "").strip() == "remote_url"
                and str(item.get("source_event_id") or "").strip() in ready_remote_sources
            )
        ]

        if not ready and not pending and not failed:
            return ""

        lines = [
            "【当前材料工作台】",
            "这些是当前会话临时放进工作台的原始材料，可能来自 QQ、桌宠本地拖拽、链接下载或其他客户端；只用于近几轮处理/讨论。",
            "不要把这些材料当成礼物、角色资源、生成成果或长期记忆。原始材料和工具生成成果是两类对象：要修改、转写、转换或整理材料时，先使用对应工具生成新成果；用户要拿到某个已有文件时，再按当前客户端可用的交付方式发送或打开它。",
        ]

        if latest_ready_image is not None:
            latest_image_id = str(latest_ready_image.get("attachment_id") or "")
            detailed_items = [
                latest_ready_image,
                *[item for item in focused if str(item.get("attachment_id") or "") != latest_image_id],
            ]
        else:
            detailed_items = focused
        detailed_ids = {str(item.get("attachment_id") or "") for item in detailed_items}
        budget_overflow = []
        if detailed_items:
            lines.append("当前重点材料 Focus（这些材料会持续放在你眼前，直到重新同步或清理）：")
            rendered, budget_overflow = self._render_focus_items_with_budget(
                detailed_items,
                total_budget=DEFAULT_WORKSPACE_CHAR_BUDGET,
            )
            lines.extend(rendered)
            if budget_overflow:
                detailed_ids.difference_update(str(item.get("attachment_id") or "") for item in budget_overflow)
                lines.append(
                    "工作台空间暂时不够完整展开："
                    + "、".join(self._compact_item_label(item) for item in budget_overflow[:8])
                    + "。这些材料仍可按 handle 使用，可用 read_attachment_section 指定页/行/sheet 展开。"
                )

        index_candidates = self._dedupe_attachment_items([
            *[item for item in budget_overflow if item.get("status") == "ready"],
            *[item for item in ready if str(item.get("attachment_id") or "") not in detailed_ids],
        ])
        indexed_ready = index_candidates[:max(0, int(index_limit or 6))]
        if indexed_ready:
            lines.append("旁边的材料清单 Manifest（只用于识别和选择，不代表已经阅读全文/全片）：")
            for index, item in enumerate(indexed_ready, start=1):
                lines.extend(self._render_manifest_item(index, item))

        if pending:
            lines.append("正在处理、还没有完整描述的材料：")
            for item in pending[: max(1, int(pending_limit or 3))]:
                lines.append(f"- {self._compact_item_label(item)}")

        if failed:
            lines.append("处理失败的材料：")
            for item in failed[:2]:
                label = self._compact_item_label(item)
                error = self._readable_failure_message(item)
                lines.append(f"- {label}" + (f"：{error}" if error else ""))

        overflow = max(
            0, len(ready) - len([
                item for item in detailed_items
                if item.get("status") == "ready" and str(item.get("attachment_id") or "") in detailed_ids
            ]) - len(indexed_ready)
        )
        if overflow:
            lines.append(f"此外还有 {overflow} 个材料未展开。")
        lines.append(
            "需要正文时使用 read_attachment_section；需要查看图片时使用 load_material；"
            "聊完或用户说不用了，可用 clear_attachment_focus 清理。"
        )
        return "\n".join(lines)

    @private_asset_scope
    def build_activity_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        kind: str = "any",
    ) -> str:
        """Render the active material handles without repeating their contents.

        Main chat only needs to know which source materials are available and
        whether they are ready.  Full observations and file contents remain
        available through the attachment tools and ``build_prompt_context``
        for consumers such as background tasks that explicitly need them.
        """

        items = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=list(ACTIVE_ATTACHMENT_STATUSES),
            limit=None,
        )
        items = self._filter_items_by_target_kind(items, "", kind)
        ready_remote_sources = {
            str(item.get("source_event_id") or "").strip()
            for item in items
            if item.get("status") == "ready"
            and str(item.get("source") or "").strip() == "remote_url"
            and str(item.get("source_event_id") or "").strip()
        }
        visible_items = [
            item
            for item in items
            if not (
                item.get("status") == "failed"
                and str(item.get("source") or "").strip() == "remote_url"
                and str(item.get("source_event_id") or "").strip() in ready_remote_sources
            )
        ]
        if not visible_items:
            return ""

        lines = [
            "attachment.workspace",
            "以下是当前会话可按 handle 使用的材料索引（含历史材料）；请核对发送者、加入时间及来源消息，内容需用材料工具读取。",
        ]
        for item in visible_items:
            handle = self._safe_prompt_label(item.get("attachment_handle") or item.get("attachment_id"))
            kind = self._safe_prompt_label(item.get("kind") or "file").lower() or "file"
            status = self._safe_prompt_label(item.get("status") or "unknown").lower() or "unknown"
            focus = "true" if int(item.get("focus_rank") or 0) > 0 else "false"
            name = self._activity_display_name(item)
            fields = [
                f"handle={handle}",
                f"kind={kind}",
                f"status={status}",
                f"focus={focus}",
            ]
            if name:
                fields.append("name=" + json.dumps(name, ensure_ascii=False))
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            source_fields = {
                "sender": self._sender_label(item),
                "sender_id": self._safe_prompt_label(detail.get("qq_sender_id")),
                "added_at": self._format_time_anchor(item.get("created_at")),
                "source_message_id": self._safe_prompt_label(item.get("source_message_id")),
            }
            for key, value in source_fields.items():
                if value:
                    fields.append(key + "=" + json.dumps(value, ensure_ascii=False))
            if status == "failed":
                reason = self._safe_prompt_label(self._readable_failure_message(item))
                if reason:
                    fields.append("reason=" + json.dumps(reason, ensure_ascii=False))
            lines.append("- " + " ".join(fields))
        return "\n".join(lines)
