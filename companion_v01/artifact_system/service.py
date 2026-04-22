from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from ..store import MemoryStore


VISIBLE_CONTAINER_STATUSES = ("kept", "internalized")
CONTAINER_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "type": "music_box",
        "name": "曲库",
        "description": "她已经收进自己世界里的歌曲，会在合适的时候被拿出来播放。",
        "asset_types": ("audio",),
    },
    {
        "type": "album",
        "name": "相册",
        "description": "她留下来的图片与被她命名过的回忆，会在这里慢慢积累。",
        "asset_types": ("image",),
    },
    {
        "type": "note_box",
        "name": "便签盒",
        "description": "信、纸条、短句和以后可能会留下来的文字礼物，都会待在这里。",
        "asset_types": ("text", "note"),
    },
    {
        "type": "keepsake_box",
        "name": "收藏盒",
        "description": "暂时还不适合细分的东西，会先被她小心地收在这里。",
        "asset_types": ("virtual", "other"),
    },
)


class ArtifactContainerService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        public_path_builder: Callable[[str], str] | None = None,
    ) -> None:
        self.store = store
        self.public_path_builder = public_path_builder or self._default_public_path_builder

    def list_containers(
        self,
        *,
        profile_user_id: str,
        preview_limit: int = 3,
        include_empty: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_preview_limit = max(1, min(12, int(preview_limit or 3)))
        result: list[dict[str, Any]] = []
        for definition in CONTAINER_DEFINITIONS:
            container_type = str(definition["type"])
            total_count = self.store.count_artifacts_by_container(
                profile_user_id=profile_user_id,
                container_type=container_type,
                statuses=list(VISIBLE_CONTAINER_STATUSES),
            )
            if total_count <= 0 and not include_empty:
                continue
            latest_items = self.store.list_artifacts_by_container(
                profile_user_id=profile_user_id,
                container_type=container_type,
                statuses=list(VISIBLE_CONTAINER_STATUSES),
                limit=normalized_preview_limit,
            )
            result.append(
                {
                    "container_type": container_type,
                    "container_name": str(definition["name"]),
                    "description": str(definition["description"]),
                    "asset_types": list(definition.get("asset_types") or []),
                    "total_count": int(total_count),
                    "is_empty": total_count <= 0,
                    "latest_items": [self._decorate_asset(item) for item in latest_items],
                }
            )
        return result

    def list_container_items(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        definition = self._get_definition(container_type)
        if definition is None:
            raise ValueError("unsupported container type")

        normalized_limit = max(1, min(200, int(limit or 50)))
        normalized_container_key = str(container_key or "").strip()
        items = self.store.list_artifacts_by_container(
            profile_user_id=profile_user_id,
            container_type=str(definition["type"]),
            container_key=normalized_container_key or None,
            statuses=list(VISIBLE_CONTAINER_STATUSES),
            limit=normalized_limit,
        )
        total_count = self.store.count_artifacts_by_container(
            profile_user_id=profile_user_id,
            container_type=str(definition["type"]),
            container_key=normalized_container_key or None,
            statuses=list(VISIBLE_CONTAINER_STATUSES),
        )
        return {
            "container_type": str(definition["type"]),
            "container_name": str(definition["name"]),
            "description": str(definition["description"]),
            "container_key": normalized_container_key,
            "total_count": int(total_count),
            "collections": self._list_collections(
                profile_user_id=profile_user_id,
                container_type=str(definition["type"]),
            ),
            "items": [self._decorate_asset(item) for item in items],
        }

    def manage_artifact(
        self,
        *,
        profile_user_id: str,
        session_id: str = "",
        asset_id: str = "",
        action: str,
        display_name: str = "",
        collection_key: str = "",
        collection_name: str = "",
        asset_role: str = "",
        placement_hint: str = "",
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any] | None:
        normalized_action = self._normalize_action(action)
        if not normalized_action:
            raise ValueError("unsupported artifact action")

        asset = self._resolve_asset(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
        )
        if asset is None:
            return None

        now_ts = int(timestamp or time.time())
        payload = dict(asset.get("payload") or {})
        flags = dict(asset.get("artifact_flags") or {})
        source_ids = [source_id] if str(source_id or "").strip() else None

        if normalized_action == "delete":
            flags["world_asset_state"] = "deleted"
            flags["deleted_at"] = now_ts
            flags["deleted_by"] = "manage_artifact"
            updated = self.store.update_gift_asset(
                profile_user_id=profile_user_id,
                asset_id=str(asset.get("asset_id") or ""),
                status="rejected",
                timestamp=now_ts,
                source_ids=source_ids,
                artifact_flags=flags,
                last_decision_at=now_ts,
                last_touched_at=now_ts,
            )
            return self._decorate_asset(updated) if updated else None

        next_display_name = self._normalize_display_name(display_name)
        if not next_display_name and normalized_action == "claim":
            next_display_name = self._normalize_display_name(
                payload.get("seed_name") or asset.get("display_name") or asset.get("origin_name")
            )

        role = self._normalize_asset_role(asset_role or payload.get("asset_role"))
        if normalized_action == "claim" and not role:
            role = "album_photo"

        resolved_collection = self._resolve_collection(
            collection_key=collection_key,
            collection_name=collection_name,
            payload=payload,
            action=normalized_action,
        )

        if normalized_action in {"claim", "move"} and resolved_collection is None:
            raise ValueError("missing artifact collection")
        if normalized_action == "rename" and not next_display_name:
            raise ValueError("missing artifact display_name")

        next_payload = dict(payload)
        next_flags = dict(flags)

        if next_display_name:
            next_payload["display_name_source"] = "akane_confirmed"

        if resolved_collection is not None:
            next_payload["collection_key"] = resolved_collection["collection_key"]
            next_payload["collection_name"] = resolved_collection["collection_name"]
            next_payload["collection_source"] = "akane_confirmed"

        normalized_placement = self._normalize_placement_hint(placement_hint)
        if role:
            next_payload["asset_role"] = role
            next_flags["asset_role"] = role
        if normalized_placement:
            next_payload["placement_hint"] = normalized_placement
            next_flags["placement_hint"] = normalized_placement

        next_status: str | None = None
        is_image_asset = str(asset.get("asset_type") or "").strip().lower() == "image"
        if is_image_asset and role in {"outfit", "expression", "portrait"}:
            next_payload = self._apply_character_projection_payload(
                asset=asset,
                payload=next_payload,
                role=role,
                display_name=next_display_name,
            )
            next_status = "internalized"

        if normalized_action == "claim":
            next_flags["world_asset_state"] = "claimed"
            next_flags.setdefault("claimed_at", now_ts)
            next_flags["claimed_by"] = "manage_artifact"
            if role == "scene" and is_image_asset:
                next_payload["projection_role"] = "scene"
                next_status = "internalized"
            elif role in {"outfit", "expression", "portrait"} and is_image_asset:
                next_status = "internalized"
            elif str(asset.get("status") or "").strip().lower() not in {"kept", "internalized"}:
                next_status = "kept"
        elif normalized_action in {"rename", "move"}:
            next_flags.setdefault("world_asset_state", "claimed")
            next_flags["updated_by"] = "manage_artifact"
            next_flags["updated_at"] = now_ts
            if role == "scene" and is_image_asset:
                next_payload["projection_role"] = "scene"
                next_status = "internalized"
            elif role == "album_photo" and is_image_asset:
                next_payload["projection_role"] = "photo"
                if str(asset.get("status") or "").strip().lower() not in {"kept", "internalized"}:
                    next_status = "kept"

        updated = self.store.update_gift_asset(
            profile_user_id=profile_user_id,
            asset_id=str(asset.get("asset_id") or ""),
            display_name=next_display_name or None,
            status=next_status,
            timestamp=now_ts,
            source_ids=source_ids,
            payload=next_payload,
            container_key="" if resolved_collection is not None else None,
            container_name="" if resolved_collection is not None else None,
            artifact_flags=next_flags,
            last_decision_at=now_ts,
            last_touched_at=now_ts,
        )
        return self._decorate_asset(updated) if updated else None

    def _decorate_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(asset)
        decorated["asset_url"] = self.public_path_builder(str(asset.get("storage_relpath") or ""))
        return decorated

    def _resolve_asset(
        self,
        *,
        profile_user_id: str,
        session_id: str = "",
        asset_id: str = "",
    ) -> dict[str, Any] | None:
        normalized_asset_id = str(asset_id or "").strip()
        if normalized_asset_id:
            return self.store.get_user_media_asset(
                profile_user_id=profile_user_id,
                asset_id=normalized_asset_id,
            )

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None
        session = self.store.get_session(profile_user_id, normalized_session_id)
        focus_asset_id = str((session or {}).get("current_gift_focus_asset_id") or "").strip()
        if not focus_asset_id:
            return None
        focused = self.store.get_user_media_asset(
            profile_user_id=profile_user_id,
            asset_id=focus_asset_id,
        )
        if focused is None or str(focused.get("session_id") or "") != normalized_session_id:
            return None
        return focused

    def _list_collections(
        self,
        *,
        profile_user_id: str,
        container_type: str,
    ) -> list[dict[str, Any]]:
        if str(container_type or "").strip().lower() != "album":
            return []
        return self.store.list_artifact_groups_by_container(
            profile_user_id=profile_user_id,
            container_type=container_type,
            statuses=list(VISIBLE_CONTAINER_STATUSES),
            limit=100,
        )

    def _get_definition(self, container_type: str) -> dict[str, Any] | None:
        normalized = str(container_type or "").strip().lower()
        for definition in CONTAINER_DEFINITIONS:
            if str(definition["type"]) == normalized:
                return dict(definition)
        return None

    def _normalize_action(self, action: str) -> str:
        normalized = str(action or "").strip().lower()
        return {
            "claim": "claim",
            "rename": "rename",
            "move": "move",
            "delete": "delete",
            "remove": "delete",
        }.get(normalized, "")

    def _normalize_asset_role(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "scene": "scene",
            "background": "scene",
            "bg": "scene",
            "outfit": "outfit",
            "clothes": "outfit",
            "costume": "outfit",
            "expression": "expression",
            "emotion": "expression",
            "portrait": "portrait",
            "character": "portrait",
            "album_photo": "album_photo",
            "photo": "album_photo",
            "image": "album_photo",
        }.get(normalized, "")

    def _normalize_display_name(self, value: Any) -> str:
        normalized = str(value or "").strip().strip("《》[](){}\"' ")
        return normalized[:80]

    def _normalize_placement_hint(self, value: Any) -> str:
        return str(value or "").strip()[:80]

    def _resolve_collection(
        self,
        *,
        collection_key: str,
        collection_name: str,
        payload: dict[str, Any],
        action: str,
    ) -> dict[str, str] | None:
        raw_name = str(collection_name or "").strip()
        raw_key = str(collection_key or "").strip()
        if not raw_key and not raw_name and action == "claim":
            raw_key = str(payload.get("seed_collection_key") or payload.get("collection_key") or "").strip()
            raw_name = str(payload.get("seed_collection_name") or payload.get("collection_name") or "").strip()
        elif not raw_key and not raw_name:
            return None

        normalized_key = self._normalize_collection_key(raw_key or raw_name)
        normalized_name = raw_name[:12] if raw_name else str(payload.get("seed_collection_name") or payload.get("collection_name") or "").strip()[:12]
        if not normalized_name:
            normalized_name = "回忆" if normalized_key == "memories" else normalized_key[:12]
        return {
            "collection_key": normalized_key,
            "collection_name": normalized_name,
        }

    def _apply_character_projection_payload(
        self,
        *,
        asset: dict[str, Any],
        payload: dict[str, Any],
        role: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        next_payload = dict(payload)
        collection_key = str(next_payload.get("collection_key") or "").strip()
        collection_name = str(next_payload.get("collection_name") or "").strip()
        asset_name = self._normalize_display_name(
            display_name or asset.get("display_name") or asset.get("origin_name")
        )

        outfit_id = self._resolve_character_outfit_id(
            collection_key=collection_key,
            collection_name=collection_name,
            asset_name=asset_name,
        )
        outfit_name = collection_name or outfit_id
        emotion_id = self._resolve_character_emotion_id(role=role, asset_name=asset_name)
        emotion_name = asset_name if role == "expression" else "默认"

        next_payload["projection_role"] = "character"
        next_payload["character_asset_role"] = role
        next_payload["character_outfit_id"] = outfit_id
        next_payload["character_outfit_name"] = outfit_name
        next_payload["character_emotion_id"] = emotion_id
        next_payload["character_emotion_name"] = emotion_name
        return next_payload

    def _resolve_character_outfit_id(
        self,
        *,
        collection_key: str,
        collection_name: str,
        asset_name: str,
    ) -> str:
        normalized_key = str(collection_key or "").strip()
        normalized_name = str(collection_name or "").strip()
        if normalized_key and not normalized_key.startswith("collection_"):
            return normalized_key[:64]
        if normalized_name:
            return normalized_name[:64]
        if normalized_key:
            return normalized_key[:64]
        return (asset_name or "custom_outfit")[:64]

    def _resolve_character_emotion_id(self, *, role: str, asset_name: str) -> str:
        if role in {"outfit", "portrait"}:
            return "normal"
        normalized = str(asset_name or "").strip().strip("《》[](){}\"' ")
        if not normalized:
            return "normal"
        return normalized[:64]

    def _normalize_collection_key(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        safe_chars = []
        for char in raw:
            if char.isascii() and char.isalnum():
                safe_chars.append(char)
            elif char in {"_", "-"}:
                safe_chars.append("_")
        normalized = "".join(safe_chars).strip("_-")
        if normalized:
            return normalized[:64]
        if raw:
            digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
            return f"collection_{digest}"
        return "memories"

    def _default_public_path_builder(self, storage_relpath: str) -> str:
        normalized = str(storage_relpath or "").strip().replace("\\", "/").lstrip("/")
        return f"/user-assets/{normalized}" if normalized else ""
