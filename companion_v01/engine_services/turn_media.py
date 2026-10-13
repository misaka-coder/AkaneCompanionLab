"""Prepare current input media without changing its dialogue or memory identity."""

from __future__ import annotations

from typing import Any

from .. import desktop_pet_engine
from ..client_protocol import ClientMode, ClientProtocolContext


def prepare_turn_media(
    engine: Any,
    *,
    payload: dict[str, Any],
    client_context: ClientProtocolContext,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    chat_model_override: str,
    extra_user_context: str,
) -> tuple[str, list[dict[str, Any]], Any]:
    """Resolve attachments and images, then select the matching model target."""
    self = engine
    turn_character_pack_id = character_pack_id
    turn_extra_user_context = extra_user_context
    if client_context.effective_mode == ClientMode.DESKTOP_PET and "current_attachment_ids" in payload:
        attachments = desktop_pet_engine.prepare_desktop_turn_attachments(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            attachment_ids=payload.get("current_attachment_ids"),
            chat_model_override=chat_model_override,
        )
        payload["current_attachment_ids"] = attachments["attachment_ids"]
        payload["native_user_images"] = attachments["images"]
        turn_extra_user_context = self._merge_extra_user_context(
            turn_extra_user_context,
            attachments["context"],
        )
    native_user_images = self._extract_native_user_images(payload)
    if native_user_images:
        turn_extra_user_context = self._merge_extra_user_context(
            turn_extra_user_context,
            self._build_native_user_image_prompt_context(native_user_images),
        )
    desktop_screen_images = self._extract_desktop_screen_frame_images(payload)
    if desktop_screen_images:
        turn_extra_user_context = self._merge_extra_user_context(
            turn_extra_user_context,
            self._build_desktop_screen_frame_prompt_context(desktop_screen_images),
        )
    turn_user_images = [*native_user_images, *desktop_screen_images][:5]
    turn_execution_target = self._resolve_turn_execution_target(
        has_real_images=bool(turn_user_images),
        chat_model_override=chat_model_override,
    )
    return turn_extra_user_context, turn_user_images, turn_execution_target
