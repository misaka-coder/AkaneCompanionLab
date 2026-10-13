"""QQ session preferences and atomic, ordered state persistence."""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from companion_v01.channels.qq.constants import QQ_GATEWAY_STATE_SCHEMA_VERSION
from companion_v01.channels.qq.validation import (
    _safe_character_pack_id,
    _safe_chat_model_id,
    _safe_outfit_id,
    _safe_qq_session_key,
    _safe_reply_mode,
)


class QQSessionPreferences:
    """Own preference maps and serialize each complete snapshot through replace.

    Every field lock is the same reentrant lock, so a persisted snapshot cannot
    race a later preference update or overwrite a newer write.
    """

    def __init__(self, *, state_path: str | Path | None,
                 default_character_pack_id: Callable[[], str],
                 default_reply_mode: Callable[[], str]) -> None:
        self._state_path = Path(state_path) if state_path is not None else None
        self._state_error = ""
        self._default_character_pack_id = default_character_pack_id
        self._default_reply_mode = default_reply_mode
        self._lock = threading.RLock()
        self.character_pack_overrides: dict[str, str] = {}
        self._character_pack_lock = self._lock
        self.outfit_overrides: dict[str, str] = {}
        self._outfit_lock = self._lock
        self.reply_mode_overrides: dict[str, str] = {}
        self._reply_mode_lock = self._lock
        self.chat_model_overrides: dict[str, str] = {}
        self._chat_model_lock = self._lock
        self.group_vision_overrides: dict[str, bool] = {}
        self._group_vision_lock = self._lock
        self.group_emotion_overrides: dict[str, bool] = {}
        self._group_emotion_lock = self._lock
        self.group_attention_mode_overrides: dict[str, str] = {}
        self._group_attention_mode_lock = self._lock
        self.group_private_memory: dict[str, dict[str, Any]] = {}

    @property
    def default_character_pack_id(self) -> str:
        return self._default_character_pack_id()

    @property
    def default_reply_mode(self) -> str:
        return _safe_reply_mode(self._default_reply_mode())

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_character_override_count": len(self.character_pack_overrides),
                "active_outfit_override_count": len(self.outfit_overrides),
                "active_chat_model_override_count": len(self.chat_model_overrides),
                "disabled_group_vision_count": sum(enabled is False for enabled in self.group_vision_overrides.values()),
                "disabled_group_emotion_count": sum(enabled is False for enabled in self.group_emotion_overrides.values()),
                "group_attention_mode_override_count": len(self.group_attention_mode_overrides),
                "private_memory_receiver_count": sum(item["enabled"] for item in self.group_private_memory.values()),
                "active_reply_mode_override_count": len(self.reply_mode_overrides),
                "state_persistence_enabled": self._state_path is not None,
                "state_status": "error" if self._state_error else ("enabled" if self._state_path is not None else "disabled"),
                "state_error": self._state_error,
            }

    def _load_persisted_state(self) -> None:
        with self._lock:
            if self._state_path is None or not self._state_path.is_file():
                return
            try:
                payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
                self.character_pack_overrides.clear()
                self.character_pack_overrides.update(overrides)

            outfit_overrides: dict[str, str] = {}
            raw_outfits = payload.get("outfit_overrides")
            if isinstance(raw_outfits, dict):
                for raw_key, raw_value in raw_outfits.items():
                    key = _safe_qq_session_key(raw_key)
                    outfit_id = _safe_outfit_id(raw_value)
                    if key and outfit_id:
                        outfit_overrides[key] = outfit_id
            with self._outfit_lock:
                self.outfit_overrides.clear()
                self.outfit_overrides.update(outfit_overrides)

            model_overrides: dict[str, str] = {}
            raw_models = payload.get("chat_model_overrides")
            if isinstance(raw_models, dict):
                for raw_key, raw_value in raw_models.items():
                    key = _safe_qq_session_key(raw_key)
                    model = _safe_chat_model_id(raw_value)
                    if key and model:
                        model_overrides[key] = model
            with self._chat_model_lock:
                self.chat_model_overrides.clear()
                self.chat_model_overrides.update(model_overrides)

            group_vision_overrides: dict[str, bool] = {}
            raw_group_vision = payload.get("group_vision_overrides")
            if isinstance(raw_group_vision, dict):
                for raw_group_id, raw_enabled in raw_group_vision.items():
                    group_id = self._safe_int(raw_group_id)
                    if group_id > 0 and raw_enabled is False:
                        group_vision_overrides[str(group_id)] = False
            with self._group_vision_lock:
                self.group_vision_overrides.clear()
                self.group_vision_overrides.update(group_vision_overrides)

            group_emotion_overrides: dict[str, bool] = {}
            raw_group_emotion = payload.get("group_emotion_overrides")
            if isinstance(raw_group_emotion, dict):
                for raw_group_id, raw_enabled in raw_group_emotion.items():
                    group_id = self._safe_int(raw_group_id)
                    if group_id > 0 and raw_enabled is False:
                        group_emotion_overrides[str(group_id)] = False
            with self._group_emotion_lock:
                self.group_emotion_overrides.clear()
                self.group_emotion_overrides.update(group_emotion_overrides)

            group_attention_modes: dict[str, str] = {}
            raw_group_attention_modes = payload.get("group_attention_mode_overrides")
            if isinstance(raw_group_attention_modes, dict):
                for raw_group_id, raw_mode in raw_group_attention_modes.items():
                    group_id = self._safe_int(raw_group_id)
                    mode = str(raw_mode or "").strip().lower()
                    if group_id > 0 and mode in {"off", "engaged", "adaptive"}:
                        group_attention_modes[str(group_id)] = mode
            with self._group_attention_mode_lock:
                self.group_attention_mode_overrides.clear()
                self.group_attention_mode_overrides.update(group_attention_modes)
            self.reply_mode_overrides.clear()
            self.group_private_memory.clear()
            raw_private_memory = payload.get("group_private_memory")
            for group_id, state in (raw_private_memory if isinstance(raw_private_memory, dict) else {}).items():
                if (str(group_id).isdigit() and int(group_id) > 0 and isinstance(state, dict)
                        and isinstance(state.get("enabled"), bool)
                        and type(state.get("generation")) is int and state["generation"] > 0):
                    self.group_private_memory[str(int(group_id))] = dict(state)
            raw_reply_modes = payload.get("reply_mode_overrides")
            if isinstance(raw_reply_modes, dict):
                for raw_key, raw_value in raw_reply_modes.items():
                    key = _safe_qq_session_key(raw_key)
                    mode = _safe_reply_mode(raw_value, default="")
                    if key and mode:
                        self.reply_mode_overrides[key] = mode
            self._state_error = ""

    def _persist_gateway_state(self) -> bool:
        with self._lock:
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
                with self._group_emotion_lock:
                    group_emotion_overrides = dict(self.group_emotion_overrides)
                with self._group_attention_mode_lock:
                    group_attention_mode_overrides = dict(self.group_attention_mode_overrides)
                payload = {
                    "schema_version": QQ_GATEWAY_STATE_SCHEMA_VERSION,
                    "character_pack_overrides": character_overrides,
                    "outfit_overrides": outfit_overrides,
                    "chat_model_overrides": chat_model_overrides,
                    "group_vision_overrides": group_vision_overrides,
                    "group_emotion_overrides": group_emotion_overrides,
                    "group_attention_mode_overrides": group_attention_mode_overrides,
                    "reply_mode_overrides": dict(self.reply_mode_overrides),
                    "group_private_memory": dict(self.group_private_memory),
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

    def private_memory_receivers(self) -> dict[str, int]:
        with self._lock:
            return {group: state["generation"] for group, state in self.group_private_memory.items() if state["enabled"]}

    def private_memory_policy(self, group_id: Any) -> tuple[bool, int]:
        with self._lock:
            state = self.group_private_memory.get(str(self._safe_int(group_id)), {})
            return bool(state.get("enabled", False)), int(state.get("generation", 0))

    def set_private_memory_enabled(self, group_id: Any, enabled: bool) -> bool:
        group = str(self._safe_int(group_id))
        if int(group) <= 0 or not isinstance(enabled, bool) or self._state_path is None:
            return False
        with self._lock:
            previous = self.group_private_memory.get(group)
            if previous is not None and previous["enabled"] == enabled:
                return True
            self.group_private_memory[group] = {
                "enabled": enabled, "generation": int((previous or {}).get("generation", 0)) + 1,
            }
            if self._persist_gateway_state():
                return True
            if previous is None:
                self.group_private_memory.pop(group, None)
            else:
                self.group_private_memory[group] = previous
            return False

    @contextmanager
    def private_memory_import_guard(self, group_id: Any, generation: int):
        """Serialize consent changes with the short atomic import, never an LLM call."""
        with self._lock:
            yield self.private_memory_policy(group_id) == (True, generation)

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
        return self._persist_gateway_state()

    def is_group_emotion_enabled(self, group_id: Any) -> bool:
        normalized_group_id = self._safe_int(group_id)
        if normalized_group_id <= 0:
            return True
        with self._group_emotion_lock:
            return self.group_emotion_overrides.get(str(normalized_group_id)) is not False

    def set_group_emotion_enabled(self, group_id: Any, enabled: bool) -> bool:
        normalized_group_id = self._safe_int(group_id)
        if normalized_group_id <= 0:
            return False
        with self._group_emotion_lock:
            if enabled:
                self.group_emotion_overrides.pop(str(normalized_group_id), None)
            else:
                self.group_emotion_overrides[str(normalized_group_id)] = False
        return self._persist_gateway_state()

    def resolve_group_attention_mode(self, group_id: Any, *, default: str = "engaged") -> str:
        normalized_default = str(default or "engaged").strip().lower()
        if normalized_default not in {"off", "engaged", "adaptive"}:
            normalized_default = "engaged"
        normalized_group_id = self._safe_int(group_id)
        if normalized_group_id <= 0:
            return normalized_default
        with self._group_attention_mode_lock:
            return self.group_attention_mode_overrides.get(str(normalized_group_id), normalized_default)

    def set_group_attention_mode(self, group_id: Any, mode: str, *, default: str = "engaged") -> bool:
        normalized_group_id = self._safe_int(group_id)
        normalized_mode = str(mode or "").strip().lower()
        normalized_default = str(default or "engaged").strip().lower()
        if normalized_group_id <= 0 or normalized_mode not in {"off", "engaged", "adaptive"}:
            return False
        with self._group_attention_mode_lock:
            if normalized_mode == normalized_default:
                self.group_attention_mode_overrides.pop(str(normalized_group_id), None)
            else:
                self.group_attention_mode_overrides[str(normalized_group_id)] = normalized_mode
        return self._persist_gateway_state()

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
        return self._persist_gateway_state()

    def clear_session_outfit_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._outfit_lock:
            self.outfit_overrides.pop(key, None)
        return self._persist_gateway_state()

    def set_session_character_pack_id(self, session_id: str, character_pack_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._character_pack_lock:
            self.character_pack_overrides[key] = _safe_character_pack_id(character_pack_id)
        with self._outfit_lock:
            self.outfit_overrides.pop(key, None)
        return self._persist_gateway_state()

    def clear_session_character_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._character_pack_lock:
            self.character_pack_overrides.pop(key, None)
        with self._outfit_lock:
            self.outfit_overrides.pop(key, None)
        return self._persist_gateway_state()

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
        return self._persist_gateway_state()

    def clear_session_chat_model_override(self, session_id: str) -> bool:
        key = _safe_qq_session_key(session_id)
        if not key:
            return False
        with self._chat_model_lock:
            self.chat_model_overrides.pop(key, None)
        return self._persist_gateway_state()

    def clear_all_chat_model_overrides(self) -> bool:
        with self._chat_model_lock:
            if not self.chat_model_overrides:
                return True
            self.chat_model_overrides.clear()
        return self._persist_gateway_state()

    def resolve_reply_mode(self, session_id: str) -> str:
        key = str(session_id or "").strip()
        if not key:
            return self.default_reply_mode
        with self._reply_mode_lock:
            if key in self.reply_mode_overrides:
                return _safe_reply_mode(self.reply_mode_overrides.get(key), default=self.default_reply_mode)
        return self.default_reply_mode

    def set_session_reply_mode(self, session_id: str, reply_mode: str) -> bool:
        key = str(session_id or "").strip()
        mode = _safe_reply_mode(reply_mode, default="")
        if not key or not mode:
            return False
        with self._reply_mode_lock:
            previous = dict(self.reply_mode_overrides)
            self.reply_mode_overrides[key] = mode
            if self._persist_gateway_state():
                return True
            self.reply_mode_overrides.clear()
            self.reply_mode_overrides.update(previous)
            return False

    def clear_session_reply_mode_override(self, session_id: str) -> bool:
        key = str(session_id or "").strip()
        if not key:
            return False
        with self._reply_mode_lock:
            previous = dict(self.reply_mode_overrides)
            self.reply_mode_overrides.pop(key, None)
            if self._persist_gateway_state():
                return True
            self.reply_mode_overrides.update(previous)
            return False

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0
