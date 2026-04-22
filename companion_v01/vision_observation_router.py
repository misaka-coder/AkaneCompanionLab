from __future__ import annotations

import logging
import time
from typing import Any

from .store import MemoryStore
from .vision_service import VisionTarget


logger = logging.getLogger("akane.vision_router")


class VisionObservationRouter:
    """Apply completed vision observations to their owning subsystem.

    VisionObservationService owns perception and caching. This router owns the
    side effects: an attachment image becomes a ready/failed inbox item, while a
    gift image receives observation metadata through the gift service.
    """

    def __init__(self, *, store: MemoryStore, gift_service: Any, attachment_service: Any | None = None) -> None:
        self.store = store
        self.gift_service = gift_service
        self.attachment_service = attachment_service

    def handle(self, target: VisionTarget, observation: dict[str, Any]) -> None:
        observation_type = str(getattr(target, "observation_type", "") or "").strip()
        if observation_type == "attachment_image":
            self._handle_attachment_image(target=target, observation=observation)
            return
        if observation_type == "gift":
            self._handle_gift_image(target=target, observation=observation)
            return
        logger.debug("Ignoring vision observation type with no side effect: %s", observation_type)

    def _handle_attachment_image(self, *, target: VisionTarget, observation: dict[str, Any]) -> None:
        profile_user_id = str(getattr(target, "owner_profile_user_id", "") or "").strip()
        session_id = str(getattr(target, "owner_session_id", "") or "").strip()
        attachment_id = str(getattr(target, "target_id", "") or "").strip()
        if not profile_user_id or not session_id or not attachment_id:
            logger.warning("Attachment image observation is missing owner ids: target=%s", target)
            return

        status = self._status(observation)
        if status != "ready":
            self.store.update_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=attachment_id,
                status="failed",
                error_message=str(observation.get("error_message") or "视觉观察失败。")[:500],
                updated_at=int(time.time()),
            )
            return

        card = observation.get("observation") if isinstance(observation.get("observation"), dict) else {}
        summary = str(card.get("summary") or observation.get("summary") or "").strip()
        summary_title = (
            str(card.get("summary_title") or "").strip()
            or str(getattr(target, "title", "") or "").strip()
            or "图片附件"
        )
        timestamp = int(time.time())
        attachment_service = getattr(self, "attachment_service", None)
        if attachment_service is not None and hasattr(attachment_service, "mark_ready"):
            attachment_service.mark_ready(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=attachment_id,
                summary_title=summary_title,
                short_hint=summary,
                detail=card,
                timestamp=timestamp,
            )
            return

        self.store.update_attachment_inbox_item(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_id=attachment_id,
            status="ready",
            summary_title=summary_title,
            short_hint=summary,
            detail=card,
            updated_at=timestamp,
        )

    def _handle_gift_image(self, *, target: VisionTarget, observation: dict[str, Any]) -> None:
        if self._status(observation) != "ready":
            return
        profile_user_id = str(getattr(target, "owner_profile_user_id", "") or "").strip()
        asset_id = str(getattr(target, "target_id", "") or "").strip()
        if not profile_user_id or not asset_id:
            logger.warning("Gift image observation is missing owner ids: target=%s", target)
            return
        self.gift_service.apply_image_observation_metadata(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
            observation=observation,
            timestamp=int(time.time()),
        )

    def _status(self, observation: dict[str, Any]) -> str:
        return str(observation.get("status") or "").strip().lower() if isinstance(observation, dict) else ""
