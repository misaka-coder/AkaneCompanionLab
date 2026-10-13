"""Deliver a rendered card or its text fallback, preserving the real receipt."""

from __future__ import annotations

import asyncio
from typing import Any


def card_failure_reason(exc: Exception) -> str:
    public_reasons = {
        "chinese_font_unavailable", "help_background_unavailable", "help_card_too_tall",
        "care_background_unavailable", "care_snapshot_unavailable",
        "commerce_background_unavailable", "commerce_snapshot_unavailable",
        "commerce_layout_too_tall", "commerce_page_out_of_range",
    }
    return str(exc) if str(exc) in public_reasons else type(exc).__name__


class QQCardResultDelivery:
    def __init__(self, *, qq_gateway, send_reply):
        self.qq_gateway = qq_gateway
        self.send_reply = send_reply

    async def deliver_card(
        self, context: Any, *, image_path: str | None = None, title: str = "",
        fallback: str, failure_reason: str = "",
    ) -> tuple[dict[str, Any], str, str]:
        if image_path is not None:
            try:
                result = await asyncio.to_thread(
                    self.qq_gateway.send_image, context, image_path=image_path, name=title,
                )
                if result.get("ok"):
                    return result, "image", ""
                failure_reason = str(result.get("reason") or "image_send_failed")
            except Exception as exc:
                failure_reason = card_failure_reason(exc)
        return await self.send_reply(context, fallback), "text_fallback", failure_reason
