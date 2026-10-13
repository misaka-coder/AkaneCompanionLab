"""QQ workspace command parsing and user-visible material summaries."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from companion_v01.channels.qq.commands.parser import resolve_qq_command
from companion_v01.channels.qq.formatting import _format_qq_timestamp, _qq_item_time_label
from companion_v01.workspace_management import list_workspace_files

QQ_WORKSPACE_KIND_LABELS = {
    "image": "图片",
    "document": "文件",
    "file": "文件",
    "audio": "音频",
    "video": "视频",
}






def _parse_qq_workspace_command(qq_gateway: Any, message: str) -> dict[str, Any] | None:
    command = resolve_qq_command(message)
    if command is None or command.error or command.definition.name != "材料":
        return None
    return dict(command.payload) if command.payload is not None else None


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
    lines.append("/材料 清理：收起收到的材料和生成结果")
    lines.append("/材料 清理 最近：收起最近收到的一个材料")
    lines.append("/材料 删除原文件：删除托管文件并清空生成结果内容")
    return "\n".join(lines)


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
