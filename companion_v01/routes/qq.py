from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..model_service_config import effective_settings_from_config, probe_model_ids, redact_provider_error
from .voice import (
    GPT_SOVITS_PROVIDER_ID,
    _coerce_synthesized_audio,
    _resolve_tts_runtime_provider,
)
from ..qq_route_helpers import (
    apply_qq_current_outfit_visual as _apply_qq_current_outfit_visual,
    build_qq_resource_manifest_builder as _build_qq_resource_manifest_builder,
    qq_attachment_ready_wait_seconds as _qq_attachment_ready_wait_seconds,
    qq_current_outfit_id_from_turn_payload as _qq_current_outfit_id_from_turn_payload,
    qq_pending_image_attachment_ids as _qq_pending_image_attachment_ids,
)


if TYPE_CHECKING:
    from ..qq_gateway import NapCatQQGateway


LogEvent = Callable[..., None]

QQ_REPLY_OBJECT_TERMS = ("工作台", "文件", "结果", "成果", "产物", "音频", "视频", "人声", "伴奏", "任务")
QQ_REPLY_ACTION_TERMS = (
    "清理",
    "清空",
    "清除",
    "收拾",
    "干净",
    "删除",
    "归档",
    "发",
    "发送",
    "转",
    "转换",
    "转成",
    "分离",
    "拆",
    "完成",
    "做好",
)


def _normalize_reply_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split()).strip()


def _canonical_reply_text(value: Any) -> str:
    return re.sub(r"[\s，。！？!?~～、,.;；:：…—-]+", "", str(value or "").strip().lower())


def _strip_canonical_prefix(text: str, canonical_prefix: str) -> str:
    prefix = str(canonical_prefix or "")
    if not prefix:
        return str(text or "").strip()
    matched = ""
    for index, character in enumerate(str(text or "")):
        canonical_character = _canonical_reply_text(character)
        if not canonical_character:
            continue
        matched += canonical_character
        if not prefix.startswith(matched):
            return str(text or "").strip()
        if matched == prefix:
            return str(text or "")[index + 1 :].strip(" \t\r\n，。！？!?~～、,.;；:：…—-")
    return str(text or "").strip()


QQ_WORKSPACE_LIST_COMMANDS = {
    "工作台",
    "材料工作台",
    "工作台列表",
    "查看工作台",
    "查看材料",
    "材料列表",
    "当前工作台",
    "当前材料",
}
QQ_WORKSPACE_HELP_COMMANDS = {"工作台帮助", "材料帮助", "工作台指令"}
QQ_WORKSPACE_CLEAR_CURRENT_COMMANDS = {
    "清理工作台",
    "清空工作台",
    "清除工作台",
    "删除工作台",
    "移除工作台",
    "清理当前工作台",
    "清空当前工作台",
    "清理材料",
    "清空材料",
    "清除材料",
    "删除材料",
    "移除材料",
    "清理附件",
    "清空附件",
    "清除附件",
    "删除附件",
    "移除附件",
    "清理文件",
    "清空文件",
    "清除文件",
    "删除文件",
    "移除文件",
    "清理全部材料",
    "清空全部材料",
    "清除全部材料",
    "清理所有材料",
    "清空所有材料",
    "清除所有材料",
}
QQ_WORKSPACE_CLEAR_LATEST_COMMANDS = {
    "清理最新材料",
    "清理最近材料",
    "清理最新附件",
    "清除最新材料",
    "清空最新材料",
    "删除最新材料",
    "移除最新材料",
}
QQ_WORKSPACE_PURGE_MARKERS = (
    "彻底",
    "删除原始",
    "删除本地",
    "删掉原始",
    "删掉本地",
    "连文件",
    "原始文件也",
    "本地文件也",
    "附件文件也",
)
QQ_WORKSPACE_KIND_LABELS = {
    "image": "图片",
    "document": "文件",
    "file": "文件",
    "audio": "音频",
    "video": "视频",
}


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


def _format_qq_timestamp(value: Any) -> str:
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


def _qq_item_time_label(item: dict[str, Any]) -> str:
    created_label = _format_qq_timestamp(item.get("created_at"))
    updated_label = _format_qq_timestamp(item.get("updated_at"))
    if created_label and updated_label and updated_label != created_label:
        return f"加入 {created_label}，更新 {updated_label}"
    if created_label:
        return f"加入 {created_label}"
    if updated_label:
        return f"更新 {updated_label}"
    return ""


def _normalize_qq_workspace_command_text(qq_gateway: Any, message: str) -> str:
    normalizer = getattr(qq_gateway, "_normalize_character_command_text", None)
    if callable(normalizer):
        try:
            return str(normalizer(message) or "").strip()
        except Exception:
            pass
    text = re.sub(r"\s+", " ", str(message or "").strip())
    text = re.sub(r"^akane(?:[\s,，:：;；、-]+|$)", "", text, flags=re.IGNORECASE)
    return text.strip()


def _parse_qq_workspace_command(qq_gateway: Any, message: str) -> dict[str, Any] | None:
    text = _normalize_qq_workspace_command_text(qq_gateway, message)
    if not text:
        return None
    lowered = text.lower()
    delete_storage = any(marker in text for marker in QQ_WORKSPACE_PURGE_MARKERS) or bool(
        re.search(r"(?:删除|删掉|移除).*(?:原始|本地|附件)?文件", text)
    )
    stripped_for_purge = text
    for marker in QQ_WORKSPACE_PURGE_MARKERS:
        stripped_for_purge = stripped_for_purge.replace(marker, "")
    stripped_for_purge = re.sub(r"\s+", "", stripped_for_purge)
    kind = "any"
    if "图片" in text or "照片" in text:
        kind = "image"
    elif "音频" in text or "语音" in text or "音乐" in text:
        kind = "audio"
    elif "文件" in text or "文档" in text or "pdf" in lowered:
        kind = "document"
    if text in QQ_WORKSPACE_HELP_COMMANDS:
        return {"action": "help"}
    if text in QQ_WORKSPACE_LIST_COMMANDS:
        return {"action": "list"}
    if text in QQ_WORKSPACE_CLEAR_LATEST_COMMANDS:
        return {"action": "clear", "target": "latest", "kind": kind, "delete_storage": delete_storage}
    if text in QQ_WORKSPACE_CLEAR_CURRENT_COMMANDS or stripped_for_purge in QQ_WORKSPACE_CLEAR_CURRENT_COMMANDS:
        return {"action": "clear", "target": "current", "kind": kind, "delete_storage": delete_storage}
    if re.fullmatch(
        r"(?:清理|清空|清除|删除|移除|收拾)(?:全部|所有|当前)?(?:工作台|材料|附件|文件)(?:文件|图片|照片|文档|音频)?",
        stripped_for_purge,
    ) or re.fullmatch(r"(?:删除|删掉|移除)(?:原始|本地|附件)?文件", text):
        return {"action": "clear", "target": "current", "kind": kind, "delete_storage": delete_storage}
    match = re.fullmatch(r"(?:清理|清除|移除|删除)(?:工作台|材料|附件)?[:：\s]+(.+)", text)
    if match:
        target = _normalize_reply_text(match.group(1))[:120]
        if target:
            return {"action": "clear", "target": target, "kind": kind, "delete_storage": delete_storage}
    return None


def _qq_workspace_kind_label(kind: Any) -> str:
    normalized = str(kind or "").strip().lower()
    return QQ_WORKSPACE_KIND_LABELS.get(normalized, "材料")


