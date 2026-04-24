from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_content_card(
    service: Any,
    *,
    title: str,
    output_format: str,
    task: str,
    structure: str,
    style: str,
    fidelity: str,
    sources: list[dict[str, Any]],
    content: str,
    table_rows: list[list[str]],
    formatting: dict[str, Any],
) -> dict[str, Any]:
    source_labels = [
        f"{source.get('handle')}《{source.get('title')}》"
        for source in sources[:8]
        if source.get("handle") or source.get("title")
    ]
    summary = f"{output_format.upper()} 文件《{title}》"
    if task:
        summary += f"，用途：{str(task).strip()[:120]}"
    if source_labels:
        summary += "，来源：" + "、".join(source_labels[:4])
    return {
        "summary": summary[:260],
        "task": str(task or "").strip()[:500],
        "structure": str(structure or "").strip()[:120],
        "style": str(style or "").strip()[:120],
        "fidelity": str(fidelity or "").strip()[:80],
        "sources": source_labels,
        "content_preview": content[:4000],
        "table_preview": table_rows[:8],
        "formatting": formatting,
    }


def build_style_content_card(
    service: Any,
    *,
    title: str,
    output_format: str,
    source: dict[str, Any],
    instruction: str,
    formatting: dict[str, Any],
) -> dict[str, Any]:
    source_label = f"{source.get('handle')}《{source.get('title')}》".strip()
    summary = f"{output_format.upper()} 样式加工《{title}》"
    if source_label:
        summary += f"，来源：{source_label}"
    if instruction:
        summary += f"，要求：{str(instruction).strip()[:100]}"
    return {
        "summary": summary[:260],
        "task": str(instruction or "").strip()[:500],
        "structure": "style_existing_file",
        "style": "formatting_only",
        "fidelity": "preserve_source_content",
        "sources": [source_label] if source_label else [],
        "content_preview": str(source.get("preview") or source.get("summary") or "").strip()[:4000],
        "table_preview": [],
        "formatting": formatting,
        "source_type": str(source.get("source_type") or "").strip(),
    }


def build_compose_followup(
    service: Any,
    *,
    generated: dict[str, Any],
    unresolved: list[str],
    send_to_user: bool,
) -> str:
    handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or "生成文件").strip()
    output_format = str(generated.get("output_format") or "").strip()
    path = str(generated.get("absolute_path") or "").strip()
    lines = [
        f"你刚刚已经生成文件 {handle}《{title}》（{output_format}）。",
        f"本地路径：{path}",
    ]
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把它发给用户；如果发送失败，你可以告诉用户文件已经生成但发送失败。")
    else:
        lines.append("这次只生成，不自动发送给用户。")
    if unresolved:
        lines.append(f"有些来源没有找到：{', '.join(unresolved[:5])}。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 compose_file。")
    return "\n".join(lines)


def build_revise_followup(
    service: Any,
    *,
    original: dict[str, Any],
    generated: dict[str, Any],
    send_to_user: bool,
) -> str:
    original_handle = str(original.get("generated_handle") or "").strip()
    new_handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or "修改版").strip()
    output_format = str(generated.get("output_format") or "").strip()
    path = str(generated.get("absolute_path") or "").strip()
    lines = [
        f"你刚刚已经基于 {original_handle} 生成了修改版 {new_handle}《{title}》（{output_format}）。",
        f"本地路径：{path}",
        "旧版本没有被覆盖，之后仍可回看或继续修改。",
    ]
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把修改版发给用户。")
    else:
        lines.append("这次只生成修改版，不自动发送给用户。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 revise_generated_file。")
    return "\n".join(lines)


def build_style_followup(
    service: Any,
    *,
    source: dict[str, Any],
    generated: dict[str, Any],
    send_to_user: bool,
) -> str:
    source_handle = str(source.get("handle") or "").strip()
    new_handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or "样式版").strip()
    output_format = str(generated.get("output_format") or "").strip()
    path = str(generated.get("absolute_path") or "").strip()
    lines = [
        f"你刚刚已经基于 {source_handle or '原文件'} 生成了样式加工版 {new_handle}《{title}》（{output_format}）。",
        f"本地路径：{path}",
        "这次只对已有文件做样式加工，尽量保留原内容，不需要你重新吐出全文。",
    ]
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把样式加工版发给用户。")
    else:
        lines.append("这次只生成样式加工版，不自动发送给用户。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 apply_style_to_existing_file。")
    return "\n".join(lines)


