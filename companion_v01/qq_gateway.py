from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import config


QQ_TEXT_CAPABILITIES = (
    "speech_segments",
    "file_drop",
    "choices",
    "tool_actions",
)

QQ_FILE_DELIVERY_DIRECT_RE = re.compile(
    r"(发我|发给我|给我发|发一下|发下|发来|传给我|丢给我|再发|补发|交付|发送|send\s*me|deliver)",
    re.IGNORECASE,
)
QQ_FILE_DELIVERY_TARGET_RE = re.compile(
    r"(文件|附件|结果|成果|产物|文档|表格|图片|照片|音频|视频|字幕|歌词|压缩包|安装包|"
    r"人声|伴奏|干声|歌声|音轨|声轨|"
    r"word|docx?|excel|xlsx?|pptx?|pdf|markdown|\bmd\b|zip|rar|7z|"
    r"mp3|wav|flac|m4a|aac|ogg|opus|vocals?|instrumental|stems?|"
    r"gen_\d+|file_\d+|img_\d+|audio_\d+|video_\d+)",
    re.IGNORECASE,
)
QQ_FILE_OUTPUT_REQUEST_RE = re.compile(
    r"(做|生成|整理|导出|转成|转换|压缩|提取|分离|下载|转写|总结成|保存为|打包|制作)",
    re.IGNORECASE,
)
QQ_FILE_DELIVERY_NEGATIVE_RE = re.compile(
    r"(不要发|别发|先别发|不用发|不用发送|不要发送|不发送|别发送|别传|不用传)",
    re.IGNORECASE,
)
QQ_AUTO_DELIVER_GENERATED_TOOLS = {
    "apply_style_to_existing_file",
    "clean_voice_track",
    "compose_file",
    "convert_media_file",
    "prepare_voice_dataset",
    "revise_generated_file",
    "separate_audio_stems",
    "transcribe_media",
}
QQ_CHARACTER_PACK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
QQ_CHARACTER_COMMAND_PREFIX_RE = re.compile(r"^[!/／]?(?:qq)?\s*", re.IGNORECASE)
QQ_CHARACTER_LIST_COMMANDS = {
    "角色列表",
    "可用角色",
    "可用角色列表",
    "有哪些角色",
    "查看角色列表",
}
QQ_CHARACTER_CURRENT_COMMANDS = {
    "当前角色",
    "现在角色",
    "角色状态",
    "qq当前角色",
}
QQ_CHARACTER_DEFAULT_COMMANDS = {
    "切回默认角色",
    "恢复默认角色",
    "使用默认角色",
    "清除角色切换",
    "取消角色切换",
    "重置角色",
}
QQ_CHARACTER_BUILTIN_COMMANDS = {
    "切回akane",
    "切回Akane",
    "切回内置Akane",
    "切回内置akane",
    "使用Akane",
    "使用akane",
    "切回默认Akane",
    "切回默认akane",
}
QQ_CHARACTER_BUILTIN_IDS = {"akane", "builtin", "built-in", "内置", "内置akane"}
QQ_CHARACTER_SWITCH_PATTERNS = (
    re.compile(r"^(?:切换|更换|换)(?:到|成)?(?:QQ)?角色(?:为|到|成)?[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:使用|启用)(?:QQ)?角色[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^角色(?:切换|切到|改为|换成)[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^角色[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:切换到|切到|换成)[:：\s]+([A-Za-z0-9_.-]+)$", re.IGNORECASE),
    re.compile(r"^character[:：\s]+(.+)$", re.IGNORECASE),
)
QQ_REPLY_MODES = {"text", "voice", "both", "auto"}
QQ_REPLY_MODE_LABELS = {
    "text": "文字模式",
    "voice": "语音模式",
    "both": "双发模式",
    "auto": "自动模式",
}
QQ_REPLY_MODE_CURRENT_COMMANDS = {
    "当前回复模式",
    "回复模式",
    "当前投递模式",
    "qq回复模式",
}
QQ_REPLY_MODE_DEFAULT_COMMANDS = {
    "切回默认回复模式",
    "恢复默认回复模式",
    "重置回复模式",
}
QQ_REPLY_MODE_SWITCH_COMMANDS = {
    "文字模式": "text",
    "文本模式": "text",
    "只发文字": "text",
    "只发文本": "text",
    "语音模式": "voice",
    "只发语音": "voice",
    "双发模式": "both",
    "文字语音模式": "both",
    "文字加语音": "both",
    "自动模式": "auto",
    "自动回复模式": "auto",
}


@dataclass(frozen=True)
class QQMessageContext:
    should_respond: bool
    reason: str
    is_group: bool = False
    target_id: int = 0
    user_id: int = 0
    group_id: int = 0
    session_id: str = ""
    profile_user_id: str = ""
    clean_message: str = ""
    raw_message: str = ""
    extra_context: str = ""
    sender_label: str = ""
    character_pack_id: str = ""
    reply_mode: str = ""
    attachments: list[dict[str, Any]] | None = None

    def to_turn_payload(self) -> dict[str, Any]:
        message = self.clean_message
        if self.is_group:
            label = self.sender_label or (f"QQ {self.user_id}" if self.user_id else "群成员")
            message = f"【{label}】{message}"
        payload = {
            "user_id": self.session_id,
            "real_user_id": self.profile_user_id,
            "message": message,
            "client_mode": "qq_text",
            "client_capabilities": list(QQ_TEXT_CAPABILITIES),
            "extra_context": self.extra_context,
            "qq_delivery_context": self.to_delivery_context(),
        }
        character_pack_id = _safe_character_pack_id(self.character_pack_id)
        if character_pack_id:
            payload["character_pack_id"] = character_pack_id
        reply_mode = _safe_reply_mode(self.reply_mode, default="")
        if reply_mode:
            payload["qq_reply_mode"] = reply_mode
        return payload

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
        }
        character_pack_id = _safe_character_pack_id(self.character_pack_id)
        if character_pack_id:
            payload["character_pack_id"] = character_pack_id
        reply_mode = _safe_reply_mode(self.reply_mode, default="")
        if reply_mode:
            payload["reply_mode"] = reply_mode
        return payload


