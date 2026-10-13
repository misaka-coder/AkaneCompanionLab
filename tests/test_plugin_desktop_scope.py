"""Desktop program calls share the model tool's client conversation scope."""
from types import SimpleNamespace
import unittest

import httpx
from fastapi import FastAPI
from capcore import CapabilityResult, InvocationContext
from akane_plugin.contracts import PluginInvocationContext

from companion_v01.routes.plugins import build_plugins_router


class PluginDesktopScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_calls_preserve_client_identity_when_memory_is_shared(self):
        contexts, stamps = [], []

        async def invoke(capability_id, args, *, context):
            contexts.append(context)
            return CapabilityResult(is_error=False, content={"ready": True})

        policy = SimpleNamespace(
            stamp_desktop=lambda payload, **kwargs: stamps.append(kwargs),
            resolve=lambda payload, **kwargs: SimpleNamespace(memory_identity=("master", "master")),
        )
        engine = SimpleNamespace(owner_private_policy=policy,
                                 _resolve_payload_character_pack_id=lambda payload: payload["character_pack_id"])
        app = FastAPI()
        app.include_router(build_plugins_router(
            extension_management_service=SimpleNamespace(invoke_capability=invoke), engine=engine,
            resolve_identity_from_query=lambda req: (req.query_params["user_id"], req.query_params["real_user_id"]),
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            plain = await client.post("/admin/plugins/capabilities/example.invoke/invoke", json={})
            scoped = await client.post("/admin/plugins/capabilities/example.invoke/invoke", params={
                "user_id": "desktop-random-id", "real_user_id": "master", "character_pack_id": "cecilia",
                "client_mode": "desktop_pet",
            }, json={})
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(contexts[0], InvocationContext(client_mode="plugin_admin"))
        self.assertIsInstance(contexts[1], PluginInvocationContext)
        self.assertEqual((contexts[1].profile_user_id, contexts[1].session_id, contexts[1].character_pack_id),
                         ("master", "desktop-random-id", "cecilia"))
        self.assertEqual(stamps, [])

    async def test_non_owner_scope_is_preserved_without_policy(self):
        contexts = []

        async def invoke(capability_id, args, *, context):
            contexts.append(context)
            return CapabilityResult(is_error=False, content={})

        app = FastAPI()
        app.include_router(build_plugins_router(
            extension_management_service=SimpleNamespace(invoke_capability=invoke),
            resolve_identity_from_query=lambda req: (req.query_params["user_id"], req.query_params["real_user_id"]),
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/admin/plugins/capabilities/example.invoke/invoke", params={
                "user_id": "visitor-session", "real_user_id": "visitor", "character_pack_id": "reimu",
            }, json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((contexts[0].profile_user_id, contexts[0].session_id, contexts[0].character_pack_id),
                         ("visitor", "visitor-session", "reimu"))
