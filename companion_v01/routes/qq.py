from __future__ import annotations

from contextlib import ExitStack, nullcontext
from akane_plugin import PluginInvocationContext
from ..plugin_turn_intents import HostTurnIntent, HostTurnResult, PluginTurnError, apply_turn_intent, normalize_turn_intent

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from memcore import segment_speech

from ..deployment_security import AdminWriteAuth, QQChannelRuntimeConfig
from ..media_bridge_engine import redact_remote_media_urls_for_prompt
from ..local_capability_config import (
    approval_mode_for_capability,
    load_capability_config,
    save_capability_approval_modes,
)
from ..model_service_config import (
    effective_settings_from_config,
    effective_settings_from_runtime_settings,
    probe_model_ids,
    redact_provider_error,
)
from ..tts_provider_runtime import (
    GPT_SOVITS_PROVIDER_ID,
    resolve_tts_runtime_provider as _resolve_tts_runtime_provider,
    synthesize_tts_resolution,
    tts_resolution_failure,
)
from ..runtime_settings import runtime_setting
from ..durable_session_queue import DurableSessionWorkQueue, RetryableSessionWorkError, SessionWorkError
from ..host_completion_batch import (
    completion_batch_key,
    completion_input_fingerprint,
    completion_metadata,
    completion_timestamp,
    prepare_completion_turn,
    record_completion_delivery,
    record_completion_error,
)
from ..turn_coordination import cancellation_requested
from ..session_inbox import SessionInboxItem
from ..turn_coordination import SessionWorkQueue, TurnCoordinator
from ..qq_group_attention import AttentionTicket, QQGroupAttentionState
from ..plugin_api import (
    DIRECT_CONVERSATION_EVENT,
    GROUP_CONVERSATION_EVENT,
    POKE_CONVERSATION_EVENT,
)
from ..plugin_text_presentation import apply_plugin_text_presentation_policy
from ..workspace_management import clear_workspace_files, list_workspace_files
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
_QQ_ROUTE_BASE_RE = re.compile(r"^/api(?:/[A-Za-z0-9._-]+)+$")
_QQ_GROUP_PASSIVE_MEMORY_MODES = frozenset({"all", "denylist", "off"})
_QQ_STOP_COMMANDS = frozenset(
    {
        "/stop",
        "stop",
        "停",
        "停止",
        "停下",
        "停一下",
        "停一下吧",
        "停下来",
        "停下来吧",
        "先停",
        "先停下",
        "先停一下",
        "先停一下吧",
        "先停下来",
        "先停下来吧",
        "停止任务",
        "取消任务",
        "取消当前任务",
        "终止任务",
        "结束任务",
        "别做了",
        "先别做了",
        "不要继续了",
        "先不要继续了",
    }
)


def _is_qq_stop_command(value: Any) -> bool:
    normalized = re.sub(r"[\s，。！？!?、]+$", "", str(value or "").strip().lower())
    return normalized in _QQ_STOP_COMMANDS


def _normalize_qq_route_base(value: Any) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if _QQ_ROUTE_BASE_RE.fullmatch(normalized) is None:
        raise ValueError("invalid_qq_route_base")
    return normalized


def _resolve_group_passive_memory_policy(config_module: Any, group_id: Any) -> dict[str, Any]:
    """Resolve host-owned passive group memory policy without changing reply turns."""

    aliases = {
        # Passive history is context, not an execution permission. Old
        # allowlist deployments migrate to default recording so newly joined
        # groups cannot silently disappear from MemCore.
        "allowlist": "all",
        "whitelist": "all",
        "blacklist": "denylist",
        "enabled": "all",
        "disabled": "off",
    }
    raw_mode = str(getattr(config_module, "QQ_GROUP_PASSIVE_MEMORY_MODE", "all") or "all").strip().lower()
    mode = aliases.get(raw_mode, raw_mode)
    if mode not in _QQ_GROUP_PASSIVE_MEMORY_MODES:
        mode = "all"
    try:
        normalized_group_id = int(group_id or 0)
    except (TypeError, ValueError):
        normalized_group_id = 0
    raw_ids = str(getattr(config_module, "QQ_GROUP_PASSIVE_MEMORY_GROUP_IDS", "") or "")
    configured_ids: set[int] = set()
    for token in re.split(r"[,;，；\s]+", raw_ids):
        if not token.isdigit() or len(token) > 20:
            continue
        value = int(token)
        if value > 0:
            configured_ids.add(value)
    if normalized_group_id <= 0:
        enabled = False
    elif mode == "off":
        enabled = False
    elif mode == "denylist":
        enabled = normalized_group_id not in configured_ids
    else:
        enabled = True
    return {"enabled": enabled, "mode": mode}


