"""Owner-bound paging of complete live listing entries."""

from __future__ import annotations

from typing import Any


class WorkspaceListingPagingMixin:
    def list_items_paged(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        paths: list[str] | None = None,
        depth: int = 1,
        max_entries: int = 10000,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Page a live directory listing by complete entries.

        The cursor payload carries the requested paths, depth and entry limit
        plus the next entry index.  Continuations re-walk the live file system
        with the same parameters, so the list reflects the current state; the
        page boundary never splits a single entry line.
        """
        from .. import paged_reading

        owner_binding = paged_reading.cursor_binding("list_workspace", profile_user_id, session_id)
        effective_paths = self._normalize_target_list(paths or ["workspace:/"])
        effective_depth = max(0, min(8, int(depth or 0)))
        entry_limit = max(1, min(50000, int(max_entries or 10000)))
        start_index = 0
        if cursor:
            payload = paged_reading.parse_json_payload(
                paged_reading.parse_paged_cursor(cursor, tool="wl", binding=owner_binding)
            )
            if not isinstance(payload, dict):
                return self._paged_failure("cursor_invalid", reason="cursor could not be decoded for this owner/session")
            effective_paths = (
                self._normalize_target_list([str(item or "") for item in payload.get("p") or []])
                or effective_paths
            )
            try:
                effective_depth = max(0, min(8, int(payload.get("d") or effective_depth)))
            except (TypeError, ValueError):
                pass
            try:
                entry_limit = max(1, min(50000, int(payload.get("m") or entry_limit)))
            except (TypeError, ValueError):
                pass
            try:
                start_index = max(0, int(payload.get("i") or 0))
            except (TypeError, ValueError):
                start_index = 0

        raw = self.list_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            paths=effective_paths,
            depth=effective_depth,
            max_entries=entry_limit,
        )
        targets = list(raw.get("results") or [])
        entries_flat: list[tuple[int, dict[str, Any]]] = [
            (target_index, dict(entry))
            for target_index, target in enumerate(targets)
            if str(target.get("status") or "") == "ok"
            for entry in list(target.get("entries") or [])
        ]
        total_entries = len(entries_flat)
        budget = paged_reading.ENTRY_PAGE_BUDGET_CHARS
        shown_chars = 0
        picked: dict[int, list[dict[str, Any]]] = {}
        end_index = start_index
        for index in range(start_index, total_entries):
            target_index, entry = entries_flat[index]
            line = self._render_entry_line(entry)
            if shown_chars > 0 and shown_chars + len(line) + 1 > budget:
                break
            picked.setdefault(target_index, []).append(entry)
            shown_chars += len(line) + 1
            end_index = index + 1
        if shown_chars == 0 and start_index < total_entries:
            # A single over-budget entry still gets shown whole (never split).
            target_index, entry = entries_flat[start_index]
            picked.setdefault(target_index, []).append(entry)
            end_index = start_index + 1

        results: list[dict[str, Any]] = []
        for target_index, target in enumerate(targets):
            if target_index in picked:
                results.append(
                    {
                        "requested": str(target.get("requested") or ""),
                        "status": "ok",
                        "entries": picked[target_index],
                    }
                )
                continue
            if str(target.get("status") or "") != "ok" and start_index == 0:
                results.append(target)
        complete = end_index >= total_entries
        next_cursor = ""
        if not complete:
            next_cursor = paged_reading.make_paged_cursor(
                tool="wl",
                binding=owner_binding,
                payload=paged_reading.json_payload(
                    {"p": effective_paths, "d": effective_depth, "m": entry_limit, "i": end_index}
                ),
            )
        return {
            "status": "ok" if results else raw.get("status") or "ok",
            "results": results,
            "truncated": bool(raw.get("truncated")),
            "complete": complete,
            "next_cursor": next_cursor,
            "shown_entries": min(total_entries, end_index),
            "page_entries": max(0, end_index - start_index),
            "total_entries": total_entries,
        }

    @staticmethod
    def _render_entry_line(entry: dict[str, Any]) -> str:
        kind = "目录" if entry.get("kind") == "directory" else "文件"
        return (
            f"  - [{kind}/{entry.get('workspace_status')}] "
            f"{entry.get('uri')} ({int(entry.get('size') or 0)} bytes)"
        )
