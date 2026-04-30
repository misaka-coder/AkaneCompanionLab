from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01.desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, DESKTOP_PET_RESOURCE_CONTRACT_VERSION
from companion_v01.routes.core import build_core_router
from companion_v01.routes.desktop_pet import build_desktop_pet_router
from companion_v01.routes.gifts import build_gifts_router
from companion_v01.routes.sessions import build_sessions_router
from companion_v01.routes.think import build_think_router


class FakeRuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []
        self.counters: dict[str, float] = {}

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))

    def incr(self, key: str, amount: float = 1.0) -> None:
        self.counters[key] = self.counters.get(key, 0.0) + amount


class FakeGuard:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.released = 0

    def try_acquire(self):
        return SimpleNamespace(
            allowed=self.allowed,
            acquired=self.allowed,
            reason="busy",
            message="busy",
        )

    def release(self) -> None:
        self.released += 1


class FakeStore:
    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str], dict[str, Any]] = {}

    def ensure_session(self, *, profile_user_id: str, session_id: str, display_title: str | None = None) -> dict[str, Any]:
        session = {
            "profile_user_id": profile_user_id,
            "session_id": session_id,
            "display_title": display_title or session_id,
        }
        self.sessions[(profile_user_id, session_id)] = session
        return session

    def get_session(self, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get((profile_user_id, session_id))

    def list_sessions(self, *, profile_user_id: str, limit: int) -> list[dict[str, Any]]:
        return [
            session
            for (stored_profile, _session_id), session in self.sessions.items()
            if stored_profile == profile_user_id
        ][:limit]

    def get_session_messages(self, *, profile_user_id: str, session_id: str, limit: int) -> list[dict[str, Any]]:
        return [{"role": "assistant", "content": "hello"}]

    def get_latest_eval_turn_for_session(self, *, profile_user_id: str, session_id: str) -> dict[str, Any]:
        return {"final_json": {"emotion": "normal"}}


def resolve_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "session")
    profile_user_id = str(request.query_params.get("real_user_id") or request.query_params.get("profileUserId") or session_id)
    return session_id, profile_user_id


def resolve_payload(payload: dict) -> tuple[str, str]:
    session_id = str(payload.get("user_id") or payload.get("session_id") or "session")
    profile_user_id = str(payload.get("real_user_id") or session_id)
    return session_id, profile_user_id