def _qq_quoted_message_reference(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    quoted = source.get("quoted_message") if isinstance(source.get("quoted_message"), dict) else {}
    if not bool(source.get("ok")) or not quoted:
        return {}
    raw_actor_id = str(quoted.get("actor_id") or "").strip()
    actor_id = "assistant" if bool(quoted.get("actor_is_bot")) else (f"qq:{raw_actor_id}" if raw_actor_id else "")
    message_id = str(quoted.get("message_id") or source.get("message_id") or "").strip()
    excerpt = str(quoted.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not (actor_id or message_id or excerpt):
        return {}
    reference: dict[str, Any] = {
        "actor_id": actor_id,
        "actor_display_name": str(quoted.get("actor_label") or "").strip(),
        "message_id": message_id,
        "excerpt": excerpt[:1000],
    }
    try:
        timestamp = int(float(quoted.get("timestamp") or 0))
    except (TypeError, ValueError):
        timestamp = 0
    if timestamp > 0:
        reference["timestamp"] = timestamp
    conversation_kind = str(quoted.get("conversation_kind") or "").strip()
    conversation_id = str(quoted.get("conversation_id") or "").strip()
    if conversation_kind:
        reference["conversation_kind"] = conversation_kind
    if conversation_id:
        reference["conversation_id"] = conversation_id
    try:
        attachment_count = int(quoted.get("attachment_count") or 0)
    except (TypeError, ValueError):
        attachment_count = 0
    if attachment_count > 0:
        reference["attachment_count"] = attachment_count
    mentions: list[dict[str, Any]] = []
    seen_mentions: set[str] = set()
    for raw_mention in list(quoted.get("mentions") or [])[:16]:
        if not isinstance(raw_mention, dict):
            continue
        mention_id = str(raw_mention.get("actor_id") or "").strip()[:160]
        if not mention_id or mention_id in seen_mentions:
            continue
        seen_mentions.add(mention_id)
        mentions.append(
            {
                "actor_id": mention_id,
                "display_name": str(raw_mention.get("display_name") or "").strip()[:160],
                "is_assistant": bool(raw_mention.get("is_assistant")),
            }
        )
    if mentions:
        reference["mentions"] = mentions
    return reference


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


def _build_qq_unavailable_quote_context(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "").strip().lower()
    if not status or status in {"not_quoted", "resolved"}:
        return ""

    message_id = str(payload.get("message_id") or "").strip()
    lines = [
        "【本轮 QQ 引用消息状态】",
        "status: unavailable",
        "OneBot 未能取得这条引用消息的可靠原文。这只表示引用证据不可用，不表示本轮请求失败。",
    ]
    if message_id:
        lines.append(f"message_id: {json.dumps(message_id, ensure_ascii=False)}")
    lines.append(
        "请继续依据用户本轮可见文字正常回应，不要猜测引用内容；"
        "只有在缺少原文导致确实无法理解意图时，才自然说明暂时看不到被引用内容并请用户补充。"
    )
    return "\n".join(lines)


def _qq_structured_forward_references(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Keep resolved forward evidence separate from the user's message text."""

    references: list[dict[str, Any]] = []
    for item in list(payload.get("forwards") or []):
        if not isinstance(item, dict):
            continue
        entry = {
            key: item[key]
            for key in (
                "source_part_id", "source_message_id", "conversation_kind", "conversation_id",
                "forward_id", "ok", "status", "reason", "node_count",
            )
            if key in item
        }
        entry["nodes"] = [dict(node) for node in list(item.get("nodes") or []) if isinstance(node, dict)]
        references.append(entry)
    return tuple(references)


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


def _format_qq_generated_item(item: dict[str, Any]) -> str:
    handle = str(item.get("generated_handle") or item.get("generated_id") or "").strip()
    title = str(item.get("output_title") or handle or "未命名生成结果").strip()[:48]
    output_format = str(item.get("output_format") or item.get("file_ext") or "file").strip().lower()
    suffix_parts = [part for part in (handle, output_format, _qq_item_time_label(item)) if part]
    suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
    return f"{title}{suffix}"


def _build_qq_workspace_list_reply(engine: Any, *, profile_user_id: str, session_id: str) -> str:
    result = list_workspace_files(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        limit=30,
    )
    if str(result.get("status") or "") == "unavailable":
        return "当前工作台服务不可用。"
    attachments = [item for item in list(result.get("attachments") or []) if isinstance(item, dict)]
    generated_files = [item for item in list(result.get("generated_files") or []) if isinstance(item, dict)]
    lines = ["当前工作台"]
    if not attachments and not generated_files:
        lines.append("空。现在没有收到的材料或生成结果挂在工作台里。")
    else:
        if attachments:
            lines.append(f"收到的材料（{len(attachments)}）：")
            for index, item in enumerate(attachments[:15], start=1):
                lines.append(f"{index}. {_format_qq_workspace_item(item)}")
            if len(attachments) > 15:
                lines.append(f"还有 {len(attachments) - 15} 个收到的材料未显示。")
        if generated_files:
            lines.append(f"生成结果（{len(generated_files)}）：")
            for index, item in enumerate(generated_files[:15], start=1):
                lines.append(f"{index}. {_format_qq_generated_item(item)}")
            if len(generated_files) > 15:
                lines.append(f"还有 {len(generated_files) - 15} 个生成结果未显示。")
    failures = [item for item in list(result.get("failures") or []) if isinstance(item, dict)]
    if failures:
        lines.append(f"另有 {len(failures)} 个工作台区域暂时无法读取。")
    lines.append("")
    lines.append("清理工作台：收起收到的材料和生成结果")
    lines.append("清理最新材料：只收起最近收到的一个材料")
    lines.append("彻底清理工作台：删除 Akane 托管的文件并清空生成结果内容")
    return "\n".join(lines)


def _build_qq_workspace_help_reply() -> str:
    return "\n".join(
        [
            "工作台指令",
            "工作台 / 查看工作台：列出收到的材料和生成结果",
            "清理工作台：收起全部收到的材料和生成结果，不删除托管文件",
            "清理最新材料：只收起最近收到的一个材料",
            "清理工作台 file_001 / gen_001：收起指定材料或生成结果",
            "彻底清理工作台：删除 Akane 托管的附件和生成文件，并清空生成结果内容",
            "外部工作区中的源文件不会被越权删除；系统会明确报告保留结果。",
        ]
    )


def _build_qq_workspace_clear_reply(result: dict[str, Any], *, delete_storage: bool) -> str:
    attachment_result = result.get("attachments") if isinstance(result.get("attachments"), dict) else {}
    generated_result = result.get("generated_files") if isinstance(result.get("generated_files"), dict) else {}
    cleared = [item for item in list(attachment_result.get("cleared") or []) if isinstance(item, dict)]
    managed_generated = [item for item in list(generated_result.get("managed") or []) if isinstance(item, dict)]
    purged = [
        str(item or "").strip()
        for item in list(attachment_result.get("purged_files") or [])
        if str(item or "").strip()
    ]
    already_absent = [
        str(item or "").strip()
        for item in list(attachment_result.get("already_absent_files") or [])
        if str(item or "").strip()
    ]
    unresolved = [str(item or "").strip() for item in list(result.get("unresolved") or []) if str(item or "").strip()]
    failures = [item for item in list(result.get("failures") or []) if isinstance(item, dict)]
    cleaned_tasks = [item for item in list(result.get("cleaned_tasks") or []) if isinstance(item, dict)]
    lines = ["工作台清理结果"]
    if cleared or managed_generated:
        if cleared:
            lines.append(f"已收起收到的材料：{len(cleared)} 个。")
            for index, item in enumerate(cleared[:12], start=1):
                lines.append(f"{index}. {_format_qq_workspace_item(item)}")
            if len(cleared) > 12:
                lines.append(f"还有 {len(cleared) - 12} 个收到的材料未显示。")
        if managed_generated:
            action_label = "已彻底清理生成结果" if delete_storage else "已收起生成结果"
            lines.append(f"{action_label}：{len(managed_generated)} 个。")
            for index, item in enumerate(managed_generated[:12], start=1):
                lines.append(f"{index}. {_format_qq_generated_item(item)}")
            if len(managed_generated) > 12:
                lines.append(f"还有 {len(managed_generated) - 12} 个生成结果未显示。")
        lines.append("这些项目不会继续显示在当前文件工作台里。")
    else:
        lines.append("当前文件工作台已经是空的。")
    if delete_storage:
        lines.append(f"已删除附件托管副本：{len(purged)} 个。")
        if already_absent:
            lines.append(f"本来就没有本地副本：{len(already_absent)} 个。")
    if cleaned_tasks:
        lines.append(f"同时关闭关联的未收尾任务白板：{len(cleaned_tasks)} 个。")
    if failures:
        lines.append(f"未完全完成：{len(failures)} 项。")
        for failure in failures[:8]:
            target = str(failure.get("target") or failure.get("domain") or "工作台项目").strip()
            reason = str(failure.get("reason") or "操作失败。").strip()
            lines.append(f"- {target}：{reason}")
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


def _qq_sender_role(event: object) -> str:
    if not isinstance(event, dict):
        return ""
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    role = str(sender.get("role") or "").strip().lower()
    return role if role in {"owner", "admin", "member"} else ""


def _reply_prefix_at_boundary(text: str, prefix: str) -> bool:
    matched = ""
    for index, char in enumerate(text):
        matched += _canonical_reply_text(char)
        if matched == prefix:
            return index + 1 == len(text) or not _canonical_reply_text(text[index + 1])
        if not prefix.startswith(matched):
            return False
    return False


def _filter_unsent_reply_messages(messages: list[str], sent_messages: list[str]) -> list[str]:
    """Subtract delivered text across segment boundaries, preserving occurrences."""
    remaining = [line.strip() for item in sent_messages for line in str(item).splitlines()
                 if _canonical_reply_text(line)]
    unsent: list[str] = []
    for message in messages:
        text = str(message or "").strip()
        while text and remaining:
            canonical = _canonical_reply_text(text)
            best = None
            for start in range(len(remaining)):
                prefix = ""
                raw_prefix = ""
                for end in range(start, len(remaining)):
                    prefix += _canonical_reply_text(remaining[end])
                    raw_prefix += "\n" + remaining[end]
                    if not (canonical.startswith(prefix) or prefix.startswith(canonical)):
                        break
                    if not ((_reply_prefix_at_boundary(text, prefix) and canonical.startswith(prefix))
                            or (_reply_prefix_at_boundary(raw_prefix, canonical) and prefix.startswith(canonical))):
                        continue
                    matched = min(len(canonical), len(prefix))
                    if matched and (best is None or matched > best[0]):
                        best = (matched, start)
            if best is None:
                break
            count, start = best
            text = _strip_canonical_prefix(text, canonical[:count])
            while count and start < len(remaining):
                sent = _canonical_reply_text(remaining[start])
                if len(sent) <= count:
                    count -= len(sent)
                    remaining.pop(start)
                else:
                    remaining[start] = _strip_canonical_prefix(remaining[start], sent[:count])
                    count = 0
        if text:
            unsent.append(text)
    return unsent


class _StageSpeechReplay:
    """Skip an exact leading replay across model stages without buffering speech.

    New text leaves push() immediately. Repetitions within one stage retain
    their count; only prefixes already accepted by an earlier stage are skipped.
    """

    def __init__(self):
        self.previous: list[str] = []
        self.current = ""
        self.candidates: list[str] = []

    def push(self, text: str) -> str:
        before = _canonical_reply_text(self.current)
        self.current += "\n" + text
        canonical = _canonical_reply_text(self.current)
        continuing = [old for old in self.candidates if _canonical_reply_text(old).startswith(canonical)
                      and _reply_prefix_at_boundary(old, canonical)]
        completed = [old for old in self.candidates if canonical.startswith(_canonical_reply_text(old))
                     and len(_canonical_reply_text(old)) > len(before)
                     and _reply_prefix_at_boundary(self.current, _canonical_reply_text(old))]
        if continuing:
            self.candidates = continuing
            return ""
        self.candidates = []
        if completed:
            longest = max((_canonical_reply_text(old) for old in completed), key=len)
            return _strip_canonical_prefix(text, longest[len(before):])
        return text

    def finish_stage(self) -> None:
        if self.current and self.current not in self.previous:
            self.previous.append(self.current)
        self.current = ""
        self.candidates = list(self.previous)


def _send_pending_stage_messages(
    *,
    qq_gateway: Any,
    context: Any,
    pending_messages: list[str],
    deferred_messages: list[str],
    streamed_messages: list[str],
    stream_send_results: list[dict[str, Any]],
    max_streamed: int,
) -> list[str]:
    for text in pending_messages:
        normalized = _normalize_reply_text(text)
        if not normalized:
            continue
        chunks = _split_reply_for_transport(text)
        for chunk in chunks:
            if not chunk:
                continue
            if max_streamed > 0 and len(streamed_messages) >= max_streamed:
                deferred_messages.append(chunk)
                continue
            result = qq_gateway.send_reply(context, chunk)
            stream_send_results.append(result)
            if bool(result.get("ok")):
                streamed_messages.append(chunk)
            else:
                # A failed immediate send is still model-authored content.
                # Keep it for the ordinary end-of-turn delivery attempt.
                deferred_messages.append(chunk)
    return []


def _coalesce_deferred_stream_messages(messages: list[str], *, max_chars: int = 1800) -> list[str]:
    """Merge delayed stream segments without deleting model-authored text."""

    visible = [str(item or "") for item in messages if str(item or "").strip()]
    if not visible:
        return []
    joined = "\n".join(visible).strip()
    return _split_reply_for_transport(joined, max_chars=max_chars)


def _split_reply_for_transport(text: str, *, max_chars: int = 1800) -> list[str]:
    """Coalesce safe segments; the delivery target must not break nested pairs."""
    parts = segment_speech(text, min_chars=1, max_chars=max_chars)
    chunks: list[str] = []
    for part in parts:
        if chunks and len(chunks[-1]) + len(part) + 1 <= max_chars:
            chunks[-1] += "\n" + part
        else:
            chunks.append(part)
    return chunks


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


def _streaming_allows_tool_preface(reply_mode: str, delivery_hint: str) -> bool:
    """Allow a native-tool preface unless QQ has explicitly selected voice-only."""

    mode = _normalize_reply_medium(reply_mode) or "auto"
    hint = _normalize_reply_medium(delivery_hint)
    if mode == "auto":
        # The final reply_medium is not necessarily known when the tool-call
        # stage arrives.  An empty hint must not swallow the preface; only an
        # explicit voice hint means that this text should stay out of QQ.
        return hint != "voice"
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


def _resolve_qq_tts_profile_user_id(*, config_module: Any, context: Any, settings: Any = None) -> str:
    from ..tts_connection_source import qq_voice_config_profile

    return qq_voice_config_profile(config_module, context, settings)


def _voice_cache_key(*, text: str, synthesis: Any) -> str:
    """Name the delivered artifact from the effective synthesis inputs."""
    raw = (
        f"{text}\nprovider={synthesis.provider_id}\nprofile={synthesis.voice_profile_id}"
        f"\nemotion={synthesis.emotion}\nemotion_voice={synthesis.emotion_voice_id}"
        f"\nprofile_fingerprint={synthesis.profile_fingerprint}"
    )
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
    emotion: str = "",
    settings: Any = None,
) -> dict[str, Any]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"ok": False, "reason": "empty_voice_text"}

    data_dir = Path(str(getattr(config_module, "DATA_DIR", "users_data") or "users_data"))
    base_dir = Path(getattr(engine, "capability_config_base_dir", None) or data_dir)
    tts_profile_user_id = _resolve_qq_tts_profile_user_id(
        config_module=config_module,
        context=context,
        settings=settings,
    )
    payload = {
        "text": clean_text,
        "real_user_id": str(getattr(context, "profile_user_id", "") or tts_profile_user_id),
        "profile_user_id": str(getattr(context, "profile_user_id", "") or tts_profile_user_id),
        "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
        "session_id": str(getattr(context, "session_id", "") or ""),
        "client_mode": "qq",
        "emotion": str(emotion or "").strip(),
    }
    resolution = _resolve_tts_runtime_provider(
        engine=engine,
        payload=payload,
    )

    active_provider = str(resolution.get("activeProviderId") or "")
    requested_provider = str(resolution.get("requestedProviderId") or "")
    failure = tts_resolution_failure(resolution)
    if failure:
        return {
            "ok": False,
            "reason": failure,
            "provider": active_provider,
            "requested_provider": requested_provider,
            "resolution_status": str(resolution.get("status") or ""),
            "tts_profile_user_id": tts_profile_user_id,
        }
    from companion_v01.tts_service import TTSServiceError
    try:
        synthesis = _run_async_safely(synthesize_tts_resolution(
            resolution=resolution, text=clean_text, payload=payload,
        ))
    except TTSServiceError as exc:
        return {"ok": False, "reason": exc.reason, "provider": "",
                "requested_provider": requested_provider, "resolution_status": exc.status,
                "tts_profile_user_id": tts_profile_user_id}

    cache_dir = data_dir / "qq_voice_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = _media_type_extension(synthesis.media_type)
    cache_key = _voice_cache_key(text=clean_text, synthesis=synthesis)
    path = cache_dir / f"{cache_key}.{ext}"
    temp_path = cache_dir / f".{cache_key}.{uuid.uuid4().hex}.tmp"
    try:
        temp_path.write_bytes(synthesis.audio)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    _prune_voice_cache(cache_dir, max_mb=50)
    return {
        "ok": True,
        "path": str(path),
        "media_type": synthesis.media_type,
        "provider": active_provider,
        "emotion": synthesis.emotion,
        "emotion_voice_id": synthesis.emotion_voice_id,
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
    delivery_hint: str = "",
    settings: Any = None,
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

    if text_enabled and unsent_reply_messages:
        text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)

    voice_reason = ""
    if voice_enabled:
        max_segments = int(
            runtime_setting(settings, config_module, "qq_voice_max_segments", "QQ_VOICE_MAX_SEGMENTS", 3) or 3
        )
        voice_messages = [str(message or "").strip() for message in reply_messages if str(message or "").strip()]
        voice_text = "\n".join(voice_messages[:max_segments]).strip()
        max_auto_chars = int(
            runtime_setting(settings, config_module, "qq_voice_max_text_chars", "QQ_VOICE_MAX_TEXT_CHARS", 280) or 280
        )
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
                    emotion=str(frame.get("emotion") or ""),
                    settings=settings,
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
    settings: Any = None,
    prepared_frame: dict[str, Any] | None = None,
    additional_delivery_events: list[dict[str, Any]] | Callable | None = None,
    file_target_allowed: Callable | None = None,
) -> dict[str, Any]:
    timing_started_at = time.perf_counter()
    timing: dict[str, float] = {}
    # Add live, Bot-bound session identity only when a queued/direct/ambient
    # turn actually runs. Do not rewrite its saved message or quoted history.
    turn_payload = dict(turn_payload)
    identity_builder = getattr(qq_gateway, "build_group_identity_context", None)
    if bool(getattr(context, "is_group", False)) and callable(identity_builder):
        identity_context = identity_builder(int(getattr(context, "group_id", 0) or 0))
        turn_payload["extra_context"] = "\n\n".join(
            part for part in (identity_context, str(turn_payload.get("extra_context") or "").strip()) if part
        )
    pending_stage_messages: list[str] = []
    deferred_stage_messages: list[str] = []
    streamed_messages: list[str] = []
    stream_send_results: list[dict[str, Any]] = []
    streamed_delivery_events: list[dict[str, Any]] = []
    frame: dict[str, Any] = dict(prepared_frame) if prepared_frame is not None else {}
    final_frame_received = prepared_frame is not None
    model_status = "not_requested"
    tool_preface_delivered = False
    final_streamed_text_delivered = False
    current_stage_streamed_count = 0
    stage_replay = _StageSpeechReplay()
    delivery_hint = ""
    single_message_delivery = (
        str(turn_payload.get("plugin_text_delivery") or "").strip().lower() == "single_message"
    )
    active_reply_mode = (
        _normalize_reply_medium(getattr(context, "reply_mode", ""))
        or _normalize_reply_medium(qq_gateway.resolve_reply_mode(getattr(context, "session_id", "")))
        or "auto"
    )
    max_streamed = max(
        0,
        min(
            100,
            int(
                getattr(
                    config_module,
                    "QQ_STREAM_IMMEDIATE_SEGMENTS",
                    0,
                )
                or 0
            ),
        ),
    )
    # Streaming enablement and an optional transport quota are separate
    # decisions.  Zero means no quota; it must not silently disable the
    # model-authored progress stream.
    stream_enabled = bool(getattr(config_module, "QQ_STREAM_REPLIES_ENABLED", True)) and not single_message_delivery

    engine_started_at = time.perf_counter()
    run_model = prepared_frame is None and not cancellation_requested()
    if run_model:
        model_status = "running"
    for stream_event in engine.process_turn_stream(turn_payload) if run_model else ():
        if not isinstance(stream_event, dict):
            continue
        event_type = str(stream_event.get("type") or "").strip()
        if event_type in {"generated_file_ready", "file_ready"}:
            streamed_delivery_events.append(dict(stream_event))
        if event_type == "delivery_hint":
            delivery_hint = _normalize_reply_medium(stream_event.get("medium")) or delivery_hint
            continue
        if event_type == "speech_segment" and stream_enabled:
            text = str(stream_event.get("text") or "").strip()
            if not text:
                continue
            if not _streaming_allows_tool_preface(active_reply_mode, delivery_hint):
                continue
            text = stage_replay.push(text)
            normalized = _normalize_reply_text(text)
            if not normalized or normalized in {_normalize_reply_text(item) for item in pending_stage_messages}:
                continue
            pending_stage_messages.append(text)
            streamed_before = len(streamed_messages)
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                deferred_messages=deferred_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            current_stage_streamed_count += max(0, len(streamed_messages) - streamed_before)
            continue
        if event_type == "assistant_stage_decision":
            stage_replay.finish_stage()
            has_tool_call = bool(stream_event.get("has_tool_call"))
            if has_tool_call and current_stage_streamed_count:
                tool_preface_delivered = True
            elif not has_tool_call and current_stage_streamed_count:
                final_streamed_text_delivered = True
            current_stage_streamed_count = 0
            if not pending_stage_messages:
                continue
            text_allowed = (
                _streaming_allows_tool_preface(active_reply_mode, delivery_hint)
                if has_tool_call
                else _streaming_allows_text(active_reply_mode, delivery_hint)
            )
            if not text_allowed:
                if has_tool_call:
                    pending_stage_messages = []
                continue
            streamed_before = len(streamed_messages)
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                deferred_messages=deferred_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            if len(streamed_messages) > streamed_before:
                tool_preface_delivered = True
            continue
        if event_type == "turn_stopped":
            model_status = "stopped"
            frame = dict(stream_event.get("payload") or {})
            frame["_turn_stopped"] = True
            final_frame_received = True
            pending_stage_messages = []
            continue
        if event_type == "final_ui" and isinstance(stream_event.get("payload"), dict):
            frame = dict(stream_event.get("payload") or {})
            model_status = "failed" if frame.get("_transient_final_failure") else "stopped" if frame.get("status") == "stopped" else "completed"
            final_frame_received = True
            if (
                pending_stage_messages
                and bool(frame.get("_transient_final_failure"))
                and _streaming_allows_text(active_reply_mode, delivery_hint)
            ):
                # The final contract failed after real speech was parsed.  Keep
                # that truthful partial delivery, while the generic fallback in
                # the transient frame remains suppressed below.
                pending_stage_messages = _send_pending_stage_messages(
                    qq_gateway=qq_gateway,
                    context=context,
                    pending_messages=pending_stage_messages,
                    deferred_messages=deferred_stage_messages,
                    streamed_messages=streamed_messages,
                    stream_send_results=stream_send_results,
                    max_streamed=max_streamed,
                )

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
            deferred_messages=deferred_stage_messages,
            streamed_messages=streamed_messages,
            stream_send_results=stream_send_results,
            max_streamed=max_streamed,
        )

    if prepared_frame is None and not frame and not streamed_messages and not cancellation_requested():
        frame = engine.process_turn(turn_payload)
        final_frame_received = bool(frame)
        model_status = "failed" if frame.get("_transient_final_failure") else "stopped" if frame.get("status") == "stopped" else "completed" if frame else "failed"
    # The generator includes provider calls, tool execution, and any streamed
    # QQ sends performed while it yields. Keep the label honest: this is not a
    # provider-only latency measurement.
    timing["turn_processing_ms"] = round((time.perf_counter() - engine_started_at) * 1000, 1)

    apply_plugin_text_presentation_policy(
        frame,
        strip_leading_addresses_from=turn_payload.get(
            "plugin_text_strip_leading_addresses",
            (),
        ),
    )

    if cancellation_requested():
        frame = {"status": "stopped", "speech": "", "speech_segments": [], "tool_events": [],
                 "reason": str(frame.get("reason") or "user_stopped"),
                 "_turn_stopped": True, "_deliberate_silence": True}
        streamed_delivery_events = []
        final_frame_received = True
    extra_events = additional_delivery_events() if callable(additional_delivery_events) else additional_delivery_events
    frame_delivery_events = [*(frame.get("tool_events") if isinstance(frame.get("tool_events"), list) else []),
                             *(extra_events or [])]
    visible_action_delivered = _qq_has_visible_action_receipt(frame_delivery_events)
    deliberate_silence = bool(frame.get("_deliberate_silence") or frame.get("_notification_suppressed"))
    retained_frame_events = [
        dict(event)
        for event in frame_delivery_events
        if isinstance(event, dict)
        and str(event.get("type") or "").strip() not in {"generated_file_ready", "file_ready"}
    ]
    merged_file_events: list[dict[str, Any]] = []
    delivery_event_indexes: dict[tuple[str, str], int] = {}
    for raw_event in [*streamed_delivery_events, *frame_delivery_events]:
        if not isinstance(raw_event, dict):
            continue
        event = dict(raw_event)
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"generated_file_ready", "file_ready"}:
            continue
        identity = _qq_file_delivery_event_identity(event)
        if identity:
            existing_index = delivery_event_indexes.get(identity)
            if existing_index is not None:
                existing = merged_file_events[existing_index]
                # One physical artifact may first appear as a generation
                # receipt (send_to_user=false) and later as an explicit
                # send_file handoff.  The delivery decision is the stronger,
                # later state and must not be discarded as a duplicate.
                if bool(event.get("send_to_user")) and not bool(existing.get("send_to_user")):
                    merged_file_events[existing_index] = event
                continue
            delivery_event_indexes[identity] = len(merged_file_events)
        merged_file_events.append(event)
    if merged_file_events:
        frame["tool_events"] = [*retained_frame_events, *merged_file_events]

    delivery_events, artifact_resolution_failures = _hydrate_plugin_managed_artifact_events(
        engine=engine,
        context=context,
        tool_events=list(frame.get("tool_events") or []),
    )
    file_delivery_started_at = time.perf_counter()
    file_send_result = qq_gateway.send_generated_files(
        context,
        delivery_events,
        **({"target_allowed": file_target_allowed} if file_target_allowed else {}),
    )
    if artifact_resolution_failures:
        delivery_results = [
            *list(file_send_result.get("results") or []),
            *artifact_resolution_failures,
        ]
        any_sent = any(bool(item.get("ok")) for item in delivery_results)
        file_send_result = {
            "ok": False,
            "status": "partial" if any_sent else "failed",
            "count": len(delivery_results),
            "results": delivery_results,
        }

    file_delivery_attempted = int(file_send_result.get("count") or 0) > 0
    file_delivery_failed = file_delivery_attempted and not bool(file_send_result.get("ok"))
    timing["file_delivery_ms"] = round((time.perf_counter() - file_delivery_started_at) * 1000, 1)
    # Deliver artifacts before the final text so transport feedback can follow
    # the model's completed reply.  An auxiliary delivery failure must not turn
    # a successfully completed LLM turn into an apparent system crash.
    deferred_stream_messages = _coalesce_deferred_stream_messages(
        [*deferred_stage_messages, *pending_stage_messages]
    )
    final_reply_messages = (
        []
        if bool(frame.get("_transient_final_failure") or deliberate_silence)
        else qq_gateway.render_reply_messages(frame)
    )
    if single_message_delivery and final_reply_messages:
        body = str(frame.get("speech") or "").strip() or "\n".join(final_reply_messages).strip()
        prefix = str(turn_payload.get("plugin_text_prefix") or "").strip()
        suffix = str(turn_payload.get("plugin_text_suffix") or "").strip()
        if prefix and body.startswith(prefix):
            body = body[len(prefix) :].lstrip()
        if suffix and body.endswith(suffix):
            body = body[: -len(suffix)].rstrip()
        final_reply_messages = ["\n".join(part for part in (prefix, body, suffix) if part)] if body else []
    reply_messages = final_reply_messages
    if streamed_messages and bool(frame.get("_transient_final_failure")):
        # A complete speech field may already have reached QQ before a malformed
        # JSON tail forces the final frame to its generic persona fallback. The
        # model's speech remains the sole body authority; never append that
        # host fallback as a second, contradictory bubble.
        unsent_reply_messages = deferred_stream_messages
    else:
        final_tail_messages = _filter_unsent_reply_messages(
            reply_messages,
            [*streamed_messages, *deferred_stream_messages],
        )
        unsent_reply_messages = [*deferred_stream_messages, *final_tail_messages]
    delivered_reply_messages = [*streamed_messages, *unsent_reply_messages]
    text_delivery_started_at = time.perf_counter()
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
        delivery_hint=delivery_hint,
        settings=settings,
    )
    timing["text_and_voice_delivery_ms"] = round((time.perf_counter() - text_delivery_started_at) * 1000, 1)
    visible_text_delivered = bool(streamed_messages or unsent_reply_messages)
    visible_file_delivered = bool(file_send_result.get("count") or 0) and bool(file_send_result.get("ok"))
    final_failure_notice_result = {"ok": True, "status": "skipped", "reason": "visible_delivery_present"}
    # Plugin events are durable, host-initiated work.  Their caller receives
    # the structured failure below and owns retry/backoff; sending the
    # interactive "please try again" notice would expose an internal retry as
    # a user-facing message and can spam the destination on every attempt.
    # Successful plugin replies still use the exact same rendering path.
    surface_incomplete_notice = (
        str(turn_payload.get("turn_kind") or "").strip().lower() != "plugin_event"
    )
    if surface_incomplete_notice and not bool(frame.get("_turn_stopped") or deliberate_silence) and (
        (not visible_text_delivered and not visible_file_delivered and not visible_action_delivered)
        or (not final_frame_received and not visible_file_delivered and not visible_action_delivered)
        or (
            bool(frame.get("_transient_final_failure"))
            and tool_preface_delivered
            and not final_streamed_text_delivered
            and not visible_file_delivered
            and not visible_action_delivered
        )
    ):
        # A streamed tool preface or emotion is not an authoritative final
        # reply.  If the turn ends without a final frame or delivered file,
        # surface a transport-level status instead of silently treating the
        # provisional text as completion.  Do not rerun the turn here: a tool
        # may already have produced irreversible side effects.
        final_failure_notice = (
            "这次没有形成可交付的文字结果，刚才的处理没有完整结束。请再试一次。"
        )
        final_failure_notice_result = qq_gateway.send_reply(context, final_failure_notice)
        if final_failure_notice_result.get("ok"):
            reply_messages.append(final_failure_notice)
            delivered_reply_messages.append(final_failure_notice)
            send_result = dict(send_result)
            send_result_results = [
                *list(send_result.get("results") or []),
                dict(final_failure_notice_result),
            ]
            send_result.update(
                {
                    "ok": all(bool(item.get("ok")) for item in send_result_results),
                    "count": len(send_result_results),
                    "results": send_result_results,
                    "final_failure_notice": True,
                }
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
            "final_failure_notice": bool(send_result.get("final_failure_notice")),
        }
    if bool(frame.get("_turn_stopped")):
        send_result = {
            "ok": True,
            "status": "stopped",
            "reason": str(frame.get("reason") or "user_stopped"),
            "count": 0,
            "results": [],
        }

    emotion_image_result = {"ok": True, "status": "skipped", "reason": "not_attempted"}
    emotion_started_at = time.perf_counter()
    if frame.get("_emotion_model_authored") is False:
        emotion_mface_result = {
            "ok": True,
            "status": "skipped",
            "reason": "emotion_not_model_authored",
        }
        emotion_image_result = dict(emotion_mface_result)
    elif (
        send_result.get("ok")
        and not bool(frame.get("_transient_final_failure") or deliberate_silence)
        and (
        visible_text_delivered
        or visible_file_delivered
        or bool(final_failure_notice_result.get("ok") and final_failure_notice_result.get("status") != "skipped")
        )
    ):
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
            "reason": (
                str(frame.get("_notification_suppressed") or "model_silence")
                if deliberate_silence
                else "main_delivery_failed"
            ),
        }
    timing["emotion_delivery_ms"] = round((time.perf_counter() - emotion_started_at) * 1000, 1)

    final_reply_fallback_result = {"ok": True, "status": "skipped", "reason": "final_reply_present"}
    if not final_reply_messages and int(file_send_result.get("count") or 0) > 0:
        if bool(file_send_result.get("ok")):
            fallback_text = "结果已经生成，我发给你了。"
            final_reply_fallback_result = qq_gateway.send_reply(context, fallback_text)
            final_reply_fallback_result["status"] = (
                "generated_result_notice_sent" if final_reply_fallback_result.get("ok")
                else "generated_result_notice_failed"
            )
            if final_reply_fallback_result.get("ok"):
                reply_messages.append(fallback_text)
                delivered_reply_messages.append(fallback_text)
                send_result = dict(send_result)
                send_result_results = [
                    *list(send_result.get("results") or []),
                    dict(final_reply_fallback_result),
                ]
                send_result.update(
                    {
                        "ok": all(bool(item.get("ok")) for item in send_result_results),
                        "count": len(send_result_results),
                        "results": send_result_results,
                    }
                )
        else:
            final_reply_fallback_result = {
                "ok": True,
                "status": "skipped",
                "reason": "delivery_failure_notice_will_be_sent",
            }
    file_delivery_feedback_result = {"ok": True, "status": "skipped", "reason": "no_delivery_issue"}
    file_delivery_status = str(file_send_result.get("status") or "").strip().lower()
    _sid = str(getattr(context, "session_id", "") or "")
    if file_delivery_status == "partial":
        file_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "这次只有一部分音频/文件发送成功，完整生成结果仍保留在工作台，可以稍后补发。",
        )
        file_delivery_feedback_result["status"] = (
            "partial_notice_sent" if file_delivery_feedback_result.get("ok") else "partial_notice_failed"
        )
        qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】生成结果只发送成功一部分，完整文件仍在工作台。")
    elif file_delivery_status == "failed" or (
        int(file_send_result.get("count") or 0) > 0 and not bool(file_send_result.get("ok"))
    ):
        file_delivery_feedback_result = qq_gateway.send_reply(
            context,
            "文件这次发送失败了，现有结果仍保留着，可以稍后再试。",
        )
        notice_sent = bool(file_delivery_feedback_result.get("ok"))
        file_delivery_feedback_result["status"] = "failure_notice_sent" if notice_sent else "failure_notice_failed"
        notice_outcome = "失败提示已发送。" if notice_sent else "失败提示也未发送成功。"
        qq_gateway.add_delivery_note(_sid, "【上一轮交付状态】文件发送失败，文件仍在工作台。" + notice_outcome)
    elif int(file_send_result.get("count") or 0) > 0 and bool(file_send_result.get("ok")):
        qq_gateway.add_delivery_note(
            _sid,
            f"【上一轮交付状态】文件发送成功（共 {file_send_result.get('count', 0)} 个）。",
        )
    sticker_started_at = time.perf_counter()
    sticker_send_result = qq_gateway.send_stickers(
        context,
        list(frame.get("tool_events") or []),
    )
    timing["sticker_delivery_ms"] = round((time.perf_counter() - sticker_started_at) * 1000, 1)
    if (
        deliberate_silence
        and not delivered_reply_messages
        and not visible_file_delivered
        and not visible_action_delivered
        and not (sticker_send_result.get("ok") and sticker_send_result.get("count"))
        and send_result.get("ok")
    ):
        send_result = {
            **send_result,
            "status": "suppressed",
            "reason": str(frame.get("_notification_suppressed") or "model_silence"),
        }
    timing["total_ms"] = round((time.perf_counter() - timing_started_at) * 1000, 1)
    if cancellation_requested():
        frame["status"] = "stopped"
    # A successfully transmitted error notice is not a completed model reply.
    # Preserve transport facts separately so partial speech/files stay auditable.
    model_incomplete = not final_frame_received or bool(frame.get("_transient_final_failure"))
    memory_failure = frame.get("_memcore_failure")
    persistence_failed = isinstance(memory_failure, dict) and memory_failure.get("reason") == "input_turn_completion_failed"
    if not bool(frame.get("_turn_stopped") or frame.get("status") == "stopped") and (
        model_incomplete or persistence_failed
    ):
        if model_incomplete:
            model_status = "failed"
        send_result = {
            **send_result,
            "ok": False,
            "status": "failed",
            "reason": "model_turn_incomplete" if model_incomplete else "reply_persistence_failed",
            "transport_ok": bool(send_result.get("ok")) and bool(final_failure_notice_result.get("ok")),
            "failure_notice_sent": bool(final_failure_notice_result.get("ok"))
                and final_failure_notice_result.get("status") != "skipped",
            "partial_delivery": bool(streamed_messages or visible_file_delivered or visible_action_delivered
                                     or (unsent_reply_messages and send_result.get("ok"))),
        }
    return {
        "frame": frame,
        "reply_messages": delivered_reply_messages,
        "send_result": send_result,
        "emotion_mface_result": emotion_mface_result,
        "emotion_image_result": emotion_image_result,
        "file_send_result": file_send_result,
        "final_reply_fallback_result": final_reply_fallback_result,
        "file_delivery_feedback_result": file_delivery_feedback_result,
        "final_failure_notice_result": final_failure_notice_result,
        "sticker_send_result": sticker_send_result,
        "visible_action_delivered": visible_action_delivered,
        "final_frame_received": bool(final_frame_received),
        "model_status": model_status,
        "text_suppression": {
            "status": "suppressed" if frame.get("_notification_suppressed") else "not_suppressed",
            "reason": str(frame.get("_notification_suppressed") or ""),
        },
        "timing": timing,
    }


