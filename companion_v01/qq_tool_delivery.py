"""Request-scoped QQ delivery port used by model tool handlers.

The reasoning engine knows only this narrow port.  QQ target reconstruction and
NapCat transport stay in the channel adapter, while tool results can contain the
real transport outcome before the model chooses its next step.
"""

from __future__ import annotations

from typing import Any

from .qq_music_audio import resolve_public_audio_url


class QQToolDeliveryPort:
    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def _context(self, request_context: dict[str, Any]) -> Any | None:
        delivery = request_context.get("qq_delivery_context") if isinstance(request_context, dict) else None
        if not isinstance(delivery, dict):
            return None
        return self._gateway.context_from_delivery_context(delivery)

    def send_music_card(
        self,
        *,
        request_context: dict[str, Any],
        platform: str,
        track_id: str,
    ) -> dict[str, Any]:
        context = self._context(request_context)
        if context is None:
            return {"ok": False, "status": "unavailable", "reason": "qq_delivery_context_missing"}
        result = dict(self._gateway.send_music_card(context, platform=platform, track_id=track_id))
        result.setdefault("status", "sent" if result.get("ok") else "failed")
        result["delivery_surface"] = "music_card"
        return result

    def send_audio_url(
        self,
        *,
        request_context: dict[str, Any],
        audio_url: str,
        name: str = "",
    ) -> dict[str, Any]:
        context = self._context(request_context)
        if context is None:
            return {"ok": False, "status": "unavailable", "reason": "qq_delivery_context_missing"}
        resolution = resolve_public_audio_url(audio_url)
        if not bool(resolution.get("ok")):
            source_status = str(resolution.get("status") or "unavailable").strip() or "unavailable"
            return {
                "ok": False,
                "status": "unavailable",
                "reason": f"public_audio_{source_status}",
                "source_status": source_status,
                "delivery_surface": "voice",
            }
        result = dict(
            self._gateway.send_voice_url(
                context,
                audio_url=str(resolution.get("url") or ""),
                name=name,
            )
        )
        result.setdefault("status", "sent" if result.get("ok") else "failed")
        result["delivery_surface"] = "voice"
        result["source_status"] = "verified_public_audio"
        return result

    def send_audio_file(
        self,
        *,
        request_context: dict[str, Any],
        absolute_path: str,
        name: str,
    ) -> dict[str, Any]:
        context = self._context(request_context)
        if context is None:
            return {"ok": False, "status": "unavailable", "reason": "qq_delivery_context_missing"}
        result = dict(self._gateway.send_voice(context, audio_path=absolute_path, name=name, claim_reply=False))
        result.setdefault("status", "sent" if result.get("ok") else "failed")
        result["delivery_surface"] = "voice"
        return result
