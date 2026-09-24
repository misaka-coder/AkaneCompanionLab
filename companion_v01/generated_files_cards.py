from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def build_media_info_followup(
    service: Any,
    *,
    source: dict[str, Any],
    media_info: dict[str, Any],
) -> str:
    handle = str(source.get("handle") or "").strip()
    title = str(source.get("title") or "媒体文件").strip()
    duration = service._format_duration_label(media_info.get("duration_seconds"))
    format_name = str(media_info.get("format_name") or "").strip()
    size_label = service._format_file_size(media_info.get("file_size"))
    lines = [
        f"你刚刚已经读取了 {handle or '媒体文件'}《{title}》的媒体信息。",
        f"容器/格式：{format_name or '未知'}；时长：{duration or '未知'}；大小：{size_label or '未知'}。",
    ]
    audio = media_info.get("audio") if isinstance(media_info.get("audio"), dict) else {}
    if audio:
        audio_bits = [
            f"编码 {audio.get('codec') or '未知'}",
            f"{audio.get('sample_rate') or '未知'}Hz",
            f"{audio.get('channels') or '未知'} 声道",
        ]
        bitrate = service._format_bitrate(audio.get("bit_rate"))
        if bitrate:
            audio_bits.append(bitrate)
        lines.append("音频：" + "，".join(audio_bits) + "。")
    else:
        lines.append("音频：没有检测到音轨。")
    video = media_info.get("video") if isinstance(media_info.get("video"), dict) else {}
    if video:
        fps = video.get("fps")
        fps_label = f"，{fps:g}fps" if isinstance(fps, (int, float)) and fps > 0 else ""
        lines.append(
            "视频："
            f"编码 {video.get('codec') or '未知'}，"
            f"{video.get('width') or '?'}x{video.get('height') or '?'}{fps_label}。"
        )
    lines.append(
        "请基于这些实际规格自然回应用户，后续操作以当前实际可用工具为准。"
    )
    return "\n".join(lines)


def build_send_file_followup_batch(
    service: Any,
    *,
    files: list[dict[str, Any]],
    unresolved: list[str],
    missing_on_disk: list[str],
    ambiguous_targets: list[str] | None = None,
) -> str:
    if len(files) == 1:
        file_ref = files[0]
        size_label = _format_size_from_item(service, file_ref)
        lines = [f"{service._sendable_file_label(file_ref)} 已经进入客户端投递队列。"]
        if size_label:
            lines.append(f"文件大小：{size_label}。")
        lines.extend(
            [
                "此时还没有客户端最终投递回执。最终回复只能说正在尝试发送或已进入投递队列，不能说用户已经收到或发送成功。",
                "请等待客户端实际文件消息；不要重复调用 send_file。",
            ]
        )
    else:
        lines = [f"你刚刚已经请求把 {len(files)} 个已有文件发送给用户。"]
        for file_ref in files[:10]:
            size_label = _format_size_from_item(service, file_ref)
            suffix = f"，大小：{size_label}" if size_label else ""
            lines.append(f"- {service._sendable_file_label(file_ref)}{suffix}")
        lines.append("这些文件只进入了客户端投递队列，尚无最终回执；不能说用户已经收到或发送成功。")
        lines.append("请等待客户端实际文件消息，不要重复调用 send_file。")
    if unresolved:
        lines.append(f"这些目标没有找到：{', '.join(unresolved[:8])}。")
    if ambiguous_targets:
        lines.append("这些目标不够明确，存在多个候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。")
    if missing_on_disk:
        lines.append(f"这些文件记录还在，但本地文件缺失：{', '.join(missing_on_disk[:8])}。")
    return "\n".join(lines)


def build_send_file_followup_missing(
    service: Any,
    *,
    requested_targets: list[str],
    unresolved: list[str],
    missing_on_disk: list[str],
    ambiguous_targets: list[str] | None = None,
) -> str:
    requested_label = ", ".join(requested_targets[:8]) or "最近文件"
    lines = [
        f"你刚刚想发送这些已有文件：{requested_label}，但这次没有成功定位到明确唯一的可发送文件。",
    ]
    if unresolved:
        lines.append(f"这些目标没有找到：{', '.join(unresolved[:8])}。")
    if ambiguous_targets:
        lines.append("这些目标不够明确，存在多个候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。")
    if missing_on_disk:
        lines.append(f"这些文件记录还在，但本地文件已经不存在：{', '.join(missing_on_disk[:8])}。")
    if any("/" in item or "\\" in item for item in unresolved):
        lines.append("target/targets 用于文件句柄，不能把磁盘路径当句柄。已有文件请用 send_file(path=准确路径, cwd=实际项目目录)；"
                     "绝对路径省略 cwd。相对路径不会在插件进程目录中搜索。若插件只返回了无法定位的相对路径，"
                     "应修复导出工具返回 ManagedArtifactPayload，不要盲搜目录、重建内容或改发 latest。")
    else:
        lines.append("请自然告诉用户哪些文件没找到，或让用户明确指出想要哪一个，不要重复调用 send_file。")
    return "\n".join(lines)