class BackendRouteModuleTests(unittest.TestCase):
    def test_core_router_decorates_resource_manifest_for_desktop_pet(self) -> None:
        captured: dict[str, Any] = {}

        def build_resource_manifest(**kwargs):
            captured.update(kwargs)
            return {
                "schema_version": 2,
                "characters": {
                    "outfits": [
                        {
                            "id": "cat",
                            "name": "cat",
                            "emotions": [{"id": "normal", "name": "normal", "path": "/assets/cat/normal.png"}],
                        }
                    ]
                },
                "defaults": {"outfit": "cat", "emotion": "normal"},
            }

        engine = SimpleNamespace(
            build_resource_manifest=build_resource_manifest
        )
        app = FastAPI()
        app.include_router(
            build_core_router(
                engine=engine,
                config_module=SimpleNamespace(STREAMING_TTS_ENABLED=True),
                resolve_identity_from_query=resolve_query,
            )
        )

        response = TestClient(app).get(
            "/resource-manifest?profileUserId=master&user_id=desktop"
            "&client=desktop_pet&character_pack_id=mika_pack"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(captured["client_mode"], "desktop_pet")
        self.assertEqual(captured["character_pack_id"], "mika_pack")
        self.assertEqual(payload["clients"]["desktop_pet"]["contract_version"], DESKTOP_PET_RESOURCE_CONTRACT_VERSION)
        self.assertEqual(payload["clients"]["desktop_pet"]["default_outfit"], "cat")

    def test_sessions_router_ensures_session_without_real_store(self) -> None:
        runtime = FakeRuntimeMetrics()
        engine = SimpleNamespace(store=FakeStore())
        app = FastAPI()
        app.include_router(
            build_sessions_router(
                engine=engine,
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
                resolve_identity_from_query=resolve_query,
                resolve_identity_from_payload=resolve_payload,
            )
        )

        response = TestClient(app).post(
            "/sessions/ensure",
            json={"user_id": "desktop", "real_user_id": "master", "display_title": "Desktop"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session"]["session_id"], "desktop")
        self.assertEqual(payload["session"]["display_title"], "Desktop")
        self.assertEqual(payload["latest_final_json"], {"emotion": "normal"})
        self.assertIn(("sessions_ensure", True), runtime.observed)

    def test_desktop_pet_router_adds_workspace_file_urls(self) -> None:
        runtime = FakeRuntimeMetrics()

        def build_panel(**_kwargs):
            return {
                "ok": True,
                "sections": {
                    "files": [{"id": "att-1", "handle": "att-1", "can_open": True}],
                    "outputs": [{"id": "gen-1", "handle": "gen-1", "can_open": True}],
                },
            }

        engine = SimpleNamespace(build_desktop_pet_workspace_panel=build_panel)
        app = FastAPI()
        app.include_router(
            build_desktop_pet_router(
                engine=engine,
                config_module=SimpleNamespace(DESKTOP_PET_AUDIO_UPLOAD_MAX_BYTES=1024),
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
                resolve_identity_from_query=resolve_query,
                resolve_identity_from_payload=resolve_payload,
            )
        )

        response = TestClient(app).get("/desktop-pet/workspace/summary?user_id=desktop&real_user_id=master")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("/desktop-pet/workspace/attachments/att-1/content", payload["sections"]["files"][0]["url"])
        self.assertIn("/desktop-pet/workspace/generated/gen-1/content", payload["sections"]["outputs"][0]["url"])
        self.assertIn(("desktop_pet_workspace_summary", True), runtime.observed)

    def test_desktop_pet_router_handles_screen_vision_workspace(self) -> None:
        runtime = FakeRuntimeMetrics()
        stored: list[dict[str, Any]] = []

        def submit_clip(**kwargs):
            clip = {
                "clip_id": "screen-1",
                "status": "pending",
                "frame_count": len(kwargs.get("frames") or []),
            }
            stored.append(clip)
            return clip

        def list_latest(**_kwargs):
            return list(stored)

        def clear(**_kwargs):
            count = len(stored)
            stored.clear()
            return {"ok": True, "removed": count}

        engine = SimpleNamespace(
            submit_desktop_screen_vision_clip=submit_clip,
            list_desktop_screen_vision_observations=list_latest,
            get_desktop_screen_vision_clip=lambda **_kwargs: stored[0] if stored else None,
            build_desktop_screen_vision_reaction=lambda **_kwargs: {
                "ok": True,
                "speech": "刚刚这一下挺有意思的。",
                "emotion": "开心",
                "skip": False,
            },
            clear_desktop_screen_vision_observations=clear,
        )
        app = FastAPI()
        app.include_router(
            build_desktop_pet_router(
                engine=engine,
                config_module=SimpleNamespace(DESKTOP_PET_AUDIO_UPLOAD_MAX_BYTES=1024),
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
                resolve_identity_from_query=resolve_query,
                resolve_identity_from_payload=resolve_payload,
            )
        )
        client = TestClient(app)

        submit_response = client.post(
            "/desktop-pet/vision/clip",
            json={
                "user_id": "desktop",
                "real_user_id": "master",
                "frames": [{"data_url": "data:image/jpeg;base64,abc"}],
            },
        )
        latest_response = client.get("/desktop-pet/vision/latest?user_id=desktop&real_user_id=master")
        reaction_response = client.post(
            "/desktop-pet/vision/reaction",
            json={"user_id": "desktop", "real_user_id": "master", "clip_id": "screen-1"},
        )
        clear_response = client.post(
            "/desktop-pet/vision/clear",
            json={"user_id": "desktop", "real_user_id": "master"},
        )

        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(submit_response.json()["clip"]["clip_id"], "screen-1")
        self.assertEqual(latest_response.status_code, 200)
        self.assertEqual(latest_response.json()["items"][0]["frame_count"], 1)
        self.assertEqual(reaction_response.status_code, 200)
        self.assertEqual(reaction_response.json()["speech"], "刚刚这一下挺有意思的。")
        self.assertEqual(clear_response.status_code, 200)
        self.assertEqual(clear_response.json()["removed"], 1)

    def test_gifts_router_validates_upload_filename_and_lists_assets(self) -> None:
        runtime = FakeRuntimeMetrics()
        engine = SimpleNamespace(
            list_gift_assets=lambda **_kwargs: [{"asset_id": "gift-1"}],
        )
        app = FastAPI()
        app.include_router(
            build_gifts_router(
                engine=engine,
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
                resolve_identity_from_query=resolve_query,
                resolve_identity_from_payload=resolve_payload,
            )
        )
        client = TestClient(app)

        list_response = client.get("/gifts?user_id=desktop&real_user_id=master")
        upload_response = client.post("/gifts/upload?user_id=desktop&real_user_id=master", content=b"")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["items"], [{"asset_id": "gift-1"}])
        self.assertEqual(upload_response.status_code, 400)
        self.assertEqual(upload_response.json()["detail"], "missing gift filename")

    def test_think_router_handles_once_and_stream_contract_with_fake_engine(self) -> None:
        runtime = FakeRuntimeMetrics()
        guard = FakeGuard()

        class FakeEngine:
            def process_turn(self, payload: dict) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "emotion": "normal",
                    "speech": f"echo: {payload.get('message')}",
                    "_debug": {},
                }

            def process_turn_stream(self, payload: dict):
                yield {"type": "ui", "emotion": "normal"}
                yield {"type": "speech_chunk", "text": "hello"}
                yield {"type": "final", "payload": self.process_turn(payload)}

        app = FastAPI()
        app.include_router(
            build_think_router(
                engine=FakeEngine(),
                public_guard=guard,
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
            )
        )
        client = TestClient(app)

        with redirect_stdout(io.StringIO()):
            once_response = client.post("/think_once", json={"user_id": "desktop", "message": "hi"})
            stream_response = client.post("/think", json={"user_id": "desktop", "message": "hi"})
        stream_lines = [json.loads(line) for line in stream_response.text.splitlines()]

        self.assertEqual(once_response.status_code, 200)
        self.assertEqual(once_response.json()["speech"], "echo: hi")
        self.assertEqual(stream_response.status_code, 200)
        self.assertEqual(stream_response.headers["x-akane-contract"], DESKTOP_PET_CONTRACT_VERSION)
        self.assertEqual(stream_lines[0]["type"], "stream_start")
        self.assertEqual(stream_lines[-1]["type"], "stream_end")
        self.assertEqual(stream_lines[-1]["partial"]["speech"], "echo: hi")
        self.assertEqual(guard.released, 2)

    def test_think_router_invalid_payload_does_not_call_engine(self) -> None:
        runtime = FakeRuntimeMetrics()
        app = FastAPI()
        app.include_router(
            build_think_router(
                engine=SimpleNamespace(),
                public_guard=FakeGuard(),
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
            )
        )

        response = TestClient(app).post("/think_once", json=["not", "object"])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_payload")
        self.assertIn(("think_once", False), runtime.observed)


if __name__ == "__main__":
    unittest.main()
