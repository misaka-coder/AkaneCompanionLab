"""File-card routing, bounded text previews and structured text summaries."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
import config
from .definitions import (
    DOCUMENT_SUFFIXES,
    MEDIA_SUFFIXES,
    TEXT_SUFFIXES,
)


def _text_preview_byte_limit() -> int:
    """Use the same byte budget for reading and preview completeness."""
    return int(getattr(config, 'QQ_TEXT_ATTACHMENT_MAX_READ_BYTES', 256 * 1024) or 256 * 1024)


class AttachmentFileCardsMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _build_file_card(self, *, source_path: Path, item: dict[str, Any], mime_type: str) -> dict[str, Any]:
        origin_name = self._clean_filename(item.get("origin_name") or source_path.name)
        title = origin_name or source_path.name
        suffix = source_path.suffix.lower()
        file_size = source_path.stat().st_size if source_path.exists() else 0
        attachment_id = str(item.get("attachment_id") or "").strip()
        base_detail: dict[str, Any] = {}
        if attachment_id:
            base_detail["attachment_id"] = attachment_id
        if str(item.get("kind") or "") == "image":
            return {
                "summary_title": title,
                "short_hint": "图片文件已接收；本次未进行视觉分析，不代表已看过图片内容。",
                "detail": {
                    **base_detail,
                    "summary": "图片文件已接收，未分析像素内容。",
                    "file_kind": "image",
                    "mime_type": mime_type,
                    "file_size": file_size,
                    "vision_status": "not_requested",
                },
            }
        if suffix in DOCUMENT_SUFFIXES:
            card = self._build_document_card(
                source_path=source_path,
                title=title,
                suffix=suffix,
                mime_type=mime_type,
                file_size=file_size,
            )
            card["detail"] = {**base_detail, **card.get("detail", {})}
            return card
        if suffix in MEDIA_SUFFIXES or mime_type.startswith(("audio/", "video/")):
            card = self._build_media_card(
                source_path=source_path,
                title=title,
                suffix=suffix,
                mime_type=mime_type,
                file_size=file_size,
            )
            card["detail"] = {**base_detail, **card.get("detail", {})}
            return card
        if suffix not in TEXT_SUFFIXES:
            return {
                "summary_title": title,
                "short_hint": f"一个 {suffix or '未知类型'} 文件，约 {file_size} bytes；当前只保留文件名和基本信息，暂未解析内容。",
                "detail": {
                    **base_detail,
                    "summary": "暂不支持直接解析该文件内容。",
                    "file_kind": suffix.lstrip(".") or "binary",
                    "mime_type": mime_type,
                    "file_size": file_size,
                    "text_preview": "",
                },
            }

        text, encoding = self._read_text_preview(source_path)
        lines = text.splitlines()
        preview = text[:4000]
        detail: dict[str, Any] = {
            **base_detail,
            "summary": f"文本文件 {title}，约 {len(text)} 字符，{len(lines)} 行。",
            "file_kind": self._file_kind_from_suffix(suffix),
            "mime_type": mime_type,
            "encoding": encoding,
            "file_size": file_size,
            "line_count": len(lines),
            "text_preview": preview,
            "preview_is_truncated": file_size > _text_preview_byte_limit() or len(text) > len(preview),
        }
        self._enrich_structured_text_detail(detail=detail, text=text, suffix=suffix)
        hint_parts = [detail["summary"]]
        if detail.get("headings"):
            hint_parts.append("标题：" + "、".join(list(detail["headings"])[:4]))
        if detail.get("columns"):
            hint_parts.append("列：" + "、".join(list(detail["columns"])[:6]))
        if detail.get("top_level_keys"):
            hint_parts.append("字段：" + "、".join(list(detail["top_level_keys"])[:6]))
        return {
            "summary_title": title,
            "short_hint": " ".join(hint_parts)[:240],
            "detail": detail,
        }

    def _read_text_preview(self, path: Path) -> tuple[str, str]:
        max_bytes = _text_preview_byte_limit()
        with path.open("rb") as handle:
            payload = handle.read(max_bytes)
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                return payload.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace"), "utf-8-replace"

    def _enrich_structured_text_detail(self, *, detail: dict[str, Any], text: str, suffix: str) -> None:
        if suffix in {".md", ".markdown"}:
            headings = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    heading = stripped.lstrip("#").strip()
                    if heading:
                        headings.append(heading[:80])
                if len(headings) >= 8:
                    break
            if headings:
                detail["headings"] = headings
            return

        if suffix == ".json":
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    detail["top_level_keys"] = [str(key)[:60] for key in list(payload.keys())[:12]]
                elif isinstance(payload, list):
                    detail["summary"] += f" 顶层是数组，约 {len(payload)} 项。"
            except Exception:
                return
            return

        if suffix == ".csv":
            try:
                rows = list(csv.reader(text.splitlines()[:3]))
                if rows:
                    detail["columns"] = [str(item).strip()[:60] for item in rows[0] if str(item).strip()][:20]
            except Exception:
                return