def generated_display_name(item: dict[str, Any]) -> str:
    handle = str(item.get("generated_handle") or "").strip()
    title = str(item.get("output_title") or "").strip()
    if handle and title:
        return f"{handle}《{title}》"
    return handle or title or "生成文件"


def _format_size_from_item(service: Any, item: dict[str, Any]) -> str:
    size = item.get("file_size")
    if isinstance(size, int) and size > 0:
        return service._format_file_size(size)
    try:
        parsed = int(size or 0)
    except Exception:
        parsed = 0
    return service._format_file_size(parsed) if parsed > 0 else ""


def _format_time_anchor(value: Any) -> str:
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


def _render_media_info_lines(service: Any, media_info: dict[str, Any], *, prefix: str = "  ") -> list[str]:
    if not isinstance(media_info, dict) or not media_info:
        return []
    lines: list[str] = []
    format_name = str(media_info.get("format_name") or "").strip()
    duration = service._format_duration_label(media_info.get("duration_seconds"))
    file_size = (
        service._format_file_size(media_info.get("file_size")) if isinstance(media_info.get("file_size"), int) else ""
    )
    basics = []
    if format_name:
        basics.append(f"格式：{format_name}")
    if duration:
        basics.append(f"时长：{duration}")
    if file_size:
        basics.append(f"大小：{file_size}")
    if basics:
        lines.append(prefix + "媒体规格：" + "；".join(basics))
    audio = media_info.get("audio") if isinstance(media_info.get("audio"), dict) else {}
    if audio:
        audio_bits = [
            f"编码 {audio.get('codec') or '未知'}",
            f"{audio.get('sample_rate')}Hz" if audio.get("sample_rate") else "",
            f"{audio.get('channels')}声道" if audio.get("channels") else "",
        ]
        bitrate = service._format_bitrate(audio.get("bit_rate"))
        if bitrate:
            audio_bits.append(bitrate)
        lines.append(prefix + "音频：" + "，".join(bit for bit in audio_bits if bit) + "。")
    video = media_info.get("video") if isinstance(media_info.get("video"), dict) else {}
    if video:
        video_bits = [
            f"编码 {video.get('codec') or '未知'}",
            f"{video.get('width') or '?'}x{video.get('height') or '?'}",
        ]
        fps = video.get("fps")
        if isinstance(fps, (int, float)) and fps > 0:
            video_bits.append(f"{fps:g}fps")
        bitrate = service._format_bitrate(video.get("bit_rate"))
        if bitrate:
            video_bits.append(bitrate)
        lines.append(prefix + "视频：" + "，".join(bit for bit in video_bits if bit) + "。")
    return lines


def render_generated_summary_inspection(service: Any, *, generated: dict[str, Any], path: Path) -> str:
    card = generated.get("content_card") if isinstance(generated.get("content_card"), dict) else {}
    output_format = str(generated.get("output_format") or path.suffix.lstrip(".")).strip().lower()
    lines = [
        f"生成物：{generated_display_name(generated)}",
        f"格式：{output_format or 'unknown'}",
        f"状态：{generated.get('status') or 'unknown'}；发送：{generated.get('delivery_status') or 'unknown'}",
    ]
    size = generated.get("file_size")
    size_label = service._format_file_size(size) if isinstance(size, int) else ""
    if size_label:
        lines.append(f"大小：{size_label}")
    summary = str(generated.get("summary") or card.get("summary") or "").strip()
    if summary:
        lines.append(f"摘要：{summary}")
    preview = str(card.get("content_preview") or "").strip()
    if preview:
        lines.extend(["内容预览：", preview])
    extra_keys = [
        key for key in sorted(card.keys()) if key not in {"summary", "content_preview", "table_preview", "formatting"}
    ]
    for key in extra_keys[:12]:
        value = card.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            lines.append(f"{key}: {str(value).strip()[:600]}")
        elif isinstance(value, (list, dict)) and value:
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            lines.append(f"{key}: {rendered[:1200]}")
    return "\n".join(lines).strip()


def render_generated_binary_inspection(
    service: Any, *, generated: dict[str, Any], path: Path, output_format: str
) -> str:
    lines = [
        f"{generated_display_name(generated)} 是 {output_format or '二进制'} 文件，不能按文本直接展开。",
    ]
    if path.exists():
        lines.append(f"文件大小：{service._format_file_size(path.stat().st_size)}。")
    card = generated.get("content_card") if isinstance(generated.get("content_card"), dict) else {}
    summary = str(generated.get("summary") or card.get("summary") or "").strip()
    if summary:
        lines.append(f"摘要：{summary}")
    preview = str(card.get("content_preview") or "").strip()
    if preview:
        lines.extend(["可用内容卡片：", preview])
    lines.append(
        "如果还需要处理这些媒体，请将资源句柄交给当前实际可用的工具。"
    )
    return "\n".join(lines).strip()


