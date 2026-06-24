from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import config
from .care_runtime import DEFAULT_CARE_SHOP_ITEMS, DEFAULT_CHECKIN_COINS, get_seasonal_shop_items


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

QQ_ECONOMY_STATUS_QUERY_FIELDS: tuple[str, ...] = (
    "饥饿",
    "饥饿度",
    "饥饿值",
    "饿",
    "精力",
    "精力值",
    "体力",
    "好感",
    "好感度",
    "金币",
    "钱",
)
QQ_ECONOMY_STATUS_QUERY_WORDS: tuple[str, ...] = ("多少", "几", "状态", "现在", "当前")
QQ_MFACE_CONFIG_COMMAND_RE = re.compile(
    r"^(?:表情包配置|抓表情包|提取表情包|mface配置|mface config)(?:[:：\s]+(.+?))?$",
    re.IGNORECASE,
)


def _is_economy_status_query(text: str) -> bool:
    """Recognize natural QQ questions that ask for current care values."""
    normalized = str(text or "").strip()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if compact in QQ_ECONOMY_STATUS_COMMANDS:
        return True
    has_field = any(field in compact for field in QQ_ECONOMY_STATUS_QUERY_FIELDS)
    has_query_word = any(word in compact for word in QQ_ECONOMY_STATUS_QUERY_WORDS)
    if has_field and has_query_word:
        return True
    return bool(re.fullmatch(r"(饿|困|累|精神)(不|吗|了没|没|嘛|么)?[？?]?", compact))


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
    def __init__(self, *, state_path: str | Path | None = None) -> None:
        self.group_follow_state: dict[str, dict[str, Any]] = {}
        self.recent_event_fingerprints: dict[str, float] = {}
        self.sender_label_cache: dict[str, str] = {}
        self.attachment_debounce_state: dict[str, dict[str, Any]] = {}
        self._attachment_debounce_lock = threading.RLock()
        self._state_path = Path(state_path) if state_path is not None else None
        self.character_pack_overrides: dict[str, str] = {}
        self._character_pack_lock = threading.RLock()
        self.reply_mode_overrides: dict[str, str] = {}
        self._reply_mode_lock = threading.RLock()
        self.emotion_mface_state: dict[str, dict[str, Any]] = {}
        self._emotion_mface_lock = threading.RLock()
        self.emotion_image_state: dict[str, dict[str, Any]] = {}
        self._emotion_image_lock = threading.RLock()
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
        raw_overrides = payload.get("character_pack_overrides")
        if not isinstance(raw_overrides, dict):
            return
        overrides: dict[str, str] = {}
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

    def _persist_character_pack_overrides(self) -> bool:
        if self._state_path is None:
            self._state_error = ""
            return True
        try:
            with self._character_pack_lock:
                overrides = dict(self.character_pack_overrides)
            payload = {
                "schema_version": QQ_GATEWAY_STATE_SCHEMA_VERSION,
                "character_pack_overrides": overrides,
                "updated_at": int(time.time()),
            }
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_name(
                f"{self._state_path.name}.{uuid.uuid4().hex}.tmp"
            )
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
            "state_persistence_enabled": self._state_path is not None,
            "state_status": "error" if self._state_error else ("enabled" if self._state_path is not None else "disabled"),
            "state_error": self._state_error,
            "reply_mode": self.default_reply_mode,
            "default_reply_mode": self.default_reply_mode,
            "active_reply_mode_override_count": len(self.reply_mode_overrides),
            "active_emotion_mface_session_count": len(self.emotion_mface_state),
            "active_emotion_image_session_count": len(self.emotion_image_state),
            "active_group_attachment_buffer_count": len(self.group_follow_state),
            "active_attachment_debounce_count": len(self.attachment_debounce_state),
        }

    def self_check(self) -> dict[str, Any]:
        """主动对 OneBot HTTP API 做连通性自检，返回结构化诊断结果。

        不执行任何消息发送动作，只检查：
        1. QQ bridge 是否已在配置里启用
        2. OneBot URL 格式是否合理
        3. /get_login_info 是否可达（连通性 + 鉴权）
        4. 返回登录账号信息（安全字段：user_id/nickname）

        不暴露 token、cookie 或本地绝对路径。
        """
        enabled = bool(getattr(config, "QQ_BRIDGE_ENABLED", False))
        if not enabled:
            return {
                "ok": False,
                "status": "bridge_disabled",
                "reason": "QQ_BRIDGE_ENABLED 未启用；在 .env 里设置 QQ_BRIDGE_ENABLED=true 并配置 NapCat。",
                "onebot_http_url": self.onebot_http_url,
            }

        url = self.onebot_http_url
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return {
                "ok": False,
                "status": "invalid_url",
                "reason": f"OneBot URL 格式不正确：{url!r}",
                "onebot_http_url": url,
            }

        try:
            response = requests.get(f"{url}/get_login_info", timeout=5)
        except requests.exceptions.ConnectionError:
            return {
                "ok": False,
                "status": "unreachable",
                "reason": f"无法连接到 {url}：端口不可达，请确认 NapCat 已启动并监听该端口。",
                "onebot_http_url": url,
            }
        except requests.exceptions.Timeout:
            return {
                "ok": False,
                "status": "timeout",
                "reason": f"连接 {url} 超时（5 秒）：NapCat 可能正在启动或端口被防火墙拦截。",
                "onebot_http_url": url,
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "connection_error",
                "reason": f"连接异常：{type(exc).__name__}",
                "onebot_http_url": url,
            }

        if response.status_code == 401 or response.status_code == 403:
            return {
                "ok": False,
                "status": "auth_failed",
                "reason": f"鉴权失败（HTTP {response.status_code}）：请检查 NapCat 访问令牌配置。",
                "onebot_http_url": url,
            }

        if response.status_code != 200:
            return {
                "ok": False,
                "status": "http_error",
                "reason": f"OneBot 返回 HTTP {response.status_code}，预期 200。",
                "onebot_http_url": url,
            }

        try:
            data = response.json()
        except Exception:
            return {
                "ok": False,
                "status": "invalid_response",
                "reason": "OneBot 返回了非 JSON 响应，可能是服务未完全启动。",
                "onebot_http_url": url,
            }

        retcode = data.get("retcode", data.get("status"))
        inner_data = data.get("data") or {}
        user_id = inner_data.get("user_id") or ""
        nickname = inner_data.get("nickname") or ""

        if retcode not in (0, "ok"):
            return {
                "ok": False,
                "status": "onebot_error",
                "reason": f"OneBot 接口返回错误：retcode={retcode!r}",
                "onebot_http_url": url,
            }

        return {
            "ok": True,
            "status": "connected",
            "onebot_http_url": url,
            "bot_qq": str(user_id) if user_id else self.bot_qq,
            "nickname": str(nickname),
            "checks": {
                "bridge_enabled": True,
                "url_reachable": True,
                "login_info": True,
                "send_test": "not_tested",
            },
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
            return self.build_notice_context(event)

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

    def build_notice_context(self, event: dict[str, Any]) -> QQMessageContext:
        if not self._is_poke_notice(event):
            return QQMessageContext(False, "not_message_event")

        target_id = self._safe_int(event.get("target_id"))
        bot_ids = {self._safe_int(item) for item in self._bot_target_ids(event)}
        if not target_id or target_id not in bot_ids:
            return QQMessageContext(False, "poke_not_for_bot")

        user_id = self._resolve_poke_user_id(event, target_id=target_id)
        group_id = self._safe_int(event.get("group_id"))
        self_id = self._safe_int(event.get("self_id"))
        if user_id and user_id in {self._safe_int(self.bot_qq), self_id}:
            return QQMessageContext(False, "self_message")

        if self._is_stale_event(event):
            return QQMessageContext(False, "stale_event")

        if self._is_duplicate_event(event):
            return QQMessageContext(False, "duplicate_event")

        is_group = bool(group_id)
        session_id, profile_user_id = self.resolve_identity(user_id=user_id, group_id=group_id)
        sender_label = self.resolve_sender_label(event=event, user_id=user_id)
        character_pack_id = self.resolve_character_pack_id(session_id)
        reply_mode = self.resolve_reply_mode(session_id)
        actor_label = sender_label or (f"QQ {user_id}" if user_id else "这位 QQ 用户")
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
            attachments=[],
            extra_context=self.build_extra_context(
                event=event,
                is_group=is_group,
                user_id=user_id,
                group_id=group_id,
                sender_label=sender_label,
                reply_mode=reply_mode,
            )
            + f"\n本轮 QQ 事件：{actor_label}双击头像戳了戳你；{actor_label}就是本轮戳一戳的发送者，请把它当作一次真实互动回应。"
            + "\n若历史记忆、旧聊天记录或用户转述里出现“有人戳了戳你”这类模糊说法，请优先依据本轮 QQ 事件里的发送者标识来回应。",
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
        key = _safe_qq_session_key(session_id)
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

    def set_session_character_pack_id(self, session_id: str, character_pack_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._character_pack_lock:
            self.character_pack_overrides[key] = _safe_character_pack_id(character_pack_id)
        return self._persist_character_pack_overrides()

    def clear_session_character_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._character_pack_lock:
            self.character_pack_overrides.pop(key, None)
        return self._persist_character_pack_overrides()

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
            "reply": (
                f"已抓到当前 QQ 会话角色包 {pack_id} 的表情包配置片段，"
                f"emotion={emotion}：\n{snippet_text}"
            ),
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

    def _is_poke_notice(self, event: dict[str, Any]) -> bool:
        post_type = str(event.get("post_type") or "").strip().lower()
        notice_type = str(event.get("notice_type") or "").strip().lower()
        sub_type = str(event.get("sub_type") or event.get("notice_sub_type") or "").strip().lower()
        if post_type not in {"notice", "notify"}:
            return False
        if notice_type == "poke":
            return True
        return notice_type == "notify" and sub_type == "poke"

    def _resolve_poke_user_id(self, event: dict[str, Any], *, target_id: int) -> int:
        candidates = [
            self._safe_int(event.get("operator_id")),
            self._safe_int(event.get("sender_id")),
            self._safe_int(event.get("user_id")),
        ]
        for candidate in candidates:
            if candidate and candidate != target_id:
                return candidate
        for candidate in candidates:
            if candidate:
                return candidate
        return 0

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

    def _sender_label_cache_key(self, *, group_id: int, user_id: int) -> str:
        if not user_id:
            return ""
        return f"{group_id or 0}:{user_id}"

    def lookup_group_member_label(self, *, group_id: int, user_id: int) -> str:
        if not group_id or not user_id:
            return ""
        payload = {"group_id": group_id, "user_id": user_id, "no_cache": False}
        try:
            response = requests.post(f"{self.onebot_http_url}/get_group_member_info", json=payload, timeout=3)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        if data.get("retcode", data.get("status")) not in (0, "ok"):
            return ""
        member = data.get("data") if isinstance(data.get("data"), dict) else {}
        label = str(member.get("card") or member.get("nickname") or "").strip()
        return label

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
                item_text = text[len(prefix):].strip()
                if item_text:
                    item_name, qty = _parse_item_and_quantity(item_text)
                    return {"action": "buy", "item_name": item_name, "quantity": qty}
        if text in QQ_ECONOMY_FEED_COMMANDS:
            return {"action": "feed"}
        for prefix in QQ_ECONOMY_FEED_PREFIXES:
            if text.startswith(prefix):
                item_text = text[len(prefix):].strip()
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
                item_name = text[len(prefix):].strip()
                if item_name:
                    return {"action": "offering", "item_name": item_name}
        return None

    def handle_economy_command(
        self,
        context: "QQMessageContext",
        *,
        care_runtime: Any = None,
        shop_items: list[dict[str, Any]] | None = None,
        checkin_coins: int = DEFAULT_CHECKIN_COINS,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Handle economy commands (签到/状态/商店/购买/供奉). Returns None if not applicable."""
        parsed = self.parse_economy_command(context.clean_message)
        if parsed is None:
            return None

        if care_runtime is None:
            return {"ok": False, "reply": "养成系统未启用。", "status": "not_configured"}

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
                reply = (
                    "养成状态\n"
                    f"饥饿 {h}/100（越低越饿）  精力 {e}/100（越高越精神）\n"
                    f"QQ好感 {a}/100  金币 {c}"
                )
                return {"ok": True, "reply": reply, "status": "ok"}

            if action == "shop_list":
                qq_items = [item for item in items if _item_usable_in_qq(item)]
                if not qq_items:
                    return {"ok": True, "reply": "商店暂时没有商品。", "status": "empty"}
                seasonal_items = [i for i in qq_items if i.get("seasonal")]
                regular_items = [i for i in qq_items if not i.get("seasonal")]
                food_items = [i for i in regular_items if i.get("category") not in ("offering", "charm", "gift", "trick", "potion")]
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
                        "reply": (
                            f"背包里没有「{item_name_display}」，"
                            f"先发送「购买 {item_name_display}」入手。"
                        ),
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
                h = int(snap.get("hunger") or 0)
                e = int(snap.get("energy") or 0)
                eff_str = _format_applied_effects_note(result["effects_applied"], qty)
                eff_note = f"（{eff_str}）" if eff_str else ""
                if h < 15:
                    hunger_desc = "极度饥饿"
                elif h < 30:
                    hunger_desc = "很饿"
                elif h < 50:
                    hunger_desc = "有些饿"
                else:
                    hunger_desc = ""
                if e < 15:
                    energy_desc = "精疲力竭"
                elif e < 30:
                    energy_desc = "很疲倦"
                elif e < 50:
                    energy_desc = "有些累"
                else:
                    energy_desc = ""
                state_parts = [s for s in (hunger_desc, energy_desc) if s]
                state_desc = "、".join(state_parts) if state_parts else "状态还行"
                qty_str = f" x{qty}" if qty > 1 else ""
                effect_context = _build_item_effect_reaction_hint(
                    item_name=item_name_display,
                    effects_applied=result["effects_applied"],
                    hunger=h,
                    energy=e,
                )
                if result["effects_applied"].get("hunger_energy_swap"):
                    effect_context += (
                        f"特别说明：这是「{item_name_display}」刚刚生效，把饥饿值和精力值交换了；"
                        "不要理解成用户说反了，也不要说“你把饥饿和精力对调了”。"
                    )
                note = (
                    f"【最新投喂】用户此刻给了你「{item_name_display}」{qty_str}{eff_note}。"
                    f"{effect_context}"
                    f"投喂后你的状态：饥饿 {h}/100，精力 {e}/100（{state_desc}）。"
                    f"请用符合你当前状态和性格的方式回应——把真实感受说出来，"
                    f"不只是念出食物名字，也不要假装特别感动。"
                    f"这是此刻刚发生的投喂，与历史对话无关。"
                )
                return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

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
                net = result["net_coins"]
                coins_after = result["coins_after"]
                aff_delta = result["affection_delta"]

                # Build LLM note based on fortune
                if fortune == "大吉":
                    reaction_hint = (
                        "你可以显得很自信甚至有点得意——'神社的神力当然灵验'，但保持傲娇，不要过分热情。"
                        f"用户好感也因此上升了（+{aff_delta}）。"
                    )
                elif fortune == "中吉":
                    reaction_hint = "平静地告知结果就好，可以说'中吉也挺不错的'或者淡淡地点头表示满意。"
                elif fortune == "小吉":
                    reaction_hint = "小吉而已，可以说几乎回本了，语气平淡，不至于失落，也不必假装很好。"
                elif fortune == "末吉":
                    reaction_hint = (
                        "末吉，什么都没得到。可以安慰两句'末吉不是坏签'，"
                        "也可以直接说'运气就这样，下次再来'，不必太尴尬。"
                    )
                else:  # 凶
                    reaction_hint = (
                        "用户抽到了凶签，还额外损失了3金币。"
                        "你可以用你的方式解释——'凶签是在提醒你注意些什么'，"
                        "或者有点幸灾乐祸，或者尴尬地为神社辩护，"
                        "但不要太过份，给个台阶下。"
                    )

                coin_desc = f"+{net} 金币" if net > 0 else (f"{net} 金币" if net < 0 else "金币不变")
                note = (
                    f"【御神签结果】用户花了 {SLIP_COST} 金币抽了一签，结果是【{fortune}】"
                    f"（{coin_desc}，当前 {coins_after} 金币）。{reaction_hint}"
                    f"用你的语气宣布签运结果，把这件事说得有点仪式感，但别假装是大事。"
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
                            "reply": (
                                f"「{matched['name']}」不是供奉/礼物类商品；"
                                f"普通食物请先购买再投喂。"
                            ),
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
                            "reply": (
                                f"背包里没有「{matched['name']}」，"
                                f"先发送「购买 {matched['name']}」入手再供奉。"
                            ),
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
                    eff_parts = []
                    for k, label in (("hunger", "饥饿"), ("energy", "精力"), ("affection", "好感")):
                        v = int(effects.get(k) or 0)
                        if v:
                            eff_parts.append(f"{label}+{v}")
                    eff_note = "（" + "、".join(eff_parts) + "）" if eff_parts else ""
                    if result.get("daily_bonus"):
                        note = (
                            f"【最新供奉】用户此刻向博丽神社供奉了「{matched['name']}」{eff_note}，"
                            f"这是今天的第一次供奉，请自然地回应。"
                        )
                    else:
                        note = (
                            f"【供奉通知】用户再次供奉「{matched['name']}」{eff_note}，"
                            f"今日好感奖励已领取，但诚意依旧在。"
                        )
                    return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": result["status"]}

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
                if result.get("daily_bonus"):
                    note = (
                        f"【最新供奉】用户此刻虔诚地向博丽神社供奉（好感+3，当前 {aff_now}/100），"
                        f"这是今天的第一次供奉，请自然地回应。"
                    )
                else:
                    note = (
                        f"【供奉通知】用户今日再次来供奉，今日好感奖励已领取，"
                        f"但依然来了（当前好感 {aff_now}/100）。"
                    )
                return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": result["status"]}

        except Exception as exc:
            return {"ok": False, "reply": "养成系统暂时出错，请稍后再试。", "status": "error", "error": str(exc)}

        return None

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

    def send_mface(self, context: QQMessageContext, *, mface: dict[str, Any]) -> dict[str, Any]:
        """Send a NapCat / OneBot marketplace emoji message segment."""
        data = _normalize_mface_payload(mface)
        if not context.target_id:
            return {"ok": False, "reason": "empty_target"}
        if not data:
            return {"ok": False, "reason": "invalid_mface_payload"}

        action = "send_group_msg" if context.is_group else "send_private_msg"
        payload = (
            {
                "group_id": context.target_id,
                "message": [{"type": "mface", "data": data}],
            }
            if context.is_group
            else {
                "user_id": context.target_id,
                "message": [{"type": "mface", "data": data}],
            }
        )
        try:
            response = requests.post(f"{self.onebot_http_url}/{action}", json=payload, timeout=8)
            response.raise_for_status()
            return {"ok": True, "action": action, "data": response.json(), "mface": data}
        except Exception as exc:
            return {"ok": False, "action": action, "reason": str(exc), "mface": data}

    def send_emotion_mface(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        qq_delivery_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a configured QQ mface based on final_output.emotion."""
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
        session_key = _safe_qq_session_key(context.session_id) or f"{context.target_id}:{'group' if context.is_group else 'private'}"
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
        emotion = str((frame or {}).get("emotion") or "").strip()
        if not emotion:
            return {"ok": True, "status": "skipped", "reason": "empty_emotion"}
        image = image if isinstance(image, dict) else {}
        image_path = str(image.get("path") or "").strip()
        if not image_path:
            return {"ok": True, "status": "skipped", "reason": "missing_emotion_image", "emotion": emotion}

        min_interval = max(0, min(3600, int(min_interval_seconds or 0)))
        fingerprint = f"image|{image_path}"
        session_key = _safe_qq_session_key(context.session_id) or f"{context.target_id}:{'group' if context.is_group else 'private'}"
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
        post_type = str(event.get("post_type") or "").strip()
        notice_type = str(event.get("notice_type") or "").strip()
        sub_type = str(event.get("sub_type") or event.get("notice_sub_type") or "").strip()
        message_type = str(event.get("message_type") or "").strip()
        user_id = str(event.get("user_id") or event.get("sender_id") or event.get("operator_id") or "").strip()
        group_id = str(event.get("group_id") or "").strip()
        self_id = str(event.get("self_id") or "").strip()
        target_id = str(event.get("target_id") or "").strip()

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
        if user_id or group_id or target_id or raw_message:
            fingerprints.append(
                f"fallback:{self_id}|{post_type}|{notice_type}|{sub_type}|"
                f"{message_type}|{user_id}|{group_id}|{target_id}|{timestamp}|{raw_message}"
            )

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


def _format_applied_effects_note(effects_applied: dict[str, Any], count: int) -> str:
    """Format what actually happened after item use, including resolved random values."""
    parts: list[str] = []
    hunger = int(effects_applied.get("hunger") or 0) * count
    energy = int(effects_applied.get("energy") or 0) * count
    affection = int(effects_applied.get("affection") or 0) * count
    if hunger:
        parts.append(f"饥饿{'+' if hunger > 0 else ''}{hunger}")
    if energy:
        parts.append(f"精力{'+' if energy > 0 else ''}{energy}")
    if affection:
        parts.append(f"好感{'+' if affection > 0 else ''}{affection}")
    if "hunger_set" in effects_applied:
        parts.append(f"饥饿→{int(effects_applied['hunger_set'])}")
    if "energy_set" in effects_applied:
        parts.append(f"精力→{int(effects_applied['energy_set'])}")
    if "affection_set" in effects_applied:
        parts.append(f"好感→{int(effects_applied['affection_set'])}")
    if effects_applied.get("hunger_energy_swap"):
        parts.append("饥饿⇄精力已互换")
    h_delta = effects_applied.get("_resolved_h_delta")
    e_delta = effects_applied.get("_resolved_e_delta")
    aff_delta = effects_applied.get("_resolved_aff_delta")
    if h_delta is not None:
        parts.append(f"饥饿{'+' if int(h_delta) > 0 else ''}{int(h_delta)}（随机）")
    if e_delta is not None:
        parts.append(f"精力{'+' if int(e_delta) > 0 else ''}{int(e_delta)}（随机）")
    if aff_delta is not None:
        parts.append(f"好感{'+' if int(aff_delta) > 0 else ''}{int(aff_delta)}（随机）")
    return "、".join(parts)


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
                f"特别说明：「{item_name}」刚刚让精力降到 {target}/100；"
                "回复里必须表现出明显犯困、反应变慢或想休息。"
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
                f"特别说明：「{item_name}」刚刚让饥饿恢复到 {target}/100；"
                "回复里必须表现出胃里踏实、被喂饱或状态回稳。"
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
