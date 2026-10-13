"""Target selection for AttachmentInboxService."""

from __future__ import annotations

import re
from typing import Any


class AttachmentTargetSelectionMixin:
    def _normalize_targets(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = re.split(r"[,，、;；\n]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        targets: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in targets:
                targets.append(text[:120])
        return targets

    def _dedupe_attachment_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in items:
            item_id = str(item.get("attachment_id") or "").strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            output.append(item)
        return output

    def _format_ambiguity_target(self, *, target: str, items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        labels = [self._compact_item_label(item) for item in items[:4]]
        labels = [label for label in labels if label]
        if not labels:
            return ""
        return f"{target} -> {'、'.join(labels)}"

    def _filter_items_by_target_kind(
        self,
        items: list[dict[str, Any]],
        target: str,
        kind: str,
    ) -> list[dict[str, Any]]:
        explicit_kind = self._infer_kind_from_target(target)
        normalized_kind = explicit_kind or str(kind or "any").strip().lower()
        if normalized_kind in {"photo", "picture", "pic", "img"}:
            normalized_kind = "image"
        if normalized_kind in {"doc", "txt", "pdf", "text"}:
            normalized_kind = "document"
        if normalized_kind in {"music", "song", "voice"}:
            normalized_kind = "audio"
        if normalized_kind not in {"image", "document", "audio", "file"}:
            return list(items)
        if normalized_kind == "document":
            return [item for item in items if item.get("kind") in {"document", "file"}]
        return [item for item in items if item.get("kind") == normalized_kind]

    def _infer_kind_from_target(self, target: str) -> str:
        text = str(target or "").strip().lower()
        if any(token in text for token in ("图", "张", "image", "img", "pic", "photo")):
            return "image"
        if any(token in text for token in ("文件", "文档", "file", "doc", "pdf", "txt")):
            return "document"
        if any(token in text for token in ("音频", "歌", "audio", "song", "music")):
            return "audio"
        return ""

    def _latest_item(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        ordered = sorted(
            items,
            key=lambda item: (
                int(item.get("created_at") or 0),
                int(item.get("updated_at") or 0),
                int(item.get("sequence_no") or 0),
            ),
        )
        return ordered[-1] if ordered else None

    def _oldest_item(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        ordered = sorted(
            items,
            key=lambda item: (
                int(item.get("created_at") or 0),
                int(item.get("updated_at") or 0),
                int(item.get("sequence_no") or 0),
            ),
        )
        return ordered[0] if ordered else None

    def _sort_by_sequence(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (
                str(item.get("kind") or ""),
                int(item.get("sequence_no") or 0),
                int(item.get("created_at") or 0),
            ),
        )

    def _parse_sequence_no(self, target: str) -> int | None:
        text = str(target or "").strip()
        match = re.search(r"(?:第\s*)?(\d+)\s*(?:张|个|份|段|图|文件|文档|音频)?", text)
        if match:
            return int(match.group(1))
        chinese = self._parse_chinese_number(text)
        return chinese

    def _looks_like_sequence_reference(self, target: str) -> bool:
        text = str(target or "").strip().lower()
        if not text:
            return False
        text = re.sub(r"\s+", "", text)
        if re.fullmatch(r"(?:第)?\d+(?:张图|个文件|份文档|段音频|张|个|份|段|图|文件|文档|音频)?", text):
            return True
        if re.fullmatch(
            r"(?:第)?[一二两三四五六七八九十]+(?:张图|个文件|份文档|段音频|张|个|份|段|图|文件|文档|音频)?", text
        ):
            return True
        return False

    def _parse_reverse_ordinal(self, target: str) -> int | None:
        text = str(target or "").strip()
        if "倒数" not in text:
            return None
        match = re.search(r"倒数(?:第)?\s*(\d+)", text)
        if match:
            return int(match.group(1))
        return self._parse_chinese_number(text.replace("倒数", ""))

    def _parse_chinese_number(self, text: str) -> int | None:
        compact = re.sub(r"[第张个份段页节章表图文件文档音频\s]", "", str(text or ""))
        compact = compact.strip()
        if not compact:
            return None
        table = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        if compact in table:
            return table[compact]
        if compact.startswith("十") and len(compact) == 2 and compact[1] in table:
            return 10 + int(table[compact[1]])
        if compact.endswith("十") and len(compact) == 2 and compact[0] in table:
            return int(table[compact[0]]) * 10
        if "十" in compact:
            left, right = compact.split("十", 1)
            if left in table and right in table:
                return int(table[left]) * 10 + int(table[right])
        return None
