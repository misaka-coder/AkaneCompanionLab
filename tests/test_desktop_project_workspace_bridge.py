from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.project_workspace import ProjectWorkspaceService
from companion_v01.routes.desktop_pet import build_desktop_pet_router
from companion_v01.store import MemoryStore


class _Metrics:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.rows.append((name, ok))


class DesktopProjectWorkspaceBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.external = root / "中文 project"
        self.external.mkdir()
        service = ProjectWorkspaceService(
            store=MemoryStore(root / "data"),
            execution_workspace_root=root / "execution",
        )
        self.metrics = _Metrics()
        app = FastAPI()
        app.include_router(
            build_desktop_pet_router(
                engine=SimpleNamespace(_get_project_workspace_service=lambda: service),
                config_module=SimpleNamespace(DESKTOP_PET_AUDIO_UPLOAD_MAX_BYTES=1024),
                runtime_metrics=self.metrics,
                log_event=lambda *_args, **_kwargs: None,
                resolve_identity_from_query=lambda request: ("desktop", "master"),
                resolve_identity_from_payload=lambda payload: (
                    str(payload.get("session_id") or "desktop"),
                    str(payload.get("real_user_id") or "master"),
                ),
                admin_auth=AdminWriteAuth(
                    token="bridge-secret",
                    require_token=True,
                    allow_loopback_without_token=False,
                ),
            )
        )
        self.client = TestClient(app)

    def test_bind_requires_admin_and_never_returns_host_path(self) -> None:
        request = {
            "action": "bind",
            "session_id": "desktop",
            "real_user_id": "master",
            "host_directory": str(self.external),
        }
        denied = self.client.post("/desktop-pet/project-workspaces/action", json=request)
        accepted = self.client.post(
            "/desktop-pet/project-workspaces/action",
            json=request,
            headers={"Authorization": "Bearer bridge-secret"},
        )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["reason"], "admin_auth_required")
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["result"]["selected"])
        self.assertNotIn(str(self.external), accepted.text)
        self.assertNotIn("host_root_path", accepted.text)

    def test_list_create_select_archive_share_one_desktop_authority(self) -> None:
        headers = {"Authorization": "Bearer bridge-secret"}
        created = self.client.post(
            "/desktop-pet/project-workspaces/action",
            json={"action": "create", "session_id": "a", "real_user_id": "master", "display_name": "Demo"},
            headers=headers,
        ).json()["result"]
        listed = self.client.post(
            "/desktop-pet/project-workspaces/action",
            json={"action": "list", "session_id": "b", "real_user_id": "master"},
            headers=headers,
        ).json()["result"]
        archived = self.client.post(
            "/desktop-pet/project-workspaces/action",
            json={
                "action": "archive",
                "session_id": "b",
                "real_user_id": "master",
                "workspace_id": created["workspace_id"],
            },
            headers=headers,
        ).json()["result"]

        self.assertEqual(listed["selected_workspace_id"], created["workspace_id"])
        self.assertEqual(archived["state"], "archived")


if __name__ == "__main__":
    unittest.main()