def _looks_like_machine_name(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    stem = Path(text).stem if "." in text else text
    compact = re.sub(r"[^A-Za-z0-9]", "", stem)
    if len(compact) >= 18 and len(compact) >= max(1, int(len(stem) * 0.72)):
        return True
    if re.fullmatch(r"[A-Fa-f0-9]{12,}", compact):
        return True
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{18,}", text):
        return True
    return False


def _friendly_qq_workspace_title(item: dict[str, Any]) -> str:
    kind_label = _qq_workspace_kind_label(item.get("kind"))
    candidates = [
        str(item.get("summary_title") or "").strip(),
        str(item.get("origin_name") or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        name = Path(candidate).name
        if name and not _looks_like_machine_name(name):
            return name[:48]
    return f"未命名{kind_label}"


def _format_qq_workspace_item(item: dict[str, Any]) -> str:
    handle = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
    title = _friendly_qq_workspace_title(item)
    kind_label = _qq_workspace_kind_label(item.get("kind"))
    suffix_parts = [part for part in (handle, _qq_item_time_label(item)) if part]
    suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
    return f"{kind_label}：{title}{suffix}"


def _build_qq_workspace_list_reply(service: Any, *, profile_user_id: str, session_id: str) -> str:
    store = getattr(service, "store", None)
    if store is None or not hasattr(store, "list_attachment_inbox_items"):
        return "当前工作台服务不可用。"
    items = store.list_attachment_inbox_items(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready", "pending_observation", "failed"],
        limit=30,
    )
    lines = ["当前工作台"]
    if not items:
        lines.append("空。现在没有材料会继续进入 Akane 的上下文。")
    else:
        lines.append(f"共有 {len(items)} 个材料：")
        for index, item in enumerate(items[:20], start=1):
            if isinstance(item, dict):
                lines.append(f"{index}. {_format_qq_workspace_item(item)}")
        if len(items) > 20:
            lines.append(f"还有 {len(items) - 20} 个未显示。")
    lines.append("")
    lines.append("清理工作台：移出上下文")
    lines.append("清理最新材料：只移出最近一个")
    lines.append("彻底清理工作台：同时删除附件文件")
    return "\n".join(lines)


def _should_sync_workspace_state_to_llm(qq_gateway: Any, message: str) -> bool:
    text = _normalize_qq_workspace_command_text(qq_gateway, message)
    if not text:
        return False
    if _parse_qq_workspace_command(qq_gateway, message) is not None:
        return False
    return any(marker in text for marker in ("工作台", "材料", "附件"))


def _build_qq_workspace_state_context(service: Any, *, profile_user_id: str, session_id: str) -> str:
    store = getattr(service, "store", None)
    if store is None or not hasattr(store, "list_attachment_inbox_items"):
        return ""
    items = store.list_attachment_inbox_items(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready", "pending_observation", "failed"],
        limit=20,
    )
    lines = [
        "【当前工作台真实状态】",
        "这是给 Akane 的实时状态同步，不是用户新发来的材料。用户正在自然询问工作台时，请基于这里回答。",
    ]
    if not items:
        lines.append("当前工作台为空。请把“空”作为事实自然回答。")
        lines.append("不要根据旧记忆、生成文件工作台、之前处理过的 mp3/图片/文件猜测仍有材料。")
        return "\n".join(lines)
    lines.append(f"当前工作台有 {len(items)} 个材料。只把下列材料当作当前工作台材料：")
    for index, item in enumerate(items[:12], start=1):
        if isinstance(item, dict):
            lines.append(f"{index}. {_format_qq_workspace_item(item)}")
    if len(items) > 12:
        lines.append(f"还有 {len(items) - 12} 个未列出。")
    lines.append("不要把旧生成文件、聊天记忆或已经清理的材料算进当前工作台。")
    return "\n".join(lines)


def _build_qq_workspace_help_reply() -> str:
    return "\n".join(
        [
            "工作台指令",
            "工作台 / 查看工作台：列出当前材料",
            "自然询问工作台状态时：会同步真实状态给 Akane，让她自然回答",
            "清理工作台：让所有当前材料退出上下文",
            "清理最新材料：只清最近一个材料",
            "清理工作台 file_001：清指定材料",
            "彻底清理工作台：同时删除附件原始文件",
        ]
    )


def _build_qq_workspace_clear_reply(result: dict[str, Any], *, delete_storage: bool) -> str:
    cleared = [item for item in list(result.get("cleared") or []) if isinstance(item, dict)]
    purged = [str(item or "").strip() for item in list(result.get("purged_files") or []) if str(item or "").strip()]
    unresolved = [str(item or "").strip() for item in list(result.get("unresolved") or []) if str(item or "").strip()]
    lines = ["工作台清理结果"]
    if cleared:
        lines.append(f"已移出上下文：{len(cleared)} 个。")
        lines.append("现在这些材料不会继续进入 Akane 的上下文。")
        for index, item in enumerate(cleared[:12], start=1):
            lines.append(f"{index}. {_format_qq_workspace_item(item)}")
        if len(cleared) > 12:
            lines.append(f"还有 {len(cleared) - 12} 个未显示。")
    else:
        lines.append("当前工作台已经是空的。")
        lines.append("没有材料会继续进入 Akane 的上下文。")
    if delete_storage:
        lines.append(f"已删除原始附件文件：{len(purged)} 个。")
    if unresolved:
        lines.append("未找到：" + "、".join(unresolved[:8]))
    lines.append("发送“工作台”可再次查看。")
    return "\n".join(lines)


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


def _reply_similarity(left: str, right: str) -> float:
    left_text = re.sub(r"[\s，。！？!?~～、,.]+", "", str(left or "").strip().lower())
    right_text = re.sub(r"[\s，。！？!?~～、,.]+", "", str(right or "").strip().lower())
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    if len(left_text) < 4 or len(right_text) < 4:
        return 0.0
    left_grams = {left_text[index : index + 2] for index in range(len(left_text) - 1)}
    right_grams = {right_text[index : index + 2] for index in range(len(right_text) - 1)}
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _is_similar_reply(left: str, right: str) -> bool:
    if _reply_similarity(left, right) >= 0.42:
        return True
    left_text = str(left or "")
    right_text = str(right or "")
    shared_objects = [term for term in QQ_REPLY_OBJECT_TERMS if term in left_text and term in right_text]
    shared_actions = [term for term in QQ_REPLY_ACTION_TERMS if term in left_text and term in right_text]
    return bool(shared_objects and shared_actions)


def _filter_unsent_reply_messages(messages: list[str], sent_messages: list[str]) -> list[str]:
    sent_normalized = {_normalize_reply_text(item) for item in sent_messages if _normalize_reply_text(item)}
    sent_joined = "".join(str(item or "").strip() for item in sent_messages if str(item or "").strip()).strip()
    sent_joined_normalized = _normalize_reply_text(sent_joined)
    sent_canonical_prefix = "".join(_canonical_reply_text(item) for item in sent_messages)
    unsent: list[str] = []
    for message in messages:
        text = str(message or "").strip()
        if not text:
            continue
        canonical = _canonical_reply_text(text)
        if sent_canonical_prefix and canonical.startswith(sent_canonical_prefix):
            if canonical == sent_canonical_prefix:
                continue
            text = _strip_canonical_prefix(text, sent_canonical_prefix)
            if not text:
                continue
        normalized = _normalize_reply_text(text)
        if normalized and normalized in sent_normalized:
            continue
        if sent_joined and text.startswith(sent_joined):
            text = text[len(sent_joined) :].strip()
            normalized = _normalize_reply_text(text)
            if not normalized:
                continue
        elif sent_joined_normalized and normalized == sent_joined_normalized:
            continue
        trimmed_by_sent_prefix = False
        for sent_item in sent_messages:
            sent_text = str(sent_item or "").strip()
            if sent_text and text.startswith(sent_text):
                text = text[len(sent_text) :].strip()
                normalized = _normalize_reply_text(text)
                trimmed_by_sent_prefix = True
                break
        if trimmed_by_sent_prefix:
            if not normalized:
                continue
            generic_tail = re.sub(r"[\s，。！？!?~～、,.]+", "", text)
            if len(generic_tail) < 10:
                continue
        if any(_is_similar_reply(text, sent_item) for sent_item in sent_messages):
            continue
        unsent.append(text)
    return unsent


def _send_pending_stage_messages(
    *,
    qq_gateway: Any,
    context: Any,
    pending_messages: list[str],
    streamed_messages: list[str],
    stream_send_results: list[dict[str, Any]],
    max_streamed: int,
) -> list[str]:
    for text in pending_messages:
        if len(streamed_messages) >= max_streamed:
            break
        normalized = _normalize_reply_text(text)
        if not normalized or normalized in {_normalize_reply_text(item) for item in streamed_messages}:
            continue
        if any(_is_similar_reply(text, sent_item) for sent_item in streamed_messages):
            continue
        result = qq_gateway.send_reply(context, text[:1800].strip())
        streamed_messages.append(text)
        stream_send_results.append(result)
    return []


def _normalize_reply_medium(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "text": "text",
        "文字": "text",
        "文本": "text",
        "voice": "voice",
        "audio": "voice",
        "record": "voice",
        "语音": "voice",
        "both": "both",
        "all": "both",
        "text_voice": "both",
        "voice_text": "both",
        "文字语音": "both",
        "双发": "both",
        "auto": "auto",
        "自动": "auto",
    }
    return aliases.get(text, "")


def _streaming_allows_text(reply_mode: str, delivery_hint: str) -> bool:
    mode = _normalize_reply_medium(reply_mode) or "auto"
    hint = _normalize_reply_medium(delivery_hint)
    if mode == "auto":
        return hint in {"text", "both"}
    return mode in {"text", "both"}


def _frame_reply_medium(frame: dict[str, Any], *, delivery_hint: str = "") -> str:
    return _normalize_reply_medium(frame.get("reply_medium")) or _normalize_reply_medium(delivery_hint)


def _resolve_delivery_medium(
    *,
    context: Any,
    qq_gateway: Any,
    frame: dict[str, Any],
    delivery_hint: str = "",
) -> dict[str, str]:
    reply_mode = (
        _normalize_reply_medium(getattr(context, "reply_mode", ""))
        or _normalize_reply_medium(qq_gateway.resolve_reply_mode(getattr(context, "session_id", "")))
        or "auto"
    )
    model_medium = _frame_reply_medium(frame, delivery_hint=delivery_hint) or "text"
    medium = model_medium if reply_mode == "auto" else reply_mode
    if medium not in {"text", "voice", "both"}:
        medium = "text"
    return {
        "reply_mode": reply_mode,
        "model_medium": model_medium,
        "medium": medium,
    }


def _media_type_extension(media_type: str) -> str:
    clean = str(media_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
        "audio/opus": "opus",
        "audio/flac": "flac",
        "audio/aac": "aac",
        "audio/mp4": "m4a",
    }.get(clean, "wav")


def _run_async_safely(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _resolve_qq_tts_profile_user_id(*, config_module: Any, context: Any) -> str:
    raw_value = str(getattr(config_module, "QQ_TTS_PROFILE_USER_ID", "") or "").strip()
    if not raw_value:
        raw_value = str(getattr(config_module, "WEB_OWNER_PROFILE_USER_ID", "") or "master").strip()
    if raw_value.lower() in {"conversation", "context", "current"}:
        raw_value = str(getattr(context, "profile_user_id", "") or "master").strip()
    if not raw_value or not re.fullmatch(r"[A-Za-z0-9_.-]+", raw_value):
        return "master"
    return raw_value


def _voice_cache_key(*, text: str, provider: str, resolution: dict[str, Any]) -> str:
    """Build a stable cache key from the synthesis inputs so identical text
    reuses an existing file instead of resynthesising."""
    raw = f"{text}\nprovider={provider}\nprofile={resolution.get('voiceProfileId', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _prune_voice_cache(cache_dir: Path, *, max_mb: int = 50) -> None:
    """Trim the voice cache to at most *max_mb* by removing the oldest files."""
    max_bytes = max(1, max_mb) * 1024 * 1024
    try:
        entries = sorted(cache_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    total = 0
    # Walk newest-first and decide what to keep
    for entry in reversed(entries):
        try:
            total += entry.stat().st_size
        except OSError:
            continue
    for entry in entries:
        if total <= max_bytes:
            break
        try:
            size = entry.stat().st_size
            entry.unlink()
            total -= size
        except OSError:
            continue


def _synthesize_qq_voice_file(
    *,
    engine: Any,
    config_module: Any,
    tts_client: Any,
    text: str,
    context: Any,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"ok": False, "reason": "empty_voice_text"}

    data_dir = Path(str(getattr(config_module, "DATA_DIR", "users_data") or "users_data"))
    base_dir = data_dir
    tts_profile_user_id = _resolve_qq_tts_profile_user_id(config_module=config_module, context=context)
    payload = {
        "text": clean_text,
        "real_user_id": tts_profile_user_id,
        "profile_user_id": tts_profile_user_id,
        "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
    }
    resolution = _resolve_tts_runtime_provider(
        engine=engine,
        payload=payload,
        base_dir=base_dir,
        config_module=config_module,
        edge_tts_available=tts_client is not None,
        gpt_sovits_client_factory=gpt_sovits_client_factory,
    )

    active_provider = str(resolution.get("activeProviderId") or "")
    if active_provider == GPT_SOVITS_PROVIDER_ID:
        synthesize_kwargs: dict[str, Any] = {
            "voice_profile_id": str(resolution.get("voiceProfileId") or ""),
        }
        voice_profile = resolution.get("voiceProfile")
        if isinstance(voice_profile, dict) and voice_profile:
            synthesize_kwargs["profile"] = voice_profile
        result = _run_async_safely(resolution["client"].synthesize(clean_text, **synthesize_kwargs))
        audio, media_type = _coerce_synthesized_audio(result, default_media_type="audio/wav")
    elif tts_client is not None:
        result = _run_async_safely(tts_client.synthesize(clean_text))
        audio, media_type = _coerce_synthesized_audio(result, default_media_type="audio/mpeg")
    else:
        return {"ok": False, "reason": "tts_unavailable", "resolution": resolution}

    cache_dir = data_dir / "qq_voice_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = _media_type_extension(media_type)
    cache_key = _voice_cache_key(text=clean_text, provider=active_provider, resolution=resolution)
    path = cache_dir / f"{cache_key}.{ext}"
    if not path.exists():
        path.write_bytes(audio)
        _prune_voice_cache(cache_dir, max_mb=50)
    return {
        "ok": True,
        "path": str(path),
        "media_type": media_type,
        "provider": active_provider,
        "resolution_status": str(resolution.get("status") or ""),
        "tts_profile_user_id": tts_profile_user_id,
    }


def _send_qq_delivery(
    *,
    engine: Any,
    qq_gateway: Any,
    context: Any,
    frame: dict[str, Any],
    reply_messages: list[str],
    unsent_reply_messages: list[str],
    streamed_messages: list[str],
    config_module: Any,
    tts_client: Any = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
    delivery_hint: str = "",
) -> dict[str, Any]:
    plan = _resolve_delivery_medium(
        context=context,
        qq_gateway=qq_gateway,
        frame=frame,
        delivery_hint=delivery_hint,
    )
    medium = plan["medium"]
    text_enabled = medium in {"text", "both"}
    voice_enabled = medium in {"voice", "both"}
    text_send_result = {"ok": True, "count": 0, "results": []}
    voice_send_result = {"ok": True, "count": 0, "results": []}

    if text_enabled:
        text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)

    voice_reason = ""
    if voice_enabled:
        max_segments = max(1, min(10, int(getattr(config_module, "QQ_VOICE_MAX_SEGMENTS", 3) or 3)))
        voice_messages = [str(message or "").strip() for message in reply_messages if str(message or "").strip()]
        voice_text = "\n".join(voice_messages[:max_segments]).strip()
        max_auto_chars = max(20, min(1200, int(getattr(config_module, "QQ_VOICE_MAX_TEXT_CHARS", 280) or 280)))
        if plan["reply_mode"] == "auto" and len(voice_text) > max_auto_chars:
            voice_enabled = False
            voice_reason = "auto_voice_text_too_long"
            if not text_enabled and not streamed_messages and unsent_reply_messages:
                text_enabled = True
                text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)
                text_send_result["fallback_from_voice"] = True
        elif voice_text:
            try:
                voice_file = _synthesize_qq_voice_file(
                    engine=engine,
                    config_module=config_module,
                    tts_client=tts_client,
                    text=voice_text,
                    context=context,
                    gpt_sovits_client_factory=gpt_sovits_client_factory,
                )
            except Exception as exc:
                voice_file = {"ok": False, "reason": str(exc)[:200]}
            if voice_file.get("ok"):
                result = qq_gateway.send_voice(
                    context,
                    audio_path=str(voice_file.get("path") or ""),
                    name="akane_reply",
                )
                result["provider"] = str(voice_file.get("provider") or "")
                result["media_type"] = str(voice_file.get("media_type") or "")
                result["tts_profile_user_id"] = str(voice_file.get("tts_profile_user_id") or "")
                voice_send_result = {
                    "ok": bool(result.get("ok")),
                    "count": 1,
                    "results": [result],
                }
                voice_reason = "" if result.get("ok") else str(result.get("reason") or "voice_send_failed")
            else:
                voice_send_result = {
                    "ok": False,
                    "count": 0,
                    "reason": str(voice_file.get("reason") or "voice_synthesis_failed"),
                    "results": [],
                }
                voice_reason = str(voice_file.get("reason") or "voice_synthesis_failed")

    needs_text_fallback = (
        voice_enabled
        and not bool(voice_send_result.get("ok"))
        and not text_enabled
        and not streamed_messages
        and unsent_reply_messages
    )
    if needs_text_fallback:
        text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)
        text_send_result["fallback_from_voice"] = True

    combined_results = [
        *list(text_send_result.get("results") or []),
        *list(voice_send_result.get("results") or []),
    ]
    ok_parts = [bool(text_send_result.get("ok"))]
    if voice_enabled:
        ok_parts.append(bool(voice_send_result.get("ok")) or needs_text_fallback)
    return {
        "ok": all(ok_parts),
        "count": len(combined_results),
        "results": combined_results,
        "delivery": {
            **plan,
            "text_enabled": bool(text_enabled),
            "voice_enabled": bool(voice_enabled),
            "voice_reason": voice_reason,
        },
        "text_result": text_send_result,
        "voice_result": voice_send_result,
    }


def _load_qq_delivery_config(engine: Any, context: Any) -> dict[str, Any]:
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
    if not character_pack_id:
        return {}
    service = getattr(engine, "desktop_pet_character_resources", None)
    loader = getattr(service, "load_qq_delivery_config", None)
    if not callable(loader):
        return {}
    try:
        value = loader(character_pack_id)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _send_qq_emotion_image_fallback(
    *,
    engine: Any,
    qq_gateway: NapCatQQGateway,
    context: Any,
    frame: dict[str, Any],
    qq_delivery_config: dict[str, Any],
    current_outfit_id: str = "",
) -> dict[str, Any]:
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
    if not character_pack_id:
        return {"ok": True, "status": "skipped", "reason": "empty_character_pack_id"}
    image_config = (
        qq_delivery_config.get("emotion_images") if isinstance(qq_delivery_config.get("emotion_images"), dict) else {}
    )
    if image_config.get("enabled") is False:
        return {"ok": True, "status": "skipped", "reason": "disabled"}
    service = getattr(engine, "desktop_pet_character_resources", None)
    resolver = getattr(service, "resolve_emotion_image_file", None)
    if not callable(resolver):
        return {"ok": True, "status": "skipped", "reason": "missing_character_resource_service"}
    emotion = str((frame or {}).get("emotion") or "").strip()
    try:
        image = resolver(character_pack_id, emotion, outfit_id=current_outfit_id)
    except TypeError:
        try:
            image = resolver(character_pack_id, emotion)
        except Exception:
            image = {}
    except Exception:
        image = {}
    if not isinstance(image, dict) or not image.get("path"):
        return {"ok": True, "status": "skipped", "reason": "missing_emotion_image", "emotion": emotion}
    try:
        min_interval = int(image_config.get("min_interval_seconds") or image_config.get("cooldown_seconds") or 20)
    except (TypeError, ValueError):
        min_interval = 20
    return qq_gateway.send_emotion_image(
        context,
        frame,
        image=image,
        min_interval_seconds=min_interval,
    )


def _process_qq_turn_streaming(
    *,
    engine: Any,
    qq_gateway: Any,
    context: Any,
    turn_payload: dict[str, Any],
    config_module: Any,
    tts_client: Any = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    pending_stage_messages: list[str] = []
    streamed_messages: list[str] = []
    stream_send_results: list[dict[str, Any]] = []
    frame: dict[str, Any] = {}
    delivery_hint = ""
    active_reply_mode = (
        _normalize_reply_medium(getattr(context, "reply_mode", ""))
        or _normalize_reply_medium(qq_gateway.resolve_reply_mode(getattr(context, "session_id", "")))
        or "auto"
    )
    max_streamed = max(
        0,
        min(
            20,
            int(
                getattr(config_module, "QQ_STREAM_MAX_SEGMENTS", getattr(config_module, "QQ_REPLY_MAX_SEGMENTS", 8))
                or 0
            ),
        ),
    )
    stream_enabled = bool(getattr(config_module, "QQ_STREAM_REPLIES_ENABLED", True)) and max_streamed > 0

    for stream_event in engine.process_turn_stream(turn_payload):
        if not isinstance(stream_event, dict):
            continue
        event_type = str(stream_event.get("type") or "").strip()
        if event_type == "delivery_hint":
            delivery_hint = _normalize_reply_medium(stream_event.get("medium")) or delivery_hint
            continue
        if event_type == "speech_segment" and stream_enabled:
            text = str(stream_event.get("text") or "").strip()
            if not text:
                continue
            normalized = _normalize_reply_text(text)
            if not normalized or normalized in {_normalize_reply_text(item) for item in pending_stage_messages}:
                continue
            pending_stage_messages.append(text)
            continue
        if (
            event_type == "assistant_stage_decision"
            and pending_stage_messages
            and _streaming_allows_text(active_reply_mode, delivery_hint)
        ):
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            continue
        if event_type == "final_ui" and isinstance(stream_event.get("payload"), dict):
            frame = dict(stream_event.get("payload") or {})

    if (
        stream_enabled
        and pending_stage_messages
        and not frame
        and _streaming_allows_text(active_reply_mode, delivery_hint)
    ):
        pending_stage_messages = _send_pending_stage_messages(
            qq_gateway=qq_gateway,
            context=context,
            pending_messages=pending_stage_messages,
            streamed_messages=streamed_messages,
            stream_send_results=stream_send_results,
            max_streamed=max_streamed,
        )

    if not frame and not streamed_messages:
        frame = engine.process_turn(turn_payload)

    reply_messages = qq_gateway.render_reply_messages(frame)
    unsent_reply_messages = _filter_unsent_reply_messages(reply_messages, streamed_messages)
    send_result = _send_qq_delivery(
        engine=engine,
        qq_gateway=qq_gateway,
        context=context,
        frame=frame,
        reply_messages=reply_messages,
        unsent_reply_messages=unsent_reply_messages,
        streamed_messages=streamed_messages,
        config_module=config_module,
        tts_client=tts_client,
        gpt_sovits_client_factory=gpt_sovits_client_factory,
        delivery_hint=delivery_hint,
    )
    if streamed_messages:
        combined_results = [*stream_send_results, *list(send_result.get("results") or [])]
        send_result = {
            "ok": all(bool(item.get("ok")) for item in combined_results) if combined_results else True,
            "count": len(combined_results),
            "streamed_count": len(streamed_messages),
            "deferred_count": len(unsent_reply_messages),
            "results": combined_results,
            "delivery": send_result.get("delivery"),
            "text_result": send_result.get("text_result"),
            "voice_result": send_result.get("voice_result"),
        }

    emotion_image_result = {"ok": True, "status": "skipped", "reason": "not_attempted"}
    if send_result.get("ok"):
        qq_delivery_config = _load_qq_delivery_config(engine, context)
        current_outfit_id = _qq_current_outfit_id_from_turn_payload(turn_payload)
        emotion_mface_result = qq_gateway.send_emotion_mface(
            context,
            frame,
            qq_delivery_config=qq_delivery_config,
        )
        if emotion_mface_result.get("status") != "sent":
            emotion_image_result = _send_qq_emotion_image_fallback(
                engine=engine,
                qq_gateway=qq_gateway,
                context=context,
                frame=frame,
                qq_delivery_config=qq_delivery_config,
                current_outfit_id=current_outfit_id,
            )
    else:
        emotion_mface_result = {
            "ok": True,
            "status": "skipped",
            "reason": "main_delivery_failed",
        }

    file_send_result = qq_gateway.send_generated_files(
        context,
        list(frame.get("tool_events") or []),
    )
    chart_send_result = qq_gateway.send_market_charts(
        context,
        list(frame.get("tool_events") or []),
        authorization="finance_tool_result",
    )
    report_send_result = qq_gateway.send_finance_reports(
        context,
        list(frame.get("tool_events") or []),
        authorization="finance_tool_result",
    )
    file_delivery_feedback_result = {"ok": True, "status": "skipped", "reason": "no_delivery_issue"}
    file_delivery_status = str(file_send_result.get("status") or "").strip().lower()
    _sid = str(getattr(context, "session_id", "") or "")
    if file_delivery_status == "blocked":
        file_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "文件这次没有发出，因为当前消息没有明确要求发送。需要时请直接说要发送哪一份。",
        )
        file_delivery_feedback_result["status"] = "blocked_notice_sent"
    elif file_delivery_status == "failed" or (
        int(file_send_result.get("count") or 0) > 0 and not bool(file_send_result.get("ok"))
    ):
        file_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "文件这次发送失败了，现有结果仍保留着，可以稍后再试。",
        )
        file_delivery_feedback_result["status"] = "failure_notice_sent"
        qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】文件发送失败，文件仍在工作台，用户已收到通知。")
    elif int(file_send_result.get("count") or 0) > 0 and bool(file_send_result.get("ok")):
        qq_gateway.add_delivery_note(
            _sid,
            f"【上一轮交付状态】文件发送成功（共 {file_send_result.get('count', 0)} 个）。",
        )
    chart_delivery_feedback_result = {"ok": True, "status": "skipped", "reason": "no_delivery_issue"}
    if str(chart_send_result.get("status") or "").strip().lower() == "failed":
        chart_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "图表已经生成，但这次 QQ 图片发送失败了；生成结果仍保留着，可以稍后再试。",
        )
        chart_delivery_feedback_result["status"] = "failure_notice_sent"
        qq_gateway.add_delivery_note(
            _sid, "【上一轮交付状态】图表已生成但 QQ 图片发送失败，文件仍在工作台，用户已收到通知。"
        )
    elif int(chart_send_result.get("count") or 0) > 0 and bool(chart_send_result.get("ok")):
        qq_gateway.add_delivery_note(
            _sid,
            f"【上一轮交付状态】图表发送成功（共 {chart_send_result.get('count', 0)} 张）。",
        )
    report_delivery_feedback_result = {"ok": True, "status": "skipped", "reason": "no_delivery_issue"}
    if str(report_send_result.get("status") or "").strip().lower() == "failed":
        report_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "金融报告已经生成，但这次 QQ 文件发送失败了；生成结果仍保留着，可以稍后再试。",
        )
        report_delivery_feedback_result["status"] = "failure_notice_sent"
        qq_gateway.add_delivery_note(
            _sid, "【上一轮交付状态】金融报告已生成但文件发送失败，文件仍在工作台，用户已收到通知。"
        )
    elif int(report_send_result.get("count") or 0) > 0 and bool(report_send_result.get("ok")):
        qq_gateway.add_delivery_note(
            _sid,
            f"【上一轮交付状态】金融报告发送成功（共 {report_send_result.get('count', 0)} 个）。",
        )
    sticker_send_result = qq_gateway.send_stickers(
        context,
        list(frame.get("tool_events") or []),
    )
    return {
        "frame": frame,
        "reply_messages": [*streamed_messages, *unsent_reply_messages],
        "send_result": send_result,
        "emotion_mface_result": emotion_mface_result,
        "emotion_image_result": emotion_image_result,
        "file_send_result": file_send_result,
        "file_delivery_feedback_result": file_delivery_feedback_result,
        "chart_send_result": chart_send_result,
        "chart_delivery_feedback_result": chart_delivery_feedback_result,
        "report_send_result": report_send_result,
        "report_delivery_feedback_result": report_delivery_feedback_result,
        "sticker_send_result": sticker_send_result,
    }


