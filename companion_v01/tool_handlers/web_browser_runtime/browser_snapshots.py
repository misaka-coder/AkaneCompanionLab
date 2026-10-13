"""Conversation-bound continuation of immutable browser snapshots."""

from __future__ import annotations

from typing import Any

from ..core import ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class BrowserSnapshotsMixin:
    def _execute_snapshot_continuation(
        self, *, cursor: str, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        from ...paged_reading import (
            NEXT_PAGE_BUDGET_CHARS,
            NEXT_PAGE_BUDGET_LINES,
            page_failure_feedback,
            parse_json_payload,
            parse_paged_cursor,
            slice_page,
        )

        payload = parse_json_payload(
            parse_paged_cursor(cursor, tool="bp", binding=self._browser_cursor_owner_binding(context))
        )
        if not isinstance(payload, dict):
            return self._snapshot_failure("cursor_invalid", "cursor 不属于当前用户/会话，或已损坏")
        snapshot_id = str(payload.get("s") or "").strip()
        if not snapshot_id:
            return self._snapshot_failure("cursor_invalid", "cursor 没有快照标识")
        try:
            offset = max(0, int(payload.get("o") or 0))
        except (TypeError, ValueError):
            return self._snapshot_failure("cursor_invalid", "cursor 偏移无效")
        runner = self._runner_for_context(context)
        record = runner.read_snapshot(snapshot_id) if hasattr(runner, "read_snapshot") else None
        if record is None:
            has_live = bool(getattr(runner, "has_live_page", None) and runner.has_live_page())
            return self._snapshot_failure(
                "page_closed" if not has_live else "snapshot_expired",
                "浏览器页面已关闭" if not has_live else "该快照已过期，需要重新读取当前页面",
            )
        snapshot_text = str(record.get("text") or "")
        if len(snapshot_text) <= offset and offset > 0:
            return self._snapshot_failure("stale_cursor", "快照内容比 cursor 记录的更短")
        page_text, next_offset, total_chars = slice_page(
            snapshot_text,
            start=offset,
            budget_chars=NEXT_PAGE_BUDGET_CHARS,
            budget_lines=NEXT_PAGE_BUDGET_LINES,
        )
        safe_page = self._sanitize_output(page_text, strip=False)
        complete = next_offset >= total_chars
        kind = str(record.get("kind") or "page")
        lines = [
            "【Akane 托管浏览器窗口·同一快照续读】",
            f"动作：snapshot 续读（不会重新滚动或点击页面）",
            f"URL: {self._sanitize_output(str(record.get('url') or ''))[:800]}",
            f"页面版本：{str(record.get('revision') or '')}",
            "元素摘要：" if kind == "elements" else "页面状态快照：",
            safe_page,
        ]
        continuation = None
        if not complete:
            next_cursor = self._issue_browser_cursor(snapshot_id=snapshot_id, offset=next_offset, context=context)
            if next_cursor:
                continuation = {"type": self.tool_type, "cursor": next_cursor}
                lines.append(
                    f"本页之后还有未展示的快照内容（已展示 {len(page_text)}/{total_chars} 字）。"
                    "如果当前内容已经足够回答，可以直接回答；只有确实需要同一份快照的后续内容时，才调用："
                    f'browser_page(cursor="{next_cursor}")'
                )
        else:
            lines.append(f"已读完这份快照（共 {total_chars} 字）。要查看操作后的最新页面状态请重新调用 snapshot。")
        content = "\n".join(lines)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "browser_page_read",
                    "provider": "managed_browser",
                    "action": "snapshot",
                    "status": "available",
                    "url": str(record.get("url") or ""),
                    "snapshot_id": snapshot_id,
                    "page_revision": str(record.get("revision") or ""),
                    "complete": complete,
                    "shown_chars": len(page_text),
                    "total_chars": total_chars,
                    "requires_confirmation": False,
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=complete,
                continuation=continuation,
                diagnostics={
                    "action": "snapshot",
                    "complete": complete,
                    "shown_chars": len(page_text),
                    "total_chars": total_chars,
                    "snapshot_id": snapshot_id,
                },
            ),
            state_updates={
                "browser_page_status": "available",
                "browser_page_url": str(record.get("url") or ""),
                "browser_page_title": str(record.get("title") or ""),
            },
        )

    def _issue_browser_cursor(self, *, snapshot_id: str, offset: Any, context: ToolExecutionContext) -> str:
        from ...paged_reading import json_payload, make_paged_cursor

        try:
            clean_offset = max(0, int(str(offset or "0").split(":")[-1]))
        except (TypeError, ValueError):
            return ""
        return make_paged_cursor(
            tool="bp",
            binding=self._browser_cursor_owner_binding(context),
            payload=json_payload({"s": str(snapshot_id or ""), "o": clean_offset}),
        )

    def _browser_cursor_owner_binding(self, context: ToolExecutionContext) -> str:
        from ...paged_reading import cursor_binding

        return cursor_binding("browser_page", context.profile_user_id, context.session_id)

    def _snapshot_failure(self, status: str, reason: str) -> ToolExecutionResult:
        from ...paged_reading import page_failure_feedback

        content = page_failure_feedback(status=status, tool=self.tool_type, detail=reason)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "browser_page_read",
                    "provider": "managed_browser",
                    "action": "snapshot",
                    "status": status,
                    "reason": reason,
                }
            ],
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content,
                producer_bounded=True,
                complete=True,
                continuation=None,
                diagnostics={"status": status},
            ),
            state_updates={
                "browser_page_status": status,
                "browser_page_reason": reason,
            },
        )
