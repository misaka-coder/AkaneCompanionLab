import unittest
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.desktop_conversation_auth import (
    DESKTOP_CHAT_HEADER, DESKTOP_CHAT_PROTOCOL_PREFIX, DesktopConversationAuth,
    build_desktop_conversation_auth_router,
)


class DesktopConversationAuthTests(unittest.TestCase):
    def setUp(self):
        self.clock = 1000
        self.admin = AdminWriteAuth(token="admin-test", require_token=True, allow_loopback_without_token=False)
        self.auth = DesktopConversationAuth(self.admin, route_prefix="/api/bots/one", now=lambda: self.clock)
        self.app = FastAPI()
        self.app.include_router(build_desktop_conversation_auth_router(self.auth), prefix="/api/bots/one")
        self.client = TestClient(self.app)

    def request(self, path="/api/bots/one/think", *, method="POST", token="", websocket=False):
        return SimpleNamespace(url=SimpleNamespace(path=path), method=method,
                               scope={"type": "websocket" if websocket else "http"},
                               headers={DESKTOP_CHAT_HEADER: token})

    def test_only_admin_can_issue_and_credential_is_not_admin_token(self):
        endpoint = "/api/bots/one/control-center/desktop-chat-session"
        self.assertEqual(self.client.post(endpoint).status_code, 401)
        result = self.client.post(endpoint, headers={"Authorization": "Bearer admin-test"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        token = result.json()["token"]
        self.assertNotIn("admin-test", token)
        self.assertFalse(self.admin.authorize(self.request(token=token)).ok)
        self.assertTrue(self.auth.authorize(self.request(token=token)).ok)
        self.assertEqual(self.client.post(endpoint, headers={DESKTOP_CHAT_HEADER: token}).status_code, 401)

    def test_chat_stream_and_materials_allowed_but_management_and_other_bot_denied(self):
        token = self.auth.issue()["token"]
        for method, path in (("POST", "/think"), ("POST", "/think/presence"),
                             ("POST", "/desktop-pet/care/action"), ("GET", "/desktop-pet/workspace/summary"),
                             ("GET", "/desktop-pet/attachments/attachment-1/content")):
            with self.subTest(path=path):
                self.assertTrue(self.auth.authorize(self.request("/api/bots/one" + path, method=method, token=token)).ok)
        for path in ("/api/bots/two/think", "/api/bots/one/admin/reset", "/api/bots/one/desktop-pet/care/shop",
                     "/api/bots/one/desktop-pet/project-workspaces/action", "/api/bots/one/control-center/settings"):
            with self.subTest(path=path):
                self.assertFalse(self.auth.authorize(self.request(path, token=token)).ok)
        self.assertFalse(self.auth.authorize(self.request(method="DELETE", token=token)).ok)
        self.assertTrue(self.auth.authorize(self.request("/think", token=token)).ok)

    def test_expiry_tampering_restart_and_invalid_unicode_fail_closed(self):
        token = self.auth.issue()["token"]
        restarted = DesktopConversationAuth(self.admin, route_prefix="/api/bots/one", now=lambda: self.clock)
        self.assertFalse(restarted.authorize(self.request(token=token)).ok)
        for bad in ("invalid", token + "x", token[:-64] + "中" * 64, "100000000.invalid.bad"):
            self.assertFalse(self.auth.authorize(self.request(token=bad)).ok)
        self.clock += 600
        self.assertFalse(self.auth.authorize(self.request(token=token)).ok)
        self.assertTrue(self.auth.authorize(self.request(token=self.auth.issue()["token"])).ok)

    def test_voice_uses_protocol_header_without_secret_in_url(self):
        token = self.auth.issue()["token"]
        request = self.request("/api/bots/one/voice/realtime", websocket=True)
        request.headers = {"sec-websocket-protocol": DESKTOP_CHAT_PROTOCOL_PREFIX + token}
        self.assertTrue(self.auth.authorize(request).ok)
        request.url.path = "/api/bots/two/voice/realtime"
        self.assertFalse(self.auth.authorize(request).ok)

    def test_real_voice_route_accepts_scoped_protocol_and_rejects_missing_auth(self):
        from starlette.websockets import WebSocketDisconnect
        from companion_v01.routes.voice import build_voice_router
        self.app.include_router(build_voice_router(engine=SimpleNamespace(owner_private_policy=SimpleNamespace(enabled=True)),
                                config_module=SimpleNamespace(), tts_client=None, runtime_metrics=SimpleNamespace(),
                                log_event=lambda *a, **kw: None, admin_auth=self.auth), prefix="/api/bots/one")
        with self.assertRaises(WebSocketDisconnect) as missing:
            with self.client.websocket_connect("/api/bots/one/voice/realtime"):
                pass
        self.assertEqual(missing.exception.code, 1008)
        protocol = DESKTOP_CHAT_PROTOCOL_PREFIX + self.auth.issue()["token"]
        with self.client.websocket_connect("/api/bots/one/voice/realtime", subprotocols=[protocol]) as socket:
            self.assertEqual(socket.accepted_subprotocol, protocol)
            socket.send_json({"type": "client.open"})
            self.assertEqual(socket.receive_json()["type"], "server.failed")

    def test_streaming_response_not_buffered_by_auth_router(self):
        from fastapi.responses import StreamingResponse
        @self.app.post("/api/bots/one/think")
        async def stream(request: Request):
            decision = self.auth.authorize(request)
            if not decision.ok:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": decision.reason}, status_code=decision.status_code)
            async def events():
                yield '{"type":"speech_segment","text":"hello"}\n'
                yield '{"type":"stream_end"}\n'
            return StreamingResponse(events(), media_type="application/x-ndjson")
        self.assertEqual(self.client.post("/api/bots/one/think").status_code, 401)
        with self.client.stream("POST", "/api/bots/one/think", headers={DESKTOP_CHAT_HEADER: self.auth.issue()["token"]}) as reply:
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(len(list(reply.iter_lines())), 2)


if __name__ == "__main__":
    unittest.main()
