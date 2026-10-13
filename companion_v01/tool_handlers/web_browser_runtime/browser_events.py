"""Public browser and generated-file events without host storage paths."""

from __future__ import annotations

from typing import Any

from ...browser_page_runtime import BrowserPageResult
from ..core import ToolExecutionContext


def build_browser_success_events(
    normalized: BrowserPageResult, *, context: ToolExecutionContext,
    open_event: dict[str, Any] | None, generated_screenshot: dict[str, Any] | None,
    safe_url: str, safe_title: str, scroll_delta: int, element_count: int,
) -> list[dict[str, Any]]:
    events = []
    if open_event:
        events.append(open_event)
    if generated_screenshot is not None:
        public_generated = {
            key: generated_screenshot.get(key)
            for key in (
                "generated_id",
                "generated_handle",
                "output_title",
                "output_format",
                "file_ext",
                "mime_type",
                "file_size",
            )
            if generated_screenshot.get(key) not in (None, "")
        }
        events.append(
            {
                "type": "generated_file_ready",
                # Public event contract: the stable handle is sufficient
                # for send_file. Managed storage paths remain host-only.
                "generated_file": public_generated,
                "send_to_user": False,
            }
        )
    events.append(
        {
            "type": "browser_page_read",
            "provider": "managed_browser",
            "action": normalized.action,
            "status": normalized.status,
            "url": safe_url,
            "title": safe_title,
            "client_mode": context.client_mode,
            "scroll_delta": scroll_delta if normalized.action == "scroll" else 0,
            "element_count": element_count,
            "requires_confirmation": False,
            "snapshot_id": str(normalized.snapshot_id or ""),
            "observation_id": str(getattr(normalized, "observation_id", "") or ""),
            "screenshot_id": normalized.screenshot_id,
            "action_state": normalized.action_state,
            "observation_state": normalized.observation_state,
            "visual_status": normalized.visual_status,
            "page_revision": str(normalized.page_revision or ""),
            "complete": bool(normalized.complete),
            "shown_chars": int(normalized.shown_chars or 0),
            "total_chars": int(normalized.total_chars or 0),
        }
    )
    return events
