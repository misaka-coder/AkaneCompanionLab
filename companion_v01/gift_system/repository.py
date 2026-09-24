from __future__ import annotations

from typing import Any

from ..store import MemoryStore


class GiftRepository:
    def __init__(self, *, store: MemoryStore) -> None:
        self.store = store

    def create_asset(self, **kwargs: Any) -> dict[str, Any]:
        return self.store.add_gift_asset(**kwargs)

    def get_asset(self, *, profile_user_id: str, asset_id: str) -> dict[str, Any] | None:
        return self.store.get_gift_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
        )

    def delete_asset(self, *, profile_user_id: str, asset_id: str) -> dict[str, Any] | None:
        return self.store.delete_gift_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
        )

    def list_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.store.list_gift_assets(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_type=asset_type,
            status=status,
            statuses=statuses,
            limit=limit,
        )

    def count_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        statuses: list[str] | None = None,
    ) -> int:
        return self.store.count_gift_assets(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_type=asset_type,
            statuses=statuses,
        )

    def update_asset(self, **kwargs: Any) -> dict[str, Any] | None:
        return self.store.update_gift_asset(**kwargs)
