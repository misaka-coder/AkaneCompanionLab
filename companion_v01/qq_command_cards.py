"""Render QQ command cards and preserve the existing text fallback policy."""

from __future__ import annotations

from .qq_care_cards import build_qq_care_card
from .qq_care_cards import render_qq_care_card
from .qq_commerce_cards import build_qq_commerce_card
from .qq_commerce_cards import render_qq_commerce_card
from .qq_commerce_cards import build_qq_purchase_card, render_qq_purchase_card
from .qq_help_cards import render_qq_help_card
from pathlib import Path
from typing import Any
from typing import Mapping
import asyncio


class QQCommandCards:
    def __init__(self, *, engine, config_module, qq_gateway, send_reply):
        self.engine = engine
        self.config_module = config_module
        self.qq_gateway = qq_gateway
        self.send_reply = send_reply

    def _card_failure(self, exc: Exception) -> str:
        public_reasons = {"chinese_font_unavailable", "help_background_unavailable", "help_card_too_tall",
                          "care_background_unavailable", "care_snapshot_unavailable",
                          "commerce_background_unavailable", "commerce_snapshot_unavailable",
                          "commerce_layout_too_tall", "commerce_page_out_of_range"}
        return str(exc) if str(exc) in public_reasons else type(exc).__name__

    async def _send_rendered_card(self, context: Any, document: Any, *, renderer: Any,
                                  cache_name: str, title: str, fallback: str) -> tuple[dict[str, Any], str, str]:
        image_failure = ""
        try:
            cache_dir = Path(str(getattr(self.config_module, "DATA_DIR", "users_data"))) / cache_name
            path = await asyncio.to_thread(renderer, document, cache_dir)
            send_result = await asyncio.to_thread(self.qq_gateway.send_image, context, image_path=str(path), name=title)
            if send_result.get("ok"):
                return send_result, "image", ""
            image_failure = str(send_result.get("reason") or "image_send_failed")
        except Exception as exc:
            image_failure = self._card_failure(exc)
        return await self.send_reply(context, fallback), "text_fallback", image_failure

    async def _send_help_document(self, context: Any, document: Any, *, text_only: bool) -> tuple[dict[str, Any], str, str]:
        if text_only:
            return await self.send_reply(context, document.as_text()), "text", ""
        return await self._send_rendered_card(context, document, renderer=render_qq_help_card,
                                        cache_name="qq_help_cache", title=document.title,
                                        fallback="帮助图片暂时不可用，先发文字版。\n\n" + document.as_text())

    async def _send_care_status(self, context: Any, snapshot: Mapping[str, Any], reply: str,
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
            return await self.send_reply(context, fallback), "text_fallback", self._card_failure(exc)
        return await self._send_rendered_card(context, document, renderer=render_qq_care_card,
                                        cache_name="qq_care_cache", title="专属状态",
                                        fallback=fallback)

    async def _send_commerce_status(self, context: Any, payload: Mapping[str, Any], reply: str,
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
            return (await self.send_reply(context, label + "暂时不可用，先发文字版。\n\n" + reply),
                    "text_fallback", self._card_failure(exc))
        if payload.get("text_only"):
            return await self.send_reply(context, document.as_text()), "text", ""
        return await self._send_rendered_card(context, document, renderer=renderer,
                                        cache_name="qq_commerce_cache", title=document.title,
                                        fallback=f"{document.title}图暂时不可用，先发文字版。\n\n" + document.as_text())
