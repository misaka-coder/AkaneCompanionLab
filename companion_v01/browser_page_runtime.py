"""Stable entry point for Akane-owned browser page execution.

Implementation owners live in managed_browser. Authorization and tool argument
validation remain in the existing handlers and executor broker.
"""
from __future__ import annotations

from .managed_browser.models import (
    BrowserPageResult, SNAPSHOT_CAPTURE_MAX_CHARS, SNAPSHOT_TTL_SECONDS,
    SNAPSHOT_MAX_ENTRIES, SNAPSHOT_MAX_TOTAL_BYTES,
)
from .managed_browser.runner import ManagedBrowserPageRunner
from .managed_browser.sessions import ManagedBrowserSessionManager
from .browser_page_download import (
    DEFAULT_MAX_DOWNLOAD_BYTES, BrowserDownloadRecord, BrowserDownloadTracker,
)
from .browser_page_observation import (
    DEFAULT_MAX_SCREENSHOT_BYTES, DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES,
    DEFAULT_OBSERVATION_MAX_RECORDS, DEFAULT_OBSERVATION_TTL_SECONDS,
    ObservationRecord, ObservationStore, generate_observation_id,
    generate_screenshot_id, png_dimensions, read_live_page_state,
    coordinate_pixels_match,
)
from .browser_page_contract import CONTROL_ACTIONS, managed_capabilities

__all__ = [
    'BrowserPageResult',
    'ManagedBrowserPageRunner',
    'ManagedBrowserSessionManager',
    'SNAPSHOT_CAPTURE_MAX_CHARS',
    'SNAPSHOT_TTL_SECONDS',
    'SNAPSHOT_MAX_ENTRIES',
    'SNAPSHOT_MAX_TOTAL_BYTES',
    'DEFAULT_MAX_DOWNLOAD_BYTES',
    'BrowserDownloadRecord',
    'BrowserDownloadTracker',
    'DEFAULT_MAX_SCREENSHOT_BYTES',
    'DEFAULT_MAX_TOTAL_SCREENSHOT_BYTES',
    'DEFAULT_OBSERVATION_MAX_RECORDS',
    'DEFAULT_OBSERVATION_TTL_SECONDS',
    'ObservationRecord',
    'ObservationStore',
    'generate_observation_id',
    'generate_screenshot_id',
    'png_dimensions',
    'read_live_page_state',
    'coordinate_pixels_match',
    'CONTROL_ACTIONS',
    'managed_capabilities',
]
