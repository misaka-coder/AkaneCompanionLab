"""Akane -> memcore bridge.

memcore is Akane's primary dialogue memory backend. The bridge stays lazy so
tests and explicit ``MEMORY_BACKEND=legacy`` runs can still boot without loading
the sibling package.
"""

from __future__ import annotations

from .manager import MemcoreManager, MemcoreRuntimeStatus, normalize_memory_backend

__all__ = ["MemcoreManager", "MemcoreRuntimeStatus", "normalize_memory_backend"]
