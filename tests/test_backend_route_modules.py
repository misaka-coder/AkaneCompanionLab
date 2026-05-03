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
from companion_v01.routes.control_center import (
    build_control_center_router,
    build_control_center_snapshot_runtime_providers,
)
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

    def snapshot(self) -> dict[str, float]:
        return dict(self.counters)


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

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "max_concurrent_thinks": 2,
            "daily_think_limit": 200,
            "active_thinks": 0,
            "used_today": 1,
        }


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

    def test_desktop_pet_router_imports_local_paths_with_workspace_urls(self) -> None:
        runtime = FakeRuntimeMetrics()
        captured: dict[str, Any] = {}

        def import_local(**kwargs):
            captured.update(kwargs)
            return {
                "ok": True,
                "source": "desktop_pet",
                "mode": "explicit_local_paths",
                "imported": 1,
                "skipped_count": 0,
                "items": [{"id": "file_001", "handle": "file_001", "can_open": True}],
                "attachments": [],
                "skipped": [],
            }

        engine = SimpleNamespace(import_desktop_pet_local_paths=import_local)
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

        response = TestClient(app).post(
            "/desktop-pet/workspace/import-local",
            json={
                "user_id": "desktop",
                "real_user_id": "master",
                "paths": ["C:/tmp/note.md"],
                "recursive": True,
                "max_files": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["profile_user_id"], "master")
        self.assertEqual(captured["session_id"], "desktop")
        self.assertEqual(captured["paths"], ["C:/tmp/note.md"])
        self.assertTrue(captured["recursive"])
        self.assertEqual(captured["max_files"], 2)
        payload = response.json()
        self.assertIn("/desktop-pet/workspace/attachments/file_001/content", payload["items"][0]["url"])
        self.assertIn(("desktop_pet_workspace_import_local", True), runtime.observed)

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

    # ---------- control center action contract ----------

    def test_control_center_action_returns_not_implemented(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).post("/control-center/actions/music.next", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["status"], "not-implemented")
        self.assertEqual(payload["actionId"], "music.next")
        self.assertEqual(payload["refresh"], False)

    def test_control_center_action_catalog_describes_contract(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).get("/control-center/actions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = response.json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["contractVersion"], 1)
        self.assertEqual(payload["actionsEndpoint"], "/control-center/actions/{actionId}")
        self.assertEqual(payload["execution"], "not-implemented")
        self.assertEqual(payload["defaultResult"]["status"], "not-implemented")
        self.assertEqual(payload["defaultResult"]["refresh"], False)

    def test_control_center_action_window_close_is_not_implemented(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).post("/control-center/actions/window.close", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "not-implemented")
        self.assertEqual(payload["actionId"], "window.close")

    def test_control_center_unknown_action_not_404(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).post("/control-center/actions/unknown.action", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "not-implemented")
        self.assertEqual(payload["actionId"], "unknown.action")

    def test_control_center_non_object_payload_does_not_500(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).post("/control-center/actions/music.next", json="not an object")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not-implemented")

    def test_control_center_empty_body_does_not_500(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).post("/control-center/actions/music.next", content=b"", headers={"Content-Type": "application/json"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not-implemented")

    def test_control_center_runtime_metrics_does_not_block_response(self) -> None:
        runtime = FakeRuntimeMetrics()
        app = FastAPI()
        app.include_router(
            build_control_center_router(runtime_metrics=runtime)
        )

        response = TestClient(app).post("/control-center/actions/music.next", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not-implemented")

    def test_control_center_log_event_receives_contract_event(self) -> None:
        events: list[tuple[str, dict[str, Any]]] = []

        def log_event(event: str, **fields: Any) -> None:
            events.append((event, fields))

        app = FastAPI()
        app.include_router(build_control_center_router(log_event=log_event))

        response = TestClient(app).post("/control-center/actions/music.next", json={"value": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(events[0][0], "control_center_action")
        self.assertEqual(events[0][1]["action_id"], "music.next")
        self.assertEqual(events[0][1]["status"], "not-implemented")
        self.assertEqual(events[0][1]["payload_keys"], ["value"])

    def test_control_center_metrics_and_log_errors_do_not_block_response(self) -> None:
        class BrokenRuntimeMetrics:
            def observe_request(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("metrics failed")

        def log_event(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("log failed")

        app = FastAPI()
        app.include_router(
            build_control_center_router(
                runtime_metrics=BrokenRuntimeMetrics(),
                log_event=log_event,
            )
        )

        response = TestClient(app).post("/control-center/actions/music.next", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not-implemented")

    # ---------- control center snapshot contract ----------

    def test_control_center_snapshot_returns_200(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).get("/control-center/snapshot")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["status"], "available")

    def test_control_center_snapshot_contract_shape(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        payload = TestClient(app).get("/control-center/snapshot").json()

        self.assertIn("schemaVersion", payload)
        self.assertIsInstance(payload["schemaVersion"], int)
        self.assertEqual(payload["sourceKind"], "backend")
        self.assertIn("generatedAt", payload)
        self.assertIsInstance(payload["generatedAt"], str)
        self.assertIn("runtime", payload)
        self.assertIsInstance(payload["runtime"], dict)

    def test_control_center_snapshot_runtime_has_all_fields(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        runtime = TestClient(app).get("/control-center/snapshot").json()["runtime"]

        for field in ("health", "diagnostics", "workspace", "resourceManifest", "metrics"):
            self.assertIn(field, runtime, f"runtime should contain {field}")
            self.assertIsInstance(runtime[field], dict)
            self.assertIn("ok", runtime[field])
            self.assertIn("status", runtime[field])

    def test_control_center_snapshot_failure_does_not_500(self) -> None:
        def fail_health() -> dict:
            raise RuntimeError("health failed")

        app = FastAPI()
        app.include_router(
            build_control_center_router(
                snapshot_runtime_providers={
                    "health": fail_health,
                    "diagnostics": lambda: {"status": "ok"},
                    "metrics": lambda: "cpu_percent 12",
                }
            )
        )

        response = TestClient(app).get("/control-center/snapshot")
        self.assertEqual(response.status_code, 200)
        runtime = response.json()["runtime"]
        self.assertEqual(runtime["health"]["status"], "unavailable")
        self.assertEqual(runtime["diagnostics"]["status"], "ok")
        self.assertEqual(runtime["metrics"], "cpu_percent 12")

    def test_control_center_snapshot_real_providers_aggregate_runtime(self) -> None:
        runtime_metrics = FakeRuntimeMetrics()
        runtime_metrics.incr("custom_total", 2)
        captured: dict[str, Any] = {}

        def build_resource_manifest(**kwargs):
            captured["resource_manifest"] = kwargs
            return {
                "schema_version": 2,
                "characters": {
                    "outfits": [
                        {
                            "id": "cat",
                            "name": "Cat",
                            "emotions": [{"id": "normal", "name": "Normal"}],
                        }
                    ]
                },
                "defaults": {"outfit": "cat", "emotion": "normal"},
            }

        def build_workspace_panel(**kwargs):
            captured["workspace"] = kwargs
            return {
                "ok": True,
                "counts": {"files": 2, "outputs": 1, "tasks": 0},
                "sections": {
                    "files": [{"id": "att-1", "handle": "att-1", "can_open": True}],
                    "outputs": [],
                },
            }

        engine = SimpleNamespace(
            build_resource_manifest=build_resource_manifest,
            build_desktop_pet_workspace_panel=build_workspace_panel,
            llm=SimpleNamespace(snapshot_metrics=lambda: {"requests_total": 3}),
            vector_store=SimpleNamespace(count_entries=lambda: 42),
            snapshot_embedding_reindex_status=lambda: {"total": 5, "processed": 2, "state": "idle"},
        )

        app = FastAPI()
        app.include_router(
            build_control_center_router(
                runtime_metrics=runtime_metrics,
                resolve_identity_from_query=resolve_query,
                snapshot_runtime_providers=build_control_center_snapshot_runtime_providers(
                    engine=engine,
                    config_module=SimpleNamespace(STREAMING_TTS_ENABLED=True),
                    runtime_metrics=runtime_metrics,
                    public_guard=FakeGuard(),
                ),
            )
        )

        response = TestClient(app).get(
            "/control-center/snapshot?user_id=desktop&real_user_id=master"
            "&client=desktop_pet&character_pack_id=mika_pack&outfit=cat&emotion=normal"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        runtime = payload["runtime"]
        self.assertEqual(runtime["health"]["status"], "ok")
        self.assertEqual(runtime["diagnostics"]["status"], "ok")
        self.assertEqual(runtime["workspace"]["counts"]["files"], 2)
        self.assertIn("/desktop-pet/workspace/attachments/att-1/content", runtime["workspace"]["sections"]["files"][0]["url"])
        self.assertEqual(runtime["resourceManifest"]["clients"]["desktop_pet"]["profile_user_id"], "master")
        self.assertIn("akane_vector_entries 42", runtime["metrics"])
        self.assertIn("akane_custom_total 2.0", runtime["metrics"])
        self.assertEqual(captured["resource_manifest"]["character_pack_id"], "mika_pack")
        self.assertEqual(captured["workspace"]["profile_user_id"], "master")
        self.assertIn(("control_center.snapshot", True), runtime_metrics.observed)

    def test_control_center_snapshot_does_not_break_action_contract(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        snapshot = TestClient(app).get("/control-center/snapshot").json()
        self.assertEqual(snapshot["ok"], True)

        action = TestClient(app).post("/control-center/actions/music.next", json={}).json()
        self.assertEqual(action["status"], "not-implemented")
        self.assertEqual(action["actionId"], "music.next")

    def test_control_center_snapshot_cached_no_store(self) -> None:
        app = FastAPI()
        app.include_router(build_control_center_router())

        response = TestClient(app).get("/control-center/snapshot")
        self.assertEqual(response.headers["cache-control"], "no-store")

    # ---------- snapshot resilience ----------

    def test_control_center_snapshot_workspace_provider_failure_still_200(self) -> None:
        def fail_workspace(context: dict) -> dict:
            raise RuntimeError("workspace failed")

        runtime_metrics = FakeRuntimeMetrics()
        app = FastAPI()
        app.include_router(
            build_control_center_router(
                runtime_metrics=runtime_metrics,
                snapshot_runtime_providers={
                    "health": lambda: {"status": "ok"},
                    "diagnostics": lambda context: {"status": "ok", "capabilities": {"tool_names": [], "declared": [], "effective_modules": [], "tool_layers": []}, "runtime": {"metrics": {}}, "resources": {}, "workspace": {}, "safety": {}},
                    "workspace": fail_workspace,
                    "resourceManifest": lambda: {"schema_version": 1, "clients": {"desktop_pet": {}}, "characters": {"outfits": []}},
                    "metrics": lambda: "cpu_percent 12",
                },
            )
        )

        response = TestClient(app).get("/control-center/snapshot")
        self.assertEqual(response.status_code, 200)
        runtime = response.json()["runtime"]
        self.assertEqual(runtime["health"]["status"], "ok")
        self.assertEqual(runtime["diagnostics"]["status"], "ok")
        self.assertEqual(runtime["workspace"]["status"], "unavailable")
        self.assertIn("error", runtime["workspace"])
        self.assertIn("schema_version", runtime["resourceManifest"])
        self.assertIn("cpu_percent", runtime["metrics"])

    def test_control_center_snapshot_metrics_provider_failure_still_200(self) -> None:
        def fail_metrics() -> str:
            raise RuntimeError("metrics failed")

        app = FastAPI()
        app.include_router(
            build_control_center_router(
                snapshot_runtime_providers={
                    "health": lambda: {"status": "ok"},
                    "diagnostics": lambda: {"status": "ok"},
                    "workspace": lambda: {},
                    "resourceManifest": lambda: {},
                    "metrics": fail_metrics,
                }
            )
        )

        response = TestClient(app).get("/control-center/snapshot")
        self.assertEqual(response.status_code, 200)
        runtime = response.json()["runtime"]
        self.assertEqual(runtime["health"]["status"], "ok")
        self.assertEqual(runtime["metrics"]["status"], "unavailable")

    def test_control_center_snapshot_no_sensitive_content(self) -> None:
        runtime_metrics = FakeRuntimeMetrics()
        runtime_metrics.incr("custom_total", 2)

        def build_resource_manifest(**kwargs):
            return {
                "schema_version": 2,
                "characters": {
                    "outfits": [
                        {
                            "id": "cat",
                            "name": "Cat",
                            "emotions": [{"id": "normal", "name": "Normal"}],
                        }
                    ]
                },
                "defaults": {"outfit": "cat", "emotion": "normal"},
            }

        engine = SimpleNamespace(
            build_resource_manifest=build_resource_manifest,
            build_desktop_pet_workspace_panel=lambda **_kwargs: {"ok": True, "counts": {"files": 0, "outputs": 0, "tasks": 0}},
            llm=SimpleNamespace(snapshot_metrics=lambda: {"requests_total": 3}),
            vector_store=SimpleNamespace(count_entries=lambda: 42),
            snapshot_embedding_reindex_status=lambda: {"total": 0, "processed": 0, "state": "idle"},
        )

        app = FastAPI()
        app.include_router(
            build_control_center_router(
                runtime_metrics=runtime_metrics,
                resolve_identity_from_query=resolve_query,
                snapshot_runtime_providers=build_control_center_snapshot_runtime_providers(
                    engine=engine,
                    config_module=SimpleNamespace(STREAMING_TTS_ENABLED=True),
                    runtime_metrics=runtime_metrics,
                    public_guard=FakeGuard(),
                ),
            )
        )

        response = TestClient(app).get("/control-center/snapshot?user_id=desktop&real_user_id=master")
        self.assertEqual(response.status_code, 200)
        body = response.text.lower()

        # Snapshot must not expose prompts, messages, api keys, secrets, clipboard, or screenshots
        for sensitive_term in ("api_key", "prompt_text", "chat_message"):
            self.assertNotIn(sensitive_term, body, f"snapshot should not contain {sensitive_term}")

        # Check that raw content fields are absent from the runtime structure
        payload = response.json()
        runtime = payload["runtime"]
        diagnostics_text = json.dumps(runtime.get("diagnostics", {}))
        for field in ("messages", "prompt"):
            self.assertNotIn(f'"{field}"', diagnostics_text, f"diagnostics should not contain {field}")

    def test_control_center_snapshot_resource_manifest_drives_character_resources(self) -> None:
        runtime_metrics = FakeRuntimeMetrics()

        def build_resource_manifest(**kwargs):
            return {
                "schema_version": 2,
                "characters": {
                    "outfits": [
                        {
                            "id": "sailor",
                            "name": "Sailor",
                            "emotions": [
                                {"id": "happy", "name": "Happy", "path": "/assets/sailor/happy.png"},
                                {"id": "sad", "name": "Sad", "path": "/assets/sailor/sad.png"},
                            ],
                        },
                        {
                            "id": "casual",
                            "name": "Casual",
                            "emotions": [
                                {"id": "smile", "name": "Smile", "path": "/assets/casual/smile.png"},
                                {"id": "angry", "name": "Angry", "path": "/assets/casual/angry.png"},
                                {"id": "cry", "name": "Cry", "path": "/assets/casual/cry.png"},
                            ],
                        },
                    ]
                },
                "defaults": {"outfit": "sailor", "emotion": "happy"},
                "scenes": {
                    "majors": [
                        {"id": "room", "minors": [{"id": "bg1", "backgrounds": [{"id": "b1"}]}]},
                    ]
                },
            }

        engine = SimpleNamespace(
            build_resource_manifest=build_resource_manifest,
            build_desktop_pet_workspace_panel=lambda **_kwargs: {"ok": True, "counts": {"files": 0, "outputs": 0, "tasks": 0}},
            llm=SimpleNamespace(snapshot_metrics=lambda: {"requests_total": 0}),
            vector_store=SimpleNamespace(count_entries=lambda: 0),
            snapshot_embedding_reindex_status=lambda: {"total": 0, "processed": 0, "state": "idle"},
        )

        app = FastAPI()
        app.include_router(
            build_control_center_router(
                runtime_metrics=runtime_metrics,
                resolve_identity_from_query=resolve_query,
                snapshot_runtime_providers=build_control_center_snapshot_runtime_providers(
                    engine=engine,
                    config_module=SimpleNamespace(STREAMING_TTS_ENABLED=True),
                    runtime_metrics=runtime_metrics,
                    public_guard=FakeGuard(),
                ),
            )
        )

        response = TestClient(app).get(
            "/control-center/snapshot?user_id=desktop&real_user_id=master"
            "&client=desktop_pet&character_pack_id=mika_pack&outfit=sailor&emotion=happy"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        runtime = payload["runtime"]

        # resourceManifest drives outfit and emotion data
        manifest = runtime["resourceManifest"]
        self.assertEqual(len(manifest["characters"]["outfits"]), 2)
        self.assertEqual(manifest["characters"]["outfits"][0]["id"], "sailor")
        self.assertEqual(len(manifest["characters"]["outfits"][1]["emotions"]), 3)

        # Diagnostics resources should reflect manifest-derived counts
        diag_resources = runtime["diagnostics"]["resources"]
        # emotion_count includes all emotions across all outfits
        self.assertGreaterEqual(diag_resources["emotion_count"], 0)

        # Check that the decorated manifest reflects the preferred outfit
        self.assertEqual(manifest["clients"]["desktop_pet"]["default_outfit"], "sailor")
        self.assertEqual(manifest["clients"]["desktop_pet"]["default_emotion"], "happy")


if __name__ == "__main__":
    unittest.main()