def _qq_file_delivery_event_identity(event: dict[str, Any]) -> tuple[str, str] | None:
    """Identify one physical file across streamed and final-frame event shapes."""

    event_type = str(event.get("type") or "").strip()
    if event_type == "generated_file_ready":
        item = event.get("generated_file") if isinstance(event.get("generated_file"), dict) else {}
        source_type = "generated"
    elif event_type == "file_ready":
        item = event.get("file") if isinstance(event.get("file"), dict) else {}
        source_type = str(item.get("source_type") or "").strip().lower() or "file"
    else:
        return None
    source_id = str(
        item.get("generated_id")
        or item.get("attachment_id")
        or item.get("source_id")
        or item.get("generated_handle")
        or item.get("attachment_handle")
        or item.get("handle")
        or item.get("absolute_path")
        or ""
    ).strip()
    return (source_type, source_id) if source_id else None


def _qq_has_visible_action_receipt(events: Any) -> bool:
    """Recognize only a successful receipt emitted by the OneBot write surface."""

    return any(
        isinstance(event, dict)
        and str(event.get("type") or "").strip() == "qq_visible_action_receipt"
        and bool(event.get("ok"))
        and str(event.get("status") or "").strip().lower() not in {"failed", "invalid", "unavailable"}
        for event in list(events or [])
    )


