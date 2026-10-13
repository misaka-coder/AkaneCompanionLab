"""QQ attachment source labels, handles and vision-result context projection."""
from __future__ import annotations

import json
import re
from typing import Any

from companion_v01.channels.qq.formatting import _format_qq_timestamp, _normalize_reply_text, _qq_item_time_label


def _with_qq_sender_context(attachments: list[dict[str, Any]] | None, context: Any) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    sender_label = str(getattr(context, "sender_label", "") or "").strip()
    sender_id = int(getattr(context, "user_id", 0) or 0)
    group_id = int(getattr(context, "group_id", 0) or 0)
    for item in list(attachments or []):
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        if sender_label:
            payload.setdefault("sender_label", sender_label)
        if sender_id:
            payload.setdefault("sender_id", str(sender_id))
        if group_id:
            payload.setdefault("group_id", str(group_id))
        enriched.append(payload)
    return enriched


def _merge_qq_attachments(*groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in list(group or []):
            if not isinstance(item, dict):
                continue
            locator = next(
                (
                    str(item.get(field) or "").strip()
                    for field in ("file", "path", "url")
                    if str(item.get(field) or "").strip()
                ),
                "",
            )
            identity = (
                str(item.get("kind") or "").strip().lower(),
                locator,
            )
            if locator and identity in seen:
                continue
            if locator:
                seen.add(identity)
            merged.append(dict(item))
    return merged


def _append_qq_attachment_handles_to_message(message: str, items: list[dict[str, Any]]) -> str:
    safe_items = [item for item in items if isinstance(item, dict)]
    if not safe_items:
        return str(message or "").strip()
    lines = [str(message or "").strip(), "qq.attachments:"]
    for item in safe_items:
        handle = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
        if not handle:
            continue
        lines.append(f"  - handle: {json.dumps(handle, ensure_ascii=False)}")
        lines.append(f"    kind: {json.dumps(str(item.get('kind') or 'file'), ensure_ascii=False)}")
        lines.append(f"    status: {json.dumps(str(item.get('status') or 'pending'), ensure_ascii=False)}")
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        forward_id = str(detail.get("qq_forward_id") or "").strip()
        if forward_id:
            lines.append(f"    forward_id: {json.dumps(forward_id, ensure_ascii=False)}")
        try:
            forward_node_index = int(detail.get("qq_forward_node_index") or 0)
        except (TypeError, ValueError):
            forward_node_index = 0
        if forward_node_index > 0:
            lines.append(f"    forward_node_index: {forward_node_index}")
        sender_label = str(detail.get("qq_sender_label") or "").strip()
        if sender_label:
            lines.append(f"    sender_label: {json.dumps(sender_label, ensure_ascii=False)}")
    return "\n".join(lines).strip()


def _normalize_passive_image_placeholder(message: str, items: list[dict[str, Any]]) -> str:
    """Keep a passive image turn without claiming its pixels were observed."""

    text = str(message or "").strip()
    image_count = sum(
        1
        for item in items
        if isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
    )
    if image_count <= 0:
        return text
    match = re.fullmatch(r"(?P<speaker>【[^】]+】)?发来了一张图片[。.!！]?", text)
    if match is None:
        return text
    speaker = str(match.group("speaker") or "")
    label = "[图片]" if image_count == 1 else f"[图片×{image_count}]"
    return f"{speaker}{label}"


def _build_qq_image_vision_followup_note(
    *,
    attachment_ids: list[str],
    wait_result: dict[str, Any],
    event_timestamp: int | None = None,
) -> str:
    def _compact_text_list(value: Any, *, limit: int = 12) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = re.split(r"[,，、\n]+", value)
        else:
            raw_items = []
        items: list[str] = []
        for raw_item in raw_items:
            item = _normalize_reply_text(raw_item)[:60]
            if item and item not in items:
                items.append(item)
            if len(items) >= limit:
                break
        return items

    base_lines = [
        "【本轮 QQ 图片内容】",
        "用户刚刚发送的图片视觉摘要已经生成；下面就是本轮用户发来的图片内容。",
        "强约束：本轮回复只依据下面的图片摘要；如果旧记忆、旧工作台、生成文件工作台或先前图片与这里冲突，一律以本轮摘要为准。",
        "不要说“让我看看”“我还没看到图片”“是不是上次那张”；不要把旧图片、旧文件或角色立绘当成这张图。",
    ]
    event_time_label = _format_qq_timestamp(event_timestamp)
    if event_time_label:
        base_lines.insert(1, f"本轮图片发送时间：{event_time_label}。")
    items_by_id = wait_result.get("items_by_id") if isinstance(wait_result.get("items_by_id"), dict) else {}
    kinds_by_id = wait_result.get("kinds_by_id") if isinstance(wait_result.get("kinds_by_id"), dict) else {}
    ready_ids = {str(item or "").strip() for item in list(wait_result.get("ready") or []) if str(item or "").strip()}
    image_lines: list[str] = []
    for attachment_id in attachment_ids:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id or normalized_id not in ready_ids:
            continue
        item = items_by_id.get(normalized_id) if isinstance(items_by_id, dict) else None
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or kinds_by_id.get(normalized_id) or "").strip().lower()
        if kind != "image":
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        title = _normalize_reply_text(item.get("summary_title") or item.get("attachment_handle") or "图片")[:40]
        summary = _normalize_reply_text(
            item.get("short_hint") or detail.get("summary") or detail.get("description") or ""
        )[:360]
        visible_text = _compact_text_list(detail.get("visible_text") or [], limit=8)
        concrete_details = _compact_text_list(detail.get("concrete_details") or detail.get("details") or [], limit=8)
        entities = _compact_text_list(detail.get("entities") or detail.get("objects") or [])
        mood_tags = _compact_text_list(detail.get("mood_tags") or detail.get("tags") or detail.get("keywords") or [])
        uncertainty = _compact_text_list(detail.get("uncertainty") or [])
        if summary:
            time_label = _qq_item_time_label(item)
            title_part = title or "图片"
            if time_label:
                title_part = f"{title_part}（{time_label}）"
            line = f"- {title_part}：{summary}" if title_part else f"- {summary}"
            extras = []
            if visible_text:
                extras.append("可见文字：" + "、".join(visible_text))
            if concrete_details:
                extras.append("细节：" + "、".join(concrete_details))
            if entities:
                extras.append("要素：" + "、".join(entities))
            if mood_tags:
                extras.append("标签：" + "、".join(mood_tags))
            if uncertainty:
                extras.append("不确定处：" + "、".join(uncertainty))
            if extras:
                line += "；" + "；".join(extras)
            image_lines.append(line)
    if image_lines:
        return "\n".join([*base_lines, *image_lines])
    return "\n".join(
        [
            *base_lines,
            "当前材料工作台里的最新图片就是本轮用户发来的图片内容。",
        ]
    )


def _qq_image_followup_failure_message(
    *,
    wait_result: dict[str, Any],
    pending_image_ids: list[str],
    failed_image_ids: list[str],
) -> str:
    items_by_id = wait_result.get("items_by_id") if isinstance(wait_result.get("items_by_id"), dict) else {}
    failure_codes = {
        str((items_by_id.get(item_id) or {}).get("error_message") or "").strip().lower()
        for item_id in failed_image_ids
    }
    if "attachment_too_large" in failure_codes:
        return "这张图片超过了当前视觉读取的大小限制，请压缩后再发一次。"
    if "attachment_vision_unavailable" in failure_codes:
        return "图片已经收到，但视觉读取服务这次不可用，请稍后再试。"
    if failed_image_ids:
        return "这张图片没有成功进入视觉读取，请重新发送一次。"
    if pending_image_ids:
        return "这张图片在等待时间内没有处理完成，请稍后重新发送。"
    return "这张图片这次没有读取成功，请重新发送一次。"
