"""Owner-bound paging of verified workspace file text."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .definitions import WorkspacePathError


class WorkspaceFilePagingMixin:
    def read_items_paged(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Read workspace files in complete logical pages with a continuation cursor.

        First page renders up to ``FIRST_PAGE_BUDGET_CHARS`` / ``FIRST_PAGE_BUDGET_LINES``;
        continuations render ``NEXT_PAGE_BUDGET_CHARS`` / ``NEXT_PAGE_BUDGET_LINES``.
        A page never splits a UTF-8 character or a text line, and multi-file reads
        pack complete file blocks into one page while budget remains.

        The cursor is stateless: its payload carries the workspace:/ target list,
        the current file index, the char offset and a content fingerprint
        (sha256 of the extracted text). Continuations stream-verify the live
        source and reuse the extraction while it is unchanged. An expired or
        evicted snapshot is recreated and checked against the same fingerprint.
        """
        from .. import paged_reading

        owner_binding = paged_reading.cursor_binding("read_workspace", profile_user_id, session_id)
        requested_targets = self._normalize_target_list(targets)
        file_index = 0
        offset = 0
        expected_fingerprint = ""
        payload = (
            paged_reading.parse_json_payload(
                paged_reading.parse_paged_cursor(cursor, tool="ws", binding=owner_binding)
            )
            if cursor
            else None
        )

        # The owner check runs first so a foreign session cursor is rejected
        # before any file access; the payload then supplies the target list.
        if cursor and payload is None:
            return self._paged_failure(
                "cursor_invalid", reason="cursor could not be decoded for this owner/session"
            )
        if payload is not None:
            if not isinstance(payload, dict):
                return self._paged_failure("cursor_invalid", reason="cursor payload is not a page record")
            embedded_targets = payload.get("t")
            if not isinstance(embedded_targets, list) or not embedded_targets:
                return self._paged_failure("cursor_invalid", reason="cursor payload has no target list")
            requested_targets = self._normalize_target_list([str(item or "") for item in embedded_targets])
            try:
                file_index = max(0, int(payload.get("i") or 0))
            except (TypeError, ValueError):
                return self._paged_failure("cursor_invalid", reason="cursor file index is invalid")
            try:
                offset = max(0, int(payload.get("o") or 0))
            except (TypeError, ValueError):
                return self._paged_failure("cursor_invalid", reason="cursor offset is invalid")
            expected_fingerprint = str(payload.get("f") or "")

        if not requested_targets:
            return self._paged_failure("cursor_invalid", reason="no workspace targets")

        resolved: list[tuple[Path, str]] = []
        for requested in requested_targets:
            try:
                path, uri = self.resolve_uri(requested)
            except WorkspacePathError as exc:
                return self._paged_failure("cursor_invalid", reason=str(exc), detail_requested=requested)
            if not path.exists():
                return self._paged_failure(
                    "source_missing", reason="workspace item does not exist", detail_requested=uri
                )
            if not path.is_file():
                return self._paged_failure("not_file", reason="target is not a file", detail_requested=uri)
            resolved.append((path, uri))

        if file_index >= len(resolved):
            return self._paged_failure("cursor_invalid", reason="cursor file index is out of range")

        state_map = self._state_map(profile_user_id=profile_user_id, session_id=session_id)
        page_items: list[dict[str, Any]] = []
        shown_chars = 0
        shown_lines = 0
        extraction_capped = bool(payload.get('c')) if isinstance(payload, dict) else False
        current_fingerprint = ""
        budget_chars, budget_lines = (
            (paged_reading.FIRST_PAGE_BUDGET_CHARS, paged_reading.FIRST_PAGE_BUDGET_LINES)
            if not cursor
            else (paged_reading.NEXT_PAGE_BUDGET_CHARS, paged_reading.NEXT_PAGE_BUDGET_LINES)
        )

        while file_index < len(resolved):
            path, uri = resolved[file_index]
            remaining = budget_chars - shown_chars
            remaining_lines = budget_lines - shown_lines
            if remaining <= 0 or remaining_lines <= 0 or (
                    page_items and remaining < paged_reading.MIN_PAGE_BUDGET_CHARS):
                break
            read = self._read_page_source(path, profile_user_id=profile_user_id, session_id=session_id)
            if read.get('status') in {'stale_cursor', 'source_missing'}:
                return self._paged_failure(str(read['status']), reason=str(read['reason']), detail_requested=uri)
            if read.get("status") != "ok":
                page_items.append({"uri": uri, **read})
                file_index += 1
                offset = 0
                expected_fingerprint = ""
                current_fingerprint = ""
                continue
            content = str(read.get("content") or "")
            if read.get("extraction_capped"):
                extraction_capped = True
            if len(content) <= offset and offset > 0:
                return self._paged_failure(
                    "stale_cursor", reason="workspace file shrank since the previous page", detail_requested=uri
                )
            # Content-based fingerprint: filesystem mtime granularity is not
            # reliable across hosts, so bind the cursor to the extracted text
            # itself.  Same text -> same page boundary; changed text -> stale.
            current_fingerprint = str(read.get('_fingerprint') or '') or self._content_fingerprint(content)
            if cursor and expected_fingerprint and current_fingerprint != expected_fingerprint:
                return self._paged_failure(
                    "stale_cursor", reason="workspace file changed since the previous page", detail_requested=uri
                )
            page_text, next_offset, total_chars = paged_reading.slice_page(
                content,
                start=offset,
                budget_chars=remaining,
                budget_lines=remaining_lines,
            )
            page_items.append(
                {
                    "uri": uri,
                    "status": "ok",
                    "content": page_text,
                    "source_kind": read.get("source_kind") or "",
                    "page_start": offset,
                    "page_end": next_offset,
                    "shown_chars": len(page_text),
                    "total_chars": total_chars,
                    "file_complete": next_offset >= total_chars and not read.get('extraction_capped'),
                }
            )
            shown_chars += len(page_text)
            shown_lines += page_text.count("\n") + (1 if page_text else 0)
            if next_offset >= total_chars:
                file_index += 1
                offset = 0
                expected_fingerprint = ""
                current_fingerprint = ""
            else:
                offset = next_offset
                # A partial file is the page boundary. Continuing the same file
                # in this result would duplicate file headers and can exceed the
                # line budget even while the raw character counter looks safe.
                break

        window_done = file_index >= len(resolved) and offset == 0
        complete = window_done and not extraction_capped

        next_cursor = None
        if not window_done:
            next_cursor = paged_reading.make_paged_cursor(
                tool="ws",
                binding=owner_binding,
                payload=paged_reading.json_payload(
                    {
                        "t": [self.to_uri(path) for path, _uri in resolved],
                        "i": file_index,
                        "o": offset,
                        "f": expected_fingerprint or current_fingerprint,
                        "c": extraction_capped,
                    }
                ),
            )
        return {
            "status": "ok",
            "items": page_items,
            "complete": complete,
            "next_cursor": next_cursor,
            "extraction_capped": extraction_capped,
            "diagnostics": {
                "shown_chars": shown_chars,
                "file_count": len(resolved),
            },
        }

    @staticmethod
    def _paged_failure(status: str, *, reason: str, detail_requested: str = "") -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "detail_requested": detail_requested,
            "items": [],
            "complete": True,
            "next_cursor": None,
            "diagnostics": {},
        }

    @staticmethod
    def _content_fingerprint(content: str) -> str:
        return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()[:32]
