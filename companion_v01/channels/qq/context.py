"""Immutable admitted QQ context and its turn/delivery projections."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from channelcore_onebot import ForwardRef, InboundMessage, MentionRef, MessageChain

from companion_v01.channels.qq.constants import QQ_TEXT_CAPABILITIES
from companion_v01.channels.qq.validation import _safe_character_pack_id, _safe_chat_model_id, _safe_reply_mode


@dataclass(frozen=True)
class QQMessageContext:
    should_respond: bool
    reason: str
    inbound_message: InboundMessage | None = field(default=None, repr=False, compare=False)
    should_record: bool = False
    is_group: bool = False
    target_id: int = 0
    user_id: int = 0
    group_id: int = 0
    session_id: str = ""
    profile_user_id: str = ""
    actor_profile_user_id: str = ""
    clean_message: str = ""
    raw_message: str = ""
    extra_context: str = ""
    sender_label: str = ""
    character_pack_id: str = ""
    dialogue_target: Any = field(default=None, repr=False, compare=False)
    reply_mode: str = ""
    chat_model_override: str = ""
    attachments: list[dict[str, Any]] | None = None
    source_message_id: str = ""
    mentioned_bot: bool = False
    addressed_to_assistant: bool = False
    reply_reference: dict[str, Any] | None = None
    mentions: tuple[MentionRef, ...] = ()
    forward_references: tuple[dict[str, Any], ...] = ()

    @property
    def message_chain(self) -> MessageChain:
        return self.inbound_message.chain if self.inbound_message is not None else MessageChain()

    @property
    def forward_refs(self) -> tuple[ForwardRef, ...]:
        return self.inbound_message.forwards if self.inbound_message is not None else ()

    @property
    def verified_forward_ids(self) -> tuple[str, ...]:
        """Only forward references bound to this turn or its verified quote."""
        conversation_kind = "group" if self.is_group else "private"
        conversation_id = str(self.target_id)
        if not self.target_id or (self.is_group and self.group_id != self.target_id):
            return ()
        source_ids = {self.source_message_id} if self.source_message_id else set()
        quote = self.reply_reference or {}
        if (
            quote.get("message_id")
            and quote.get("conversation_kind") == conversation_kind
            and str(quote.get("conversation_id")) == conversation_id
        ):
            source_ids.add(str(quote.get("message_id") or ""))
        ids = {
            str(ref.get("forward_id") or "").strip()
            for ref in self.forward_references
            if ref.get("source_message_id") and ref.get("source_message_id") in source_ids
            and ref.get("conversation_kind") == conversation_kind
            and str(ref.get("conversation_id")) == conversation_id
        }
        inbound = self.inbound_message
        if (
            inbound is not None
            and inbound.event_id == self.source_message_id
            and inbound.conversation.kind == conversation_kind
            and inbound.conversation.id == conversation_id
        ):
            ids.update(ref.forward_id for ref in inbound.forwards)
        return tuple(sorted(value for value in ids if value))

    def to_turn_payload(self) -> dict[str, Any]:
        message = self.clean_message
        mention_labels = {
            str(mention.target_id or "").strip(): (
                str(mention.display_name or "").strip() or ("助手" if mention.is_bot else "")
            )
            for mention in self.mentions
            if str(mention.target_id or "").strip()
        }
        memory_message = (
            self.message_chain.render_text_with_mentions(
                mention_labels=mention_labels,
                bot_label="助手",
            )
            if self.message_chain.parts
            else self.clean_message
        )
        memory_message = memory_message or self.clean_message
        if self.inbound_message is not None and self.inbound_message.event_kind == "poke":
            # A poke's unknown segment has no participants; keep the resolved
            # interaction rather than an unsupported-segment placeholder.
            memory_message = self.clean_message
        if self.dialogue_target is not None:
            memory_message = self.clean_message
        if self.is_group:
            label = self.sender_label or (f"QQ {self.user_id}" if self.user_id else "群成员")
            message = f"【{label}】{message}"
        payload = {
            "user_id": self.session_id,
            "real_user_id": self.profile_user_id,
            "message": message,
            "memory_message": memory_message,
            "source_message_id": str(self.source_message_id or "").strip(),
            "client_mode": "qq_text",
            "client_capabilities": list(QQ_TEXT_CAPABILITIES),
            "extra_context": self.extra_context,
            "qq_delivery_context": self.to_delivery_context(),
        }
        if self.is_group or self.reply_reference:
            payload["message_addressing"] = self._message_addressing()
        if self.forward_references:
            payload["forward_references"] = [dict(item) for item in self.forward_references]
        if self.actor_profile_user_id:
            payload["actor_profile_user_id"] = self.actor_profile_user_id
        if self.user_id:
            payload["actor_stable_id"] = f"qq:{self.user_id}"
            payload["actor_display_name"] = self.sender_label
            payload["actor_platform"] = "qq"
        character_pack_id = _safe_character_pack_id(self.character_pack_id)
        if self.dialogue_target is not None:
            character_pack_id = self.dialogue_target.memory_domain
        if character_pack_id:
            payload["character_pack_id"] = character_pack_id
        reply_mode = _safe_reply_mode(self.reply_mode, default="")
        if reply_mode:
            payload["qq_reply_mode"] = reply_mode
        chat_model_override = _safe_chat_model_id(self.chat_model_override)
        if chat_model_override:
            payload["chat_model_override"] = chat_model_override
        return payload

    def _message_addressing(self) -> dict[str, Any]:
        mentions = [
            {
                "actor_id": "assistant" if mention.is_bot else f"qq:{mention.target_id}",
                "display_name": mention.display_name,
                "is_assistant": bool(mention.is_bot),
            }
            for mention in self.mentions
            if str(mention.target_id or "").strip()
        ]
        addressed_to_assistant = bool(self.addressed_to_assistant)
        reply_reference = dict(self.reply_reference or {})
        reply_actor_id = str(reply_reference.get("actor_id") or "").strip()
        reply_actor_name = str(reply_reference.get("actor_display_name") or "").strip()
        primary_target = (
            {"actor_id": "assistant", "display_name": ""}
            if addressed_to_assistant
            else (
                {
                    "actor_id": str(mentions[0].get("actor_id") or ""),
                    "display_name": str(mentions[0].get("display_name") or ""),
                }
                if len(mentions) == 1 and mentions[0]["actor_id"] != "qq:all"
                else {"actor_id": reply_actor_id, "display_name": reply_actor_name}
                if not mentions and reply_actor_id
                else {}
            )
        )
        result = {
            "mode": "current_request" if self.should_respond else "observed",
            "trigger": str(self.reason or ""),
            "addressed_to_assistant": addressed_to_assistant,
            "explicit_assistant_mention": bool(self.mentioned_bot),
            "primary_target": primary_target,
            "mentions": mentions,
        }
        if reply_reference:
            result["reply_reference"] = reply_reference
        return result

    def to_delivery_context(self) -> dict[str, Any]:
        payload = {
            "is_group": bool(self.is_group),
            "target_id": int(self.target_id or 0),
            "user_id": int(self.user_id or 0),
            "group_id": int(self.group_id or 0),
            "session_id": self.session_id,
            "profile_user_id": self.profile_user_id,
            "clean_message": self.clean_message,
            "raw_message": self.raw_message,
            "sender_label": self.sender_label,
            "source_message_id": self.source_message_id,
        }
        if self.reply_reference:
            payload["reply_reference"] = dict(self.reply_reference)
        if self.forward_references:
            # Delivery/queued tool context needs source proofs, not duplicate node bodies.
            payload["forward_references"] = [
                {key: item[key] for key in (
                    "forward_id", "source_message_id", "conversation_kind", "conversation_id",
                ) if key in item}
                for item in self.forward_references
            ]
        if self.user_id:
            payload["actor_stable_id"] = f"qq:{self.user_id}"
            if self.actor_profile_user_id:
                payload["actor_profile_user_id"] = self.actor_profile_user_id
            payload["actor_display_name"] = self.sender_label
            payload["actor_platform"] = "qq"
        character_pack_id = _safe_character_pack_id(self.character_pack_id)
        if self.dialogue_target is not None:
            character_pack_id = self.dialogue_target.memory_domain
            payload["dialogue_target"] = self.dialogue_target.to_dict()
        if character_pack_id:
            payload["character_pack_id"] = character_pack_id
        reply_mode = _safe_reply_mode(self.reply_mode, default="")
        if reply_mode:
            payload["reply_mode"] = reply_mode
        chat_model_override = _safe_chat_model_id(self.chat_model_override)
        if chat_model_override:
            payload["chat_model_override"] = chat_model_override
        return payload
