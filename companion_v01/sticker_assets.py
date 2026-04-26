from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StickerResolution:
    ok: bool
    sticker: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    reason: str = ""


class StickerAssetService:
    """Small static sticker registry for Akane expression packs."""

    def __init__(self, *, assets_dir: Path) -> None:
        self.assets_dir = Path(assets_dir)
        self.pack_dir = self.assets_dir / "stickers" / "akane_v1"
        self.manifest_path = self.pack_dir / "stickers.json"
        self._manifest: dict[str, Any] | None = None

    def list_stickers(self) -> list[dict[str, Any]]:
        manifest = self._load_manifest()
        stickers = manifest.get("stickers") if isinstance(manifest, dict) else []
        if not isinstance(stickers, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in stickers:
            sticker = self._normalize_sticker(item)
            if sticker:
                normalized.append(sticker)
        return normalized

    def resolve(self, target: Any) -> StickerResolution:
        query = self._normalize_key(str(target or ""))
        if not query:
            return StickerResolution(ok=False, reason="empty_target")

        stickers = self.list_stickers()
        exact_matches: list[dict[str, Any]] = []
        fuzzy_matches: list[dict[str, Any]] = []
        for sticker in stickers:
            keys = self._sticker_keys(sticker)
            if query in keys:
                exact_matches.append(sticker)
                continue
            if any(query and (query in key or key in query) for key in keys):
                fuzzy_matches.append(sticker)

        if len(exact_matches) == 1:
            return StickerResolution(ok=True, sticker=exact_matches[0])
        if len(exact_matches) > 1:
            return StickerResolution(ok=False, candidates=tuple(exact_matches), reason="ambiguous")
        if len(fuzzy_matches) == 1:
            return StickerResolution(ok=True, sticker=fuzzy_matches[0])
        if len(fuzzy_matches) > 1:
            return StickerResolution(ok=False, candidates=tuple(fuzzy_matches), reason="ambiguous")
        return StickerResolution(ok=False, reason="not_found")

    def build_prompt_list(self) -> str:
        items = []
        for sticker in self.list_stickers():
            aliases = [alias for alias in sticker.get("aliases", []) if alias != sticker.get("display_name")]
            alias_text = f"；别名：{'、'.join(aliases[:3])}" if aliases else ""
            items.append(f"{sticker['id']}({sticker['display_name']}{alias_text})")
        return "、".join(items)

    def _load_manifest(self) -> dict[str, Any]:
        if self._manifest is not None:
            return self._manifest
        if not self.manifest_path.exists():
            self._manifest = {"pack_id": "akane_v1", "stickers": []}
            return self._manifest
        try:
            self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            self._manifest = {"pack_id": "akane_v1", "stickers": []}
        return self._manifest

    def _normalize_sticker(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        sticker_id = str(item.get("id") or "").strip()
        filename = str(item.get("filename") or "").strip()
        if not sticker_id or not filename:
            return None
        display_name = str(item.get("display_name") or sticker_id).strip()
        path = self.pack_dir / filename
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        return {
            "id": sticker_id,
            "display_name": display_name,
            "filename": filename,
            "absolute_path": str(path),
            "public_path": f"/assets/stickers/akane_v1/{filename}",
            "exists": path.exists(),
            "aliases": [str(alias or "").strip() for alias in aliases if str(alias or "").strip()],
            "tags": [str(tag or "").strip() for tag in tags if str(tag or "").strip()],
        }

    def _sticker_keys(self, sticker: dict[str, Any]) -> set[str]:
        values = [
            sticker.get("id"),
            sticker.get("display_name"),
            sticker.get("filename"),
            *list(sticker.get("aliases") or []),
            *list(sticker.get("tags") or []),
        ]
        return {self._normalize_key(str(value or "")) for value in values if str(value or "").strip()}

    def _normalize_key(self, value: str) -> str:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("-", "")
            .replace(".png", "")
        )