def build_media_conversion_followup(
    service: Any,
    *,
    generated: dict[str, Any],
    source: dict[str, Any],
    output_format: str,
    send_to_user: bool,
) -> str:
    source_handle = str(source.get("handle") or "").strip()
    new_handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or "转换音频").strip()
    path = str(generated.get("absolute_path") or "").strip()
    lines = [
        f"你刚刚已经把 {source_handle or '媒体文件'} 转换成 {new_handle}《{title}》（{output_format}）。",
        f"本地路径：{path}",
    ]
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把转换后的媒体文件发给用户。")
    else:
        lines.append("这次只生成转换后的媒体文件，不自动发送给用户。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 convert_media_file。")
    return "\n".join(lines)


def build_audio_separation_followup(
    service: Any,
    *,
    generated_files: list[dict[str, Any]],
    source: dict[str, Any],
    output_format: str,
    send_to_user: bool,
) -> str:
    source_handle = str(source.get("handle") or "").strip()
    source_title = str(source.get("title") or "媒体文件").strip()
    lines = [
        f"你刚刚已经把 {source_handle or '媒体文件'}《{source_title}》做成人声 / 伴奏分离。",
        f"输出格式：{output_format}。",
    ]
    for generated in generated_files:
        handle = str(generated.get("generated_handle") or "").strip()
        title = str(generated.get("output_title") or handle or "生成文件").strip()
        absolute_path = str(generated.get("absolute_path") or "").strip()
        size_label = _format_size_from_item(service, generated)
        card = generated.get("content_card") if isinstance(generated.get("content_card"), dict) else {}
        separation = card.get("separation") if isinstance(card.get("separation"), dict) else {}
        stem_role = str(separation.get("stem_role") or "").strip().lower()
        role_label = "人声" if stem_role == "vocals" else "伴奏" if stem_role == "instrumental" else (stem_role or "分离轨")
        lines.append(f"- {role_label}：{handle}《{title}》" + (f"，大小：{size_label}" if size_label else ""))
        if absolute_path:
            lines.append(f"  本地路径：{absolute_path}")
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把这些分离结果发给用户。")
    else:
        lines.append("这次只生成分离结果，不自动发送给用户。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 separate_audio_stems。")
    return "\n".join(lines)


def build_voice_clean_followup(
    service: Any,
    *,
    generated: dict[str, Any],
    source: dict[str, Any],
    mode: str,
    backend_used: str,
    send_to_user: bool,
) -> str:
    source_handle = str(source.get("handle") or "").strip()
    source_title = str(source.get("title") or "媒体文件").strip()
    handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or handle or "净化结果").strip()
    path = str(generated.get("absolute_path") or "").strip()
    size_label = _format_size_from_item(service, generated)
    mode_label = {
        "denoise": "降噪净化",
        "dereverb": "去混响净化",
        "deecho": "去回声净化",
        "voice_focus": "人声聚焦净化",
    }.get(mode, "净化")
    backend_label = "AI 净化（DeepFilterNet）" if backend_used == "deepfilternet" else "基础净化（ffmpeg）"
    lines = [
        f"你刚刚已经把 {source_handle or '媒体文件'}《{source_title}》做了{mode_label}。",
        f"输出结果：{handle}《{title}》。",
        f"本次后端：{backend_label}。",
    ]
    if size_label:
        lines.append(f"文件大小：{size_label}。")
    if path:
        lines.append(f"本地路径：{path}")
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把净化后的文件发给用户。")
    else:
        lines.append("这次只生成净化结果，不自动发送给用户。")
    lines.append("如果用户还要裁剪、转码、去头尾静音或统一采样率，请对这个新结果继续调用 convert_media_file。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 clean_voice_track。")
    return "\n".join(lines)


