"""Private scene reads use the same host authorization as scene writes."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.routes.scene import build_scene_router
from companion_v01.scene.contracts import Snapshot, StoryCatalogResponse


class SceneRouteAuthTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "profile_user_id": "master",
            "session_id": "scene-auth-test",
            "character_pack_id": "akane_v1",
        }
        self.snapshot = Snapshot.model_validate({
            "protocol": "scene_v1", "identity": self.identity, "character_name": "Akane",
            "room": {"revision": 0, "outfit_id": "default", "background_id": "", "music_id": "", "position": "center"},
            "care": {"enabled": True, "reason": "", "coins": 20, "hunger": 60, "energy": 80, "affection": 0, "inventory": {}},
            "shop": [], "catalog": {"revision": "test", "backgrounds": [], "outfits": [], "music": []},
        })
        self.service = SimpleNamespace(
            snapshot=Mock(return_value=self.snapshot),
            story=SimpleNamespace(catalog=Mock(return_value=StoryCatalogResponse(stories=[]))),
        )

    def client(self, auth, **kwargs):
        app = FastAPI()
        app.include_router(build_scene_router(service=self.service, admin_auth=auth), prefix="/api/bots/main")
        return TestClient(app, **kwargs)

    def test_private_reads_require_auth_before_accessing_the_requested_identity(self):
        auth = AdminWriteAuth(token="scene-test-token", require_token=True, allow_loopback_without_token=False)
        with self.client(auth) as client:
            for path in ("/scene/snapshot", "/scene/story/catalog"):
                for headers in ({}, {"Authorization": "Bearer wrong-token"}):
                    with self.subTest(path=path, headers_present=bool(headers)):
                        response = client.post("/api/bots/main" + path, json=self.identity, headers=headers)
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.json(), {"detail": "admin_auth_required"})
            self.service.snapshot.assert_not_called()
            self.service.story.catalog.assert_not_called()

            headers = {"Authorization": "Bearer scene-test-token"}
            response = client.post("/api/bots/main/scene/snapshot", json=self.identity, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["care"]["coins"], 20)
            response = client.post("/api/bots/main/scene/story/catalog", json=self.identity, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"stories": []})
            self.service.snapshot.assert_called_once()
            self.service.story.catalog.assert_called_once()

    def test_default_loopback_host_remains_usable_without_a_token(self):
        with self.client(AdminWriteAuth.local_compatibility(), client=("127.0.0.1", 50000)) as client:
            for path in ("/scene/snapshot", "/scene/story/catalog"):
                with self.subTest(path=path):
                    self.assertEqual(client.post("/api/bots/main" + path, json=self.identity).status_code, 200)

    def test_loopback_compatibility_does_not_allow_external_reads(self):
        with self.client(AdminWriteAuth.local_compatibility(), client=("198.51.100.10", 50000)) as client:
            for path in ("/scene/snapshot", "/scene/story/catalog"):
                with self.subTest(path=path):
                    response = client.post("/api/bots/main" + path, json=self.identity)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json(), {"detail": "local_request_required"})
        self.service.snapshot.assert_not_called()
        self.service.story.catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
