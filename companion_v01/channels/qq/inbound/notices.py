"""Observe member interactions that the reply-oriented OneBot parser ignores."""
from __future__ import annotations

import hashlib
from typing import Any

from channelcore_onebot import (
    ActorRef,
    ConversationRef,
    InboundMessage,
    InboundParseResult,
    MessageChain,
    UnknownPart,
    event_fingerprints,
    normalize_event_timestamp,
    normalize_inbound_event,
    safe_int,
)


def normalize_qq_notice(event: dict[str, Any], *, bot_account_id: str) -> InboundParseResult:
    parsed = normalize_inbound_event(event, bot_account_id=bot_account_id, wake_words=())
    if parsed.message is not None or parsed.reason != "poke_not_for_bot":
        return parsed
    group_id = safe_int(event.get("group_id"))
    target_id = safe_int(event.get("target_id"))
    candidates = [safe_int(event.get(key)) for key in ("operator_id", "sender_id", "user_id")]
    actor_id = next((value for value in candidates if value > 0 and value != target_id), 0)
    actor_id = actor_id or next((value for value in candidates if value > 0), 0)
    if group_id <= 0 or target_id <= 0 or actor_id <= 0:
        return parsed

    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    if sender.get("user_id") and safe_int(sender["user_id"]) != actor_id:
        sender = {}
    event_id = str(event.get("message_id") or "").strip()
    if not event_id:
        # Notices often have only second-resolution time. Keep different
        # participants separate in plugin receipts and queued restoration.
        keys = event_fingerprints({**event, "user_id": actor_id}, bot_account_id=bot_account_id)
        event_id = "qq-poke:" + hashlib.sha256(keys[-1].encode("utf-8")).hexdigest()[:24]
    message = InboundMessage(
        bot_account_id=str(bot_account_id or event.get("self_id") or "").strip(),
        event_id=event_id,
        conversation=ConversationRef(kind="group", id=str(group_id)),
        actor=ActorRef(
            id=str(actor_id), display_name=str(sender.get("card") or sender.get("nickname") or "").strip(),
        ),
        chain=MessageChain(parts=(
            UnknownPart(
                segment_type="poke", segment_index=1, source_message_id="",
                source_event_id=event_id, raw_fragment="[QQ戳一戳]",
            ),
        )),
        timestamp=normalize_event_timestamp(event),
        trigger_reason="group_poke_observed",
        event_kind="poke",
    )
    return InboundParseResult(True, "accepted", "group_poke_observed", message)
