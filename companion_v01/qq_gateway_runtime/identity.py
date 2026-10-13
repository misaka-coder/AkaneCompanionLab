"""QQ identity domain and its per-gateway state."""
from __future__ import annotations
import threading
import time
from dataclasses import replace
from typing import Any
from channelcore_onebot import (
    MentionRef,
    message_mentions_bot as onebot_message_mentions_bot,
    normalize_wake_words as _normalize_qq_wake_words,
)
from .validation import _safe_reply_mode


class QQIdentityRuntime:
    """Own identity behavior and state; other operations use the injected port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway
        self.sender_label_cache: dict[str, str] = {}
        self._bot_nickname = ""
        self._bot_nickname_status = "not_checked"
        self._bot_nickname_refresh_at = 0.0
        self._bot_nickname_lock = threading.RLock()
        self._bot_group_labels: dict[tuple[str, int], tuple[float, str]] = {}
        self._delivery_notes: dict[str, list[str]] = {}
        self._delivery_notes_lock = threading.RLock()

    def message_mentions_bot(self, event: dict[str, Any], raw_message: str) -> bool:
        return onebot_message_mentions_bot(event, raw_message, bot_account_id=self.gateway.bot_qq)

    def resolve_identity(self, *, user_id: int, group_id: int = 0) -> tuple[str, str]:
        user_text = str(user_id or "")
        if group_id:
            shared_group_id = f"qq_group_shared_{group_id}"
            return shared_group_id, shared_group_id
        if self.gateway.master_qq and user_text == self.gateway.master_qq:
            return "master", "master"
        return f"qq_pri_{user_id}", f"qq_{user_id}"

    def resolve_actor_profile_user_id(self, *, user_id: int) -> str:
        user_text = str(user_id or "")
        if self.gateway.master_qq and user_text == self.gateway.master_qq:
            return "master"
        return f"qq_{user_id}" if user_id else ""

    def resolve_sender_label(self, *, event: dict[str, Any], user_id: int) -> str:
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        nickname = str(sender.get("card") or sender.get("nickname") or "").strip()
        if nickname:
            group_id = self.gateway._safe_int(event.get("group_id"))
            cache_key = self._sender_label_cache_key(group_id=group_id, user_id=user_id)
            if cache_key:
                self.sender_label_cache[cache_key] = nickname
            return nickname
        group_id = self.gateway._safe_int(event.get("group_id"))
        cache_key = self._sender_label_cache_key(group_id=group_id, user_id=user_id)
        if cache_key:
            cached = str(self.sender_label_cache.get(cache_key) or "").strip()
            if cached:
                return cached
        if group_id and user_id:
            remote_label = self.gateway.lookup_group_member_label(group_id=group_id, user_id=user_id)
            if remote_label:
                if cache_key:
                    self.sender_label_cache[cache_key] = remote_label
                return remote_label
        return f"QQ {user_id}" if user_id else "群成员"

    def resolve_mention_labels(
        self,
        *,
        mentions: tuple[MentionRef, ...],
        group_id: int,
    ) -> tuple[MentionRef, ...]:
        if not mentions:
            return tuple(mentions)
        resolved: list[MentionRef] = []
        for mention in mentions:
            target_text = str(mention.target_id or "").strip()
            target_id = self.gateway._safe_int(target_text)
            display_name = str(mention.display_name or "").strip()
            if mention.is_bot:
                resolved.append(replace(mention, display_name=self.gateway._resolve_bot_group_label(group_id)))
                continue
            cache_key = self._sender_label_cache_key(group_id=group_id, user_id=target_id)
            if display_name and cache_key:
                self.sender_label_cache[cache_key] = display_name
            if not display_name and cache_key:
                display_name = str(self.sender_label_cache.get(cache_key) or "").strip()
            if not display_name and target_id:
                display_name = self.gateway.lookup_group_member_label(
                    group_id=group_id,
                    user_id=target_id,
                )
                if display_name and cache_key:
                    self.sender_label_cache[cache_key] = display_name
            resolved.append(
                MentionRef(
                    target_id=target_text,
                    display_name=display_name,
                    is_bot=False,
                )
            )
        return tuple(resolved)

    def _resolve_bot_group_label(self, group_id: int) -> str:
        """Resolve the bound account's group card, never an inbound @ label."""
        if not group_id or not self.gateway.bridge_enabled or not self.gateway.bot_qq:
            return ""
        key = (str(self.gateway.bot_qq), int(group_id))
        with self._bot_nickname_lock:
            now = time.monotonic()
            cached = self._bot_group_labels.get(key)
            if cached is not None and now < cached[0]:
                return cached[1]
            result = self.gateway._onebot_transport.call(
                "get_group_member_info",
                {"group_id": group_id, "user_id": int(self.gateway.bot_qq), "no_cache": True},
                timeout=2,
            )
            label = ""
            if result.ok and str(result.data.get("user_id") or "").strip() == str(self.gateway.bot_qq):
                # Some OneBot implementations omit group_id in this response.
                returned_group = result.data.get("group_id")
                if returned_group is None or str(returned_group) == str(group_id):
                    candidate = str(result.data.get("card") or "").strip() or str(
                        result.data.get("nickname") or ""
                    ).strip()
                    if candidate and len(candidate) <= 160 and not any(ord(ch) < 32 for ch in candidate):
                        label = candidate
            if key not in self._bot_group_labels and len(self._bot_group_labels) >= 256:
                self._bot_group_labels.pop(next(iter(self._bot_group_labels)))
            self._bot_group_labels[key] = (now + (60.0 if label else 15.0), label)
            return label

    def _resolve_bot_nickname(self, *, login_result: Any = None) -> str:
        """Use the bound login identity, never sender-controlled mention labels.

        Refresh at most once a minute. Failed/invalid refreshes clear the old
        name and briefly back off; explicit mentions remain usable.
        """
        if not self.gateway.bridge_enabled or not self.gateway.bot_qq:
            return ""
        with self._bot_nickname_lock:
            if login_result is None and time.monotonic() < self._bot_nickname_refresh_at:
                return self._bot_nickname
            self._bot_nickname = ""
            self._bot_nickname_refresh_at = time.monotonic() + 15.0
            result = login_result if login_result is not None else self.gateway._onebot_transport.call("get_login_info", timeout=2)
            if not result.ok:
                self._bot_nickname_status = result.code or "login_lookup_failed"
                return ""
            if str(result.data.get("user_id") or "").strip() != str(self.gateway.bot_qq):
                self._bot_nickname_status = "account_identity_mismatch"
                return ""
            nickname = str(result.data.get("nickname") or "").strip()
            try:
                words = _normalize_qq_wake_words((nickname,))
            except ValueError:
                self._bot_nickname_status = "invalid_login_nickname"
                return ""
            self._bot_nickname = words[0]
            self._bot_nickname_status = "resolved"
            self._bot_nickname_refresh_at = time.monotonic() + 60.0
            return self._bot_nickname

    def build_group_identity_context(self, group_id: int) -> str:
        """Current request facts, not a new identity repeated in chat history."""
        if not group_id or not self.gateway.bot_qq:
            return ""
        lines = [f"当前会话：QQ群（group:{int(group_id)}）", f"你的 QQ 账号：qq:{self.gateway.bot_qq}"]
        display_name = self.gateway._resolve_bot_group_label(group_id)
        if display_name:
            lines.append(f"你在本群的显示名：{display_name}")
        return "\n".join(lines)

    @staticmethod
    def _project_mention_evidence(mentions: tuple[MentionRef, ...]) -> list[dict[str, Any]]:
        """Project package mention facts without reconstructing OneBot segments."""

        return [
            {
                "actor_id": "assistant" if mention.is_bot else f"qq:{mention.target_id}",
                "display_name": str(mention.display_name or "").strip(),
                "is_assistant": bool(mention.is_bot),
            }
            for mention in mentions
            if str(mention.target_id or "").strip()
        ]

    def _sender_label_cache_key(self, *, group_id: int, user_id: int) -> str:
        if not user_id:
            return ""
        return f"{group_id or 0}:{user_id}"

    def lookup_group_member_label(self, *, group_id: int, user_id: int) -> str:
        if not group_id or not user_id:
            return ""
        payload = {"group_id": group_id, "user_id": user_id, "no_cache": False}
        result = self.gateway._onebot_transport.call("get_group_member_info", payload, timeout=3)
        if not result.ok:
            return ""
        member = result.data
        label = str(member.get("card") or member.get("nickname") or "").strip()
        return label

    def add_delivery_note(self, session_id: str, note: str) -> None:
        key = str(session_id or "").strip()
        note_text = str(note or "").strip()
        if not key or not note_text:
            return
        with self._delivery_notes_lock:
            self._delivery_notes.setdefault(key, []).append(note_text)

    def consume_delivery_notes(self, session_id: str) -> list[str]:
        key = str(session_id or "").strip()
        if not key:
            return []
        with self._delivery_notes_lock:
            notes = self._delivery_notes.pop(key, [])
        return [str(n).strip() for n in notes if str(n).strip()]

    def build_extra_context(
        self,
        *,
        event: dict[str, Any],
        is_group: bool,
        user_id: int,
        group_id: int,
        sender_label: str = "",
        reply_mode: str = "",
        chat_model_override: str = "",
        session_id: str = "",
    ) -> str:
        active_reply_mode = _safe_reply_mode(reply_mode, default=self.gateway.default_reply_mode)
        lines = [f"qq.reply_delivery: {active_reply_mode}"]
        master_qq = self.gateway.master_qq
        if master_qq:
            lines.append(f"qq.master_qq: {master_qq}")
        lines.extend(self.gateway.consume_delivery_notes(session_id))
        return "\n".join(lines)
