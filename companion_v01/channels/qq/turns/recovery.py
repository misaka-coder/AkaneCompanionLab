"""Restore admitted inbox observations without admitting the event again."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from channelcore_onebot import normalize_inbound_event, validate_onebot_identity

from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.inbound.notices import normalize_qq_notice


def restore_admitted_passive_context(
    gateway: Any,
    event: dict[str, Any],
    delivery_context: dict[str, Any],
) -> QQMessageContext:
    """Restore trusted inbox work, not a new webhook admission.

    The durable inbox already admitted this event. Re-admitting it loses
    media as a duplicate (or stale after restart). Keep the saved product
    routing and use the package normalizer to restore its ordered content.
    """
    stored = gateway.context_from_delivery_context(delivery_context)
    if stored is None:
        raise ValueError("queued_context_restore_failed")
    identity = validate_onebot_identity(
        event,
        bot_account_id=gateway.bot_qq,
        require_self_id=gateway._channel_config.require_self_id if gateway._channel_config is not None else False,
    )
    if not identity.ok:
        raise ValueError(identity.reason)
    parsed = (
        normalize_inbound_event(event, bot_account_id=gateway.bot_qq, wake_words=())
        if str(event.get("post_type") or "").strip().lower() == "message"
        else normalize_qq_notice(event, bot_account_id=gateway.bot_qq)
    )
    inbound = parsed.message
    if inbound is None:
        raise ValueError("queued_context_invalid_event")
    if (
        inbound.conversation.kind != ("group" if stored.is_group else "private")
        or inbound.conversation.id != str(stored.target_id)
        or (stored.is_group and stored.group_id != stored.target_id)
        or inbound.actor.id != str(stored.user_id)
        or inbound.event_id != stored.source_message_id
    ):
        raise ValueError("queued_context_source_mismatch")
    return replace(
        stored,
        should_respond=False,
        should_record=True,
        reason="queued_passive_restored",
        inbound_message=inbound,
        attachments=gateway._legacy_attachments(inbound.attachments),
        mentions=gateway.resolve_mention_labels(mentions=inbound.mentions, group_id=stored.group_id),
        mentioned_bot=inbound.mentioned_bot,
    )