def _hydrate_plugin_managed_artifact_events(
    *,
    engine: Any,
    context: Any,
    tool_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve safe plugin handles to local paths only at the QQ transport edge."""

    service_getter = getattr(engine, "_get_generated_file_service", None)
    service = service_getter() if callable(service_getter) else None
    hydrated: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for raw_event in tool_events:
        if not isinstance(raw_event, dict):
            continue
        event = dict(raw_event)
        generated = event.get("generated_file")
        if (
            event.get("type") != "generated_file_ready"
            or str(event.get("delivery_scope") or "").strip().lower() != "plugin_managed_artifact"
            or not bool(event.get("send_to_user"))
            or not isinstance(generated, dict)
            or str(generated.get("absolute_path") or "").strip()
        ):
            hydrated.append(event)
            continue
        generated_id = str(generated.get("generated_id") or "").strip()
        generated_handle = str(generated.get("generated_handle") or "").strip()
        resolved = None
        if service is not None and callable(getattr(service, "resolve_generated_artifact", None)):
            target = generated_id or generated_handle
            try:
                resolved = service.resolve_generated_artifact(
                    profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                    session_id=str(getattr(context, "session_id", "") or ""),
                    target=target,
                )
            except Exception:
                resolved = None
        if isinstance(resolved, dict) and str(resolved.get("absolute_path") or "").strip():
            event["generated_file"] = resolved
        else:
            failures.append(
                {
                    "ok": False,
                    "reason": "managed_artifact_unavailable",
                    "generated_id": generated_id,
                }
            )
        hydrated.append(event)
    return hydrated, failures


def _group_attention_review_event(reason: Any) -> str:
    return (
        "event.group_attention_followup_review"
        if str(reason or "").strip().lower() == "engaged_followup"
        else "event.group_attention_idle_review"
    )


def _apply_group_attention_actor_scope(turn_payload: dict[str, Any], *, reason: Any) -> None:
    """Keep capability ownership only for a concrete engaged follow-up.

    An idle observation represents the room and must not inherit whichever
    member happened to speak last.  An engaged follow-up, however, is armed by
    a real inbound message from a concrete actor.  Keeping that actor scope
    lets actor-owned tools (for example project workspaces) operate without
    changing the separate ``message_addressing`` decision that still tells the
    model this is an observed, optional-reply turn.
    """

    if str(reason or "").strip().lower() == "engaged_followup":
        return
    for field in ("actor_stable_id", "actor_profile_user_id", "actor_display_name", "actor_platform"):
        turn_payload.pop(field, None)


def build_qq_router(
    *,
    engine: Any,
    config_module: Any,
    qq_gateway: Any,
    runtime_metrics: Any,
    logger: Any,
    log_event: LogEvent,
    tts_client: Any = None,
    settings: Any = None,
    async_task_supervisor: Any = None,
    channel_config: QQChannelRuntimeConfig | None = None,
    admin_auth: AdminWriteAuth | None = None,
    route_base: str = "/api/qq",
    plugin_command_broker_provider: Callable[[], Any] | None = None,
    plugin_event_broker_provider: Callable[[], Any] | None = None,
    plugin_agent_event_handler_registrar: Callable[[str, Any], None] | None = None,
    plugin_conversation_ref_issuer: Callable[..., str] | None = None,
    plugin_turn_router: Any = None,
    thinking_mode_setter: Callable[[str], str] | None = None,
    turn_coordinator: Any = None,
    session_work_queue: Any = None,
) -> APIRouter:
    router = APIRouter()
    qq_route_base = _normalize_qq_route_base(route_base)
    diagnostic_auth = admin_auth or AdminWriteAuth.local_compatibility()
    turn_coordinator = turn_coordinator or TurnCoordinator()
    group_attention = QQGroupAttentionState()
    attention_latest: dict[
        str,
        tuple[Any, dict[str, Any], str, tuple[str, ...], tuple[str, ...]],
    ] = {}
    attention_pending_media: dict[str, list[dict[str, Any]]] = {}

    def schedule_followup(coroutine: Any) -> Any:
        if async_task_supervisor is not None:
            return async_task_supervisor.create_task(coroutine)
        return asyncio.create_task(coroutine)

    async def _send_route_reply(context: Any, text: str) -> dict[str, Any]:
        """Keep blocking OneBot delivery and synchronous Hook bridging off this loop."""

        return await asyncio.to_thread(qq_gateway.send_reply, context, text)

    async def _send_route_replies(context: Any, messages: list[str]) -> dict[str, Any]:
        return await asyncio.to_thread(qq_gateway.send_replies, context, messages)

    def _plugin_input_context(context):
        reference = plugin_conversation_ref_issuer(
            profile_user_id=context.profile_user_id, session_id=context.session_id,
            character_pack_id=context.character_pack_id, user_id=context.user_id, group_id=context.group_id,
            actor_stable_id=f"qq:{context.user_id}" if context.is_group else "",
            actor_profile_user_id=str(context.actor_profile_user_id or ""),
        ) if plugin_conversation_ref_issuer else ""
        return PluginInvocationContext(context.profile_user_id, context.session_id, "qq",
            character_pack_id=context.character_pack_id, conversation_ref=reference)

    def _input_scope(context, marker=None):
        marker = marker or {}
        required = bool(marker.get("_plugin_inbound_required"))
        key = str(marker.get("_plugin_inbound_turn_id") or "")
        if required and (plugin_turn_router is None or not key):
            raise PluginTurnError("agent_turn_scope_expired")
        return plugin_turn_router.inbound(_plugin_input_context(context), inbound_turn_id=key,
            required=required) if plugin_turn_router else nullcontext("")

    def _input_marker(inbound):
        return {"_plugin_inbound_turn_id": inbound,
                "_plugin_inbound_required": plugin_turn_router.inbound_required(inbound)} if inbound else {}

    def _deferred_input_payload(inbound, payload):
        marker = _input_marker(inbound)
        if inbound:
            plugin_turn_router.defer_inbound(inbound)
        return {**payload, **marker}

    def _schedule_input_followup(coroutine, inbound):
        lease = plugin_turn_router.defer_inbound(inbound) if inbound else 0
        try:
            task = schedule_followup(coroutine)
        except BaseException:
            coroutine.close()
            if inbound:
                plugin_turn_router.abandon_inbound(inbound, lease, reason="inbound_followup_schedule_failed")
            raise
        if inbound:
            task.add_done_callback(lambda _task: plugin_turn_router.abandon_inbound(
                inbound, lease, reason="inbound_followup_incomplete"))
        return task

    def _settle_plugin_input(inbound, result):
        if not inbound:
            return
        frame = result.get("frame") or {}
        send = result.get("send_result") or {}
        status = str(frame.get("status") or "")
        if status in {"queued", "steer_accepted"} and send.get("ok"):
            return
        if status in {"stop_requested", "idle", "finalizing", "busy_other_actor", "duplicate", "queue_failed"}:
            plugin_turn_router.finish_inbound(inbound, model_status="not_started",
                reason="inbound_control_or_queue_handled")
            return
        model_status = "stopped" if status == "stopped" or frame.get("_turn_stopped") else "completed"
        model_failed = result.get("final_frame_received") is False or frame.get("_transient_final_failure")
        delivery_failed = not send.get("ok") or (result.get("file_send_result") or {}).get("ok") is False
        reason = "agent_turn_model_incomplete" if model_failed else "agent_turn_delivery_failed" if delivery_failed else ""
        plugin_turn_router.finish_inbound(inbound, model_status="failed" if model_failed else model_status,
            delivery_status=str(send.get("status") or ("sent" if send.get("ok") else "failed")), reason=reason)

    async def _publish_plugin_channel_event(
        *,
        context: Any,
        event: dict[str, Any],
        request: Request,
    ) -> None:
        """Publish one real QQ message as a typed host event.

        Publication is the only side effect: declared subscriptions decide what
        the host does with the event (observe, record, or request a turn).
        """

        inbound = getattr(context, "inbound_message", None)
        if inbound is None:
            return
        conversation_kind = str(getattr(inbound.conversation, "kind", "") or "").strip().lower()
        event_type = GROUP_CONVERSATION_EVENT if conversation_kind == "group" else DIRECT_CONVERSATION_EVENT
        try:
            broker = (
                plugin_event_broker_provider()
                if plugin_event_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_event_broker", None)
            )
            if broker is None:
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "qq_plugin_event_degraded",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                broker_status="broker_unavailable",
                handler_failure_count=0,
            )
            return
        event_key = str(getattr(inbound, "event_id", "") or "").strip()
        try:
            reference = plugin_conversation_ref_issuer(
                profile_user_id=context.profile_user_id, session_id=context.session_id,
                character_pack_id=context.character_pack_id, user_id=context.user_id, group_id=context.group_id,
                actor_stable_id=f"qq:{context.user_id}" if context.is_group else "",
                actor_profile_user_id=str(context.actor_profile_user_id or ""),
            ) if plugin_conversation_ref_issuer else ""
            receipt = await broker.emit(event_type, {
                **inbound.public_summary(), "channel": "qq",
                "parts": [part.public_summary() for part in inbound.chain.parts],
            }, context=PluginInvocationContext(context.profile_user_id, context.session_id, "qq",
                character_pack_id=context.character_pack_id, conversation_ref=reference),
                event_key=event_key, occurred_at_ms=int(getattr(inbound, "timestamp", 0) or 0) * 1000 or None)
            if receipt.status == "rejected":
                log_event("qq_plugin_event_degraded", broker_status=receipt.status,
                          reason=receipt.reason, event_key=event_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event("qq_plugin_event_degraded", broker_status="publication_failed", event_key=event_key)

    async def _publish_plugin_poke_event(
        *,
        context: Any,
        event: dict[str, Any],
        outcome: Any,
        request: Request,
    ) -> None:
        """Publish a real QQ poke as a typed public event.

        The host has already applied its own care effect; subscribers only get
        the observed facts and the resolved outcome, never the host prompt text
        or the ability to choose the conversation.
        """

        inbound = getattr(context, "inbound_message", None)
        if inbound is None or outcome is None:
            return
        try:
            broker = (
                plugin_event_broker_provider()
                if plugin_event_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_event_broker", None)
            )
            if broker is None or not broker.observes(POKE_CONVERSATION_EVENT):
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "qq_plugin_poke_degraded",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                broker_status="broker_unavailable",
            )
            return
        event_key = str(getattr(outcome, "event_id", "") or "").strip()
        if not event_key:
            event_key = str(getattr(inbound, "event_id", "") or "").strip()
        try:
            receipt = await broker.emit(
                POKE_CONVERSATION_EVENT,
                {
                    "event_kind": "poke",
                    "channel": "qq",
                    "conversation_kind": str(getattr(inbound.conversation, "kind", "") or "direct"),
                    "conversation_id": str(getattr(inbound.conversation, "id", "") or ""),
                    "actor_id": str(getattr(inbound.actor, "id", "") or ""),
                    "actor_label": str(getattr(outcome, "actor_label", "") or ""),
                    "outcome_kind": str(getattr(outcome, "outcome_kind", "") or "plain"),
                    "status": str(getattr(outcome, "status", "") or "ok"),
                    "reason": str(getattr(outcome, "reason", "") or ""),
                },
                context=_plugin_input_context(context),
                event_key=event_key,
                occurred_at_ms=int(getattr(inbound, "timestamp", 0) or 0) * 1000 or None,
            )
            if receipt.status == "rejected":
                log_event(
                    "qq_plugin_poke_degraded",
                    session_id=str(getattr(context, "session_id", "") or ""),
                    profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                    broker_status=receipt.status,
                    reason=str(getattr(receipt, "reason", "") or ""),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "qq_plugin_poke_degraded",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                broker_status="publication_failed",
            )

    def _session_work_key(context: Any) -> str:
        profile_user_id = str(getattr(context, "profile_user_id", "") or "").strip()
        session_id = str(getattr(context, "session_id", "") or "").strip()
        return f"{profile_user_id}\0{session_id}"

    @staticmethod
    def _attention_actor_key(context: Any) -> str:
        user_id = int(getattr(context, "user_id", 0) or 0)
        if user_id:
            return f"qq:{user_id}"
        return str(getattr(context, "sender_label", "") or "").strip()

    def _stage_attention_media(
        context: Any,
        event: dict[str, Any],
        registered_items: list[dict[str, Any]],
        *,
        timeline_source_id: str,
    ) -> None:
        attachment_ids = tuple(
            str(item.get("attachment_id") or "").strip()
            for item in registered_items
            if str(item.get("kind") or "").strip().lower() == "image"
            and str(item.get("attachment_id") or "").strip()
        )
        if not attachment_ids:
            return
        key = _session_work_key(context)
        timestamp = int(event.get("time") or time.time())
        ttl = max(10, int(getattr(config_module, "QQ_GROUP_ATTENTION_TTL_SECONDS", 120) or 120))
        pending = [
            item
            for item in attention_pending_media.get(key, [])
            if timestamp - int(item.get("timestamp") or 0) <= ttl
        ]
        pending.append(
            {
                "actor_key": _attention_actor_key(context),
                "timestamp": timestamp,
                "attachment_ids": attachment_ids,
                "timeline_source_id": str(timeline_source_id or "").strip(),
            }
        )
        attention_pending_media[key] = pending[-16:]

    def _take_attention_media(context: Any, event: dict[str, Any]) -> dict[str, tuple[str, ...]]:
        key = _session_work_key(context)
        timestamp = int(event.get("time") or time.time())
        actor_key = _attention_actor_key(context)
        ttl = max(10, int(getattr(config_module, "QQ_GROUP_ATTENTION_TTL_SECONDS", 120) or 120))
        pending = attention_pending_media.get(key, [])
        eligible = [
            item
            for item in pending
            if str(item.get("actor_key") or "") == actor_key
            and 0 <= timestamp - int(item.get("timestamp") or 0) <= ttl
        ]
        selected = eligible[-1] if eligible else None
        retained = [
            item
            for item in pending
            if item is not selected and timestamp - int(item.get("timestamp") or 0) <= ttl
        ]
        if retained:
            attention_pending_media[key] = retained
        else:
            attention_pending_media.pop(key, None)
        if not selected:
            return {"attachment_ids": (), "timeline_source_ids": ()}
        timeline_source_id = str(selected.get("timeline_source_id") or "").strip()
        return {
            "attachment_ids": tuple(
                str(item or "").strip()
                for item in selected.get("attachment_ids") or ()
                if str(item or "").strip()
            ),
            "timeline_source_ids": (timeline_source_id,) if timeline_source_id else (),
        }

    def _group_attention_mode(context: Any) -> str:
        default_mode = str(getattr(config_module, "QQ_GROUP_ATTENTION_MODE", "engaged") or "engaged")
        resolver = getattr(qq_gateway, "resolve_group_attention_mode", None)
        if callable(resolver):
            return str(resolver(getattr(context, "group_id", 0), default=default_mode) or default_mode)
        return QQGroupAttentionState.normalize_mode(default_mode)

    def _turn_has_real_visible_delivery(result: Any) -> bool:
        payload = result if isinstance(result, dict) else {}
        frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
        if bool(payload.get("visible_action_delivered")) or _qq_has_visible_action_receipt(
            frame.get("tool_events")
        ):
            return True
        if frame.get("_transient_final_failure"):
            return False
        if list(payload.get("reply_messages") or []):
            return True
        file_result = payload.get("file_send_result") if isinstance(payload.get("file_send_result"), dict) else {}
        return bool(file_result.get("ok")) and int(file_result.get("count") or 0) > 0

    def _mark_group_engaged(context: Any, result: Any) -> None:
        if not bool(getattr(context, "is_group", False)) or not _turn_has_real_visible_delivery(result):
            return
        group_attention.mark_visible_reply(
            _session_work_key(context),
            ttl_seconds=float(getattr(config_module, "QQ_GROUP_ATTENTION_TTL_SECONDS", 120) or 120),
        )

    def _cancel_group_attention(context: Any) -> None:
        if not bool(getattr(context, "is_group", False)):
            return
        key = _session_work_key(context)
        group_attention.cancel(key)
        attention_latest.pop(key, None)

    async def _run_group_attention_ticket(ticket: AttentionTicket) -> None:
        await asyncio.sleep(max(0.0, ticket.deadline - time.monotonic()))
        if not group_attention.claim(ticket):
            return
        snapshot = attention_latest.pop(ticket.key, None)
        if snapshot is None:
            group_attention.finish(ticket, discard_dirty=True)
            return
        context, event, projection_anchor_source_id, attachment_ids, stimulus_source_ids = snapshot
        # Ambient participation is based on the whole MemCore projection, not
        # mechanically on the last message that happened to update the ticket.
        # Clear only the delivery reply reference so Akane joins the discussion
        # naturally instead of quoting an arbitrary last line.
        context = replace(context, source_message_id="")
        if turn_coordinator.is_busy(context.profile_user_id, context.session_id) or session_work_queue.has_work(
            ticket.key
        ):
            if ticket.reason == "idle_observation":
                group_attention.mark_idle_observed(
                    ticket.key,
                    cooldown_seconds=float(
                        getattr(config_module, "QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS", 60) or 60
                    ),
                )
            log_event(
                "qq_group_attention_skipped",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                group_id=int(getattr(context, "group_id", 0) or 0),
                reason="session_busy",
                attention_reason=ticket.reason,
            )
            group_attention.finish(ticket, discard_dirty=True)
            attention_latest.pop(ticket.key, None)
            return
        review_event = _group_attention_review_event(ticket.reason)
        turn_payload = context.to_turn_payload()
        turn_payload.update(
            {
                "message": review_event,
                "timestamp": int(time.time()),
                "turn_kind": "qq_attention",
                "transient_user_message": True,
                # The review instruction exists only in this provider request.
                # Real group messages are already durable MemCore facts; these
                # IDs are lineage evidence for a hidden host-owned turn and
                # must never be relinked or copied into a synthetic message.
                "memory_attention_reference_source_ids": list(stimulus_source_ids),
                "message_addressing": {
                    "mode": "observed",
                    "trigger": review_event.removeprefix("event."),
                    "addressed_to_assistant": False,
                    "explicit_assistant_mention": False,
                    "primary_target": {},
                    "mentions": [],
                },
            }
        )
        if attachment_ids:
            turn_payload["qq_current_attachment_ids"] = list(attachment_ids)
            native_prepare = getattr(engine, "prepare_qq_native_image_inputs", None)
            if callable(native_prepare):
                native_result = await asyncio.to_thread(
                    native_prepare,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachment_ids=list(attachment_ids),
                    chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                    timeout_seconds=max(
                        0.0,
                        min(
                            15.0,
                            float(getattr(config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                        ),
                    ),
                )
                native_images = [
                    dict(item)
                    for item in list((native_result or {}).get("images") or [])
                    if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                ][:5]
                if native_images:
                    turn_payload["native_user_images"] = native_images
                log_event(
                    "qq_group_attention_media_bound",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    group_id=int(getattr(context, "group_id", 0) or 0),
                    attachment_count=len(attachment_ids),
                    native_image_count=len(native_images),
                    native_status=str((native_result or {}).get("status") or "unavailable"),
                )
        _apply_group_attention_actor_scope(turn_payload, reason=ticket.reason)
        try:
            result = await _run_qq_turn_delivery(context=context, event=event, turn_payload=turn_payload)
        except Exception as exc:
            if ticket.reason == "idle_observation":
                group_attention.mark_idle_observed(
                    ticket.key,
                    cooldown_seconds=float(
                        getattr(config_module, "QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS", 60) or 60
                    ),
                )
            logger.exception("qq group attention observation failed")
            log_event(
                "qq_group_attention_failed",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                group_id=int(getattr(context, "group_id", 0) or 0),
                attention_reason=ticket.reason,
                reason=exc.__class__.__name__,
            )
            group_attention.finish(ticket, discard_dirty=True)
            attention_latest.pop(ticket.key, None)
            return
        if ticket.reason == "idle_observation" and not _turn_has_real_visible_delivery(result):
            group_attention.mark_idle_observed(
                ticket.key,
                cooldown_seconds=float(
                    getattr(config_module, "QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS", 60) or 60
                ),
            )
        visible_reply = _turn_has_real_visible_delivery(result)
        result_frame = dict(result.get("frame") or {}) if isinstance(result, dict) else {}
        log_event(
            "qq_group_attention_completed",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            group_id=int(getattr(context, "group_id", 0) or 0),
            attention_reason=ticket.reason,
            visible_reply=visible_reply,
            silent=bool(result_frame.get("_deliberate_silence")) and not visible_reply,
            silent_reason="model_decision" if bool(result_frame.get("_deliberate_silence")) else "",
        )
        newer_generation_waiting = group_attention.finish(ticket)
        if not newer_generation_waiting:
            return
        next_snapshot = attention_latest.get(ticket.key)
        if next_snapshot is None:
            return
        next_context = next_snapshot[0]
        next_ticket, next_reason = group_attention.arm(
            ticket.key,
            mode=_group_attention_mode(next_context),
            delay_seconds=float(getattr(config_module, "QQ_GROUP_ATTENTION_DELAY_SECONDS", 10.0) or 0.0),
        )
        if next_ticket is not None and next_reason == "armed":
            schedule_followup(_run_group_attention_ticket(next_ticket))
            log_event(
                "qq_group_attention_rearmed",
                session_id=str(getattr(next_context, "session_id", "") or ""),
                profile_user_id=str(getattr(next_context, "profile_user_id", "") or ""),
                group_id=int(getattr(next_context, "group_id", 0) or 0),
                reason="newer_memcore_generation",
                attention_reason=next_ticket.reason,
            )
        elif next_reason not in {"already_pending", "in_flight_dirty"}:
            attention_latest.pop(ticket.key, None)

    def _schedule_group_attention(
        context: Any,
        event: dict[str, Any],
        *,
        projection_anchor_source_id: str,
    ) -> dict[str, Any]:
        if not bool(getattr(context, "is_group", False)):
            return {"scheduled": False, "reason": "not_group"}
        if any(
            isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
            for item in list(getattr(context, "attachments", None) or [])
        ):
            # Passive storage and active perception are separate concerns. An
            # unaddressed image is preserved with a durable handle, but it must
            # not create a model request that lacks the actual pixels. Explicit
            # image turns use the ordinary QQ path; a later ambient message can
            # bind this exact image through the attention evidence snapshot.
            return {"scheduled": False, "reason": "passive_image_recorded"}
        anchor_source_id = str(projection_anchor_source_id or "").strip()
        if not anchor_source_id:
            return {"scheduled": False, "reason": "projection_anchor_missing"}
        key = _session_work_key(context)
        ticket, reason = group_attention.arm(
            key,
            mode=_group_attention_mode(context),
            delay_seconds=float(getattr(config_module, "QQ_GROUP_ATTENTION_DELAY_SECONDS", 10.0) or 0.0),
        )
        if ticket is None and reason != "in_flight_dirty":
            return {"scheduled": False, "reason": reason}
        media_evidence = _take_attention_media(context, event)
        previous = attention_latest.get(key)
        previous_attachment_ids = previous[3] if previous is not None else ()
        previous_stimulus_source_ids = previous[4] if previous is not None else ()
        attachment_ids = tuple(
            dict.fromkeys([*previous_attachment_ids, *media_evidence["attachment_ids"]])
        )[-5:]
        stimulus_source_ids = tuple(
            dict.fromkeys(
                [
                    *previous_stimulus_source_ids,
                    *media_evidence["timeline_source_ids"],
                    anchor_source_id,
                ]
            )
        )
        attention_latest[key] = (
            context,
            dict(event),
            anchor_source_id,
            attachment_ids,
            stimulus_source_ids,
        )
        if reason != "armed":
            return {"scheduled": False, "reason": reason}
        schedule_followup(_run_group_attention_ticket(ticket))
        return {"scheduled": True, "reason": ticket.reason, "deadline": ticket.deadline}

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

    async def _run_qq_turn_delivery_unlocked(
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
        prepared_frame: dict[str, Any] | None = None,
        additional_delivery_events: list[dict[str, Any]] | Callable | None = None,
        file_target_allowed: Callable | None = None,
    ) -> dict[str, Any]:
        turn_kind = str(turn_payload.get("turn_kind") or "").strip().lower()
        is_plugin_event = turn_kind == "plugin_event"
        plugin_event_message = str(turn_payload.get("message") or "")
        if is_plugin_event and re.search(r"https?://", plugin_event_message, re.IGNORECASE):
            original_extra_context = str(turn_payload.get("extra_context") or "").strip()
            turn_payload["extra_context"] = "\n\n".join(
                part
                for part in (
                    original_extra_context,
                    (
                        "【插件事件来源链接】\n"
                        "本轮由插件触发，消息里的 URL 是事件来源或溯源标记，不是用户提交的媒体下载请求。"
                        "不要尝试把它作为音视频素材下载，也不要在回复中声称链接无法打开、平台不受支持，"
                        "或要求用户更换媒体直链；需要核验事实时仍可使用可用的只读检索工具。"
                    ),
                )
                if part
            )
        remote_prefetch_result = (
            await asyncio.to_thread(
                engine.prefetch_remote_media_links_for_message,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                message=context.clean_message or context.raw_message,
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                timestamp=int(event.get("time") or time.time()),
            )
            if prepared_frame is None and not is_plugin_event
            else {}
        )
        if isinstance(remote_prefetch_result, dict) and remote_prefetch_result:
            turn_payload["message"] = redact_remote_media_urls_for_prompt(str(turn_payload.get("message") or ""))
            delivery_context = (
                dict(turn_payload.get("qq_delivery_context") or {})
                if isinstance(turn_payload.get("qq_delivery_context"), dict)
                else {}
            )
            if delivery_context:
                delivery_context["clean_message"] = redact_remote_media_urls_for_prompt(
                    str(delivery_context.get("clean_message") or "")
                )
                delivery_context["raw_message"] = redact_remote_media_urls_for_prompt(
                    str(delivery_context.get("raw_message") or "")
                )
                turn_payload["qq_delivery_context"] = delivery_context
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
            settings=settings,
            prepared_frame=prepared_frame,
            additional_delivery_events=additional_delivery_events,
            file_target_allowed=file_target_allowed,
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
        _mark_group_engaged(context, turn_result)
        return turn_result

    async def _prepare_passive_qq_record_payload(
        *,
        context: Any,
        event: dict[str, Any],
        base_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int], list[dict[str, Any]]]:
        payload = dict(base_payload)
        effective_attachments = [dict(item) for item in list(getattr(context, "attachments", None) or [])]
        metrics = {
            "forward_count": 0,
            "forward_resolved_count": 0,
            "attachment_count": len(effective_attachments),
            "attachments_registered": 0,
        }
        if getattr(context, "forward_refs", ()):
            forward_result = await asyncio.to_thread(
                qq_gateway.resolve_forward_message_evidence,
                event,
                context=context,
            )
            forward_payload = forward_result if isinstance(forward_result, dict) else {}
            metrics["forward_count"] = int(forward_payload.get("forward_count") or 0)
            metrics["forward_resolved_count"] = int(forward_payload.get("resolved_count") or 0)
            forwarded_attachments = [
                dict(item) for item in list(forward_payload.get("attachments") or []) if isinstance(item, dict)
            ]
            effective_attachments = _merge_qq_attachments(effective_attachments, forwarded_attachments)
            forward_references = _qq_structured_forward_references(forward_payload)
            if forward_references:
                payload["forward_references"] = [dict(item) for item in forward_references]

        metrics["attachment_count"] = len(effective_attachments)
        registered_items: list[dict[str, Any]] = []
        if effective_attachments:
            registered = await asyncio.to_thread(
                engine.ingest_qq_attachments,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachments=_with_qq_sender_context(effective_attachments, context),
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                timestamp=int(event.get("time") or time.time()),
                observe_images=False,
            )
            registered_items = [item for item in list(registered or []) if isinstance(item, dict)]
            metrics["attachments_registered"] = len(registered_items)
            payload["message"] = _normalize_passive_image_placeholder(
                str(payload.get("message") or ""),
                registered_items,
            )
            payload["message"] = _append_qq_attachment_handles_to_message(
                str(payload.get("message") or ""),
                registered_items,
            )
        return payload, metrics, registered_items

    def _durable_qq_work_payload(
        *,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
    ) -> dict[str, Any]:
        safe_turn_payload = dict(turn_payload)
        # Native pixels are recreated from the already-managed attachment IDs
        # when the item is claimed. Keeping base64 in SQLite would duplicate
        # large artifacts and make ordinary queue operations expensive.
        safe_turn_payload.pop("native_user_images", None)
        return {
            "event": dict(event),
            "turn_payload": safe_turn_payload,
        }

    def _restore_queued_context(item: Any, payload: dict[str, Any]) -> Any:
        legacy_context = payload.get("context")
        if legacy_context is not None:
            return legacy_context
        event = dict(payload.get("event") or {})
        turn_payload = payload.get("turn_payload") if isinstance(payload.get("turn_payload"), dict) else {}
        delivery_context = turn_payload.get("qq_delivery_context")
        resolver = getattr(qq_gateway, "context_from_delivery_context", None)
        stored_context = (
            resolver(delivery_context)
            if callable(resolver) and isinstance(delivery_context, dict)
            else None
        )
        context = stored_context
        if str(getattr(item, "kind", "") or "") == "passive":
            restorer = getattr(qq_gateway, "restore_admitted_passive_context", None)
            if not callable(restorer) or not isinstance(delivery_context, dict):
                raise ValueError("queued_passive_context_restore_unavailable")
            context = restorer(event, delivery_context)
        if context is None:
            raise ValueError("queued_context_restore_failed")
        expected_profile = str(getattr(item, "profile_user_id", "") or "").strip()
        expected_session = str(getattr(item, "session_id", "") or "").strip()
        if expected_profile and expected_profile != str(getattr(context, "profile_user_id", "") or "").strip():
            raise ValueError("queued_context_profile_mismatch")
        if expected_session and expected_session != str(getattr(context, "session_id", "") or "").strip():
            raise ValueError("queued_context_session_mismatch")
        return context

    async def _restore_queued_native_images(context: Any, turn_payload: dict[str, Any]) -> None:
        if list(turn_payload.get("native_user_images") or []):
            return
        attachment_ids = [
            str(item or "").strip()
            for item in list(turn_payload.get("qq_current_attachment_ids") or [])[:5]
            if str(item or "").strip()
        ]
        if not attachment_ids:
            return
        prepare = getattr(engine, "prepare_qq_native_image_inputs", None)
        if not callable(prepare):
            return
        result = await asyncio.to_thread(
            prepare,
            profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
            session_id=str(getattr(context, "session_id", "") or ""),
            attachment_ids=attachment_ids,
            chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
            timeout_seconds=_qq_attachment_ready_wait_seconds(context, config_module),
        )
        images = [
            dict(item)
            for item in list((result or {}).get("images") or [])[:5]
            if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
        ]
        if images:
            turn_payload["native_user_images"] = images

    @staticmethod
    def _session_item_wait_ms(item: Any) -> float:
        if isinstance(item, SessionInboxItem):
            return max(0.0, (time.time() - float(item.created_at or 0)) * 1000)
        return max(0.0, (time.perf_counter() - float(getattr(item, "enqueued_at", 0) or 0)) * 1000)

    async def _handle_queued_session_work(key, items):
        if not items:
            return
        first_payload = items[0].payload if isinstance(items[0].payload, dict) else {}
        if items[0].kind == "passive" or first_payload.get("plugin_turn_request_id"):
            return await _handle_queued_session_work_admitted(key, items)
        context = _restore_queued_context(items[0], first_payload)
        marker = first_payload.get("turn_payload") or {}
        with _input_scope(context, marker) as inbound:
            return await _handle_queued_session_work_admitted(key, items, inbound=inbound)

    async def _handle_queued_session_work_admitted(_key: str, items: list[Any], *, inbound="") -> None:
        if not items:
            return
        first_payload = items[0].payload if isinstance(items[0].payload, dict) else {}
        context = _restore_queued_context(items[0], first_payload)
        profile_user_id = str(getattr(context, "profile_user_id", "") or "")
        session_id = str(getattr(context, "session_id", "") or "")
        group_id = int(getattr(context, "group_id", 0) or 0)
        if items[0].kind == "passive":
            started_at = time.perf_counter()
            payloads: list[dict[str, Any]] = []
            registered_items_by_payload: list[list[dict[str, Any]]] = []
            enrichment_totals = {
                "forward_count": 0,
                "forward_resolved_count": 0,
                "attachment_count": 0,
                "attachments_registered": 0,
            }
            for item in items:
                item_payload = item.payload if isinstance(item.payload, dict) else {}
                item_context = context if item is items[0] else _restore_queued_context(item, item_payload)
                item_event = dict(item_payload.get("event") or {})
                base_payload = (
                    dict(item_payload.get("turn_payload") or {})
                    if isinstance(item_payload.get("turn_payload"), dict)
                    else {}
                )
                prepared, item_metrics, registered_items = await _prepare_passive_qq_record_payload(
                    context=item_context,
                    event=item_event,
                    base_payload=base_payload,
                )
                payloads.append(prepared)
                registered_items_by_payload.append(registered_items)
                for key in enrichment_totals:
                    enrichment_totals[key] += int(item_metrics.get(key) or 0)
            async with turn_coordinator.hold(profile_user_id, session_id):
                queue_wait_ms = _session_item_wait_ms(items[0])
                batch_recorder = getattr(engine, "record_passive_qq_messages", None)
                recorder = getattr(engine, "record_passive_qq_message", None)
                if callable(batch_recorder):
                    record_result = await asyncio.to_thread(batch_recorder, payloads)
                elif callable(recorder):
                    fallback_results = [await asyncio.to_thread(recorder, payload) for payload in payloads]
                    failed_count = sum(
                        1 for result in fallback_results if not isinstance(result, dict) or not bool(result.get("ok"))
                    )
                    record_result = {
                        "ok": failed_count == 0,
                        "status": "recorded" if failed_count == 0 else "partially_recorded",
                        "count": len(fallback_results),
                        "recorded_count": len(fallback_results) - failed_count,
                        "failed_count": failed_count,
                        "results": fallback_results,
                    }
                else:
                    record_result = {
                        "ok": False,
                        "status": "recorder_unavailable",
                        "count": len(payloads),
                        "recorded_count": 0,
                        "failed_count": len(payloads),
                        "results": [],
                    }
            result_payload = record_result if isinstance(record_result, dict) else {}
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request(
                "qq_passive_group_message_batch",
                duration_ms=duration_ms,
                ok=bool(result_payload.get("ok")),
            )
            log_event(
                "qq_passive_group_message_batch_recorded",
                session_id=session_id,
                profile_user_id=profile_user_id,
                group_id=group_id,
                batch_count=len(payloads),
                recorded_count=int(result_payload.get("recorded_count") or 0),
                failed_count=int(result_payload.get("failed_count") or 0),
                record_status=str(result_payload.get("status") or ""),
                queue_wait_ms=round(queue_wait_ms, 1),
                duration_ms=round(duration_ms, 1),
                **enrichment_totals,
            )
            if bool(result_payload.get("ok")):
                batch_results = list(result_payload.get("results") or [])
                for index, (item, registered_items) in enumerate(zip(items, registered_items_by_payload)):
                    item_payload = item.payload if isinstance(item.payload, dict) else {}
                    item_context = _restore_queued_context(item, item_payload)
                    item_event = dict(item_payload.get("event") or {})
                    item_record_result = (
                        batch_results[index]
                        if index < len(batch_results) and isinstance(batch_results[index], dict)
                        else {}
                    )
                    _stage_attention_media(
                        item_context,
                        item_event,
                        registered_items,
                        timeline_source_id=str(item_record_result.get("source_id") or ""),
                    )
                last_item_payload = items[-1].payload if isinstance(items[-1].payload, dict) else {}
                last_context = _restore_queued_context(items[-1], last_item_payload)
                last_event = dict(last_item_payload.get("event") or {})
                last_record_result = (
                    batch_results[-1]
                    if batch_results and isinstance(batch_results[-1], dict)
                    else {}
                )
                attention_result = _schedule_group_attention(
                    last_context,
                    last_event,
                    projection_anchor_source_id=str(last_record_result.get("source_id") or ""),
                )
                log_event(
                    "qq_group_attention_considered",
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    group_id=group_id,
                    scheduled=bool(attention_result.get("scheduled")),
                    reason=str(attention_result.get("reason") or ""),
                )
            return

        event = dict(first_payload.get("event") or {})
        turn_payload = dict(first_payload.get("turn_payload") or {})
        request_id = str(first_payload.get("plugin_turn_request_id") or "")
        if request_id:
            if plugin_turn_router is None:
                raise PluginTurnError("agent_turn_scope_expired")
            plugin_turn_router.require_pending(request_id)
        qq_user_id = int(getattr(context, "user_id", 0) or 0)
        actor_id = str(turn_payload.get("actor_stable_id") or (f"qq:{qq_user_id}" if qq_user_id else f"qq-profile:{profile_user_id}"))
        started_at = time.perf_counter()
        try:
            async with turn_coordinator.hold(
                profile_user_id,
                session_id,
                actor_id=actor_id,
                channel="qq",
                turn_kind=str(turn_payload.get("turn_kind") or ""),
            ) as turn_control_id:
                queue_wait_ms = _session_item_wait_ms(items[0])
                if isinstance(session_work_queue, DurableSessionWorkQueue) and (completion_batch_key(items[0]) or request_id):
                    await session_work_queue.begin_processing(items)
                completion = prepare_completion_turn(engine, items)
                turn_payload, prepared_frame = completion.payload, completion.prepared_frame
                turn_payload.pop("_plugin_inbound_turn_id", None)
                turn_payload.pop("_plugin_inbound_required", None)
                if len(items) > 1:
                    context = _restore_queued_context(items[0], {**first_payload, "turn_payload": turn_payload})
                await _restore_queued_native_images(context, turn_payload)
                turn_payload["_turn_control_id"] = turn_control_id
                if request_id:
                    apply_turn_intent(turn_payload, plugin_turn_router.begin(request_id, turn_control_id))
                    context = _restore_queued_context(items[0], {**first_payload, "turn_payload": turn_payload})
                processing_started_at = time.perf_counter()
                with completion.processing(turn_coordinator, turn_control_id), plugin_turn_router.processing(request_id) if request_id else plugin_turn_router.processing_inbound(inbound, turn_control_id) if inbound else nullcontext():
                    turn_result = await _run_qq_turn_delivery_unlocked(
                        context=context,
                        event=event,
                        turn_payload=turn_payload,
                        prepared_frame=prepared_frame,
                        additional_delivery_events=completion.direct_events,
                        file_target_allowed=completion.allows_file_target if completion.jobs else None,
                    )
                processing_ms = (time.perf_counter() - processing_started_at) * 1000
            result_payload = turn_result if isinstance(turn_result, dict) else {}
            _settle_plugin_input(inbound, result_payload)
            send_result = result_payload.get("send_result")
            send_payload = send_result if isinstance(send_result, dict) else {}
            model_failed = result_payload.get("final_frame_received") is False or bool(
                (result_payload.get("frame") or {}).get("_transient_final_failure"))
            files = result_payload.get("file_send_result") or {}
            delivered = bool(send_payload.get("ok")) and files.get("ok") is not False
            stopped = (result_payload.get("frame") or {}).get("status") == "stopped"
            record_completion_delivery(engine, items, ok=delivered and not model_failed and not stopped,
                model_status=str(result_payload.get("model_status") or (
                    "stopped" if stopped else "not_requested" if prepared_frame is not None else "failed" if model_failed else "completed")),
                delivery_status="failed" if not delivered else str((files.get("status") if files.get("count") else None) or send_payload.get("status") or "sent"),
                reason="host_completion_stopped" if stopped else "host_completion_turn_or_delivery_incomplete" if not delivered or model_failed else "", files=files)
            if request_id and (result_payload.get("frame") or {}).get("status") == "stopped":
                plugin_turn_router.finish(request_id, model_status="stopped",
                                          delivery_status=str(send_payload.get("status") or "not_sent"),
                                          reason=str((result_payload.get("frame") or {}).get("reason") or ""))
                return
            if (completion_batch_key(items[0]) or request_id) and (
                result_payload.get("final_frame_received") is False
                or bool((result_payload.get("frame") or {}).get("_transient_final_failure"))
                or not send_payload.get("ok")
                or (result_payload.get("file_send_result") or {}).get("ok") is False
            ):
                if request_id:
                    model_failed = (result_payload.get("final_frame_received") is False or
                                    bool((result_payload.get("frame") or {}).get("_transient_final_failure")))
                    plugin_turn_router.finish(request_id, model_status="failed" if model_failed else "completed",
                        delivery_status=str(send_payload.get("status") or "failed"),
                        reason="agent_turn_model_incomplete" if model_failed else "agent_turn_delivery_failed")
                raise SessionWorkError("host_completion_turn_or_delivery_incomplete")
            if request_id:
                plugin_turn_router.finish(request_id, model_status="completed",
                                          delivery_status=str(send_payload.get("status") or "sent"))
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request(
                "qq_group_turn_queue",
                duration_ms=duration_ms,
                ok=bool(send_payload.get("ok")),
            )
            log_event(
                "qq_group_turn_queue_completed",
                session_id=session_id,
                profile_user_id=profile_user_id,
                group_id=group_id,
                user_id=qq_user_id,
                source_message_id=str(getattr(context, "source_message_id", "") or ""),
                queue_sequence=items[0].sequence,
                queue_wait_ms=round(queue_wait_ms, 1),
                turn_processing_ms=round(processing_ms, 1),
                duration_ms=round(duration_ms, 1),
                sent=bool(send_payload.get("ok")),
                delivery_status=str(send_payload.get("status") or ""),
                batch_count=len(items),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("qq_group_turn_queue", duration_ms=duration_ms, ok=False)
            logger.exception("queued qq group turn failed")
            log_event(
                "qq_group_turn_queue_failed",
                session_id=session_id,
                profile_user_id=profile_user_id,
                group_id=group_id,
                user_id=qq_user_id,
                source_message_id=str(getattr(context, "source_message_id", "") or ""),
                queue_sequence=items[0].sequence,
                reason=exc.reason if isinstance(exc, SessionWorkError) else exc.__class__.__name__,
                duration_ms=round(duration_ms, 1),
            )
            raise

    def _session_work_error(_key: str, items: list[Any], exc: BaseException) -> None:
        if not isinstance(exc, RetryableSessionWorkError):
            record_completion_error(engine, items, exc)
        payload = items[0].payload if items and isinstance(items[0].payload, dict) else {}
        request_id = str(payload.get("plugin_turn_request_id") or "")
        if request_id and plugin_turn_router is not None:
            plugin_turn_router.fail(request_id, exc)
        context = payload.get("context")
        turn_payload = payload.get("turn_payload") if isinstance(payload.get("turn_payload"), dict) else {}
        inbound = str(turn_payload.get("_plugin_inbound_turn_id") or "")
        if inbound and plugin_turn_router is not None:
            plugin_turn_router.finish_inbound(inbound, model_status="failed", reason="inbound_queue_failed")
        delivery_context = (
            turn_payload.get("qq_delivery_context")
            if isinstance(turn_payload.get("qq_delivery_context"), dict)
            else {}
        )
        error_logger = getattr(logger, "error", None)
        if callable(error_logger):
            error_logger("qq session work handler failed: %s", exc.__class__.__name__)
        log_event(
            "qq_session_work_failed",
            session_id=str(getattr(context, "session_id", "") or delivery_context.get("session_id") or ""),
            profile_user_id=str(
                getattr(context, "profile_user_id", "") or delivery_context.get("profile_user_id") or ""
            ),
            work_kind=items[0].kind if items else "",
            batch_count=len(items),
            reason=exc.__class__.__name__,
        )

    if session_work_queue is None:
        session_work_queue = SessionWorkQueue(
            _handle_queued_session_work,
            schedule_task=schedule_followup,
            batchable_kinds={"passive"},
            on_error=_session_work_error,
        )
    elif isinstance(session_work_queue, DurableSessionWorkQueue):
        session_work_queue.register_handler(
            "qq",
            _handle_queued_session_work,
            on_error=_session_work_error,
            batch_key=completion_batch_key,
        )

    async def _enqueue_session_work(
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
        kind: str,
        schedule: bool = True,
    ) -> dict[str, Any]:
        key = _session_work_key(context)
        if isinstance(session_work_queue, DurableSessionWorkQueue):
            return await session_work_queue.enqueue(
                session_key=key,
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                session_id=str(getattr(context, "session_id", "") or ""),
                kind=kind,
                payload=_durable_qq_work_payload(
                    event=event,
                    turn_payload=turn_payload,
                ),
                source="qq",
                source_event_id=str(getattr(context, "source_message_id", "") or ""),
                schedule=schedule,
            )
        return session_work_queue.enqueue(
            key,
            kind=kind,
            payload={"context": context, "event": dict(event), "turn_payload": dict(turn_payload)},
        )

    async def _run_qq_turn_delivery(*, context, event, turn_payload):
        turn_payload = dict(turn_payload)
        marker = {key: turn_payload.pop(key) for key in ("_plugin_inbound_turn_id", "_plugin_inbound_required") if key in turn_payload}
        with _input_scope(context, marker) as inbound:
            try:
                result = await _run_qq_turn_delivery_admitted(context=context, event=event,
                    turn_payload=turn_payload, inbound=inbound)
            except BaseException:
                if inbound:
                    plugin_turn_router.finish_inbound(inbound, model_status="failed", reason="inbound_turn_failed")
                raise
            _settle_plugin_input(inbound, result)
            return result

    async def _run_qq_turn_delivery_admitted(
        *,
        context: Any,
        event: dict[str, Any],
        turn_payload: dict[str, Any],
        inbound="",
    ) -> dict[str, Any]:
        qq_user_id = int(getattr(context, "user_id", 0) or 0)
        actor_id = f"qq:{qq_user_id}" if qq_user_id else f"qq-profile:{context.profile_user_id}"
        steer_text = str(turn_payload.get("message") or "").strip()
        stop_requested = _is_qq_stop_command(getattr(context, "clean_message", ""))
        if stop_requested:
            master_qq = str(getattr(qq_gateway, "master_qq", "") or "").strip()
            requester_is_master = bool(master_qq) and str(qq_user_id) == master_qq
            stop_result = turn_coordinator.request_stop(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                # The owner is the host-level operator for this Bot instance and
                # may stop a group task started by another actor. Ordinary steer
                # messages remain actor-scoped below.
                actor_id="" if requester_is_master else actor_id,
            )
            if stop_result.get("ok"):
                acknowledgement = "收到，已请求在安全位置停止当前任务。"
                send_result = await asyncio.to_thread(qq_gateway.send_reply, context, acknowledgement)
                return {
                    "frame": {"status": "stop_requested", "speech": acknowledgement},
                    "reply_messages": [acknowledgement],
                    "send_result": send_result,
                }
            if stop_result.get("status") == "finalizing":
                acknowledgement = "当前任务已经在收尾，来不及再中止了。"
            elif stop_result.get("status") == "busy_other_actor":
                acknowledgement = "当前任务由另一位群成员发起；只有主人或任务发起者可以停止。"
            else:
                acknowledgement = "当前没有正在执行的任务。"
            send_result = await asyncio.to_thread(qq_gateway.send_reply, context, acknowledgement)
            return {
                "frame": {"status": str(stop_result.get("status") or "idle"), "speech": acknowledgement},
                "reply_messages": [acknowledgement],
                "send_result": send_result,
            }
        queue_key = _session_work_key(context)
        queued_for_later: dict[str, Any] | None = None
        durable_receipt: dict[str, str] = {}
        if (
            isinstance(session_work_queue, DurableSessionWorkQueue)
            and turn_coordinator.is_busy(context.profile_user_id, context.session_id)
        ):
            persisted = await _enqueue_session_work(
                context=context,
                event=dict(event),
                turn_payload=_deferred_input_payload(inbound, turn_payload),
                kind="turn",
                schedule=False,
            )
            if not persisted.get("ok"):
                return {
                    "frame": {"status": "queue_failed", "speech": ""},
                    "reply_messages": [],
                    "send_result": {
                        "ok": False,
                        "status": "queue_failed",
                        "reason": str(persisted.get("reason") or "session_inbox_persist_failed"),
                        "results": [],
                    },
                }
            persisted_item = persisted.get("item")
            persisted_status = str(getattr(persisted_item, "status", "") or "")
            if persisted.get("status") == "duplicate" and persisted_status == "committed":
                return {
                    "frame": {"status": "duplicate", "speech": ""},
                    "reply_messages": [],
                    "send_result": {
                        "ok": True,
                        "status": "suppressed",
                        "reason": "source_event_already_committed",
                        "results": [],
                    },
                }
            if persisted.get("status") == "duplicate" and persisted_status == "failed":
                return {
                    "frame": {"status": "queue_failed", "speech": ""},
                    "reply_messages": [],
                    "send_result": {
                        "ok": False,
                        "status": "queue_failed",
                        "reason": "source_event_previously_failed",
                        "results": [],
                    },
                }
            item_id = str(persisted.get("item_id") or getattr(persisted_item, "item_id", "") or "")
            claim = await session_work_queue.claim_for_active_turn(queue_key, item_id)
            if claim.get("ok"):
                durable_receipt = {
                    "source_id": f"steer_{item_id}",
                    "receipt_item_id": item_id,
                    "receipt_claim_token": str(claim.get("claim_token") or ""),
                }
            else:
                queued_for_later = persisted
                await session_work_queue.schedule_session(queue_key)

        steer_source_id = durable_receipt.get("source_id") or f"steer_{uuid.uuid4().hex}"
        target_token = turn_coordinator.active_token(context.profile_user_id, context.session_id) if inbound else ""
        with plugin_turn_router.steering(inbound, target_token, steer_source_id) if inbound else nullcontext(lambda: None) as accept_steer:
            steer_result = (
                turn_coordinator.offer_steer(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    actor_id=actor_id,
                    content=steer_text,
                    timestamp=int(event.get("time") or time.time()),
                    actor_display_name=str(getattr(context, "sender_label", "") or ""),
                    channel="qq",
                    native_user_images=[
                        dict(item)
                        for item in list(turn_payload.get("native_user_images") or [])[:5]
                        if isinstance(item, dict)
                    ],
                    **{**durable_receipt, "source_id": steer_source_id},
                )
                if queued_for_later is None and accept_steer is not None
                else {"ok": False, "status": "queued", "reason": "session_inbox_waiting"}
            )
            if steer_result.get("ok"):
                accept_steer()
        if steer_result.get("ok"):
            return {
                "frame": {"status": "steer_accepted", "speech": ""},
                "reply_messages": [],
                "send_result": {
                    "ok": True,
                    "status": "suppressed",
                    "reason": "steer_accepted_without_visible_ack",
                    "results": [],
                },
            }
        if durable_receipt:
            await session_work_queue.requeue_claim(
                durable_receipt["receipt_item_id"],
                claim_token=durable_receipt["receipt_claim_token"],
                error=str(steer_result.get("reason") or "steer_not_accepted"),
            )
            await session_work_queue.schedule_session(queue_key)
            queued_for_later = {
                "ok": True,
                "status": "queued",
                "reason": "steer_not_accepted_requeued",
                "item_id": durable_receipt["receipt_item_id"],
            }
        queue_behind_active_turn = str(steer_result.get("status") or "") in {
            "busy_other_actor",
            "finalizing",
            "preempting_optional_turn",
            "queued",
        }
        if queued_for_later is not None or (
            context.is_group and (queue_behind_active_turn or session_work_queue.has_work(queue_key))
        ):
            queued = queued_for_later or await _enqueue_session_work(
                context=context,
                event=dict(event),
                turn_payload=_deferred_input_payload(inbound, turn_payload),
                kind="turn",
            )
            log_event(
                "qq_group_turn_queued",
                session_id=context.session_id,
                profile_user_id=context.profile_user_id,
                group_id=int(getattr(context, "group_id", 0) or 0),
                user_id=qq_user_id,
                source_message_id=str(getattr(context, "source_message_id", "") or ""),
                queue_sequence=int(queued.get("sequence") or 0),
                pending_count=int(queued.get("pending_count") or 0),
                reason=str(steer_result.get("status") or "session_queue_busy"),
            )
            return {
                "frame": {"status": "queued", "speech": ""},
                "reply_messages": [],
                "send_result": {
                    "ok": bool(queued.get("ok")),
                    "status": "queued" if queued.get("ok") else "queue_failed",
                    "reason": str(queued.get("reason") or "session_fifo"),
                    "results": [],
                    "queue_sequence": int(queued.get("sequence") or 0),
                    "pending_count": int(queued.get("pending_count") or 0),
                },
            }
        queue_wait_started_at = time.perf_counter()
        async with turn_coordinator.hold(
            context.profile_user_id,
            context.session_id,
            actor_id=actor_id,
            channel="qq",
            turn_kind=str(turn_payload.get("turn_kind") or "").strip(),
        ) as turn_control_id:
            queue_wait_ms = max(0.0, (time.perf_counter() - queue_wait_started_at) * 1000)
            turn_payload["_turn_control_id"] = turn_control_id
            with plugin_turn_router.processing_inbound(inbound, turn_control_id) if inbound else nullcontext():
                result = await _run_qq_turn_delivery_unlocked(
                    context=context, event=event, turn_payload=turn_payload,
                )
            if isinstance(result, dict):
                timing = dict(result.get("timing") or {})
                timing["queue_wait_ms"] = round(queue_wait_ms, 1)
                result["timing"] = timing
            return result

    async def _submit_plugin_agent_event(
        request: HostTurnIntent,
        resolved_reference: Mapping[str, str],
    ) -> HostTurnResult:
        """Run one plugin event through the ordinary QQ Agent turn path."""

        try:
            request = normalize_turn_intent(request)
        except PluginTurnError as exc:
            return HostTurnResult(False, "rejected", exc.reason)
        if request.requires_queue and not isinstance(session_work_queue, DurableSessionWorkQueue):
            return HostTurnResult(False, "rejected", "agent_turn_queue_unavailable")
        if not isinstance(resolved_reference, Mapping):
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        if str(resolved_reference.get("channel") or "") != "qq":
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        conversation_kind = str(resolved_reference.get("kind") or "").strip().lower()
        if conversation_kind not in {"direct", "group"}:
            return HostTurnResult(False, "rejected", "unsupported_conversation_kind")
        memory_mode = str(getattr(request, "memory_mode", "current_turn")).strip().lower()
        if memory_mode not in {"current_turn", "timeline"}:
            return HostTurnResult(False, "rejected", "unsupported_memory_mode")
        text_delivery = str(getattr(request, "presentation_mode", "default") or "default").strip().lower()
        if text_delivery not in {"default", "single_message"}:
            return HostTurnResult(False, "rejected", "unsupported_text_delivery")

        recipient = str(resolved_reference.get("recipient") or "").strip()
        session_id = str(resolved_reference.get("session") or "").strip()
        profile_user_id = str(resolved_reference.get("profile") or "").strip()
        target_id = 0
        user_id = 0
        group_id = 0
        if conversation_kind == "group":
            raw_group_id = recipient.removeprefix("group:").strip() if recipient else ""
            if not raw_group_id and session_id.startswith("qq_group_shared_"):
                raw_group_id = session_id.removeprefix("qq_group_shared_")
            if not raw_group_id.isdigit() or int(raw_group_id) <= 0:
                return HostTurnResult(False, "rejected", "event_context_unresolved")
            group_id = int(raw_group_id)
            target_id = group_id
            resolved_session_id, resolved_profile_user_id = qq_gateway.resolve_identity(
                user_id=0,
                group_id=group_id,
            )
        else:
            raw_user_id = recipient.removeprefix("user:").strip() if recipient else ""
            if not raw_user_id and session_id.startswith("qq_pri_"):
                raw_user_id = session_id.removeprefix("qq_pri_")
            if not raw_user_id and session_id == "master":
                raw_user_id = str(getattr(qq_gateway, "master_qq", "") or "").strip()
            if not raw_user_id.isdigit() or int(raw_user_id) <= 0:
                return HostTurnResult(False, "rejected", "event_context_unresolved")
            user_id = int(raw_user_id)
            target_id = user_id
            resolved_session_id, resolved_profile_user_id = qq_gateway.resolve_identity(
                user_id=user_id,
                group_id=0,
            )

        # The signed reference must still resolve to the same live namespace.
        if session_id != resolved_session_id:
            return HostTurnResult(False, "rejected", "event_context_mismatch")
        if profile_user_id != resolved_profile_user_id:
            return HostTurnResult(False, "rejected", "event_context_mismatch")
        character_pack_id = str(resolved_reference.get("character") or "").strip()
        if not character_pack_id:
            return HostTurnResult(False, "rejected", "event_context_unresolved")
        message = str(request.message or "").strip()
        if not message:
            return HostTurnResult(False, "rejected", "agent_event_message_required")
        event_timestamp = completion_timestamp(request, int(time.time()))
        sender_label = "插件事件"
        if str(request.source or "").strip().lower().startswith("host."):
            sender_label = "系统事件"
        source_event_id = str(getattr(request, "idempotency_key", "") or request.trace_id or "").strip()
        synthetic_event = {
            "time": event_timestamp,
            "post_type": "message",
            "message_type": "group" if conversation_kind == "group" else "private",
            "group_id": group_id,
            "user_id": user_id,
            "message_id": "",
        }
        extra_context = qq_gateway.build_extra_context(
            event=synthetic_event,
            is_group=conversation_kind == "group",
            user_id=user_id,
            group_id=group_id,
            sender_label=sender_label,
            reply_mode=qq_gateway.resolve_reply_mode(resolved_session_id),
            session_id=resolved_session_id,
        )
        from ..qq_gateway import QQMessageContext

        context = QQMessageContext(
            should_respond=True,
            reason="plugin_event",
            should_record=False,
            is_group=conversation_kind == "group",
            target_id=target_id,
            user_id=user_id,
            group_id=group_id,
            session_id=resolved_session_id,
            profile_user_id=resolved_profile_user_id,
            clean_message=message,
            raw_message=message,
            extra_context=extra_context,
            sender_label=sender_label,
            character_pack_id=character_pack_id,
            reply_mode=qq_gateway.resolve_reply_mode(resolved_session_id),
            chat_model_override=qq_gateway.resolve_chat_model_override(resolved_session_id),
            source_message_id=source_event_id,
        )
        turn_payload = context.to_turn_payload()
        causal_actor = str(resolved_reference.get("actor") or "").strip()
        causal_actor_profile = str(resolved_reference.get("actor_profile") or "").strip()
        if conversation_kind == "group" and causal_actor.startswith("qq:"):
            turn_payload["actor_stable_id"] = causal_actor
            if causal_actor_profile:
                turn_payload["actor_profile_user_id"] = causal_actor_profile
        turn_payload.update(
            {
                "message": message,
                "memory_message": message,
                "timestamp": event_timestamp,
                "turn_kind": "plugin_event",
                "transient_user_message": memory_mode == "current_turn",
                "plugin_external_event": request.event_payload(),
                "memory_idempotency_key": str(getattr(request, "idempotency_key", "") or "").strip(),
                "plugin_text_delivery": text_delivery,
                "plugin_text_prefix": str(getattr(request, "presentation_prefix", "") or "").strip(),
                "plugin_text_suffix": str(getattr(request, "presentation_suffix", "") or "").strip(),
                "plugin_text_strip_leading_addresses": list(
                    getattr(request, "strip_leading_addresses", ())
                ),
                "message_addressing": {
                    "mode": "current_request",
                    "trigger": "plugin_event",
                    "addressed_to_assistant": True,
                    "explicit_assistant_mention": False,
                    "primary_target": {"actor_id": "assistant"},
                    "mentions": [],
                },
            }
        )
        if isinstance(session_work_queue, DurableSessionWorkQueue):
            queued = await session_work_queue.enqueue(
                session_key=_session_work_key(context),
                profile_user_id=resolved_profile_user_id,
                session_id=resolved_session_id,
                kind="turn",
                payload={
                    **_durable_qq_work_payload(event=synthetic_event, turn_payload=turn_payload),
                    **({"plugin_turn_request_id": request.request_id} if request.request_id else {}),
                    "host_completion": completion_metadata(request, resolved_reference),
                },
                source="qq",
                source_event_id=f"plugin-turn:{request.request_id}" if request.request_id else source_event_id,
                input_fingerprint=completion_input_fingerprint(request, resolved_reference),
            )
            if not queued.get("ok"):
                return HostTurnResult(
                    False,
                    "failed",
                    str(queued.get("reason") or "agent_event_queue_failed"),
                    "not_sent",
                )
            queued_item = queued.get("item")
            queued_status = str(getattr(queued_item, "status", "") or "")
            if queued_status == "failed":
                return HostTurnResult(False, "failed", "agent_event_previously_failed", "not_sent")
            delivery_status = "suppressed" if queued_status == "committed" else "queued"
            return HostTurnResult(True, "accepted", "", delivery_status,
                                  str(queued.get("item_id") or getattr(queued_item, "item_id", "")))
        try:
            result = await _run_qq_turn_delivery(
                context=context,
                event=synthetic_event,
                turn_payload=turn_payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("plugin Agent event delivery failed")
            return HostTurnResult(False, "failed", "agent_event_delivery_failed")
        result_payload = result if isinstance(result, dict) else {}
        send_result = result_payload.get("send_result")
        send_payload = send_result if isinstance(send_result, dict) else {}
        if result_payload.get("final_frame_received") is False or bool(
            result_payload.get("_transient_final_failure")
        ) or bool(
            result_payload.get("frame", {}).get("_transient_final_failure")
            if isinstance(result_payload.get("frame"), dict)
            else False
        ):
            return HostTurnResult(False, "failed", "agent_event_turn_incomplete", "not_sent")
        if not bool(send_payload.get("ok")):
            return HostTurnResult(
                False,
                "failed",
                str(send_payload.get("reason") or "agent_event_delivery_failed"),
                str(send_payload.get("status") or "not_sent"),
            )
        delivery_status = str(send_payload.get("status") or "delivered").strip().lower()
        if delivery_status not in {"queued", "sent", "delivered", "suppressed"}:
            delivery_status = "delivered"
        return HostTurnResult(True, "completed", "", delivery_status)

    if plugin_agent_event_handler_registrar is not None:
        plugin_agent_event_handler_registrar("qq", _submit_plugin_agent_event)

    async def _resume_qq_after_capability_decision(
        *,
        context: Any,
        event: dict[str, Any],
        decision: str,
        capability_id: str,
        action_id: str,
        authorization_profile_user_id: str,
    ) -> None:
        """Resume through the normal turn path without inventing a user message."""

        decision_label = "批准" if decision == "approved" else "拒绝"
        target = action_id or capability_id or "该动作"
        original_actor_profile = str(authorization_profile_user_id or "").strip()
        original_user_id = int(getattr(context, "user_id", 0) or 0)
        if original_actor_profile.startswith("qq_") and original_actor_profile[3:].isdigit():
            original_user_id = int(original_actor_profile[3:])
        elif original_actor_profile == "master":
            original_user_id = int(getattr(qq_gateway, "master_qq", 0) or original_user_id)
        resume_context = replace(
            context,
            source_message_id="",
            clean_message="",
            raw_message="",
            user_id=original_user_id,
            sender_label=(
                str(getattr(context, "sender_label", "") or "")
                if original_user_id == int(getattr(context, "user_id", 0) or 0)
                else f"QQ {original_user_id}"
            ),
            actor_profile_user_id=original_actor_profile,
        )
        turn_payload = resume_context.to_turn_payload()
        turn_payload.update(
            {
                "message": f"【宿主能力审批结果】用户已{decision_label} {target}。请从刚才暂停的位置继续；若被拒绝，改用不需要该权限的方案或如实说明。",
                "memory_message": "",
                "source_message_id": "",
                "timestamp": int(time.time()),
                "turn_kind": "capability_approval_resume",
                "transient_user_message": True,
                "message_addressing": {
                    "mode": "current_request",
                    "trigger": "capability_approval_resume",
                    "addressed_to_assistant": True,
                    "explicit_assistant_mention": False,
                    "primary_target": {"actor_id": "assistant"},
                    "mentions": [],
                },
            }
        )
        try:
            await _run_qq_turn_delivery(
                context=resume_context,
                event={**event, "time": int(time.time()), "message_id": ""},
                turn_payload=turn_payload,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_logger = getattr(logger, "error", None)
            if callable(error_logger):
                error_logger("qq approval auto-resume failed: %s", exc.__class__.__name__)
            log_event(
                "qq_capability_approval_resume_failed",
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                capability_id=capability_id,
                action_id=action_id,
                decision=decision,
                reason=exc.__class__.__name__,
            )

    async def _run_qq_image_vision_followup(
        *,
        context: Any,
        event: dict[str, Any],
        attachment_ids: list[str],
        attachments_registered: list[dict[str, Any]],
        message_override: str = "",
        action_note: str = "",
        plugin_input: dict | None = None,
    ) -> None:
        started_at = time.perf_counter()
        try:
            if bool(getattr(context, "is_group", False)) and not qq_gateway.is_group_vision_enabled(
                getattr(context, "group_id", 0)
            ):
                log_event(
                    "qq_image_vision_followup_skipped",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    reason="group_vision_disabled",
                    attachment_count=len(getattr(context, "attachments", None) or []),
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )
                return
            wait_result = await asyncio.to_thread(
                engine.wait_for_qq_attachments_settled,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                attachment_ids=attachment_ids,
                timeout_seconds=_qq_attachment_ready_wait_seconds(context, config_module),
            )
            pending_image_ids = _qq_pending_image_attachment_ids(attachments_registered, wait_result)
            kinds_by_id = wait_result.get("kinds_by_id") if isinstance(wait_result.get("kinds_by_id"), dict) else {}
            native_images: list[dict[str, Any]] = []
            native_prepare = getattr(engine, "prepare_qq_native_image_inputs", None)
            if callable(native_prepare) and any(kind == "image" for kind in kinds_by_id.values()):
                native_result = await asyncio.to_thread(
                    native_prepare,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachment_ids=attachment_ids,
                    chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                    timeout_seconds=0.0,
                )
                native_images = [
                    dict(item) for item in list(native_result.get("images") or [])
                    if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                ][:5]
                native_ids = {str(item.get("attachment_id") or "") for item in native_images}
                pending_image_ids = [item_id for item_id in pending_image_ids if item_id not in native_ids]
            else:
                native_ids = set()
            failed_image_ids = [
                str(item or "").strip()
                for item in list(wait_result.get("failed") or [])
                if str(item or "").strip() and str(kinds_by_id.get(str(item or "").strip()) or "").lower() == "image"
                and str(item) not in native_ids
            ]
            if pending_image_ids or failed_image_ids:
                failure_send_result = await asyncio.to_thread(
                    qq_gateway.send_reply,
                    context,
                    _qq_image_followup_failure_message(
                        wait_result=wait_result if isinstance(wait_result, dict) else {},
                        pending_image_ids=pending_image_ids,
                        failed_image_ids=failed_image_ids,
                    ),
                )
                log_event(
                    "qq_image_vision_followup_not_ready",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    pending_count=len(pending_image_ids),
                    failed_count=len(failed_image_ids),
                    attachment_count=len(getattr(context, "attachments", None) or []),
                    attachments_registered=len(attachments_registered),
                    failure_notice_sent=bool(failure_send_result.get("ok")),
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )
                return

            effective_message_override = (
                message_override.strip() or str(getattr(context, "clean_message", "") or "").strip()
                or "用户刚刚发送了一张图片。请只根据本轮实际提供的图片或视觉摘要自然回应。"
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
            if attachment_ids:
                turn_payload["qq_current_attachment_ids"] = list(attachment_ids)
            if native_images:
                turn_payload["native_user_images"] = native_images
            turn_payload.update(plugin_input or {})
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

    @router.get(f"{qq_route_base}/napcat/status")
    async def qq_napcat_status(request: Request) -> JSONResponse:
        authorization = diagnostic_auth.authorize(request)
        if not authorization.ok:
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        data = dict(qq_gateway.status())
        return JSONResponse({"status": "ok", "data": data})

    @router.post(f"{qq_route_base}/self-check")
    async def qq_self_check(request: Request) -> JSONResponse:
        """QQ / NapCat 连通性自检。主动测试 OneBot HTTP API 可达性和鉴权，返回结构化诊断。"""
        authorization = diagnostic_auth.authorize(request)
        if not authorization.ok:
            return JSONResponse(
                {"ok": False, "status": "forbidden", "reason": authorization.reason},
                status_code=authorization.status_code,
            )
        result = qq_gateway.self_check()
        return JSONResponse({"status": "ok", "data": result})

    @router.post(f"{qq_route_base}/napcat/event")
    async def qq_napcat_event(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        event: dict = {}
        context = None
        turn_timing: dict[str, float] = {}
        plugin_inputs = ExitStack()
        plugin_inbound = ""

        if channel_config is not None:
            auth = channel_config.authorize_webhook(request, body=await request.body())
            if not auth.ok:
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=False,
                )
                return JSONResponse(
                    {"ok": False, "status": "forbidden", "reason": auth.reason},
                    status_code=auth.status_code,
                )

        bridge_enabled = (
            channel_config.enabled
            if channel_config is not None
            else bool(getattr(config_module, "QQ_BRIDGE_ENABLED", False))
        )
        if not bridge_enabled:
            runtime_metrics.observe_request(
                "qq_napcat_event",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=True,
            )
            return JSONResponse({"status": "disabled", "message": "QQ bridge is disabled"})

        try:
            event = await request.json()
            if not isinstance(event, dict):
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=False,
                )
                return JSONResponse(
                    {"ok": False, "status": "invalid_event", "reason": "qq_event_must_be_object"},
                    status_code=400,
                )

            if channel_config is not None:
                identity = channel_config.authorize_event_identity(event)
                if not identity.ok:
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                        ok=False,
                    )
                    return JSONResponse(
                        {"ok": False, "status": "forbidden", "reason": identity.reason},
                        status_code=identity.status_code,
                    )

            context = qq_gateway.build_message_context(event)
            if plugin_turn_router is not None and getattr(context, "inbound_message", None) is not None:
                plugin_inbound = plugin_inputs.enter_context(plugin_turn_router.inbound(
                    _plugin_input_context(context), required=not context.should_respond))
            await _publish_plugin_channel_event(context=context, event=event, request=request)
            if plugin_inbound and plugin_turn_router.inbound_requested(plugin_inbound) and not context.should_respond:
                context = replace(
                    context,
                    should_respond=True,
                    reason="plugin_event_requested_turn",
                    addressed_to_assistant=True,
                )
            pre_resolved_quote: dict[str, Any] = {}
            optional_reply = False
            if (
                not context.should_respond
                and bool(getattr(context, "is_group", False))
                and context.inbound_message is not None
                and context.inbound_message.reply_to is not None
            ):
                quoted_resolver = getattr(qq_gateway, "resolve_quoted_message_evidence", None)
                if callable(quoted_resolver):
                    resolved_quote = await asyncio.to_thread(quoted_resolver, event, context=context)
                    pre_resolved_quote = resolved_quote if isinstance(resolved_quote, dict) else {}
                    reply_reference = _qq_quoted_message_reference(pre_resolved_quote)
                    if reply_reference:
                        context = replace(context, reply_reference=reply_reference)
                    quoted_message = (
                        pre_resolved_quote.get("quoted_message")
                        if isinstance(pre_resolved_quote.get("quoted_message"), dict)
                        else {}
                    )
                    if bool(pre_resolved_quote.get("ok")) and bool(quoted_message.get("actor_is_bot")):
                        context = replace(
                            context,
                            should_respond=True,
                            reason="group_reply_to_assistant",
                            addressed_to_assistant=True,
                        )
                        optional_reply = not bool(getattr(context, "mentioned_bot", False))
            master_qq = str(getattr(qq_gateway, "master_qq", "") or "").strip()
            if (
                not context.should_respond
                and master_qq
                and str(int(getattr(context, "user_id", 0) or 0)) == master_qq
                and _is_qq_stop_command(getattr(context, "clean_message", ""))
            ):
                # Stop is a host control event, not a conversational reply
                # trigger. It must reach the coordinator even without @ while
                # passive group messages continue through the normal recorder.
                context = replace(
                    context,
                    should_respond=True,
                    reason="owner_stop_command",
                    addressed_to_assistant=True,
                )
            if not context.should_respond:
                if bool(getattr(context, "should_record", False)):
                    passive_memory_policy = _resolve_group_passive_memory_policy(
                        config_module,
                        getattr(context, "group_id", 0),
                    )
                    if not bool(passive_memory_policy.get("enabled")):
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        runtime_metrics.observe_request(
                            "qq_napcat_event",
                            duration_ms=duration_ms,
                            ok=True,
                        )
                        log_event(
                            "qq_passive_group_message_skipped",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            group_id=int(getattr(context, "group_id", 0) or 0),
                            user_id=int(getattr(context, "user_id", 0) or 0),
                            reason="group_passive_memory_filtered",
                            policy_mode=str(passive_memory_policy.get("mode") or "all"),
                            duration_ms=round(duration_ms, 1),
                        )
                        return JSONResponse(
                            {
                                "status": "ignored",
                                "reason": "group_passive_memory_filtered",
                                "policy_mode": str(passive_memory_policy.get("mode") or "all"),
                            }
                        )
                    passive_queue_key = _session_work_key(context)
                    needs_passive_enrichment = bool(
                        getattr(context, "forward_refs", ()) or list(getattr(context, "attachments", None) or [])
                    )
                    if turn_coordinator.is_busy(
                        context.profile_user_id,
                        context.session_id,
                    ) or session_work_queue.has_work(passive_queue_key) or needs_passive_enrichment:
                        deferred_payload = context.to_turn_payload()
                        deferred_payload["timestamp"] = int(event.get("time") or time.time())
                        queued = await _enqueue_session_work(
                            context=context,
                            event=dict(event),
                            turn_payload=dict(deferred_payload),
                            kind="passive",
                        )
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        runtime_metrics.observe_request(
                            "qq_napcat_event",
                            duration_ms=duration_ms,
                            ok=bool(queued.get("ok")),
                        )
                        log_event(
                            "qq_passive_group_message_buffered",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            group_id=int(getattr(context, "group_id", 0) or 0),
                            user_id=int(getattr(context, "user_id", 0) or 0),
                            reason=("passive_content_enrichment" if needs_passive_enrichment else "active_turn_in_progress"),
                            queue_sequence=int(queued.get("sequence") or 0),
                            pending_count=int(queued.get("pending_count") or 0),
                            duration_ms=round(duration_ms, 1),
                        )
                        return JSONResponse(
                            {
                                "status": "buffered" if queued.get("ok") else "record_failed",
                                "reason": (
                                    "passive_content_enrichment" if needs_passive_enrichment else "active_turn_in_progress"
                                ),
                                "queue_reason": str(queued.get("reason") or "session_fifo"),
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "queue_sequence": int(queued.get("sequence") or 0),
                                "pending_count": int(queued.get("pending_count") or 0),
                            }
                        )
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
                    attention_result = (
                        _schedule_group_attention(
                            context,
                            event,
                            projection_anchor_source_id=str(record_payload.get("source_id") or ""),
                        )
                        if record_ok
                        else {"scheduled": False, "reason": "record_failed"}
                    )
                    return JSONResponse(
                        {
                            "status": "recorded" if record_ok else "record_failed",
                            "reason": context.reason,
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                            "record_result": record_result,
                            "attention": attention_result,
                        }
                    )
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "ignored", "reason": context.reason})

            _cancel_group_attention(context)

            _qq_action_note = ""
            _qq_turn_message_override = ""
            _qq_turn_extra_context_note = (
                "event.qq_optional_reply_review"
                if optional_reply
                else ""
            )
            _qq_native_user_images: list[dict[str, Any]] = []

            if context.reason == "qq_poke":
                _care_module_getter = getattr(engine, "get_care_module", None)
                _care_module = _care_module_getter() if callable(_care_module_getter) else None
                _char_resources = getattr(engine, "desktop_pet_character_resources", None)
                _shop_items = (
                    _char_resources.load_care_shop_items(context.character_pack_id)
                    if _care_module is not None
                    and _care_module.enabled
                    and _char_resources
                    and context.character_pack_id
                    else None
                )
                poke_handler = getattr(qq_gateway, "handle_poke_event", None)
                poke_outcome = (
                    await asyncio.to_thread(
                        poke_handler,
                        context,
                        event,
                        care_module=_care_module,
                        shop_items=_shop_items,
                        now_ms=int(event.get("time") or time.time()) * 1000,
                    )
                    if callable(poke_handler)
                    else None
                )
                if poke_outcome is not None and str(getattr(poke_outcome, "status", "") or "") == "duplicate":
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                    log_event(
                        "qq_poke_duplicate",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        event_id=str(getattr(poke_outcome, "event_id", "") or ""),
                    )
                    return JSONResponse(
                        {
                            "status": "duplicate",
                            "reason": "poke_event_already_applied",
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                        }
                    )
                if poke_outcome is not None:
                    _qq_turn_message_override = str(getattr(poke_outcome, "memory_text", "") or "").strip()
                    _qq_action_note = str(getattr(poke_outcome, "prompt_text", "") or "").strip()
                    if str(getattr(poke_outcome, "status", "") or "") == "failed":
                        log_event(
                            "qq_poke_reactor_failed",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            event_id=str(getattr(poke_outcome, "event_id", "") or ""),
                            reason=str(getattr(poke_outcome, "reason", "") or "state_apply_failed"),
                        )
                    else:
                        log_event(
                            "qq_poke_reactor",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            event_id=str(getattr(poke_outcome, "event_id", "") or ""),
                            outcome_kind=str(getattr(poke_outcome, "outcome_kind", "") or "plain"),
                            outcome_status=str(getattr(poke_outcome, "status", "") or "ok"),
                        )
                    await _publish_plugin_poke_event(
                        context=context,
                        event=event,
                        outcome=poke_outcome,
                        request=request,
                    )

            group_vision_command_result = qq_gateway.handle_group_vision_command(
                context,
                sender_role=_qq_sender_role(event),
            )
            if isinstance(group_vision_command_result, dict):
                reply = str(group_vision_command_result.get("reply") or "").strip()
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")) and bool(group_vision_command_result.get("ok")),
                )
                log_event(
                    "qq_group_vision_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(group_vision_command_result.get("status") or ""),
                    command_ok=bool(group_vision_command_result.get("ok")),
                    vision_enabled=bool(group_vision_command_result.get("vision_enabled")),
                    state_persisted=group_vision_command_result.get("state_persisted"),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_group_vision_command",
                        "command_status": str(group_vision_command_result.get("status") or ""),
                        "command_ok": bool(group_vision_command_result.get("ok")),
                        "vision_enabled": bool(group_vision_command_result.get("vision_enabled")),
                        "state_persisted": group_vision_command_result.get("state_persisted"),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )

            group_emotion_command_result = qq_gateway.handle_group_emotion_command(
                context,
                sender_role=_qq_sender_role(event),
            )
            if isinstance(group_emotion_command_result, dict):
                reply = str(group_emotion_command_result.get("reply") or "").strip()
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
                command_ok = bool(group_emotion_command_result.get("ok"))
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")) and command_ok,
                )
                log_event(
                    "qq_group_emotion_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(group_emotion_command_result.get("status") or ""),
                    command_ok=command_ok,
                    emotion_enabled=bool(group_emotion_command_result.get("emotion_enabled")),
                    state_persisted=group_emotion_command_result.get("state_persisted"),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") and command_ok else "send_failed",
                        "reason": "qq_group_emotion_command",
                        "command_status": str(group_emotion_command_result.get("status") or ""),
                        "command_ok": command_ok,
                        "emotion_enabled": bool(group_emotion_command_result.get("emotion_enabled")),
                        "state_persisted": group_emotion_command_result.get("state_persisted"),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )

            group_attention_command_result = qq_gateway.handle_group_attention_command(
                context,
                sender_role=_qq_sender_role(event),
                default_mode=str(getattr(config_module, "QQ_GROUP_ATTENTION_MODE", "engaged") or "engaged"),
            )
            if isinstance(group_attention_command_result, dict):
                reply = str(group_attention_command_result.get("reply") or "").strip()
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
                command_ok = bool(group_attention_command_result.get("ok"))
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")) and command_ok,
                )
                log_event(
                    "qq_group_attention_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(group_attention_command_result.get("status") or ""),
                    command_ok=command_ok,
                    attention_mode=str(group_attention_command_result.get("attention_mode") or ""),
                    state_persisted=group_attention_command_result.get("state_persisted"),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") and command_ok else "send_failed",
                        "reason": "qq_group_attention_command",
                        "command_status": str(group_attention_command_result.get("status") or ""),
                        "command_ok": command_ok,
                        "attention_mode": str(group_attention_command_result.get("attention_mode") or ""),
                        "state_persisted": group_attention_command_result.get("state_persisted"),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "send_result": send_result,
                    }
                )

            access_permission_command = qq_gateway.parse_access_permission_command(context.clean_message)
            if isinstance(access_permission_command, dict):
                capability_config_base_dir = getattr(
                    engine,
                    "capability_config_base_dir",
                    getattr(config_module, "DATA_DIR", None),
                )
                policy_profile_user_id = str(
                    getattr(context, "actor_profile_user_id", "") or context.profile_user_id
                )
                policy_payload = load_capability_config(
                    base_dir=capability_config_base_dir,
                    profile_user_id=policy_profile_user_id,
                )
                current_modes = {
                    family_id: approval_mode_for_capability(
                        policy_payload.get("approvalPolicy"),
                        "",
                        family_id=family_id,
                    )
                    for family_id in ("ops", "extensions")
                }

                def _save_access_modes(modes: dict[str, str]) -> dict[str, str]:
                    saved = save_capability_approval_modes(
                        base_dir=capability_config_base_dir,
                        profile_user_id=policy_profile_user_id,
                        modes=modes,
                    )
                    if not saved.get("ok"):
                        raise RuntimeError(str(saved.get("reason") or "access_permission_save_failed"))
                    return dict(saved.get("approvalModes") or {})

                access_result = qq_gateway.handle_access_permission_command(
                    context,
                    command=access_permission_command,
                    current_modes=current_modes,
                    apply_modes=_save_access_modes,
                )
                if isinstance(access_result, dict):
                    reply = str(access_result.get("reply") or "").strip()
                    send_result = await _send_route_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                    command_ok = bool(access_result.get("ok"))
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=bool(send_result.get("ok")) and command_ok)
                    log_event(
                        "qq_access_permission_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        policy_profile_user_id=policy_profile_user_id,
                        command_status=str(access_result.get("status") or ""),
                        command_ok=command_ok,
                        scope=str(access_permission_command.get("scope") or ""),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_access_permission_command",
                            "command_status": str(access_result.get("status") or ""),
                            "command_ok": command_ok,
                            "modes": dict(access_result.get("modes") or {}),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "send_result": send_result,
                        }
                    )

            capability_approval_command = qq_gateway.parse_capability_approval_command(context.clean_message)
            if isinstance(capability_approval_command, dict):
                approval_store_getter = getattr(engine, "_get_approval_store", None)
                approval_store = (
                    approval_store_getter()
                    if callable(approval_store_getter)
                    else getattr(engine, "approval_store", None)
                )
                approval_result = qq_gateway.handle_capability_approval_command(
                    context,
                    command=capability_approval_command,
                    approval_store=approval_store,
                )
                if isinstance(approval_result, dict):
                    reply = str(approval_result.get("reply") or "").strip()
                    send_result = (
                        await _send_route_reply(context, reply)
                        if reply
                        else {"ok": False, "reason": "empty_reply"}
                    )
                    command_ok = bool(approval_result.get("ok"))
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")) and command_ok,
                    )
                    log_event(
                        "qq_capability_approval_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        is_group=bool(context.is_group),
                        command_status=str(approval_result.get("status") or ""),
                        command_ok=command_ok,
                        request_id_suffix=str(approval_result.get("request_id") or "")[-8:],
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    decision = str(approval_result.get("status") or "")
                    if command_ok and send_result.get("ok") and decision in {"approved", "denied"}:
                        schedule_followup(
                            _resume_qq_after_capability_decision(
                                context=context,
                                event=event,
                                decision=decision,
                                capability_id=str(approval_result.get("capability_id") or ""),
                                action_id=str(approval_result.get("action_id") or ""),
                                authorization_profile_user_id=str(
                                    approval_result.get("authorization_profile_user_id") or ""
                                ),
                            )
                        )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_capability_approval_command",
                            "command_status": str(approval_result.get("status") or ""),
                            "command_ok": command_ok,
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "send_result": send_result,
                        }
                    )

            mface_config_result = qq_gateway.handle_mface_config_command(context, event)
            if isinstance(mface_config_result, dict):
                reply = str(mface_config_result.get("reply") or "").strip()
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
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
                action = str(workspace_command.get("action") or "").strip()
                command_status = "ok"
                command_ok = True
                if action == "help":
                    reply = _build_qq_workspace_help_reply()
                elif action == "list":
                    reply = _build_qq_workspace_list_reply(
                        engine,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                    )
                elif action == "clear":
                    clear_result = clear_workspace_files(
                        engine,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                        target=str(workspace_command.get("target") or "current"),
                        kind=str(workspace_command.get("kind") or "any"),
                        reason="用户通过 QQ 工作台指令清理当前文件工作台。",
                        delete_storage=bool(workspace_command.get("delete_storage")),
                        timestamp=int(event.get("time") or time.time()),
                    )
                    command_ok = bool(clear_result.get("ok")) if isinstance(clear_result, dict) else False
                    command_status = (
                        str(clear_result.get("status") or "failed")
                        if isinstance(clear_result, dict)
                        else "failed"
                    )
                    reply = _build_qq_workspace_clear_reply(
                        clear_result if isinstance(clear_result, dict) else {},
                        delete_storage=bool(workspace_command.get("delete_storage")),
                    )
                else:
                    command_status = "unknown_action"
                    command_ok = False
                    reply = "这个工作台指令暂时不支持。发送“工作台帮助”查看可用指令。"
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
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

            character_command_result = qq_gateway.handle_character_command(
                context,
                character_resource_service=getattr(engine, "desktop_pet_character_resources", None),
            )
            if isinstance(character_command_result, dict):
                reply = str(character_command_result.get("reply") or "").strip()
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
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
                        await _send_route_reply(context, reply)
                        if reply
                        else {"ok": False, "reason": "empty_reply"}
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
                send_result = (
                    await _send_route_reply(context, reply)
                    if reply
                    else {"ok": False, "reason": "empty_reply"}
                )
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

            thinking_mode_command = qq_gateway.parse_thinking_mode_command(context.clean_message)
            if isinstance(thinking_mode_command, dict):
                llm_runtime = getattr(engine, "llm", None)
                try:
                    supports_thinking = bool(
                        llm_runtime is not None
                        and callable(getattr(llm_runtime, "chat_supports_deepseek_thinking", None))
                        and llm_runtime.chat_supports_deepseek_thinking(
                            chat_model_override=str(getattr(context, "chat_model_override", "") or "")
                        )
                    )
                except Exception:
                    supports_thinking = False
                thinking_mode_command_result = qq_gateway.handle_thinking_mode_command(
                    context,
                    command=thinking_mode_command,
                    current_mode=str(
                        getattr(getattr(engine, "settings", None), "llm_thinking_mode", "disabled")
                        or "disabled"
                    ),
                    supported=supports_thinking,
                    apply_mode=thinking_mode_setter,
                )
                if isinstance(thinking_mode_command_result, dict):
                    reply = str(thinking_mode_command_result.get("reply") or "").strip()
                    send_result = (
                        await _send_route_reply(context, reply)
                        if reply
                        else {"ok": False, "reason": "empty_reply"}
                    )
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    command_ok = bool(thinking_mode_command_result.get("ok"))
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")) and command_ok,
                    )
                    log_event(
                        "qq_thinking_mode_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_status=str(thinking_mode_command_result.get("status") or ""),
                        command_ok=command_ok,
                        thinking_mode=str(thinking_mode_command_result.get("thinking_mode") or ""),
                        supported=bool(thinking_mode_command_result.get("supported")),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_thinking_mode_command",
                            "command_status": str(thinking_mode_command_result.get("status") or ""),
                            "command_ok": command_ok,
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "thinking_mode": str(
                                thinking_mode_command_result.get("thinking_mode") or ""
                            ),
                            "supported": bool(thinking_mode_command_result.get("supported")),
                            "send_result": send_result,
                        }
                    )

            chat_model_command = qq_gateway.parse_chat_model_command(context.clean_message)
            if isinstance(chat_model_command, dict):
                available_models: list[str] | None = None
                list_error = ""
                if str(chat_model_command.get("action") or "") in {"list", "switch"}:
                    try:
                        runtime_settings = getattr(engine, "settings", None)
                        settings = (
                            effective_settings_from_runtime_settings(runtime_settings)
                            if runtime_settings is not None
                            else effective_settings_from_config(config_module)
                        )
                        available_models = await asyncio.to_thread(probe_model_ids, settings)
                    except Exception as exc:
                        list_error = redact_provider_error(exc)
                        available_models = []
                chat_model_command_result = qq_gateway.handle_chat_model_command(
                    context,
                    command=chat_model_command,
                    default_model=str(
                        getattr(getattr(engine, "settings", None), "chat_model_name", "")
                        or getattr(config_module, "CHAT_MODEL_NAME", "")
                        or ""
                    ),
                    available_models=available_models,
                    list_error=list_error,
                )
                if isinstance(chat_model_command_result, dict):
                    reply = str(chat_model_command_result.get("reply") or "").strip()
                    send_result = (
                        await _send_route_reply(context, reply)
                        if reply
                        else {"ok": False, "reason": "empty_reply"}
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

            _care_module_getter = getattr(engine, "get_care_module", None)
            _care_module = _care_module_getter() if callable(_care_module_getter) else None
            _char_resources = getattr(engine, "desktop_pet_character_resources", None)
            _shop_items = (
                _char_resources.load_care_shop_items(context.character_pack_id)
                if _care_module is not None and _care_module.enabled and _char_resources and context.character_pack_id
                else None
            )
            economy_command_result = qq_gateway.handle_economy_command(
                context,
                care_module=_care_module,
                shop_items=_shop_items,
                now_ms=int(time.time() * 1000),
            )
            if isinstance(economy_command_result, dict):
                if economy_command_result.get("_llm_passthrough"):
                    # Economy action processed; hand off to LLM for the actual reply
                    economy_note = str(economy_command_result.get("qq_action_note") or "").strip()
                    _qq_action_note = "\n".join(part for part in [_qq_action_note, economy_note] if part)
                    economy_turn_message = str(economy_command_result.get("turn_message") or "").strip()
                    if economy_turn_message:
                        _qq_turn_message_override = economy_turn_message
                    # fall through to LLM pipeline below
                else:
                    reply = str(economy_command_result.get("reply") or "").strip()
                    send_result = (
                        await _send_route_reply(context, reply)
                        if reply
                        else {"ok": False, "reason": "empty_reply"}
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

            # Plugin QQ command dispatch — checked after all built-in commands
            _plugin_command_broker = (
                plugin_command_broker_provider()
                if plugin_command_broker_provider is not None
                else getattr(request.app.state, "akane_plugin_command_broker", None)
            )
            if _plugin_command_broker is not None and context.clean_message.startswith("/"):
                _cmd_text = context.clean_message.strip()
                _cmd_token, _, _cmd_args = _cmd_text.partition(" ")
                if _plugin_command_broker.handles(_cmd_token):
                    _source_event_id = str(event.get("message_id") or "").strip()
                    if not _source_event_id:
                        _source_event_id = ":".join(
                            (
                                str(event.get("time") or ""),
                                str(context.user_id or 0),
                                str(context.group_id or 0),
                                _cmd_token,
                            )
                        )
                    _cmd_result = await _plugin_command_broker.dispatch(
                        command=_cmd_token,
                        args=_cmd_args,
                        qq_number=int(context.user_id or 0),
                        group_id=int(context.group_id or 0),
                        is_group=bool(context.is_group),
                        idempotency_key=_source_event_id,
                        sender_role=_qq_sender_role(event),
                        profile_user_id=str(context.profile_user_id or ""),
                        session_id=str(context.session_id or ""),
                        character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                        conversation_ref=(
                            str(plugin_conversation_ref_issuer(
                                profile_user_id=context.profile_user_id,
                                session_id=context.session_id,
                                character_pack_id=context.character_pack_id,
                                user_id=context.user_id,
                                group_id=context.group_id,
                                actor_stable_id=f"qq:{context.user_id}" if context.is_group else "",
                                actor_profile_user_id=str(context.actor_profile_user_id or ""),
                            ) or "")
                            if plugin_conversation_ref_issuer is not None
                            else ""
                        ),
                    )
                    if _cmd_result.handled:
                        if _cmd_result.reply_text:
                            send_result = await _send_route_replies(context, [_cmd_result.reply_text])
                        else:
                            send_result = {
                                "ok": True,
                                "status": "no_reply",
                                "reason": "plugin_requested_no_reply",
                                "count": 0,
                                "results": [],
                            }
                        command_ok = not bool(_cmd_result.reason)
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        runtime_metrics.observe_request(
                            "qq_napcat_event",
                            duration_ms=duration_ms,
                            ok=bool(send_result.get("ok")) and command_ok,
                        )
                        log_event(
                            "qq_plugin_command",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            command=_cmd_token,
                            command_ok=command_ok,
                            command_status=str(_cmd_result.reason or "ok"),
                            sent=bool(send_result.get("ok")) and bool(_cmd_result.reply_text),
                            duration_ms=round(duration_ms, 1),
                        )
                        return JSONResponse(
                            {
                                "status": "ok" if send_result.get("ok") else "send_failed",
                                "reason": "qq_plugin_command",
                                "command_status": str(_cmd_result.reason or "ok"),
                                "command_ok": command_ok,
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "send_result": send_result,
                            }
                        )

            forward_payload: dict[str, Any] = {}
            if getattr(context, "forward_refs", ()):
                forward_result = await asyncio.to_thread(
                    qq_gateway.resolve_forward_message_evidence,
                    event,
                    context=context,
                )
                forward_payload = forward_result if isinstance(forward_result, dict) else {}
                forwarded_attachments = [
                    dict(item)
                    for item in list(forward_payload.get("attachments") or [])
                    if isinstance(item, dict)
                ]
                if forwarded_attachments:
                    context = replace(
                        context,
                        attachments=_merge_qq_attachments(context.attachments, forwarded_attachments),
                    )
                forward_references = _qq_structured_forward_references(forward_payload)
                if forward_references:
                    context = replace(context, forward_references=forward_references)
                log_event(
                    "qq_forward_message_resolved",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    status=str(forward_payload.get("status") or "unknown"),
                    ok=bool(forward_payload.get("ok")),
                    forward_count=int(forward_payload.get("forward_count") or 0),
                    resolved_count=int(forward_payload.get("resolved_count") or 0),
                    attachment_count=len(forwarded_attachments),
                )

            quoted_resolver = getattr(qq_gateway, "resolve_quoted_message_evidence", None)
            if not callable(quoted_resolver):
                quoted_resolver = qq_gateway.resolve_quoted_attachments
            if pre_resolved_quote:
                quoted_payload = dict(pre_resolved_quote)
            else:
                quoted_result = await asyncio.to_thread(quoted_resolver, event, context=context)
                quoted_payload = quoted_result if isinstance(quoted_result, dict) else {}
            reply_reference = _qq_quoted_message_reference(quoted_payload)
            if reply_reference:
                context = replace(context, reply_reference=reply_reference)
            quoted_forwards = _qq_structured_forward_references(quoted_payload)
            if quoted_forwards:
                context = replace(context, forward_references=context.forward_references + quoted_forwards)
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
            quoted_context_note = _build_qq_unavailable_quote_context(quoted_payload)
            if quoted_context_note:
                _qq_turn_extra_context_note = "\n\n".join(
                    part for part in (_qq_turn_extra_context_note, quoted_context_note) if part
                )

            images_allowed = not context.is_group or qq_gateway.is_group_vision_enabled(context.group_id)
            if not images_allowed:
                attachments = list(context.attachments or [])
                allowed_attachments = [
                    item
                    for item in attachments
                    if not (isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image")
                ]
                blocked_image_count = len(attachments) - len(allowed_attachments)
                if blocked_image_count or any(
                    isinstance(item, dict) and str(item.get("kind") or "") in {"file", "document"}
                    for item in allowed_attachments
                ):
                    context = replace(context, attachments=allowed_attachments)
                    vision_disabled_note = (
                        "【本群识图设置】本群已关闭图片识别；包括以文件发送的图片，本轮未送入视觉分析。"
                        "文件可能只登记基本信息，不代表已看过图片内容。"
                        "不要声称看到了图片；如果用户询问图片内容，请简短说明本群识图已关闭。"
                    )
                    _qq_turn_extra_context_note = "\n".join(
                        part for part in (_qq_turn_extra_context_note, vision_disabled_note) if part
                    )
                    log_event(
                        "qq_group_vision_bypassed",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        blocked_image_count=blocked_image_count,
                    )

            attachments_registered = []
            attachment_ids: list[str] = []
            if context.attachments:
                attachments_registered = await asyncio.to_thread(
                    engine.ingest_qq_attachments,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachments=_with_qq_sender_context(list(context.attachments), context),
                    character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                    timestamp=int(event.get("time") or time.time()),
                    observe_images=images_allowed,
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
                    may_contain_file_image = any(
                        isinstance(item, dict) and str(item.get("kind") or "").strip().lower() in {"file", "document"}
                        for item in list(context.attachments or [])
                    )
                    if images_allowed and (has_image_attachment or may_contain_file_image):
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
                        elif has_image_attachment:
                            _schedule_input_followup(
                                _run_qq_image_vision_followup(
                                    context=context,
                                    event=dict(event),
                                    attachment_ids=attachment_ids,
                                    attachments_registered=[
                                        item for item in attachments_registered if isinstance(item, dict)
                                    ],
                                    message_override=_qq_turn_message_override,
                                    action_note=_qq_action_note,
                                    plugin_input=_input_marker(plugin_inbound),
                                ), plugin_inbound,
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
                        # A file can finish byte classification during this
                        # wait, after the first native-image attempt returned.
                        settled_kinds = attachment_wait_result.get("kinds_by_id") or {}
                        native_prepare = getattr(engine, "prepare_qq_native_image_inputs", None)
                        if images_allowed and callable(native_prepare) and any(
                            kind == "image" for kind in settled_kinds.values()
                        ):
                            native_result = await asyncio.to_thread(
                                native_prepare,
                                profile_user_id=context.profile_user_id,
                                session_id=context.session_id,
                                attachment_ids=attachment_ids,
                                chat_model_override=str(getattr(context, "chat_model_override", "") or ""),
                                timeout_seconds=0.0,
                            )
                            _qq_native_user_images = [
                                dict(item) for item in list(native_result.get("images") or [])
                                if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/")
                            ][:5]
                        pending_image_ids = (
                            _qq_pending_image_attachment_ids([], attachment_wait_result) if images_allowed else []
                        )
                        native_ids = {str(item.get("attachment_id") or "") for item in _qq_native_user_images}
                        pending_image_ids = [item_id for item_id in pending_image_ids if item_id not in native_ids]
                        if pending_image_ids:
                            _schedule_input_followup(
                                _run_qq_image_vision_followup(
                                    context=context,
                                    event=dict(event),
                                    attachment_ids=attachment_ids,
                                    attachments_registered=[
                                        item for item in attachments_registered if isinstance(item, dict)
                                    ],
                                    message_override=_qq_turn_message_override,
                                    action_note=_qq_action_note,
                                    plugin_input=_input_marker(plugin_inbound),
                                ), plugin_inbound,
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
            if optional_reply:
                turn_payload["turn_kind"] = "qq_optional_reply"
            if _qq_native_user_images:
                turn_payload["native_user_images"] = _qq_native_user_images
            if attachment_ids:
                # Bind relative selectors such as inspect_attachment(latest) to
                # evidence from this QQ event, rather than the shared group's
                # historical workspace.
                turn_payload["qq_current_attachment_ids"] = list(attachment_ids)
            turn_payload.update(_input_marker(plugin_inbound))
            turn_result = await _run_qq_turn_delivery(context=context, event=event, turn_payload=turn_payload)
            raw_turn_timing = turn_result.get("timing") if isinstance(turn_result, dict) else None
            if isinstance(raw_turn_timing, dict):
                turn_timing = {
                    str(key): round(float(value), 1)
                    for key, value in raw_turn_timing.items()
                    if isinstance(value, (int, float))
                }
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
            final_failure_notice_result = dict(
                turn_result.get("final_failure_notice_result")
                or {"ok": True, "status": "skipped", "reason": "missing_result"}
            )
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

        finally:
            plugin_inputs.close()

        duration_ms = (time.perf_counter() - started_at) * 1000
        delivery_status = str(send_result.get("status") or "")
        request_ok = bool(send_result.get("ok"))
        visibly_sent = request_ok and delivery_status not in {"queued", "suppressed", "stopped", "skipped"}
        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=request_ok)
        log_event(
            "qq_napcat_event",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
            reason=context.reason,
            sent=visibly_sent,
            delivery_status=delivery_status,
            model_status=str(turn_result.get("model_status") or ""),
            failure_notice_sent=bool(send_result.get("failure_notice_sent")),
            partial_delivery=bool(send_result.get("partial_delivery")),
            attachment_count=len(context.attachments or []),
            attachments_registered=len(attachments_registered),
            duration_ms=round(duration_ms, 1),
            turn_timing=turn_timing,
        )
        return JSONResponse(
            {
                "status": "ok" if request_ok else "send_failed",
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
                "final_failure_notice_result": final_failure_notice_result,
            }
        )

    return router
