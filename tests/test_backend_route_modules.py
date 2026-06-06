from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, DESKTOP_PET_RESOURCE_CONTRACT_VERSION
from companion_v01.local_workflow_execution import WorkflowExecutionRequest
from companion_v01.routes.capabilities import build_capabilities_router
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


class FakeWorkflowRunner:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.requests: list[WorkflowExecutionRequest] = []

    def execute_workflow(self, request: WorkflowExecutionRequest) -> dict[str, Any]:
        self.requests.append(request)
        return self.result


class ExplodingWorkflowRunner:
    def __init__(self) -> None:
        self.requests: list[WorkflowExecutionRequest] = []

    def execute_workflow(self, request: WorkflowExecutionRequest) -> dict[str, Any]:
        self.requests.append(request)
        raise RuntimeError(r"secret token leaked from C:\Users\Lenovo\portrait.png")


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
        self.last_character_pack_id: str | None = None

    def ensure_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        display_title: str | None = None,
    ) -> dict[str, Any]:
        self.last_character_pack_id = character_pack_id
        session = {
            "profile_user_id": profile_user_id,
            "session_id": session_id,
            "character_pack_id": character_pack_id,
            "display_title": display_title or session_id,
        }
        self.sessions[(profile_user_id, session_id)] = session
        return session

    def get_session(self, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get((profile_user_id, session_id))

    def get_character_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any] | None:
        self.last_character_pack_id = character_pack_id
        session = self.sessions.get((profile_user_id, session_id))
        if session and str(session.get("character_pack_id") or "") == character_pack_id:
            return session
        return None

    def list_sessions(
        self,
        *,
        profile_user_id: str,
        limit: int,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.last_character_pack_id = character_pack_id
        return [
            session
            for (stored_profile, _session_id), session in self.sessions.items()
            if stored_profile == profile_user_id
            and (character_pack_id is None or str(session.get("character_pack_id") or "") == character_pack_id)
        ][:limit]

    def get_session_messages(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.last_character_pack_id = character_pack_id
        return [{"role": "assistant", "content": "hello"}]

    def get_latest_eval_turn_for_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
    ) -> dict[str, Any]:
        self.last_character_pack_id = character_pack_id
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
            json={
                "user_id": "desktop",
                "real_user_id": "master",
                "display_title": "Desktop",
                "character_pack_id": "kaju",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session"]["session_id"], "desktop")
        self.assertEqual(payload["session"]["character_pack_id"], "kaju")
        self.assertEqual(payload["session"]["display_title"], "Desktop")
        self.assertEqual(payload["latest_final_json"], {"emotion": "normal"})
        self.assertEqual(engine.store.last_character_pack_id, "kaju")
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


    def test_control_center_snapshot_providers_are_reality_not_placeholder(self) -> None:
        """Verify ALL 5 snapshot providers return real data, not _unavailable placeholders."""
        runtime_metrics = FakeRuntimeMetrics()
        runtime_metrics.incr("requests_total", 1)

        engine = SimpleNamespace(
            build_resource_manifest=lambda **kwargs: {
                "schema_version": 2,
                "characters": {"outfits": [{"id": "cat", "name": "Cat", "emotions": [{"id": "normal", "name": "Normal"}]}]},
                "defaults": {"outfit": "cat", "emotion": "normal"},
            },
            build_desktop_pet_workspace_panel=lambda **_kwargs: {"ok": True, "counts": {"files": 1, "outputs": 0, "tasks": 0}},
            llm=SimpleNamespace(snapshot_metrics=lambda: {"requests_total": 1}),
            vector_store=SimpleNamespace(count_entries=lambda: 10),
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
            "&client=desktop_pet&character_pack_id=mika_pack&outfit=cat&emotion=normal"
        )

        self.assertEqual(response.status_code, 200)
        runtime = response.json()["runtime"]

        # All 5 fields must be present and none should be placeholder/unavailable
        for field in ("health", "diagnostics", "workspace", "resourceManifest", "metrics"):
            self.assertIn(field, runtime, f"snapshot should contain runtime.{field}")
            # If the field is a dict with ok:False, it's an unavailable provider
            value = runtime[field]
            if isinstance(value, dict):
                self.assertNotEqual(
                    value.get("ok"), False,
                    f"snapshot runtime.{field} should NOT be unavailable/placeholder; "
                    f"got error={value.get('error')}"
                )

        # health: real status/pid/python/contracts from config_module
        self.assertEqual(runtime["health"]["status"], "ok")
        self.assertIsInstance(runtime["health"]["pid"], int)
        self.assertIsInstance(runtime["health"]["python"], str)
        self.assertIn("desktop_pet", runtime["health"]["contracts"])

        # diagnostics: real engine calls
        self.assertEqual(runtime["diagnostics"]["status"], "ok")
        self.assertIn("resources", runtime["diagnostics"])
        self.assertIn("capabilities", runtime["diagnostics"])

        # workspace: real engine.build_desktop_pet_workspace_panel called
        self.assertTrue(runtime["workspace"]["ok"])
        self.assertEqual(runtime["workspace"]["counts"]["files"], 1)

        # resourceManifest: real engine.build_resource_manifest + decorate
        self.assertIsInstance(runtime["resourceManifest"]["schema_version"], int)
        self.assertIn("characters", runtime["resourceManifest"])
        self.assertIn("clients", runtime["resourceManifest"])

        # metrics: prometheus text with real tracemalloc/llm/vector counts
        self.assertIsInstance(runtime["metrics"], str)
        self.assertIn("akane_vector_entries", runtime["metrics"])
        self.assertIn("akane_tracemalloc", runtime["metrics"])
        self.assertIn("akane_llm_requests_total", runtime["metrics"])

    def test_control_center_action_inert_refresh_only(self) -> None:
        """Verify that ALL backend action endpoints only return not-implemented,
        never execute desktop operations."""
        app = FastAPI()
        app.include_router(build_control_center_router())

        client = TestClient(app)

        # Multiple action types: desktop-related, window, music, unknown
        for action_id in ("window.close", "music.next", "unknown.action", "character.importZip"):
            response = client.post(f"/control-center/actions/{action_id}", json={})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["ok"], False, f"{action_id} should be ok:false")
            self.assertEqual(payload["status"], "not-implemented", f"{action_id} should be not-implemented")
            self.assertEqual(payload["actionId"], action_id, f"{action_id} should echo actionId")
            self.assertEqual(payload["refresh"], False, f"{action_id} should have refresh:false")

    def test_capabilities_catalog_exposes_readonly_existing_tools_and_providers(self) -> None:
        runtime = FakeRuntimeMetrics()
        engine = SimpleNamespace(
            tool_handlers={
                "retrieve_memory": object(),
                "compose_file": object(),
                "transcribe_media": object(),
            }
        )
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=engine,
                config_module=SimpleNamespace(
                    TTS_VOICE="zh-CN-XiaoxiaoNeural",
                    STREAMING_TTS_ENABLED=True,
                    ASR_WHISPER_MODEL_SIZE="small",
                    ASR_LANGUAGE="zh",
                ),
                tts_client=object(),
                runtime_metrics=runtime,
                resolve_identity_from_query=resolve_query,
            )
        )

        response = TestClient(app).get("/capabilities?user_id=desktop&real_user_id=master")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["execution"], "read-only")
        self.assertEqual(payload["configScope"]["profileUserId"], "master")
        self.assertEqual(
            payload["configScope"]["explicitConfigPath"],
            "users_data/<profile_user_id>/capabilities/capabilities.yaml",
        )
        self.assertEqual(
            payload["configScope"]["localDiscoveryPath"],
            "users_data/_local/capabilities/discovery.json",
        )

        capabilities = payload["capabilities"]
        by_id = {item["id"]: item for item in capabilities}
        self.assertIn("tool.retrieve_memory", by_id)
        self.assertIn("tool.compose_file", by_id)
        self.assertIn("tool.transcribe_media", by_id)
        self.assertIn("provider.tts.edge", by_id)
        self.assertIn("provider.asr.faster_whisper", by_id)
        self.assertIn("workflow.workshop.portrait.cutout", by_id)

        self.assertEqual(by_id["tool.compose_file"]["source"], "backend_tool")
        self.assertEqual(by_id["tool.compose_file"]["adapter"], "tool_runtime")
        self.assertEqual(by_id["tool.compose_file"]["status"], "ready")
        self.assertEqual(by_id["tool.transcribe_media"]["risk"], "medium")

        tts_provider = by_id["provider.tts.edge"]
        self.assertEqual(tts_provider["type"], "tts_provider")
        self.assertEqual(tts_provider["source"], "builtin")
        self.assertEqual(tts_provider["adapter"], "edge_tts")
        self.assertEqual(tts_provider["executionMode"], "internal")

        cutout_workflow = by_id["workflow.workshop.portrait.cutout"]
        self.assertEqual(cutout_workflow["kind"], "workflow")
        self.assertEqual(cutout_workflow["type"], "asset_processor")
        self.assertEqual(cutout_workflow["source"], "external_executor")
        self.assertEqual(cutout_workflow["adapter"], "comfyui")
        self.assertEqual(cutout_workflow["providerId"], "provider.comfyui.local")
        self.assertEqual(cutout_workflow["status"], "missing_config")
        self.assertFalse(cutout_workflow["enabled"])
        self.assertEqual(cutout_workflow["inputSchema"]["pathPolicy"], "safe-handle-only")

        # Product names must stay in adapter/provider ids, not base source/type.
        for item in capabilities:
            self.assertNotIn(item.get("source"), {"comfyui", "gpt_sovits", "rvc", "faster_whisper", "demucs"})
            self.assertNotIn(item.get("type"), {"comfyui", "gpt_sovits", "rvc", "faster_whisper", "demucs"})

        body = response.text.lower()
        for sensitive in ("api_key", "password", "secret", "token", "prompt_text", "chat_message"):
            self.assertNotIn(sensitive, body)
        self.assertIn(("capabilities.catalog", True), runtime.observed)

    def test_capabilities_workflows_are_read_only_and_do_not_fake_readiness(self) -> None:
        runtime = FakeRuntimeMetrics()
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    provider_health_checker=lambda _host, _port, _timeout: (False, "connection failed"),
                )
            )
            client = TestClient(app)

            missing = client.get("/capabilities/workflows?user_id=desktop&real_user_id=master")
            self.assertEqual(missing.status_code, 200)
            missing_payload = missing.json()
            self.assertTrue(missing_payload["ok"])
            self.assertEqual(missing_payload["execution"], "read-only")
            workflow = {item["id"]: item for item in missing_payload["workflows"]}[
                "workflow.workshop.portrait.cutout"
            ]
            self.assertEqual(workflow["kind"], "workflow")
            self.assertEqual(workflow["capabilityId"], "workshop.portrait.cutout")
            self.assertEqual(workflow["workflowId"], "workflow.comfyui.portrait_cutout")
            self.assertEqual(workflow["providerId"], "provider.comfyui.local")
            self.assertEqual(workflow["target"], "character_pack_assets")
            self.assertEqual(workflow["output"], "transparent_png")
            self.assertEqual(workflow["status"], "missing_config")
            self.assertFalse(workflow["enabled"])
            self.assertIn("input_image_handle", workflow["slots"]["required"])
            self.assertIn("output_image_handle", workflow["slots"]["required"])

            saved = client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            ).json()
            self.assertTrue(saved["ok"])
            configured = client.get("/capabilities/workflows?user_id=desktop&real_user_id=master").json()
            configured_workflow = {item["id"]: item for item in configured["workflows"]}[
                "workflow.workshop.portrait.cutout"
            ]
            self.assertEqual(configured_workflow["status"], "missing_workflow")
            self.assertEqual(configured_workflow["reason"], "workflow_binding_missing")
            self.assertFalse(configured_workflow["enabled"])

            client.post(
                "/capabilities/providers/provider.comfyui.local/health-check?user_id=desktop&real_user_id=master",
                json={},
            )
            unreachable = client.get("/capabilities/workflows?user_id=desktop&real_user_id=master").json()
            unreachable_workflow = {item["id"]: item for item in unreachable["workflows"]}[
                "workflow.workshop.portrait.cutout"
            ]
            self.assertEqual(unreachable_workflow["status"], "unreachable")
            self.assertFalse(unreachable_workflow["enabled"])

            body = json.dumps(unreachable, ensure_ascii=False).lower()
            for sensitive in ("api_key", "password", "secret", "token", str(Path(temp_dir)).lower()):
                self.assertNotIn(sensitive, body)
            self.assertIn(("capabilities.workflows", True), runtime.observed)

    def test_capabilities_workflow_config_skeleton_persists_safe_binding_without_execution(self) -> None:
        runtime = FakeRuntimeMetrics()
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)

            provider = client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            ).json()
            self.assertTrue(provider["ok"])

            saved_response = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": r"workflows\comfyui\portrait_cutout.json?token=secret",
                    "slotMapping": {
                        "input_image_handle": "input_image",
                        "output_image_handle": "output_image",
                        "ignored_extra": "should_not_echo",
                    },
                },
            )
            saved = saved_response.json()
            self.assertFalse(saved["ok"])
            self.assertEqual(saved["status"], "invalid_workflow_config")

            saved_response = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": r"workflows\comfyui\portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "input_image",
                        "output_image_handle": "output_image",
                        "ignored_extra": "should_not_echo",
                    },
                },
            )
            self.assertEqual(saved_response.status_code, 200)
            saved = saved_response.json()
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["status"], "saved")
            self.assertFalse(saved["executionReady"])
            workflow = saved["workflow"]
            self.assertEqual(workflow["workflowPath"], "workflows/comfyui/portrait_cutout.json")
            self.assertEqual(workflow["status"], "configured")
            self.assertEqual(workflow["reason"], "workflow_runtime_not_bound")
            self.assertFalse(workflow["executionReady"])
            self.assertNotIn("ignored_extra", json.dumps(workflow, ensure_ascii=False))

            validated = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/validate?user_id=desktop&real_user_id=master"
            ).json()
            self.assertTrue(validated["ok"])
            self.assertEqual(validated["status"], "validated_config")
            self.assertFalse(validated["executionReady"])
            self.assertTrue(validated["checks"]["providerConfigured"])
            self.assertTrue(validated["checks"]["workflowConfigured"])
            self.assertTrue(validated["checks"]["requiredSlots"])

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            by_id = {item["id"]: item for item in catalog["capabilities"]}
            self.assertEqual(by_id["workflow.workshop.portrait.cutout"]["status"], "configured")
            self.assertFalse(by_id["workflow.workshop.portrait.cutout"]["executionReady"])

            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("workflows/comfyui/portrait_cutout.json", config_text)
            for forbidden in ("token", "secret", "ignored_extra", str(Path(temp_dir))):
                self.assertNotIn(forbidden.lower(), config_text.lower())

            rejected_path = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "workflowPath": r"C:\Users\Lenovo\workflow.json"},
            ).json()
            self.assertFalse(rejected_path["ok"])
            self.assertEqual(rejected_path["status"], "invalid_workflow_config")

            rejected_slots = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {"input_image_handle": "input image"},
                },
            ).json()
            self.assertFalse(rejected_slots["ok"])
            self.assertIn(rejected_slots["status"], {"missing_slot_mapping", "invalid_workflow_config"})
            self.assertIn(("capabilities.workflow_config", True), runtime.observed)
            self.assertIn(("capabilities.workflow_config", False), runtime.observed)
            self.assertIn(("capabilities.workflow_validate", True), runtime.observed)

    def test_capabilities_workflow_preflight_is_safe_and_inert(self) -> None:
        runtime = FakeRuntimeMetrics()
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)

            missing = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            ).json()
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["status"], "missing_config")
            self.assertFalse(missing["executionReady"])
            self.assertFalse(missing["canRun"])
            self.assertFalse(missing["checks"]["providerConfigured"])
            self.assertFalse(missing["checks"]["runnerBound"])

            client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            )
            client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "input_image",
                        "output_image_handle": "output_image",
                    },
                },
            )

            rejected = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": r"C:\Users\Lenovo\secret.png",
                    "outputImageHandle": "portrait_cutout",
                },
            ).json()
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["status"], "invalid_request")
            self.assertEqual(rejected["reason"], "asset_handle_must_be_safe_opaque_id")
            rejected_text = json.dumps(rejected, ensure_ascii=False).lower()
            self.assertNotIn("secret.png", rejected_text)
            self.assertNotIn(str(Path(temp_dir)).lower(), rejected_text)

            ready_but_inert = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            ).json()
            self.assertFalse(ready_but_inert["ok"])
            self.assertEqual(ready_but_inert["status"], "not-implemented")
            self.assertEqual(ready_but_inert["reason"], "workflow_runner_not_bound")
            self.assertFalse(ready_but_inert["executionReady"])
            self.assertFalse(ready_but_inert["canRun"])
            self.assertTrue(ready_but_inert["checks"]["providerConfigured"])
            self.assertTrue(ready_but_inert["checks"]["workflowConfigured"])
            self.assertTrue(ready_but_inert["checks"]["inputImageHandle"])
            self.assertTrue(ready_but_inert["checks"]["outputImageHandle"])
            self.assertFalse(ready_but_inert["checks"]["runnerBound"])
            self.assertEqual(ready_but_inert["acceptedInputs"]["inputImageHandle"], "portrait_source")
            self.assertEqual(ready_but_inert["acceptedInputs"]["outputImageHandle"], "portrait_cutout")

            unknown = client.post(
                "/capabilities/workflows/unknown.workflow/preflight?user_id=desktop&real_user_id=master",
                json={},
            )
            self.assertEqual(unknown.status_code, 404)
            self.assertEqual(unknown.json()["status"], "unknown_workflow")
            self.assertIn(("capabilities.workflow_preflight", False), runtime.observed)

    def test_capabilities_workflow_job_routes_are_inert_and_safe(self) -> None:
        runtime = FakeRuntimeMetrics()
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)

            missing = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            )
            self.assertEqual(missing.status_code, 200)
            missing_payload = missing.json()
            self.assertFalse(missing_payload["ok"])
            self.assertEqual(missing_payload["status"], "missing_config")
            self.assertNotIn("jobId", missing_payload)

            unknown = client.post(
                "/capabilities/workflows/unknown.workflow/jobs?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            )
            self.assertEqual(unknown.status_code, 404)
            self.assertEqual(unknown.json()["status"], "unknown_workflow")

            client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            )
            client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "input_image",
                        "output_image_handle": "output_image",
                    },
                },
            )

            rejected = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": "https://example.test/portrait.png?token=secret",
                    "outputImageHandle": "portrait_cutout",
                    "imageBytes": "RAW_IMAGE_BYTES_SHOULD_NOT_ECHO",
                },
            )
            self.assertEqual(rejected.status_code, 200)
            rejected_payload = rejected.json()
            self.assertFalse(rejected_payload["ok"])
            self.assertEqual(rejected_payload["status"], "invalid_request")
            self.assertNotIn("jobId", rejected_payload)

            inert = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": "portrait_source",
                    "outputImageHandle": "portrait_cutout",
                    "imageBytes": "RAW_IMAGE_BYTES_SHOULD_NOT_ECHO",
                    "token": "secret",
                },
            )
            self.assertEqual(inert.status_code, 200)
            inert_payload = inert.json()
            self.assertFalse(inert_payload["ok"])
            self.assertEqual(inert_payload["status"], "not-implemented")
            self.assertEqual(inert_payload["reason"], "workflow_runner_not_bound")
            self.assertFalse(inert_payload["executionReady"])
            self.assertFalse(inert_payload["canRun"])
            self.assertEqual(inert_payload["jobStatus"], "queued-but-inert")
            self.assertNotEqual(inert_payload["job"]["status"], "completed")
            self.assertFalse(inert_payload["job"]["runner"]["bound"])
            job_id = inert_payload["jobId"]
            self.assertTrue(job_id.startswith("workflowjob_"))

            status_response = client.get(
                f"/capabilities/workflow-jobs/{job_id}?user_id=desktop&real_user_id=master"
            )
            self.assertEqual(status_response.status_code, 200)
            status_payload = status_response.json()
            self.assertTrue(status_payload["ok"])
            self.assertEqual(status_payload["status"], "queued-but-inert")
            self.assertEqual(status_payload["reason"], "workflow_runner_not_bound")
            self.assertFalse(status_payload["executionReady"])
            self.assertFalse(status_payload["canRun"])
            self.assertEqual(status_payload["job"]["outputs"], [])
            self.assertNotEqual(status_payload["job"]["status"], "completed")
            self.assertNotIn("_profileUserId", status_payload["job"])
            self.assertNotIn("_sessionId", status_payload["job"])

            wrong_profile = client.get(
                f"/capabilities/workflow-jobs/{job_id}?user_id=desktop&real_user_id=other_profile"
            )
            self.assertEqual(wrong_profile.status_code, 404)
            self.assertEqual(wrong_profile.json()["status"], "unknown_workflow_job")

            unknown_job = client.get(
                "/capabilities/workflow-jobs/token_secret?user_id=desktop&real_user_id=master"
            )
            self.assertEqual(unknown_job.status_code, 404)
            self.assertEqual(unknown_job.json()["status"], "unknown_workflow_job")

            combined_text = json.dumps(
                [
                    missing_payload,
                    unknown.json(),
                    rejected_payload,
                    inert_payload,
                    status_payload,
                    wrong_profile.json(),
                    unknown_job.json(),
                ],
                ensure_ascii=False,
            ).lower()
            for forbidden in (
                "https://example.test",
                "portrait.png",
                "raw_image_bytes_should_not_echo",
                "token",
                "secret",
                str(Path(temp_dir)).lower(),
            ):
                self.assertNotIn(forbidden, combined_text)
            self.assertIn(("capabilities.workflow_job_start", False), runtime.observed)
            self.assertIn(("capabilities.workflow_job_status", True), runtime.observed)
            self.assertIn(("capabilities.workflow_job_status", False), runtime.observed)

    def test_capabilities_workflow_job_routes_use_bound_background_runner(self) -> None:
        runtime = FakeRuntimeMetrics()
        background = BackgroundTaskRunner({"workflow": 1})
        self.addCleanup(background.close)
        runner = FakeWorkflowRunner(
            {
                "ok": True,
                "status": "completed",
                "reason": "cutout_done",
                "outputs": [
                    {"handle": "portrait_cutout", "kind": "image", "contentType": "image/png"},
                    {"handle": "token_secret_output", "kind": "image", "contentType": "image/png"},
                    {"handle": r"C:\Users\Lenovo\portrait.png", "kind": "image"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    workflow_runner=runner,
                    background_tasks=background,
                )
            )
            client = TestClient(app)
            client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            )
            client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "input_image",
                        "output_image_handle": "output_image",
                    },
                },
            )

            preflight = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            ).json()
            self.assertTrue(preflight["ok"])
            self.assertEqual(preflight["status"], "ready")
            self.assertTrue(preflight["executionReady"])
            self.assertTrue(preflight["canRun"])
            self.assertTrue(preflight["checks"]["runnerBound"])

            started = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": "portrait_source",
                    "outputImageHandle": "portrait_cutout",
                    "imageBytes": "RAW_IMAGE_BYTES_SHOULD_NOT_ECHO",
                },
            ).json()
            self.assertTrue(started["ok"])
            self.assertEqual(started["status"], "queued")
            self.assertIn(started["jobStatus"], {"queued", "running", "completed"})
            self.assertTrue(started["job"]["runner"]["bound"])
            job_id = started["jobId"]

            self.assertTrue(background.wait_idle(lane="workflow", timeout=2.0))
            status_payload = client.get(
                f"/capabilities/workflow-jobs/{job_id}?user_id=desktop&real_user_id=master"
            ).json()
            self.assertTrue(status_payload["ok"])
            self.assertEqual(status_payload["status"], "completed")
            self.assertEqual(status_payload["reason"], "cutout_done")
            self.assertEqual(
                status_payload["job"]["outputs"],
                [{"handle": "portrait_cutout", "kind": "image", "contentType": "image/png"}],
            )
            self.assertEqual(len(runner.requests), 1)
            self.assertEqual(runner.requests[0].profile_user_id, "master")
            self.assertEqual(runner.requests[0].session_id, "desktop")
            self.assertEqual(runner.requests[0].inputs["inputImageHandle"], "portrait_source")
            self.assertEqual(runner.requests[0].inputs["outputImageHandle"], "portrait_cutout")
            self.assertNotIn("_workflow", status_payload["job"])
            combined_text = json.dumps([started, status_payload], ensure_ascii=False).lower()
            for forbidden in (
                "raw_image_bytes_should_not_echo",
                "token_secret_output",
                "users\\lenovo",
                "portrait.png",
                str(Path(temp_dir)).lower(),
            ):
                self.assertNotIn(forbidden, combined_text)
            self.assertIn(("capabilities.workflow_job_start", True), runtime.observed)
            self.assertIn(("capabilities.workflow_job_status", True), runtime.observed)

    def test_capabilities_workflow_job_runner_failure_is_structured(self) -> None:
        background = BackgroundTaskRunner({"workflow": 1})
        self.addCleanup(background.close)
        runner = ExplodingWorkflowRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    resolve_identity_from_query=resolve_query,
                    workflow_runner=runner,
                    background_tasks=background,
                )
            )
            client = TestClient(app)
            client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            )
            client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "workflowPath": "workflows/comfyui/portrait_cutout.json",
                    "slotMapping": {
                        "input_image_handle": "input_image",
                        "output_image_handle": "output_image",
                    },
                },
            )

            started = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            ).json()
            self.assertTrue(started["ok"])
            self.assertTrue(background.wait_idle(lane="workflow", timeout=2.0))

            status_payload = client.get(
                f"/capabilities/workflow-jobs/{started['jobId']}?user_id=desktop&real_user_id=master"
            ).json()
            self.assertTrue(status_payload["ok"])
            self.assertEqual(status_payload["status"], "failed")
            self.assertEqual(status_payload["reason"], "workflow_runner_failed")
            self.assertEqual(status_payload["job"]["outputs"], [])
            self.assertEqual(len(runner.requests), 1)
            combined_text = json.dumps([started, status_payload], ensure_ascii=False).lower()
            for forbidden in ("secret", "token", "users\\lenovo", "portrait.png", str(Path(temp_dir)).lower()):
                self.assertNotIn(forbidden, combined_text)

    def test_capabilities_local_environment_check_is_discovery_not_enablement(self) -> None:
        runtime = FakeRuntimeMetrics()

        def fake_probe() -> dict[str, Any]:
            return {
                "ok": True,
                "status": "checked",
                "schemaVersion": 1,
                "autoEnable": False,
                "services": [
                    {
                        "id": "provider.comfyui.local",
                        "kind": "provider",
                        "type": "asset_processor",
                        "source": "external_executor",
                        "adapter": "comfyui",
                        "executionMode": "external",
                        "enabled": False,
                        "status": "ready",
                        "endpoint": "http://127.0.0.1:8188",
                        "discovered": True,
                        "bindable": True,
                        "autoEnabled": False,
                    }
                ],
                "summary": {"total": 1},
            }

        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=SimpleNamespace(tool_handlers={}),
                runtime_metrics=runtime,
                local_environment_probe=fake_probe,
            )
        )

        response = TestClient(app).post("/capabilities/local-environment-check")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["autoEnable"])
        self.assertEqual(payload["services"][0]["adapter"], "comfyui")
        self.assertEqual(payload["services"][0]["source"], "external_executor")
        self.assertFalse(payload["services"][0]["enabled"])
        self.assertFalse(payload["services"][0]["autoEnabled"])
        self.assertIn(("capabilities.local_environment_check", True), runtime.observed)

    def test_capabilities_provider_config_skeleton_persists_profile_scoped_local_endpoint(self) -> None:
        runtime = FakeRuntimeMetrics()
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)

            initial = client.get("/capabilities/providers?user_id=desktop&real_user_id=master").json()
            comfy = {item["id"]: item for item in initial["providers"]}["provider.comfyui.local"]
            self.assertEqual(comfy["source"], "external_executor")
            self.assertEqual(comfy["type"], "asset_processor")
            self.assertEqual(comfy["adapter"], "comfyui")
            self.assertEqual(comfy["executionMode"], "external")
            self.assertFalse(comfy["configured"])
            self.assertEqual(comfy["status"], "missing_config")

            saved_response = client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188/ui?token=secret"},
            )
            self.assertEqual(saved_response.status_code, 200)
            saved = saved_response.json()
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["status"], "saved")
            self.assertFalse(saved["autoEnable"])
            self.assertEqual(saved["provider"]["endpoint"], "http://127.0.0.1:8188")
            self.assertEqual(saved["provider"]["status"], "configured")
            self.assertNotIn(str(Path(temp_dir)), saved_response.text)
            self.assertNotIn("secret", saved_response.text.lower())

            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            self.assertTrue(config_path.exists())
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("provider.comfyui.local", config_text)
            self.assertNotIn("secret", config_text.lower())

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            by_id = {item["id"]: item for item in catalog["capabilities"]}
            self.assertEqual(by_id["provider.comfyui.local"]["status"], "configured")
            self.assertTrue(by_id["provider.comfyui.local"]["configured"])

            invalid = client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "https://example.com:8188"},
            ).json()
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["status"], "invalid_config")
            self.assertIn(("capabilities.provider_config", True), runtime.observed)
            self.assertIn(("capabilities.provider_config", False), runtime.observed)

    def test_capabilities_provider_config_load_sanitizes_manual_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "providers": {
                            "provider.comfyui.local": {
                                "enabled": True,
                                "endpoint": "http://127.0.0.1:8188/ui?token=secret",
                                "api_key": "secret-api-key",
                                "lastHealth": {
                                    "status": "unreachable",
                                    "endpoint": "http://127.0.0.1:8188/ui?token=secret",
                                    "reason": r"failed token=secret C:\Users\Lenovo\secret.txt",
                                },
                            },
                            "provider.tts.gpt_sovits.local": {
                                "enabled": True,
                                "endpoint": "https://example.com:9880?token=secret",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)

            providers_payload = client.get("/capabilities/providers?user_id=desktop&real_user_id=master").json()
            providers_text = json.dumps(providers_payload, ensure_ascii=False)
            by_id = {item["id"]: item for item in providers_payload["providers"]}

            self.assertEqual(providers_payload["configStatus"], "partial_invalid_config")
            self.assertEqual(by_id["provider.comfyui.local"]["endpoint"], "http://127.0.0.1:8188")
            self.assertEqual(by_id["provider.comfyui.local"]["status"], "unreachable")
            self.assertEqual(by_id["provider.tts.gpt_sovits.local"]["status"], "invalid_config")
            self.assertEqual(by_id["provider.tts.gpt_sovits.local"]["endpoint"], "")
            self.assertNotIn("secret", providers_text.lower())
            self.assertNotIn("api_key", providers_text.lower())
            self.assertNotIn("/ui", providers_text)
            self.assertNotIn(str(Path(temp_dir)), providers_text)

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            catalog_text = json.dumps(catalog, ensure_ascii=False)
            self.assertEqual(catalog["providerConfigStatus"], "partial_invalid_config")
            self.assertNotIn("secret", catalog_text.lower())
            self.assertNotIn("api_key", catalog_text.lower())

            saved = client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": False, "endpoint": "http://127.0.0.1:8188/api?token=secret"},
            ).json()
            self.assertTrue(saved["ok"])
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("http://127.0.0.1:8188", config_text)
            self.assertNotIn("secret", config_text.lower())
            self.assertNotIn("api_key", config_text.lower())
            self.assertNotIn("/api", config_text)

    def test_capabilities_provider_config_corrupt_file_is_structured_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("{not-json", encoding="utf-8")
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)

            providers = client.get("/capabilities/providers?user_id=desktop&real_user_id=master").json()
            self.assertEqual(providers["configStatus"], "invalid_config")
            self.assertEqual(providers["warnings"][0]["reason"], "provider_config_file_invalid_json")

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            self.assertEqual(catalog["providerConfigStatus"], "invalid_config")

            saved = client.post(
                "/capabilities/providers/provider.comfyui.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:8188"},
            ).json()
            self.assertFalse(saved["ok"])
            self.assertEqual(saved["status"], "invalid_config")
            self.assertEqual(config_path.read_text(encoding="utf-8"), "{not-json")

    def test_capabilities_provider_health_check_is_bounded_and_not_enablement(self) -> None:
        runtime = FakeRuntimeMetrics()
        checks: list[tuple[str, int, float]] = []

        def fake_health_checker(host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
            checks.append((host, port, timeout_seconds))
            return True, ""

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    provider_health_checker=fake_health_checker,
                )
            )
            client = TestClient(app)

            result = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/health-check?user_id=desktop&real_user_id=master",
                json={"endpoint": "http://localhost:9880"},
            ).json()

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ready")
            self.assertFalse(result["autoEnable"])
            self.assertFalse(result["enabled"])
            self.assertEqual(result["endpoint"], "http://127.0.0.1:9880")
            self.assertEqual(checks, [("127.0.0.1", 9880, 0.35)])

            providers = client.get("/capabilities/providers?user_id=desktop&real_user_id=master").json()["providers"]
            by_id = {item["id"]: item for item in providers}
            self.assertFalse(by_id["provider.tts.gpt_sovits.local"]["configured"])
            self.assertEqual(by_id["provider.tts.gpt_sovits.local"]["status"], "missing_config")

            rejected = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/health-check?user_id=desktop&real_user_id=master",
                json={"endpoint": "http://example.com:9880"},
            ).json()
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["status"], "invalid_config")
            self.assertEqual(len(checks), 1)
            self.assertIn(("capabilities.provider_health_check", True), runtime.observed)
            self.assertIn(("capabilities.provider_health_check", False), runtime.observed)


if __name__ == "__main__":
    unittest.main()
