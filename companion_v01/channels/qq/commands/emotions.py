"""Capture a QQ mface binding command; automatic emotion delivery is separate."""
from __future__ import annotations

import json
from typing import Any

import config
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.delivery.emotions import _normalize_mface_payload


class QQEmotionCommands:
    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

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
