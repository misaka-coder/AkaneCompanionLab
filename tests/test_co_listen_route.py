"""Route tests for POST /capabilities/music/co_listen_summary."""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01 import co_listen_store
from companion_v01.routes.capabilities import build_capabilities_router


def _resolve_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or "session")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


class _InMemoryStore:
    def __init__(self) -> None:
        # Route handlers run through `asyncio.to_thread`, so the connection
        # must be shareable across worker threads.
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = __import__("threading").Lock()

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


def _seed(store: _InMemoryStore, **kwargs) -> None:
    defaults = dict(
        profile_user_id="master",
        identity_key="晴天|周杰伦|叶惠美",
        title_normalized="晴天",
        artist_normalized="周杰伦",
        album_hint="叶惠美",
        display_title="晴天",
        display_artist="周杰伦",
        source="qq_music",
        progress_seconds=60.0,
        now_ts=1_700_000_000,
    )
    defaults.update(kwargs)
    with store._connect() as conn:
        co_listen_store.ensure_schema(conn)
        co_listen_store.record_co_listen_event(conn, **defaults)


class CoListenSummaryRouteTests(unittest.TestCase):
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

    def _post(self, body: dict, *, query: str = "user_id=desktop&real_user_id=master"):
        return self.client.post(
            f"/capabilities/music/co_listen_summary?{query}",
            json=body,
        )

    def test_first_listen_returns_zero_count_and_no_recent(self) -> None:
        response = self._post(
            {
                "title": "晴天",
                "artist": "周杰伦",
                "album": "叶惠美",
                "source_kind": "system_media",
                "source_app": "QQMusic.exe",
                "system_media": True,
            }
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ready")
        self.assertIsNotNone(payload["now"])
        self.assertEqual(payload["now"]["co_listen_count"], 0)
        self.assertTrue(payload["now"]["is_first_listen"])
        self.assertEqual(payload["now"]["source"], "qq_music")
        self.assertEqual(payload["recent"], [])

    def test_reports_existing_count_and_strips_artist_suffix(self) -> None:
        _seed(self.store)
        response = self._post(
            {
                # The frontend joins title + artist with " - " before sending.
                "title": "晴天 - 周杰伦",
                "artist": "周杰伦",
                "album": "叶惠美",
                "source_kind": "system_media",
                "source_app": "QQMusic.exe",
                "system_media": True,
            }
        )
        payload = response.json()
        self.assertEqual(payload["now"]["co_listen_count"], 1)
        self.assertFalse(payload["now"]["is_first_listen"])
        # The artist suffix should have been stripped before going into the
        # display field — the card should show "晴天", not "晴天 - 周杰伦".
        self.assertEqual(payload["now"]["title"], "晴天")
        self.assertGreater(int(payload["now"]["last_listened_at"]), 0)
        self.assertIsInstance(payload["now"]["last_listened_label"], str)

    def test_recent_excludes_current_track(self) -> None:
        _seed(
            self.store,
            identity_key="七里香|周杰伦|七里香",
            title_normalized="七里香",
            album_hint="七里香",
            display_title="七里香",
            now_ts=1_700_000_000,
        )
        _seed(
            self.store,
            identity_key="晴天|周杰伦|叶惠美",
            title_normalized="晴天",
            album_hint="叶惠美",
            display_title="晴天",
            source="local_akane",
            now_ts=1_700_000_100,
        )
        response = self._post(
            {
                "title": "晴天",
                "artist": "周杰伦",
                "album": "叶惠美",
            }
        )
        payload = response.json()
        titles = [item["title"] for item in payload["recent"]]
        self.assertNotIn("晴天", titles)
        self.assertIn("七里香", titles)
        self.assertEqual(payload["now"]["co_listen_count"], 1)

    def test_empty_title_still_returns_recent_only(self) -> None:
        _seed(
            self.store,
            identity_key="七里香|周杰伦|七里香",
            title_normalized="七里香",
            album_hint="七里香",
            display_title="七里香",
        )
        response = self._post({})
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["now"])
        self.assertEqual(len(payload["recent"]), 1)
        self.assertEqual(payload["recent"][0]["title"], "七里香")

    def test_query_path_is_read_only(self) -> None:
        """Asking the route never bumps the co-listen counter."""
        first = self._post(
            {"title": "晴天", "artist": "周杰伦", "album": "叶惠美"}
        ).json()
        second = self._post(
            {"title": "晴天", "artist": "周杰伦", "album": "叶惠美"}
        ).json()
        self.assertEqual(first["now"]["co_listen_count"], 0)
        self.assertEqual(second["now"]["co_listen_count"], 0)

    def test_profile_isolation_across_users(self) -> None:
        _seed(self.store, profile_user_id="master")
        response = self._post(
            {"title": "晴天", "artist": "周杰伦", "album": "叶惠美"},
            query="user_id=guest&real_user_id=guest",
        ).json()
        # Guest profile should not see master's history.
        self.assertEqual(response["now"]["co_listen_count"], 0)
        self.assertEqual(response["recent"], [])


class CoListenSummaryControlsFieldTests(unittest.TestCase):
    """T4: co_listen_summary response must include enabled_music_controls."""

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

    def _post_summary(self, body: dict, *, query: str = "user_id=desktop&real_user_id=master"):
        return self.client.post(
            f"/capabilities/music/co_listen_summary?{query}",
            json=body,
        )

    def _post_permissions(self, body: dict, *, query: str = "user_id=desktop&real_user_id=master"):
        return self.client.post(
            f"/capabilities/music/control_permissions?{query}",
            json=body,
        )

    def test_co_listen_summary_contains_enabled_music_controls_field(self) -> None:
        response = self._post_summary({"title": "晴天", "artist": "周杰伦"})
        payload = response.json()
        self.assertIn("enabled_music_controls", payload)
        self.assertIsInstance(payload["enabled_music_controls"], list)

    def test_co_listen_summary_default_controls_all_enabled(self) -> None:
        response = self._post_summary({"title": "晴天", "artist": "周杰伦"})
        controls = response.json()["enabled_music_controls"]
        self.assertIn("pause", controls)
        self.assertIn("next", controls)
        self.assertIn("prev", controls)
        self.assertIn("recommend", controls)

    def test_co_listen_summary_reflects_revoked_control(self) -> None:
        # Revoke "next"
        self._post_permissions({"controls": {"next": False}})
        # co_listen_summary should no longer include "next"
        response = self._post_summary({"title": "晴天", "artist": "周杰伦"})
        controls = response.json()["enabled_music_controls"]
        self.assertNotIn("next", controls)
        self.assertIn("pause", controls)


if __name__ == "__main__":
    unittest.main()
