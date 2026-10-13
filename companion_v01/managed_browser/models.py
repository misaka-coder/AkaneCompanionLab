"""Page result contract and snapshot cache defaults."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SNAPSHOT_CAPTURE_MAX_CHARS = 250_000
SNAPSHOT_TTL_SECONDS = 30 * 60
SNAPSHOT_MAX_ENTRIES = 16
SNAPSHOT_MAX_TOTAL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class BrowserPageResult:
    ok: bool
    status: str
    action: str
    url: str = ""
    title: str = ""
    text: str = ""
    reason: str = ""
    snapshot_id: str = ""
    complete: bool = True
    next_cursor: str = ""
    shown_chars: int = 0
    total_chars: int = 0
    page_revision: str = ""
    retryable: bool = False
    next_action: str = ""
    observation_id: str = ""
    screenshot_id: str = ""
    action_state: str = "not_started"
    observation_state: str = "complete"
    page_changed: str = "unknown"
    visual_status: str = "not_requested"
    model_image: dict[str, Any] | None = None
    viewport: tuple[int, int] = (0, 0)
    screenshot_dimensions: tuple[int, int] = (0, 0)
    download_id: str = ""
    download_status: str = ""
    download_filename: str = ""
    attachment_handle: str = ""
    attachment_id: str = ""
    dialog_events: tuple[dict[str, Any], ...] = ()
    step_results: tuple[dict[str, Any], ...] = ()
