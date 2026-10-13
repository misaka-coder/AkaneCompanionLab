"""Admit QQ events and resolve their sender, addressing, session and preferences."""
from __future__ import annotations

import re
from typing import Any

from channelcore_onebot import OneBotEventAdmission, normalize_inbound_event

import config
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.inbound.notices import normalize_qq_notice


class QQInboundRuntime:
    """Build the QQ context for a new message or supported notice."""

    def __init__(self, *, gateway: Any, admission: OneBotEventAdmission) -> None:
        self.gateway = gateway
        self._event_admission = admission

    def build_message_context(self, event: dict[str, Any]) -> QQMessageContext:
        if str(event.get("post_type") or "").strip().lower() != "message":
            return self.gateway.build_notice_context(event)

        message_type = str(event.get("message_type") or "").strip().lower()
        is_private = message_type == "private"
        is_group = message_type == "group" or bool(event.get("group_id"))
        if not (is_private or is_group):
            return QQMessageContext(False, "unsupported_message_type")

        user_id = self.gateway._safe_int(event.get("user_id"))
        group_id = self.gateway._safe_int(event.get("group_id"))
        admission = self._admit_event(event)
        if admission is not None:
            return admission

        inbound_result = normalize_inbound_event(
            event,
            bot_account_id=self.gateway.bot_qq,
            wake_words=(),
        )
        inbound = inbound_result.message
        if inbound is None:
            return QQMessageContext(False, inbound_result.reason or "invalid_event")
        raw_message = inbound.raw_text
        clean_message = inbound.text
        attachments = self.gateway._legacy_attachments(inbound.attachments)
        group_vision_enabled = not is_group or self.gateway.is_group_vision_enabled(group_id)
        has_group_image = bool(
            is_group
            and any(
                isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
                for item in attachments
            )
        )
        clean_message = re.sub(r"\s+", " ", clean_message).strip()
        mentions = self.gateway.resolve_mention_labels(
            mentions=inbound.mentions,
            group_id=group_id,
        )
        mention_only = bool(mentions and not clean_message)
        if mention_only:
            clean_message = "event.mention"
        if not clean_message:
            return QQMessageContext(False, "empty_message")
        mentions_bot = inbound.mentioned_bot
        session_id, profile_user_id = self.gateway.resolve_identity(user_id=user_id, group_id=group_id)
        actor_profile_user_id = self.gateway.resolve_actor_profile_user_id(user_id=user_id)
        sender_label = inbound.actor.display_name or self.gateway.resolve_sender_label(event=event, user_id=user_id)
        if sender_label:
            self.gateway.sender_label_cache[self.gateway._sender_label_cache_key(group_id=group_id, user_id=user_id)] = sender_label
        character_pack_id = self.gateway.resolve_character_pack_id(session_id)
        reply_mode = self.gateway.resolve_reply_mode(session_id)
        chat_model_override = self.gateway.resolve_chat_model_override(session_id)

        access_permission_command = self.gateway.parse_access_permission_command(clean_message)
        capability_approval_command = self.gateway.parse_capability_approval_command(clean_message)
        group_emotion_command = self.gateway.parse_group_emotion_command(clean_message)
        group_attention_command = self.gateway.parse_group_attention_command(clean_message)
        group_reason = ""
        if is_group and mentions_bot and access_permission_command is not None:
            group_reason = "qq_access_permission_command"
        elif is_group and mentions_bot and capability_approval_command is not None:
            group_reason = "qq_capability_approval_command"
        elif is_group and mentions_bot and group_emotion_command is not None:
            group_reason = "qq_group_emotion_command"
        elif is_group and mentions_bot and group_attention_command is not None:
            group_reason = "qq_group_attention_command"
        elif is_group:
            if mentions_bot:
                group_reason = "group_mention"
            else:
                suppress_passive_image = bool(has_group_image and not group_vision_enabled)
                unbound_group_image = bool(has_group_image)
                passive_reason = (
                    "group_vision_disabled"
                    if suppress_passive_image
                    else ("group_passive_image_unbound" if unbound_group_image else "group_passive_observed")
                )
                return QQMessageContext(
                    should_respond=False,
                    reason=passive_reason,
                    inbound_message=inbound,
                    # Product policy decides whether the passive group is allowed
                    # into memory.  Inbound preparation materializes media in the background
                    # before recording a real handle, so the protocol event itself
                    # must not be discarded merely because it contains an image.
                    should_record=True,
                    is_group=True,
                    target_id=group_id,
                    user_id=user_id,
                    group_id=group_id,
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    actor_profile_user_id=actor_profile_user_id,
                    clean_message=clean_message,
                    raw_message=raw_message,
                    sender_label=sender_label,
                    character_pack_id=character_pack_id,
                    reply_mode=reply_mode,
                    chat_model_override=chat_model_override,
                    attachments=attachments,
                    source_message_id=inbound.event_id,
                    mentioned_bot=mentions_bot,
                    mentions=mentions,
                )

        return QQMessageContext(
            should_respond=True,
            reason="private" if is_private else group_reason,
            inbound_message=inbound,
            is_group=is_group,
            target_id=group_id if is_group else user_id,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            actor_profile_user_id=actor_profile_user_id,
            clean_message=clean_message,
            raw_message=raw_message,
            sender_label=sender_label,
            character_pack_id=character_pack_id,
            reply_mode=reply_mode,
            chat_model_override=chat_model_override,
            attachments=attachments,
            source_message_id=inbound.event_id,
            mentioned_bot=mentions_bot,
            addressed_to_assistant=bool(is_private or group_reason),
            mentions=mentions,
            extra_context=self.gateway.build_extra_context(
                event=event,
                is_group=is_group,
                user_id=user_id,
                group_id=group_id,
                sender_label=sender_label,
                reply_mode=reply_mode,
                chat_model_override=chat_model_override,
                session_id=session_id,
            ),
        )

    def build_notice_context(self, event: dict[str, Any]) -> QQMessageContext:
        inbound_result = normalize_qq_notice(event, bot_account_id=self.gateway.bot_qq)
        inbound = inbound_result.message
        if inbound is None:
            return QQMessageContext(False, inbound_result.reason or "not_message_event")

        admission = self._admit_event({**event, "user_id": inbound.actor.id})
        if admission is not None:
            return admission

        user_id = self.gateway._safe_int(inbound.actor.id)
        group_id = self.gateway._safe_int(inbound.conversation.id) if inbound.conversation.kind == "group" else 0
        is_group = bool(group_id)
        session_id, profile_user_id = self.gateway.resolve_identity(user_id=user_id, group_id=group_id)
        actor_profile_user_id = self.gateway.resolve_actor_profile_user_id(user_id=user_id)
        sender_label = inbound.actor.display_name or self.gateway.resolve_sender_label(
            event={"group_id": group_id}, user_id=user_id,
        )
        if sender_label:
            self.gateway.sender_label_cache[self.gateway._sender_label_cache_key(group_id=group_id, user_id=user_id)] = sender_label
        character_pack_id = self.gateway.resolve_character_pack_id(session_id)
        reply_mode = self.gateway.resolve_reply_mode(session_id)
        chat_model_override = self.gateway.resolve_chat_model_override(session_id)
        actor_label = "我" if not is_group else (sender_label or (f"QQ {user_id}" if user_id else "这位 QQ 用户"))
        passive_poke = inbound_result.reason == "group_poke_observed"
        if passive_poke:
            poke_target_id = self.gateway._safe_int(event.get("target_id"))
            target_label = self.gateway.resolve_sender_label(event={"group_id": group_id}, user_id=poke_target_id)
            clean_message = (
                f"QQ互动：{sender_label or '群成员'}（QQ {user_id}）戳了戳"
                f"{target_label or '群成员'}（QQ {poke_target_id}）。"
            )
        else:
            clean_message = f"刚才发生的互动：{actor_label}在 QQ 里戳了戳你的头像。"
        return QQMessageContext(
            should_respond=not passive_poke,
            should_record=passive_poke,
            reason="group_poke_observed" if passive_poke else "qq_poke",
            inbound_message=inbound,
            is_group=is_group,
            target_id=group_id if is_group else user_id,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            actor_profile_user_id=actor_profile_user_id,
            clean_message=clean_message,
            raw_message="[QQ戳一戳]",
            sender_label=sender_label,
            character_pack_id=character_pack_id,
            reply_mode=reply_mode,
            chat_model_override=chat_model_override,
            attachments=[],
            source_message_id=inbound.event_id if passive_poke else "",
            addressed_to_assistant=not passive_poke,
            extra_context="" if passive_poke else self.gateway.build_extra_context(
                event=event,
                is_group=is_group,
                user_id=user_id,
                group_id=group_id,
                sender_label=sender_label,
                reply_mode=reply_mode,
                chat_model_override=chat_model_override,
                session_id=session_id,
            )
            + f"\n本轮 QQ 事件：{actor_label}双击头像戳了戳你；{actor_label}就是本轮戳一戳的发送者。",
        )







    def _admit_event(self, event: dict[str, Any]) -> QQMessageContext | None:
        result = self._event_admission.admit(
            event,
            max_age_seconds=getattr(config, "QQ_EVENT_MAX_AGE_SECONDS", 300),
            allow_stale_events=getattr(config, "QQ_ALLOW_STALE_EVENTS", False),
        )
        if result.status == "accepted":
            return None
        reason = "qq_self_id_mismatch" if result.reason == "onebot_self_id_mismatch" else result.reason
        return QQMessageContext(False, reason or result.status)
