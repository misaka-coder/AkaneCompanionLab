"""Rebuild a QQ delivery target from a saved background-task envelope."""
from __future__ import annotations

from typing import Any

from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.validation import _safe_character_pack_id, _safe_chat_model_id, _safe_reply_mode


def context_from_delivery_context(gateway: Any, value: dict[str, Any]) -> QQMessageContext | None:
    if not isinstance(value, dict):
        return None
    target_id = gateway._safe_int(value.get("target_id"))
    if not target_id:
        return None
    dialogue_target = None
    if "dialogue_target" in value:
        registry = getattr(gateway, "dialogue_registry", None)
        if registry is None:
            raise ValueError("dialogue_registry_unavailable")
        dialogue_target = registry.restore(value["dialogue_target"])
    return QQMessageContext(
        should_respond=True,
        reason="background_task_delivery",
        is_group=bool(value.get("is_group")),
        target_id=target_id,
        user_id=gateway._safe_int(value.get("user_id")),
        group_id=gateway._safe_int(value.get("group_id")),
        session_id=str(value.get("session_id") or ""),
        profile_user_id=str(value.get("profile_user_id") or ""),
        actor_profile_user_id=(
            str(value.get("actor_profile_user_id") or "").strip()
            or gateway.resolve_actor_profile_user_id(user_id=gateway._safe_int(value.get("user_id")))
        ),
        clean_message=str(value.get("clean_message") or ""),
        raw_message=str(value.get("raw_message") or ""),
        sender_label=str(value.get("sender_label") or ""),
        character_pack_id=(dialogue_target.memory_domain if dialogue_target is not None else
                           _safe_character_pack_id(value.get("character_pack_id") or value.get("characterPackId"))),
        dialogue_target=dialogue_target,
        reply_mode=_safe_reply_mode(value.get("reply_mode") or value.get("replyMode"), default=""),
        chat_model_override=_safe_chat_model_id(value.get("chat_model_override") or value.get("chatModelOverride")),
        attachments=[],
        source_message_id=str(value.get("source_message_id") or value.get("sourceMessageId") or "").strip(),
        reply_reference=(
            dict(value.get("reply_reference")) if isinstance(value.get("reply_reference"), dict) else None
        ),
        forward_references=tuple(
            dict(item) for item in list(value.get("forward_references") or []) if isinstance(item, dict)
        ),
    )
