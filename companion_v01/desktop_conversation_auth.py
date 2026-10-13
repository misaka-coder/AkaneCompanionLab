"""Short-lived desktop credentials for ordinary chat, separate from admin writes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .deployment_security import AdminWriteAuth, AuthorizationDecision

DESKTOP_CHAT_HEADER = "x-akane-desktop-chat-token"
DESKTOP_CHAT_TTL_SECONDS = 600
DESKTOP_CHAT_PROTOCOL_PREFIX = "akane.desktop-chat."


def is_desktop_conversation_path(path: str, method: str) -> bool:
    """Only owner-facing conversation routes can consume this credential."""
    if method == "WEBSOCKET" and path == "/voice/realtime":
        return True
    if method == "POST" and path in {
        "/think", "/think_once", "/think/steer", "/think/stop", "/think/presence",
        "/desktop-pet/care/snapshot", "/desktop-pet/care/action",
        "/desktop-pet/workspace/action", "/desktop-pet/workspace/import-file",
        "/desktop-pet/attachments/audio", "/desktop-pet/music-timeline/prepare",
        "/desktop-pet/vision/clip", "/desktop-pet/vision/reaction", "/desktop-pet/vision/clear",
    }:
        return True
    return method == "GET" and (
        path in {"/desktop-pet/workspace/summary", "/desktop-pet/vision/latest"}
        or any(path.startswith(prefix) and path.endswith("/content") for prefix in (
            "/desktop-pet/attachments/", "/desktop-pet/generated/",
            "/desktop-pet/workspace/attachments/", "/desktop-pet/workspace/generated/",
        ))
    )


class DesktopConversationAuth:
    def __init__(self, admin: AdminWriteAuth, *, route_prefix: str, now: Callable[[], float] = time.time) -> None:
        self.admin = admin
        self.route_prefix = route_prefix.rstrip("/")
        self._now = now
        self._key = secrets.token_bytes(32)

    def issue(self) -> dict[str, Any]:
        expires = int(self._now()) + DESKTOP_CHAT_TTL_SECONDS
        payload = f"{expires}.{secrets.token_urlsafe(18)}"
        signature = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
        return {"ok": True, "token": f"{payload}.{signature}", "expiresAt": expires * 1000}

    def authorize(self, request: Any) -> AuthorizationDecision:
        admin = self.admin.authorize(request)
        if admin.ok:
            return admin
        supplied = str(request.headers.get(DESKTOP_CHAT_HEADER) or "")
        websocket = getattr(request, "scope", {}).get("type") == "websocket"
        if websocket and not supplied:
            offered = str(request.headers.get("sec-websocket-protocol") or "").split(",")
            supplied = next((p.strip()[len(DESKTOP_CHAT_PROTOCOL_PREFIX):] for p in offered
                             if p.strip().startswith(DESKTOP_CHAT_PROTOCOL_PREFIX)), "")
        if not supplied:
            return admin
        path = str(request.url.path)
        prefix = self.route_prefix + "/"
        if path.startswith(prefix):
            local_path = "/" + path[len(prefix):]
        elif not path.startswith("/api/bots/"):
            # Only the default Bot exposes these legacy URL aliases.
            local_path = path
        else:
            return AuthorizationDecision(False, 403, "desktop_chat_scope_mismatch")
        method = "WEBSOCKET" if websocket else request.method
        if not is_desktop_conversation_path(local_path, method):
            return AuthorizationDecision(False, 403, "desktop_chat_scope_mismatch")
        try:
            payload, signature = supplied.rsplit(".", 1)
            expires = int(payload.split(".", 1)[0])
            if len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
                raise ValueError("invalid_signature")
        except (ValueError, TypeError):
            return AuthorizationDecision(False, 401, "desktop_chat_auth_required")
        expected = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
        if expires <= self._now() or not hmac.compare_digest(signature, expected):
            return AuthorizationDecision(False, 401, "desktop_chat_auth_required")
        return AuthorizationDecision(True)


def build_desktop_conversation_auth_router(auth: DesktopConversationAuth) -> APIRouter:
    router = APIRouter()

    @router.post("/control-center/desktop-chat-session")
    async def create_desktop_chat_session(request: Request):
        decision = auth.admin.authorize(request)
        if not decision.ok:
            return JSONResponse({"ok": False, "reason": decision.reason}, status_code=decision.status_code,
                                headers={"Cache-Control": "no-store"})
        return JSONResponse(auth.issue(), headers={"Cache-Control": "no-store"})

    return router
