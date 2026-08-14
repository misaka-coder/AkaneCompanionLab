"""Shared producer-owned paging contract for long tool results.

Long tool results are handed to the model as complete logical pages plus an
opaque continuation cursor.  This module owns the shared wire format and the
line-boundary page slicer; each tool handler owns its source identity,
fingerprint and page budgets.

Cursor wire format::

    p1.<tool>.<tag>.<payload_hex>

- ``p1``            cursor schema version (frozen once it enters MemCore history)
- ``<tool>``        short tool tag, e.g. ``ws`` (read_workspace), ``wl`` (list_workspace),
                    ``we`` (web_search extract/search), ``bp`` (browser_page snapshot),
                    ``at`` (read_attachment_section), ``gf`` (inspect_generated_file)
- ``<tag>``         sha256(binding material)[:16]; the binding contains tool + owner
                    (profile/session) + source identity + fingerprint, so a cursor
                    cannot be replayed across sessions or against changed content
- ``<payload_hex>`` hex-encoded ``"file_index:offset"`` read position

Cursors are opaque read positions, not resource IDs, and never contain secrets,
absolute host paths, credentials, or page bodies.
"""

from __future__ import annotations

import hashlib
from typing import Any

CURSOR_VERSION = "p1"
CURSOR_BINDING_SEPARATOR = "\x1f"

FIRST_PAGE_BUDGET_CHARS = 50_000
FIRST_PAGE_BUDGET_LINES = 2_000
NEXT_PAGE_BUDGET_CHARS = 32_000
NEXT_PAGE_BUDGET_LINES = 1_000
ENTRY_PAGE_BUDGET_CHARS = 16_000
MIN_PAGE_BUDGET_CHARS = 500


def cursor_binding(*parts: Any) -> str:
    """Deterministic binding material for one source + owner + fingerprint."""
    return CURSOR_BINDING_SEPARATOR.join(str(part or "") for part in parts)


def cursor_tag(binding: str) -> str:
    return hashlib.sha256(str(binding or "").encode("utf-8")).hexdigest()[:16]


def make_paged_cursor(*, tool: str, binding: str, payload: str) -> str:
    """Build an opaque versioned cursor bound to tool + binding material."""
    clean_tool = str(tool or "").strip()
    if not clean_tool or CURSOR_BINDING_SEPARATOR in clean_tool or "." in clean_tool:
        raise ValueError("invalid_cursor_tool")
    return f"{CURSOR_VERSION}.{clean_tool}.{cursor_tag(binding)}.{str(payload or '').encode('utf-8').hex()}"


def parse_paged_cursor(value: Any, *, tool: str, binding: str) -> str | None:
    """Decode a cursor; return its payload only when tool + binding match.

    Returns ``None`` for garbage, wrong version, wrong tool, wrong owner or
    changed fingerprint.  Callers that need to distinguish *why* decode in two
    passes: first with an owner-only binding to detect ``cursor_owner_mismatch``,
    then with the full fingerprint binding to detect ``stale_cursor``.
    """
    text = str(value or "").strip()
    parts = text.split(".")
    if len(parts) != 4 or parts[0] != CURSOR_VERSION or parts[1] != str(tool or "").strip():
        return None
    if parts[2] != cursor_tag(binding):
        return None
    try:
        payload = bytes.fromhex(parts[3]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return payload


def parse_file_offset_payload(payload: str) -> tuple[int, int]:
    """Parse ``file_index:offset`` payload; invalid values become (0, 0)."""
    try:
        file_index_text, offset_text = str(payload or "").split(":", 1)
        file_index = int(file_index_text)
        offset = int(offset_text)
    except (ValueError, AttributeError):
        return (0, 0)
    return (max(0, file_index), max(0, offset))


def file_offset_payload(*, file_index: int, offset: int) -> str:
    return f"{max(0, int(file_index or 0))}:{max(0, int(offset or 0))}"


def offset_payload(*, offset: int) -> str:
    return file_offset_payload(file_index=0, offset=offset)


def json_payload(value: Any) -> str:
    """JSON-encode a payload with stable, compact serialization."""
    import json as _json

    return _json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json_payload(payload: str) -> Any | None:
    import json as _json

    try:
        return _json.loads(str(payload or ""))
    except (ValueError, TypeError):
        return None


def slice_page(
    text: str,
    *,
    start: int = 0,
    budget_chars: int = FIRST_PAGE_BUDGET_CHARS,
    budget_lines: int = FIRST_PAGE_BUDGET_LINES,
) -> tuple[str, int, int]:
    """Return one page of ``text`` as ``(page, next_offset, total_chars)``.

    Cuts at a line boundary whenever a newline exists in the budget window;
    otherwise falls back to a char cut.  Offsets are Python string indices
    (code-point safe: a UTF-8 sequence is never split).  Calling with
    ``start == next_offset`` until ``next_offset == total_chars`` reproduces
    the whole text with no loss and no overlap.
    """
    total = len(text)
    if start >= total:
        return "", total, total
    start = max(0, start)
    char_end = min(total, start + max(MIN_PAGE_BUDGET_CHARS, int(budget_chars or 0)))
    segment = text[start:char_end]
    # When the entire remainder fits, returning all of it is both lossless and
    # complete.  Cutting back to the last newline here would manufacture an
    # unnecessary continuation for a short final line.
    if char_end >= total:
        return segment, total, total
    newline_count = segment.count("\n")
    if newline_count >= max(1, int(budget_lines or 1)):
        target = max(1, int(budget_lines or 1))
        position = -1
        seen = 0
        while seen < target:
            position = segment.find("\n", position + 1)
            if position < 0:
                break
            seen += 1
        end = start + position + 1 if position >= 0 else char_end
    else:
        last_newline = segment.rfind("\n")
        if last_newline >= 0 and last_newline + 1 >= len(segment) * 0.6:
            end = start + last_newline + 1
        else:
            end = char_end
    return text[start:end], end, total


def page_progress_lines(
    *,
    shown_lines: int,
    shown_chars: int,
    total_chars: int,
    next_call_hint: str,
    page_label: str = "",
) -> list[str]:
    """Standard model-visible trailing block for an incomplete page.

    Says explicitly that answering now is fine and only a real need for the
    remaining content justifies continuing with the cursor.
    """
    label = f"（{page_label}）" if page_label else ""
    lines = [
        f"本页{label}之后仍有内容：已展示 {shown_lines} 行 / {shown_chars} 字，"
        f"来源共 {total_chars} 字，剩余尚未展示。",
        "如果这些内容已经足够回答，可以直接回答；只有确实需要后续正文时，才调用：",
        next_call_hint,
    ]
    return lines


def page_complete_lines(*, shown_lines: int, shown_chars: int, total_chars: int) -> list[str]:
    return [f"已完整读取来源正文：共 {total_chars} 字、{shown_lines} 行（本次展示 {shown_chars} 字）。"]


def page_failure_feedback(*, status: str, tool: str, detail: str = "") -> str:
    """Model-visible structured feedback for one paging failure status."""
    clean_status = str(status or "cursor_invalid")
    clean_tool = str(tool or "tool").strip() or "tool"
    detail_suffix = f"（{str(detail or '').strip()}）" if str(detail or "").strip() else ""
    return (
        f"本次 {clean_tool} 续读没有执行{detail_suffix}：状态={clean_status}。"
        "之前的页面内容仍然有效；不要声称已经读到未返回的后续内容，"
        "也不要假装内容没有变化。需要时重新发起一次完整读取。"
    )
