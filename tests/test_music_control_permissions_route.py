"""Route tests for GET/POST /capabilities/music/control_permissions."""

from __future__ import annotations

import sqlite3
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01.routes.capabilities import build_capabilities_router


def _resolve_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or "session")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


class _InMemoryStore:
    def __init__(self) -> None:
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    @contextmanager
    def _connect(self):
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        self._connection.close()


class MusicControlPermissionsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _InMemoryStore()
        self.engine = SimpleNamespace(store=self.store, tool_handlers={})
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=self.engine,
                resolve_identity_from_query=_resolve_query,
            )
        )
        self.client = TestClient(app)
        self.addCleanup(self.store.close)

    def _get(self, *, query: str = "user_id=desktop&real_user_id=master"):
        return self.client.get(
            f"/capabilities/music/control_permissions?{query}"
        )

    def _post(self, body: dict, *, query: str = "user_id=desktop&real_user_id=master"):
        return self.client.post(
            f"/capabilities/music/control_permissions?{query}",
            json=body,
        )

    # ------------------------------------------------------------------
    # GET — default state
    # ------------------------------------------------------------------

    def test_get_default_all_enabled(self) -> None:
        response = self._get()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ready")
        controls = payload["controls"]
        self.assertTrue(controls["pause"])
        self.assertTrue(controls["next"])
        self.assertTrue(controls["prev"])
        self.assertTrue(controls["recommend"])

    # ------------------------------------------------------------------
    # POST then GET roundtrip
    # ------------------------------------------------------------------

    def test_post_disable_next_then_get_reflects(self) -> None:
        post_resp = self._post({"controls": {"next": False}})
        self.assertEqual(post_resp.status_code, 200)
        post_payload = post_resp.json()
        self.assertTrue(post_payload["ok"])
        self.assertFalse(post_payload["controls"]["next"])

        get_payload = self._get().json()
        self.assertFalse(get_payload["controls"]["next"])

    # ------------------------------------------------------------------
    # Partial update leaves others untouched
    # ------------------------------------------------------------------

    def test_partial_post_only_changes_given_key(self) -> None:
        self._post({"controls": {"next": False}})
        payload = self._get().json()
        self.assertFalse(payload["controls"]["next"])
        self.assertTrue(payload["controls"]["pause"])
        self.assertTrue(payload["controls"]["prev"])
        self.assertTrue(payload["controls"]["recommend"])

    # ------------------------------------------------------------------
    # Unknown key is silently ignored
    # ------------------------------------------------------------------

    def test_post_unknown_key_silently_ignored(self) -> None:
        response = self._post({"controls": {"wipe_disk": True}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        # Verify nothing was written for the unknown key
        get_payload = self._get().json()
        self.assertNotIn("wipe_disk", get_payload["controls"])

    # ------------------------------------------------------------------
    # Non-bool value coercion
    # ------------------------------------------------------------------

    def test_post_truthy_string_treated_as_true(self) -> None:
        # First disable next
        self._post({"controls": {"next": False}})
        # Re-enable with a truthy string
        resp = self._post({"controls": {"next": "yes"}})
        self.assertTrue(resp.json()["controls"]["next"])

    # ------------------------------------------------------------------
    # Profile isolation
    # ------------------------------------------------------------------

    def test_profile_isolation(self) -> None:
        self._post(
            {"controls": {"next": False}},
            query="user_id=desktop&real_user_id=alice",
        )
        bob_payload = self._get(query="user_id=desktop&real_user_id=bob").json()
        self.assertTrue(bob_payload["controls"]["next"])

    def test_get_without_connect_reports_unavailable(self) -> None:
        engine = SimpleNamespace(store=object(), tool_handlers={})
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=engine,
                resolve_identity_from_query=_resolve_query,
            )
        )
        response = TestClient(app).get(
            "/capabilities/music/control_permissions?user_id=desktop&real_user_id=master"
        )
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "store_unavailable")

    def test_post_without_connect_does_not_fake_success(self) -> None:
        engine = SimpleNamespace(store=object(), tool_handlers={})
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=engine,
                resolve_identity_from_query=_resolve_query,
            )
        )
        response = TestClient(app).post(
            "/capabilities/music/control_permissions?user_id=desktop&real_user_id=master",
            json={"controls": {"next": False}},
        )
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "store_unavailable")


if __name__ == "__main__":
    unittest.main()
