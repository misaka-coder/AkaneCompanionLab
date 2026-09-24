"""Observation data structures and lifecycle management for browser_page."""
from __future__ import annotations

import base64
import io
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


DEFAULT_OBSERVATION_TTL_SECONDS = 30 * 60  # 30 minutes
DEFAULT_OBSERVATION_MAX_RECORDS = 32
DEFAULT_MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024  # 8MB per temporary screenshot
DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES = 32 * 1024 * 1024  # 32MB per browser session
COORDINATE_MAX_AGE_SECONDS = 60.0


@dataclass(frozen=True)
class ObservationRecord:
    observation_id: str
    page_id: str = ""
    browser_generation: int = 1
    document_generation: int = 1
    page_revision: int = 0
    url: str = ""
    title: str = ""
    captured_at: float = field(default_factory=time.time)
    aria_snapshot: str = ""
    visible_text: str = ""
    element_candidates: tuple[dict[str, Any], ...] = ()
    screenshot_id: str = ""
    screenshot_bytes: bytes | None = None
    screenshot_base64: str = ""
    viewport: tuple[int, int] = (0, 0)
    screenshot_dimensions: tuple[int, int] = (0, 0)
    device_scale_factor: float = 1.0
    action_state: str = "not_started"  # not_started | executed | unknown
    observation_state: str = "complete"  # complete | partial | failed | unstable
    page_changed: str = "unknown"  # yes | no | unknown
    visual_status: str = "not_requested"  # available | visual_unavailable | not_requested
    live_state: dict[str, Any] = field(default_factory=dict)

    def build_model_image_dict(self) -> dict[str, Any] | None:
        """Construct model_image_inputs entry if a valid temporary screenshot is present."""
        if not self.screenshot_base64 and self.screenshot_bytes:
            encoded = "data:image/png;base64," + base64.b64encode(self.screenshot_bytes).decode("ascii")
        else:
            encoded = self.screenshot_base64
        if not encoded or not encoded.startswith("data:image/"):
            return None
        return {
            "attachment_id": self.observation_id,
            "attachment_handle": self.screenshot_id or f"shot_{self.observation_id[-8:]}",
            "title": f"browser-view-{self.observation_id[:8]}",
            "media_type": "image/png",
            "data_url": encoded,
        }


