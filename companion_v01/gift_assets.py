from __future__ import annotations

from pathlib import Path
from typing import Any

from .gift_system import GiftSystemService
from .store import MemoryStore


class GiftAssetLibrary:
    def __init__(self, base_dir: Path, *, store: MemoryStore) -> None:
        self.base_dir = Path(base_dir)
        self.store = store
        self.service = GiftSystemService(self.base_dir, store=store)

    def reset(self) -> None:
        self.service.reset()

    def save_audio_gift(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        return self.service.ingest_upload(
            profile_user_id=profile_user_id,
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            content=content,
            now_ts=now_ts,
        )

    def list_assets(
        self,
        *,
        profile_user_id: str,
        media_kind: str = "all",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_media_kind = str(media_kind or "").strip().lower() or "all"
        asset_type = {
            "bgm": "audio",
            "audio": "audio",
            "image": "image",
            "photo": "image",
            "all": None,
        }.get(normalized_media_kind, None)
        return self.service.list_assets(
            profile_user_id=profile_user_id,
            asset_type=asset_type,
            limit=limit,
        )

    def apply_action(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_id: str,
        action: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return self.service.apply_action(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
            action=action,
            timestamp=timestamp,
        )

    def build_internalized_bgm_entries(self, profile_user_id: str) -> list[dict[str, Any]]:
        return self.service.build_internalized_bgm_entries(profile_user_id)
