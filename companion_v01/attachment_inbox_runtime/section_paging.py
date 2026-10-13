"""Page cached section text while checking the actual source on every request."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import hashlib
import json
import time
from typing import Any

from ..paged_reading import slice_page
from .definitions import SECTION_EXTRACTION_CHAR_LIMIT
from .section_cache import SectionSnapshot


class AttachmentSectionPagingMixin:
    @private_asset_scope
    def read_section_page(
        self, *, profile_user_id: str, session_id: str, target: str, section: str,
        kind: str, start_offset: int, expected_fingerprint: str,
        budget_chars: int, budget_lines: int, timestamp: int | None = None,
    ) -> dict[str, Any]:
        prepared = self._prepare_section_request(
            profile_user_id=profile_user_id, session_id=session_id,
            target=target, kind=kind, timestamp=timestamp,
        )
        if not prepared["ok"]:
            if expected_fingerprint:
                return self._section_page_failure("source_unavailable", "原材料已移除或不再可用")
            return prepared
        item = prepared["item"]
        section = str(section or "当前可用片段").strip()
        key = (profile_user_id, session_id, str(item.get("attachment_id") or ""), section)
        cached = self._section_cache.get(key)
        try:
            stamp = self._section_source_stamp(item)
            if cached is not None and cached.source_stamp != stamp:
                self._section_cache.discard(key)
                if expected_fingerprint:
                    return self._section_page_failure("stale_cursor", "附件来源在上一页后已变化")
                cached = None
            if cached is None:
                extracted = self._extract_section_result(
                    item, section=section, max_chars=SECTION_EXTRACTION_CHAR_LIMIT + 1,
                )
                content = extracted.text
                if not content:
                    unavailable = self._section_unavailable(item)
                    unavailable["read_reason"] = extracted.reason or "source_text_unavailable"
                    unavailable["read_error_type"] = extracted.error_type
                    return unavailable
                if stamp != self._section_source_stamp(item):
                    return self._section_page_failure("stale_cursor", "读取期间附件来源发生变化，请重新读取")
                truncated = len(content) > SECTION_EXTRACTION_CHAR_LIMIT or extracted.truncated
                content = content[:SECTION_EXTRACTION_CHAR_LIMIT]
                cached = SectionSnapshot(
                    text=content, fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    source_stamp=stamp, truncated=truncated, created_at=time.monotonic(),
                )
                self._section_cache.put(key, cached)
        except OSError:
            self._section_cache.discard(key)
            return self._section_page_failure("source_unavailable", "附件原文件暂时无法读取")
        if expected_fingerprint and cached.fingerprint != expected_fingerprint:
            return self._section_page_failure("stale_cursor", "附件内容在上一页后已变化")
        if start_offset > len(cached.text):
            return self._section_page_failure("stale_cursor", "附件片段比 cursor 记录的更短")
        page, next_offset, total_chars = slice_page(
            cached.text, start=start_offset, budget_chars=budget_chars, budget_lines=budget_lines,
        )
        window_done = next_offset >= total_chars
        return {
            "ok": True, "item": item, "content": page,
            "page": {
                "fingerprint": cached.fingerprint, "next_offset": next_offset,
                "total_chars": total_chars, "window_done": window_done,
                "source_truncated": cached.truncated,
                "complete": window_done and not cached.truncated,
            },
        }

    def _section_source_stamp(self, item: dict[str, Any]) -> str:
        digest = hashlib.sha256()
        # Touching last_used_at/updated_at must not invalidate the same source.
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        payload = {"storage": item.get("storage_relpath"), "status": item.get("status"), "detail": detail}
        digest.update(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
        path = self._resolve_storage_path(item)
        if path is not None:
            digest.update(str(path).encode("utf-8"))
            # Full streaming hashing also detects equal-size edits that restore
            # mtime. It avoids reparsing or retaining the source's entire bytes.
            with path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _section_page_failure(status: str, reason: str) -> dict[str, Any]:
        return {"ok": False, "status": status, "reason": reason, "content": "", "item": None}