class ObservationStore:
    """Bounded, thread-safe cache for temporary observations in a browser session."""

    def __init__(
        self,
        *,
        max_records: int = DEFAULT_OBSERVATION_MAX_RECORDS,
        ttl_seconds: float = DEFAULT_OBSERVATION_TTL_SECONDS,
        max_screenshot_bytes: int = DEFAULT_MAX_SCREENSHOT_BYTES,
        max_total_screenshot_bytes: int = DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES,
        now: Any = None,
    ) -> None:
        self.max_records = max(4, int(max_records))
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self.max_screenshot_bytes = max(0, int(max_screenshot_bytes))
        self.max_total_screenshot_bytes = max(0, int(max_total_screenshot_bytes))
        self._now = now or time.time
        self._lock = threading.RLock()
        self._records: dict[str, tuple[float, ObservationRecord]] = {}
        self._latest_id: str = ""
        self._screenshot_bytes_total = 0

    def put(self, record: ObservationRecord) -> ObservationRecord:
        with self._lock:
            now = float(self._now())
            record = self._fit_screenshot_budget_locked(record)
            previous = self._records.get(record.observation_id)
            if previous is not None:
                self._screenshot_bytes_total -= self._record_screenshot_bytes(previous[1])
            self._records[record.observation_id] = (now, record)
            self._screenshot_bytes_total += self._record_screenshot_bytes(record)
            self._latest_id = record.observation_id
            self._evict_expired_or_overflow_locked(now)
        return record

    def get(self, observation_id: str) -> ObservationRecord | None:
        clean_id = str(observation_id or "").strip()
        if not clean_id:
            return None
        with self._lock:
            cached = self._records.get(clean_id)
            if cached is None:
                return None
            created_at, record = cached
            now = float(self._now())
            if now - created_at > self.ttl_seconds:
                self._records.pop(clean_id, None)
                self._screenshot_bytes_total -= self._record_screenshot_bytes(record)
                if self._latest_id == clean_id:
                    self._latest_id = ""
                return None
            return record

    def latest(self) -> ObservationRecord | None:
        with self._lock:
            if not self._latest_id:
                return None
            return self.get(self._latest_id)

    def validate_action_target(
        self,
        *,
        observation_id: str,
        current_browser_gen: int,
        current_doc_gen: int,
        current_page_id: str = "",
        current_page_revision: int | str | None = None,
    ) -> tuple[bool, str]:
        """Validate if an observation target is still fresh enough for interaction."""
        clean_id = str(observation_id or "").strip()
        if not clean_id:
            # If no observation_id was specified by caller, we don't reject by observation id.
            return True, ""
        record = self.get(clean_id)
        if record is None:
            return False, "observation_expired_or_not_found"
        if record.browser_generation != current_browser_gen:
            return False, "browser_restarted_stale_observation"
        if record.document_generation != current_doc_gen:
            return False, "document_navigated_stale_observation"
        if current_page_id and record.page_id and record.page_id != current_page_id:
            return False, "page_switched_stale_observation"
        if current_page_revision is not None and record.page_revision != _coerce_revision(current_page_revision):
            return False, "page_revision_stale_observation"
        return True, ""

    def get_by_screenshot_id(self, screenshot_id: str) -> ObservationRecord | None:
        """Find an observation record associated with the given screenshot_id."""
        clean_id = str(screenshot_id or "").strip()
        if not clean_id:
            return None
        with self._lock:
            now = float(self._now())
            for obs_id, (ts, rec) in list(self._records.items()):
                if now - ts > self.ttl_seconds:
                    self._records.pop(obs_id, None)
                    self._screenshot_bytes_total -= self._record_screenshot_bytes(rec)
                    if self._latest_id == obs_id:
                        self._latest_id = ""
                    continue
                if rec.screenshot_id == clean_id:
                    return rec
            return None

    def validate_coordinate_target(
        self,
        *,
        screenshot_id: str,
        coordinate: tuple[int, int] | list[int] | Any,
        current_browser_gen: int,
        current_doc_gen: int,
        observation_id: str = "",
        current_page_id: str = "",
        current_page_revision: int | str | None = None,
        current_viewport: tuple[int, int] | None = None,
    ) -> tuple[bool, str]:
        """Validate if coordinate click is bound to an active, fresh screenshot and within viewport."""
        clean_id = str(screenshot_id or "").strip()
        if not clean_id:
            return False, "screenshot_id_required"
        record = self.get_by_screenshot_id(clean_id)
        if record is None:
            return False, "screenshot_expired_or_not_found"
        if float(self._now()) - record.captured_at > COORDINATE_MAX_AGE_SECONDS:
            return False, "screenshot_too_old_reobserve"
        if record.observation_state != "complete":
            return False, "screenshot_observation_incomplete"
        if record.browser_generation != current_browser_gen:
            return False, "browser_restarted_stale_screenshot"
        if record.document_generation != current_doc_gen:
            return False, "document_navigated_stale_screenshot"
        if observation_id and record.observation_id != str(observation_id).strip():
            return False, "screenshot_observation_mismatch"
        if current_page_id and record.page_id and record.page_id != current_page_id:
            return False, "page_switched_stale_screenshot"
        if current_page_revision is not None and record.page_revision != _coerce_revision(current_page_revision):
            return False, "page_revision_stale_screenshot"
        if current_viewport and record.viewport and tuple(current_viewport) != tuple(record.viewport):
            return False, "viewport_changed_stale_screenshot"
        if not isinstance(coordinate, (tuple, list)) or len(coordinate) != 2:
            return False, "invalid_coordinate_format"
        try:
            x, y = int(coordinate[0]), int(coordinate[1])
        except (ValueError, TypeError):
            return False, "invalid_coordinate_values"
        vp_w, vp_h = record.viewport
        if vp_w > 0 and vp_h > 0:
            if not (0 <= x < vp_w and 0 <= y < vp_h):
                return False, f"coordinate_out_of_bounds: ({x}, {y}) outside viewport ({vp_w}x{vp_h})"
        return True, ""

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._latest_id = ""
            self._screenshot_bytes_total = 0

    @staticmethod
    def _record_screenshot_bytes(record: ObservationRecord) -> int:
        if record.screenshot_bytes:
            return len(record.screenshot_bytes)
        if record.screenshot_base64:
            return max(0, (len(record.screenshot_base64) * 3) // 4)
        return 0

    def _fit_screenshot_budget_locked(self, record: ObservationRecord) -> ObservationRecord:
        size = self._record_screenshot_bytes(record)
        if size <= 0:
            return record
        if self.max_screenshot_bytes and size > self.max_screenshot_bytes:
            return _without_screenshot(record)

        while (
            self.max_total_screenshot_bytes
            and self._screenshot_bytes_total + size > self.max_total_screenshot_bytes
        ):
            candidates = [
                item for item in self._records.items()
                if self._record_screenshot_bytes(item[1][1]) > 0
            ]
            if not candidates:
                return _without_screenshot(record)
            oldest_id, (_created_at, oldest_record) = min(candidates, key=lambda item: item[1][0])
            del self._records[oldest_id]
            self._screenshot_bytes_total -= self._record_screenshot_bytes(oldest_record)
            if self._latest_id == oldest_id:
                self._latest_id = ""
        return record

    def _evict_expired_or_overflow_locked(self, now: float) -> None:
        expired = [
            obs_id
            for obs_id, (ts, _rec) in self._records.items()
            if now - ts > self.ttl_seconds
        ]
        for obs_id in expired:
            _created_at, expired_record = self._records.pop(obs_id)
            self._screenshot_bytes_total -= self._record_screenshot_bytes(expired_record)
            if self._latest_id == obs_id:
                self._latest_id = ""
        while len(self._records) > self.max_records:
            oldest_id = min(self._records.items(), key=lambda item: item[1][0])[0]
            _created_at, oldest_record = self._records.pop(oldest_id)
            self._screenshot_bytes_total -= self._record_screenshot_bytes(oldest_record)
            if self._latest_id == oldest_id:
                self._latest_id = ""


def _coerce_revision(value: int | str) -> int:
    try:
        text = str(value).strip().lower()
        if text.startswith("r"):
            text = text[1:]
        return int(text)
    except (TypeError, ValueError):
        return -1


def _without_screenshot(record: ObservationRecord) -> ObservationRecord:
    from dataclasses import replace

    return replace(
        record,
        screenshot_id="",
        screenshot_bytes=None,
        screenshot_base64="",
        screenshot_dimensions=(0, 0),
        visual_status="visual_unavailable",
        observation_state="partial",
    )


def png_dimensions(data: bytes | None) -> tuple[int, int]:
    """Read PNG dimensions without decoding the image or adding an imaging dependency."""
    if not data or len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    try:
        return tuple(int(value) for value in struct.unpack(">II", data[16:24]))
    except (struct.error, TypeError, ValueError):
        return (0, 0)


def generate_observation_id() -> str:
    return "obs_" + uuid.uuid4().hex[:12]


def generate_screenshot_id() -> str:
    return "shot_" + uuid.uuid4().hex[:12]


def read_live_page_state(page: Any) -> dict[str, Any]:
    """Track autonomous document/layout changes, independently of tool revisions.

    The observer is local to each document, so same-URL reloads also invalidate
    evidence. This is a freshness check, not an atomic lock on a web page.
    """
    return page.evaluate("""() => {
        const key = '__akaneObservationState';
        if (!window[key] || window[key].document !== document) {
            const state = {document, id: `${performance.timeOrigin}-${Math.random()}`, revision: 0};
            const changed = () => { state.revision++; };
            new MutationObserver(records => {
                // Playwright inserts/removes its own masking overlay during
                // screenshots. That transient helper does not change the page.
                const relevant = records.some(r => r.type !== 'childList' ||
                    [...r.addedNodes, ...r.removedNodes].some(n => n.nodeName !== 'X-PW-GLASS'));
                if (relevant) changed();
            }).observe(document, {
                subtree: true, childList: true, attributes: true, characterData: true
            });
            window.addEventListener('scroll', changed, true);
            window.addEventListener('resize', changed);
            document.addEventListener('input', changed, true);
            document.addEventListener('change', changed, true);
            document.addEventListener('load', changed, true);
            window[key] = state;
        }
        const state = window[key];
        return {document: state.id, revision: state.revision, url: location.href,
            width: innerWidth, height: innerHeight, x: scrollX, y: scrollY};
    }""")


def coordinate_pixels_match(record: ObservationRecord, current_png: bytes, x: int, y: int) -> bool:
    """Recheck the click neighborhood, including canvas changes with no DOM event.

    Comparing locally avoids unrelated clocks/animations invalidating every
    coordinate action. Images stay host-local; only the next observation is sent.
    """
    from PIL import Image, ImageChops

    if not record.screenshot_bytes:
        return False
    with Image.open(io.BytesIO(record.screenshot_bytes)) as before, Image.open(io.BytesIO(current_png)) as after:
        if before.size != after.size or before.size != record.viewport:
            return False
        width, height = before.size
        box = (max(0, x - 24), max(0, y - 24), min(width, x + 25), min(height, y + 25))
        delta = ImageChops.difference(before.crop(box).convert("RGB"), after.crop(box).convert("RGB"))
        return delta.getbbox() is None
