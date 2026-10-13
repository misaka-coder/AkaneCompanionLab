"""Akane -> memcore bridge.

MemCore is Akane's authoritative host dialogue memory. Package imports remain
lazy for isolated tests and programmatic migration adapters; legacy memory
selection is not a supported host environment setting.
"""

from __future__ import annotations

from .manager import MemcoreManager, MemcoreRuntimeStatus, normalize_memory_backend

__all__ = ["MemcoreManager", "MemcoreRuntimeStatus", "normalize_memory_backend"]
