from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from channelcore_onebot import (
    AttachmentRef,
    EventAdmissionConfig,
    GroupTriggerPolicy,
    MentionRef,
    OneBotEventAdmission,
    OutboundAction,
    OutboundTarget,
    build_message_action,
    build_upload_file_action,
    clean_message_text as clean_onebot_message_text,
    compile_wake_word_prefix as _compile_qq_wake_word_prefix,
    compile_wake_word_search as _compile_qq_wake_word_search,
    message_mentions_bot as onebot_message_mentions_bot,
    normalize_inbound_event,
    normalize_wake_words as _normalize_qq_wake_words,
    parse_attachments as parse_onebot_attachments,
    parse_cq_params as parse_onebot_cq_params,
    render_message_text as render_onebot_message_text,
    resolve_quoted_message as resolve_onebot_quoted_message,
    image_segment,
    mface_segment,
    music_segment,
    text_segment,
    voice_segment,
)

import config
from .care_runtime import CareModulePort, DEFAULT_CARE_SHOP_ITEMS, DEFAULT_CHECKIN_COINS, get_seasonal_shop_items
from .deployment_security import QQChannelRuntimeConfig
from .model_service_config import normalize_provider_model_id
from .onebot_transport import OneBotActionTransport
from .qq_poke_reactor import PokeEventReactor, PokeOutcome


QQ_TEXT_CAPABILITIES = (
    "speech_segments",
    "file_drop",
    "choices",
    "tool_actions",
)

QQ_REPLY_REFERENCE_MAX_CLAIMS = 4096

# Akane QQ music-card platform -> OneBot music segment type (V1 open set).
QQ_MUSIC_PLATFORM_TO_ONEBOT = {
    "netease_music": "163",
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
QQ_INLINE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
QQ_INLINE_FILE_MAX_BYTES = 4 * 1024 * 1024
# QQ/NapCat may truncate longer group-file names by bytes rather than Unicode
# characters.  Keep a small margin below the observed 100-byte boundary so a
# multibyte title is never cut into invalid UTF-8 by the remote client.
QQ_FILE_NAME_MAX_UTF8_BYTES = 96
QQ_OUTFIT_LIST_COMMANDS = {
    "服装列表",
    "可用服装",
    "可用服装列表",
    "有哪些服装",
    "查看服装列表",
    "衣服列表",
}
QQ_OUTFIT_CURRENT_COMMANDS = {
    "当前服装",
    "现在服装",
    "服装状态",
    "qq当前服装",
}
QQ_OUTFIT_DEFAULT_COMMANDS = {
    "切回默认服装",
    "恢复默认服装",
    "使用默认服装",
    "清除服装切换",
    "取消服装切换",
    "重置服装",
}
QQ_OUTFIT_SWITCH_PATTERNS = (
    re.compile(r"^(?:切换|更换|换)(?:到|成)?(?:QQ)?(?:服装|衣服|衣装)(?:为|到|成)?[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:使用|启用)(?:QQ)?(?:服装|衣服|衣装)[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:服装|衣服|衣装)(?:切换|切到|改为|换成)[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:服装|衣服|衣装)[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:穿上|换上)[:：\s]+(.+)$", re.IGNORECASE),
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
QQ_GROUP_VISION_ENABLE_COMMAND = "识图开"
QQ_GROUP_VISION_DISABLE_COMMAND = "识图关"
QQ_CHAT_MODEL_LIST_COMMANDS = {
    "模型列表",
    "可用模型",
    "可用模型列表",
    "查看模型列表",
    "有哪些模型",
}
QQ_CHAT_MODEL_CURRENT_COMMANDS = {
    "当前模型",
    "现在模型",
    "模型状态",
    "qq当前模型",
}
QQ_CHAT_MODEL_DEFAULT_COMMANDS = {
    "切回默认模型",
    "恢复默认模型",
    "使用默认模型",
    "清除模型切换",
    "取消模型切换",
    "重置模型",
}
QQ_CHAT_MODEL_SWITCH_PATTERNS = (
    re.compile(r"^(?:切换|更换|换)(?:到|成)?(?:QQ)?模型(?:为|到|成)?[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:使用|启用)(?:QQ)?模型[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^模型(?:切换|切到|改为|换成)[:：\s]+(.+)$", re.IGNORECASE),
    re.compile(r"^model[:：\s]+(.+)$", re.IGNORECASE),
)
QQ_THINKING_MODE_CURRENT_COMMANDS = {
    "思考模式",
    "思考状态",
    "当前思考模式",
    "思考模式状态",
}
QQ_THINKING_MODE_ENABLE_COMMANDS = {
    "思考模式开",
    "思考模式开启",
    "开启思考模式",
    "启用思考模式",
}
QQ_THINKING_MODE_DISABLE_COMMANDS = {
    "思考模式关",
    "思考模式关闭",
    "关闭思考模式",
    "禁用思考模式",
}
QQ_SHELL_PERMISSION_COMMAND_RE = re.compile(
    r"^[/／]shell(?:\s+(status|on|ask|off|状态|开启|询问|关闭))?$",
    re.IGNORECASE,
)
QQ_GATEWAY_STATE_SCHEMA_VERSION = "akane.qq_gateway_state.v1"

QQ_ECONOMY_CHECKIN_COMMANDS: frozenset[str] = frozenset({"签到", "每日签到", "领签到", "签到领奖"})
QQ_ECONOMY_STATUS_COMMANDS: frozenset[str] = frozenset({"我的状态", "养成状态", "查状态", "当前状态", "状态查询"})
QQ_ECONOMY_SHOP_COMMANDS: frozenset[str] = frozenset({"商店", "商店列表", "查看商店", "查商店"})
QQ_ECONOMY_BUY_PREFIXES: tuple[str, ...] = ("购买 ", "购买:", "购买：")
QQ_ECONOMY_FEED_PREFIXES: tuple[str, ...] = ("投喂 ",)
QQ_ECONOMY_FEED_COMMANDS: frozenset[str] = frozenset({"投喂"})

_QTY_RE = re.compile(r"^(.+?)\s*[Xx×*×](\d+)$")
QQ_ECONOMY_BACKPACK_COMMANDS: frozenset[str] = frozenset({"背包", "我的背包", "查看背包"})
QQ_ECONOMY_OFFERING_COMMANDS: frozenset[str] = frozenset({"供奉", "今日供奉"})
QQ_ECONOMY_LOTTERY_COMMANDS: frozenset[str] = frozenset({"抽签", "御神签", "求签", "抽御神签"})
QQ_ECONOMY_OFFERING_STATUS_COMMANDS: frozenset[str] = frozenset({"查看供奉", "供奉状态"})
QQ_ECONOMY_OFFERING_PREFIXES: tuple[str, ...] = ("供奉 ",)
VALID_USABLE_IN: frozenset[str] = frozenset({"desktop_pet", "qq"})

QQ_ECONOMY_STATUS_QUERY_FIELDS_RE = r"(?:养成)?状态|金币(?:余额)?|余额|饥饿(?:度|值)?|精力(?:值)?|体力|好感(?:度)?"
QQ_MFACE_CONFIG_COMMAND_RE = re.compile(
    r"^(?:表情包配置|抓表情包|提取表情包|mface配置|mface config)(?:[:：\s]+(.+?))?$",
    re.IGNORECASE,
)


def _is_economy_status_query(text: str) -> bool:
    """Recognize explicit QQ status commands without stealing natural chat."""
    normalized = str(text or "").strip()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if compact in QQ_ECONOMY_STATUS_COMMANDS:
        return True
    field = f"(?:{QQ_ECONOMY_STATUS_QUERY_FIELDS_RE})"
    if re.fullmatch(rf"(?:查|查看|看看|显示)(?:我的|当前|现在|QQ)?{field}", compact):
        return True
    if re.fullmatch(rf"(?:我的|当前|现在|QQ)?{field}(?:当前|现在)?(?:多少|几|状态|查询)?[？?]?", compact):
        return True
    return False


def _safe_qq_file_name(value: str, *, fallback: str) -> str:
    clean_name = Path(str(value or fallback).replace("\\", "/")).name.strip()
    if not clean_name:
        clean_name = Path(str(fallback or "file").replace("\\", "/")).name.strip() or "file"
    if len(clean_name.encode("utf-8")) <= QQ_FILE_NAME_MAX_UTF8_BYTES:
        return clean_name

    suffix = Path(clean_name).suffix
    stem = clean_name[: -len(suffix)] if suffix else clean_name
    marker = "…"
    fixed_bytes = len((marker + suffix).encode("utf-8"))
    stem_budget = max(1, QQ_FILE_NAME_MAX_UTF8_BYTES - fixed_bytes)
    kept: list[str] = []
    used_bytes = 0
    for character in stem:
        character_bytes = len(character.encode("utf-8"))
        if used_bytes + character_bytes > stem_budget:
            break
        kept.append(character)
        used_bytes += character_bytes
    short_stem = "".join(kept).rstrip(" ._-") or "file"
    return f"{short_stem}{marker}{suffix}"


@dataclass(frozen=True)
class QQMessageContext:
    should_respond: bool
    reason: str
    should_record: bool = False
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
    chat_model_override: str = ""
    attachments: list[dict[str, Any]] | None = None
    source_message_id: str = ""
    mentioned_bot: bool = False
    mentions: tuple[MentionRef, ...] = ()

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
        if self.is_group:
            payload["message_addressing"] = self._message_addressing()
        if self.is_group and self.user_id:
            payload["actor_stable_id"] = f"qq:{self.user_id}"
            payload["actor_display_name"] = self.sender_label
            payload["actor_platform"] = "qq"
        character_pack_id = _safe_character_pack_id(self.character_pack_id)
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
                "display_name": "" if mention.is_bot else mention.display_name,
                "is_assistant": bool(mention.is_bot),
            }
            for mention in self.mentions
            if str(mention.target_id or "").strip()
        ]
        addressed_to_assistant = bool(self.should_respond)
        primary_target = (
            {"actor_id": "assistant", "display_name": ""}
            if addressed_to_assistant
            else (
                {
                    "actor_id": str(mentions[0].get("actor_id") or ""),
                    "display_name": str(mentions[0].get("display_name") or ""),
                }
                if mentions
                else {}
            )
        )
        return {
            "mode": "current_request" if self.should_respond else "observed",
            "trigger": str(self.reason or ""),
            "addressed_to_assistant": addressed_to_assistant,
            "explicit_assistant_mention": bool(self.mentioned_bot),
            "primary_target": primary_target,
            "mentions": mentions,
        }

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
        if self.is_group and self.user_id:
            payload["actor_stable_id"] = f"qq:{self.user_id}"
            payload["actor_display_name"] = self.sender_label
            payload["actor_platform"] = "qq"
        character_pack_id = _safe_character_pack_id(self.character_pack_id)
        if character_pack_id:
            payload["character_pack_id"] = character_pack_id
        reply_mode = _safe_reply_mode(self.reply_mode, default="")
        if reply_mode:
            payload["reply_mode"] = reply_mode
        chat_model_override = _safe_chat_model_id(self.chat_model_override)
        if chat_model_override:
            payload["chat_model_override"] = chat_model_override
        return payload


