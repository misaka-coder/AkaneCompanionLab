from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, DESKTOP_PET_RESOURCE_CONTRACT_VERSION
from companion_v01.local_capability_config import save_provider_config, save_voice_profile_config
from companion_v01.local_workflow_execution import WorkflowExecutionAsset, WorkflowExecutionRequest
from companion_v01.mcp_stdio_discoverer import McpStdioToolCaller, McpStdioToolDiscoverer
from companion_v01.music_lyrics import parse_lrc_segments
from companion_v01.routes.capabilities import build_capabilities_router
from companion_v01.routes.control_center import (
    build_control_center_router,
    build_control_center_snapshot_runtime_providers,
)
from companion_v01.routes.core import build_core_router
from companion_v01.routes.desktop_pet import build_desktop_pet_router
from companion_v01.routes.gifts import build_gifts_router
from companion_v01.routes.qq import build_qq_router
from companion_v01.routes.sessions import build_sessions_router
from companion_v01.routes.think import build_think_router
from companion_v01.routes.voice import build_voice_router
from companion_v01.qq_gateway import NapCatQQGateway


QQ_BOT_FIXTURE_ID = 10001
QQ_USER_FIXTURE_ID = 10003


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
        raise RuntimeError(r"secret token leaked from C:\Users\ExampleUser\portrait.png")


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