def build_voice_dataset_followup(
    service: Any,
    *,
    generated: dict[str, Any],
    manifest: dict[str, Any],
    send_to_user: bool,
) -> str:
    handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or "语音训练集").strip()
    path = str(generated.get("absolute_path") or "").strip()
    stats = manifest.get("stats") if isinstance(manifest.get("stats"), dict) else {}
    lines = [
        f"你刚刚已经生成语音训练素材批次 {handle}《{title}》（zip）。",
        f"共切出 {stats.get('slice_count') or 0} 段，合计 {service._format_duration_label(stats.get('total_duration_seconds')) or '0:00'}。",
        f"推荐片段 {stats.get('recommended_count') or 0} 段；带标记片段 {stats.get('flagged_count') or 0} 段。",
    ]
    source_stats = manifest.get("sources") if isinstance(manifest.get("sources"), list) else []
    if source_stats:
        lines.append("来源统计：")
        for item in source_stats[:8]:
            lines.append(f"- {item.get('handle') or item.get('title') or '来源'}：{item.get('slice_count') or 0} 段")
    issue_slices = manifest.get("issue_slices") if isinstance(manifest.get("issue_slices"), dict) else {}
    for flag, label in (
        ("too_short", "过短"),
        ("too_long", "过长"),
        ("low_volume", "音量偏低"),
        ("clipping", "可能爆音"),
        ("empty_or_failed", "疑似空片段"),
    ):
        items = issue_slices.get(flag) if isinstance(issue_slices.get(flag), list) else []
        if items:
            labels = [
                f"{item.get('filename')}({item.get('duration_seconds')}s)"
                for item in items[:8]
                if item.get("filename")
            ]
            if labels:
                lines.append(f"{label}片段：{', '.join(labels)}。")
    if path:
        lines.append(f"本地路径：{path}")
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把训练集 zip 发给用户。")
    else:
        lines.append("这次只生成训练集 zip，不自动发送给用户。")
    lines.append("请基于这个既成事实自然回应；如果用户要继续筛选片段，可以根据 manifest 里的文件名讨论。")
    lines.append("不要重复调用 prepare_voice_dataset。")
    return "\n".join(lines)


