"""Section selection for AttachmentInboxService."""

from __future__ import annotations

import re
from typing import Any


class AttachmentSectionSelectionMixin:
    def _slice_plain_text_section(self, text: str, *, section: str, max_chars: int) -> str:
        normalized = str(section or "").strip()
        if normalized and normalized not in {"全文", "全部", "预览", "当前可用片段"}:
            line_range = self._parse_line_range(normalized)
            if line_range is not None:
                start, end = line_range
                lines = text.splitlines()
                return "\n".join(lines[max(0, start - 1) : max(start, end)]).strip()[:max_chars]

            paragraph_index = self._parse_section_index(normalized)
            paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
            if paragraph_index is not None and paragraphs:
                if 1 <= paragraph_index <= len(paragraphs):
                    return paragraphs[paragraph_index - 1][:max_chars]
                return ""

            lowered = normalized.lower()
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if lowered in line.lower():
                    start = max(0, index - 6)
                    end = min(len(lines), index + 20)
                    return "\n".join(lines[start:end]).strip()[:max_chars]
        return text.strip()[:max_chars]

    def _select_sheet_name(self, sheet_names: list[str], section: str) -> str:
        lowered = str(section or "").strip().lower()
        matches = [name for name in sheet_names if name.lower() in lowered]
        if matches:
            return max(matches, key=len)
        index = self._parse_section_index(section)
        if index is not None and 1 <= index <= len(sheet_names):
            return sheet_names[index - 1]
        return sheet_names[0]

    def _parse_page_range(self, section: str) -> tuple[int, int] | None:
        text = str(section or "").strip()
        match = re.search(
            r"(?:第\s*)?(\d+)\s*(?:页|page)?\s*(?:-|到|至|~)\s*(?:第\s*)?(\d+)\s*(?:页|page)?", text, re.IGNORECASE
        )
        if match:
            start = int(match.group(1))
            end = int(match.group(2))
            if start > end:
                start, end = end, start
            return max(1, start), max(1, end)
        match = re.search(r"(?:第\s*)?(\d+)\s*(?:页|page)", text, re.IGNORECASE)
        if match:
            page = int(match.group(1))
            return page, page
        return None

    def _cell_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _extract_sheet_section(self, sheets: list[Any], section: str) -> str:
        selected = None
        index = self._parse_section_index(section)
        lowered = str(section or "").strip().lower()
        if index is not None and 1 <= index <= len(sheets):
            selected = sheets[index - 1]
        if selected is None and lowered:
            for sheet in sheets:
                if not isinstance(sheet, dict):
                    continue
                name = str(sheet.get("name") or "").strip()
                if name and name.lower() in lowered:
                    selected = sheet
                    break
        if selected is None:
            selected = sheets[0]
        if not isinstance(selected, dict):
            return ""
        rows = selected.get("preview_rows") if isinstance(selected.get("preview_rows"), list) else []
        line_range = self._parse_line_range(section)
        if line_range is not None:
            start, end = line_range
            rows = rows[max(0, start - 1):end]
            if not rows:
                return ""
        else:
            rows = rows[:12]
        output = [f"[{selected.get('name') or 'Sheet'}]"]
        for row in rows:
            if isinstance(row, list):
                output.append(" | ".join(str(cell) for cell in row if str(cell).strip()))
        return "\n".join(output).strip()

    def _extract_table_section(self, tables: list[Any], section: str) -> str:
        index = self._parse_section_index(section) or 1
        if not (1 <= index <= len(tables)):
            return ""
        table = tables[index - 1]
        if not isinstance(table, dict):
            return ""
        rows = table.get("preview_rows") if isinstance(table.get("preview_rows"), list) else []
        output = [f"[表格 {index}] 行数约 {table.get('row_count') or '?'}，列数约 {table.get('column_count') or '?'}"]
        for row in rows[:12]:
            if isinstance(row, list):
                output.append(" | ".join(str(cell) for cell in row if str(cell).strip()))
        return "\n".join(output).strip()

    def _parse_line_range(self, section: str) -> tuple[int, int] | None:
        text = str(section or "").strip()
        match = re.search(
            r"(?:第\s*)?(\d+)\s*(?:行|line)?\s*(?:-|到|至|~)\s*(?:第\s*)?(\d+)\s*(?:行|line)?", text, re.IGNORECASE
        )
        if match:
            start = int(match.group(1))
            end = int(match.group(2))
            if start > end:
                start, end = end, start
            return max(1, start), max(1, end)
        match = re.search(r"(?:第\s*)?(\d+)\s*(?:行|line)", text, re.IGNORECASE)
        if match:
            start = int(match.group(1))
            return start, min(start + 20, start + 80)
        return None

    def _parse_section_index(self, section: str) -> int | None:
        text = str(section or "").strip()
        match = re.search(
            r"(?:第\s*)?(\d+)\s*(?:页|段|节|章|个|张|表|sheet|page|paragraph|section)?", text, re.IGNORECASE
        )
        if match:
            return int(match.group(1))
        return self._parse_chinese_number(text)
