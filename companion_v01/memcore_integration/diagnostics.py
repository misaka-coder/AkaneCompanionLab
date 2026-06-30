"""Diagnostics helpers for memcore shadow comparisons.

Do not put full memory text into shadow diagnostics. These payloads can surface
inside debug state, so they should stay structural and hash-based.
"""

from __future__ import annotations

import hashlib
from typing import Any


def safe_status(manager: Any) -> dict[str, Any]:
    if manager is None:
        return {"backend": "legacy", "enabled": False, "available": False, "reason": "not_configured"}
    try:
        status = manager.status()
    except Exception as exc:
        return {"backend": "unknown", "enabled": False, "available": False, "reason": str(exc)}
    return dict(status) if isinstance(status, dict) else {"backend": "unknown", "available": False}


def snippet_hashes(snippets: list[str] | tuple[str, ...] | None, *, limit: int = 5) -> list[str]:
    hashes: list[str] = []
    seen: set[str] = set()
    for snippet in snippets or []:
        text = str(snippet or "").strip()
        if not text:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if digest in seen:
            continue
        seen.add(digest)
        hashes.append(digest)
        if len(hashes) >= max(1, int(limit)):
            break
    return hashes


def build_shadow_payload(
    *,
    legacy_snippets: list[str],
    memcore_result: dict[str, Any],
) -> dict[str, Any]:
    result = dict(memcore_result if isinstance(memcore_result, dict) else {})
    legacy_hashes = snippet_hashes(legacy_snippets)
    memcore_hashes = [str(item) for item in list(result.get("snippet_hashes") or []) if str(item).strip()]
    result["legacy_snippet_count"] = len([item for item in legacy_snippets if str(item or "").strip()])
    result["legacy_snippet_hashes"] = legacy_hashes
    result["overlap_hash_count"] = len(set(legacy_hashes).intersection(memcore_hashes))
    return result
