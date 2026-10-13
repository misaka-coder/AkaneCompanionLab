"""Prepare command documents, render them, then delegate delivery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from companion_v01.channels.qq.cards.care import build_qq_care_card, render_qq_care_card
from companion_v01.channels.qq.cards.commerce import (
    build_qq_commerce_card,
    build_qq_purchase_card,
    render_qq_commerce_card,
    render_qq_purchase_card,
)
from companion_v01.channels.qq.cards.help import render_qq_help_card
from companion_v01.channels.qq.delivery.card_results import QQCardResultDelivery, card_failure_reason


class QQCommandCards:
    def __init__(self, *, engine, config_module, qq_gateway, send_reply):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.send_reply = send_reply
        self.delivery = QQCardResultDelivery(qq_gateway=qq_gateway, send_reply=send_reply)

    async def render_and_deliver(self, context: Any, document: Any, *, renderer: Any,
                                  cache_name: str, title: str, fallback: str) -> tuple[dict[str, Any], str, str]:
        try:
            cache_dir = Path(str(getattr(self.config_module, "DATA_DIR", "users_data"))) / cache_name
            path = await asyncio.to_thread(renderer, document, cache_dir)
        except Exception as exc:
            return await self.delivery.deliver_card(context, fallback=fallback, failure_reason=card_failure_reason(exc))
        return await self.delivery.deliver_card(context, image_path=str(path), title=title, fallback=fallback)

    async def present_help(self, context: Any, document: Any, *, text_only: bool) -> tuple[dict[str, Any], str, str]:
        if text_only:
            return await self.send_reply(context, document.as_text()), "text", ""
        return await self.render_and_deliver(context, document, renderer=render_qq_help_card,
                                        cache_name="qq_help_cache", title=document.title,
                                        fallback="帮助图片暂时不可用，先发文字版。\n\n" + document.as_text())

    async def present_care(self, context: Any, snapshot: Mapping[str, Any], reply: str,
                                *, user_label: str) -> tuple[dict[str, Any], str, str]:
        fallback = "状态图暂时不可用，先发文字版。\n\n" + reply
        try:
            identity = await asyncio.to_thread(
                self.qq_gateway._resolve_pack_identity, context.character_pack_id,
                character_resource_service=getattr(self.engine, "desktop_pet_character_resources", None),
            )
            character_label = (identity.get("assistant_name") or
                               ("塞西莉亚" if context.character_pack_id == "cecilia" else "伙伴"))
            document = build_qq_care_card(
                snapshot, user_label=user_label or f"QQ {context.user_id}", character_label=character_label,
                user_scope=f"{context.character_pack_id}|{context.session_id}|qq:{context.user_id}",
                character_pack_id=str(context.character_pack_id or ""),
            )
        except Exception as exc:
            return await self.delivery.deliver_card(context, fallback=fallback, failure_reason=card_failure_reason(exc))
        return await self.render_and_deliver(context, document, renderer=render_qq_care_card,
                                        cache_name="qq_care_cache", title="专属状态",
                                        fallback=fallback)

    async def present_commerce(self, context: Any, payload: Mapping[str, Any], reply: str,
                                   *, user_label: str) -> tuple[dict[str, Any], str, str]:
        purchase = payload.get("kind") == "purchase"
        builder = build_qq_purchase_card if purchase else build_qq_commerce_card
        renderer = render_qq_purchase_card if purchase else render_qq_commerce_card
        try:
            identity = await asyncio.to_thread(
                self.qq_gateway._resolve_pack_identity, context.character_pack_id,
                character_resource_service=getattr(self.engine, "desktop_pet_character_resources", None),
            )
            character_label = (identity.get("assistant_name") or
                               ("塞西莉亚" if context.character_pack_id == "cecilia" else "伙伴"))
            document = builder(
                payload, user_label=user_label or f"QQ {context.user_id}", character_label=character_label,
                user_scope=f"{context.character_pack_id}|{context.session_id}|qq:{context.user_id}",
                character_pack_id=str(context.character_pack_id or ""),
                resources=getattr(self.engine, "desktop_pet_character_resources", None),
            )
        except Exception as exc:
            label = "购买回执" if purchase else "商店或背包图"
            return await self.delivery.deliver_card(
                context, fallback=label + "暂时不可用，先发文字版。\n\n" + reply, failure_reason=card_failure_reason(exc),
            )
        if payload.get("text_only"):
            return await self.send_reply(context, document.as_text()), "text", ""
        return await self.render_and_deliver(context, document, renderer=renderer,
                                        cache_name="qq_commerce_cache", title=document.title,
                                        fallback=f"{document.title}图暂时不可用，先发文字版。\n\n" + document.as_text())
