"""QQ emotions delivery policy and its owned state."""
from __future__ import annotations
import json
import threading
import time
from typing import Any, Callable
import config
from .context import QQMessageContext
from .validation import _safe_qq_session_key


class QQEmotionDelivery:
    """Own emotions behavior; transport and cross-domain operations use the injected port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway
        self.emotion_mface_state: dict[str, dict[str, Any]] = {}
        self._emotion_mface_lock = threading.RLock()
        self.emotion_image_state: dict[str, dict[str, Any]] = {}
        self._emotion_image_lock = threading.RLock()
        self._pending_mfaces: dict[str, dict[str, object]] = {}
        self._latest_mfaces: dict[str, object] = {}
        self._pending_images: dict[str, dict[str, object]] = {}
        self._latest_images: dict[str, object] = {}

    def _send_with_dedupe(
        self, *, context: QQMessageContext, emotion: str, fingerprint: str,
        min_interval: int, state: dict[str, dict[str, Any]], lock: Any,
        pending: dict[str, dict[str, object]], latest: dict[str, object],
        sender: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        session_key = (
            _safe_qq_session_key(context.session_id)
            or f"{context.target_id}:{'group' if context.is_group else 'private'}"
        )
        claim = object()
        with lock:
            previous = state.get(session_key) or {}
            reason = ""
            if min_interval > 0:
                if (previous.get("fingerprint") == fingerprint
                    and time.time() - float(previous.get("sent_at") or 0.0) < min_interval):
                    reason = "dedupe_interval"
                elif fingerprint in pending.get(session_key, {}):
                    reason = "delivery_in_progress"
            if reason:
                return {
                    "ok": True, "status": "skipped", "reason": reason,
                    "emotion": emotion, "min_interval_seconds": min_interval,
                }
            pending.setdefault(session_key, {})[fingerprint] = claim
            latest[session_key] = claim
        try:
            result = sender()
            result["status"] = "sent" if result.get("ok") else "send_failed"
            result["emotion"] = emotion
            if result.get("ok"):
                with lock:
                    # A slower previous request cannot replace newer delivery state.
                    if latest.get(session_key) is claim:
                        state[session_key] = {
                            "emotion": emotion, "fingerprint": fingerprint,
                            "sent_at": time.time(),
                        }
            return result
        finally:
            with lock:
                inflight = pending.get(session_key, {})
                if inflight.get(fingerprint) is claim:
                    inflight.pop(fingerprint, None)
                if not inflight:
                    pending.pop(session_key, None)
                if latest.get(session_key) is claim:
                    latest.pop(session_key, None)

    def handle_mface_config_command(
        self,
        context: QQMessageContext,
        event: dict[str, Any],
        *,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if command is not None:
            emotion_key = str(command.get("emotion") or "")
        else:
            emotion_key = self.gateway.parse_mface_config_command(context.clean_message)
        if emotion_key is None:
            return None

        master_qq = self.gateway._safe_int(getattr(config, "MASTER_QQ", 0))
        if master_qq and int(context.user_id or 0) != master_qq:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "这个命令只允许主人使用。",
                "character_pack_id": context.character_pack_id,
            }

        mfaces = self.gateway.extract_mface_payloads(context)
        if not mfaces:
            return {
                "handled": True,
                "ok": False,
                "status": "missing_mface",
                "reply": "这条消息里没抓到 NapCat mface 字段。请把 /表情包 happy 和要绑定的 QQ 表情包一起发，或转发一条包含表情包的消息。",
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

    @staticmethod
    def extract_mface_payloads(context: QQMessageContext) -> list[dict[str, Any]]:
        """Read trusted mface fields from the already-normalized message chain."""

        payloads: list[dict[str, Any]] = []
        for part in context.message_chain.parts:
            segment_type = str(part.segment_type or "").strip().lower()
            if segment_type not in {"mface", "market_face", "marketface", "image"}:
                continue
            data = part.data_dict()
            mface = _normalize_mface_payload(data)
            if mface:
                payloads.append(mface)
        return payloads

    def send_emotion_mface(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        qq_delivery_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a configured QQ mface based on final_output.emotion."""
        if context.is_group and not self.gateway.is_group_emotion_enabled(context.group_id):
            return {"ok": True, "status": "skipped", "reason": "group_emotion_disabled"}
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
        return self._send_with_dedupe(
            context=context, emotion=emotion, fingerprint=fingerprint, min_interval=min_interval,
            state=self.emotion_mface_state, lock=self._emotion_mface_lock,
            pending=self._pending_mfaces, latest=self._latest_mfaces,
            sender=lambda: self.gateway.send_mface(context, mface=mface),
        )

    def send_emotion_image(
        self,
        context: QQMessageContext,
        frame: dict[str, Any],
        *,
        image: dict[str, Any] | None = None,
        min_interval_seconds: int = 20,
    ) -> dict[str, Any]:
        """Send the current character pack emotion image as a QQ image fallback."""
        if context.is_group and not self.gateway.is_group_emotion_enabled(context.group_id):
            return {"ok": True, "status": "skipped", "reason": "group_emotion_disabled"}
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

        def send_image() -> dict[str, Any]:
            result = self.gateway.send_image(
                context, image_path=image_path, name=str(image.get("name") or image.get("emotion") or emotion),
            )
            result["image_emotion"] = str(image.get("emotion") or "")
            return result

        return self._send_with_dedupe(
            context=context, emotion=emotion, fingerprint=fingerprint, min_interval=min_interval,
            state=self.emotion_image_state, lock=self._emotion_image_lock,
            pending=self._pending_images, latest=self._latest_images,
            sender=send_image,
        )

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
