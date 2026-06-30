"""Optional Akane -> memcore bridge.

The legacy Akane memory stack remains the default. This package is intentionally
lazy: importing it must not require the sibling memcore package unless a
non-legacy backend is explicitly enabled.
"""

from __future__ import annotations

from .manager import MemcoreManager, MemcoreRuntimeStatus, normalize_memory_backend

__all__ = ["MemcoreManager", "MemcoreRuntimeStatus", "normalize_memory_backend"]
