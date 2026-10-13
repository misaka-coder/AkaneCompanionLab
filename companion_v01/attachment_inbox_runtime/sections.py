"""Sections for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import re
import time
from typing import Any

from .definitions import SECTION_EXTRACTION_CHAR_LIMIT
from .section_content import SectionContent


class AttachmentSectionsMixin:
    @private_asset_scope
    def read_section(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "",
        section: str = "",
        kind: str = "any",
        max_chars: int = 12000,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        prepared = self._prepare_section_request(
            profile_user_id=profile_user_id, session_id=session_id,
            target=target, kind=kind, timestamp=timestamp,
        )
        if not prepared["ok"]:
            return prepared
        touched = prepared["item"]
        content = self._extract_section_content(
            touched, section=section,
            max_chars=max(500, min(SECTION_EXTRACTION_CHAR_LIMIT, int(max_chars or 12000))),
        )
        if not content:
            return self._section_unavailable(touched)
        label = self._compact_item_label(touched)
        section_text = str(section or "当前可用片段").strip()
        return {
            "ok": True,
            "item": touched,
            "content": content,
            "followup_context": (
                f"你刚刚展开读取了工作台材料 {label} 的「{section_text}」。\n"
                f"展开内容如下：\n{content}\n"
                "请基于这段展开内容自然回应；不要把材料全文默认写入长期记忆。"
            ),
        }

    def _prepare_section_request(
        self, *, profile_user_id: str, session_id: str, target: str, kind: str,
        timestamp: int | None,
    ) -> dict[str, Any]:
        resolution = self._resolve_target_result(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind=kind,
        )
        item = resolution.get("item") if isinstance(resolution, dict) else None
        if item is None:
            ambiguity = self._format_ambiguity_target(
                target=str(target or "").strip(),
                items=list(resolution.get("ambiguous_matches") or []) if isinstance(resolution, dict) else [],
            )
            return {
                "ok": False,
                "item": None,
                "content": "",
                "ambiguous_targets": [ambiguity] if ambiguity else [],
                "followup_context": (
                    "你刚刚想展开读取附件的一部分，但当前没有找到明确匹配的图片或文件。请自然向用户确认是哪一个。"
                    if not ambiguity
                    else (
                        "你刚刚想展开读取附件的一部分，但当前有多个候选，请让用户确认："
                        f"{ambiguity}。不要自己替用户决定。"
                    )
                ),
            }

        touched = (
            self.store.update_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=str(item.get("attachment_id") or ""),
                last_used_at=int(timestamp or time.time()),
                updated_at=int(timestamp or time.time()),
            )
            or item
        )
        return {"ok": True, "item": touched}

    def _section_unavailable(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "item": item,
            "content": "",
            "followup_context": (
                "你刚刚想展开读取附件内容，但这个附件目前没有可展开的文本预览。"
                "如果它是扫描 PDF、图片型文件或未支持格式，请自然告诉用户暂时只能看到摘要。"
            ),
        }

    def _extract_section_content(self, item: dict[str, Any], *, section: str, max_chars: int = 12000) -> str:
        return self._extract_section_result(item, section=section, max_chars=max_chars).text

    def _extract_section_result(self, item: dict[str, Any], *, section: str, max_chars: int) -> SectionContent:
        # Paging probes one extra character to distinguish a full section from
        # an exhausted extraction window. Legacy read_section retains its limit.
        limit = max(500, min(SECTION_EXTRACTION_CHAR_LIMIT + 1, int(max_chars or 12000)))
        original = self._extract_original_file_result(item, section=section, max_chars=limit)
        if original.text or original.reason in {"section_empty", "section_out_of_range", "source_text_unavailable"}:
            return original

        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        normalized_section = str(section or "").strip()
        sheets = detail.get("sheets")
        if isinstance(sheets, list) and sheets:
            sheet_content = self._extract_sheet_section(sheets, normalized_section)
            if sheet_content:
                return SectionContent(sheet_content[:limit], source="preview", truncated=True, reason=original.reason)

        tables = detail.get("tables")
        if (
            isinstance(tables, list)
            and tables
            and ("表" in normalized_section or "table" in normalized_section.lower())
        ):
            table_content = self._extract_table_section(tables, normalized_section)
            if table_content:
                return SectionContent(table_content[:limit], source="preview", truncated=True, reason=original.reason)

        preview = str(detail.get("text_preview") or detail.get("content_preview") or "").strip()
        if not preview:
            return original
        def preview_result(text: str) -> SectionContent:
            return SectionContent(
                text, source="preview", truncated=bool(detail.get("preview_is_truncated")),
                reason=original.reason, error_type=original.error_type,
            )
        lines = preview.splitlines()
        line_range = self._parse_line_range(normalized_section)
        if line_range is not None:
            start, end = line_range
            selected = lines[max(0, start - 1) : max(start, end)]
            return preview_result("\n".join(selected).strip()[:limit])

        paragraph_index = self._parse_section_index(normalized_section)
        paragraphs = [block.strip() for block in re.split(r"\n\s*\n", preview) if block.strip()]
        if paragraph_index is not None and paragraphs:
            if 1 <= paragraph_index <= len(paragraphs):
                return preview_result(paragraphs[paragraph_index - 1][:limit])
            return preview_result("")
        if normalized_section and normalized_section not in {"全文", "全部", "预览", "当前可用片段"}:
            lowered = normalized_section.lower()
            for index, line in enumerate(lines):
                if lowered in line.lower():
                    start = max(0, index - 2)
                    end = min(len(lines), index + 8)
                    return preview_result("\n".join(lines[start:end]).strip()[:limit])
        return preview_result(preview[:limit])

    def _extract_original_file_section(self, item: dict[str, Any], *, section: str, max_chars: int = 12000) -> str:
        return self._extract_original_file_result(item, section=section, max_chars=max_chars).text

    def _extract_original_file_result(self, item: dict[str, Any], *, section: str, max_chars: int) -> SectionContent:
        source_path = self._resolve_storage_path(item)
        if source_path is None:
            return SectionContent("", source="unavailable", reason="source_unavailable")
        suffix = source_path.suffix.lower()
        normalized_section = str(section or "").strip()
        try:
            if suffix in {
                ".txt",
                ".md",
                ".markdown",
                ".log",
                ".json",
                ".toml",
                ".yaml",
                ".yml",
                ".csv",
                ".ini",
                ".cfg",
                ".conf",
                ".py",
                ".js",
                ".ts",
                ".tsx",
                ".jsx",
                ".html",
                ".css",
                ".xml",
                ".sql",
                ".java",
                ".c",
                ".cpp",
                ".h",
                ".hpp",
                ".cs",
                ".go",
                ".rs",
            }:
                content = self._read_text_file_section(source_path, section=normalized_section, max_chars=max_chars)
                return SectionContent(content, reason="" if content else "section_empty")
            if suffix == ".docx":
                return SectionContent(self._read_docx_section(source_path, section=normalized_section, max_chars=max_chars))
            if suffix == ".xlsx":
                return self._read_xlsx_result(source_path, section=normalized_section, max_chars=max_chars)
            if suffix == ".pdf":
                return self._read_pdf_result(source_path, section=normalized_section, max_chars=max_chars)
        except Exception as exc:
            return SectionContent("", source="unavailable", reason="source_unreadable", error_type=type(exc).__name__)
        return SectionContent("", source="unavailable", reason="unsupported_format")