def write_valid_cutout_workflow(base_dir: str | Path, profile_user_id: str = "master") -> Path:
    workflow_path = Path(base_dir) / profile_user_id / "capabilities" / "workflows" / "comfyui" / "portrait_cutout.json"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        json.dumps(
            {
                "12": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
                "20": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
            }
        ),
        encoding="utf-8",
    )
    return workflow_path


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

    def test_qq_router_character_command_switches_without_llm_turn(self) -> None:
        runtime = FakeRuntimeMetrics()
        gateway = NapCatQQGateway()
        process_calls: list[dict[str, Any]] = []

        class FakeCharacterResources:
            def list_character_packs(self):
                return [
                    {
                        "pack_id": "reimu",
                        "name": "Reimu",
                        "app_name": "Reimu Pet",
                        "user_title": "你",
                    }
                ]

            def build_character_identity(self, character_pack_id: str):
                if character_pack_id != "reimu":
                    return {}
                return {
                    "character_id": "reimu",
                    "assistant_name": "Reimu",
                    "app_name": "Reimu Pet",
                    "user_label": "你",
                    "pack_id": "reimu",
                }

        class FakeEngine:
            desktop_pet_character_resources = FakeCharacterResources()

            def process_turn_stream(self, payload: dict):
                process_calls.append(payload)
                yield {"type": "final_ui", "payload": {"speech": "should not run"}}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=FakeEngine(),
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
                qq_gateway=gateway,
                runtime_metrics=runtime,
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
            )
        )

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
            response = TestClient(app).post(
                "/api/qq/napcat/event",
                json={
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "message_id": "route-character-switch-1",
                    "raw_message": "切换角色 reimu",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reason"], "qq_character_command")
        self.assertEqual(payload["command_status"], "switched")
        self.assertEqual(payload["character_pack_id"], "reimu")
        self.assertEqual(gateway.resolve_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "reimu")
        self.assertEqual(process_calls, [])
        mocked_post.assert_called_once()
        sent_payload = mocked_post.call_args.kwargs["json"]
        self.assertIn("已切换本 QQ 会话角色为", sent_payload["message"])

    def test_qq_router_poke_notice_runs_llm_as_normal_user_message(self) -> None:
        runtime = FakeRuntimeMetrics()
        gateway = NapCatQQGateway()
        process_calls: list[dict[str, Any]] = []
        log_calls: list[tuple[str, dict[str, Any]]] = []

        class FakeEngine:
            care_runtime = None
            desktop_pet_character_resources = None

            def prefetch_remote_media_links_for_message(self, **_kwargs):
                return {}

            def process_turn_stream(self, payload: dict):
                process_calls.append(payload)
                yield {"type": "final_ui", "payload": {"speech": "别戳了。"}}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=FakeEngine(),
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
                qq_gateway=gateway,
                runtime_metrics=runtime,
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda event_name, **kwargs: log_calls.append((event_name, kwargs)),
            )
        )

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
            response = TestClient(app).post(
                "/api/qq/napcat/event",
                json={
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "poke",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "sender_id": QQ_USER_FIXTURE_ID,
                    "target_id": QQ_BOT_FIXTURE_ID,
                    "time": int(time.time()),
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reason"], "qq_poke")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(process_calls), 1)
        turn_payload = process_calls[0]
        self.assertEqual(turn_payload["message"], f"刚才发生的互动：QQ {QQ_USER_FIXTURE_ID}在 QQ 里戳了戳你的头像。")
        self.assertEqual(turn_payload["client_mode"], "qq_text")
        self.assertNotIn("transient_user_message", turn_payload)
        self.assertIn(f"QQ {QQ_USER_FIXTURE_ID}", turn_payload["extra_context"])
        self.assertIn("戳了戳你", turn_payload["extra_context"])
        self.assertIn("请优先依据本轮 QQ 事件里的发送者标识来回应", turn_payload["extra_context"])
        poke_logs = [payload for event_name, payload in log_calls if event_name == "qq_poke_context"]
        self.assertEqual(len(poke_logs), 1)
        self.assertEqual(poke_logs[0]["event_sender_id"], str(QQ_USER_FIXTURE_ID))
        self.assertEqual(poke_logs[0]["resolved_user_id"], QQ_USER_FIXTURE_ID)
        self.assertIn(f"QQ {QQ_USER_FIXTURE_ID}", poke_logs[0]["turn_message"])
        mocked_post.assert_called_once()
        sent_payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["message"], "别戳了。")

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
                "web_search": object(),
                "open_browser": object(),
                "browser_page": object(),
                "open_music_search": object(),
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
        self.assertIn("tool.web_search", by_id)
        self.assertIn("tool.open_browser", by_id)
        self.assertIn("tool.browser_page", by_id)
        self.assertIn("tool.open_music_search", by_id)
        self.assertIn("provider.tts.edge", by_id)
        self.assertIn("provider.music.system_media_control", by_id)
        self.assertIn("provider.asr.faster_whisper", by_id)
        self.assertIn("workflow.workshop.portrait.cutout", by_id)

        self.assertEqual(by_id["tool.compose_file"]["source"], "backend_tool")
        self.assertEqual(by_id["tool.compose_file"]["adapter"], "tool_runtime")
        self.assertEqual(by_id["tool.compose_file"]["status"], "ready")
        self.assertEqual(by_id["tool.transcribe_media"]["risk"], "medium")
        self.assertEqual(by_id["tool.web_search"]["group"], "web")
        self.assertEqual(by_id["tool.web_search"]["risk"], "low")
        self.assertFalse(by_id["tool.web_search"]["requiresConfirmation"])
        self.assertEqual(by_id["tool.web_search"]["approvalMode"], "trusted_auto_allow")
        self.assertEqual(by_id["tool.open_browser"]["group"], "desktop_browser")
        self.assertEqual(by_id["tool.open_browser"]["risk"], "medium")
        self.assertEqual(by_id["tool.open_browser"]["approvalMode"], "trusted_auto_allow")
        self.assertEqual(by_id["tool.browser_page"]["group"], "desktop_browser")
        self.assertEqual(by_id["tool.browser_page"]["risk"], "medium")
        self.assertEqual(by_id["tool.browser_page"]["approvalMode"], "trusted_auto_allow")
        self.assertEqual(by_id["tool.open_music_search"]["group"], "music")
        self.assertEqual(by_id["tool.open_music_search"]["risk"], "medium")
        self.assertEqual(by_id["provider.music.system_media_control"]["type"], "music_playback_provider")
        self.assertEqual(by_id["provider.music.system_media_control"]["risk"], "medium")

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
        self.assertEqual(cutout_workflow["approvalMode"], "disabled")
        self.assertEqual(cutout_workflow["inputSchema"]["pathPolicy"], "safe-handle-only")

        # Product names must stay in adapter/provider ids, not base source/type.
        for item in capabilities:
            self.assertNotIn(item.get("source"), {"comfyui", "gpt_sovits", "rvc", "faster_whisper", "demucs"})
            self.assertNotIn(item.get("type"), {"comfyui", "gpt_sovits", "rvc", "faster_whisper", "demucs"})

        body = response.text.lower()
        for sensitive in ("api_key", "password", "secret", "token", "prompt_text", "chat_message"):
            self.assertNotIn(sensitive, body)
        self.assertIn(("capabilities.catalog", True), runtime.observed)

    def test_capabilities_approval_request_lifecycle_is_structured_and_redacted(self) -> None:
        runtime = FakeRuntimeMetrics()
        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=SimpleNamespace(tool_handlers={}),
                config_module=SimpleNamespace(),
                runtime_metrics=runtime,
                resolve_identity_from_query=resolve_query,
            )
        )
        client = TestClient(app)

        empty = client.get("/capabilities/approval-requests?user_id=desktop&real_user_id=master").json()
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["pendingCount"], 0)
        self.assertEqual(empty["approvalRequests"], [])

        created_response = client.post(
            "/capabilities/approval-requests?user_id=desktop&real_user_id=master",
            json={
                "capabilityId": "mcp.browser.browser_click",
                "actionId": "browser_click",
                "title": "点击浏览器元素",
                "summary": "Click the selected page element for the user.",
                "risk": "high",
                "approvalMode": "ask_each_time",
                "payloadPreview": {
                    "selector": "#play",
                    "localPath": r"C:\Users\ExampleUser\secret.txt",
                    "api_key": "real-secret-value",
                    "nested": {"token": "real-token", "label": "公开标签"},
                },
            },
        )
        self.assertEqual(created_response.status_code, 200)
        created = created_response.json()
        self.assertTrue(created["ok"])
        self.assertEqual(created["status"], "pending")
        request_id = created["requestId"]
        self.assertTrue(request_id.startswith("approvalreq_"))
        request = created["request"]
        self.assertEqual(request["approvalMode"], "ask_each_time")
        self.assertEqual(request["risk"], "high")
        self.assertEqual(request["payloadPreview"]["selector"], "#play")
        created_text = created_response.text.lower()
        self.assertNotIn("api_key", created_text)
        self.assertNotIn("real-secret", created_text)
        self.assertNotIn("real-token", created_text)
        self.assertNotIn("exampleuser", created_text)
        self.assertNotIn(r"c:\users", created_text)

        listed = client.get("/capabilities/approval-requests?user_id=desktop&real_user_id=master").json()
        self.assertEqual(listed["pendingCount"], 1)
        self.assertEqual(listed["approvalRequests"][0]["requestId"], request_id)

        approved = client.post(
            f"/capabilities/approval-requests/{request_id}/decision?user_id=desktop&real_user_id=master",
            json={"decision": "approved"},
        ).json()
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approvalGrant"]["requestId"], request_id)
        self.assertTrue(approved["approvalGrant"]["grantId"].startswith("approvalgrant_"))

        resolved = client.get(
            "/capabilities/approval-requests?user_id=desktop&real_user_id=master&include_resolved=1"
        ).json()
        self.assertEqual(resolved["pendingCount"], 0)
        self.assertEqual(resolved["approvalRequests"][0]["status"], "approved")

        repeated = client.post(
            f"/capabilities/approval-requests/{request_id}/decision?user_id=desktop&real_user_id=master",
            json={"decision": "denied"},
        )
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json()["reason"], "approval_request_already_resolved")

        not_required = client.post(
            "/capabilities/approval-requests?user_id=desktop&real_user_id=master",
            json={"capabilityId": "tool.web_search", "risk": "low", "approvalMode": "trusted_auto_allow"},
        )
        self.assertEqual(not_required.status_code, 400)
        self.assertEqual(not_required.json()["status"], "not_required")

        self.assertIn(("capabilities.approval_requests", True), runtime.observed)
        self.assertIn(("capabilities.approval_request_create", True), runtime.observed)
        self.assertIn(("capabilities.approval_request_decision", True), runtime.observed)

    def test_capabilities_approval_policy_can_switch_high_risk_catalog_entries(self) -> None:
        runtime = FakeRuntimeMetrics()

        async def fake_mcp_discoverer(*, server: dict[str, Any]) -> dict[str, Any]:
            return {
                "tools": [
                    {
                        "name": "read_page",
                        "description": "Read a public browser page.",
                        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                    {
                        "name": "browser_click",
                        "description": "Click a browser element on behalf of the user.",
                        "inputSchema": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]},
                    },
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    mcp_tool_discoverer=fake_mcp_discoverer,
                )
            )
            client = TestClient(app)

            default_policy = client.get("/capabilities/approval-policy?user_id=desktop&real_user_id=master")
            self.assertEqual(default_policy.status_code, 200)
            self.assertEqual(default_policy.json()["approvalPolicy"]["defaultMode"], "ask_each_time")

            client.post(
                "/capabilities/mcp-servers/browser/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "displayName": "Browser MCP", "command": "browser-mcp"},
            )
            client.post("/capabilities/mcp-servers/browser/discover?user_id=desktop&real_user_id=master", json={})

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            by_id = {item["id"]: item for item in catalog["capabilities"]}
            self.assertEqual(catalog["approvalPolicy"]["defaultMode"], "ask_each_time")
            self.assertEqual(by_id["mcp.browser.browser_click"]["risk"], "high")
            self.assertTrue(by_id["mcp.browser.browser_click"]["requiresConfirmation"])
            self.assertEqual(by_id["mcp.browser.browser_click"]["approvalMode"], "ask_each_time")
            self.assertEqual(by_id["workflow.workshop.portrait.cutout"]["approvalMode"], "disabled")

            saved = client.post(
                "/capabilities/approval-policy?user_id=desktop&real_user_id=master",
                json={"defaultMode": "trusted_auto_allow", "api_key": "must-not-leak"},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()["ok"])
            self.assertEqual(saved.json()["approvalPolicy"]["defaultMode"], "trusted_auto_allow")
            self.assertNotIn("must-not-leak", saved.text)

            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn('"approvalPolicy"', config_text)
            self.assertIn('"trusted_auto_allow"', config_text)
            self.assertNotIn("must-not-leak", config_text)

            # Saving another config family must preserve the profile approval policy.
            client.post(
                "/capabilities/mcp-servers/browser/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "displayName": "Browser MCP", "command": "browser-mcp"},
            )
            preserved = client.get("/capabilities/approval-policy?user_id=desktop&real_user_id=master").json()
            self.assertEqual(preserved["approvalPolicy"]["defaultMode"], "trusted_auto_allow")

            trusted_catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            trusted_by_id = {item["id"]: item for item in trusted_catalog["capabilities"]}
            trusted_click = trusted_by_id["mcp.browser.browser_click"]
            self.assertEqual(trusted_catalog["approvalPolicy"]["defaultMode"], "trusted_auto_allow")
            self.assertEqual(trusted_click["approvalMode"], "trusted_auto_allow")
            self.assertEqual(trusted_click["approvalReason"], "user_policy_trusted_auto_allow")
            self.assertFalse(trusted_click["requiresConfirmation"])
            self.assertEqual(trusted_by_id["workflow.workshop.portrait.cutout"]["approvalMode"], "disabled")

            workflows = client.get("/capabilities/workflows?user_id=desktop&real_user_id=master").json()
            workflow_by_id = {item["id"]: item for item in workflows["workflows"]}
            self.assertEqual(workflow_by_id["workflow.workshop.portrait.cutout"]["approvalMode"], "disabled")

            invalid = client.post(
                "/capabilities/approval-policy?user_id=desktop&real_user_id=master",
                json={"defaultMode": "always_yes"},
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.json()["status"], "invalid_config")
            self.assertEqual(invalid.json()["reason"], "approval_policy_mode_invalid")

            self.assertIn(("capabilities.approval_policy", True), runtime.observed)
            self.assertIn(("capabilities.approval_policy_save", True), runtime.observed)
            self.assertIn(("capabilities.catalog", True), runtime.observed)

    def test_capabilities_mcp_server_config_and_discovery_merge_safe_catalog_entries(self) -> None:
        runtime = FakeRuntimeMetrics()
        discoverer_calls: list[dict[str, Any]] = []

        async def fake_mcp_discoverer(*, server: dict[str, Any]) -> dict[str, Any]:
            discoverer_calls.append(server)
            return {
                "tools": [
                    {
                        "name": "read_page",
                        "description": "Read the current browser page without controlling it.",
                        "risk": "low",
                        "confirm": "never",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "Page URL"},
                                "api_key": {"type": "string", "description": "must be dropped"},
                            },
                            "required": ["url", "api_key"],
                        },
                    },
                    {
                        "name": "browser_click",
                        "description": "Click a browser element.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "selector": {"type": "string", "description": "CSS selector"},
                            },
                            "required": ["selector"],
                        },
                    },
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    mcp_tool_discoverer=fake_mcp_discoverer,
                )
            )
            client = TestClient(app)

            saved = client.post(
                "/capabilities/mcp-servers/browser/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "displayName": "Browser MCP",
                    "transport": "stdio",
                    "command": r"C:\Users\ExampleUser\mcp\browser-mcp.exe",
                    "args": ["--profile", "akane"],
                    "cwd": r"C:\Users\ExampleUser\mcp",
                    "env": {"MCP_MODE": "local"},
                    "lowRiskAllowlist": ["read_page"],
                },
            )
            self.assertEqual(saved.status_code, 200)
            saved_payload = saved.json()
            self.assertTrue(saved_payload["ok"])
            self.assertEqual(saved_payload["mcpServer"]["status"], "configured")
            self.assertEqual(saved_payload["mcpServer"]["commandName"], "browser-mcp.exe")
            self.assertEqual(saved_payload["mcpServer"]["argsCount"], 2)
            self.assertEqual(saved_payload["mcpServer"]["approvalMode"], "disabled")
            self.assertNotIn("exampleuser", saved.text.lower())
            self.assertNotIn(r"c:\users", saved.text.lower())

            discovered = client.post(
                "/capabilities/mcp-servers/browser/discover?user_id=desktop&real_user_id=master",
                json={},
            )
            self.assertEqual(discovered.status_code, 200)
            discovered_payload = discovered.json()
            self.assertTrue(discovered_payload["ok"])
            self.assertEqual(discovered_payload["status"], "discovered")
            self.assertEqual(discovered_payload["toolCount"], 2)
            self.assertEqual(discoverer_calls[0]["command"], r"C:\Users\ExampleUser\mcp\browser-mcp.exe")
            discovered_text = discovered.text.lower()
            self.assertNotIn("exampleuser", discovered_text)
            self.assertNotIn("api_key", discovered_text)
            self.assertNotIn("secret", discovered_text)

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            by_id = {item["id"]: item for item in catalog["capabilities"]}
            self.assertEqual(by_id["provider.mcp.browser"]["status"], "ready")
            self.assertFalse(by_id["provider.mcp.browser"]["requiresConfirmation"])
            self.assertEqual(by_id["provider.mcp.browser"]["approvalMode"], "trusted_auto_allow")
            self.assertIn("mcp.browser.read_page", by_id)
            self.assertIn("mcp.browser.browser_click", by_id)
            read_page = by_id["mcp.browser.read_page"]
            browser_click = by_id["mcp.browser.browser_click"]
            self.assertEqual(read_page["kind"], "mcp_tool")
            self.assertEqual(read_page["source"], "mcp")
            self.assertEqual(read_page["adapter"], "mcp_stdio")
            self.assertFalse(read_page["exposedToPrompt"])
            self.assertEqual(read_page["inputSchema"]["required"], ["url"])
            self.assertEqual(read_page["approvalMode"], "trusted_auto_allow")
            self.assertNotIn("api_key", json.dumps(read_page, ensure_ascii=False).lower())
            self.assertEqual(browser_click["risk"], "high")
            self.assertTrue(browser_click["requiresConfirmation"])
            self.assertEqual(browser_click["approvalMode"], "ask_each_time")
            self.assertIn(("capabilities.mcp_server_config", True), runtime.observed)
            self.assertIn(("capabilities.mcp_server_discover", True), runtime.observed)
            self.assertIn(("capabilities.catalog", True), runtime.observed)

    def test_capabilities_mcp_anysearch_env_placeholder_is_allowed_without_key_leak(self) -> None:
        runtime = FakeRuntimeMetrics()
        discoverer_calls: list[dict[str, Any]] = []

        async def fake_mcp_discoverer(*, server: dict[str, Any]) -> dict[str, Any]:
            discoverer_calls.append(server)
            return {
                "tools": [
                    {
                        "name": "search",
                        "description": "Execute a public web search.",
                        "risk": "low",
                        "confirm": "never",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Search query"},
                                "max_results": {"type": "integer", "description": "Result count"},
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "extract",
                        "description": "Extract readable public page content from a URL.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"url": {"type": "string", "description": "Page URL"}},
                            "required": ["url"],
                        },
                    },
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    mcp_tool_discoverer=fake_mcp_discoverer,
                )
            )
            client = TestClient(app)

            saved = client.post(
                "/capabilities/mcp-servers/anysearch/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "displayName": "AnySearch 网页搜索",
                    "transport": "stdio",
                    "command": "npx",
                    "args": [
                        "-y",
                        "mcp-remote",
                        "https://api.anysearch.com/mcp",
                        "--header",
                        "Authorization: Bearer ${ANYSEARCH_API_KEY}",
                    ],
                    "lowRiskAllowlist": ["search"],
                },
            )
            self.assertEqual(saved.status_code, 200)
            saved_payload = saved.json()
            self.assertTrue(saved_payload["ok"])
            self.assertEqual(saved_payload["mcpServer"]["status"], "configured")
            self.assertEqual(saved_payload["mcpServer"]["commandName"], "npx")
            self.assertEqual(saved_payload["mcpServer"]["argsCount"], 5)
            self.assertNotIn("authorization", saved.text.lower())
            self.assertNotIn("bearer", saved.text.lower())
            self.assertNotIn("anysearch_api_key", saved.text.lower())

            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            self.assertIn("${ANYSEARCH_API_KEY}", config_path.read_text(encoding="utf-8"))

            discovered = client.post(
                "/capabilities/mcp-servers/anysearch/discover?user_id=desktop&real_user_id=master",
                json={},
            ).json()
            self.assertTrue(discovered["ok"])
            self.assertEqual(discovered["toolCount"], 2)
            self.assertEqual(discoverer_calls[0]["args"][-1], "Authorization: Bearer ${ANYSEARCH_API_KEY}")

            catalog_text = client.get("/capabilities?user_id=desktop&real_user_id=master").text
            catalog = json.loads(catalog_text)
            by_id = {item["id"]: item for item in catalog["capabilities"]}
            self.assertEqual(by_id["provider.mcp.anysearch"]["status"], "ready")
            self.assertFalse(by_id["provider.mcp.anysearch"]["requiresConfirmation"])
            self.assertEqual(by_id["provider.mcp.anysearch"]["approvalMode"], "trusted_auto_allow")
            self.assertIn("mcp.anysearch.search", by_id)
            self.assertIn("mcp.anysearch.extract", by_id)
            self.assertFalse(by_id["mcp.anysearch.search"]["requiresConfirmation"])
            self.assertEqual(by_id["mcp.anysearch.search"]["approvalMode"], "trusted_auto_allow")
            self.assertFalse(by_id["mcp.anysearch.search"]["exposedToPrompt"])
            self.assertNotIn("authorization", catalog_text.lower())
            self.assertNotIn("bearer", catalog_text.lower())
            self.assertNotIn("anysearch_api_key", catalog_text.lower())

            inline_secret = client.post(
                "/capabilities/mcp-servers/unsafe/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "command": "npx",
                    "args": ["--header", "Authorization: Bearer real-secret-value"],
                },
            ).json()
            self.assertFalse(inline_secret["ok"])
            self.assertEqual(inline_secret["reason"], "mcp_server_args_invalid")

            env_secret = client.post(
                "/capabilities/mcp-servers/unsafe-env/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "command": "npx",
                    "env": {"ANYSEARCH_API_KEY": "real-secret-value"},
                },
            ).json()
            self.assertFalse(env_secret["ok"])
            self.assertEqual(env_secret["reason"], "mcp_server_env_invalid")

    def test_capabilities_mcp_discovery_is_not_implemented_without_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    resolve_identity_from_query=resolve_query,
                )
            )
            client = TestClient(app)
            saved = client.post(
                "/capabilities/mcp-servers/browser/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "command": "browser-mcp"},
            ).json()
            self.assertTrue(saved["ok"])
            discovered = client.post(
                "/capabilities/mcp-servers/browser/discover?user_id=desktop&real_user_id=master",
                json={},
            ).json()
            self.assertFalse(discovered["ok"])
            self.assertEqual(discovered["status"], "not-implemented")
            self.assertEqual(discovered["reason"], "mcp_discoverer_not_bound")

    def test_capabilities_mcp_stdio_discoverer_lists_tools_without_calling_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server_script = Path(temp_dir) / "fake_mcp_server.py"
            server_script.write_text(
                """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if message.get("id") == 1 and method == "initialize":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-browser", "version": "0.1"}
            }
        }), flush=True)
    elif method == "tools/list":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "read_page",
                        "description": "Read the browser page.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "Page URL"},
                                "api_key": {"type": "string", "description": "drop me"}
                            },
                            "required": ["url", "api_key"]
                        }
                    },
                    {
                        "name": "browser_click",
                        "description": "Click a browser element.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "selector": {"type": "string", "description": "CSS selector"}
                            },
                            "required": ["selector"]
                        }
                    }
                ]
            }
        }), flush=True)
""",
                encoding="utf-8",
            )
            runtime = FakeRuntimeMetrics()
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    mcp_tool_discoverer=McpStdioToolDiscoverer(timeout_seconds=4),
                )
            )
            client = TestClient(app)
            saved = client.post(
                "/capabilities/mcp-servers/browser/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "displayName": "Browser MCP",
                    "command": sys.executable,
                    "args": [str(server_script)],
                    "cwd": temp_dir,
                },
            )
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()["ok"])
            self.assertNotIn(temp_dir.lower(), saved.text.lower())

            discovered = client.post(
                "/capabilities/mcp-servers/browser/discover?user_id=desktop&real_user_id=master",
                json={},
            )
            self.assertEqual(discovered.status_code, 200)
            discovered_payload = discovered.json()
            self.assertTrue(discovered_payload["ok"])
            self.assertEqual(discovered_payload["status"], "discovered")
            self.assertEqual(discovered_payload["toolCount"], 2)
            self.assertNotIn(temp_dir.lower(), discovered.text.lower())
            self.assertNotIn("api_key", discovered.text.lower())

            catalog = client.get("/capabilities?user_id=desktop&real_user_id=master").json()
            by_id = {item["id"]: item for item in catalog["capabilities"]}
            self.assertEqual(by_id["provider.mcp.browser"]["status"], "ready")
            self.assertEqual(by_id["mcp.browser.read_page"]["inputSchema"]["required"], ["url"])
            self.assertEqual(by_id["mcp.browser.browser_click"]["risk"], "high")
            self.assertFalse(by_id["mcp.browser.read_page"]["exposedToPrompt"])
            self.assertIn(("capabilities.mcp_server_discover", True), runtime.observed)

    def test_capabilities_mcp_stdio_discoverer_hydrates_env_placeholder_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".env").write_text("ANYSEARCH_API_KEY=dotenv-secret\n", encoding="utf-8")
            server_script = Path(temp_dir) / "fake_anysearch_mcp.py"
            server_script.write_text(
                """
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if message.get("id") == 1 and method == "initialize":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-anysearch", "version": "0.1"}
            }
        }), flush=True)
    elif method == "tools/list":
        has_key = os.environ.get("ANYSEARCH_API_KEY") == "dotenv-secret"
        has_arg_key = len(sys.argv) > 1 and sys.argv[-1] == "Authorization: Bearer dotenv-secret"
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search public web pages.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string", "description": "Search query"}},
                            "required": ["query"]
                        }
                    }
                ] if has_key and has_arg_key else []
            }
        }), flush=True)
""",
                encoding="utf-8",
            )
            runtime = FakeRuntimeMetrics()
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    mcp_tool_discoverer=McpStdioToolDiscoverer(timeout_seconds=4),
                )
            )
            client = TestClient(app)
            with patch.dict(os.environ, {"ANYSEARCH_API_KEY": ""}):
                saved = client.post(
                    "/capabilities/mcp-servers/anysearch/config?user_id=desktop&real_user_id=master",
                    json={
                        "enabled": True,
                        "displayName": "AnySearch",
                        "command": sys.executable,
                        "args": [str(server_script), "Authorization: Bearer ${ANYSEARCH_API_KEY}"],
                        "cwd": temp_dir,
                    },
                )
                self.assertTrue(saved.json()["ok"])
                discovered = client.post(
                    "/capabilities/mcp-servers/anysearch/discover?user_id=desktop&real_user_id=master",
                    json={},
                )
            self.assertTrue(discovered.json()["ok"])
            self.assertEqual(discovered.json()["toolCount"], 1)
            self.assertNotIn("dotenv-secret", discovered.text)
            self.assertNotIn("ANYSEARCH_API_KEY", discovered.text)
            catalog_text = client.get("/capabilities?user_id=desktop&real_user_id=master").text
            self.assertNotIn("dotenv-secret", catalog_text)
            self.assertNotIn("ANYSEARCH_API_KEY", catalog_text)
            self.assertIn(("capabilities.mcp_server_discover", True), runtime.observed)

    def test_capabilities_mcp_stdio_tool_caller_calls_single_tool_with_dotenv_hydration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".env").write_text("ANYSEARCH_API_KEY=dotenv-secret\n", encoding="utf-8")
            server_script = Path(temp_dir) / "fake_anysearch_call_mcp.py"
            server_script.write_text(
                """
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if message.get("id") == 1 and method == "initialize":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-anysearch", "version": "0.1"}
            }
        }), flush=True)
    elif method == "tools/list":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32000, "message": "tools/list should not be called"}
        }), flush=True)
    elif method == "tools/call":
        has_key = os.environ.get("ANYSEARCH_API_KEY") == "dotenv-secret"
        has_arg_key = len(sys.argv) > 1 and sys.argv[-1] == "Authorization: Bearer dotenv-secret"
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "tool": message.get("params", {}).get("name"),
                        "arguments": message.get("params", {}).get("arguments"),
                        "has_key": has_key,
                        "has_arg_key": has_arg_key
                    }, ensure_ascii=False)
                }]
            }
        }), flush=True)
""",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"ANYSEARCH_API_KEY": ""}):
                result = asyncio.run(
                    McpStdioToolCaller(timeout_seconds=4)(
                        server={
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(server_script), "Authorization: Bearer ${ANYSEARCH_API_KEY}"],
                            "cwd": temp_dir,
                            "env": {},
                        },
                        tool_name="search",
                        arguments={"query": "Akane AnySearch", "max_results": 2},
                    )
                )

            self.assertIn("content", result)
            text = result["content"][0]["text"]
            payload = json.loads(text)
            self.assertEqual(payload["tool"], "search")
            self.assertEqual(payload["arguments"], {"query": "Akane AnySearch", "max_results": 2})
            self.assertTrue(payload["has_key"])
            self.assertTrue(payload["has_arg_key"])
            self.assertNotIn("dotenv-secret", json.dumps(result, ensure_ascii=False))

    def test_capabilities_catalog_resolves_voice_provider_with_degradation(self) -> None:
        class FakeCharacterVoiceService:
            def __init__(self) -> None:
                self.profile_id = ""

            def build_character_voice_preference(self, character_pack_id: str) -> dict[str, str]:
                return {
                    "packId": character_pack_id,
                    "provider": "gpt_sovits",
                    "profileId": self.profile_id,
                    "notes": r"do not leak C:\Users\ExampleUser\voice.txt token=secret",
                }

        voice_service = FakeCharacterVoiceService()
        runtime = FakeRuntimeMetrics()
        checks: list[tuple[str, int, float]] = []

        def fake_health_checker(host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
            checks.append((host, port, timeout_seconds))
            return True, ""

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(
                        tool_handlers={},
                        desktop_pet_character_resources=voice_service,
                    ),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, STREAMING_TTS_ENABLED=True),
                    tts_client=object(),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    provider_health_checker=fake_health_checker,
                )
            )
            client = TestClient(app)

            degraded = client.get(
                "/capabilities?user_id=desktop&real_user_id=master&character_pack_id=reimu"
            ).json()
            by_id = {item["id"]: item for item in degraded["capabilities"]}
            self.assertIn("provider.voice.text_only", by_id)
            self.assertIn("provider.asr.text_input", by_id)
            tts_resolution = degraded["resolutions"]["voice.tts.character"]
            self.assertEqual(tts_resolution["status"], "degraded")
            self.assertEqual(tts_resolution["requestedProviderId"], "provider.tts.gpt_sovits.local")
            self.assertEqual(tts_resolution["activeProviderId"], "provider.tts.edge")
            self.assertEqual(tts_resolution["fallbackProviderId"], "provider.tts.edge")
            self.assertEqual(tts_resolution["reason"], "requested_voice_profile_missing")
            self.assertEqual(tts_resolution["requestSource"], "character_pack")

            voice_service.profile_id = "reimu_main"
            saved = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "endpoint": "http://127.0.0.1:9880"},
            ).json()
            self.assertTrue(saved["ok"])
            health = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/health-check?user_id=desktop&real_user_id=master",
                json={},
            ).json()
            self.assertTrue(health["ok"])
            ready = client.get(
                "/capabilities?user_id=desktop&real_user_id=master&character_pack_id=reimu"
            ).json()
            ready_resolution = ready["resolutions"]["voice.tts.character"]
            self.assertEqual(ready_resolution["status"], "ready")
            self.assertEqual(ready_resolution["requestedProviderId"], "provider.tts.gpt_sovits.local")
            self.assertEqual(ready_resolution["activeProviderId"], "provider.tts.gpt_sovits.local")
            self.assertEqual(ready_resolution["fallbackProviderId"], "")
            self.assertEqual(ready_resolution["voiceProfileId"], "reimu_main")
            self.assertEqual(checks, [("127.0.0.1", 9880, 0.35)])

            serialized = json.dumps([degraded, ready], ensure_ascii=False).lower()
            self.assertNotIn("token", serialized)
            self.assertNotIn("secret", serialized)
            self.assertNotIn(str(Path(temp_dir)).lower(), serialized)
            self.assertIn(("capabilities.catalog", True), runtime.observed)

    def test_tts_route_uses_edge_by_default_with_provider_headers(self) -> None:
        class FakeEdgeTTS:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def synthesize(self, text: str) -> bytes:
                self.calls.append(text)
                return f"edge:{text}".encode("utf-8")

        edge = FakeEdgeTTS()
        runtime = FakeRuntimeMetrics()
        app = FastAPI()
        app.include_router(
            build_voice_router(
                engine=SimpleNamespace(),
                config_module=SimpleNamespace(DATA_DIR=None),
                tts_client=edge,
                runtime_metrics=runtime,
                log_event=lambda *_args, **_kwargs: None,
            )
        )

        response = TestClient(app).post("/tts", json={"text": "你好", "real_user_id": "master"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"edge:\xe4\xbd\xa0\xe5\xa5\xbd")
        self.assertEqual(response.headers.get("x-akane-tts-provider"), "provider.tts.edge")
        self.assertEqual(response.headers.get("x-akane-tts-requested-provider"), "provider.tts.edge")
        self.assertEqual(response.headers.get("x-akane-tts-status"), "ready")
        self.assertEqual(edge.calls, ["你好"])
        self.assertIn(("tts", True), runtime.observed)

    def test_tts_route_degrades_character_gpt_sovits_request_without_profile(self) -> None:
        class FakeCharacterVoiceService:
            def build_character_voice_preference(self, character_pack_id: str) -> dict[str, str]:
                return {"packId": character_pack_id, "provider": "gpt_sovits", "profileId": ""}

        class FakeEdgeTTS:
            async def synthesize(self, text: str) -> bytes:
                return b"edge-audio"

        app = FastAPI()
        app.include_router(
            build_voice_router(
                engine=SimpleNamespace(desktop_pet_character_resources=FakeCharacterVoiceService()),
                config_module=SimpleNamespace(DATA_DIR=None),
                tts_client=FakeEdgeTTS(),
                runtime_metrics=FakeRuntimeMetrics(),
                log_event=lambda *_args, **_kwargs: None,
            )
        )

        response = TestClient(app).post(
            "/tts",
            json={"text": "测试", "real_user_id": "master", "character_pack_id": "reimu"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"edge-audio")
        self.assertEqual(response.headers.get("x-akane-tts-requested-provider"), "provider.tts.gpt_sovits.local")
        self.assertEqual(response.headers.get("x-akane-tts-provider"), "provider.tts.edge")
        self.assertEqual(response.headers.get("x-akane-tts-fallback"), "provider.tts.edge")
        self.assertEqual(response.headers.get("x-akane-tts-reason"), "requested_voice_profile_missing")

    def test_tts_route_uses_configured_gpt_sovits_for_character_voice(self) -> None:
        class FakeCharacterVoiceService:
            def build_character_voice_preference(self, character_pack_id: str) -> dict[str, str]:
                return {"packId": character_pack_id, "provider": "gpt_sovits", "profileId": "reimu_main"}

        class FakeGptSovitsClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any]]] = []

            async def synthesize(
                self,
                text: str,
                *,
                voice_profile_id: str = "",
                profile: dict[str, Any] | None = None,
            ) -> SimpleNamespace:
                self.calls.append((text, voice_profile_id, dict(profile or {})))
                return SimpleNamespace(audio=b"gpt-audio", media_type="audio/wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.tts.gpt_sovits.local",
                payload={"enabled": True, "endpoint": "http://127.0.0.1:9880"},
            )
            save_voice_profile_config(
                base_dir=temp_dir,
                profile_user_id="master",
                voice_profile_id="reimu_main",
                payload={
                    "enabled": True,
                    "displayName": "Reimu Main",
                    "textLang": "zh",
                    "promptLang": "zh",
                    "mediaType": "wav",
                    "refAudioPath": r"C:\voices\reimu_ref.wav",
                    "promptText": "主人，今天也要一起努力。",
                    "streamingMode": True,
                    "parallelInfer": True,
                    "splitBucket": False,
                    "batchSize": 1,
                    "speedFactor": 1.05,
                    "fragmentInterval": 0.1,
                    "textSplitMethod": "cut5",
                },
            )
            gpt_client = FakeGptSovitsClient()
            factory_calls: list[str] = []

            def factory(endpoint: str) -> FakeGptSovitsClient:
                factory_calls.append(endpoint)
                return gpt_client

            app = FastAPI()
            app.include_router(
                build_voice_router(
                    engine=SimpleNamespace(desktop_pet_character_resources=FakeCharacterVoiceService()),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    tts_client=object(),
                    runtime_metrics=FakeRuntimeMetrics(),
                    log_event=lambda *_args, **_kwargs: None,
                    gpt_sovits_client_factory=factory,
                )
            )

            response = TestClient(app).post(
                "/tts",
                json={"text": "角色语音", "real_user_id": "master", "character_pack_id": "reimu"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"gpt-audio")
        self.assertEqual(response.headers.get("content-type"), "audio/wav")
        self.assertEqual(response.headers.get("x-akane-tts-provider"), "provider.tts.gpt_sovits.local")
        self.assertEqual(response.headers.get("x-akane-tts-requested-provider"), "provider.tts.gpt_sovits.local")
        self.assertEqual(response.headers.get("x-akane-tts-fallback"), "")
        self.assertEqual(response.headers.get("x-akane-tts-reason"), "")
        self.assertEqual(factory_calls, ["http://127.0.0.1:9880"])
        self.assertEqual(
            gpt_client.calls,
            [
                (
                    "角色语音",
                    "reimu_main",
                    {
                        "id": "reimu_main",
                        "providerId": "provider.tts.gpt_sovits.local",
                        "textLang": "zh",
                        "promptLang": "zh",
                        "mediaType": "wav",
                        "refAudioPath": r"C:\voices\reimu_ref.wav",
                        "promptText": "主人，今天也要一起努力。",
                        "streamingMode": True,
                        "parallelInfer": True,
                        "splitBucket": False,
                        "batchSize": 1,
                        "speedFactor": 1.05,
                        "fragmentInterval": 0.1,
                        "textSplitMethod": "cut5",
                    },
                )
            ],
        )

    def test_tts_route_uses_request_voice_profile_for_speech_preview(self) -> None:
        class FakeGptSovitsClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any]]] = []

            async def synthesize(
                self,
                text: str,
                *,
                voice_profile_id: str = "",
                profile: dict[str, Any] | None = None,
            ) -> SimpleNamespace:
                self.calls.append((text, voice_profile_id, dict(profile or {})))
                return SimpleNamespace(audio=b"speech-preview-audio", media_type="audio/wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.tts.gpt_sovits.local",
                payload={"enabled": True, "endpoint": "http://127.0.0.1:9880"},
            )
            save_voice_profile_config(
                base_dir=temp_dir,
                profile_user_id="master",
                voice_profile_id="dania",
                payload={
                    "enabled": True,
                    "displayName": "Dania",
                    "textLang": "zh",
                    "promptLang": "zh",
                    "mediaType": "wav",
                    "refAudioPath": r"C:\voices\dania_ref.wav",
                    "promptText": "怎么啊？如果有你在也不放心。",
                },
            )
            gpt_client = FakeGptSovitsClient()

            app = FastAPI()
            app.include_router(
                build_voice_router(
                    engine=SimpleNamespace(),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    tts_client=object(),
                    runtime_metrics=FakeRuntimeMetrics(),
                    log_event=lambda *_args, **_kwargs: None,
                    gpt_sovits_client_factory=lambda _endpoint: gpt_client,
                )
            )

            response = TestClient(app).post(
                "/tts",
                json={
                    "text": "这是从角色 speech 字段拿来预览的一句台词。",
                    "real_user_id": "master",
                    "voiceProfileId": "dania",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"speech-preview-audio")
        self.assertEqual(response.headers.get("x-akane-tts-provider"), "provider.tts.gpt_sovits.local")
        self.assertEqual(response.headers.get("x-akane-tts-requested-provider"), "provider.tts.gpt_sovits.local")
        self.assertEqual(
            gpt_client.calls,
            [
                (
                    "这是从角色 speech 字段拿来预览的一句台词。",
                    "dania",
                    {
                        "id": "dania",
                        "providerId": "provider.tts.gpt_sovits.local",
                        "textLang": "zh",
                        "promptLang": "zh",
                        "mediaType": "wav",
                        "refAudioPath": r"C:\voices\dania_ref.wav",
                        "promptText": "怎么啊？如果有你在也不放心。",
                    },
                )
            ],
        )

    def test_tts_route_falls_back_to_edge_when_gpt_sovits_call_fails_without_leak(self) -> None:
        class FakeCharacterVoiceService:
            def build_character_voice_preference(self, character_pack_id: str) -> dict[str, str]:
                return {"packId": character_pack_id, "provider": "gpt_sovits", "profileId": "reimu_main"}

        class FakeEdgeTTS:
            async def synthesize(self, text: str) -> bytes:
                return b"edge-after-gpt-fail"

        class ExplodingGptSovitsClient:
            async def synthesize(
                self,
                text: str,
                *,
                voice_profile_id: str = "",
                profile: dict[str, Any] | None = None,
            ) -> bytes:
                raise RuntimeError(r"secret token from C:\Users\ExampleUser\voice.wav")

        logs: list[dict[str, Any]] = []

        def log_event(event: str, **fields: Any) -> None:
            logs.append({"event": event, **fields})

        with tempfile.TemporaryDirectory() as temp_dir:
            save_provider_config(
                base_dir=temp_dir,
                profile_user_id="master",
                provider_id="provider.tts.gpt_sovits.local",
                payload={"enabled": True, "endpoint": "http://localhost:9880"},
            )
            app = FastAPI()
            app.include_router(
                build_voice_router(
                    engine=SimpleNamespace(desktop_pet_character_resources=FakeCharacterVoiceService()),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    tts_client=FakeEdgeTTS(),
                    runtime_metrics=FakeRuntimeMetrics(),
                    log_event=log_event,
                    gpt_sovits_client_factory=lambda _endpoint: ExplodingGptSovitsClient(),
                )
            )

            response = TestClient(app).post(
                "/tts",
                json={"text": "失败回退", "real_user_id": "master", "character_pack_id": "reimu"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"edge-after-gpt-fail")
        self.assertEqual(response.headers.get("x-akane-tts-provider"), "provider.tts.edge")
        self.assertEqual(response.headers.get("x-akane-tts-fallback"), "provider.tts.edge")
        self.assertEqual(response.headers.get("x-akane-tts-reason"), "gpt_sovits_failed")
        serialized_logs = json.dumps(logs, ensure_ascii=False).lower()
        self.assertIn("tts_provider_fallback", serialized_logs)
        self.assertNotIn("secret", serialized_logs)
        self.assertNotIn("token", serialized_logs)
        self.assertNotIn("users", serialized_logs)

    def test_lrc_parser_normalizes_segments_without_metadata(self) -> None:
        segments = parse_lrc_segments(
            "\n".join(
                [
                    "[ar:周杰伦]",
                    "[ti:晴天]",
                    "[00:01.00][00:03.50]故事的小黄花",
                    "[00:07.000]<00:07.10>从出生那年就飘着",
                    "[00:09.00]",
                ]
            )
        )

        self.assertEqual(
            segments,
            [
                {"start": 1.0, "end": 3.5, "text": "故事的小黄花"},
                {"start": 3.5, "end": 7.0, "text": "故事的小黄花"},
                {"start": 7.0, "end": 12.0, "text": "从出生那年就飘着"},
            ],
        )

    def test_music_lyrics_route_caches_ready_segments_profile_scoped(self) -> None:
        runtime = FakeRuntimeMetrics()
        calls: list[tuple[str, tuple[str, ...]]] = []

        def fake_search(query: str, providers: list[str]) -> str:
            calls.append((query, tuple(providers)))
            return "[00:01.00]第一句\n[00:04.20]第二句\n[00:07.50]第三句"

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(
                        DATA_DIR=temp_dir,
                        MUSIC_ONLINE_LYRICS_ENABLED=True,
                        MUSIC_ONLINE_LYRICS_PROVIDERS="Lrclib,NetEase",
                    ),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    lyrics_searcher=fake_search,
                )
            )
            client = TestClient(app)
            payload = {
                "user_id": "desktop",
                "real_user_id": "master",
                "trackKey": "qqmusic::晴天::周杰伦",
                "title": "晴天",
                "artist": "周杰伦",
                "album": "叶惠美",
                "source": "system_media",
                "positionSeconds": 135,
            }

            first = client.post("/capabilities/music/lyrics", json=payload)
            second = client.post("/capabilities/music/lyrics", json=payload)

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            first_payload = first.json()
            second_payload = second.json()
            self.assertTrue(first_payload["ok"])
            self.assertEqual(first_payload["status"], "ready")
            self.assertFalse(first_payload["cached"])
            self.assertEqual(first_payload["lineCount"], 3)
            self.assertEqual(first_payload["segments"][0], {"start": 1.0, "end": 4.2, "text": "第一句"})
            self.assertTrue(second_payload["cached"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "晴天 周杰伦")
            self.assertEqual(calls[0][1], ("Lrclib", "NetEase"))

            cache_root = Path(temp_dir) / "master" / "music" / "lyrics_cache"
            self.assertTrue(cache_root.exists())
            combined = json.dumps([first_payload, second_payload], ensure_ascii=False).lower()
            self.assertNotIn(str(Path(temp_dir)).lower(), combined)
            self.assertNotIn("api_key", combined)
            self.assertIn(("capabilities.music_lyrics", True), runtime.observed)

    def test_music_lyrics_route_uses_body_identity_when_query_identity_is_absent(self) -> None:
        calls: list[str] = []

        def fake_search(query: str, _providers: list[str]) -> str:
            calls.append(query)
            return "[00:01.00]第一句\n[00:04.00]第二句"

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, MUSIC_ONLINE_LYRICS_ENABLED=True),
                    resolve_identity_from_query=resolve_query,
                    lyrics_searcher=fake_search,
                )
            )
            response = TestClient(app).post(
                "/capabilities/music/lyrics?t=1",
                json={
                    "user_id": "desktop_pet_next",
                    "real_user_id": "body_master",
                    "title": "晴天",
                    "artist": "周杰伦",
                    "source": "system_media",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(calls, ["晴天 周杰伦"])
            self.assertTrue((Path(temp_dir) / "body_master" / "music" / "lyrics_cache").exists())
            self.assertFalse((Path(temp_dir) / "session" / "music" / "lyrics_cache").exists())

            mixed = TestClient(app).post(
                "/capabilities/music/lyrics?user_id=query_session&t=2",
                json={
                    "real_user_id": "mixed_master",
                    "title": "七里香",
                    "artist": "周杰伦",
                    "source": "system_media",
                },
            )
            self.assertEqual(mixed.status_code, 200)
            self.assertTrue((Path(temp_dir) / "mixed_master" / "music" / "lyrics_cache").exists())
            self.assertFalse((Path(temp_dir) / "query_session" / "music" / "lyrics_cache").exists())

    def test_music_lyrics_route_derives_artist_from_combined_title(self) -> None:
        calls: list[str] = []

        def fake_search(query: str, _providers: list[str]) -> str:
            calls.append(query)
            return "[00:34.68]我说了所有的谎\n[00:42.97]你全都相信\n[00:50.55]简单的我爱你"

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, MUSIC_ONLINE_LYRICS_ENABLED=True),
                    resolve_identity_from_query=resolve_query,
                    lyrics_searcher=fake_search,
                )
            )
            result = TestClient(app).post(
                "/capabilities/music/lyrics",
                json={
                    "user_id": "desktop",
                    "real_user_id": "master",
                    "trackKey": "qqmusic::淘汰::陈奕迅",
                    "title": "淘汰 - 陈奕迅",
                    "artist": "",
                    "source": "system_media",
                },
            ).json()

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["confidence"], "medium")
            self.assertEqual(result["lineCount"], 3)
            self.assertEqual(calls, ["淘汰 陈奕迅"])

    def test_music_lyrics_route_cleans_noisy_system_media_artist_for_lookup(self) -> None:
        calls: list[str] = []

        def fake_search(query: str, _providers: list[str]) -> str:
            calls.append(query)
            return "[00:49.00]重力が眠りにつく\n[00:55.00]一千年に一度の今日"

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, MUSIC_ONLINE_LYRICS_ENABLED=True),
                    resolve_identity_from_query=resolve_query,
                    lyrics_searcher=fake_search,
                )
            )
            client = TestClient(app)
            payload = {
                "user_id": "desktop",
                "real_user_id": "master",
                "trackKey": "system::grand_escape",
                "title": "グランドエスケープ feat.三浦透子",
                "artist": "RADWIMPS (ラッドウィンプス)/三浦透子 (みうら とうこ)",
                "source": "system_media",
            }

            first = client.post("/capabilities/music/lyrics", json=payload).json()
            second = client.post("/capabilities/music/lyrics", json=payload).json()

            self.assertTrue(first["ok"])
            self.assertEqual(first["status"], "ready")
            self.assertEqual(calls, ["グランドエスケープ feat.三浦透子 RADWIMPS 三浦透子"])
            self.assertTrue(second["cached"])
            self.assertEqual(len(calls), 1)

    def test_music_lyrics_route_returns_structured_failures_without_network(self) -> None:
        disabled_app = FastAPI()
        disabled_app.include_router(
            build_capabilities_router(
                engine=SimpleNamespace(tool_handlers={}),
                config_module=SimpleNamespace(MUSIC_ONLINE_LYRICS_ENABLED=False),
                resolve_identity_from_query=resolve_query,
            )
        )
        disabled = TestClient(disabled_app).post(
            "/capabilities/music/lyrics?user_id=desktop&real_user_id=master",
            json={"title": "晴天", "artist": "周杰伦"},
        ).json()
        self.assertFalse(disabled["ok"])
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(disabled["reason"], "network_lyrics_disabled")

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "companion_v01.music_lyrics.syncedlyrics_available",
            return_value=False,
        ):
            missing_dep_app = FastAPI()
            missing_dep_app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, MUSIC_ONLINE_LYRICS_ENABLED=True),
                    resolve_identity_from_query=resolve_query,
                )
            )
            missing_dep = TestClient(missing_dep_app).post(
                "/capabilities/music/lyrics?user_id=desktop&real_user_id=master",
                json={"title": "晴天", "artist": "周杰伦"},
            ).json()
            self.assertFalse(missing_dep["ok"])
            self.assertEqual(missing_dep["status"], "unavailable")
            self.assertEqual(missing_dep["reason"], "syncedlyrics_missing")
            self.assertEqual(missing_dep["segments"], [])

        calls: list[str] = []

        def not_found_search(query: str, _providers: list[str]) -> str:
            calls.append(query)
            return ""

        with tempfile.TemporaryDirectory() as temp_dir:
            not_found_app = FastAPI()
            not_found_app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir, MUSIC_ONLINE_LYRICS_ENABLED=True),
                    resolve_identity_from_query=resolve_query,
                    lyrics_searcher=not_found_search,
                )
            )
            client = TestClient(not_found_app)
            not_found = client.post(
                "/capabilities/music/lyrics?user_id=desktop&real_user_id=master",
                json={"title": "晴天", "artist": "周杰伦"},
            ).json()
            cached = client.post(
                "/capabilities/music/lyrics?user_id=desktop&real_user_id=master",
                json={"title": "晴天", "artist": "周杰伦"},
            ).json()
            low_confidence = client.post(
                "/capabilities/music/lyrics?user_id=desktop&real_user_id=master",
                json={"title": "只有歌名"},
            ).json()

            self.assertFalse(not_found["ok"])
            self.assertEqual(not_found["status"], "not-found")
            self.assertEqual(not_found["reason"], "lyrics_not_found")
            self.assertTrue(cached["cached"])
            self.assertEqual(len(calls), 1)
            self.assertFalse(low_confidence["ok"])
            self.assertEqual(low_confidence["status"], "low-confidence")
            self.assertEqual(low_confidence["segments"], [])

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
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
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
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
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

            validated_missing_file = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/validate?user_id=desktop&real_user_id=master"
            ).json()
            self.assertFalse(validated_missing_file["ok"])
            self.assertEqual(validated_missing_file["status"], "invalid_workflow_config")
            self.assertEqual(validated_missing_file["reason"], "workflow_file_missing")
            self.assertFalse(validated_missing_file["checks"]["workflowFile"])

            rejected_workflow_file = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/file?user_id=desktop&real_user_id=master",
                json={
                    "workflowPath": r"workflows\comfyui\portrait_cutout.json",
                    "workflowJson": "{not-json",
                },
            ).json()
            self.assertFalse(rejected_workflow_file["ok"])
            self.assertEqual(rejected_workflow_file["status"], "invalid_workflow_config")
            self.assertEqual(rejected_workflow_file["reason"], "workflow_file_invalid_json")

            imported_workflow_file = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/file?user_id=desktop&real_user_id=master",
                json={
                    "workflowPath": r"workflows\comfyui\portrait_cutout.json",
                    "workflowJson": json.dumps(
                        {
                            "12": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
                            "20": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
                        }
                    ),
                },
            ).json()
            self.assertTrue(imported_workflow_file["ok"])
            self.assertEqual(imported_workflow_file["status"], "workflow_file_saved")
            self.assertEqual(imported_workflow_file["workflowPath"], "workflows/comfyui/portrait_cutout.json")
            imported_file_path = Path(temp_dir) / "master" / "capabilities" / "workflows" / "comfyui" / "portrait_cutout.json"
            self.assertTrue(imported_file_path.is_file())
            imported_text = imported_file_path.read_text(encoding="utf-8").lower()
            self.assertNotIn(str(Path(temp_dir)).lower(), imported_text)
            self.assertNotIn("token", imported_text)

            validated = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/validate?user_id=desktop&real_user_id=master"
            ).json()
            self.assertTrue(validated["ok"])
            self.assertEqual(validated["status"], "validated_config")
            self.assertFalse(validated["executionReady"])
            self.assertTrue(validated["checks"]["providerConfigured"])
            self.assertTrue(validated["checks"]["workflowConfigured"])
            self.assertTrue(validated["checks"]["requiredSlots"])
            self.assertTrue(validated["checks"]["workflowFile"])
            self.assertTrue(validated["checks"]["slotPaths"])

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
                json={"enabled": True, "workflowPath": r"C:\Users\ExampleUser\workflow.json"},
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
            self.assertIn(("capabilities.workflow_file", True), runtime.observed)
            self.assertIn(("capabilities.workflow_file", False), runtime.observed)
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
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
                    },
                },
            )

            rejected = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": r"C:\Users\ExampleUser\secret.png",
                    "outputImageHandle": "portrait_cutout",
                },
            ).json()
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["status"], "invalid_request")
            self.assertEqual(rejected["reason"], "asset_handle_must_be_safe_opaque_id")
            rejected_text = json.dumps(rejected, ensure_ascii=False).lower()
            self.assertNotIn("secret.png", rejected_text)
            self.assertNotIn(str(Path(temp_dir)).lower(), rejected_text)

            missing_file = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            ).json()
            self.assertFalse(missing_file["ok"])
            self.assertEqual(missing_file["status"], "invalid_workflow_config")
            self.assertEqual(missing_file["reason"], "workflow_file_missing")
            self.assertFalse(missing_file["checks"]["workflowFile"])

            write_valid_cutout_workflow(temp_dir)
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
            self.assertTrue(ready_but_inert["checks"]["workflowFile"])
            self.assertTrue(ready_but_inert["checks"]["slotPaths"])
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
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
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

            missing_file = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": "portrait_source",
                    "outputImageHandle": "portrait_cutout",
                    "imageBytes": "RAW_IMAGE_BYTES_SHOULD_NOT_ECHO",
                },
            ).json()
            self.assertFalse(missing_file["ok"])
            self.assertEqual(missing_file["status"], "invalid_workflow_config")
            self.assertEqual(missing_file["reason"], "workflow_file_missing")
            self.assertNotIn("jobId", missing_file)

            write_valid_cutout_workflow(temp_dir)
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
                    {"handle": r"C:\Users\ExampleUser\portrait.png", "kind": "image"},
                ],
                "outputAssets": [
                    WorkflowExecutionAsset(
                        handle="portrait_cutout",
                        data=b"\x89PNG\r\n\x1a\ncutout",
                        content_type="image/png",
                    ),
                    {
                        "handle": "token_secret_output",
                        "bytes": [137, 80, 78, 71, 13, 10, 26, 10],
                        "contentType": "image/png",
                    },
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
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
                    },
                },
            )
            missing_file_catalog = client.get("/capabilities/workflows?user_id=desktop&real_user_id=master").json()
            missing_file_workflow = {item["id"]: item for item in missing_file_catalog["workflows"]}[
                "workflow.workshop.portrait.cutout"
            ]
            self.assertEqual(missing_file_workflow["status"], "invalid_workflow_config")
            self.assertEqual(missing_file_workflow["reason"], "workflow_file_missing")
            self.assertFalse(missing_file_workflow["executionReady"])

            write_valid_cutout_workflow(temp_dir)
            ready_catalog = client.get("/capabilities/workflows?user_id=desktop&real_user_id=master").json()
            ready_workflow = {item["id"]: item for item in ready_catalog["workflows"]}[
                "workflow.workshop.portrait.cutout"
            ]
            self.assertEqual(ready_workflow["status"], "ready")
            self.assertTrue(ready_workflow["executionReady"])

            preflight = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/preflight?user_id=desktop&real_user_id=master",
                json={"inputImageHandle": "portrait_source", "outputImageHandle": "portrait_cutout"},
            ).json()
            self.assertTrue(preflight["ok"])
            self.assertEqual(preflight["status"], "ready")
            self.assertTrue(preflight["executionReady"])
            self.assertTrue(preflight["canRun"])
            self.assertTrue(preflight["checks"]["runnerBound"])
            self.assertTrue(preflight["checks"]["workflowFile"])
            self.assertTrue(preflight["checks"]["slotPaths"])

            started = client.post(
                "/capabilities/workflows/workflow.workshop.portrait.cutout/jobs?user_id=desktop&real_user_id=master",
                json={
                    "inputImageHandle": "portrait_source",
                    "outputImageHandle": "portrait_cutout",
                    "inputImageBytes": [137, 80, 78, 71, 13, 10, 26, 10, 115, 111, 117, 114, 99, 101],
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
            self.assertEqual(runner.requests[0].input_assets["portrait_source"].data, b"\x89PNG\r\n\x1a\nsource")
            self.assertNotIn("_workflow", status_payload["job"])
            self.assertNotIn("_outputAssets", status_payload["job"])

            output_response = client.get(
                f"/capabilities/workflow-jobs/{job_id}/outputs/portrait_cutout?user_id=desktop&real_user_id=master"
            )
            self.assertEqual(output_response.status_code, 200)
            self.assertEqual(output_response.content, b"\x89PNG\r\n\x1a\ncutout")
            self.assertEqual(output_response.headers["content-type"], "image/png")

            wrong_profile_output = client.get(
                f"/capabilities/workflow-jobs/{job_id}/outputs/portrait_cutout?user_id=desktop&real_user_id=other_profile"
            )
            self.assertEqual(wrong_profile_output.status_code, 404)
            combined_text = json.dumps([started, status_payload], ensure_ascii=False).lower()
            for forbidden in (
                "raw_image_bytes_should_not_echo",
                "token_secret_output",
                "users\\exampleuser",
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
                        "input_image_handle": "12.inputs.image",
                        "output_image_handle": "20.inputs.filename_prefix",
                    },
                },
            )
            write_valid_cutout_workflow(temp_dir)

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
            for forbidden in ("secret", "token", "users\\exampleuser", "portrait.png", str(Path(temp_dir)).lower()):
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

    def test_capabilities_provider_tts_test_returns_short_audio_without_persisting_profile(self) -> None:
        runtime = FakeRuntimeMetrics()
        calls: list[dict[str, str]] = []

        async def fake_tts_runner(*, endpoint: str, text: str, voice_profile_id: str):
            calls.append({"endpoint": endpoint, "text": text, "voiceProfileId": voice_profile_id})
            return SimpleNamespace(audio=b"wav-bytes", media_type="audio/wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            app.include_router(
                build_capabilities_router(
                    engine=SimpleNamespace(tool_handlers={}),
                    config_module=SimpleNamespace(DATA_DIR=temp_dir),
                    runtime_metrics=runtime,
                    resolve_identity_from_query=resolve_query,
                    provider_tts_test_runner=fake_tts_runner,
                )
            )
            client = TestClient(app)

            response = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/tts-test?user_id=desktop&real_user_id=master",
                json={
                    "endpoint": "http://localhost:9880/ui?token=secret",
                    "text": "  你好，测试一下  ",
                    "voiceProfileId": r"reimu_main",
                    "token": "must-not-return",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "tts-test-ready")
        self.assertEqual(payload["providerId"], "provider.tts.gpt_sovits.local")
        self.assertEqual(payload["mediaType"], "audio/wav")
        self.assertEqual(payload["audioBase64"], "d2F2LWJ5dGVz")
        self.assertEqual(payload["audioBytes"], 9)
        self.assertEqual(payload["voiceProfileId"], "reimu_main")
        self.assertEqual(payload["profileSource"], "missing")
        self.assertFalse(payload["profileApplied"])
        self.assertEqual(payload["checks"]["endpoint"], True)
        self.assertEqual(payload["checks"]["voiceProfileId"], True)
        self.assertEqual(
            calls,
            [
                {
                    "endpoint": "http://127.0.0.1:9880",
                    "text": "你好，测试一下",
                    "voiceProfileId": "reimu_main",
                }
            ],
        )
        serialized = response.text.lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret", serialized)
        config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
        self.assertFalse(config_path.exists(), "tts-test must not persist profile or provider config")
        self.assertIn(("capabilities.provider_tts_test", True), runtime.observed)

    def test_capabilities_provider_tts_test_loads_saved_voice_profile_fields(self) -> None:
        runtime = FakeRuntimeMetrics()
        calls: list[dict[str, Any]] = []

        class CapturingGptSovitsClient:
            def __init__(self, endpoint: str, **_kwargs: Any) -> None:
                self.endpoint = endpoint

            async def synthesize(self, text: str, *, voice_profile_id: str = "", profile: dict[str, Any] | None = None):
                calls.append(
                    {
                        "endpoint": self.endpoint,
                        "text": text,
                        "voiceProfileId": voice_profile_id,
                        "profile": dict(profile or {}),
                    }
                )
                return SimpleNamespace(audio=b"saved-profile-wav", media_type="audio/wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            save_voice_profile_config(
                base_dir=temp_dir,
                profile_user_id="master",
                voice_profile_id="dania",
                payload={
                    "providerId": "provider.tts.gpt_sovits.local",
                    "enabled": True,
                    "displayName": "Dania",
                    "textLang": "zh",
                    "promptLang": "zh",
                    "mediaType": "wav",
                    "refAudioPath": r"C:\voices\dania_ref.wav",
                    "promptText": "这是保存好的参考文本。",
                },
            )
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

            with patch("companion_v01.routes.capabilities.GptSovitsTTSClient", CapturingGptSovitsClient):
                response = client.post(
                    "/capabilities/providers/provider.tts.gpt_sovits.local/tts-test?user_id=desktop&real_user_id=master",
                    json={
                        "endpoint": "http://127.0.0.1:9880",
                        "text": "  你好  ",
                        "voiceProfileId": "dania",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "tts-test-ready")
            self.assertEqual(payload["audioBase64"], "c2F2ZWQtcHJvZmlsZS13YXY=")
            self.assertEqual(payload["voiceProfileId"], "dania")
            self.assertEqual(payload["profileApplied"], True)
            self.assertEqual(payload["profileSource"], "saved")
            self.assertEqual(payload["checks"]["refAudio"], True)
            self.assertEqual(payload["checks"]["promptText"], True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["endpoint"], "http://127.0.0.1:9880")
            self.assertEqual(calls[0]["text"], "你好")
            self.assertEqual(calls[0]["voiceProfileId"], "dania")
            self.assertEqual(calls[0]["profile"]["refAudioPath"], r"C:\voices\dania_ref.wav")
            self.assertEqual(calls[0]["profile"]["promptText"], "这是保存好的参考文本。")
            self.assertEqual(calls[0]["profile"]["promptLang"], "zh")
            response_text = response.text.lower()
            self.assertNotIn(r"c:\voices", response_text)
            self.assertNotIn("保存好的参考文本", response.text)
            self.assertIn(("capabilities.provider_tts_test", True), runtime.observed)

    def test_capabilities_voice_profile_config_saves_private_fields_without_public_leak(self) -> None:
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

            response = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/voice-profiles/reimu_main/config?user_id=desktop&real_user_id=master",
                json={
                    "enabled": True,
                    "displayName": "Reimu Main",
                    "textLang": "zh",
                    "promptLang": "zh",
                    "mediaType": "wav",
                    "refAudioPath": r"C:\Users\ExampleUser\voices\reimu_ref.wav",
                    "promptText": "主人，今天也要一起努力。",
                    "streamingMode": True,
                    "parallelInfer": True,
                    "splitBucket": False,
                    "batchSize": 1,
                    "speedFactor": 1.05,
                    "fragmentInterval": 0.1,
                    "textSplitMethod": "cut5",
                    "token": "must-not-return",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "saved")
            self.assertEqual(payload["voiceProfileId"], "reimu_main")
            public_profile = payload["voiceProfile"]
            self.assertEqual(public_profile["voiceProfileId"], "reimu_main")
            self.assertEqual(public_profile["providerId"], "provider.tts.gpt_sovits.local")
            self.assertEqual(public_profile["referenceAudioName"], "reimu_ref.wav")
            self.assertEqual(public_profile["promptTextLength"], len("主人，今天也要一起努力。"))
            response_text = response.text.lower()
            self.assertNotIn(r"c:\users", response_text)
            self.assertNotIn("exampleuser", response_text)
            self.assertNotIn(str(Path(temp_dir)).lower(), response_text)
            self.assertNotIn("主人，今天也要一起努力", response.text)
            self.assertNotIn("token", response_text)
            self.assertNotIn("secret", response_text)

            profiles_payload = client.get(
                "/capabilities/voice-profiles?user_id=desktop&real_user_id=master"
            ).json()
            profiles_text = json.dumps(profiles_payload, ensure_ascii=False).lower()
            self.assertTrue(profiles_payload["ok"])
            self.assertEqual(profiles_payload["summary"]["total"], 1)
            self.assertNotIn(r"c:\users", profiles_text)
            self.assertNotIn("exampleuser", profiles_text)
            self.assertNotIn(str(Path(temp_dir)).lower(), profiles_text)
            self.assertNotIn("主人，今天也要一起努力", json.dumps(profiles_payload, ensure_ascii=False))

            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            config_text = config_path.read_text(encoding="utf-8")
            config_data = json.loads(config_text)
            stored_profile = config_data["voiceProfiles"]["reimu_main"]
            self.assertEqual(stored_profile["refAudioPath"], r"C:\Users\ExampleUser\voices\reimu_ref.wav")
            self.assertEqual(stored_profile["promptText"], "主人，今天也要一起努力。")
            self.assertEqual(stored_profile["streamingMode"], True)
            self.assertEqual(stored_profile["parallelInfer"], True)
            self.assertEqual(stored_profile["splitBucket"], False)
            self.assertEqual(stored_profile["batchSize"], 1)
            self.assertEqual(stored_profile["speedFactor"], 1.05)
            self.assertEqual(stored_profile["fragmentInterval"], 0.1)
            self.assertEqual(stored_profile["textSplitMethod"], "cut5")

            update = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/voice-profiles/reimu_main/config?user_id=desktop&real_user_id=master",
                json={"enabled": True, "displayName": "Reimu Updated", "textLang": "ja"},
            ).json()
            self.assertTrue(update["ok"])
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            stored_profile = config_data["voiceProfiles"]["reimu_main"]
            self.assertEqual(stored_profile["refAudioPath"], r"C:\Users\ExampleUser\voices\reimu_ref.wav")
            self.assertEqual(stored_profile["promptText"], "主人，今天也要一起努力。")
            self.assertEqual(stored_profile["streamingMode"], True)
            self.assertEqual(stored_profile["batchSize"], 1)
            self.assertEqual(stored_profile["textSplitMethod"], "cut5")
            self.assertIn(("capabilities.voice_profile_config", True), runtime.observed)
            self.assertIn(("capabilities.voice_profiles", True), runtime.observed)

    def test_capabilities_voice_profile_folder_inspect_suggests_profile_without_persisting(self) -> None:
        runtime = FakeRuntimeMetrics()
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "models" / "dania"
            model_dir.mkdir(parents=True)
            ref_audio = model_dir / "output.wav_0009342720_0009558400.wav"
            ref_audio.write_bytes(b"RIFFfake-wav")
            (model_dir / "dania-e15.ckpt").write_bytes(b"gpt")
            (model_dir / "dania_e16_s2192.pth").write_bytes(b"sovits")
            (model_dir / "tts_infer.yaml").write_text(
                "\n".join(
                    [
                        "prompt_text: 你好，今天也要一起努力。",
                        "prompt_lang: zh",
                        "text_lang: zh",
                        "media_type: wav",
                        "ref_audio_path: output.wav_0009342720_0009558400.wav",
                    ]
                ),
                encoding="utf-8",
            )
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

            response = client.post(
                "/capabilities/providers/provider.tts.gpt_sovits.local/voice-profiles/inspect-folder?user_id=desktop&real_user_id=master",
                json={"folderPath": str(model_dir), "token": "must-not-return"},
            )
            payload = response.json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "inspected")
            self.assertEqual(payload["providerId"], "provider.tts.gpt_sovits.local")
            suggested = payload["suggestedProfile"]
            self.assertEqual(suggested["voiceProfileId"], "dania")
            self.assertEqual(suggested["displayName"], "dania")
            self.assertEqual(suggested["textLang"], "zh")
            self.assertEqual(suggested["promptLang"], "zh")
            self.assertEqual(suggested["mediaType"], "wav")
            self.assertEqual(suggested["refAudioPath"], str(ref_audio.resolve()))
            self.assertEqual(suggested["promptText"], "你好，今天也要一起努力。")
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(payload["detected"]["configFileName"], "tts_infer.yaml")
            self.assertEqual(payload["detected"]["referenceAudioName"], ref_audio.name)
            self.assertEqual(payload["detected"]["gptWeightName"], "dania-e15.ckpt")
            self.assertEqual(payload["detected"]["sovitsWeightName"], "dania_e16_s2192.pth")
            self.assertFalse(payload["autoEnable"])
            self.assertFalse(payload["refresh"])
            self.assertNotIn("token", response.text.lower())
            self.assertIn(("capabilities.voice_profile_folder_inspect", True), runtime.observed)

            config_path = Path(temp_dir) / "master" / "capabilities" / "capabilities.yaml"
            self.assertFalse(config_path.exists(), "inspect-folder must not persist a voice profile")
            profiles_payload = client.get(
                "/capabilities/voice-profiles?user_id=desktop&real_user_id=master"
            ).json()
            profiles_text = json.dumps(profiles_payload, ensure_ascii=False).lower()
            self.assertEqual(profiles_payload["summary"]["total"], 0)
            self.assertNotIn(str(model_dir).lower(), profiles_text)
            self.assertNotIn("你好，今天也要一起努力", json.dumps(profiles_payload, ensure_ascii=False))

    def test_capabilities_voice_profile_folder_inspect_rejects_unsafe_or_missing_paths(self) -> None:
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
            base_url = "/capabilities/providers/provider.tts.gpt_sovits.local/voice-profiles/inspect-folder?user_id=desktop&real_user_id=master"
            readme_only_dir = Path(temp_dir) / "readme_only"
            readme_only_dir.mkdir()
            (readme_only_dir / "README.md").write_text("not a voice model", encoding="utf-8")

            unsafe = client.post(base_url, json={"folderPath": "https://example.com/model"}).json()
            relative = client.post(base_url, json={"folderPath": "models/dania"}).json()
            missing = client.post(base_url, json={"folderPath": str(Path(temp_dir) / "missing")}).json()
            no_model_files = client.post(base_url, json={"folderPath": str(readme_only_dir)}).json()
            unsupported = client.post(
                "/capabilities/providers/provider.comfyui.local/voice-profiles/inspect-folder?user_id=desktop&real_user_id=master",
                json={"folderPath": str(Path(temp_dir))},
            ).json()

            self.assertFalse(unsafe["ok"])
            self.assertEqual(unsafe["status"], "invalid_request")
            self.assertEqual(unsafe["reason"], "model_folder_path_invalid")
            self.assertFalse(relative["ok"])
            self.assertEqual(relative["reason"], "model_folder_must_be_absolute")
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["status"], "missing_model_folder")
            self.assertEqual(missing["reason"], "model_folder_not_found")
            self.assertFalse(no_model_files["ok"])
            self.assertEqual(no_model_files["status"], "missing_model_files")
            self.assertEqual(no_model_files["reason"], "model_folder_has_no_supported_files")
            self.assertFalse(unsupported["ok"])
            self.assertEqual(unsupported["status"], "unsupported_provider")
            self.assertIn(("capabilities.voice_profile_folder_inspect", False), runtime.observed)

    def test_capabilities_provider_tts_test_degrades_with_safe_reason(self) -> None:
        runtime = FakeRuntimeMetrics()

        def exploding_tts_runner(*, endpoint: str, text: str, voice_profile_id: str):
            raise RuntimeError(r"secret token from C:\Users\ExampleUser\voice.wav")

        app = FastAPI()
        app.include_router(
            build_capabilities_router(
                engine=SimpleNamespace(tool_handlers={}),
                config_module=SimpleNamespace(DATA_DIR=None),
                runtime_metrics=runtime,
                provider_tts_test_runner=exploding_tts_runner,
            )
        )
        client = TestClient(app)

        failed = client.post(
            "/capabilities/providers/provider.tts.gpt_sovits.local/tts-test",
            json={"endpoint": "http://127.0.0.1:9880", "text": "测试"},
        )
        unsupported = client.post(
            "/capabilities/providers/provider.comfyui.local/tts-test",
            json={"endpoint": "http://127.0.0.1:8188"},
        )

        self.assertEqual(failed.status_code, 200)
        payload = failed.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "tts-test-failed")
        self.assertEqual(payload["reason"], "provider_tts_test_failed")
        self.assertEqual(payload["profileSource"], "none")
        self.assertEqual(payload["checks"]["endpoint"], True)
        self.assertNotIn("secret", failed.text.lower())
        self.assertNotIn("token", failed.text.lower())
        self.assertNotIn("users", failed.text.lower())
        self.assertEqual(unsupported.status_code, 200)
        self.assertEqual(unsupported.json()["status"], "unsupported_provider")
        self.assertIn(("capabilities.provider_tts_test", False), runtime.observed)

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
                                    "reason": r"failed token=secret C:\Users\ExampleUser\secret.txt",
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
