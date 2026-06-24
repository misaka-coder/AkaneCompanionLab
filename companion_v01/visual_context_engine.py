from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("akane.engine")


def build_resource_manifest(
    engine: Any,
    *,
    profile_user_id: str = "",
    resource_manifest: Any = None,
) -> dict[str, Any]:
    manifest_service = resource_manifest or engine.resource_manifest
    if not manifest_service:
        return {
            "schema_version": 2,
            "scenes": {"majors": []},
            "characters": {"outfits": []},
            "defaults": {},
        }
    manifest_service.refresh()
    runtime_projection = get_user_runtime_projection(engine, profile_user_id)
    return manifest_service.build_runtime_manifest(
        extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
        extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
        extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
    )


def describe_tool_scene_context(engine: Any, visual_payload: dict[str, Any]) -> str:
    if engine.resource_manifest:
        profile_user_id = str(visual_payload.get("_profile_user_id") or "").strip()
        runtime_projection = get_user_runtime_projection(engine, profile_user_id) if profile_user_id else {}
        return engine.resource_manifest.describe_visual_state(
            visual_payload,
            extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
            extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
            extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
        )
    return "当前场景未设置"


def build_current_visual_context(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    current_visual_payload: Any,
    visual_payload: dict[str, Any] | None = None,
    runtime_projection: dict[str, Any] | None = None,
    character_only: bool = False,
    resource_manifest: Any = None,
) -> str:
    manifest_service = resource_manifest or engine.resource_manifest
    if not manifest_service:
        return "当前没有额外的演出状态参考。"

    effective_visual_payload = visual_payload or resolve_current_visual_payload(
        engine,
        session_id=session_id,
        current_visual_payload=current_visual_payload,
    )
    if not effective_visual_payload:
        return "当前没有额外的演出状态参考。"
    effective_runtime_projection = runtime_projection or get_user_runtime_projection(engine, profile_user_id)
    if character_only:
        return manifest_service.describe_character_visual_state(
            effective_visual_payload,
            extra_character_outfits=list(effective_runtime_projection.get("extra_character_outfits") or []),
        )
    return manifest_service.describe_visual_state(
        effective_visual_payload,
        extra_bgm_tracks=list(effective_runtime_projection.get("extra_bgm_tracks") or []),
        extra_scene_groups=list(effective_runtime_projection.get("extra_scene_groups") or []),
        extra_character_outfits=list(effective_runtime_projection.get("extra_character_outfits") or []),
    )


def schedule_visual_observations_for_payload(
    engine: Any,
    *,
    payload: dict[str, Any] | None,
    profile_user_id: str,
    session_id: str,
) -> None:
    if not isinstance(payload, dict):
        return

    visual_payload = coerce_visual_payload(payload)
    runtime_projection = get_user_runtime_projection(engine, profile_user_id)
    if visual_payload:
        try:
            engine.vision_service.schedule_scene_observation(
                visual_payload=visual_payload,
                extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
            )
        except Exception as exc:
            logger.warning("schedule scene observation failed: %s", exc)
        try:
            engine.vision_service.schedule_outfit_observation(
                visual_payload=visual_payload,
                extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
            )
        except Exception as exc:
            logger.warning("schedule outfit observation failed: %s", exc)

    try:
        focused_gift = engine.gift_service.resolve_focus_asset(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id="",
        )
        if focused_gift:
            engine.vision_service.schedule_gift_observation(asset=focused_gift)
    except Exception as exc:
        logger.warning("schedule gift observation failed: %s", exc)


def handle_ready_visual_observation(
    engine: Any,
    target: Any,
    observation: dict[str, Any],
) -> None:
    engine.vision_observation_router.handle(target, observation)


def get_user_runtime_projection(engine: Any, profile_user_id: str) -> dict[str, Any]:
    normalized = str(profile_user_id or "").strip()
    if not normalized:
        return {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }
    return engine.gift_service.build_runtime_projection(profile_user_id=normalized)


def get_user_bgm_tracks(engine: Any, profile_user_id: str) -> list[dict[str, Any]]:
    return list(get_user_runtime_projection(engine, profile_user_id).get("extra_bgm_tracks") or [])


def resolve_current_visual_payload(
    engine: Any,
    *,
    session_id: str,
    current_visual_payload: Any,
) -> dict[str, Any] | None:
    if isinstance(current_visual_payload, dict):
        payload = coerce_visual_payload(current_visual_payload)
        if payload:
            return payload

    latest_eval = engine.store.get_latest_eval_turn(session_id)
    if not latest_eval:
        return None
    final_json = latest_eval.get("final_json")
    if not isinstance(final_json, dict):
        return None
    return coerce_visual_payload(final_json)


def coerce_visual_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    scene = payload.get("scene")
    character = payload.get("character")
    emotion = payload.get("emotion")
    if not isinstance(scene, dict) and not isinstance(character, dict) and emotion is None:
        return None
    return {
        "emotion": str(emotion or ""),
        "character": character if isinstance(character, dict) else {},
        "scene": scene if isinstance(scene, dict) else {},
    }