def build_qq_router(
    *,
    engine: Any,
    config_module: Any,
    qq_gateway: Any,
    runtime_metrics: Any,
    logger: Any,
    log_event: LogEvent,
    tts_client: Any = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
    finance_subscription_service: Any = None,
    finance_event_worker: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _prepare_qq_turn_payload(
        *,
        context: Any,
        event: dict[str, Any],
        message_override: str = "",
        action_note: str = "",
        extra_context_note: str = "",
    ) -> dict[str, Any]:
        turn_payload = context.to_turn_payload()
        turn_payload["timestamp"] = int(event.get("time") or time.time())
        if message_override:
            turn_payload["message"] = message_override
        _apply_qq_current_outfit_visual(turn_payload, qq_gateway=qq_gateway, context=context, engine=engine)
        if action_note:
            turn_payload["qq_action_note"] = action_note
        if extra_context_note:
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    extra_context_note,
                )
                if part
            )
        if context.reason == "qq_poke":
            log_event(
                "qq_poke_context",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                is_group=bool(context.is_group),
                group_id=int(getattr(context, "group_id", 0) or 0),
                event_user_id=str(event.get("user_id") or ""),
                event_sender_id=str(event.get("sender_id") or ""),
                event_operator_id=str(event.get("operator_id") or ""),
                event_target_id=str(event.get("target_id") or ""),
                event_self_id=str(event.get("self_id") or ""),
                resolved_user_id=int(getattr(context, "user_id", 0) or 0),
                sender_label=str(getattr(context, "sender_label", "") or ""),
                turn_message=str(turn_payload.get("message") or "")[:240],
            )
        return turn_payload

    async def _run_qq_turn_delivery(
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
    ) -> dict[str, Any]:
        remote_prefetch_result = await asyncio.to_thread(
            engine.prefetch_remote_media_links_for_message,
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            message=context.clean_message or context.raw_message,
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
            timestamp=int(event.get("time") or time.time()),
        )
        if (
            isinstance(remote_prefetch_result, dict)
            and str(remote_prefetch_result.get("followup_context") or "").strip()
        ):
            prefetch_context = str(remote_prefetch_result.get("followup_context") or "").strip()
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    "【链接素材预处理结果】\n" + prefetch_context,
                )
                if part
            )
        turn_result = await asyncio.to_thread(
            _process_qq_turn_streaming,
            engine=engine,
            qq_gateway=qq_gateway,
            context=context,
            turn_payload=turn_payload,
            config_module=config_module,
            tts_client=tts_client,
            gpt_sovits_client_factory=gpt_sovits_client_factory,
        )
        file_send_result = dict(turn_result.get("file_send_result") or {"ok": True, "count": 0, "results": []})
        for item in list(file_send_result.get("results") or []):
            generated_id = str(item.get("generated_id") or "").strip()
            if not generated_id:
                continue
            await asyncio.to_thread(
                engine.mark_generated_file_delivery,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                generated_id=generated_id,
                delivery_status="sent" if item.get("ok") else "failed",
                timestamp=int(time.time()),
            )
        return turn_result

    async def _run_qq_image_vision_followup(
        *,
        context: Any,
        event: dict[str, Any],
        attachment_ids: list[str],
        attachments_registered: list[dict[str, Any]],
        message_override: str = "",
        action_note: str = "",
    ) -> None:
        started_at = time.perf_counter()
        try:
            wait_result = await asyncio.to_thread(
                engine.wait_for_qq_attachments_settled,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachment_ids=attachment_ids,
                timeout_seconds=_qq_attachment_ready_wait_seconds(context, config_module),
            )
            pending_image_ids = _qq_pending_image_attachment_ids(attachments_registered, wait_result)
            kinds_by_id = wait_result.get("kinds_by_id") if isinstance(wait_result.get("kinds_by_id"), dict) else {}
            failed_image_ids = [
                str(item or "").strip()
                for item in list(wait_result.get("failed") or [])
                if str(item or "").strip() and str(kinds_by_id.get(str(item or "").strip()) or "").lower() == "image"
            ]
            if pending_image_ids or failed_image_ids:
                log_event(
                    "qq_image_vision_followup_not_ready",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    pending_count=len(pending_image_ids),
                    failed_count=len(failed_image_ids),
                    attachment_count=len(getattr(context, "attachments", None) or []),
                    attachments_registered=len(attachments_registered),
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )
                return

            effective_message_override = (
                message_override.strip() or "用户刚刚发送了一张图片。请只根据【本轮 QQ 图片内容】中的视觉摘要自然回应。"
            )
            turn_payload = _prepare_qq_turn_payload(
                context=context,
                event=event,
                message_override=effective_message_override,
                action_note=action_note,
                extra_context_note=_build_qq_image_vision_followup_note(
                    attachment_ids=attachment_ids,
                    wait_result=wait_result if isinstance(wait_result, dict) else {},
                    event_timestamp=int(event.get("time") or time.time()),
                ),
            )
            turn_result = await _run_qq_turn_delivery(context=context, event=event, turn_payload=turn_payload)
            send_result = dict(
                turn_result.get("send_result") or {"ok": False, "reason": "missing_send_result", "results": []}
            )
            log_event(
                "qq_image_vision_followup_sent",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                sent=bool(send_result.get("ok")),
                attachment_count=len(getattr(context, "attachments", None) or []),
                attachments_registered=len(attachments_registered),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
            )
        except Exception as exc:
            logger.exception("qq image vision followup failed")
            log_event(
                "qq_image_vision_followup_error",
                session_id=getattr(context, "session_id", ""),
                profile_user_id=getattr(context, "profile_user_id", ""),
                reason=str(exc)[:500],
                duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
            )

    @router.get("/api/qq/napcat/status")
    async def qq_napcat_status() -> JSONResponse:
        data = dict(qq_gateway.status())
        if finance_event_worker is not None and callable(getattr(finance_event_worker, "status", None)):
            data["finance_event_worker"] = finance_event_worker.status()
        return JSONResponse({"status": "ok", "data": data})

    @router.post("/api/qq/self-check")
    async def qq_self_check() -> JSONResponse:
        """QQ / NapCat 连通性自检。主动测试 OneBot HTTP API 可达性和鉴权，返回结构化诊断。"""
        result = qq_gateway.self_check()
        return JSONResponse({"status": "ok", "data": result})

    @router.post("/api/qq/napcat/event")
    async def qq_napcat_event(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        event: dict = {}
        context = None
        try:
            event = await request.json()
            if not bool(getattr(config_module, "QQ_BRIDGE_ENABLED", False)):
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "disabled", "message": "QQ bridge is disabled"})

            context = qq_gateway.build_message_context(event)
            if not context.should_respond:
                if bool(getattr(context, "should_record", False)):
                    recorder = getattr(engine, "record_passive_qq_message", None)
                    turn_payload = context.to_turn_payload()
                    turn_payload["timestamp"] = int(event.get("time") or time.time())
                    record_result = (
                        await asyncio.to_thread(recorder, turn_payload)
                        if callable(recorder)
                        else {"ok": False, "status": "recorder_unavailable"}
                    )
                    record_payload = record_result if isinstance(record_result, dict) else {}
                    record_ok = bool(record_payload.get("ok"))
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=record_ok,
                    )
                    log_event(
                        "qq_passive_group_message_recorded",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        group_id=int(getattr(context, "group_id", 0) or 0),
                        user_id=int(getattr(context, "user_id", 0) or 0),
                        reason=context.reason,
                        record_status=str(record_payload.get("status") or ""),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "recorded" if record_ok else "record_failed",
                            "reason": context.reason,
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                            "record_result": record_result,
                        }
                    )
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "ignored", "reason": context.reason})

            _qq_action_note = ""
            _qq_turn_message_override = ""
            _qq_turn_extra_context_note = ""
            _qq_native_user_images: list[dict[str, Any]] = []

            mface_config_result = qq_gateway.handle_mface_config_command(context, event)
            if isinstance(mface_config_result, dict):
                reply = str(mface_config_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_mface_config_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(mface_config_result.get("status") or ""),
                    command_ok=bool(mface_config_result.get("ok")),
                    character_pack_id=str(mface_config_result.get("character_pack_id") or ""),
                    emotion=str(mface_config_result.get("emotion") or ""),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_mface_config_command",
                        "command_status": str(mface_config_result.get("status") or ""),
                        "command_ok": bool(mface_config_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "character_pack_id": str(mface_config_result.get("character_pack_id") or ""),
                        "emotion": str(mface_config_result.get("emotion") or ""),
                        "mface": mface_config_result.get("mface"),
                        "send_result": send_result,
                    }
                )

            workspace_command = _parse_qq_workspace_command(qq_gateway, context.clean_message)
            if isinstance(workspace_command, dict):
                service_factory = getattr(engine, "_get_attachment_inbox_service", None)
                attachment_service = service_factory() if callable(service_factory) else None
                action = str(workspace_command.get("action") or "").strip()
                command_status = "ok"
                command_ok = True
                if action == "help":
                    reply = _build_qq_workspace_help_reply()
                elif attachment_service is None:
                    command_status = "service_unavailable"
                    command_ok = False
                    reply = "当前工作台服务不可用。"
                elif action == "list":
                    reply = _build_qq_workspace_list_reply(
                        attachment_service,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                    )
                elif action == "clear":
                    clear_result = attachment_service.clear_focus(
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        target=str(workspace_command.get("target") or "current"),
                        kind=str(workspace_command.get("kind") or "any"),
                        delete_storage=bool(workspace_command.get("delete_storage")),
                        timestamp=int(event.get("time") or time.time()),
                    )
                    command_ok = bool(clear_result.get("ok")) if isinstance(clear_result, dict) else False
                    command_status = "cleared" if command_ok else "not_found"
                    reply = _build_qq_workspace_clear_reply(
                        clear_result if isinstance(clear_result, dict) else {},
                        delete_storage=bool(workspace_command.get("delete_storage")),
                    )
                else:
                    command_status = "unknown_action"
                    command_ok = False
                    reply = "这个工作台指令暂时不支持。发送“工作台帮助”查看可用指令。"
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")) and command_ok,
                )
                log_event(
                    "qq_workspace_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_action=action,
                    command_status=command_status,
                    command_ok=command_ok,
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_workspace_command",
                        "command_action": action,
                        "command_status": command_status,
                        "command_ok": command_ok,
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )

            if _should_sync_workspace_state_to_llm(qq_gateway, context.clean_message):
                service_factory = getattr(engine, "_get_attachment_inbox_service", None)
                attachment_service = service_factory() if callable(service_factory) else None
                if attachment_service is not None:
                    _qq_turn_extra_context_note = _build_qq_workspace_state_context(
                        attachment_service,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                    )

            if finance_subscription_service is not None:
                finance_watchlist_command = finance_subscription_service.parse_watchlist_command(context.clean_message)
                if isinstance(finance_watchlist_command, dict):
                    finance_watchlist_result = await asyncio.to_thread(
                        finance_subscription_service.handle_watchlist_command,
                        context,
                        authorized=qq_gateway.can_manage_finance_subscription(context, event=event),
                    )
                    watchlist_payload = (
                        dict(finance_watchlist_result) if isinstance(finance_watchlist_result, dict) else {}
                    )
                    reply = str(watchlist_payload.get("reply") or "").strip()
                    send_result = (
                        qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                    )
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")) and bool(watchlist_payload.get("ok")),
                    )
                    log_event(
                        "qq_finance_watchlist_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_action=str(finance_watchlist_command.get("action") or ""),
                        command_status=str(watchlist_payload.get("status") or ""),
                        command_ok=bool(watchlist_payload.get("ok")),
                        subscription_id=str(watchlist_payload.get("subscription_id") or ""),
                        code=str(watchlist_payload.get("code") or ""),
                        watchlist_count=int(watchlist_payload.get("watchlist_count") or 0),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_finance_watchlist_command",
                            "command_action": str(finance_watchlist_command.get("action") or ""),
                            "command_status": str(watchlist_payload.get("status") or ""),
                            "command_ok": bool(watchlist_payload.get("ok")),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "subscription_id": str(watchlist_payload.get("subscription_id") or ""),
                            "code": str(watchlist_payload.get("code") or ""),
                            "watchlist_count": int(watchlist_payload.get("watchlist_count") or 0),
                            "send_result": send_result,
                        }
                    )

            finance_mode_command_result = await asyncio.to_thread(
                qq_gateway.handle_finance_mode_command,
                context,
                event=event,
                subscription_service=finance_subscription_service,
            )
            if isinstance(finance_mode_command_result, dict):
                reply = str(finance_mode_command_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")) and bool(finance_mode_command_result.get("ok")),
                )
                log_event(
                    "qq_finance_mode_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(finance_mode_command_result.get("status") or ""),
                    command_ok=bool(finance_mode_command_result.get("ok")),
                    finance_mode=str(finance_mode_command_result.get("finance_mode") or ""),
                    state_persisted=finance_mode_command_result.get("state_persisted"),
                    subscription_id=str(finance_mode_command_result.get("subscription_id") or ""),
                    subscription_status=str(finance_mode_command_result.get("subscription_status") or ""),
                    watchlist_count=int(finance_mode_command_result.get("watchlist_count") or 0),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_finance_mode_command",
                        "command_status": str(finance_mode_command_result.get("status") or ""),
                        "command_ok": bool(finance_mode_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "finance_mode": str(finance_mode_command_result.get("finance_mode") or ""),
                        "domain_profile": str(finance_mode_command_result.get("domain_profile") or ""),
                        "state_persisted": finance_mode_command_result.get("state_persisted"),
                        "subscription_id": str(finance_mode_command_result.get("subscription_id") or ""),
                        "subscription_status": str(finance_mode_command_result.get("subscription_status") or ""),
                        "watchlist_count": int(finance_mode_command_result.get("watchlist_count") or 0),
                        "send_result": send_result,
                    }
                )

            character_command_result = qq_gateway.handle_character_command(
                context,
                character_resource_service=getattr(engine, "desktop_pet_character_resources", None),
            )
            if isinstance(character_command_result, dict):
                reply = str(character_command_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_character_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(character_command_result.get("status") or ""),
                    command_ok=bool(character_command_result.get("ok")),
                    character_pack_id=str(character_command_result.get("character_pack_id") or ""),
                    state_persisted=character_command_result.get("state_persisted"),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_character_command",
                        "command_status": str(character_command_result.get("status") or ""),
                        "command_ok": bool(character_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "character_pack_id": str(character_command_result.get("character_pack_id") or ""),
                        "state_persisted": character_command_result.get("state_persisted"),
                        "send_result": send_result,
                    }
                )

            outfit_command_result = qq_gateway.handle_outfit_command(
                context,
                resource_manifest_builder=_build_qq_resource_manifest_builder(engine, context),
            )
            if isinstance(outfit_command_result, dict):
                if outfit_command_result.get("_llm_passthrough"):
                    _qq_action_note = str(outfit_command_result.get("qq_action_note") or "").strip()
                    _qq_turn_message_override = str(outfit_command_result.get("turn_message") or "").strip()
                    log_event(
                        "qq_outfit_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_status=str(outfit_command_result.get("status") or ""),
                        command_ok=bool(outfit_command_result.get("ok")),
                        character_pack_id=str(outfit_command_result.get("character_pack_id") or ""),
                        outfit_id=str(outfit_command_result.get("outfit_id") or ""),
                        state_persisted=outfit_command_result.get("state_persisted"),
                        llm_passthrough=True,
                        sent=False,
                    )
                else:
                    reply = str(outfit_command_result.get("reply") or "").strip()
                    send_result = (
                        qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                    )
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")),
                    )
                    log_event(
                        "qq_outfit_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_status=str(outfit_command_result.get("status") or ""),
                        command_ok=bool(outfit_command_result.get("ok")),
                        character_pack_id=str(outfit_command_result.get("character_pack_id") or ""),
                        outfit_id=str(outfit_command_result.get("outfit_id") or ""),
                        state_persisted=outfit_command_result.get("state_persisted"),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_outfit_command",
                            "command_status": str(outfit_command_result.get("status") or ""),
                            "command_ok": bool(outfit_command_result.get("ok")),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "character_pack_id": str(outfit_command_result.get("character_pack_id") or ""),
                            "outfit_id": str(outfit_command_result.get("outfit_id") or ""),
                            "state_persisted": outfit_command_result.get("state_persisted"),
                            "send_result": send_result,
                        }
                    )

            reply_mode_command_result = qq_gateway.handle_reply_mode_command(context)
            if isinstance(reply_mode_command_result, dict):
                reply = str(reply_mode_command_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_reply_mode_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(reply_mode_command_result.get("status") or ""),
                    command_ok=bool(reply_mode_command_result.get("ok")),
                    reply_mode=str(reply_mode_command_result.get("reply_mode") or ""),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_reply_mode_command",
                        "command_status": str(reply_mode_command_result.get("status") or ""),
                        "command_ok": bool(reply_mode_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "reply_mode": str(reply_mode_command_result.get("reply_mode") or ""),
                        "send_result": send_result,
                    }
                )

            chat_model_command = qq_gateway.parse_chat_model_command(context.clean_message)
            if isinstance(chat_model_command, dict):
                available_models: list[str] | None = None
                list_error = ""
                if str(chat_model_command.get("action") or "") == "list":
                    try:
                        settings = effective_settings_from_config(config_module)
                        available_models = await asyncio.to_thread(probe_model_ids, settings)
                    except Exception as exc:
                        list_error = redact_provider_error(exc)
                        available_models = []
                chat_model_command_result = qq_gateway.handle_chat_model_command(
                    context,
                    command=chat_model_command,
                    default_model=str(getattr(config_module, "CHAT_MODEL_NAME", "") or ""),
                    available_models=available_models,
                    list_error=list_error,
                )
                if isinstance(chat_model_command_result, dict):
                    reply = str(chat_model_command_result.get("reply") or "").strip()
                    send_result = (
                        qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                    )
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")),
                    )
                    log_event(
                        "qq_chat_model_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_status=str(chat_model_command_result.get("status") or ""),
                        command_ok=bool(chat_model_command_result.get("ok")),
                        chat_model=str(chat_model_command_result.get("chat_model") or ""),
                        has_model_override=bool(chat_model_command_result.get("chat_model_override")),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_chat_model_command",
                            "command_status": str(chat_model_command_result.get("status") or ""),
                            "command_ok": bool(chat_model_command_result.get("ok")),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "chat_model": str(chat_model_command_result.get("chat_model") or ""),
                            "has_model_override": bool(chat_model_command_result.get("chat_model_override")),
                            "send_result": send_result,
                        }
                    )

            _care_runtime = getattr(engine, "care_runtime", None)
            _char_resources = getattr(engine, "desktop_pet_character_resources", None)
            _shop_items = (
                _char_resources.load_care_shop_items(context.character_pack_id)
                if _char_resources and context.character_pack_id
                else None
            )
            economy_command_result = qq_gateway.handle_economy_command(
                context,
                care_runtime=_care_runtime,
                shop_items=_shop_items,
                now_ms=int(time.time() * 1000),
            )
            if isinstance(economy_command_result, dict):
                if economy_command_result.get("_llm_passthrough"):
                    # Economy action processed; hand off to LLM for the actual reply
                    economy_note = str(economy_command_result.get("qq_action_note") or "").strip()
                    _qq_action_note = "\n".join(part for part in [_qq_action_note, economy_note] if part)
                    # fall through to LLM pipeline below
                else:
                    reply = str(economy_command_result.get("reply") or "").strip()
                    send_result = (
                        qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                    )
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")),
                    )
                    log_event(
                        "qq_economy_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_status=str(economy_command_result.get("status") or ""),
                        command_ok=bool(economy_command_result.get("ok")),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_economy_command",
                            "command_status": str(economy_command_result.get("status") or ""),
                            "command_ok": bool(economy_command_result.get("ok")),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "send_result": send_result,
                        }
                    )

            quoted_result = await asyncio.to_thread(
                qq_gateway.resolve_quoted_attachments,
                event,
                context=context,
            )
            quoted_payload = quoted_result if isinstance(quoted_result, dict) else {}
            quoted_attachments = [
                dict(item) for item in list(quoted_payload.get("attachments") or []) if isinstance(item, dict)
            ]
            if quoted_attachments:
                context = replace(
                    context,
                    attachments=_merge_qq_attachments(context.attachments, quoted_attachments),
                )
            if str(quoted_payload.get("status") or "") != "not_quoted":
                log_event(
                    "qq_quoted_attachments_resolved",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    status=str(quoted_payload.get("status") or "unknown"),
                    ok=bool(quoted_payload.get("ok")),
                    attachment_count=len(quoted_attachments),
                )

            attachments_registered = []
            if context.attachments:
                attachments_registered = await asyncio.to_thread(
                    engine.ingest_qq_attachments,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachments=_with_qq_sender_context(list(context.attachments), context),
                    character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                    timestamp=int(event.get("time") or time.time()),
                )
                attachment_ids = [
                    str(item.get("attachment_id") or "").strip()
                    for item in attachments_registered
                    if isinstance(item, dict) and str(item.get("attachment_id") or "").strip()
                ]
                debounce_token = qq_gateway.register_attachment_debounce(context, attachment_ids=attachment_ids)
                if bool(debounce_token.get("enabled")):
                    await asyncio.sleep(float(debounce_token.get("delay_seconds") or 0.0))
                    debounce_result = qq_gateway.consume_attachment_debounce(debounce_token)
                    if not bool(debounce_result.get("process")):
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                        log_event(
                            "qq_napcat_event_buffered",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            reason=str(debounce_result.get("reason") or "attachment_debounce"),
                            attachment_count=len(context.attachments or []),
                            attachments_registered=len(attachments_registered),
                            duration_ms=round(duration_ms, 1),
                        )
                        return JSONResponse(
                            {
                                "status": "buffered",
                                "reason": str(debounce_result.get("reason") or "attachment_debounce"),
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                                "attachments_registered": len(attachments_registered),
                            }
                        )
                    attachment_ids = [
                        str(item or "").strip()
                        for item in list(debounce_result.get("attachment_ids") or attachment_ids)
                        if str(item or "").strip()
                    ]
                if attachment_ids:
                    has_image_attachment = any(
                        isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
                        for item in list(context.attachments or [])
                    )
                    if has_image_attachment:
                        native_prepare = getattr(engine, "prepare_qq_native_image_inputs", None)
                        native_result: dict[str, Any] = {}
                        if callable(native_prepare):
                            native_wait_seconds = max(
                                0.0,
                                min(
                                    15.0,
                                    float(getattr(config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                                ),
                            )
                            native_result = await asyncio.to_thread(
                                native_prepare,
                                profile_user_id=context.profile_user_id,
                                session_id=context.session_id,
                                attachment_ids=attachment_ids,
                                chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                                timeout_seconds=native_wait_seconds,
                            )
                        _qq_native_user_images = [
                            dict(item)
                            for item in list(native_result.get("images") or [])
                            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                        ][:5]
                        if _qq_native_user_images:
                            log_event(
                                "qq_native_multimodal_images_ready",
                                session_id=context.session_id,
                                profile_user_id=context.profile_user_id,
                                image_count=len(_qq_native_user_images),
                                skipped_count=len(list(native_result.get("skipped") or [])),
                                native_status=str(native_result.get("status") or "ready"),
                            )
                        else:
                            asyncio.create_task(
                                _run_qq_image_vision_followup(
                                    context=context,
                                    event=dict(event),
                                    attachment_ids=attachment_ids,
                                    attachments_registered=[
                                        item for item in attachments_registered if isinstance(item, dict)
                                    ],
                                    message_override=_qq_turn_message_override,
                                    action_note=_qq_action_note,
                                )
                            )
                            duration_ms = (time.perf_counter() - started_at) * 1000
                            runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                            log_event(
                                "qq_image_vision_followup_scheduled",
                                session_id=context.session_id,
                                profile_user_id=context.profile_user_id,
                                attachment_count=len(context.attachments or []),
                                attachments_registered=len(attachments_registered),
                                native_reason=str(native_result.get("reason") or "native_image_unavailable"),
                                duration_ms=round(duration_ms, 1),
                            )
                            return JSONResponse(
                                {
                                    "status": "buffered",
                                    "reason": "qq_image_vision_followup_scheduled",
                                    "session_id": context.session_id,
                                    "profile_user_id": context.profile_user_id,
                                    "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                                    "attachment_count": len(context.attachments or []),
                                    "attachments_registered": len(attachments_registered),
                                }
                            )
                    if not _qq_native_user_images:
                        attachment_wait_result = await asyncio.to_thread(
                            engine.wait_for_qq_attachments_settled,
                            profile_user_id=context.profile_user_id,
                            session_id=context.session_id,
                            attachment_ids=attachment_ids,
                            timeout_seconds=_qq_attachment_ready_wait_seconds(context, config_module),
                        )
                        pending_image_ids = _qq_pending_image_attachment_ids([], attachment_wait_result)
                        if pending_image_ids:
                            asyncio.create_task(
                                _run_qq_image_vision_followup(
                                    context=context,
                                    event=dict(event),
                                    attachment_ids=attachment_ids,
                                    attachments_registered=[
                                        item for item in attachments_registered if isinstance(item, dict)
                                    ],
                                    message_override=_qq_turn_message_override,
                                    action_note=_qq_action_note,
                                )
                            )
                            duration_ms = (time.perf_counter() - started_at) * 1000
                            runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                            log_event(
                                "qq_image_vision_followup_scheduled",
                                session_id=context.session_id,
                                profile_user_id=context.profile_user_id,
                                attachment_count=len(context.attachments or []),
                                attachments_registered=len(attachments_registered),
                                pending_image_count=len(pending_image_ids),
                                duration_ms=round(duration_ms, 1),
                            )
                            return JSONResponse(
                                {
                                    "status": "buffered",
                                    "reason": "qq_image_vision_followup_scheduled",
                                    "session_id": context.session_id,
                                    "profile_user_id": context.profile_user_id,
                                    "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                                    "attachment_count": len(context.attachments or []),
                                    "attachments_registered": len(attachments_registered),
                                    "pending_image_count": len(pending_image_ids),
                                }
                            )

            turn_payload = _prepare_qq_turn_payload(
                context=context,
                event=event,
                message_override=_qq_turn_message_override,
                action_note=_qq_action_note,
                extra_context_note=_qq_turn_extra_context_note,
            )
            if _qq_native_user_images:
                turn_payload["native_user_images"] = _qq_native_user_images
            turn_result = await _run_qq_turn_delivery(context=context, event=event, turn_payload=turn_payload)
            frame = dict(turn_result.get("frame") or {})
            reply_messages = list(turn_result.get("reply_messages") or [])
            send_result = dict(
                turn_result.get("send_result") or {"ok": False, "reason": "missing_send_result", "results": []}
            )
            emotion_mface_result = dict(
                turn_result.get("emotion_mface_result") or {"ok": True, "status": "skipped", "reason": "missing_result"}
            )
            emotion_image_result = dict(
                turn_result.get("emotion_image_result") or {"ok": True, "status": "skipped", "reason": "missing_result"}
            )
            file_send_result = dict(turn_result.get("file_send_result") or {"ok": True, "count": 0, "results": []})
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=False)
            logger.exception("qq_napcat_event failed")
            log_event(
                "qq_napcat_event_error",
                session_id=getattr(context, "session_id", ""),
                profile_user_id=getattr(context, "profile_user_id", ""),
                post_type=str(event.get("post_type") or "") if isinstance(event, dict) else "",
                message_type=str(event.get("message_type") or "") if isinstance(event, dict) else "",
                reason=str(exc),
                duration_ms=round(duration_ms, 1),
            )
            return JSONResponse(
                {
                    "status": "error",
                    "reason": "qq_event_processing_failed",
                    "message": str(exc)[:500],
                }
            )

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=bool(send_result.get("ok")))
        log_event(
            "qq_napcat_event",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
            reason=context.reason,
            sent=bool(send_result.get("ok")),
            attachment_count=len(context.attachments or []),
            attachments_registered=len(attachments_registered),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(
            {
                "status": "ok" if send_result.get("ok") else "send_failed",
                "reason": context.reason,
                "session_id": context.session_id,
                "profile_user_id": context.profile_user_id,
                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                # NapCat / OneBot HTTP report supports "quick operation" replies.
                # Do not include a "reply" field here, or it may send a second
                # aggregated message after our explicit send_replies() call.
                "sent_count": len(reply_messages),
                "attachment_count": len(context.attachments or []),
                "attachments_registered": len(attachments_registered),
                "send_result": send_result,
                "emotion_mface_result": emotion_mface_result,
                "emotion_image_result": emotion_image_result,
                "file_send_result": file_send_result,
            }
        )

    return router