def build_generated_inspection_followup(service: Any, *, generated: dict[str, Any], inspection: dict[str, Any]) -> str:
    section = str(inspection.get("section") or "content").strip()
    content = str(inspection.get("content") or "").strip()
    lines = [
        f"你刚刚查看了 {generated_display_name(generated)} 的 {section}。",
    ]
    if inspection.get("truncated"):
        lines.append("注意：下面内容不是完整全文，已经按本次预算截断。")
    if content:
        lines.extend(["检查结果：", content])
    else:
        lines.append("检查结果为空。")
    lines.append("请根据已读取内容回应；未展示的部分不能当成已读证据。")
    return "\n".join(lines)


def render_generated_prompt_item(service: Any, item: dict[str, Any]) -> list[str]:
    handle = str(item.get("generated_handle") or item.get("generated_id") or "").strip()
    title = str(item.get("output_title") or "生成文件").strip()
    output_format = str(item.get("output_format") or "").strip()
    status = str(item.get("status") or "").strip()
    delivery = str(item.get("delivery_status") or "").strip()
    card = item.get("content_card") if isinstance(item.get("content_card"), dict) else {}
    summary = str(item.get("summary") or card.get("summary") or "").strip()
    size_label = _format_size_from_item(service, item)
    created_by = str(item.get("created_by_tool") or "").strip()
    meta_parts = [f"状态：{status}", f"发送：{delivery}"]
    created_label = _format_time_anchor(item.get("created_at"))
    updated_label = _format_time_anchor(item.get("updated_at"))
    if created_label:
        meta_parts.append(f"生成：{created_label}")
    if updated_label and updated_label != created_label:
        meta_parts.append(f"更新：{updated_label}")
    if size_label:
        meta_parts.append(f"大小：{size_label}")
    if created_by:
        meta_parts.append(f"来源工具：{created_by}")
    lines = [f"- {handle}：{title}.{output_format}（{'，'.join(meta_parts)}）"]
    if summary:
        lines.append(f"  摘要：{summary[:240]}")
    source = card.get("source") if isinstance(card.get("source"), dict) else {}
    if source:
        source_handle = str(source.get("handle") or "").strip()
        source_title = str(source.get("title") or "").strip()
        if source_handle or source_title:
            lines.append(f"  来源：{source_handle}《{source_title}》")
    separation = card.get("separation") if isinstance(card.get("separation"), dict) else {}
    if separation:
        stem_role = str(separation.get("stem_role") or "").strip()
        if stem_role:
            lines.append(f"  音轨角色：{stem_role}")
        separation_output_format = str(separation.get("output_format") or "").strip()
        mode = str(separation.get("mode") or "").strip()
        if mode or separation_output_format:
            lines.append(
                f"  处理信息：separation mode={mode or 'unknown'}，output={separation_output_format or 'unknown'}"
            )
    voice_cleaning = card.get("voice_cleaning") if isinstance(card.get("voice_cleaning"), dict) else {}
    if voice_cleaning:
        mode = str(voice_cleaning.get("mode") or "").strip()
        backend = str(voice_cleaning.get("backend_used") or "").strip()
        if mode or backend:
            lines.append(f"  净化信息：mode={mode or 'unknown'}，backend={backend or 'unknown'}")
    conversion = card.get("conversion") if isinstance(card.get("conversion"), dict) else {}
    if conversion:
        bits = []
        if conversion.get("output_format"):
            bits.append(f"output={conversion.get('output_format')}")
        if conversion.get("bitrate"):
            bits.append(f"bitrate={conversion.get('bitrate')}")
        if conversion.get("sample_rate"):
            bits.append(f"sample_rate={conversion.get('sample_rate')}")
        if conversion.get("channels"):
            bits.append(f"channels={conversion.get('channels')}")
        if conversion.get("start_seconds") or conversion.get("end_seconds"):
            bits.append(f"range={conversion.get('start_seconds')}-{conversion.get('end_seconds')}")
        if bits:
            lines.append("  转换信息：" + "，".join(str(bit) for bit in bits))
    media_info = card.get("media_info") if isinstance(card.get("media_info"), dict) else {}
    if media_info:
        lines.extend(_render_media_info_lines(service, media_info))
    preview = str(card.get("content_preview") or "").strip()
    if preview:
        lines.append(f"  内容预览：{preview[:320]}")
    return lines


def build_prompt_context(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    limit: int = 3,
) -> str:
    items = service.store.list_generated_files(
        profile_user_id=profile_user_id,
        session_id=session_id,
        statuses=["ready", "failed"],
        limit=max(1, int(limit or 3)),
    )
    if not items:
        return ""
    lines = [
        "【生成文件工作台】",
        "这些是你刚生成或整理出的文件；它们不是用户原始附件，可以继续修改、另存为新版本或发送给用户。",
    ]
    for item in items:
        lines.extend(render_generated_prompt_item(service, item))
    lines.append("如果用户要继续改刚生成的文件，优先引用 gen_001 这类 handle；不要覆盖旧版本。")
    return "\n".join(lines)
