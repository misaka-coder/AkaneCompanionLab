from __future__ import annotations

import re
from pathlib import Path

from .resource_manifest import ResourceManifest


PACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class DesktopPetCharacterResourceService:
    def __init__(
        self,
        *,
        characters_dir: Path,
        public_prefix: str = "/desktop-pet-character-packs",
    ) -> None:
        self.characters_dir = Path(characters_dir)
        self.public_prefix = f"/{str(public_prefix or '').strip('/')}"
        self._cache: dict[str, ResourceManifest] = {}

    def get_manifest(self, character_pack_id: str) -> ResourceManifest | None:
        pack_id = sanitize_character_pack_id(character_pack_id)
        if not pack_id:
            return None

        pack_dir = self._resolve_pack_dir(pack_id)
        if pack_dir is None or not pack_dir.is_dir():
            return None

        assets_dir = pack_dir / "assets"
        if not assets_dir.is_dir():
            return None

        cached = self._cache.get(pack_id)
        if cached is not None and cached.assets_dir == assets_dir:
            return cached

        manifest = ResourceManifest(
            assets_dir,
            public_prefix=f"{self.public_prefix}/{pack_id}/assets",
        )
        self._cache[pack_id] = manifest
        return manifest

    def build_runtime_manifest(self, character_pack_id: str) -> dict | None:
        manifest = self.get_manifest(character_pack_id)
        if manifest is None:
            return None
        manifest.refresh()
        return manifest.build_runtime_manifest()

    def _resolve_pack_dir(self, pack_id: str) -> Path | None:
        base = self.characters_dir.resolve()
        target = (base / pack_id).resolve()
        if target != base and base in target.parents:
            return target
        return None


def sanitize_character_pack_id(value: str) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not PACK_ID_PATTERN.fullmatch(pack_id):
        return ""
    return pack_id