class NapCatQQGateway:
    def __init__(self) -> None:
        self.group_follow_state: dict[str, dict[str, Any]] = {}
        self.recent_event_fingerprints: dict[str, float] = {}
        self.attachment_debounce_state: dict[str, dict[str, Any]] = {}
        self._attachment_debounce_lock = threading.RLock()
        self.character_pack_overrides: dict[str, str] = {}
        self._character_pack_lock = threading.RLock()
        self.reply_mode_overrides: dict[str, str] = {}
        self._reply_mode_lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(getattr(config, "QQ_BRIDGE_ENABLED", False)),
            "onebot_http_url": self.onebot_http_url,
            "master_qq": str(getattr(config, "MASTER_QQ", "") or ""),
            "bot_qq": self.bot_qq,
            "group_plaintext_enabled": bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False)),
            "event_max_age_seconds": int(getattr(config, "QQ_EVENT_MAX_AGE_SECONDS", 300) or 0),
            "allow_stale_events": bool(getattr(config, "QQ_ALLOW_STALE_EVENTS", False)),
            "require_file_delivery_intent": bool(getattr(config, "QQ_REQUIRE_FILE_DELIVERY_INTENT", True)),
            "character_pack_id": self.character_pack_id,
            "default_character_pack_id": self.default_character_pack_id,
            "active_character_override_count": len(self.character_pack_overrides),
            "reply_mode": self.default_reply_mode,
            "default_reply_mode": self.default_reply_mode,
            "active_reply_mode_override_count": len(self.reply_mode_overrides),
            "active_group_attachment_buffer_count": len(self.group_follow_state),
            "active_attachment_debounce_count": len(self.attachment_debounce_state),
        }

    @property
    def onebot_http_url(self) -> str:
        return str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip().rstrip("/") or "http://127.0.0.1:3001"

    @property
    def bot_qq(self) -> str:
        return str(getattr(config, "QQ_BOT_QQ", "") or "").strip()

    @property
    def character_pack_id(self) -> str:
        return self.default_character_pack_id

    @property
    def default_character_pack_id(self) -> str:
        return _safe_character_pack_id(getattr(config, "QQ_CHARACTER_PACK_ID", ""))

    @property
    def default_reply_mode(self) -> str:
        return _safe_reply_mode(getattr(config, "QQ_REPLY_MODE", "auto"), default="auto")

    @property
    def master_qq(self) -> str:
        value = str(getattr(config, "MASTER_QQ", "") or "").strip()
        return value if value.isdigit() else ""

    def build_message_context(self, event: dict[str, Any]) -> QQMessageContext:
        if str(event.get("post_type") or "").strip().lower() != "message":
            return QQMessageContext(False, "not_message_event")

        message_type = str(event.get("message_type") or "").strip().lower()
        is_private = message_type == "private"
        is_group = message_type == "group" or bool(event.get("group_id"))
        if not (is_private or is_group):
            return QQMessageContext(False, "unsupported_message_type")

        user_id = self._safe_int(event.get("user_id"))
        group_id = self._safe_int(event.get("group_id"))
        self_id = self._safe_int(event.get("self_id"))
        if user_id and user_id in {self._safe_int(self.bot_qq), self_id}:
            return QQMessageContext(False, "self_message")

        if self._is_stale_event(event):
            return QQMessageContext(False, "stale_event")

        if self._is_duplicate_event(event):
            return QQMessageContext(False, "duplicate_event")

        raw_message = self.extract_message_text(event)
        clean_message = self.clean_message_text(event, raw_message)
        attachments = self.extract_attachments(event)
        if not clean_message:
            return QQMessageContext(False, "empty_message")

        mentions_bot = self.message_mentions_bot(event, raw_message)
        session_id, profile_user_id = self.resolve_identity(user_id=user_id, group_id=group_id)
        sender_label = self.resolve_sender_label(event=event, user_id=user_id)
        allow_group_plaintext = self._is_group_plaintext_allowed(
            session_id=session_id,
            user_id=user_id,
        )
        allow_group_attachment_buffer = self._is_group_attachment_buffer_allowed(
            session_id=session_id,
            user_id=user_id,
            attachments=attachments,
        )

        if is_group:
            if mentions_bot:
                self._arm_group_attachment_buffer(
                    session_id=session_id,
                    user_id=user_id,
                    reason="group_mention",
                )
            elif allow_group_attachment_buffer:
                pass
            elif not allow_group_plaintext:
                return QQMessageContext(False, "group_message_without_mention")

        character_pack_id = self.resolve_character_pack_id(session_id)
        reply_mode = self.resolve_reply_mode(session_id)
        return QQMessageContext(
            should_respond=True,
            reason="private"
            if is_private
            else ("group_mention" if mentions_bot else ("group_attachment_buffer" if allow_group_attachment_buffer else "group_follow")),
            is_group=is_group,
            target_id=group_id if is_group else user_id,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            clean_message=clean_message,
            raw_message=raw_message,
            sender_label=sender_label,
            character_pack_id=character_pack_id,
            reply_mode=reply_mode,
            attachments=attachments,
            extra_context=self.build_extra_context(
                event=event,
                is_group=is_group,
                user_id=user_id,
                group_id=group_id,
                sender_label=sender_label,
                reply_mode=reply_mode,
            ),
        )

    def context_from_delivery_context(self, value: dict[str, Any]) -> QQMessageContext | None:
        if not isinstance(value, dict):
            return None
        target_id = self._safe_int(value.get("target_id"))
        if not target_id:
            return None
        return QQMessageContext(
            should_respond=True,
            reason="background_task_delivery",
            is_group=bool(value.get("is_group")),
            target_id=target_id,
            user_id=self._safe_int(value.get("user_id")),
            group_id=self._safe_int(value.get("group_id")),
            session_id=str(value.get("session_id") or ""),
            profile_user_id=str(value.get("profile_user_id") or ""),
            clean_message=str(value.get("clean_message") or ""),
            raw_message=str(value.get("raw_message") or ""),
            sender_label=str(value.get("sender_label") or ""),
            character_pack_id=_safe_character_pack_id(value.get("character_pack_id") or value.get("characterPackId")),
            reply_mode=_safe_reply_mode(value.get("reply_mode") or value.get("replyMode"), default=""),
            attachments=[],
        )

    def resolve_character_pack_id(self, session_id: str) -> str:
        key = str(session_id or "").strip()
        if not key:
            return self.default_character_pack_id
        with self._character_pack_lock:
            if key in self.character_pack_overrides:
                return _safe_character_pack_id(self.character_pack_overrides.get(key))
        return self.default_character_pack_id

    def handle_character_command(
        self,
        context: QQMessageContext,
        *,
        character_resource_service: Any = None,
    ) -> dict[str, Any] | None:
        command = self.parse_character_command(context.clean_message)
        if command is None:
            return None

        action = str(command.get("action") or "")
        if action == "list":
            packs = self._list_character_packs(character_resource_service)
            if not packs:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "no_character_packs",
                    "reply": "当前还没有可用角色包。",
                    "character_pack_id": self.resolve_character_pack_id(context.session_id),
                    "available_packs": [],
                }
            labels = [self._format_pack_label(item) for item in packs[:20]]
            suffix = f"；还有 {len(packs) - len(labels)} 个未显示" if len(packs) > len(labels) else ""
            return {
                "handled": True,
                "ok": True,
                "status": "listed",
                "reply": "可用角色包：" + "、".join(labels) + suffix + "。发送“切换角色 角色包id”即可切换当前 QQ 会话。",
                "character_pack_id": self.resolve_character_pack_id(context.session_id),
                "available_packs": [str(item.get("pack_id") or "") for item in packs if str(item.get("pack_id") or "")],
            }

        if action == "current":
            active_pack_id = self.resolve_character_pack_id(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_character_reply(
                    active_pack_id,
                    character_resource_service=character_resource_service,
                    session_id=context.session_id,
                ),
                "character_pack_id": active_pack_id,
            }

        if action == "default":
            self.clear_session_character_override(context.session_id)
            active_pack_id = self.resolve_character_pack_id(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": self._build_default_character_reply(
                    active_pack_id,
                    character_resource_service=character_resource_service,
                ),
                "character_pack_id": active_pack_id,
            }

        if action == "builtin":
            self.set_session_character_pack_id(context.session_id, "")
            return {
                "handled": True,
                "ok": True,
                "status": "builtin",
                "reply": "已切回内置 Akane 人设。之后这个 QQ 会话会使用未绑定角色包的默认聊天记忆。",
                "character_pack_id": "",
            }

        if action == "switch":
            requested_pack_id = _safe_character_pack_id(command.get("pack_id"))
            if not requested_pack_id:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_character_pack_id",
                    "reply": "这个角色包 id 不太对。只能使用字母、数字、下划线、点和短横线，比如 reimu。",
                    "character_pack_id": self.resolve_character_pack_id(context.session_id),
                }
            if requested_pack_id.lower() in QQ_CHARACTER_BUILTIN_IDS:
                self.set_session_character_pack_id(context.session_id, "")
                return {
                    "handled": True,
                    "ok": True,
                    "status": "builtin",
                    "reply": "已切回内置 Akane 人设。之后这个 QQ 会话会使用未绑定角色包的默认聊天记忆。",
                    "character_pack_id": "",
                }
            identity = self._resolve_pack_identity(
                requested_pack_id,
                character_resource_service=character_resource_service,
            )
            if not identity:
                packs = self._list_character_packs(character_resource_service)
                available = "、".join(str(item.get("pack_id") or "") for item in packs[:12] if str(item.get("pack_id") or ""))
                hint = f"当前可用：{available}。" if available else "可以先在角色工坊创建或导入角色包。"
                return {
                    "handled": True,
                    "ok": False,
                    "status": "unknown_character_pack",
                    "reply": f"没有找到角色包 {requested_pack_id}。{hint}",
                    "character_pack_id": self.resolve_character_pack_id(context.session_id),
                    "requested_pack_id": requested_pack_id,
                }
            self.set_session_character_pack_id(context.session_id, requested_pack_id)
            label = self._format_identity_label(identity)
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": f"已切换本 QQ 会话角色为 {label}（{requested_pack_id}）。之后的聊天和记忆会按这个角色包隔离。",
                "character_pack_id": requested_pack_id,
                "requested_pack_id": requested_pack_id,
            }

        return None

    def parse_character_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_character_command_text(message)
        if not text:
            return None
        if text in QQ_CHARACTER_LIST_COMMANDS:
            return {"action": "list"}
        if text in QQ_CHARACTER_CURRENT_COMMANDS:
            return {"action": "current"}
        if text in QQ_CHARACTER_DEFAULT_COMMANDS:
            return {"action": "default"}
        if text in QQ_CHARACTER_BUILTIN_COMMANDS:
            return {"action": "builtin"}
        for pattern in QQ_CHARACTER_SWITCH_PATTERNS:
            match = pattern.fullmatch(text)
            if not match:
                continue
            pack_id = _clean_character_pack_argument(match.group(1))
            if pack_id.lower() in QQ_CHARACTER_BUILTIN_IDS:
                return {"action": "builtin", "pack_id": pack_id}
            return {"action": "switch", "pack_id": pack_id}
        return None

    def set_session_character_pack_id(self, session_id: str, character_pack_id: str) -> None:
        key = str(session_id or "").strip()
        if not key:
            return
        with self._character_pack_lock:
            self.character_pack_overrides[key] = _safe_character_pack_id(character_pack_id)

    def clear_session_character_override(self, session_id: str) -> None:
        key = str(session_id or "").strip()
        if not key:
            return
        with self._character_pack_lock:
            self.character_pack_overrides.pop(key, None)

    def resolve_reply_mode(self, session_id: str) -> str:
        key = str(session_id or "").strip()
        if not key:
            return self.default_reply_mode
        with self._reply_mode_lock:
            if key in self.reply_mode_overrides:
                return _safe_reply_mode(self.reply_mode_overrides.get(key), default=self.default_reply_mode)
        return self.default_reply_mode

    def handle_reply_mode_command(self, context: QQMessageContext) -> dict[str, Any] | None:
        command = self.parse_reply_mode_command(context.clean_message)
        if command is None:
            return None

        action = str(command.get("action") or "")
        if action == "current":
            active_mode = self.resolve_reply_mode(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_reply_mode_reply(active_mode, session_id=context.session_id),
                "reply_mode": active_mode,
            }
        if action == "default":
            self.clear_session_reply_mode_override(context.session_id)
            active_mode = self.resolve_reply_mode(context.session_id)
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": f"已恢复 QQ 默认回复模式：{self._format_reply_mode_label(active_mode)}。",
                "reply_mode": active_mode,
            }
        if action == "switch":
            reply_mode = _safe_reply_mode(command.get("reply_mode"), default="")
            if not reply_mode:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_reply_mode",
                    "reply": "回复模式只能是文字模式、语音模式、双发模式或自动模式。",
                    "reply_mode": self.resolve_reply_mode(context.session_id),
                }
            self.set_session_reply_mode(context.session_id, reply_mode)
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": f"已把当前 QQ 会话切到{self._format_reply_mode_label(reply_mode)}。",
                "reply_mode": reply_mode,
            }
        return None

    def parse_reply_mode_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_character_command_text(message)
        if not text:
            return None
        if text in QQ_REPLY_MODE_CURRENT_COMMANDS:
            return {"action": "current"}
        if text in QQ_REPLY_MODE_DEFAULT_COMMANDS:
            return {"action": "default"}
        if text in QQ_REPLY_MODE_SWITCH_COMMANDS:
            return {"action": "switch", "reply_mode": QQ_REPLY_MODE_SWITCH_COMMANDS[text]}
        match = re.fullmatch(r"^(?:切换|更换|设置|设为)(?:QQ)?回复模式[:：\s]+(.+)$", text, re.IGNORECASE)
        if match:
            return {"action": "switch", "reply_mode": _clean_reply_mode_argument(match.group(1))}
        return None

    def set_session_reply_mode(self, session_id: str, reply_mode: str) -> None:
        key = str(session_id or "").strip()
        mode = _safe_reply_mode(reply_mode, default="")
        if not key or not mode:
            return
        with self._reply_mode_lock:
            self.reply_mode_overrides[key] = mode

    def clear_session_reply_mode_override(self, session_id: str) -> None:
        key = str(session_id or "").strip()
        if not key:
            return
        with self._reply_mode_lock:
            self.reply_mode_overrides.pop(key, None)

    def _build_current_reply_mode_reply(self, active_mode: str, *, session_id: str = "") -> str:
        key = str(session_id or "").strip()
        with self._reply_mode_lock:
            has_override = bool(key and key in self.reply_mode_overrides)
        source = "本会话临时切换" if has_override else "QQ 默认配置"
        return f"当前 QQ 会话回复模式：{self._format_reply_mode_label(active_mode)}（来源：{source}）。"

    def _format_reply_mode_label(self, reply_mode: str) -> str:
        return QQ_REPLY_MODE_LABELS.get(_safe_reply_mode(reply_mode), QQ_REPLY_MODE_LABELS["auto"])

    def _normalize_character_command_text(self, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        text = QQ_CHARACTER_COMMAND_PREFIX_RE.sub("", text, count=1).strip()
        return text.strip()

    def _list_character_packs(self, character_resource_service: Any = None) -> list[dict[str, str]]:
        if character_resource_service is None:
            return []
        listing = getattr(character_resource_service, "list_character_packs", None)
        if listing is None:
            return []
        try:
            raw_items = listing()
        except Exception:
            return []
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            pack_id = _safe_character_pack_id(item.get("pack_id") or item.get("id"))
            if not pack_id or pack_id in seen:
                continue
            seen.add(pack_id)
            normalized.append(
                {
                    "pack_id": pack_id,
                    "name": str(item.get("name") or pack_id).strip()[:80],
                    "app_name": str(item.get("app_name") or item.get("appName") or item.get("name") or pack_id).strip()[:80],
                    "user_title": str(item.get("user_title") or item.get("userTitle") or "").strip()[:80],
                }
            )
        return normalized

    def _resolve_pack_identity(
        self,
        pack_id: str,
        *,
        character_resource_service: Any = None,
    ) -> dict[str, str]:
        normalized_pack_id = _safe_character_pack_id(pack_id)
        if not normalized_pack_id or character_resource_service is None:
            return {}
        builder = getattr(character_resource_service, "build_character_identity", None)
        if builder is None:
            return {}
        try:
            identity = builder(normalized_pack_id)
        except Exception:
            return {}
        if not isinstance(identity, dict) or not identity:
            return {}
        resolved_pack_id = _safe_character_pack_id(identity.get("pack_id") or normalized_pack_id)
        if resolved_pack_id != normalized_pack_id:
            return {}
        assistant_name = str(identity.get("assistant_name") or identity.get("name") or normalized_pack_id).strip()
        app_name = str(identity.get("app_name") or assistant_name or normalized_pack_id).strip()
        user_label = str(identity.get("user_label") or "").strip()
        return {
            "pack_id": normalized_pack_id,
            "assistant_name": assistant_name[:80] or normalized_pack_id,
            "app_name": app_name[:80] or assistant_name[:80] or normalized_pack_id,
            "user_label": user_label[:80],
        }

    def _format_pack_label(self, item: dict[str, Any]) -> str:
        pack_id = str(item.get("pack_id") or "").strip()
        name = str(item.get("name") or "").strip()
        app_name = str(item.get("app_name") or "").strip()
        display = " / ".join(part for part in (app_name, name) if part and part != pack_id)
        return f"{pack_id}（{display}）" if display else pack_id

    def _format_identity_label(self, identity: dict[str, Any]) -> str:
        assistant_name = str(identity.get("assistant_name") or "").strip()
        app_name = str(identity.get("app_name") or "").strip()
        label = " / ".join(part for part in (app_name, assistant_name) if part)
        return label or str(identity.get("pack_id") or "角色包").strip() or "角色包"

    def _build_current_character_reply(
        self,
        active_pack_id: str,
        *,
        character_resource_service: Any = None,
        session_id: str = "",
    ) -> str:
        key = str(session_id or "").strip()
        with self._character_pack_lock:
            has_override = bool(key and key in self.character_pack_overrides)
        if not active_pack_id:
            source = "本会话临时切换" if has_override else "QQ 默认配置"
            return f"当前 QQ 会话使用内置 Akane 人设（来源：{source}）。"
        identity = self._resolve_pack_identity(
            active_pack_id,
            character_resource_service=character_resource_service,
        )
        label = self._format_identity_label(identity) if identity else active_pack_id
        source = "本会话临时切换" if has_override else "QQ 默认配置"
        return f"当前 QQ 会话角色：{label}（{active_pack_id}，来源：{source}）。"

    def _build_default_character_reply(
        self,
        active_pack_id: str,
        *,
        character_resource_service: Any = None,
    ) -> str:
        if not active_pack_id:
            return "已恢复 QQ 默认角色：内置 Akane 人设。"
        identity = self._resolve_pack_identity(
            active_pack_id,
            character_resource_service=character_resource_service,
        )
        label = self._format_identity_label(identity) if identity else active_pack_id
        return f"已恢复 QQ 默认角色：{label}（{active_pack_id}）。"

    def extract_message_text(self, event: dict[str, Any]) -> str:
        raw_message = str(event.get("raw_message") or "").strip()
        if raw_message:
            return raw_message

        segments = event.get("message")
        if not isinstance(segments, list):
            return str(segments or "").strip()

        rendered: list[str] = []
        for item in segments:
            if not isinstance(item, dict):
                continue
            seg_type = str(item.get("type") or "").strip().lower()
            seg_data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if seg_type == "text":
                rendered.append(str(seg_data.get("text") or ""))
            elif seg_type == "at":
                rendered.append(f"[CQ:at,qq={str(seg_data.get('qq') or '').strip()}]")
            elif seg_type == "image":
                url_value = str(seg_data.get("url") or "").strip()
                file_value = str(seg_data.get("file") or "").strip()
                if url_value:
                    rendered.append(f"[CQ:image,url={url_value}]")
                elif file_value:
                    rendered.append(f"[CQ:image,file={file_value}]")
                else:
                    rendered.append("[CQ:image]")
            elif seg_type == "file":
                name_value = str(seg_data.get("name") or seg_data.get("file") or "").strip()
                if name_value:
                    rendered.append(f"[CQ:file,name={name_value}]")
                else:
                    rendered.append("[CQ:file]")
            elif seg_type in {"record", "voice"}:
                rendered.append("[CQ:record]")
        return "".join(rendered).strip()

    def clean_message_text(self, event: dict[str, Any], raw_message: str) -> str:
        text = str(raw_message or "").strip()
        has_image = bool(re.search(r"\[CQ:image(?:,[^\]]*)?\]", text))
        has_file = bool(re.search(r"\[CQ:file(?:,[^\]]*)?\]", text))
        has_record = bool(re.search(r"\[CQ:record(?:,[^\]]*)?\]", text))
        text = re.sub(r"\[CQ:image(?:,[^\]]*)?\]", " [图片] ", text)
        text = re.sub(r"\[CQ:file(?:,[^\]]*)?\]", " [文件] ", text)
        text = re.sub(r"\[CQ:record(?:,[^\]]*)?\]", " [语音] ", text)
        for bot_id in self._bot_target_ids(event):
            text = text.replace(f"[CQ:at,qq={bot_id}]", "")
        text = re.sub(r"\[CQ:at,qq=\d+\]", "", text)
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text in {"[图片]", "[文件]", "[语音]"}:
            text = ""
        if text in {"[图片] [文件]", "[文件] [图片]"}:
            text = ""
        if not text:
            if has_image and has_file:
                return "发来了图片和文件。"
            if has_image:
                return "发来了一张图片。"
            if has_file:
                return "发来了一个文件。"
            if has_record:
                return "发来了一段语音。"
        return text

    def extract_attachments(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        message_id = str(event.get("message_id") or "").strip()
        segments = event.get("message")
        if isinstance(segments, list):
            for index, item in enumerate(segments, start=1):
                if not isinstance(item, dict):
                    continue
                seg_type = str(item.get("type") or "").strip().lower()
                seg_data = item.get("data") if isinstance(item.get("data"), dict) else {}
                parsed = self._segment_to_attachment(seg_type=seg_type, data=seg_data)
                if parsed:
                    parsed["source_message_id"] = message_id
                    parsed["source_event_id"] = str(event.get("message_id") or event.get("time") or "").strip()
                    parsed["segment_index"] = index
                    attachments.append(parsed)
            return attachments

        raw_message = self.extract_message_text(event)
        for match in re.finditer(r"\[CQ:(image|file|record)(?:,([^\]]*))?\]", raw_message):
            seg_type = match.group(1)
            data = self._parse_cq_params(match.group(2) or "")
            parsed = self._segment_to_attachment(seg_type=seg_type, data=data)
            if parsed:
                parsed["source_message_id"] = message_id
                parsed["source_event_id"] = str(event.get("message_id") or event.get("time") or "").strip()
                attachments.append(parsed)
        return attachments

    def _segment_to_attachment(self, *, seg_type: str, data: dict[str, Any]) -> dict[str, Any] | None:
        if seg_type == "image":
            file_value = str(data.get("file") or data.get("filename") or data.get("name") or "").strip()
            return {
                "kind": "image",
                "file": file_value,
                "url": str(data.get("url") or "").strip(),
                "path": str(data.get("path") or data.get("local_path") or "").strip(),
                "origin_name": file_value,
                "mime_type": "image/jpeg" if file_value.lower().endswith((".jpg", ".jpeg")) else "",
                "file_size": self._safe_int(data.get("size") or data.get("file_size")),
            }
        if seg_type == "file":
            file_value = str(data.get("file") or data.get("filename") or "").strip()
            origin_name = str(data.get("name") or file_value or "").strip()
            return {
                "kind": "document",
                "file": file_value or origin_name,
                "url": str(data.get("url") or "").strip(),
                "path": str(data.get("path") or data.get("local_path") or "").strip(),
                "origin_name": origin_name,
                "mime_type": str(data.get("mime_type") or "").strip(),
                "file_size": self._safe_int(data.get("size") or data.get("file_size")),
            }
        if seg_type in {"record", "voice"}:
            file_value = str(data.get("file") or data.get("filename") or data.get("name") or "").strip()
            return {
                "kind": "audio",
                "file": file_value,
                "url": str(data.get("url") or "").strip(),
                "path": str(data.get("path") or data.get("local_path") or "").strip(),
                "origin_name": file_value,
                "mime_type": str(data.get("mime_type") or "audio/mpeg").strip(),
                "file_size": self._safe_int(data.get("size") or data.get("file_size")),
            }
        return None

    def _parse_cq_params(self, raw: str) -> dict[str, str]:
        params: dict[str, str] = {}
        for part in str(raw or "").split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            params[key.strip()] = value.strip()
        return params

    def message_mentions_bot(self, event: dict[str, Any], raw_message: str) -> bool:
        if bool(event.get("to_me")):
            return True
        raw_text = str(raw_message or "")
        for bot_id in self._bot_target_ids(event):
            if f"[CQ:at,qq={bot_id}]" in raw_text:
                return True

        segments = event.get("message")
        if isinstance(segments, list):
            for item in segments:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip().lower() != "at":
                    continue
                qq_value = str((item.get("data") or {}).get("qq") or "").strip()
                if qq_value in self._bot_target_ids(event):
                    return True
        return False

    def resolve_identity(self, *, user_id: int, group_id: int = 0) -> tuple[str, str]:
        user_text = str(user_id or "")
        if group_id:
            shared_group_id = f"qq_group_shared_{group_id}"
            return shared_group_id, shared_group_id
        if self.master_qq and user_text == self.master_qq:
            return "master", "master"
        return f"qq_pri_{user_id}", f"qq_{user_id}"

    def resolve_sender_label(self, *, event: dict[str, Any], user_id: int) -> str:
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        nickname = str(sender.get("card") or sender.get("nickname") or "").strip()
        if nickname:
            return nickname
        return f"QQ {user_id}" if user_id else "群成员"

    def build_extra_context(
        self,
        *,
        event: dict[str, Any],
        is_group: bool,
        user_id: int,
        group_id: int,
        sender_label: str = "",
        reply_mode: str = "",
    ) -> str:
        sender_label = str(sender_label or self.resolve_sender_label(event=event, user_id=user_id)).strip()
        lines = [
            "【QQ 客户端上下文】",
            f"本轮来自：{'QQ群聊' if is_group else 'QQ私聊'}",
            f"发送者 QQ：{user_id or 'unknown'}",
        ]
        if sender_label:
            lines.append(f"发送者标识：{sender_label}")
        if is_group:
            lines.append(f"群号：{group_id or 'unknown'}")
            lines.append("群聊消息会带有【昵称】标记；这是说话人标记，不是用户正文。")
        lines.append("这是纯文字客户端；不需要切换场景、BGM 或立绘。")
        active_reply_mode = _safe_reply_mode(reply_mode, default=self.default_reply_mode)
        lines.append(
            "当前 QQ 回复投递模式："
            f"{self._format_reply_mode_label(active_reply_mode)}。"
            "只有自动模式会参考 reply_medium；文字/语音/双发模式由后端强制执行。"
        )
        lines.append("QQ 会尽早发送 speech 中已经成句的内容；为了响应更快，优先把正文写进 speech，并用自然标点或换行分隔。")
        lines.append("QQ 里音视频转码、分离人声伴奏、降噪、转写、切片打包这类可能耗时的媒体处理，优先用 delegate_task 交给后台工坊；前台只简短说已经开始，完成后系统会主动通知并交付。")
        return "\n".join(lines)

    def render_reply_text(self, frame: dict[str, Any]) -> str:
        return "\n".join(self.render_reply_messages(frame)).strip()

    def render_reply_messages(self, frame: dict[str, Any]) -> list[str]:
        messages: list[str] = []
        max_segments = max(1, min(20, int(getattr(config, "QQ_REPLY_MAX_SEGMENTS", 8) or 8)))
        segments = frame.get("speech_segments")
        if isinstance(segments, list):
            for item in segments:
                text = str(item or "").strip()
                if text:
                    messages.append(text[:1800].strip())
                if len(messages) >= max_segments:
                    break

        if not messages:
            speech = str(frame.get("speech") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            inferred = [line.strip() for line in speech.split("\n") if line.strip()]
            if 1 < len(inferred) <= max_segments:
                messages = [line[:1800].strip() for line in inferred[:max_segments]]
            elif speech:
                messages = [speech[:1800].strip()]

        code_snippet = str(frame.get("code_snippet") or "").strip()
        if code_snippet:
            if messages:
                messages[-1] = f"{messages[-1]}\n\n{code_snippet}".strip()[:1800].strip()
            else:
                messages.append(code_snippet[:1800].strip())
        return [message for message in messages if message]

    def send_replies(self, context: QQMessageContext, messages: list[str]) -> dict[str, Any]:
        clean_messages = [str(message or "").strip() for message in messages if str(message or "").strip()]
        if not clean_messages:
            return {"ok": False, "reason": "empty_messages", "results": []}

        results: list[dict[str, Any]] = []
        delay_seconds = min(3.0, max(0.0, float(getattr(config, "QQ_REPLY_SEGMENT_DELAY_SECONDS", 0.8) or 0.0)))
        for index, message in enumerate(clean_messages):
            if index > 0:
                time.sleep(delay_seconds)
            results.append(self.send_reply(context, message))
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_generated_files(self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None) -> dict[str, Any]:
        events = [event for event in tool_events or [] if isinstance(event, dict)]
        targets: list[dict[str, Any]] = []
        for event in events:
            if not self._event_allows_qq_file_delivery(event):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in {"generated_file_ready", "file_ready"}:
                continue
            if not bool(event.get("send_to_user")):
                continue
            if event_type == "file_ready":
                file_ref = event.get("file") if isinstance(event.get("file"), dict) else {}
                path = str(file_ref.get("absolute_path") or "").strip()
                name = str(file_ref.get("name") or file_ref.get("title") or Path(path).name).strip()
                if path:
                    targets.append(
                        {
                            "generated_id": str(file_ref.get("generated_id") or "").strip(),
                            "source_id": str(file_ref.get("source_id") or "").strip(),
                            "source_type": str(file_ref.get("source_type") or "").strip(),
                            "path": path,
                            "name": name or Path(path).name,
                        }
                    )
                continue

            generated = event.get("generated_file") if isinstance(event.get("generated_file"), dict) else {}
            path = str(generated.get("absolute_path") or "").strip()
            generated_id = str(generated.get("generated_id") or "").strip()
            title = str(generated.get("output_title") or generated.get("generated_handle") or "akane_output").strip()
            ext = str(generated.get("file_ext") or generated.get("output_format") or "").strip().lstrip(".")
            if path and generated_id:
                targets.append(
                    {
                        "generated_id": generated_id,
                        "source_id": generated_id,
                        "source_type": "generated",
                        "path": path,
                        "name": f"{title}.{ext}" if ext and not title.lower().endswith(f".{ext.lower()}") else title,
                        "allow_without_delivery_intent": self._allows_current_generated_file_delivery(generated),
                    }
                )
        if not targets:
            return {"ok": True, "count": 0, "results": []}

        if self._should_block_file_delivery(context):
            blocked_targets = [target for target in targets if not bool(target.get("allow_without_delivery_intent"))]
            targets = [target for target in targets if bool(target.get("allow_without_delivery_intent"))]
            if targets and blocked_targets:
                blocked_count = len(blocked_targets)
            elif targets:
                blocked_count = 0
            else:
                blocked_count = len(blocked_targets)
            return {
                "ok": True,
                "count": 0,
                "blocked_count": blocked_count,
                "reason": "missing_file_delivery_intent",
                "results": [],
            } if not targets else self._send_generated_file_targets(context, targets, blocked_count=blocked_count)

        return self._send_generated_file_targets(context, targets)

    def _send_generated_file_targets(
        self,
        context: QQMessageContext,
        targets: list[dict[str, Any]],
        *,
        blocked_count: int = 0,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for target in targets:
            result = self.send_file(
                context,
                file_path=str(target.get("path") or ""),
                name=str(target.get("name") or ""),
            )
            result["generated_id"] = str(target.get("generated_id") or "")
            results.append(result)
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
            **({"blocked_count": blocked_count} if blocked_count else {}),
        }

    def _allows_current_generated_file_delivery(self, generated: dict[str, Any]) -> bool:
        created_by_tool = str(generated.get("created_by_tool") or "").strip()
        delivery_status = str(generated.get("delivery_status") or "pending").strip().lower()
        return created_by_tool in QQ_AUTO_DELIVER_GENERATED_TOOLS and delivery_status in {"", "pending"}

    def _event_allows_qq_file_delivery(self, event: dict[str, Any]) -> bool:
        event_mode = str(event.get("client_mode") or "").strip().lower()
        return not event_mode or event_mode == "qq_text"

    def message_requests_file_delivery(self, text: str) -> bool:
        clean_text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not clean_text:
            return False
        if QQ_FILE_DELIVERY_NEGATIVE_RE.search(clean_text):
            return False
        if QQ_FILE_DELIVERY_DIRECT_RE.search(clean_text):
            return True
        has_target = bool(QQ_FILE_DELIVERY_TARGET_RE.search(clean_text))
        if not has_target:
            return False
        return bool(QQ_FILE_OUTPUT_REQUEST_RE.search(clean_text))

    def send_stickers(self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None) -> dict[str, Any]:
        events = [event for event in tool_events or [] if isinstance(event, dict)]
        targets: list[dict[str, Any]] = []
        for event in events:
            if str(event.get("type") or "") != "sticker_ready":
                continue
            if not bool(event.get("send_to_user")):
                continue
            sticker = event.get("sticker") if isinstance(event.get("sticker"), dict) else {}
            path = str(sticker.get("absolute_path") or "").strip()
            if not path:
                continue
            targets.append(
                {
                    "sticker_id": str(sticker.get("id") or "").strip(),
                    "path": path,
                    "name": str(sticker.get("display_name") or Path(path).stem).strip(),
                }
            )
        if not targets:
            return {"ok": True, "count": 0, "results": []}

        results: list[dict[str, Any]] = []
        for target in targets:
            result = self.send_image(
                context,
                image_path=str(target.get("path") or ""),
                name=str(target.get("name") or ""),
            )
            result["sticker_id"] = str(target.get("sticker_id") or "")
            results.append(result)
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_reply(self, context: QQMessageContext, message: str) -> dict[str, Any]:
        clean_message = str(message or "").strip()
        if not context.target_id or not clean_message:
            return {"ok": False, "reason": "empty_target_or_message"}

        action = "send_group_msg" if context.is_group else "send_private_msg"
        payload = (
            {"group_id": context.target_id, "message": clean_message}
            if context.is_group
            else {"user_id": context.target_id, "message": clean_message}
        )
        try:
            response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=8)
            response.raise_for_status()
            data = response.json()
            return {"ok": True, "action": action, "data": data}
        except Exception as exc:
            return {"ok": False, "action": action, "reason": str(exc)}

    def send_image(self, context: QQMessageContext, *, image_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(image_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_image"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "image_not_found", "file": clean_path}

        action = "send_group_msg" if context.is_group else "send_private_msg"
        base_payload = {"group_id": context.target_id} if context.is_group else {"user_id": context.target_id}
        file_candidates = [path_obj.resolve().as_uri(), str(path_obj.resolve())]
        last_error = ""
        for file_value in file_candidates:
            payload = {
                **base_payload,
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": file_value,
                            "summary": name or path_obj.name,
                        },
                    }
                ],
            }
            try:
                response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=20)
                response.raise_for_status()
                data = response.json()
                return {"ok": True, "action": action, "data": data, "file": clean_path}
            except Exception as exc:
                last_error = str(exc)
        return {"ok": False, "action": action, "reason": last_error, "file": clean_path}

    def send_voice(self, context: QQMessageContext, *, audio_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(audio_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_audio"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "audio_not_found", "file": clean_path}

        action = "send_group_msg" if context.is_group else "send_private_msg"
        base_payload = {"group_id": context.target_id} if context.is_group else {"user_id": context.target_id}
        file_candidates = [path_obj.resolve().as_uri(), str(path_obj.resolve())]
        last_error = ""
        for file_value in file_candidates:
            payload = {
                **base_payload,
                "message": [
                    {
                        "type": "record",
                        "data": {
                            "file": file_value,
                            "summary": name or path_obj.name,
                        },
                    }
                ],
            }
            try:
                response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=30)
                response.raise_for_status()
                data = response.json()
                return {"ok": True, "action": action, "data": data, "file": clean_path}
            except Exception as exc:
                last_error = str(exc)
        return {"ok": False, "action": action, "reason": last_error, "file": clean_path}

    def send_file(self, context: QQMessageContext, *, file_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(file_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_file"}

        action = "upload_group_file" if context.is_group else "upload_private_file"
        payload = (
            {"group_id": context.target_id, "file": clean_path, "name": name or Path(clean_path).name}
            if context.is_group
            else {"user_id": context.target_id, "file": clean_path, "name": name or Path(clean_path).name}
        )
        try:
            response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            return {"ok": True, "action": action, "data": data}
        except Exception as exc:
            return {"ok": False, "action": action, "reason": str(exc)}

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

    def _bot_target_ids(self, event: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for candidate in (self.bot_qq, event.get("self_id")):
            text = str(candidate or "").strip()
            if text and text != "0":
                ids.add(text)
        return ids

    def _is_group_plaintext_allowed(self, *, session_id: str, user_id: int) -> bool:
        return bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False))

    def _is_group_attachment_buffer_allowed(
        self,
        *,
        session_id: str,
        user_id: int,
        attachments: list[dict[str, Any]] | None,
    ) -> bool:
        if not attachments:
            return False
        state = self._get_group_follow_state(session_id)
        return bool(state and int(state.get("user_id") or 0) == int(user_id or 0))

    def _get_group_follow_state(self, session_id: str) -> dict[str, Any] | None:
        state = self.group_follow_state.get(session_id)
        if not isinstance(state, dict):
            return None
        if float(state.get("expires_at") or 0.0) <= time.time():
            self.group_follow_state.pop(session_id, None)
            return None
        return state

    def _arm_group_attachment_buffer(self, *, session_id: str, user_id: int, reason: str) -> None:
        ttl = max(
            20,
            int(
                getattr(
                    config,
                    "QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS",
                    getattr(config, "QQ_GROUP_FOLLOW_TTL_SECONDS", 180),
                )
                or 180
            ),
        )
        self.group_follow_state[session_id] = {
            "user_id": int(user_id or 0),
            "expires_at": time.time() + ttl,
            "reason": reason,
        }

    def _arm_group_follow(self, *, session_id: str, user_id: int, reason: str) -> None:
        self._arm_group_attachment_buffer(session_id=session_id, user_id=user_id, reason=reason)

    def _is_stale_event(self, event: dict[str, Any]) -> bool:
        if bool(getattr(config, "QQ_ALLOW_STALE_EVENTS", False)):
            return False
        max_age_seconds = max(0.0, float(getattr(config, "QQ_EVENT_MAX_AGE_SECONDS", 300) or 0.0))
        if max_age_seconds <= 0:
            return False
        event_ts = self._event_timestamp(event)
        if event_ts <= 0:
            return False
        age_seconds = time.time() - event_ts
        return age_seconds > max_age_seconds

    def _event_timestamp(self, event: dict[str, Any]) -> float:
        try:
            event_ts = float(event.get("time") or 0.0)
        except Exception:
            return 0.0
        if event_ts > 10_000_000_000:
            event_ts = event_ts / 1000.0
        return event_ts if event_ts > 0 else 0.0

    def _should_block_file_delivery(self, context: QQMessageContext) -> bool:
        if not bool(getattr(config, "QQ_REQUIRE_FILE_DELIVERY_INTENT", True)):
            return False
        return not self.message_requests_file_delivery(context.clean_message or context.raw_message)

    def _is_duplicate_event(self, event: dict[str, Any], *, ttl_seconds: float = 300.0) -> bool:
        now_ts = time.time()
        stale_keys = [key for key, seen_at in self.recent_event_fingerprints.items() if now_ts - seen_at > ttl_seconds]
        for key in stale_keys:
            self.recent_event_fingerprints.pop(key, None)

        fingerprints = self._event_fingerprints(event)
        if not fingerprints:
            return False
        if any(
            (seen_at := self.recent_event_fingerprints.get(fingerprint)) is not None and now_ts - seen_at <= ttl_seconds
            for fingerprint in fingerprints
        ):
            for fingerprint in fingerprints:
                self.recent_event_fingerprints[fingerprint] = now_ts
            return True
        for fingerprint in fingerprints:
            self.recent_event_fingerprints[fingerprint] = now_ts
        return False

    def _event_fingerprint(self, event: dict[str, Any]) -> str:
        fingerprints = self._event_fingerprints(event)
        return fingerprints[0] if fingerprints else ""

    def _event_fingerprints(self, event: dict[str, Any]) -> list[str]:
        fingerprints: list[str] = []
        message_type = str(event.get("message_type") or "").strip()
        user_id = str(event.get("user_id") or "").strip()
        group_id = str(event.get("group_id") or "").strip()
        self_id = str(event.get("self_id") or "").strip()

        message_id = str(event.get("message_id") or "").strip()
        if message_id:
            fingerprints.append(f"id:{message_id}")
            fingerprints.append(f"peer:{self_id}|{message_type}|{group_id}|{user_id}|{message_id}")
        for key in ("real_id", "message_seq", "msg_id"):
            value = str(event.get(key) or "").strip()
            if value:
                fingerprints.append(f"{key}:{self_id}|{message_type}|{group_id}|{user_id}|{value}")

        timestamp = str(event.get("time") or "").strip()
        raw_message = self.extract_message_text(event)[:200]
        if user_id or group_id or raw_message:
            fingerprints.append(f"fallback:{self_id}|{message_type}|{user_id}|{group_id}|{timestamp}|{raw_message}")

        seen: set[str] = set()
        unique: list[str] = []
        for fingerprint in fingerprints:
            if fingerprint and fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(fingerprint)
        return unique

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0


def _safe_character_pack_id(value: Any) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not QQ_CHARACTER_PACK_ID_RE.fullmatch(pack_id):
        return ""
    return pack_id


def _clean_character_pack_argument(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip("`'\"“”‘’")
    text = text.rstrip("。.!！?？,，;；")
    return text.strip()


def _safe_reply_mode(value: Any, *, default: str = "auto") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "text": "text",
        "文字": "text",
        "文本": "text",
        "voice": "voice",
        "audio": "voice",
        "record": "voice",
        "语音": "voice",
        "both": "both",
        "all": "both",
        "text_voice": "both",
        "voice_text": "both",
        "文字语音": "both",
        "双发": "both",
        "auto": "auto",
        "automatic": "auto",
        "自动": "auto",
    }
    if not text:
        return default if default in QQ_REPLY_MODES else "auto"
    mode = aliases.get(text, "")
    if mode:
        return mode
    return default if default in QQ_REPLY_MODES else "auto"


def _clean_reply_mode_argument(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip("`'\"“”‘’")
    text = text.rstrip("。.!！?？,，;；")
    return text.strip()