class NapCatQQGateway:
    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        channel_config: QQChannelRuntimeConfig | None = None,
        default_character_pack_id: str = "",
        wake_words: tuple[str, ...] | list[str] | None = None,
        poke_reactor: PokeEventReactor | None = None,
    ) -> None:
        self._channel_config = channel_config
        transport_config = channel_config or QQChannelRuntimeConfig(
            enabled=bool(getattr(config, "QQ_BRIDGE_ENABLED", False)),
            profile_ref="",
            bot_id=str(getattr(config, "QQ_BOT_QQ", "") or "").strip(),
            onebot_http_url=str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip(),
            webhook_secret="",
            onebot_access_token="",
            require_webhook_auth=False,
            require_self_id=False,
        )
        self._onebot_transport = OneBotActionTransport(transport_config)
        self._reply_reference_claims: dict[tuple[str, str, str], None] = {}
        self._reply_reference_lock = threading.RLock()
        self._bound_default_character_pack_id = _safe_character_pack_id(default_character_pack_id)
        self._wake_words = _normalize_qq_wake_words(wake_words)
        self._wake_word_search_re = _compile_qq_wake_word_search(self._wake_words)
        self._wake_word_prefix_re = _compile_qq_wake_word_prefix(self._wake_words)
        self.poke_reactor = poke_reactor or PokeEventReactor()
        self._group_trigger = GroupTriggerPolicy(bot_account_id=self.bot_qq)
        require_self_id = self._channel_config.require_self_id if self._channel_config is not None else False
        self._event_admission = OneBotEventAdmission(
            EventAdmissionConfig(
                bot_account_id=self.bot_qq,
                require_self_id=require_self_id,
            )
        )
        self.sender_label_cache: dict[str, str] = {}
        self.attachment_debounce_state: dict[str, dict[str, Any]] = {}
        self._attachment_debounce_lock = threading.RLock()
        self._state_path = Path(state_path) if state_path is not None else None
        self.character_pack_overrides: dict[str, str] = {}
        self._character_pack_lock = threading.RLock()
        self.outfit_overrides: dict[str, str] = {}
        self._outfit_lock = threading.RLock()
        self.reply_mode_overrides: dict[str, str] = {}
        self._reply_mode_lock = threading.RLock()
        self.chat_model_overrides: dict[str, str] = {}
        self._chat_model_lock = threading.RLock()
        self.group_vision_overrides: dict[str, bool] = {}
        self._group_vision_lock = threading.RLock()
        self.emotion_mface_state: dict[str, dict[str, Any]] = {}
        self._emotion_mface_lock = threading.RLock()
        self.emotion_image_state: dict[str, dict[str, Any]] = {}
        self._emotion_image_lock = threading.RLock()
        self._delivery_notes: dict[str, list[str]] = {}
        self._delivery_notes_lock = threading.RLock()
        self._state_error = ""
        self._load_persisted_state()

    def _load_persisted_state(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._state_error = exc.__class__.__name__
            return
        if not isinstance(payload, dict):
            self._state_error = "invalid_state_payload"
            return
        overrides: dict[str, str] = {}
        raw_overrides = payload.get("character_pack_overrides")
        if isinstance(raw_overrides, dict):
            for raw_key, raw_value in raw_overrides.items():
                key = _safe_qq_session_key(raw_key)
                if not key:
                    continue
                pack_id = _safe_character_pack_id(raw_value)
                if str(raw_value or "").strip() and not pack_id:
                    continue
                overrides[key] = pack_id
        with self._character_pack_lock:
            self.character_pack_overrides = overrides

        outfit_overrides: dict[str, str] = {}
        raw_outfits = payload.get("outfit_overrides")
        if isinstance(raw_outfits, dict):
            for raw_key, raw_value in raw_outfits.items():
                key = _safe_qq_session_key(raw_key)
                outfit_id = _safe_outfit_id(raw_value)
                if key and outfit_id:
                    outfit_overrides[key] = outfit_id
        with self._outfit_lock:
            self.outfit_overrides = outfit_overrides

        model_overrides: dict[str, str] = {}
        raw_models = payload.get("chat_model_overrides")
        if isinstance(raw_models, dict):
            for raw_key, raw_value in raw_models.items():
                key = _safe_qq_session_key(raw_key)
                model = _safe_chat_model_id(raw_value)
                if key and model:
                    model_overrides[key] = model
        with self._chat_model_lock:
            self.chat_model_overrides = model_overrides

        group_vision_overrides: dict[str, bool] = {}
        raw_group_vision = payload.get("group_vision_overrides")
        if isinstance(raw_group_vision, dict):
            for raw_group_id, raw_enabled in raw_group_vision.items():
                group_id = self._safe_int(raw_group_id)
                if group_id > 0 and raw_enabled is False:
                    group_vision_overrides[str(group_id)] = False
        with self._group_vision_lock:
            self.group_vision_overrides = group_vision_overrides

    def _persist_gateway_state(self) -> bool:
        if self._state_path is None:
            self._state_error = ""
            return True
        try:
            with self._character_pack_lock:
                character_overrides = dict(self.character_pack_overrides)
            with self._outfit_lock:
                outfit_overrides = dict(self.outfit_overrides)
            with self._chat_model_lock:
                chat_model_overrides = dict(self.chat_model_overrides)
            with self._group_vision_lock:
                group_vision_overrides = dict(self.group_vision_overrides)
            payload = {
                "schema_version": QQ_GATEWAY_STATE_SCHEMA_VERSION,
                "character_pack_overrides": character_overrides,
                "outfit_overrides": outfit_overrides,
                "chat_model_overrides": chat_model_overrides,
                "group_vision_overrides": group_vision_overrides,
                "updated_at": int(time.time()),
            }
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_name(f"{self._state_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp_path.write_text(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(self._state_path)
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
        except OSError as exc:
            self._state_error = exc.__class__.__name__
            return False
        self._state_error = ""
        return True

    def _persist_character_pack_overrides(self) -> bool:
        return self._persist_gateway_state()

    def _persist_outfit_overrides(self) -> bool:
        return self._persist_gateway_state()

    def _persist_chat_model_overrides(self) -> bool:
        return self._persist_gateway_state()

    def _persist_group_vision_overrides(self) -> bool:
        return self._persist_gateway_state()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.bridge_enabled,
            "onebot_http_url": self.onebot_http_url,
            "master_qq": str(getattr(config, "MASTER_QQ", "") or ""),
            "bot_qq": self.bot_qq,
            "group_plaintext_enabled": bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False)),
            "event_max_age_seconds": int(getattr(config, "QQ_EVENT_MAX_AGE_SECONDS", 300) or 0),
            "allow_stale_events": bool(getattr(config, "QQ_ALLOW_STALE_EVENTS", False)),
            "character_pack_id": self.character_pack_id,
            "default_character_pack_id": self.default_character_pack_id,
            "active_character_override_count": len(self.character_pack_overrides),
            "active_outfit_override_count": len(self.outfit_overrides),
            "active_chat_model_override_count": len(self.chat_model_overrides),
            "disabled_group_vision_count": sum(
                1 for enabled in self.group_vision_overrides.values() if enabled is False
            ),
            "state_persistence_enabled": self._state_path is not None,
            "state_status": "error"
            if self._state_error
            else ("enabled" if self._state_path is not None else "disabled"),
            "state_error": self._state_error,
            "reply_mode": self.default_reply_mode,
            "default_reply_mode": self.default_reply_mode,
            "active_reply_mode_override_count": len(self.reply_mode_overrides),
            "active_emotion_mface_session_count": len(self.emotion_mface_state),
            "active_emotion_image_session_count": len(self.emotion_image_state),
            "active_group_attachment_buffer_count": self._group_trigger.active_window_count,
            "active_attachment_debounce_count": len(self.attachment_debounce_state),
            "active_event_fingerprint_count": self._event_admission.active_fingerprint_count,
        }

    def self_check(self) -> dict[str, Any]:
        """Check login and online state without exposing endpoint or credentials."""
        if not self.bridge_enabled:
            return {
                "ok": False,
                "status": "bridge_disabled",
                "reason": "QQ_BRIDGE_ENABLED 未启用；在 .env 里设置 QQ_BRIDGE_ENABLED=true 并配置 NapCat。",
            }
        login_result = self._onebot_transport.call("get_login_info", timeout=5)
        if not login_result.ok:
            return self._self_check_failure(login_result.code, login_result.public_reason)
        user_id = login_result.data.get("user_id") or ""
        nickname = login_result.data.get("nickname") or ""
        status_result = self._onebot_transport.call("get_status", timeout=5)
        if not status_result.ok:
            return self._self_check_failure(status_result.code, status_result.public_reason)
        status_data = status_result.data
        online = status_data.get("online", status_data.get("is_online"))
        if online is not True:
            return {
                "ok": False,
                "status": "account_offline" if online is False else "account_status_unknown",
                "reason": "QQ 账号当前不在线，请在 NapCat 中重新登录并确认未被下线。",
            }
        if status_data.get("good") is False:
            return {
                "ok": False,
                "status": "account_unhealthy",
                "reason": "QQ 账号在线，但 OneBot 报告运行状态异常。",
            }

        return {
            "ok": True,
            "status": "connected",
            "bot_qq": str(user_id) if user_id else self.bot_qq,
            "nickname": str(nickname),
            "checks": {
                "bridge_enabled": True,
                "url_reachable": True,
                "login_info": True,
                "account_online": True,
                "send_test": "not_tested",
            },
        }

    @staticmethod
    def _self_check_failure(code: str, public_reason: str) -> dict[str, Any]:
        status = {
            "invalid_base_url": "invalid_url",
            "timeout": "timeout",
            "connection_error": "unreachable",
            "auth_failed": "auth_failed",
            "http_error": "http_error",
            "redirect_rejected": "redirect_rejected",
            "invalid_json": "invalid_response",
            "invalid_response": "invalid_response",
            "onebot_status_error": "onebot_error",
            "onebot_retcode_error": "onebot_error",
        }.get(code, "connection_error")
        return {"ok": False, "status": status, "reason": public_reason or "OneBot 自检失败。"}

    @property
    def onebot_http_url(self) -> str:
        if self._channel_config is not None:
            return self._channel_config.onebot_http_url
        return (
            str(getattr(config, "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001") or "").strip().rstrip("/")
            or "http://127.0.0.1:3001"
        )

    @property
    def bot_qq(self) -> str:
        if self._channel_config is not None:
            return self._channel_config.bot_id
        return str(getattr(config, "QQ_BOT_QQ", "") or "").strip()

    @property
    def bridge_enabled(self) -> bool:
        if self._channel_config is not None:
            return self._channel_config.enabled
        return bool(getattr(config, "QQ_BRIDGE_ENABLED", False))

    @property
    def onebot_headers(self) -> dict[str, str]:
        if self._channel_config is None:
            return {}
        return self._channel_config.onebot_headers()

    @property
    def character_pack_id(self) -> str:
        return self.default_character_pack_id

    @property
    def default_character_pack_id(self) -> str:
        if self._bound_default_character_pack_id:
            return self._bound_default_character_pack_id
        return _safe_character_pack_id(getattr(config, "QQ_CHARACTER_PACK_ID", ""))

    @property
    def default_reply_mode(self) -> str:
        return _safe_reply_mode(getattr(config, "QQ_REPLY_MODE", "auto"), default="auto")

    @property
    def master_qq(self) -> str:
        value = str(getattr(config, "MASTER_QQ", "") or "").strip()
        return value if value.isdigit() else ""

    def is_group_vision_enabled(self, group_id: Any) -> bool:
        normalized_group_id = self._safe_int(group_id)
        if normalized_group_id <= 0:
            return True
        with self._group_vision_lock:
            return self.group_vision_overrides.get(str(normalized_group_id)) is not False

    def set_group_vision_enabled(self, group_id: Any, enabled: bool) -> bool:
        normalized_group_id = self._safe_int(group_id)
        if normalized_group_id <= 0:
            return False
        with self._group_vision_lock:
            if enabled:
                self.group_vision_overrides.pop(str(normalized_group_id), None)
            else:
                self.group_vision_overrides[str(normalized_group_id)] = False
        return self._persist_group_vision_overrides()

    def parse_group_vision_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_character_command_text(message)
        if text == QQ_GROUP_VISION_ENABLE_COMMAND:
            return {"action": "enable"}
        if text == QQ_GROUP_VISION_DISABLE_COMMAND:
            return {"action": "disable"}
        return None

    def handle_group_vision_command(
        self,
        context: QQMessageContext,
        *,
        sender_role: str = "",
    ) -> dict[str, Any] | None:
        command = self.parse_group_vision_command(context.clean_message)
        if command is None:
            return None
        if not context.is_group or context.group_id <= 0:
            return {
                "handled": True,
                "ok": False,
                "status": "group_only",
                "reply": "请在群内设置识图模式",
                "vision_enabled": True,
            }

        vision_enabled = self.is_group_vision_enabled(context.group_id)
        action = str(command.get("action") or "")
        role = str(sender_role or "").strip().lower()
        is_master = bool(self.master_qq) and str(context.user_id) == self.master_qq
        if role not in {"owner", "admin"} and not is_master:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有群主、群管理员或 Akane 主账号可以修改本群识图开关。",
                "vision_enabled": vision_enabled,
            }

        requested_enabled = action == "enable"
        state_persisted = self.set_group_vision_enabled(context.group_id, requested_enabled)
        if requested_enabled:
            reply = "识图模式已打开"
            status = "enabled"
        else:
            reply = "识图模式已关闭"
            status = "disabled"
        return {
            "handled": True,
            "ok": True,
            "status": status,
            "reply": self._append_state_persistence_warning(reply, state_persisted),
            "vision_enabled": requested_enabled,
            "state_persisted": state_persisted,
        }

    def build_message_context(self, event: dict[str, Any]) -> QQMessageContext:
        if str(event.get("post_type") or "").strip().lower() != "message":
            return self.build_notice_context(event)

        message_type = str(event.get("message_type") or "").strip().lower()
        is_private = message_type == "private"
        is_group = message_type == "group" or bool(event.get("group_id"))
        if not (is_private or is_group):
            return QQMessageContext(False, "unsupported_message_type")

        user_id = self._safe_int(event.get("user_id"))
        group_id = self._safe_int(event.get("group_id"))
        admission = self._admit_event(event)
        if admission is not None:
            return admission

        inbound_result = normalize_inbound_event(
            event,
            bot_account_id=self.bot_qq,
            wake_words=self._wake_words,
        )
        inbound = inbound_result.message
        if inbound is None:
            return QQMessageContext(False, inbound_result.reason or "invalid_event")
        raw_message = inbound.raw_text
        clean_message = inbound.text
        attachments = self._legacy_attachments(inbound.attachments)
        group_vision_enabled = not is_group or self.is_group_vision_enabled(group_id)
        has_group_image = bool(
            is_group
            and any(
                isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "image"
                for item in attachments
            )
        )
        unsupported_attachment_labels = {
            "video": "[视频]",
            "sticker": "[表情]",
            "emoji": "[表情]",
        }
        for attachment in inbound.attachments:
            label = unsupported_attachment_labels.get(attachment.kind)
            if label:
                clean_message = clean_message.replace(label, " ")
        clean_message = re.sub(r"\s+", " ", clean_message).strip()
        mentions = self.resolve_mention_labels(
            mentions=inbound.mentions,
            group_id=group_id,
        )
        mention_only = bool(mentions and not clean_message)
        if mention_only:
            clean_message = "event.mention"
        if not clean_message or (
            not inbound.has_text_content and not attachments and not mention_only
        ):
            return QQMessageContext(False, "empty_message")
        mentions_bot = inbound.mentioned_bot
        session_id, profile_user_id = self.resolve_identity(user_id=user_id, group_id=group_id)
        sender_label = inbound.actor.display_name or self.resolve_sender_label(event=event, user_id=user_id)
        if sender_label:
            self.sender_label_cache[self._sender_label_cache_key(group_id=group_id, user_id=user_id)] = sender_label
        mentions_wake_word = inbound.mentioned_wake_word
        character_pack_id = self.resolve_character_pack_id(session_id)
        reply_mode = self.resolve_reply_mode(session_id)
        chat_model_override = self.resolve_chat_model_override(session_id)

        shell_permission_command = self.parse_shell_permission_command(clean_message)
        group_reason = ""
        if is_group and shell_permission_command is not None:
            # Explicit control-plane commands must reach the authorization
            # handler even when the group normally requires @/wake-word.
            group_reason = "qq_shell_permission_command"
        elif is_group:
            trigger = self._group_trigger.evaluate(
                group_id=str(group_id),
                actor_id=str(user_id),
                mentioned_bot=mentions_bot,
                mentioned_wake_word=mentions_wake_word,
                has_attachments=bool(attachments),
                allow_attachment_follow=group_vision_enabled,
                ttl_seconds=getattr(
                    config,
                    "QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS",
                    getattr(config, "QQ_GROUP_FOLLOW_TTL_SECONDS", 180),
                ),
            )
            if not trigger.should_respond:
                suppress_passive_image = bool(has_group_image and not group_vision_enabled)
                unbound_group_image = bool(has_group_image)
                return QQMessageContext(
                    should_respond=False,
                    reason=(
                        "group_vision_disabled"
                        if suppress_passive_image
                        else ("group_passive_image_unbound" if unbound_group_image else trigger.reason)
                    ),
                    # An unaddressed image event has no attachment handle or
                    # pixels in MemCore. Recording "sent an image" would invite a
                    # later turn to mistake some older workspace image for it.
                    # A caption attached to unavailable pixels is incomplete
                    # evidence too, so the whole unaddressed image event stays
                    # outside passive memory.
                    should_record=not (suppress_passive_image or unbound_group_image),
                    is_group=True,
                    target_id=group_id,
                    user_id=user_id,
                    group_id=group_id,
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    clean_message=clean_message,
                    raw_message=raw_message,
                    sender_label=sender_label,
                    character_pack_id=character_pack_id,
                    reply_mode=reply_mode,
                    chat_model_override=chat_model_override,
                    attachments=attachments,
                    source_message_id=str(event.get("message_id") or "").strip(),
                    mentioned_bot=mentions_bot,
                    mentions=mentions,
                )
            group_reason = str(trigger.reason or "group")

        return QQMessageContext(
            should_respond=True,
            reason="private" if is_private else group_reason,
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
            chat_model_override=chat_model_override,
            attachments=attachments,
            source_message_id=str(event.get("message_id") or "").strip(),
            mentioned_bot=mentions_bot,
            mentions=mentions,
            extra_context=self.build_extra_context(
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
        inbound_result = normalize_inbound_event(
            event,
            bot_account_id=self.bot_qq,
            wake_words=self._wake_words,
        )
        inbound = inbound_result.message
        if inbound is None:
            return QQMessageContext(False, inbound_result.reason or "not_message_event")

        admission = self._admit_event(event)
        if admission is not None:
            return admission

        user_id = self._safe_int(inbound.actor.id)
        group_id = self._safe_int(inbound.conversation.id) if inbound.conversation.kind == "group" else 0
        is_group = bool(group_id)
        session_id, profile_user_id = self.resolve_identity(user_id=user_id, group_id=group_id)
        sender_label = inbound.actor.display_name or self.resolve_sender_label(event=event, user_id=user_id)
        if sender_label:
            self.sender_label_cache[self._sender_label_cache_key(group_id=group_id, user_id=user_id)] = sender_label
        character_pack_id = self.resolve_character_pack_id(session_id)
        reply_mode = self.resolve_reply_mode(session_id)
        chat_model_override = self.resolve_chat_model_override(session_id)
        actor_label = "我" if not is_group else (sender_label or (f"QQ {user_id}" if user_id else "这位 QQ 用户"))
        clean_message = f"刚才发生的互动：{actor_label}在 QQ 里戳了戳你的头像。"
        return QQMessageContext(
            should_respond=True,
            reason="qq_poke",
            is_group=is_group,
            target_id=group_id if is_group else user_id,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            clean_message=clean_message,
            raw_message="[QQ戳一戳]",
            sender_label=sender_label,
            character_pack_id=character_pack_id,
            reply_mode=reply_mode,
            chat_model_override=chat_model_override,
            attachments=[],
            extra_context=self.build_extra_context(
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
            chat_model_override=_safe_chat_model_id(value.get("chat_model_override") or value.get("chatModelOverride")),
            attachments=[],
            source_message_id=str(value.get("source_message_id") or value.get("sourceMessageId") or "").strip(),
        )

    def resolve_character_pack_id(self, session_id: str) -> str:
        key = _safe_qq_session_key(session_id)
        if not key:
            return self.default_character_pack_id
        with self._character_pack_lock:
            if key in self.character_pack_overrides:
                return _safe_character_pack_id(self.character_pack_overrides.get(key))
        return self.default_character_pack_id

    def resolve_session_outfit_id(self, session_id: str) -> str:
        key = _safe_qq_session_key(session_id)
        if not key:
            return ""
        with self._outfit_lock:
            return _safe_outfit_id(self.outfit_overrides.get(key))

    def set_session_outfit_id(self, session_id: str, outfit_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        outfit = _safe_outfit_id(outfit_id)
        if not key or not outfit:
            return False
        with self._outfit_lock:
            self.outfit_overrides[key] = outfit
        return self._persist_outfit_overrides()

    def clear_session_outfit_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._outfit_lock:
            self.outfit_overrides.pop(key, None)
        return self._persist_outfit_overrides()

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
            overflow_count = max(0, len(packs) - len(labels))
            return {
                "handled": True,
                "ok": True,
                "status": "listed",
                "reply": self._build_multiline_option_reply(
                    "可用角色包",
                    labels,
                    instruction="发送“切换角色 角色包id”即可切换当前 QQ 会话。",
                    overflow_count=overflow_count,
                ),
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
            state_persisted = self.clear_session_character_override(context.session_id)
            active_pack_id = self.resolve_character_pack_id(context.session_id)
            reply = self._build_default_character_reply(
                active_pack_id,
                character_resource_service=character_resource_service,
            )
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": self._append_state_persistence_warning(reply, state_persisted),
                "character_pack_id": active_pack_id,
                "state_persisted": state_persisted,
            }

        if action == "builtin":
            state_persisted = self.set_session_character_pack_id(context.session_id, "")
            return {
                "handled": True,
                "ok": True,
                "status": "builtin",
                "reply": self._append_state_persistence_warning(
                    "已切回内置 Akane 人设。之后这个 QQ 会话会使用未绑定角色包的默认聊天记忆。",
                    state_persisted,
                ),
                "character_pack_id": "",
                "state_persisted": state_persisted,
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
                state_persisted = self.set_session_character_pack_id(context.session_id, "")
                return {
                    "handled": True,
                    "ok": True,
                    "status": "builtin",
                    "reply": self._append_state_persistence_warning(
                        "已切回内置 Akane 人设。之后这个 QQ 会话会使用未绑定角色包的默认聊天记忆。",
                        state_persisted,
                    ),
                    "character_pack_id": "",
                    "state_persisted": state_persisted,
                }
            identity = self._resolve_pack_identity(
                requested_pack_id,
                character_resource_service=character_resource_service,
            )
            if not identity:
                packs = self._list_character_packs(character_resource_service)
                available = [str(item.get("pack_id") or "") for item in packs[:12] if str(item.get("pack_id") or "")]
                hint = self._build_multiline_available_hint(
                    available,
                    empty_message="可以先在角色工坊创建或导入角色包。",
                )
                return {
                    "handled": True,
                    "ok": False,
                    "status": "unknown_character_pack",
                    "reply": f"没有找到角色包 {requested_pack_id}。\n{hint}",
                    "character_pack_id": self.resolve_character_pack_id(context.session_id),
                    "requested_pack_id": requested_pack_id,
                }
            state_persisted = self.set_session_character_pack_id(context.session_id, requested_pack_id)
            label = self._format_identity_label(identity)
            reply = f"已切换本 QQ 会话角色为 {label}（{requested_pack_id}）。之后的聊天和记忆会按这个角色包隔离。"
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": self._append_state_persistence_warning(reply, state_persisted),
                "character_pack_id": requested_pack_id,
                "requested_pack_id": requested_pack_id,
                "state_persisted": state_persisted,
            }

        return None

    def handle_outfit_command(
        self,
        context: QQMessageContext,
        *,
        resource_manifest_builder: Any = None,
    ) -> dict[str, Any] | None:
        command = self.parse_outfit_command(context.clean_message)
        if command is None:
            return None

        active_pack_id = self.resolve_character_pack_id(context.session_id)
        if not active_pack_id:
            return {
                "handled": True,
                "ok": False,
                "status": "missing_character_pack",
                "reply": "当前 QQ 会话还没有绑定角色包。请先发送“角色列表”或“切换角色 角色包id”。",
                "character_pack_id": "",
                "outfit_id": "",
            }

        outfits = self._list_manifest_outfits(
            active_pack_id,
            resource_manifest_builder=resource_manifest_builder,
        )
        action = str(command.get("action") or "")
        if action == "list":
            if not outfits:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "no_outfits",
                    "reply": f"角色包 {active_pack_id} 当前没有可用服装资源。",
                    "character_pack_id": active_pack_id,
                    "outfit_id": self.resolve_session_outfit_id(context.session_id),
                }
            labels = [self._format_outfit_label(item) for item in outfits[:20]]
            overflow_count = max(0, len(outfits) - len(labels))
            return {
                "handled": True,
                "ok": True,
                "status": "listed",
                "reply": self._build_multiline_option_reply(
                    "可用服装",
                    labels,
                    instruction="发送“切换服装 服装id”即可切换当前 QQ 会话的服装感知。",
                    overflow_count=overflow_count,
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": self.resolve_session_outfit_id(context.session_id),
                "available_outfits": [str(item.get("id") or "") for item in outfits if str(item.get("id") or "")],
            }

        if action == "current":
            active_outfit = self.resolve_session_outfit_id(context.session_id)
            default_outfit = self._manifest_default_outfit(
                active_pack_id,
                resource_manifest_builder=resource_manifest_builder,
                outfits=outfits,
            )
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_outfit_reply(
                    active_outfit or default_outfit,
                    default_outfit=default_outfit,
                    outfits=outfits,
                    session_id=context.session_id,
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": active_outfit or default_outfit,
            }

        if action == "default":
            state_persisted = self.clear_session_outfit_override(context.session_id)
            default_outfit = self._manifest_default_outfit(
                active_pack_id,
                resource_manifest_builder=resource_manifest_builder,
                outfits=outfits,
            )
            outfit_label = self._format_outfit_label(
                self._find_outfit(outfits, default_outfit) or {"id": default_outfit}
            )
            note = (
                f"【当前换装】用户刚把你在当前 QQ 会话中的服装恢复为角色包默认服装：{outfit_label}。"
                "角色本身没有切换，只是同一个角色换回默认服装；请自然回应这次换装，不要像系统通知。"
            )
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "_llm_passthrough": True,
                "qq_action_note": note,
                "turn_message": self._build_outfit_event_turn_message(
                    context,
                    f"把你的 QQ 当前会话服装恢复为默认服装：{outfit_label}。",
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": default_outfit,
                "state_persisted": state_persisted,
            }

        if action == "switch":
            requested_outfit = _safe_outfit_id(command.get("outfit_id"))
            if not requested_outfit:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_outfit_id",
                    "reply": "这个服装名不太对。请不要包含路径分隔符或过长文本，比如“切换服装 default”。",
                    "character_pack_id": active_pack_id,
                    "outfit_id": self.resolve_session_outfit_id(context.session_id),
                }
            outfit = self._find_outfit(outfits, requested_outfit)
            if outfit is None:
                available = [self._format_outfit_label(item) for item in outfits[:12]]
                hint = self._build_multiline_available_hint(
                    available,
                    empty_message="这个角色包暂时没有可用服装资源。",
                )
                return {
                    "handled": True,
                    "ok": False,
                    "status": "unknown_outfit",
                    "reply": f"没有找到服装 {requested_outfit}。\n{hint}",
                    "character_pack_id": active_pack_id,
                    "outfit_id": self.resolve_session_outfit_id(context.session_id),
                    "requested_outfit_id": requested_outfit,
                }
            outfit_id = str(outfit.get("id") or requested_outfit).strip()
            state_persisted = self.set_session_outfit_id(context.session_id, outfit_id)
            outfit_label = self._format_outfit_label(outfit)
            note = (
                f"【当前换装】用户刚把你在当前 QQ 会话中的服装切换为：{outfit_label}。"
                "角色本身没有切换，只是同一个角色换了这套衣服；请按当前服装自然回应，可以简短提到穿着感受，"
                "不要复述系统字段。"
            )
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "_llm_passthrough": True,
                "qq_action_note": note,
                "turn_message": self._build_outfit_event_turn_message(
                    context,
                    f"把你的 QQ 当前会话服装切换为：{outfit_label}。",
                ),
                "character_pack_id": active_pack_id,
                "outfit_id": outfit_id,
                "requested_outfit_id": requested_outfit,
                "state_persisted": state_persisted,
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

    def parse_outfit_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_character_command_text(message)
        if not text:
            return None
        if text in QQ_OUTFIT_LIST_COMMANDS:
            return {"action": "list"}
        if text in QQ_OUTFIT_CURRENT_COMMANDS:
            return {"action": "current"}
        if text in QQ_OUTFIT_DEFAULT_COMMANDS:
            return {"action": "default"}
        for pattern in QQ_OUTFIT_SWITCH_PATTERNS:
            match = pattern.fullmatch(text)
            if not match:
                continue
            outfit_id = _safe_outfit_id(match.group(1))
            return {"action": "switch", "outfit_id": outfit_id}
        return None

    def parse_chat_model_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_character_command_text(message)
        if not text:
            return None
        if text in QQ_CHAT_MODEL_LIST_COMMANDS:
            return {"action": "list"}
        if text in QQ_CHAT_MODEL_CURRENT_COMMANDS:
            return {"action": "current"}
        if text in QQ_CHAT_MODEL_DEFAULT_COMMANDS:
            return {"action": "default"}
        for pattern in QQ_CHAT_MODEL_SWITCH_PATTERNS:
            match = pattern.fullmatch(text)
            if not match:
                continue
            return {"action": "switch", "model": _safe_chat_model_id(match.group(1))}
        return None

    def parse_thinking_mode_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_character_command_text(message)
        compact = re.sub(r"[\s:：]+", "", text)
        if compact in QQ_THINKING_MODE_CURRENT_COMMANDS:
            return {"action": "current"}
        if compact in QQ_THINKING_MODE_ENABLE_COMMANDS:
            return {"action": "enable"}
        if compact in QQ_THINKING_MODE_DISABLE_COMMANDS:
            return {"action": "disable"}
        return None

    def parse_shell_permission_command(self, message: str) -> dict[str, str] | None:
        text = re.sub(r"\s+", " ", str(message or "").strip())
        match = QQ_SHELL_PERMISSION_COMMAND_RE.fullmatch(text)
        if match is None:
            return None
        argument = str(match.group(1) or "status").strip().lower()
        action = {
            "status": "status",
            "状态": "status",
            "on": "on",
            "开启": "on",
            "ask": "ask",
            "询问": "ask",
            "off": "off",
            "关闭": "off",
        }.get(argument)
        return {"action": action} if action else None

    def handle_shell_permission_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        current_mode: str = "disabled",
        supported: bool = True,
        apply_mode: Callable[[str], str] | None = None,
    ) -> dict[str, Any] | None:
        command = command or self.parse_shell_permission_command(context.clean_message)
        if command is None:
            return None

        normalized_current = str(current_mode or "").strip().lower()
        if normalized_current not in {"trusted_auto_allow", "ask_each_time", "disabled"}:
            normalized_current = "disabled"
        scope_label = "本群" if context.is_group else "当前私聊"
        action = str(command.get("action") or "")
        if action == "status":
            mode_label = {
                "trusted_auto_allow": "已开启（直接执行）",
                "ask_each_time": "每次询问",
                "disabled": "已关闭",
            }[normalized_current]
            support_note = "" if supported else "\n宿主的 QQ Shell 总闸或执行提供者当前未启用。"
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": (
                    f"{scope_label} Shell：{mode_label}。{support_note}\n"
                    "/shell on 直接执行 · /shell ask 每次审批 · /shell off 关闭"
                ),
                "approval_mode": normalized_current,
                "supported": supported,
            }

        master_qq = self._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq <= 0 or int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "只有 Akane 主人账号可以调节 Shell 权限。",
                "approval_mode": normalized_current,
                "supported": supported,
            }

        target_mode = {
            "on": "trusted_auto_allow",
            "ask": "ask_each_time",
            "off": "disabled",
        }.get(action, "")
        if not target_mode:
            return {
                "handled": True,
                "ok": False,
                "status": "invalid_action",
                "reply": "这个 Shell 指令不支持。可用：/shell on、/shell ask、/shell off、/shell status。",
                "approval_mode": normalized_current,
                "supported": supported,
            }
        if target_mode != "disabled" and not supported:
            return {
                "handled": True,
                "ok": False,
                "status": "execution_unavailable",
                "reply": "宿主尚未启用 QQ Shell 或执行提供者不可用，权限没有修改。",
                "approval_mode": normalized_current,
                "supported": False,
            }
        if apply_mode is None:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_unavailable",
                "reply": "当前运行环境不能保存 Shell 权限，原配置保持不变。",
                "approval_mode": normalized_current,
                "supported": supported,
            }
        try:
            applied_mode = str(apply_mode(target_mode) or "").strip().lower()
        except Exception:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_failed",
                "reply": "Shell 权限保存失败，原配置保持不变。",
                "approval_mode": normalized_current,
                "supported": supported,
            }
        if applied_mode != target_mode:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_mismatch",
                "reply": "Shell 权限没有切换成功，原配置保持不变。",
                "approval_mode": normalized_current,
                "supported": supported,
            }

        if target_mode == "trusted_auto_allow":
            reply = (
                "本群 Shell 已开启：群成员提出的任务可由模型在 Bot 所在机器直接执行，不再逐条审批。"
                if context.is_group
                else "当前私聊 Shell 已开启：模型可在 Bot 所在机器直接执行，不再逐条审批。"
            )
            status = "enabled"
        elif target_mode == "ask_each_time":
            reply = f"{scope_label} Shell 已改为每次审批；命令获批前不会执行。"
            status = "ask_each_time"
        else:
            reply = f"{scope_label} Shell 已关闭；模型从下一条消息起看不到执行工具。"
            status = "disabled"
        return {
            "handled": True,
            "ok": True,
            "status": status,
            "reply": reply,
            "approval_mode": applied_mode,
            "supported": supported,
            "state_persisted": True,
        }

    def handle_thinking_mode_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        current_mode: str = "disabled",
        supported: bool = True,
        apply_mode: Callable[[str], str] | None = None,
    ) -> dict[str, Any] | None:
        command = command or self.parse_thinking_mode_command(context.clean_message)
        if command is None:
            return None

        normalized_current = str(current_mode or "").strip().lower()
        if normalized_current not in {"enabled", "disabled"}:
            normalized_current = "disabled"
        master_qq = self._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq and int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "这个命令只允许主人使用。",
                "thinking_mode": normalized_current,
                "supported": supported,
            }

        action = str(command.get("action") or "")
        if action == "current":
            label = "开启" if normalized_current == "enabled" else "关闭"
            support_note = "" if supported else "\n当前聊天模型不支持 DeepSeek thinking 开关。"
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": (
                    f"当前思考模式：{label}。{support_note}\n"
                    "发送“思考模式 开”或“思考模式 关”切换。"
                ),
                "thinking_mode": normalized_current,
                "supported": supported,
            }

        target_mode = {"enable": "enabled", "disable": "disabled"}.get(action, "")
        if not target_mode:
            return {
                "handled": True,
                "ok": False,
                "status": "invalid_action",
                "reply": "这个思考模式指令暂时不支持。",
                "thinking_mode": normalized_current,
                "supported": supported,
            }
        if not supported:
            return {
                "handled": True,
                "ok": False,
                "status": "unsupported_current_model",
                "reply": "当前聊天模型不支持 DeepSeek thinking 开关，配置没有修改。",
                "thinking_mode": normalized_current,
                "supported": False,
            }
        if apply_mode is None:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_unavailable",
                "reply": "当前运行环境暂时不能切换思考模式，配置没有修改。",
                "thinking_mode": normalized_current,
                "supported": True,
            }
        try:
            applied_mode = str(apply_mode(target_mode) or "").strip().lower()
        except Exception:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_failed",
                "reply": "思考模式切换失败，原配置保持不变。",
                "thinking_mode": normalized_current,
                "supported": True,
            }
        if applied_mode != target_mode:
            return {
                "handled": True,
                "ok": False,
                "status": "runtime_update_mismatch",
                "reply": "思考模式没有切换成功，原配置保持不变。",
                "thinking_mode": normalized_current,
                "supported": True,
            }

        enabled = applied_mode == "enabled"
        return {
            "handled": True,
            "ok": True,
            "status": "enabled" if enabled else "disabled",
            "reply": (
                "已开启 DeepSeek 思考模式；从下一条消息开始生效，开启时不会发送温度参数。"
                if enabled
                else "已关闭 DeepSeek 思考模式；从下一条消息开始恢复非思考采样。"
            ),
            "thinking_mode": applied_mode,
            "supported": True,
            "state_persisted": True,
        }

    def set_session_character_pack_id(self, session_id: str, character_pack_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._character_pack_lock:
            self.character_pack_overrides[key] = _safe_character_pack_id(character_pack_id)
        with self._outfit_lock:
            self.outfit_overrides.pop(key, None)
        return self._persist_character_pack_overrides()

    def clear_session_character_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._character_pack_lock:
            self.character_pack_overrides.pop(key, None)
        with self._outfit_lock:
            self.outfit_overrides.pop(key, None)
        return self._persist_character_pack_overrides()

    def resolve_chat_model_override(self, session_id: str) -> str:
        key = _safe_qq_session_key(session_id)
        if not key:
            return ""
        with self._chat_model_lock:
            return _safe_chat_model_id(self.chat_model_overrides.get(key))

    def resolve_chat_model(self, session_id: str, *, default_model: str = "") -> str:
        return self.resolve_chat_model_override(session_id) or _safe_chat_model_id(default_model)

    def set_session_chat_model_override(self, session_id: str, model: str) -> bool:
        key = _safe_qq_session_key(session_id)
        model_id = _safe_chat_model_id(model)
        if not key or not model_id:
            return False
        with self._chat_model_lock:
            self.chat_model_overrides[key] = model_id
        return self._persist_chat_model_overrides()

    def clear_session_chat_model_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._chat_model_lock:
            self.chat_model_overrides.pop(key, None)
        return self._persist_chat_model_overrides()

    def clear_all_chat_model_overrides(self) -> bool:
        with self._chat_model_lock:
            if not self.chat_model_overrides:
                return True
            self.chat_model_overrides.clear()
        return self._persist_chat_model_overrides()

    def handle_chat_model_command(
        self,
        context: QQMessageContext,
        *,
        command: dict[str, str] | None = None,
        default_model: str = "",
        available_models: list[str] | None = None,
        list_error: str = "",
    ) -> dict[str, Any] | None:
        command = command or self.parse_chat_model_command(context.clean_message)
        if command is None:
            return None

        master_qq = self._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq and int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "这个命令只允许主人使用。",
                "chat_model": self.resolve_chat_model(
                    context.session_id,
                    default_model=default_model,
                ),
                "chat_model_override": self.resolve_chat_model_override(context.session_id),
            }

        default_model_id = _safe_chat_model_id(default_model)
        active_override = self.resolve_chat_model_override(context.session_id)
        active_model = active_override or default_model_id
        action = str(command.get("action") or "")
        if action == "list":
            labels = self._ordered_chat_model_ids(
                available_models,
                preferred_model=active_model or default_model_id,
            )
            if labels:
                visible_labels = labels[:40]
                return {
                    "handled": True,
                    "ok": True,
                    "status": "listed",
                    "reply": self._build_numbered_model_reply(
                        visible_labels,
                        instruction=(
                            "发送“切换模型 序号”即可切换；也可以只写基础模型名，"
                            "系统会优先选择当前默认分组。"
                        ),
                        overflow_count=max(0, len(labels) - 40),
                    ),
                    "chat_model": active_model,
                    "chat_model_override": active_override,
                    "available_models": labels,
                }
            reason = str(list_error or "").strip()
            reply = "当前供应商没有返回可用模型列表。"
            if reason:
                reply += f"\n原因：{reason}"
            reply += "\n仍可直接发送“切换模型 模型名”手动切换。"
            return {
                "handled": True,
                "ok": False,
                "status": "model_list_unavailable",
                "reply": reply,
                "chat_model": active_model,
                "chat_model_override": active_override,
                "available_models": [],
            }
        if action == "current":
            return {
                "handled": True,
                "ok": True,
                "status": "current",
                "reply": self._build_current_chat_model_reply(
                    active_model,
                    has_override=bool(active_override),
                    default_model=default_model_id,
                ),
                "chat_model": active_model,
                "chat_model_override": active_override,
            }
        if action == "default":
            state_persisted = self.clear_session_chat_model_override(context.session_id)
            active_model = _safe_chat_model_id(default_model)
            reply = f"已恢复 QQ 默认聊天模型：{active_model or '未配置'}。"
            return {
                "handled": True,
                "ok": True,
                "status": "default",
                "reply": self._append_state_persistence_warning(reply, state_persisted),
                "chat_model": active_model,
                "chat_model_override": "",
                "state_persisted": state_persisted,
            }
        if action == "switch":
            requested_model = _safe_chat_model_id(command.get("model"))
            model_id, resolution_status, candidates = self._resolve_chat_model_choice(
                requested_model,
                available_models=available_models,
                preferred_model=default_model_id or active_model,
            )
            if not requested_model:
                return {
                    "handled": True,
                    "ok": False,
                    "status": "invalid_chat_model",
                    "reply": "这个模型名不太对。请发送“模型列表”查看可用序号和完整模型名。",
                    "chat_model": active_model,
                    "chat_model_override": active_override,
                }
            if not model_id:
                if resolution_status == "index_out_of_range":
                    count = len(self._ordered_chat_model_ids(available_models, preferred_model=default_model_id))
                    reply = f"模型序号超出范围，当前可选范围是 1-{count}。请重新发送“模型列表”查看。"
                elif resolution_status == "ambiguous":
                    reply = self._build_multiline_option_reply(
                        "这个基础模型名对应多个分组",
                        candidates[:8],
                        instruction="请发送完整模型名，或先发送“模型列表”再按序号切换。",
                        overflow_count=max(0, len(candidates) - 8),
                    )
                elif resolution_status == "model_list_unavailable":
                    reason = str(list_error or "").strip()
                    reply = "当前无法读取模型列表，因此不能按序号切换。请稍后再试，或发送完整模型名。"
                    if reason:
                        reply += f"\n原因：{reason}"
                else:
                    reply = (
                        f"当前供应商没有返回模型“{requested_model}”。"
                        "请发送“模型列表”查看可用项，避免切到不存在的分组。"
                    )
                return {
                    "handled": True,
                    "ok": False,
                    "status": resolution_status or "model_unavailable",
                    "reply": reply,
                    "chat_model": active_model,
                    "chat_model_override": active_override,
                }
            state_persisted = self.set_session_chat_model_override(context.session_id, model_id)
            reply = f"已把当前 QQ 会话聊天模型切换为：{model_id}。"
            return {
                "handled": True,
                "ok": True,
                "status": "switched",
                "reply": self._append_state_persistence_warning(reply, state_persisted),
                "chat_model": model_id,
                "chat_model_override": model_id,
                "state_persisted": state_persisted,
            }
        return None

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

    def handle_mface_config_command(
        self,
        context: QQMessageContext,
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        emotion_key = self.parse_mface_config_command(context.clean_message)
        if emotion_key is None:
            return None

        master_qq = self._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq and int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "这个命令只允许主人使用。",
                "character_pack_id": context.character_pack_id,
            }

        mfaces = self.extract_mface_payloads(event)
        if not mfaces:
            return {
                "handled": True,
                "ok": False,
                "status": "missing_mface",
                "reply": "这条消息里没抓到 NapCat mface 字段。请把“表情包配置 happy”和要抓的 QQ 表情包一起发，或转发一条包含表情包的消息。",
                "character_pack_id": context.character_pack_id,
            }

        emotion = emotion_key or "happy"
        mface = mfaces[0]
        snippet = {
            "qq_delivery": {
                "emotion_mfaces": {
                    "enabled": True,
                    "min_interval_seconds": 20,
                    "map": {
                        emotion: mface,
                    },
                },
            },
        }
        snippet_text = json.dumps(snippet, ensure_ascii=False, indent=2)
        pack_id = context.character_pack_id or "(内置 Akane，无角色包)"
        return {
            "handled": True,
            "ok": True,
            "status": "captured",
            "reply": (f"已抓到当前 QQ 会话角色包 {pack_id} 的表情包配置片段，emotion={emotion}：\n{snippet_text}"),
            "character_pack_id": context.character_pack_id,
            "emotion": emotion,
            "mface": mface,
            "snippet": snippet,
        }

    def parse_mface_config_command(self, message: str) -> str | None:
        text = self._normalize_character_command_text(message)
        text = re.sub(r"\[(?:图片|表情|文件|语音)\]", "", text).strip()
        if not text:
            return None
        match = QQ_MFACE_CONFIG_COMMAND_RE.fullmatch(text)
        if not match:
            return None
        emotion = str(match.group(1) or "").strip()
        emotion = re.sub(r"\s+", "_", emotion)
        return emotion[:80] or "happy"

    def extract_mface_payloads(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        segments = event.get("message") if isinstance(event, dict) else None
        if isinstance(segments, list):
            for item in segments:
                if not isinstance(item, dict):
                    continue
                seg_type = str(item.get("type") or "").strip().lower()
                seg_data = item.get("data") if isinstance(item.get("data"), dict) else {}
                if seg_type in {"mface", "market_face", "marketface"} or (
                    seg_type == "image" and _normalize_mface_payload(seg_data)
                ):
                    mface = _normalize_mface_payload(seg_data)
                    if mface:
                        payloads.append(mface)
            if payloads:
                return payloads

        raw_message = self.extract_message_text(event)
        for match in re.finditer(r"\[CQ:(mface|image)(?:,([^\]]*))?\]", raw_message, flags=re.IGNORECASE):
            data = self._parse_cq_params(match.group(2) or "")
            mface = _normalize_mface_payload(data)
            if mface:
                payloads.append(mface)
        return payloads

    def _build_current_reply_mode_reply(self, active_mode: str, *, session_id: str = "") -> str:
        key = str(session_id or "").strip()
        with self._reply_mode_lock:
            has_override = bool(key and key in self.reply_mode_overrides)
        source = "本会话临时切换" if has_override else "QQ 默认配置"
        return f"当前 QQ 会话回复模式：{self._format_reply_mode_label(active_mode)}（来源：{source}）。"

    def _build_current_chat_model_reply(self, active_model: str, *, has_override: bool, default_model: str = "") -> str:
        source = "本会话临时切换" if has_override else "QQ 默认配置"
        lines = ["当前 QQ 会话聊天模型", "─" * 18]
        lines.append(f"  模型：{_safe_chat_model_id(active_model) or '未配置'}")
        lines.append(f"  来源：{source}")
        if has_override and _safe_chat_model_id(default_model):
            lines.append(f"  默认：{_safe_chat_model_id(default_model)}")
        return "\n".join(lines)

    def _ordered_chat_model_ids(
        self,
        available_models: list[str] | None,
        *,
        preferred_model: str = "",
    ) -> list[str]:
        models: list[str] = []
        seen: set[str] = set()
        for raw_model in list(available_models or []):
            model = _safe_chat_model_id(raw_model)
            if not model or model in seen:
                continue
            seen.add(model)
            models.append(model)
        preferred = _safe_chat_model_id(preferred_model)
        if preferred and all(model.casefold() != preferred.casefold() for model in models):
            # Some compatible providers accept a configured model while
            # omitting it from /models. The configured default is still a
            # verified route and must remain selectable/listed.
            models.append(preferred)
        preferred_prefix = _chat_model_route_prefix(preferred)
        return sorted(
            models,
            key=lambda model: (
                0 if preferred and model.casefold() == preferred.casefold() else 1,
                0
                if preferred_prefix and _chat_model_route_prefix(model).casefold() == preferred_prefix.casefold()
                else 1,
                _chat_model_base_id(model).casefold(),
                model.casefold(),
            ),
        )

    def _resolve_chat_model_choice(
        self,
        requested_model: str,
        *,
        available_models: list[str] | None,
        preferred_model: str = "",
    ) -> tuple[str, str, list[str]]:
        requested = _safe_chat_model_id(requested_model)
        if not requested:
            return "", "invalid_chat_model", []
        if available_models is None:
            if requested.isdecimal():
                return "", "model_list_unavailable", []
            return requested, "direct", []
        models = self._ordered_chat_model_ids(
            available_models,
            preferred_model=preferred_model,
        )
        if not models:
            if requested.isdecimal():
                return "", "model_list_unavailable", []
            return requested, "direct", []
        if requested.isdecimal():
            index = int(requested)
            if 1 <= index <= len(models):
                return models[index - 1], "index", []
            return "", "index_out_of_range", []
        for model in models:
            if model.casefold() == requested.casefold():
                return model, "exact", []
        if _chat_model_route_prefix(requested):
            return "", "model_unavailable", []
        candidates = [
            model
            for model in models
            if _chat_model_base_id(model).casefold() == requested.casefold()
        ]
        if len(candidates) == 1:
            return candidates[0], "unique_base_name", []
        preferred_prefix = _chat_model_route_prefix(preferred_model)
        preferred_candidates = [
            model
            for model in candidates
            if preferred_prefix
            and _chat_model_route_prefix(model).casefold() == preferred_prefix.casefold()
        ]
        if len(preferred_candidates) == 1:
            return preferred_candidates[0], "preferred_group", []
        if candidates:
            return "", "ambiguous", candidates
        return "", "model_unavailable", []

    def _build_numbered_model_reply(
        self,
        labels: list[str],
        *,
        instruction: str,
        overflow_count: int = 0,
    ) -> str:
        lines = ["当前供应商可用模型", "─" * 18]
        lines.extend(f"  {index}. {label}" for index, label in enumerate(labels, start=1))
        if overflow_count > 0:
            lines.append(f"还有 {overflow_count} 个未显示。")
        instruction_text = str(instruction or "").strip()
        if instruction_text:
            lines.append("─" * 18)
            lines.append(instruction_text)
        return "\n".join(lines)

    def _format_reply_mode_label(self, reply_mode: str) -> str:
        return QQ_REPLY_MODE_LABELS.get(_safe_reply_mode(reply_mode), QQ_REPLY_MODE_LABELS["auto"])

    def _normalize_character_command_text(self, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        text = self._strip_wake_word_command_prefix(text)
        text = QQ_CHARACTER_COMMAND_PREFIX_RE.sub("", text, count=1).strip()
        return text.strip()

    def _strip_wake_word_command_prefix(self, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        match = self._wake_word_prefix_re.match(text)
        if not match:
            return text
        return text[match.end() :].strip()

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
                    "app_name": str(item.get("app_name") or item.get("appName") or item.get("name") or pack_id).strip()[
                        :80
                    ],
                    "user_title": str(item.get("user_title") or item.get("userTitle") or "").strip()[:80],
                }
            )
        return normalized

    def _list_manifest_outfits(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
    ) -> list[dict[str, Any]]:
        manifest = self._build_outfit_manifest(
            character_pack_id,
            resource_manifest_builder=resource_manifest_builder,
        )
        raw_outfits = manifest.get("characters", {}).get("outfits") if isinstance(manifest, dict) else []
        outfits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_outfits if isinstance(raw_outfits, list) else []:
            if not isinstance(item, dict):
                continue
            outfit_id = _safe_outfit_id(item.get("id") or item.get("name"))
            if not outfit_id or outfit_id in seen:
                continue
            seen.add(outfit_id)
            aliases = [_safe_outfit_id(alias) for alias in list(item.get("aliases") or []) if _safe_outfit_id(alias)]
            outfits.append(
                {
                    **item,
                    "id": outfit_id,
                    "name": str(item.get("name") or outfit_id).strip()[:80],
                    "aliases": aliases,
                }
            )
        return outfits

    def _build_outfit_manifest(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
    ) -> dict[str, Any]:
        if resource_manifest_builder is None:
            return {}
        try:
            manifest = resource_manifest_builder(character_pack_id)
        except TypeError:
            try:
                manifest = resource_manifest_builder(character_pack_id=character_pack_id)
            except Exception:
                return {}
        except Exception:
            return {}
        return manifest if isinstance(manifest, dict) else {}

    def _manifest_default_outfit(
        self,
        character_pack_id: str,
        *,
        resource_manifest_builder: Any = None,
        outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        outfit_items = list(outfits or [])
        manifest = self._build_outfit_manifest(
            character_pack_id,
            resource_manifest_builder=resource_manifest_builder,
        )
        defaults = manifest.get("defaults") if isinstance(manifest.get("defaults"), dict) else {}
        clients = manifest.get("clients") if isinstance(manifest.get("clients"), dict) else {}
        desktop = clients.get("desktop_pet") if isinstance(clients.get("desktop_pet"), dict) else {}
        candidates = [
            desktop.get("default_outfit"),
            defaults.get("desktop_pet_outfit"),
            defaults.get("outfit"),
        ]
        for candidate in candidates:
            outfit = self._find_outfit(outfit_items, _safe_outfit_id(candidate))
            if outfit is not None:
                return str(outfit.get("id") or "").strip()
        if outfit_items:
            return str(outfit_items[0].get("id") or "").strip()
        return _safe_outfit_id(candidates[0] if candidates else "")

    def _find_outfit(self, outfits: list[dict[str, Any]], requested: str) -> dict[str, Any] | None:
        target = _outfit_lookup_key(requested)
        if not target:
            return None
        for outfit in outfits:
            candidates = [
                outfit.get("id"),
                outfit.get("name"),
                *list(outfit.get("aliases") or []),
            ]
            if any(_outfit_lookup_key(candidate) == target for candidate in candidates):
                return outfit
        return None

    def _build_multiline_option_reply(
        self,
        title: str,
        labels: list[str],
        *,
        instruction: str,
        overflow_count: int = 0,
    ) -> str:
        lines = [str(title or "").strip() or "可用选项", "─" * 18]
        lines.extend(f"  {label}" for label in labels if str(label or "").strip())
        if overflow_count > 0:
            lines.append(f"还有 {overflow_count} 个未显示。")
        instruction_text = str(instruction or "").strip()
        if instruction_text:
            lines.append("─" * 18)
            lines.append(instruction_text)
        return "\n".join(lines)

    def _build_multiline_available_hint(self, labels: list[str], *, empty_message: str) -> str:
        visible_labels = [str(label or "").strip() for label in labels if str(label or "").strip()]
        if not visible_labels:
            return str(empty_message or "").strip()
        lines = ["当前可用："]
        lines.extend(f"  {label}" for label in visible_labels)
        return "\n".join(lines)

    def _format_outfit_label(self, item: dict[str, Any]) -> str:
        outfit_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        return (
            f"{outfit_id}（{name}）"
            if outfit_id and name and name != outfit_id
            else (outfit_id or name or "未命名服装")
        )

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

    def _append_state_persistence_warning(self, reply: str, state_persisted: bool) -> str:
        if state_persisted:
            return reply
        return f"{reply} 但状态文件保存失败，本次运行内会生效，重启后可能恢复默认角色。"

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

    def _build_current_outfit_reply(
        self,
        active_outfit_id: str,
        *,
        default_outfit: str,
        outfits: list[dict[str, Any]],
        session_id: str = "",
    ) -> str:
        key = _safe_qq_session_key(session_id)
        with self._outfit_lock:
            has_override = bool(key and key in self.outfit_overrides)
        source = "本会话临时切换" if has_override else "角色包默认"
        outfit = self._find_outfit(outfits, active_outfit_id) or {"id": active_outfit_id or default_outfit}
        return f"当前 QQ 会话服装：{self._format_outfit_label(outfit)}（来源：{source}）。"

    def _build_outfit_event_turn_message(self, context: QQMessageContext, action_text: str) -> str:
        text = str(action_text or "").strip()
        if not text:
            text = "切换了你的 QQ 当前会话服装。"
        if context.is_group:
            label = context.sender_label or (f"QQ {context.user_id}" if context.user_id else "群成员")
            return f"【{label}】用户刚刚{text}"
        return f"我{text}"

    def extract_message_text(self, event: dict[str, Any]) -> str:
        return render_onebot_message_text(event)

    def clean_message_text(self, event: dict[str, Any], raw_message: str) -> str:
        return clean_onebot_message_text(event, raw_message, bot_account_id=self.bot_qq)

    def extract_attachments(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        return self._legacy_attachments(parse_onebot_attachments(event))

    def resolve_quoted_message_evidence(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        """Project a package-resolved QQ quote into Akane's product contract.

        ``channelcore-onebot`` remains authoritative for OneBot lookup and scope
        validation.  This method only exposes the protocol-neutral evidence Akane
        needs for the current turn.  The returned attachment payload is internal
        input for the existing attachment inbox; callers must not log or expose the
        full payload because it may contain private media URLs or local paths.
        """
        inbound_result = normalize_inbound_event(
            event,
            bot_account_id=self.bot_qq,
            wake_words=self._wake_words,
        )
        inbound = inbound_result.message
        if inbound is None:
            return {
                "ok": False,
                "status": "invalid_event",
                "quoted_message": None,
                "attachments": [],
            }

        result = resolve_onebot_quoted_message(
            inbound,
            call_action=self._call_onebot_action,
            timeout_seconds=5.0,
        )
        attachments = self._legacy_attachments(result.message.attachments if result.message is not None else ())
        status = result.status
        quoted_message: dict[str, Any] | None = None
        if result.ok and result.message is not None:
            message = result.message
            status = "resolved"
            actor_id = str(message.actor.id or "").strip()
            bot_account_id = str(inbound.bot_account_id or event.get("self_id") or self.bot_qq or "").strip()
            quoted_message = {
                "message_id": message.message_id,
                "text": message.text,
                "actor_id": actor_id,
                "actor_label": message.actor.display_name,
                "actor_is_bot": bool(actor_id and bot_account_id and actor_id == bot_account_id),
                "timestamp": message.timestamp,
                "conversation_kind": message.conversation.kind,
                "conversation_id": message.conversation.id,
                "attachment_count": len(attachments),
            }
        payload: dict[str, Any] = {
            "ok": result.ok,
            "status": status,
            "quoted_message": quoted_message,
            "attachments": attachments,
        }
        if result.reason:
            payload["reason"] = result.reason
        if result.message is not None or inbound.reply_to is not None:
            payload["message_id"] = (
                result.message.message_id
                if result.message is not None
                else str(inbound.reply_to.message_id or "").strip()
            )
        if result.message is not None:
            payload["attachment_count"] = len(attachments)
        return payload

    def resolve_quoted_attachments(
        self,
        event: dict[str, Any],
        *,
        context: QQMessageContext,
    ) -> dict[str, Any]:
        """Compatibility adapter for callers that only consumed quote attachments."""
        return self.resolve_quoted_message_evidence(event, context=context)

    def _call_onebot_action(
        self,
        action: str,
        params: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> object:
        result = self._onebot_transport.call(action, dict(params), timeout=timeout_seconds)
        if not result.ok and result.code not in {"onebot_status_error", "onebot_retcode_error"}:
            raise RuntimeError(result.code)
        return {
            "status": "ok" if result.ok else "failed",
            "retcode": 0 if result.ok else -1,
            "data": result.data,
        }

    @staticmethod
    def _legacy_attachments(attachments: tuple[AttachmentRef, ...]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for attachment in attachments:
            legacy_kind = {
                "image": "image",
                "audio": "audio",
                "video": "video",
                "file": "document",
            }.get(attachment.kind)
            if not legacy_kind:
                continue
            item = {
                "kind": legacy_kind,
                "file": attachment.locator.file_id or attachment.name,
                "url": attachment.locator.url,
                "path": attachment.locator.path,
                "origin_name": attachment.name or attachment.locator.file_id,
                "mime_type": attachment.mime_type,
                "file_size": attachment.size,
                "source_message_id": attachment.source_message_id,
                "source_event_id": attachment.source_event_id,
                "segment_index": attachment.segment_index,
            }
            metadata = attachment.metadata_dict()
            for key in ("quoted_message_id", "sender_id", "sender_label", "group_id"):
                value = str(metadata.get(key) or "").strip()
                if value:
                    item[key] = value
            projected.append(item)
        return projected

    def _parse_cq_params(self, raw: str) -> dict[str, str]:
        return parse_onebot_cq_params(raw)

    def message_mentions_bot(self, event: dict[str, Any], raw_message: str) -> bool:
        return onebot_message_mentions_bot(event, raw_message, bot_account_id=self.bot_qq)

    def message_mentions_wake_word(self, clean_message: str) -> bool:
        return bool(self._wake_word_search_re.search(str(clean_message or "")))

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
            group_id = self._safe_int(event.get("group_id"))
            cache_key = self._sender_label_cache_key(group_id=group_id, user_id=user_id)
            if cache_key:
                self.sender_label_cache[cache_key] = nickname
            return nickname
        group_id = self._safe_int(event.get("group_id"))
        cache_key = self._sender_label_cache_key(group_id=group_id, user_id=user_id)
        if cache_key:
            cached = str(self.sender_label_cache.get(cache_key) or "").strip()
            if cached:
                return cached
        if group_id and user_id:
            remote_label = self.lookup_group_member_label(group_id=group_id, user_id=user_id)
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
        if not mentions or not group_id:
            return tuple(mentions)
        resolved: list[MentionRef] = []
        for mention in mentions:
            target_text = str(mention.target_id or "").strip()
            target_id = self._safe_int(target_text)
            display_name = str(mention.display_name or "").strip()
            if mention.is_bot:
                resolved.append(mention)
                continue
            cache_key = self._sender_label_cache_key(group_id=group_id, user_id=target_id)
            if display_name and cache_key:
                self.sender_label_cache[cache_key] = display_name
            if not display_name and cache_key:
                display_name = str(self.sender_label_cache.get(cache_key) or "").strip()
            if not display_name and target_id:
                display_name = self.lookup_group_member_label(
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

    def _sender_label_cache_key(self, *, group_id: int, user_id: int) -> str:
        if not user_id:
            return ""
        return f"{group_id or 0}:{user_id}"

    def lookup_group_member_label(self, *, group_id: int, user_id: int) -> str:
        if not group_id or not user_id:
            return ""
        payload = {"group_id": group_id, "user_id": user_id, "no_cache": False}
        result = self._onebot_transport.call("get_group_member_info", payload, timeout=3)
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
        active_reply_mode = _safe_reply_mode(reply_mode, default=self.default_reply_mode)
        lines = [f"qq.reply_delivery: {active_reply_mode}"]
        lines.extend(self.consume_delivery_notes(session_id))
        return "\n".join(lines)

    def render_reply_text(self, frame: dict[str, Any]) -> str:
        return "\n".join(self.render_reply_messages(frame)).strip()

    @staticmethod
    def _trim_segment_ending(text: str) -> str:
        """Hide only an ordinary trailing Chinese full stop in QQ bubbles.

        Question/exclamation marks and punctuation clusters carry meaning and
        stay visible.  This is presentation-only: the authoritative ``speech``
        text used by memory and TTS keeps its original punctuation.
        """
        text = str(text or "").strip()
        if not text:
            return text
        if len(text) > 1 and text.endswith("。") and text[-2] not in "。！？!?…":
            return text[:-1].strip()
        return text

    def render_reply_messages(self, frame: dict[str, Any]) -> list[str]:
        segment_texts: list[str] = []
        max_segments = max(1, min(20, int(getattr(config, "QQ_REPLY_MAX_SEGMENTS", 8) or 8)))
        segments = frame.get("speech_segments")
        if isinstance(segments, list):
            for item in segments:
                text = self._trim_segment_ending(str(item or ""))
                if text:
                    segment_texts.append(text)

        if not segment_texts:
            speech = str(frame.get("speech") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            inferred = [self._trim_segment_ending(line) for line in speech.split("\n") if line.strip()]
            if len(inferred) > 1:
                segment_texts = inferred
            elif speech:
                segment_texts = [self._trim_segment_ending(speech)]

        if len(segment_texts) > max_segments:
            messages = [
                *segment_texts[: max_segments - 1],
                "\n".join(segment_texts[max_segments - 1 :]).strip(),
            ]
        else:
            messages = segment_texts

        code_snippet = str(frame.get("code_snippet") or "").strip()
        if code_snippet:
            if messages:
                messages[-1] = f"{messages[-1]}\n\n{code_snippet}".strip()
            else:
                messages.append(code_snippet)
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
            results.append(self.send_reply(context, message, include_reply=index == 0))
        return {
            "ok": all(bool(result.get("ok")) for result in results),
            "count": len(results),
            "results": results,
        }

    def send_generated_files(
        self, context: QQMessageContext, tool_events: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        events = [event for event in tool_events or [] if isinstance(event, dict)]
        targets: list[dict[str, Any]] = []
        seen_targets: set[tuple[str, str]] = set()
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
                mime_type = str(file_ref.get("mime_type") or "").strip().lower()
                ext = Path(name or path).suffix.lower().lstrip(".")
                if path:
                    target = {
                        "generated_id": str(file_ref.get("generated_id") or "").strip(),
                        "source_id": str(
                            file_ref.get("source_id")
                            or file_ref.get("attachment_id")
                            or file_ref.get("generated_id")
                            or ""
                        ).strip(),
                        "source_type": str(file_ref.get("source_type") or "").strip().lower() or "file",
                        "handle": str(
                            file_ref.get("handle")
                            or file_ref.get("attachment_handle")
                            or file_ref.get("generated_handle")
                            or ""
                        ).strip(),
                        "path": path,
                        "name": name or Path(path).name,
                        "is_image": ext in {"png", "jpg", "jpeg", "webp", "gif"}
                        or mime_type.startswith("image/"),
                        "is_audio": ext in {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
                        or mime_type.startswith("audio/"),
                        "delivery_mode": str(event.get("delivery_mode") or "file").strip().lower(),
                    }
                    identity = self._qq_file_delivery_target_identity(target)
                    if identity in seen_targets:
                        continue
                    seen_targets.add(identity)
                    targets.append(target)
                continue

            generated = event.get("generated_file") if isinstance(event.get("generated_file"), dict) else {}
            path = str(generated.get("absolute_path") or "").strip()
            generated_id = str(generated.get("generated_id") or "").strip()
            title = str(generated.get("output_title") or generated.get("generated_handle") or "akane_output").strip()
            ext = str(generated.get("file_ext") or generated.get("output_format") or "").strip().lstrip(".")
            mime_type = str(generated.get("mime_type") or "").strip().lower()
            if path and generated_id:
                target = {
                    "generated_id": generated_id,
                    "source_id": generated_id,
                    "source_type": "generated",
                    "handle": str(generated.get("generated_handle") or "").strip(),
                    "path": path,
                    "name": f"{title}.{ext}" if ext and not title.lower().endswith(f".{ext.lower()}") else title,
                    "is_image": ext.lower() in {"png", "jpg", "jpeg", "webp", "gif"}
                    or mime_type.startswith("image/"),
                    "is_audio": ext.lower() in {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
                    or mime_type.startswith("audio/"),
                    "delivery_mode": str(event.get("delivery_mode") or "file").strip().lower(),
                }
                identity = self._qq_file_delivery_target_identity(target)
                if identity in seen_targets:
                    continue
                seen_targets.add(identity)
                targets.append(target)
        if not targets:
            return {"ok": True, "count": 0, "results": []}

        # The current turn's structured tool event is the delivery decision.
        # The gateway validates transport-facing boundaries above, then sends
        # exactly the files selected by the model instead of reinterpreting the
        # user's natural-language message with another intent classifier.
        return self._send_generated_file_targets(context, targets)

    @staticmethod
    def _qq_file_delivery_target_identity(target: dict[str, Any]) -> tuple[str, str]:
        source_type = str(target.get("source_type") or "").strip().lower() or "file"
        source_id = str(
            target.get("source_id")
            or target.get("generated_id")
            or target.get("attachment_id")
            or target.get("handle")
            or target.get("path")
            or ""
        ).strip()
        return source_type, source_id

    def _send_generated_file_targets(
        self,
        context: QQMessageContext,
        targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for target in targets:
            delivery_mode = str(target.get("delivery_mode") or "file").strip().lower()
            if delivery_mode not in {"file", "voice", "both"}:
                delivery_mode = "file"
            is_audio = bool(target.get("is_audio"))
            if delivery_mode in {"voice", "both"} and not is_audio:
                result = {
                    "ok": False,
                    "status": "failed",
                    "reason": "voice_delivery_requires_audio",
                    "delivery_mode": delivery_mode,
                }
            elif bool(target.get("is_image")):
                result = dict(self.send_image(
                    context,
                    image_path=str(target.get("path") or ""),
                    name=str(target.get("name") or ""),
                ))
                result["delivery_mode"] = "file"
            elif is_audio:
                if delivery_mode == "both":
                    voice_result = dict(self.send_voice(
                        context,
                        audio_path=str(target.get("path") or ""),
                        name=str(target.get("name") or ""),
                    ))
                    file_result = dict(self.send_file(
                        context,
                        file_path=str(target.get("path") or ""),
                        name=str(target.get("name") or ""),
                    ))
                    result = {
                        "ok": bool(voice_result.get("ok")) and bool(file_result.get("ok")),
                        "delivery_mode": "both",
                        "voice_result": voice_result,
                        "file_result": file_result,
                    }
                elif delivery_mode == "file":
                    result = dict(self.send_file(
                        context,
                        file_path=str(target.get("path") or ""),
                        name=str(target.get("name") or ""),
                    ))
                    result["delivery_mode"] = "file"
                else:
                    result = dict(self.send_voice(
                        context,
                        audio_path=str(target.get("path") or ""),
                        name=str(target.get("name") or ""),
                    ))
                    result["delivery_mode"] = "voice"
            else:
                result = dict(self.send_file(
                    context,
                    file_path=str(target.get("path") or ""),
                    name=str(target.get("name") or ""),
                ))
                result["delivery_mode"] = "file"
            result["generated_id"] = str(target.get("generated_id") or "")
            results.append(result)
        all_ok = bool(results) and all(bool(result.get("ok")) for result in results)
        any_ok = any(bool(result.get("ok")) for result in results)
        status = "sent" if all_ok else "partial" if any_ok else "failed"
        return {
            "ok": all_ok,
            "status": status,
            "count": len(results),
            "results": results,
        }

    def _event_allows_qq_file_delivery(self, event: dict[str, Any]) -> bool:
        event_mode = str(event.get("client_mode") or "").strip().lower()
        return not event_mode or event_mode == "qq_text"

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

    def send_music_card(
        self,
        context: QQMessageContext,
        *,
        platform: str,
        track_id: str,
    ) -> dict[str, Any]:
        """Send one QQ native music card; returns the real NapCat transport result."""
        clean_platform = str(platform or "").strip()
        clean_track_id = str(track_id or "").strip()
        onebot_type = QQ_MUSIC_PLATFORM_TO_ONEBOT.get(clean_platform)
        if onebot_type is None:
            return {"ok": False, "status": "unavailable", "reason": "unsupported_music_card_platform"}
        if not context.target_id or not clean_track_id:
            return {"ok": False, "reason": "empty_target_or_music"}
        try:
            plan = build_message_action(
                self._outbound_target(context),
                [music_segment(onebot_type, clean_track_id)],
                # The card is delivered before the final text reply; do not claim
                # the one reply reference so the model's closing text keeps it.
                reply_to="",
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        result = self._send_outbound_plan(plan, timeout=8)
        result["platform"] = clean_platform
        result["track_id"] = clean_track_id
        return result

    def parse_economy_command(self, message: str) -> dict[str, Any] | None:
        """Parse economy commands. Returns None if not an economy command.

        Returns one of:
          {"action": "checkin"}
          {"action": "status"}
          {"action": "shop_list"}
          {"action": "buy", "item_name": str}
        """
        text = str(message or "").strip()
        if not text:
            return None
        text = self._strip_wake_word_command_prefix(re.sub(r"\s+", " ", text))
        if text in QQ_ECONOMY_CHECKIN_COMMANDS:
            return {"action": "checkin"}
        if text in QQ_ECONOMY_STATUS_COMMANDS or _is_economy_status_query(text):
            return {"action": "status"}
        if text in QQ_ECONOMY_SHOP_COMMANDS:
            return {"action": "shop_list"}
        if text in QQ_ECONOMY_BACKPACK_COMMANDS:
            return {"action": "backpack"}
        for prefix in QQ_ECONOMY_BUY_PREFIXES:
            if text.startswith(prefix):
                item_text = text[len(prefix) :].strip()
                if item_text:
                    item_name, qty = _parse_item_and_quantity(item_text)
                    return {"action": "buy", "item_name": item_name, "quantity": qty}
        if text in QQ_ECONOMY_FEED_COMMANDS:
            return {"action": "feed"}
        for prefix in QQ_ECONOMY_FEED_PREFIXES:
            if text.startswith(prefix):
                item_text = text[len(prefix) :].strip()
                if item_text:
                    item_name, qty = _parse_item_and_quantity(item_text)
                    return {"action": "feed", "item_name": item_name, "quantity": qty}
        if text in QQ_ECONOMY_LOTTERY_COMMANDS:
            return {"action": "lottery"}
        if text in QQ_ECONOMY_OFFERING_COMMANDS:
            return {"action": "offering"}
        if text in QQ_ECONOMY_OFFERING_STATUS_COMMANDS:
            return {"action": "offering_status"}
        for prefix in QQ_ECONOMY_OFFERING_PREFIXES:
            if text.startswith(prefix):
                item_name = text[len(prefix) :].strip()
                if item_name:
                    return {"action": "offering", "item_name": item_name}
        return None

    def handle_poke_event(
        self,
        context: "QQMessageContext",
        event: dict[str, Any],
        *,
        care_module: CareModulePort | None = None,
        shop_items: list[dict[str, Any]] | None = None,
        now_ms: int | None = None,
    ) -> PokeOutcome | None:
        """Resolve and apply a QQ poke side effect, then describe only its facts."""
        if str(getattr(context, "reason", "") or "") != "qq_poke":
            return None

        actor_label = _poke_actor_label(context)
        base_text = f"{actor_label}在 QQ 里戳了戳你的头像。"
        event_id = self.poke_event_id(event, context=context)
        plain = lambda *, status="ok", reason="", variant="": _build_poke_outcome(
            actor_label=actor_label,
            outcome_kind="variant" if variant else "plain",
            event_id=event_id,
            memory_text=(f"刚才发生的互动：{base_text}" if not variant else f"刚才发生的互动：{actor_label}戳了戳你，{variant}。"),
            prompt_text=(f"【本轮戳一戳结果】{actor_label}戳了戳你，{variant}。" if variant else ""),
            status=status,
            reason=reason,
        )

        if care_module is None:
            care_module = CareModulePort.disabled("not_configured")
        care_runtime = care_module.runtime
        if care_runtime is None:
            return plain(status="disabled", reason=care_module.reason or "feature_disabled")

        items = _merge_shop_items(DEFAULT_CARE_SHOP_ITEMS, shop_items or [])
        items = _merge_shop_items(items, get_seasonal_shop_items())
        profile_user_id = str(context.profile_user_id or "")
        character_pack_id = str(context.character_pack_id or "")
        relation_user_id = f"qq:{context.user_id}" if context.user_id else f"qq:{profile_user_id}"
        timestamp_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        scope_key, group_scope_key = _poke_scope_keys(context)
        try:
            snapshot = care_runtime.snapshot_for_client(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=relation_user_id,
                now_ms=timestamp_ms,
            )
            plan = self.poke_reactor.plan(snapshot=snapshot, shop_items=items)
            outcome_kind = str(plan.get("outcome_kind") or "plain")
            if outcome_kind == "plain":
                return plain(reason=str(plan.get("fallback_reason") or ""))
            apply_result = care_runtime.apply_poke_plan(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                plan=plan,
                event_id=event_id,
                scope_key=scope_key,
                group_scope_key=group_scope_key,
                cooldown_ms=30_000,
                group_cooldown_ms=2_000,
                now_ms=timestamp_ms,
            )
            apply_status = str(apply_result.get("status") or "error")
            if apply_status == "duplicate":
                return _build_poke_outcome(
                    actor_label=actor_label,
                    outcome_kind=outcome_kind,
                    event_id=event_id,
                    memory_text="",
                    prompt_text="",
                    status="duplicate",
                    reason="event_already_applied",
                )
            if apply_status == "cooldown":
                return plain(status="cooldown", reason="stateful_event_cooldown")
            if apply_status != "ok":
                return plain(status="failed", reason=apply_status)
            if outcome_kind == "variant":
                return plain(variant=str(plan.get("variant") or "发生了奇怪的反应"))
            return _render_poke_mutation_outcome(
                actor_label=actor_label,
                plan=plan,
                result=apply_result,
                event_id=event_id,
            )
        except Exception as exc:
            return plain(status="failed", reason=f"{exc.__class__.__name__}:{exc}")

    @staticmethod
    def poke_event_id(event: dict[str, Any], *, context: "QQMessageContext") -> str:
        """Build a stable replay key from the character, scope, actor, and event."""
        raw_fingerprint = str(event.get("message_id") or event.get("event_id") or "").strip()
        if not raw_fingerprint:
            raw_fingerprint = hashlib.sha256(
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        scope = f"group:{int(context.group_id)}" if context.is_group else f"private:{int(context.user_id)}"
        actor = str(int(context.user_id or 0))
        character = _safe_character_pack_id(context.character_pack_id) or "default_character"
        return f"{character}|{scope}|actor:{actor}|event:{raw_fingerprint}"

    def handle_economy_command(
        self,
        context: "QQMessageContext",
        *,
        care_module: CareModulePort | None = None,
        shop_items: list[dict[str, Any]] | None = None,
        checkin_coins: int = DEFAULT_CHECKIN_COINS,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Handle economy commands (签到/状态/商店/购买/供奉). Returns None if not applicable."""
        parsed = self.parse_economy_command(context.clean_message)
        if parsed is None:
            return None

        if care_module is None:
            care_module = CareModulePort.disabled("not_configured")
        care_runtime = care_module.runtime
        if care_runtime is None:
            return care_module.disabled_result()

        items = _merge_shop_items(DEFAULT_CARE_SHOP_ITEMS, shop_items or [])
        items = _merge_shop_items(items, get_seasonal_shop_items())
        profile_user_id = context.profile_user_id
        character_pack_id = context.character_pack_id or ""
        relation_user_id = f"qq:{context.user_id}" if context.user_id else f"qq:{profile_user_id}"
        ts_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        action = parsed["action"]

        try:
            if action == "checkin":
                date_key = datetime.now().strftime("%Y-%m-%d")
                result = care_runtime.claim_daily_checkin(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    date_key=date_key,
                    coins=checkin_coins,
                    now_ms=ts_ms,
                )
                snap = result["snapshot"]
                coins_now = snap["coins"]
                streak = int(result.get("streak") or 1)

                if result["status"] == "already":
                    note = (
                        f"【签到通知】用户今天已经签到过了，又来说了一句签到"
                        f"（当前金币 {coins_now}，连续签到 {streak} 天）。自然回应即可，不必强调规则。"
                    )
                    return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "already"}

                coins_granted = result["coins_granted"]
                streak_broken = bool(result.get("streak_broken"))
                days_absent = int(result.get("days_absent") or 0)
                is_milestone = bool(result.get("streak_milestone"))

                if streak_broken and days_absent >= 1:
                    # Comeback after breaking a streak — LLM reacts to absence
                    note = (
                        f"【签到通知】用户消失了 {days_absent} 天后回来签到了（之前有连续签到纪录）。"
                        f"本次签到 +{coins_granted} 金币，当前 {coins_now} 金币，连续签到重置为第 1 天。"
                        f"可以用你自己的方式表达一下——不一定要抱怨，但可以让他感受到这几天有什么不同。"
                    )
                    return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

                if is_milestone:
                    # Streak milestone — LLM celebrates (or reacts in character)
                    note = (
                        f"【签到里程碑】用户已连续签到 {streak} 天！"
                        f"本次签到 +{coins_granted} 金币（连击奖励），当前 {coins_now} 金币。"
                        f"用你自己的方式回应这个里程碑——不用过分热情，但要让他感觉到这件事有意义。"
                    )
                    return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

                # Normal checkin — local reply
                streak_str = f"（连续 {streak} 天）" if streak >= 2 else ""
                reply = f"✓ 签到成功！+{coins_granted} 金币{streak_str}（当前：{coins_now} 金币）"
                return {"ok": True, "reply": reply, "status": "ok"}

            if action == "status":
                snapshot = care_runtime.snapshot_for_client(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id,
                    now_ms=ts_ms,
                )
                h = snapshot["hunger"]
                e = snapshot["energy"]
                a = snapshot["affection"]
                c = snapshot["coins"]
                reply = f"养成状态\n饥饿 {h}/100（越低越饿）  精力 {e}/100（越高越精神）\nQQ好感 {a}/100  金币 {c}"
                return {"ok": True, "reply": reply, "status": "ok"}

            if action == "shop_list":
                qq_items = [item for item in items if _item_usable_in_qq(item)]
                if not qq_items:
                    return {"ok": True, "reply": "商店暂时没有商品。", "status": "empty"}
                seasonal_items = [i for i in qq_items if i.get("seasonal")]
                regular_items = [i for i in qq_items if not i.get("seasonal")]
                food_items = [
                    i
                    for i in regular_items
                    if i.get("category") not in ("offering", "charm", "gift", "trick", "potion")
                ]
                trick_items = [i for i in regular_items if i.get("category") in ("charm", "trick", "potion")]
                offer_items = [i for i in regular_items if i.get("category") in ("offering", "gift")]
                lines = ["\U0001f6d2 商店", "─" * 18]

                def _item_line(item: dict) -> str:
                    eff = _format_effects_summary(item.get("effects") or {})
                    line = f"  {item['name']}  {item.get('price', 0)} 金币"
                    if eff:
                        line += f"  ({eff})"
                    return line

                if seasonal_items:
                    # Group by label for display
                    by_label: dict[str, list] = {}
                    for i in seasonal_items:
                        lbl = f"{i.get('seasonal_emoji', '🌸')} {i.get('seasonal_label', '限定')}"
                        by_label.setdefault(lbl, []).append(i)
                    for lbl, grp in by_label.items():
                        lines.append(f"{lbl}（限时）")
                        for item in grp:
                            lines.append(_item_line(item))
                    lines.append("")
                if food_items:
                    lines.append("\U0001f35a 食物 / 饮品")
                    for item in food_items:
                        lines.append(_item_line(item))
                if trick_items:
                    if food_items:
                        lines.append("")
                    lines.append("🔮 歪门邪道")
                    for item in trick_items:
                        lines.append(_item_line(item))
                if offer_items:
                    if food_items or trick_items:
                        lines.append("")
                    lines.append("⛩ 供奉 / 礼物")
                    for item in offer_items:
                        lines.append(_item_line(item))
                lines.append("─" * 18)
                lines.append("购买 商品名  /  供奉 商品名  /  供奉  /  抽签（5金币）")
                return {"ok": True, "reply": "\n".join(lines), "status": "ok"}

            if action == "buy":
                item_name = str(parsed.get("item_name") or "").strip()
                qty = max(1, int(parsed.get("quantity") or 1))
                matched = _find_shop_item(items, item_name)
                if matched is None:
                    return {
                        "ok": False,
                        "reply": f"没有找到「{item_name}」，发送「商店」查看可用商品。",
                        "status": "item_not_found",
                    }
                if not _item_usable_in_qq(matched):
                    return {
                        "ok": False,
                        "reply": f"「{matched['name']}」只能在桌宠端使用，QQ 不支持。",
                        "status": "not_usable_in_qq",
                    }
                price_each = int(matched.get("price") or 0)
                result = care_runtime.buy_to_inventory(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    item_id=str(matched.get("id") or ""),
                    item_name=str(matched["name"]),
                    price=price_each,
                    count=qty,
                    item_effects=dict(matched.get("effects") or {}),
                    item_category=str(matched.get("category") or ""),
                    item_metadata=matched,
                    now_ms=ts_ms,
                )
                if result["status"] == "insufficient_coins":
                    needed = result["coins_needed"]
                    have = result["coins_before"]
                    return {
                        "ok": False,
                        "reply": f"金币不够，需要 {needed} 金币，当前只有 {have} 金币。",
                        "status": "insufficient_coins",
                    }
                coins_after = result["coins_after"]
                total_count = result["item_count"]
                qty_str = f" x{qty}" if qty > 1 else ""
                reply = (
                    f"✓ 购买成功！「{matched['name']}」{qty_str} 已放入背包"
                    f"（-{price_each * qty} 金币，剩余 {coins_after} 金币，背包共 x{total_count}）"
                    f"\n发送「投喂 {matched['name']}」来使用。"
                )
                return {"ok": True, "reply": reply, "status": "ok"}

            if action == "backpack":
                snapshot = care_runtime.snapshot_for_client(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id,
                    now_ms=ts_ms,
                )
                inventory = snapshot.get("inventory") or {}
                if not inventory:
                    return {
                        "ok": True,
                        "reply": "\U0001f392 背包是空的，发送「商店」查看可购买的商品。",
                        "status": "empty",
                    }
                lines = ["\U0001f392 背包", "─" * 16]
                for item_data in inventory.values():
                    name = str(item_data.get("name") or "")
                    count = int(item_data.get("count") or 0)
                    eff_str = _format_effects_summary(item_data.get("effects") or {})
                    line = f"  {name}  x{count}"
                    if eff_str:
                        line += f"  [{eff_str}]"
                    lines.append(line)
                lines.append("─" * 16)
                lines.append("投喂 商品名  / 供奉 商品名")
                return {"ok": True, "reply": "\n".join(lines), "status": "ok"}

            if action == "feed":
                item_name = str(parsed.get("item_name") or "").strip()
                qty = max(1, int(parsed.get("quantity") or 1))
                if not item_name:
                    return {
                        "ok": False,
                        "reply": "请指定要投喂的物品，发送「背包」查看持有。",
                        "status": "no_item_specified",
                    }
                matched = _find_shop_item(items, item_name)
                # Resolve item_id and effects: prefer live shop, fall back to inventory snapshot
                if matched is not None:
                    item_id = str(matched.get("id") or "")
                    item_name_display = str(matched["name"])
                    effects_for_feed: dict[str, Any] = dict(matched.get("effects") or {})
                else:
                    # Item may be seasonal / expired — look it up in the user's inventory
                    inv_snap = care_runtime.snapshot_for_client(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=relation_user_id,
                        now_ms=ts_ms,
                    )
                    inv = inv_snap.get("inventory") or {}
                    item_id = ""
                    item_name_display = item_name
                    effects_for_feed = {}
                    for inv_id, inv_entry in inv.items():
                        if str(inv_entry.get("name") or "").lower() == item_name.lower():
                            item_id = inv_id
                            effects_for_feed = dict(inv_entry.get("effects") or {})
                            item_name_display = str(inv_entry.get("name") or item_name)
                            break
                    if not item_id:
                        return {
                            "ok": False,
                            "reply": f"没有找到「{item_name}」，发送「背包」查看持有物品。",
                            "status": "item_not_found",
                        }
                result = care_runtime.use_from_inventory(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    item_id=item_id,
                    item_effects=effects_for_feed,
                    count=qty,
                    now_ms=ts_ms,
                )
                if result["status"] == "not_in_inventory":
                    return {
                        "ok": False,
                        "reply": (f"背包里没有「{item_name_display}」，先发送「购买 {item_name_display}」入手。"),
                        "status": "not_in_inventory",
                    }
                if result["status"] == "insufficient_count":
                    have = result.get("available", 0)
                    return {
                        "ok": False,
                        "reply": (
                            f"背包里「{item_name_display}」只剩 x{have}，"
                            f"发送「投喂 {item_name_display} x{have}」或先补货。"
                        ),
                        "status": "insufficient_count",
                    }
                snap = result["snapshot"]
                qty_str = f" x{qty}" if qty > 1 else ""
                _feed_actor = context.sender_label or ("我" if not context.is_group else "用户")
                feed_memory = f"刚才发生的互动：{_feed_actor}投喂了你「{item_name_display}」{qty_str}。"
                feed_facts = [
                    feed_memory.removeprefix("刚才发生的互动："),
                    f"「{item_name_display}」已真实消耗。",
                    f"实际效果：{_render_poke_effects(result)}。",
                    f"投喂后状态：饥饿 {int(snap.get('hunger') or 0)}/100，精力 {int(snap.get('energy') or 0)}/100。",
                ]
                if isinstance(matched, dict):
                    seasonal_note = _poke_seasonal_note(matched)
                    if seasonal_note:
                        feed_facts.insert(2, seasonal_note)
                note = (
                    "【本轮投喂结果】\n" + "\n".join(feed_facts)
                )
                return {
                    "_llm_passthrough": True,
                    "qq_action_note": note,
                    "turn_message": feed_memory,
                    "ok": True,
                    "status": "ok",
                }

            if action == "lottery":
                SLIP_COST = 5
                result = care_runtime.draw_fortune_slip(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    slip_cost=SLIP_COST,
                    now_ms=ts_ms,
                )
                if result["status"] == "insufficient_coins":
                    have = result["coins_before"]
                    return {
                        "ok": False,
                        "reply": f"金币不足，抽一签需要 {SLIP_COST} 金币，当前只有 {have} 金币。",
                        "status": "insufficient_coins",
                    }
                fortune = result["fortune"]
                coins_after = result["coins_after"]
                aff_delta = result["affection_delta"]

                actual_coin_delta = int(coins_after) - int(result.get("coins_before") or 0)
                effect_parts = [f"金币 {_signed_number(actual_coin_delta)}"]
                if aff_delta:
                    effect_parts.append(f"QQ 好感 {_signed_number(aff_delta)}")
                note = (
                    f"【本轮抽签结果】用户花费 {SLIP_COST} 金币抽了一签，结果是【{fortune}】。"
                    f"实际效果：{'，'.join(effect_parts)}；当前金币 {coins_after}。"
                )
                return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

            if action == "offering_status":
                snapshot = care_runtime.snapshot_for_client(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id,
                    now_ms=ts_ms,
                )
                today = datetime.now().strftime("%Y-%m-%d")
                offered_today = str(snapshot.get("last_offering_date") or "") == today
                mark = "✓ 今日已供奉" if offered_today else "· 今日未供奉"
                a = snapshot["affection"]
                c = snapshot["coins"]
                reply = f"⛩ 供奉状态\n{mark}\nQQ好感 {a}/100  金币 {c}"
                return {"ok": True, "reply": reply, "status": "ok"}

            if action == "offering":
                item_name = str(parsed.get("item_name") or "").strip()
                date_key = datetime.now().strftime("%Y-%m-%d")

                if item_name:
                    matched = _find_shop_item(items, item_name)
                    if matched is None:
                        return {
                            "ok": False,
                            "reply": f"没有找到「{item_name}」，发送「商店」查看可用商品。",
                            "status": "item_not_found",
                        }
                    if not _item_usable_in_qq(matched):
                        return {
                            "ok": False,
                            "reply": f"「{matched['name']}」只能在桌宠端使用，QQ 不支持。",
                            "status": "not_usable_in_qq",
                        }
                    if not _item_usable_as_offering(matched):
                        return {
                            "ok": False,
                            "reply": (f"「{matched['name']}」不是供奉/礼物类商品；普通食物请先购买再投喂。"),
                            "status": "not_offering_item",
                        }
                    item_id = str(matched.get("id") or "")
                    effects = matched.get("effects") or {}
                    body_effects = {k: v for k, v in effects.items() if k in ("hunger", "energy")}
                    affection_effect = int(effects.get("affection") or 0)
                    use_result = care_runtime.use_from_inventory(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        relation_user_id=relation_user_id,
                        item_id=item_id,
                        item_effects=body_effects,
                        now_ms=ts_ms,
                    )
                    if use_result["status"] == "not_in_inventory":
                        return {
                            "ok": False,
                            "reply": (f"背包里没有「{matched['name']}」，先发送「购买 {matched['name']}」入手再供奉。"),
                            "status": "not_in_inventory",
                        }
                    result = care_runtime.claim_daily_offering(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        relation_user_id=relation_user_id,
                        date_key=date_key,
                        affection_bonus=0,
                        item_price=0,
                        item_effects={"affection": affection_effect},
                        now_ms=ts_ms,
                    )
                    daily_affection = int(result.get("affection_granted") or 0)
                    body_effect = _render_poke_effects(use_result)
                    offering_memory = f"刚才发生的互动：用户供奉了「{matched['name']}」。"
                    offering_facts = [
                        f"用户供奉了「{matched['name']}」，该物品已真实消耗。",
                        f"物品效果：{body_effect}。",
                        f"本次供奉实际增加 QQ 好感 {_signed_number(daily_affection)}。",
                        f"这是当天第{'一次' if result.get('daily_bonus') else '二次及以后'}供奉。",
                    ]
                    seasonal_note = _poke_seasonal_note(matched)
                    if seasonal_note:
                        offering_facts.insert(1, seasonal_note)
                    note = "【本轮供奉结果】\n" + "\n".join(offering_facts)
                    return {
                        "_llm_passthrough": True,
                        "qq_action_note": note,
                        "turn_message": offering_memory,
                        "ok": True,
                        "status": result["status"],
                    }

                # Free offering (no item)
                result = care_runtime.claim_daily_offering(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    date_key=date_key,
                    affection_bonus=3,
                    item_price=0,
                    item_effects={},
                    now_ms=ts_ms,
                )
                aff_now = result["snapshot"]["affection"]
                note = (
                    "【本轮供奉结果】\n"
                    f"用户进行了{'当天第一次' if result.get('daily_bonus') else '当天再次'}供奉。\n"
                    f"实际效果：QQ 好感 {_signed_number(int(result.get('affection_granted') or 0))}，当前 QQ 好感 {aff_now}/100。"
                )
                return {
                    "_llm_passthrough": True,
                    "qq_action_note": note,
                    "turn_message": "刚才发生的互动：用户向神社进行了供奉。",
                    "ok": True,
                    "status": result["status"],
                }

        except Exception as exc:
            return {"ok": False, "reply": "养成系统暂时出错，请稍后再试。", "status": "error", "error": str(exc)}

        return None

    def send_reply(
        self,
        context: QQMessageContext,
        message: str,
        *,
        include_reply: bool = True,
    ) -> dict[str, Any]:
        clean_message = self._trim_segment_ending(str(message or ""))
        if not context.target_id or not clean_message:
            return {"ok": False, "reason": "empty_target_or_message"}
        try:
            plan = build_message_action(
                self._outbound_target(context),
                [text_segment(clean_message)],
                reply_to=self._claim_reply_message_id(context, include_reply=include_reply),
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        return self._send_outbound_plan(plan, timeout=8)

    def send_mface(self, context: QQMessageContext, *, mface: dict[str, Any]) -> dict[str, Any]:
        """Send a NapCat / OneBot marketplace emoji message segment."""
        data = _normalize_mface_payload(mface)
        if not context.target_id:
            return {"ok": False, "reason": "empty_target"}
        if not data:
            return {"ok": False, "reason": "invalid_mface_payload"}

        try:
            plan = build_message_action(
                self._outbound_target(context),
                [mface_segment(data)],
                reply_to=self._claim_reply_message_id(context),
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        return self._send_outbound_plan(plan, timeout=8)

    def send_emotion_mface(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        qq_delivery_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a configured QQ mface based on final_output.emotion."""
        if self._frame_has_artifact_delivery_activity(frame):
            return {"ok": True, "status": "skipped", "reason": "artifact_delivery_turn"}
        emotion = str((frame or {}).get("emotion") or "").strip()
        if not emotion:
            return {"ok": True, "status": "skipped", "reason": "empty_emotion"}

        config_payload = _normalize_emotion_mface_config(qq_delivery_config)
        if not config_payload.get("enabled"):
            return {"ok": True, "status": "skipped", "reason": "disabled", "emotion": emotion}

        mface = _resolve_emotion_mface(emotion, config_payload.get("map"))
        if not mface:
            return {"ok": True, "status": "skipped", "reason": "no_mface_mapping", "emotion": emotion}

        min_interval = max(0, min(3600, int(config_payload.get("min_interval_seconds") or 0)))
        fingerprint = _mface_fingerprint(mface)
        session_key = (
            _safe_qq_session_key(context.session_id)
            or f"{context.target_id}:{'group' if context.is_group else 'private'}"
        )
        now = time.time()
        with self._emotion_mface_lock:
            previous = self.emotion_mface_state.get(session_key) or {}
            if (
                min_interval > 0
                and previous.get("fingerprint") == fingerprint
                and now - float(previous.get("sent_at") or 0.0) < min_interval
            ):
                return {
                    "ok": True,
                    "status": "skipped",
                    "reason": "dedupe_interval",
                    "emotion": emotion,
                    "min_interval_seconds": min_interval,
                }

        result = self.send_mface(context, mface=mface)
        result["status"] = "sent" if result.get("ok") else "send_failed"
        result["emotion"] = emotion
        if result.get("ok"):
            with self._emotion_mface_lock:
                self.emotion_mface_state[session_key] = {
                    "emotion": emotion,
                    "fingerprint": fingerprint,
                    "sent_at": now,
                }
        return result

    def send_emotion_image(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        image: dict[str, Any] | None = None,
        min_interval_seconds: int = 20,
    ) -> dict[str, Any]:
        """Send the current character pack emotion image as a QQ image fallback."""
        if self._frame_has_artifact_delivery_activity(frame):
            return {"ok": True, "status": "skipped", "reason": "artifact_delivery_turn"}
        emotion = str((frame or {}).get("emotion") or "").strip()
        if not emotion:
            return {"ok": True, "status": "skipped", "reason": "empty_emotion"}
        image = image if isinstance(image, dict) else {}
        image_path = str(image.get("path") or "").strip()
        if not image_path:
            return {"ok": True, "status": "skipped", "reason": "missing_emotion_image", "emotion": emotion}

        min_interval = max(0, min(3600, int(min_interval_seconds or 0)))
        fingerprint = f"image|{image_path}"
        session_key = (
            _safe_qq_session_key(context.session_id)
            or f"{context.target_id}:{'group' if context.is_group else 'private'}"
        )
        now = time.time()
        with self._emotion_image_lock:
            previous = self.emotion_image_state.get(session_key) or {}
            if (
                min_interval > 0
                and previous.get("fingerprint") == fingerprint
                and now - float(previous.get("sent_at") or 0.0) < min_interval
            ):
                return {
                    "ok": True,
                    "status": "skipped",
                    "reason": "dedupe_interval",
                    "emotion": emotion,
                    "min_interval_seconds": min_interval,
                }

        result = self.send_image(
            context,
            image_path=image_path,
            name=str(image.get("name") or image.get("emotion") or emotion),
        )
        result["status"] = "sent" if result.get("ok") else "send_failed"
        result["emotion"] = emotion
        result["image_emotion"] = str(image.get("emotion") or "")
        if result.get("ok"):
            with self._emotion_image_lock:
                self.emotion_image_state[session_key] = {
                    "emotion": emotion,
                    "fingerprint": fingerprint,
                    "sent_at": now,
                }
        return result

    @staticmethod
    def _frame_has_artifact_delivery_activity(frame: dict[str, Any] | None) -> bool:
        events = (frame or {}).get("tool_events")
        if not isinstance(events, list):
            return False
        artifact_ready_types = {"generated_file_ready", "file_ready"}
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip()
            if event_type in artifact_ready_types and event.get("send_to_user") is not False:
                return True
        return False

    def send_image(self, context: QQMessageContext, *, image_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(image_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_image"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "image_not_found"}

        resolved_path = path_obj.resolve()
        reply_to = self._claim_reply_message_id(context)
        onebot_path = self._onebot_file_path(resolved_path)
        file_candidates: list[tuple[str, str]] = []
        if onebot_path != str(resolved_path):
            file_candidates.append(("shared_path", onebot_path))
        file_candidates.extend(
            [
                ("file_uri", resolved_path.as_uri()),
                ("absolute_path", str(resolved_path)),
            ]
        )
        inline_fallback_skipped = False
        try:
            if resolved_path.stat().st_size <= QQ_INLINE_IMAGE_MAX_BYTES:
                encoded = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
                file_candidates.append(("base64", f"base64://{encoded}"))
            else:
                inline_fallback_skipped = True
        except OSError:
            inline_fallback_skipped = True
        last_result = None
        for transport, file_value in file_candidates:
            try:
                plan = build_message_action(
                    self._outbound_target(context),
                    [image_segment(file_value, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
            except ValueError as exc:
                return self._outbound_plan_failure(exc)
            last_result = self._onebot_transport.call(plan.action, plan.params(), timeout=20)
            if last_result.ok:
                result = last_result.as_dict()
                result["transport"] = transport
                return result
        result = (last_result or self._onebot_transport.call("unknown", {})).as_dict()
        result["inline_fallback_skipped"] = inline_fallback_skipped
        return result

    def send_voice(
        self,
        context: QQMessageContext,
        *,
        audio_path: str,
        name: str = "",
        claim_reply: bool = True,
    ) -> dict[str, Any]:
        clean_path = str(audio_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_audio"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "audio_not_found"}

        reply_to = self._claim_reply_message_id(context) if claim_reply else ""
        resolved_path = path_obj.resolve()
        onebot_path = self._onebot_file_path(resolved_path)
        file_candidates: list[tuple[str, str]] = []
        if onebot_path != str(resolved_path):
            file_candidates.append(("shared_path", onebot_path))
        file_candidates.extend(
            [
                ("file_uri", resolved_path.as_uri()),
                ("absolute_path", str(resolved_path)),
            ]
        )
        last_result = None
        for transport, file_value in file_candidates:
            try:
                plan = build_message_action(
                    self._outbound_target(context),
                    [voice_segment(file_value, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
            except ValueError as exc:
                return self._outbound_plan_failure(exc)
            last_result = self._onebot_transport.call(plan.action, plan.params(), timeout=30)
            if last_result.ok:
                result = last_result.as_dict()
                result["transport"] = transport
                return result

        staged = self._onebot_transport.stage_file(
            path_obj,
            filename=name or path_obj.name,
        )
        if staged.ok:
            try:
                plan = build_message_action(
                    self._outbound_target(context),
                    [voice_segment(staged.file_ref, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
            except ValueError as exc:
                return self._outbound_plan_failure(exc)
            streamed_result = self._onebot_transport.call(plan.action, plan.params(), timeout=30)
            if streamed_result.ok:
                result = streamed_result.as_dict()
                result["transport"] = "stream_upload"
                return result
            last_result = streamed_result

        try:
            if path_obj.stat().st_size <= QQ_INLINE_FILE_MAX_BYTES:
                inline_ref = "base64://" + base64.b64encode(path_obj.read_bytes()).decode("ascii")
                plan = build_message_action(
                    self._outbound_target(context),
                    [voice_segment(inline_ref, summary=name or path_obj.name)],
                    reply_to=reply_to,
                )
                inline_result = self._onebot_transport.call(plan.action, plan.params(), timeout=30)
                if inline_result.ok:
                    result = inline_result.as_dict()
                    result["transport"] = "base64"
                    return result
                last_result = inline_result
        except (OSError, ValueError):
            pass
        result = (last_result or self._onebot_transport.call("unknown", {})).as_dict()
        if not staged.ok:
            result["staging_reason"] = staged.code
        return result

    def send_voice_url(self, context: QQMessageContext, *, audio_url: str, name: str = "") -> dict[str, Any]:
        """Send an already preflighted public audio URL as a QQ voice message."""
        clean_url = str(audio_url or "").strip()
        if not context.target_id or not clean_url:
            return {"ok": False, "reason": "empty_target_or_audio_url"}
        try:
            plan = build_message_action(
                self._outbound_target(context),
                [voice_segment(clean_url, summary=str(name or "网络音频").strip() or "网络音频")],
                # Tool delivery happens before the model's final reply.  Keep
                # the source-message reply reference available for that reply.
                reply_to="",
            )
        except ValueError as exc:
            return self._outbound_plan_failure(exc)
        result = self._send_outbound_plan(plan, timeout=30)
        result["transport"] = "public_url"
        return result

    def send_file(self, context: QQMessageContext, *, file_path: str, name: str = "") -> dict[str, Any]:
        clean_path = str(file_path or "").strip()
        if not context.target_id or not clean_path:
            return {"ok": False, "reason": "empty_target_or_file"}

        path_obj = Path(clean_path)
        if not path_obj.exists():
            return {"ok": False, "reason": "file_not_found"}
        requested_name = name or path_obj.name
        safe_name = _safe_qq_file_name(requested_name, fallback=path_obj.name)
        resolved_path = path_obj.resolve()
        onebot_path = self._onebot_file_path(resolved_path)
        direct_candidates: list[tuple[str, str]] = []
        if onebot_path != str(resolved_path):
            direct_candidates.append(("shared_path", onebot_path))
        direct_candidates.append(("local_path", str(resolved_path)))
        direct_result: dict[str, Any] = {"ok": False, "reason": "file_delivery_not_attempted"}
        for transport, file_ref in direct_candidates:
            try:
                plan = build_upload_file_action(
                    self._outbound_target(context),
                    file_ref=file_ref,
                    name=safe_name,
                )
            except ValueError as exc:
                return self._outbound_plan_failure(exc)
            direct_result = self._send_outbound_plan(plan, timeout=60)
            if direct_result.get("ok"):
                direct_result["transport"] = transport
                if safe_name != requested_name:
                    direct_result["filename_adjusted"] = True
                    direct_result["display_name"] = safe_name
                return direct_result

        staged = self._onebot_transport.stage_file(path_obj, filename=safe_name)
        if staged.ok:
            try:
                staged_plan = build_upload_file_action(
                    self._outbound_target(context),
                    file_ref=staged.file_ref,
                    name=safe_name,
                )
            except ValueError as exc:
                return self._outbound_plan_failure(exc)
            staged_result = self._send_outbound_plan(staged_plan, timeout=30)
            if staged_result.get("ok"):
                staged_result["transport"] = "stream_upload"
                if safe_name != requested_name:
                    staged_result["filename_adjusted"] = True
                    staged_result["display_name"] = safe_name
                return staged_result
            direct_result = staged_result

        try:
            if path_obj.stat().st_size <= QQ_INLINE_FILE_MAX_BYTES:
                inline_ref = "base64://" + base64.b64encode(path_obj.read_bytes()).decode("ascii")
                inline_plan = build_upload_file_action(
                    self._outbound_target(context),
                    file_ref=inline_ref,
                    name=safe_name,
                )
                inline_result = self._send_outbound_plan(inline_plan, timeout=30)
                if inline_result.get("ok"):
                    inline_result["transport"] = "base64"
                    if safe_name != requested_name:
                        inline_result["filename_adjusted"] = True
                        inline_result["display_name"] = safe_name
                    return inline_result
                direct_result = inline_result
        except (OSError, ValueError):
            pass
        if not staged.ok:
            direct_result["staging_reason"] = staged.code
        return direct_result

    def _onebot_file_path(self, path: Path) -> str:
        if self._channel_config is None:
            return str(path)
        return self._channel_config.project_local_file(path)

    @staticmethod
    def _outbound_target(context: QQMessageContext) -> OutboundTarget:
        return OutboundTarget("group" if context.is_group else "private", context.target_id)

    def _claim_reply_message_id(self, context: QQMessageContext, *, include_reply: bool = True) -> str:
        """Allow at most one visible OneBot reply frame for each inbound message."""
        if not include_reply:
            return ""
        message_id = str(context.source_message_id or "").strip()
        if not message_id:
            return ""
        key = ("group" if context.is_group else "private", str(context.target_id or ""), message_id)
        with self._reply_reference_lock:
            if key in self._reply_reference_claims:
                return ""
            self._reply_reference_claims[key] = None
            while len(self._reply_reference_claims) > QQ_REPLY_REFERENCE_MAX_CLAIMS:
                self._reply_reference_claims.pop(next(iter(self._reply_reference_claims)))
        return message_id

    def _send_outbound_plan(self, plan: OutboundAction, *, timeout: float) -> dict[str, Any]:
        return self._onebot_transport.call(plan.action, plan.params(), timeout=timeout).as_dict()

    @staticmethod
    def _outbound_plan_failure(exc: ValueError) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "failed",
            "code": str(exc) or "onebot_outbound_plan_invalid",
            "action": "unknown",
            "data": {},
            "public_reason": "OneBot 出站消息参数无效。",
        }

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

    def _is_group_plaintext_allowed(self, *, session_id: str, user_id: int) -> bool:
        return bool(getattr(config, "QQ_GROUP_PLAINTEXT_ENABLED", False))

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


def _safe_qq_session_key(value: Any) -> str:
    key = str(value or "").strip()
    if key == "master":
        return key
    if re.fullmatch(r"qq_pri_\d+", key):
        return key
    if re.fullmatch(r"qq_group_shared_\d+", key):
        return key
    return ""


def _clean_character_pack_argument(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip('`\'"""‘’')
    text = text.rstrip("。.!！?？,，;；")
    return text.strip()


def _safe_outfit_id(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip('`\'"""‘’')
    text = text.rstrip("。.!！?？,，;；")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > 80:
        return ""
    if any(ord(char) < 0x20 or char in {"/", "\\", "\x7f"} for char in text):
        return ""
    if text in {".", ".."}:
        return ""
    return text


def _safe_chat_model_id(value: Any) -> str:
    return normalize_provider_model_id(value)


def _chat_model_route_prefix(value: Any) -> str:
    model = _safe_chat_model_id(value)
    if not model.startswith("["):
        return ""
    end = model.find("]")
    if end <= 1:
        return ""
    return model[: end + 1]


def _chat_model_base_id(value: Any) -> str:
    model = _safe_chat_model_id(value)
    prefix = _chat_model_route_prefix(model)
    return model[len(prefix) :] if prefix else model


def _outfit_lookup_key(value: Any) -> str:
    return re.sub(r"[\s_\-.]+", "", _safe_outfit_id(value).lower())


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
    text = text.strip('`\'"""‘’')
    text = text.rstrip("。.!！?？,，;；")
    return text.strip()


def _item_usable_in_qq(item: dict[str, Any]) -> bool:
    """Return True if item has no usable_in restriction or explicitly includes 'qq'."""
    usable_in = item.get("usable_in")
    if not usable_in:
        return True
    return "qq" in [str(u).lower() for u in usable_in]


def _poke_actor_label(context: QQMessageContext) -> str:
    if not context.is_group:
        return "我"
    return context.sender_label or (f"QQ {context.user_id}" if context.user_id else "这位 QQ 用户")


def _poke_scope_keys(context: QQMessageContext) -> tuple[str, str]:
    character = _safe_character_pack_id(context.character_pack_id) or "default_character"
    if context.is_group:
        return (
            f"{character}|group:{int(context.group_id)}|actor:{int(context.user_id)}",
            f"{character}|group:{int(context.group_id)}",
        )
    return f"{character}|private:{int(context.user_id)}|actor:{int(context.user_id)}", ""


def _build_poke_outcome(
    *,
    actor_label: str,
    outcome_kind: str,
    event_id: str,
    memory_text: str,
    prompt_text: str,
    mutations: list[dict[str, Any]] | None = None,
    status: str = "ok",
    reason: str = "",
) -> PokeOutcome:
    return PokeOutcome(
        event_kind="qq_poke",
        source="poke",
        actor_label=actor_label,
        outcome_kind=outcome_kind,
        memory_text=memory_text,
        prompt_text=prompt_text,
        mutations=tuple(dict(item) for item in (mutations or []) if isinstance(item, dict)),
        status=status,
        reason=reason,
        event_id=event_id,
    )


def _render_poke_mutation_outcome(
    *,
    actor_label: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    event_id: str,
) -> PokeOutcome:
    outcome_kind = str(plan.get("outcome_kind") or "plain")
    item = plan.get("item") if isinstance(plan.get("item"), dict) else {}
    count = max(1, int(plan.get("count") or 1))
    item_name = str(item.get("name") or plan.get("item_id") or "物品")
    owner = "我的" if actor_label == "我" else f"{actor_label}的"
    seasonal_note = _poke_seasonal_note(item)

    if outcome_kind == "consume_inventory_item":
        item_desc = f"{item_name} x{count}" if count > 1 else item_name
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，你从{owner}背包里偷吃了{item_desc}。"
        fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"{item_name}已真实消耗。"]
        if seasonal_note:
            fact_lines.append(seasonal_note)
        fact_lines.append(f"实际效果：{_render_poke_effects(result)}。")
        mutations = [
            {
                "kind": "inventory_consume",
                "item_id": str(result.get("item_id") or plan.get("item_id") or ""),
                "item_name": item_name,
                "count": int(result.get("count_used") or count),
                "effects_applied": dict(result.get("effects_applied") or {}),
            }
        ]
    elif outcome_kind == "grant_inventory_item":
        item_desc = f"{item_name} x{count}" if count > 1 else item_name
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，{item_desc}落进了{owner}背包。"
        fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"{item_desc}已加入{owner}背包。"]
        if seasonal_note:
            fact_lines.append(seasonal_note)
        mutations = [dict(result.get("mutation") or {})]
    elif outcome_kind == "coin_change":
        mutation = result.get("mutation") if isinstance(result.get("mutation"), dict) else {}
        actual_delta = int(mutation.get("actual_delta") or 0)
        delta_text = _signed_number(actual_delta)
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，{owner}余额发生了变化：金币 {delta_text}。"
        fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"实际效果：金币 {delta_text}。"]
        mutations = [dict(mutation)]
    elif outcome_kind == "lottery":
        fortune = str(result.get("fortune") or "未知")
        net_coins = int(result.get("coins_after") or 0) - int(result.get("coins_before") or 0)
        affection_delta = int(result.get("affection_delta") or 0)
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，触发了一次抽签，结果是{fortune}。"
        effects = [f"金币 {_signed_number(net_coins)}"]
        if affection_delta:
            effects.append(f"QQ 好感 {_signed_number(affection_delta)}")
        fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"实际效果：{'，'.join(effects)}。"]
        mutations = [
            {
                "kind": "lottery",
                "fortune": fortune,
                "net_coins": net_coins,
                "affection_delta": affection_delta,
            }
        ]
    else:
        return _build_poke_outcome(
            actor_label=actor_label,
            outcome_kind="plain",
            event_id=event_id,
            memory_text=f"刚才发生的互动：{actor_label}在 QQ 里戳了戳你的头像。",
            prompt_text="",
        )

    return _build_poke_outcome(
        actor_label=actor_label,
        outcome_kind=outcome_kind,
        event_id=event_id,
        memory_text=memory_text,
        prompt_text="【本轮戳一戳结果】\n" + "\n".join(fact_lines),
        mutations=mutations,
    )


def _render_poke_effects(result: dict[str, Any]) -> str:
    effects = dict(result.get("effects_applied") or {})
    before = result.get("state_before") if isinstance(result.get("state_before"), dict) else {}
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
    parts: list[str] = []
    labels = (("hunger", "饥饿值"), ("energy", "精力值"), ("affection", "QQ 好感"))
    for key, label in labels:
        if f"{key}_set" in effects:
            parts.append(f"{label}设为 {int(snapshot.get(key) or effects[f'{key}_set'])}/100")
            continue
        if key not in effects and not (
            (key == "affection" and effects.get("random_affection"))
            or (key in {"hunger", "energy"} and effects.get("random_vitals"))
        ):
            continue
        old_value = int(before.get(key) or 0)
        new_value = int(snapshot.get(key) or 0)
        delta = new_value - old_value
        if delta:
            parts.append(f"{label} {_signed_number(delta)}")
    if effects.get("hunger_energy_swap"):
        parts.append("饥饿值与精力值互换")
    return "，".join(parts) or "数值未变化"


def _poke_seasonal_note(item: dict[str, Any]) -> str:
    if not bool(item.get("seasonal")):
        return ""
    label = str(item.get("seasonal_label") or "限定")
    return f"这是{label}限定商品。"


def _signed_number(value: int) -> str:
    value = int(value or 0)
    return f"+{value}" if value > 0 else str(value)


def _normalize_mface_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    package_raw = value.get("emoji_package_id", value.get("emojiPackageId"))
    emoji_id = str(value.get("emoji_id", value.get("emojiId", "")) or "").strip()
    key = str(value.get("key") or "").strip()
    summary = str(value.get("summary") or value.get("faceName") or value.get("name") or "[商城表情]").strip()
    try:
        emoji_package_id = int(package_raw)
    except (TypeError, ValueError):
        return {}
    if emoji_package_id < 0 or not emoji_id or not key:
        return {}
    return {
        "emoji_package_id": emoji_package_id,
        "emoji_id": emoji_id[:128],
        "key": key[:512],
        "summary": (summary or "[商城表情]")[:80],
    }


def _normalize_emotion_mface_config(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    config = raw.get("emotion_mfaces") if isinstance(raw.get("emotion_mfaces"), dict) else raw
    if not isinstance(config, dict):
        return {"enabled": False, "map": {}, "min_interval_seconds": 0}
    raw_map = config.get("map") if isinstance(config.get("map"), dict) else {}
    if not raw_map and any(isinstance(item, dict) for item in config.values()):
        raw_map = {
            key: item
            for key, item in config.items()
            if key not in {"enabled", "min_interval_seconds", "cooldown_seconds", "send_timing", "dedupe_same_emotion"}
            and isinstance(item, dict)
        }
    normalized_map: dict[str, dict[str, Any]] = {}
    for raw_key, raw_mface in raw_map.items():
        key = str(raw_key or "").strip()
        mface = _normalize_mface_payload(raw_mface)
        if key and mface:
            normalized_map[key] = mface
    try:
        min_interval = int(config.get("min_interval_seconds") or config.get("cooldown_seconds") or 0)
    except (TypeError, ValueError):
        min_interval = 0
    return {
        "enabled": bool(config.get("enabled")) and bool(normalized_map),
        "map": normalized_map,
        "min_interval_seconds": max(0, min(3600, min_interval)),
    }


def _resolve_emotion_mface(emotion: str, mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    clean_emotion = str(emotion or "").strip()
    if not clean_emotion:
        return {}
    candidates = [
        clean_emotion,
        clean_emotion.lower(),
        clean_emotion.replace(" ", "_"),
        clean_emotion.replace("_", " "),
    ]
    normalized_mapping = {str(key).strip(): value for key, value in mapping.items() if str(key).strip()}
    lower_mapping = {key.lower(): value for key, value in normalized_mapping.items()}
    for candidate in candidates:
        if candidate in normalized_mapping:
            return dict(normalized_mapping[candidate])
        lower = candidate.lower()
        if lower in lower_mapping:
            return dict(lower_mapping[lower])
    return {}


def _mface_fingerprint(mface: dict[str, Any]) -> str:
    normalized = _normalize_mface_payload(mface)
    if not normalized:
        return ""
    return "|".join(
        [
            str(normalized.get("emoji_package_id") or ""),
            str(normalized.get("emoji_id") or ""),
            str(normalized.get("key") or ""),
        ]
    )


def _item_usable_as_offering(item: dict[str, Any]) -> bool:
    """Return True for QQ offering item categories."""
    category = str(item.get("category") or "").strip().lower()
    return category in {"offering", "charm", "gift"}


def _merge_shop_items(
    base_items: list[dict[str, Any]],
    override_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge shop item lists by id; later lists replace earlier items with the same id."""
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for raw_item in [*(base_items or []), *(override_items or [])]:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or "").strip()
        if not item_id:
            continue
        item = dict(raw_item)
        if item_id in positions:
            merged[positions[item_id]] = item
        else:
            positions[item_id] = len(merged)
            merged.append(item)
    return merged


def _parse_item_and_quantity(text: str) -> "tuple[str, int]":
    """Parse 'item_nameX5' or 'item_name x5' → ('item_name', 5). Returns (text, 1) if no qty suffix."""
    m = _QTY_RE.match(text.strip())
    if m:
        qty = max(1, min(99, int(m.group(2))))
        return m.group(1).strip(), qty
    return text.strip(), 1


def _find_shop_item(items: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Find a shop item by id or name (case-insensitive)."""
    query_clean = str(query or "").strip().lower()
    if not query_clean:
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip().lower() == query_clean:
            return item
        if str(item.get("name") or "").strip().lower() == query_clean:
            return item
    return None


def _format_effects_summary(effects: dict[str, Any]) -> str:
    """Format item effects as a short human-readable summary (for shop/backpack listing)."""
    parts: list[str] = []
    hunger = int(effects.get("hunger") or 0)
    energy = int(effects.get("energy") or 0)
    affection = int(effects.get("affection") or 0)
    if hunger:
        parts.append(f"饥饿{'+' if hunger > 0 else ''}{hunger}")
    if energy:
        parts.append(f"精力{'+' if energy > 0 else ''}{energy}")
    if affection:
        parts.append(f"好感{'+' if affection > 0 else ''}{affection}")
    if "hunger_set" in effects:
        parts.append(f"饥饿→{int(effects['hunger_set'])}")
    if "energy_set" in effects:
        parts.append(f"精力→{int(effects['energy_set'])}")
    if "affection_set" in effects:
        parts.append(f"好感→{int(effects['affection_set'])}")
    if effects.get("hunger_energy_swap"):
        parts.append("饥饿⇄精力")
    if effects.get("random_vitals"):
        parts.append("体征随机±")
    if effects.get("random_affection"):
        parts.append("好感随机±")
    return "  ".join(parts)


def _build_item_effect_reaction_hint(
    *,
    item_name: str,
    effects_applied: dict[str, Any],
    hunger: int,
    energy: int,
) -> str:
    """Build an explicit LLM-facing reaction hint for special item effects."""
    hints: list[str] = []
    if "energy_set" in effects_applied:
        target = int(effects_applied.get("energy_set", energy))
        if target >= 80:
            hints.append(
                f"特别说明：「{item_name}」刚刚让精力恢复到 {target}/100；"
                "这不是普通闲聊，回复里必须明显表现出困意被驱散、眼神清醒或精神突然回来的身体反应。"
            )
        elif target <= 20:
            hints.append(
                f"特别说明：「{item_name}」刚刚让精力降到 {target}/100；回复里必须表现出明显犯困、反应变慢或想休息。"
            )
    if "hunger_set" in effects_applied:
        target = int(effects_applied.get("hunger_set", hunger))
        if target <= 20:
            hints.append(
                f"特别说明：「{item_name}」刚刚让饥饿降到 {target}/100；"
                "0/100 不是不饿，而是饿到极限、胃里空得发慌；"
                "回复里必须表现出突然非常饿、注意力被吃的占住，可以直接要吃的。"
                "禁止说“不饿了”“胃不叫了”“饿感消失”或“空但不饿”。"
            )
        elif target >= 80:
            hints.append(
                f"特别说明：「{item_name}」刚刚让饥饿恢复到 {target}/100；回复里必须表现出胃里踏实、被喂饱或状态回稳。"
            )
    if effects_applied.get("random_vitals"):
        hints.append(
            f"特别说明：「{item_name}」刚刚触发随机体征变化；"
            "回复里要承认身体状态发生了不可预测的变化，并按当前饥饿/精力结果反应。"
        )
    if effects_applied.get("random_affection"):
        hints.append(
            f"特别说明：「{item_name}」刚刚触发随机好感变化；"
            "回复里可以表现出对这个道具效果的意外，但不要忽略当前状态变化。"
        )
    if hints:
        hints.append("可以吐槽道具来路或用户乱来，但不能只有吐槽；必须把道具造成的身体变化演出来。")
    return "".join(hints)
