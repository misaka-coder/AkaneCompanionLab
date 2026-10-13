"""QQ inbound domain and its per-gateway state."""
from __future__ import annotations
import re
import threading
import time
from dataclasses import replace
from typing import Any
from channelcore_onebot import OneBotEventAdmission, normalize_inbound_event, validate_onebot_identity
import config
from ..qq_group_notices import normalize_qq_notice
from .context import QQMessageContext
from .validation import _safe_character_pack_id, _safe_chat_model_id, _safe_reply_mode


class QQInboundRuntime:
    """Own inbound behavior and state; other operations use the injected port."""

    def __init__(self, *, gateway: Any, admission: OneBotEventAdmission) -> None:
        self.gateway = gateway
        self._event_admission = admission
        self.attachment_debounce_state: dict[str, dict[str, Any]] = {}
        self._attachment_debounce_lock = threading.RLock()

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
                    # into memory.  The route materializes media in the background
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

    def restore_admitted_passive_context(
        self,
        event: dict[str, Any],
        delivery_context: dict[str, Any],
    ) -> QQMessageContext:
        """Restore trusted inbox work, not a new webhook admission.

        The durable inbox already admitted this event. Re-admitting it loses
        media as a duplicate (or stale after restart). Keep the saved product
        routing and use the package normalizer to restore its ordered content.
        """
        stored = self.gateway.context_from_delivery_context(delivery_context)
        if stored is None:
            raise ValueError("queued_context_restore_failed")
        identity = validate_onebot_identity(
            event,
            bot_account_id=self.gateway.bot_qq,
            require_self_id=self.gateway._channel_config.require_self_id if self.gateway._channel_config is not None else False,
        )
        if not identity.ok:
            raise ValueError(identity.reason)
        parsed = (
            normalize_inbound_event(event, bot_account_id=self.gateway.bot_qq, wake_words=())
            if str(event.get("post_type") or "").strip().lower() == "message"
            else normalize_qq_notice(event, bot_account_id=self.gateway.bot_qq)
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
            attachments=self.gateway._legacy_attachments(inbound.attachments),
            mentions=self.gateway.resolve_mention_labels(mentions=inbound.mentions, group_id=stored.group_id),
            mentioned_bot=inbound.mentioned_bot,
        )

    def context_from_delivery_context(self, value: dict[str, Any]) -> QQMessageContext | None:
        if not isinstance(value, dict):
            return None
        target_id = self.gateway._safe_int(value.get("target_id"))
        if not target_id:
            return None
        dialogue_target = None
        if "dialogue_target" in value:
            registry = getattr(self.gateway, "dialogue_registry", None)
            if registry is None:
                raise ValueError("dialogue_registry_unavailable")
            dialogue_target = registry.restore(value["dialogue_target"])
        return QQMessageContext(
            should_respond=True,
            reason="background_task_delivery",
            is_group=bool(value.get("is_group")),
            target_id=target_id,
            user_id=self.gateway._safe_int(value.get("user_id")),
            group_id=self.gateway._safe_int(value.get("group_id")),
            session_id=str(value.get("session_id") or ""),
            profile_user_id=str(value.get("profile_user_id") or ""),
            actor_profile_user_id=(
                str(value.get("actor_profile_user_id") or "").strip()
                or self.gateway.resolve_actor_profile_user_id(user_id=self.gateway._safe_int(value.get("user_id")))
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

    def register_attachment_debounce(
        self,
        context: QQMessageContext,
        *,
        attachment_ids: list[str],
    ) -> dict[str, Any]:
        """Merge bursty QQ attachment events into one final LLM turn.

        Every event still registers its attachments immediately. The debounce
        only decides which HTTP callback is allowed to wake Akane, so older
        image-only events do not cause "I haven't seen it yet" replies.
        """
        normalized_ids = [str(item or "").strip() for item in attachment_ids or [] if str(item or "").strip()]
        delay_seconds = min(5.0, max(0.0, float(getattr(config, "QQ_ATTACHMENT_DEBOUNCE_SECONDS", 1.2) or 0.0)))
        if not normalized_ids or delay_seconds <= 0:
            return {
                "enabled": False,
                "process": True,
                "delay_seconds": 0.0,
                "attachment_ids": normalized_ids,
            }

        now_ts = time.time()
        key = self._attachment_debounce_key(context)
        with self._attachment_debounce_lock:
            self._prune_attachment_debounce_locked(now_ts=now_ts)
            state = self.attachment_debounce_state.get(key)
            generation = int((state or {}).get("generation") or 0) + 1
            merged_ids = list((state or {}).get("attachment_ids") or [])
            for item_id in normalized_ids:
                if item_id not in merged_ids:
                    merged_ids.append(item_id)
            self.attachment_debounce_state[key] = {
                "generation": generation,
                "attachment_ids": merged_ids,
                "updated_at": now_ts,
                "expires_at": now_ts + max(10.0, delay_seconds + 5.0),
            }
        return {
            "enabled": True,
            "process": True,
            "key": key,
            "generation": generation,
            "delay_seconds": delay_seconds,
            "attachment_ids": merged_ids,
        }

    def consume_attachment_debounce(self, token: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(token, dict) or not bool(token.get("enabled")):
            return {
                "process": True,
                "reason": "disabled",
                "attachment_ids": list(token.get("attachment_ids") or []) if isinstance(token, dict) else [],
            }

        key = str(token.get("key") or "").strip()
        generation = int(token.get("generation") or 0)
        with self._attachment_debounce_lock:
            state = self.attachment_debounce_state.get(key)
            if not state:
                return {"process": False, "reason": "debounce_state_missing", "attachment_ids": []}
            if int(state.get("generation") or 0) != generation:
                return {
                    "process": False,
                    "reason": "superseded_by_newer_attachment_event",
                    "attachment_ids": list(state.get("attachment_ids") or []),
                }
            self.attachment_debounce_state.pop(key, None)
            return {
                "process": True,
                "reason": "latest_attachment_event",
                "attachment_ids": list(state.get("attachment_ids") or []),
            }

    def _attachment_debounce_key(self, context: QQMessageContext) -> str:
        # Group memory is shared, but burst merging should stay tied to the
        # sender who is currently feeding Akane attachments.
        return f"{context.session_id or context.target_id}:{context.user_id or 0}"

    def _prune_attachment_debounce_locked(self, *, now_ts: float) -> None:
        stale_keys = [
            key
            for key, state in self.attachment_debounce_state.items()
            if float(state.get("expires_at") or 0.0) <= now_ts
        ]
        for key in stale_keys:
            self.attachment_debounce_state.pop(key, None)

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