def build_voice_dataset_content_card(service: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    title = str(manifest.get("title") or "语音训练集").strip()
    stats = manifest.get("stats") if isinstance(manifest.get("stats"), dict) else {}
    profile = str(manifest.get("profile") or "gpt_sovits").strip()
    issue_slices = manifest.get("issue_slices") if isinstance(manifest.get("issue_slices"), dict) else {}
    preview_lines = [
        f"训练集批次：{title}",
        f"用途 preset：{profile}",
        f"共 {stats.get('slice_count') or 0} 段，合计 {service._format_duration_label(stats.get('total_duration_seconds')) or '0:00'}。",
        f"推荐片段：{stats.get('recommended_count') or 0}；带标记片段：{stats.get('flagged_count') or 0}。",
    ]
    sources = manifest.get("sources") if isinstance(manifest.get("sources"), list) else []
    if sources:
        preview_lines.append("来源统计：")
        for source in sources[:8]:
            preview_lines.append(f"- {source.get('handle') or source.get('title') or '来源'}：{source.get('slice_count') or 0} 段")
    for flag, label in (
        ("too_short", "过短"),
        ("too_long", "过长"),
        ("low_volume", "音量偏低"),
        ("clipping", "可能爆音"),
        ("empty_or_failed", "疑似空片段"),
    ):
        items = issue_slices.get(flag) if isinstance(issue_slices.get(flag), list) else []
        if items:
            names = [str(item.get("filename") or "").strip() for item in items[:12] if item.get("filename")]
            if names:
                preview_lines.append(f"{label}：{', '.join(names)}")
    summary = (
        f"语音训练素材批次《{title}》，"
        f"{stats.get('slice_count') or 0} 段，"
        f"推荐 {stats.get('recommended_count') or 0} 段，"
        f"带标记 {stats.get('flagged_count') or 0} 段。"
    )
    return {
        "type": "voice_dataset",
        "summary": summary[:260],
        "content_preview": "\n".join(preview_lines)[:4000],
        "voice_dataset": {
            "profile": profile,
            "stats": stats,
            "options": manifest.get("options") if isinstance(manifest.get("options"), dict) else {},
            "sources": sources[:12],
            "issue_slices": {key: value[:20] for key, value in issue_slices.items() if isinstance(value, list)},
            "slice_preview": list(manifest.get("slices") or [])[:24],
        },
    }


def build_transcript_content_card(
    service: Any,
    *,
    title: str,
    output_format: str,
    transcripts: list[dict[str, Any]],
    all_transcripts: list[dict[str, Any]],
    language: str,
    with_timestamps: bool,
    merge_outputs: bool,
    model_size: str,
    device: str,
    compute_type: str,
    content: str,
) -> dict[str, Any]:
    ready_count = len([item for item in all_transcripts if item.get("status") == "ready"])
    failed_count = len([item for item in all_transcripts if item.get("status") == "failed"])
    total_segments = sum(int(item.get("segment_count") or len(item.get("segments") or [])) for item in transcripts)
    total_duration = round(sum(float(item.get("duration_seconds") or 0) for item in transcripts), 3)
    sources = []
    for transcript in transcripts:
        source = transcript.get("source") if isinstance(transcript.get("source"), dict) else {}
        sources.append(
            {
                "handle": source.get("handle"),
                "title": source.get("title"),
                "segment_count": transcript.get("segment_count"),
                "duration_seconds": transcript.get("duration_seconds"),
                "language": transcript.get("language"),
            }
        )
    summary = (
        f"{output_format.upper()} 转写稿《{title}》，"
        f"{ready_count} 个来源成功，{failed_count} 个失败，"
        f"共 {total_segments} 段。"
    )
    return {
        "type": "media_transcript",
        "summary": summary[:260],
        "content_preview": content[:4000],
        "transcription": {
            "language": language or "auto",
            "with_timestamps": bool(with_timestamps),
            "merge_outputs": bool(merge_outputs),
            "model_size": model_size,
            "device": device,
            "compute_type": compute_type,
            "stats": {
                "ready_count": ready_count,
                "failed_count": failed_count,
                "segment_count": total_segments,
                "total_duration_seconds": total_duration,
            },
            "sources": sources[:20],
            "failed_sources": [
                {
                    "source": item.get("source"),
                    "error": item.get("error"),
                }
                for item in all_transcripts
                if item.get("status") == "failed"
            ][:10],
        },
    }


def build_transcribe_followup(
    service: Any,
    *,
    generated_files: list[dict[str, Any]],
    transcripts: list[dict[str, Any]],
    output_format: str,
    merge_outputs: bool,
    send_to_user: bool,
) -> str:
    ready = [item for item in transcripts if item.get("status") == "ready"]
    failed = [item for item in transcripts if item.get("status") == "failed"]
    total_segments = sum(int(item.get("segment_count") or len(item.get("segments") or [])) for item in ready)
    total_duration = round(sum(float(item.get("duration_seconds") or 0) for item in ready), 3)
    if merge_outputs:
        generated = generated_files[0] if generated_files else {}
        lines = [
            f"你刚刚已经生成合并转写稿 {generated.get('generated_handle') or ''}《{generated.get('output_title') or '转写稿'}》（{output_format}）。",
        ]
    else:
        lines = [f"你刚刚已经生成 {len(generated_files)} 份独立转写稿（{output_format}）。"]
        for generated in generated_files[:8]:
            lines.append(f"- {generated.get('generated_handle')}《{generated.get('output_title')}》")
    lines.append(f"成功来源 {len(ready)} 个，共 {total_segments} 段，合计 {service._format_duration_label(total_duration) or '0:00'}。")
    if failed:
        bits = []
        for item in failed[:5]:
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            bits.append(f"{source.get('handle') or source.get('title') or item.get('source_index')}：{item.get('error')}")
        lines.append("部分来源转写失败：" + "；".join(bits))
    if send_to_user:
        lines.append("当前客户端如果支持发送文件，系统会尝试把转写稿发给用户。")
    else:
        lines.append("这次只生成转写稿，不自动发送给用户。")
    lines.append("之后可以基于这些转写稿继续总结视频/音频内容、整理会议纪要、提取重点，或生成字幕文件。")
    lines.append("请基于这个既成事实自然回应，不要重复调用 transcribe_media。")
    return "\n".join(lines)


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
    lines.append("请基于这些实际规格自然回应用户；如果后续要转换、截取或压缩，可参考这些信息再调用 convert_media_file。")
    return "\n".join(lines)


def build_send_followup(service: Any, *, generated: dict[str, Any]) -> str:
    handle = str(generated.get("generated_handle") or "").strip()
    title = str(generated.get("output_title") or "生成文件").strip()
    output_format = str(generated.get("output_format") or "").strip()
    path = str(generated.get("absolute_path") or "").strip()
    size_label = _format_size_from_item(service, generated)
    lines = [f"你刚刚已经请求把生成文件 {handle}《{title}》（{output_format}）再次发送给用户。"]
    if size_label:
        lines.append(f"文件大小：{size_label}。")
    lines.extend(
        [
            f"本地路径：{path}",
            "当前客户端如果支持发送文件，系统会尝试上传它；如果发送失败，你可以告诉用户文件还在但发送失败。",
            "请基于这个既成事实自然回应，不要重复调用 send_generated_file。",
        ]
    )
    return "\n".join(lines)


def build_send_followup_batch(
    service: Any,
    *,
    generated_files: list[dict[str, Any]],
    unresolved: list[str],
    missing_on_disk: list[str],
    ambiguous_targets: list[str] | None = None,
) -> str:
    if len(generated_files) == 1:
        lines = [build_send_followup(service, generated=generated_files[0])]
    else:
        lines = [
            f"你刚刚已经请求把 {len(generated_files)} 份生成文件再次发送给用户。",
        ]
        for generated in generated_files[:8]:
            handle = str(generated.get("generated_handle") or "").strip()
            title = str(generated.get("output_title") or "生成文件").strip()
            output_format = str(generated.get("output_format") or "").strip()
            path = str(generated.get("absolute_path") or "").strip()
            size_label = _format_size_from_item(service, generated)
            suffix = f"，大小：{size_label}" if size_label else ""
            lines.append(f"- {handle}《{title}》（{output_format}{suffix}）")
            if path:
                lines.append(f"  本地路径：{path}")
        lines.append("当前客户端如果支持发送文件，系统会尝试依次上传这些文件。")
        lines.append("请基于这个既成事实自然回应，不要重复调用 send_generated_file。")
    if unresolved:
        lines.append(f"这些目标没有找到：{', '.join(unresolved[:8])}。")
    if ambiguous_targets:
        lines.append("这些目标不够明确，存在多个生成文件候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。")
    if missing_on_disk:
        lines.append(f"这些生成文件记录还在，但本地文件缺失：{', '.join(missing_on_disk[:8])}。")
    return "\n".join(lines)


def build_send_followup_missing(
    service: Any,
    *,
    requested_targets: list[str],
    unresolved: list[str],
    missing_on_disk: list[str],
    ambiguous_targets: list[str] | None = None,
) -> str:
    requested_label = ", ".join(requested_targets[:8]) or "最近生成文件"
    lines = [
        f"你刚刚想再次发送这些生成文件：{requested_label}，但这次没有成功定位到明确唯一的可发送文件。",
    ]
    if unresolved:
        lines.append(f"这些目标没有找到：{', '.join(unresolved[:8])}。")
    if ambiguous_targets:
        lines.append("这些目标不够明确，存在多个生成文件候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。")
    if missing_on_disk:
        lines.append(f"这些文件的记录还在，但本地文件已经不存在：{', '.join(missing_on_disk[:8])}。")
    lines.append("请自然告诉用户哪些文件没找到，或让用户明确指出想要哪一个，不要重复调用 send_generated_file。")
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
        lines = [f"你刚刚已经请求把 {service._sendable_file_label(file_ref)} 发送给用户。"]
        if size_label:
            lines.append(f"文件大小：{size_label}。")
        lines.extend(
            [
                f"本地路径：{file_ref.get('absolute_path') or ''}",
                "当前客户端如果支持发送文件，系统会尝试上传它；如果发送失败，你可以告诉用户文件还在但发送失败。",
                "请基于这个既成事实自然回应，不要重复调用 send_file。",
            ]
        )
    else:
        lines = [f"你刚刚已经请求把 {len(files)} 个已有文件发送给用户。"]
        for file_ref in files[:10]:
            size_label = _format_size_from_item(service, file_ref)
            suffix = f"，大小：{size_label}" if size_label else ""
            lines.append(f"- {service._sendable_file_label(file_ref)}{suffix}")
            path = str(file_ref.get("absolute_path") or "").strip()
            if path:
                lines.append(f"  本地路径：{path}")
        lines.append("当前客户端如果支持发送文件，系统会尝试依次上传这些文件。")
        lines.append("请基于这个既成事实自然回应，不要重复调用 send_file。")
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


def _render_media_info_lines(service: Any, media_info: dict[str, Any], *, prefix: str = "  ") -> list[str]:
    if not isinstance(media_info, dict) or not media_info:
        return []
    lines: list[str] = []
    format_name = str(media_info.get("format_name") or "").strip()
    duration = service._format_duration_label(media_info.get("duration_seconds"))
    file_size = service._format_file_size(media_info.get("file_size")) if isinstance(media_info.get("file_size"), int) else ""
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
        key
        for key in sorted(card.keys())
        if key not in {"summary", "content_preview", "table_preview", "formatting"}
    ]
    for key in extra_keys[:12]:
        value = card.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            lines.append(f"{key}: {str(value).strip()[:600]}")
        elif isinstance(value, (list, dict)) and value:
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            lines.append(f"{key}: {rendered[:1200]}")
    return "\n".join(lines).strip()


def render_generated_binary_inspection(service: Any, *, generated: dict[str, Any], path: Path, output_format: str) -> str:
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
    lines.append("如果这是音频/视频，请使用 inspect_media_info、transcribe_media、convert_media_file 等媒体工具继续处理。")
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
    lines.append("请基于这份检查结果自然回应，不要重复调用 inspect_generated_file。")
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
            lines.append(f"  处理信息：separation mode={mode or 'unknown'}，output={separation_output_format or 'unknown'}")
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
