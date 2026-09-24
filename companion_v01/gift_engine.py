from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger("akane.engine")


def list_gift_assets(
    engine: Any,
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
    return engine.gift_service.list_assets(
        profile_user_id=profile_user_id,
        asset_type=asset_type,
        limit=limit,
    )


def upload_gift_asset(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    filename: str,
    content_type: str,
    content: bytes,
    now_ts: int | None = None,
) -> dict[str, Any]:
    asset = engine.gift_service.ingest_upload(
        profile_user_id=profile_user_id,
        session_id=session_id,
        filename=filename,
        content_type=content_type,
        content=content,
        now_ts=now_ts,
    )
    if str(asset.get("asset_type") or "").strip().lower() == "image":
        try:
            engine.vision_service.schedule_gift_observation(asset=asset)
        except Exception as exc:
            logger.warning("schedule gift observation after upload failed: %s", exc)
    return asset


def apply_gift_action(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str | None = None,
    asset_id: str,
    action: str,
    timestamp: int | None = None,
) -> dict[str, Any] | None:
    return engine.gift_service.apply_action(
        profile_user_id=profile_user_id,
        session_id=session_id,
        asset_id=asset_id,
        action=action,
        timestamp=timestamp,
    )


def observe_gift_image_once(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    asset_id: str,
    timestamp: int | None = None,
) -> dict[str, Any] | None:
    asset = engine.gift_service.resolve_focus_asset(
        profile_user_id=profile_user_id,
        session_id=session_id,
        asset_id=asset_id,
    )
    if asset is None:
        return None
    if str(asset.get("asset_type") or "").strip().lower() != "image":
        raise ValueError("only image gifts can be observed without saving")

    observation = engine.vision_service.analyze_gift_once(asset=asset)
    assistant_line = engine.gift_service.build_transient_image_reply(
        asset=asset,
        observation=observation,
    )
    discarded = engine.gift_service.discard_asset(
        profile_user_id=profile_user_id,
        session_id=session_id,
        asset_id=str(asset.get("asset_id") or asset_id),
        timestamp=timestamp,
    )
    return {
        "assistant_line": assistant_line,
        "asset": discarded or asset,
        "observation": dict((observation or {}).get("observation") or {}),
    }


def list_gift_inventory(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    scope: str = "pending_recent",
    limit: int = 5,
) -> dict[str, Any]:
    return engine.gift_service.list_inventory(
        profile_user_id=profile_user_id,
        session_id=session_id,
        scope=scope,
        limit=limit,
    )


def list_artifact_containers(
    engine: Any,
    *,
    profile_user_id: str,
    preview_limit: int = 3,
    include_empty: bool = True,
) -> list[dict[str, Any]]:
    return engine.artifact_service.list_containers(
        profile_user_id=profile_user_id,
        preview_limit=preview_limit,
        include_empty=include_empty,
    )


def list_artifacts_in_container(
    engine: Any,
    *,
    profile_user_id: str,
    container_type: str,
    container_key: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return engine.artifact_service.list_container_items(
        profile_user_id=profile_user_id,
        container_type=container_type,
        container_key=container_key,
        limit=limit,
    )
