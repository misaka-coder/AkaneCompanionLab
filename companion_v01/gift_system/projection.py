from __future__ import annotations

from typing import Any

from .repository import GiftRepository
from .processors.base import BaseGiftProcessor


class GiftProjection:
    def __init__(
        self,
        *,
        repository: GiftRepository,
        processors: dict[str, BaseGiftProcessor],
        public_path_builder,
    ) -> None:
        self.repository = repository
        self.processors = dict(processors)
        self.public_path_builder = public_path_builder

    def build_runtime_projection(self, *, profile_user_id: str) -> dict[str, Any]:
        records = self.repository.list_assets(
            profile_user_id=profile_user_id,
            status="internalized",
            limit=200,
        )
        extra_bgm_tracks: list[dict[str, Any]] = []
        extra_scene_groups: list[dict[str, Any]] = []
        extra_character_outfits: list[dict[str, Any]] = []
        for record in records:
            processor = self.processors.get(str(record.get("asset_type") or ""))
            if processor is None:
                continue
            projection = processor.build_runtime_projection(
                record,
                public_asset_url=self.public_path_builder(str(record.get("storage_relpath") or "")),
            )
            bgm_track = projection.get("bgm_track")
            if isinstance(bgm_track, dict):
                extra_bgm_tracks.append(bgm_track)
            scene_group = projection.get("scene_group")
            if isinstance(scene_group, dict):
                extra_scene_groups.append(scene_group)
            character_outfit = projection.get("character_outfit")
            if isinstance(character_outfit, dict):
                extra_character_outfits.append(character_outfit)
        return {
            "extra_bgm_tracks": extra_bgm_tracks,
            "extra_scene_groups": extra_scene_groups,
            "extra_character_outfits": extra_character_outfits,
        }
