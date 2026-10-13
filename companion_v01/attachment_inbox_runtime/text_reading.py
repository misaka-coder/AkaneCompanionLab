"""Text reading for AttachmentInboxService."""

from __future__ import annotations

import csv
import codecs
import io
import re
from pathlib import Path


_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk")
_READ_CHUNK_CHARS = 64 * 1024
_LINE_BREAK = re.compile(r"[\n\v\f\x1c-\x1e\x85\u2028\u2029]")


def _read_prefix(path: Path, max_chars: int) -> str:
    byte_limit = max(256 * 1024, max_chars * 4)
    with path.open("rb") as handle:
        payload = handle.read(byte_limit)
        at_eof = len(payload) >= path.stat().st_size
    for encoding in _TEXT_ENCODINGS:
        try:
            # A byte budget can end within a valid multi-byte character. Keep
            # that pending suffix out of the preview instead of misdetecting GBK.
            return codecs.getincrementaldecoder(encoding)().decode(payload, final=at_eof)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _read_lines(path: Path, start: int, end: int, max_chars: int, encoding: str, errors: str) -> str:
    parts: list[str] = []
    used = 0
    line_no = 1
    with path.open("r", encoding=encoding, errors=errors, newline=None) as handle:
        while chunk := handle.read(_READ_CHUNK_CHARS):
            offset = 0
            for match in _LINE_BREAK.finditer(chunk):
                stop = match.end()
                if start <= line_no <= end:
                    piece = chunk[offset:match.start()] + "\n"
                    parts.append(piece[:max_chars - used])
                    used += min(len(piece), max_chars - used)
                line_no += 1
                offset = stop
                if line_no > end or used >= max_chars:
                    return "".join(parts).strip()[:max_chars]
            if start <= line_no <= end:
                piece = chunk[offset:]
                parts.append(piece[:max_chars - used])
                used += min(len(piece), max_chars - used)
                if used >= max_chars:
                    break
    return "".join(parts).strip()[:max_chars]


def _read_csv_rows(path: Path, start: int, end: int, max_chars: int, encoding: str, errors: str) -> str:
    rows: list[str] = []
    used = 0
    with path.open("r", encoding=encoding, errors=errors, newline="") as handle:
        for index, row in enumerate(csv.reader(handle), start=1):
            if index < start:
                continue
            if index > end:
                break
            text = " | ".join(str(cell).strip() for cell in row)
            rows.append(text[:max_chars - used])
            used += len(rows[-1]) + 1
            if used >= max_chars:
                break
    return "\n".join(rows).strip()[:max_chars]


class AttachmentTextReadingMixin:
    def _read_text_file_section(self, path: Path, *, section: str, max_chars: int) -> str:
        line_range = self._parse_line_range(section)
        if line_range is not None:
            start, end = line_range
            start, end = max(1, start), max(1, end)
            reader = _read_csv_rows if path.suffix.lower() == ".csv" else _read_lines
            for encoding in _TEXT_ENCODINGS:
                try:
                    return reader(path, start, end, max_chars, encoding, "strict")
                except UnicodeDecodeError:
                    continue
            return reader(path, start, end, max_chars, "utf-8", "replace")
        text = _read_prefix(path, max_chars)
        if path.suffix.lower() == ".csv":
            text = self._render_csv_text(text)
        return self._slice_plain_text_section(text, section=section, max_chars=max_chars)

    def _render_csv_text(self, text: str) -> str:
        rows: list[str] = []
        try:
            reader = csv.reader(io.StringIO(text, newline=""))
            for row in reader:
                rows.append(" | ".join(str(cell).strip() for cell in row))
        except Exception:
            return text
        return "\n".join(rows)
