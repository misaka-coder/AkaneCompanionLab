from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.artifact_broker import ArtifactBroker
from companion_v01.capability_adapters import McpStdioCapabilityAdapter
from companion_v01.capability_registry import (
    CapabilityRegistry,
    CapabilitySelection,
    CapabilitySnapshot,
    ExecutorBroker,
    ServerLocalOfferIndex,
)
from companion_v01.client_protocol import ClientMode
from companion_v01.routes.desktop_pet import build_desktop_pet_router
from companion_v01.routes.capabilities import _run_bound_workflow_job
from companion_v01.task_worker import TaskWorkerService
from companion_v01.tool_invocation import TOOL_EXECUTION_RECEIPT_FIELD, ToolInvocation
from companion_v01.tool_orchestration_engine import (
    execute_tool_invocation,
    normalize_tool_invocation,
    validate_tool_invocation,
)
from companion_v01.tool_runtime import AdapterCapabilityToolHandler, ToolExecutionResult


class _Metrics:
    def observe_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Handler:
    def __init__(self, name: str, *, ready: bool = True) -> None:
        self.tool_type = name
        self.ready = ready
        self.execute_calls = 0

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "enabled": self.ready,
            "status": "ready" if self.ready else "unavailable",
        }

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        return dict(value)

    def execute(self, *, call: dict[str, Any], context: Any) -> ToolExecutionResult:
        del call, context
        self.execute_calls += 1
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=f"executed:{self.tool_type}",
        )


class _Engine:
    def __init__(self, handlers: dict[str, Any], broker: Any | None = None) -> None:
        self.tool_handlers = dict(handlers)
        self.executor_broker = broker or ExecutorBroker(None)

    def _resolve_tool_handlers(self, **kwargs: Any) -> dict[str, Any]:
        selection = kwargs.get("capability_selection")
        frozen = getattr(selection, "resolved_handlers", None)
        if frozen:
            return dict(frozen)
        return dict(self.tool_handlers)


def _resolve_query(request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id


def _resolve_payload(payload: dict[str, Any]) -> tuple[str, str]:
    session_id = str(payload.get("user_id") or payload.get("session_id") or "")
    profile_user_id = str(payload.get("real_user_id") or session_id)
    return session_id, profile_user_id


class CapabilityFabricRepairTests(unittest.TestCase):
    def test_artifact_broker_rejects_traversal_outside_root_and_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(temp_dir)
            artifact = root / "files" / "note.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"akane")
            outside = Path(outside_dir) / "outside.txt"
            outside.write_bytes(b"outside")
            broker = ArtifactBroker(instance_id="personal", data_root=root)

            record = broker.record(
                handle="file_001",
                kind="attachment",
                path=artifact,
                file_name="../note.txt",
                media_type="text/plain; charset=utf-8",
                item={"file_size": 5},
            )

            self.assertEqual(record.file_name, "note.txt")
            self.assertEqual(record.transfer_headers()["X-Akane-Artifact-Instance"], "personal")
            self.assertEqual(record.transfer_headers()["X-Akane-Artifact-Size"], "5")
            self.assertEqual(len(record.transfer_headers()["X-Akane-Artifact-Sha256"]), 64)
            with self.assertRaisesRegex(ValueError, "artifact_handle_invalid"):
                broker.record(
                    handle="../../file_001",
                    kind="attachment",
                    path=artifact,
                    file_name="note.txt",
                    media_type="text/plain",
                )
            with self.assertRaisesRegex(ValueError, "artifact_handle_invalid"):
                broker.record(
                    handle="file_../../secret",
                    kind="attachment",
                    path=artifact,
                    file_name="note.txt",
                    media_type="text/plain",
                )
            with self.assertRaisesRegex(ValueError, "artifact_outside_instance_root"):
                broker.record(
                    handle="file_002",
                    kind="attachment",
                    path=outside,
                    file_name="outside.txt",
                    media_type="text/plain",
                )
            with self.assertRaisesRegex(ValueError, "artifact_size_record_mismatch"):
                broker.record(
                    handle="file_003",
                    kind="attachment",
                    path=artifact,
                    file_name="note.txt",
                    media_type="text/plain",
                    item={"file_size": 99},
                )

    def test_workspace_upload_uses_instance_staging_profile_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured: list[dict[str, Any]] = []

            def import_paths(**kwargs: Any) -> dict[str, Any]:
                path = Path(kwargs["paths"][0]).resolve()
                captured.append({**kwargs, "path": path, "exists_during_import": path.is_file()})
                self.assertTrue(path.is_relative_to((root / "cache").resolve()))
                return {
                    "ok": True,
                    "items": [
                        {
                            "handle": "file_001",
                            "absolute_path": str(path),
                        }
                    ],
                }

            engine = SimpleNamespace(
                runtime_layout=SimpleNamespace(cache_dir=root / "cache"),
                import_desktop_pet_local_paths=import_paths,
            )
            app = FastAPI()
            app.include_router(
                build_desktop_pet_router(
                    engine=engine,
                    config_module=SimpleNamespace(
                        DESKTOP_PET_WORKSPACE_UPLOAD_MAX_FILES=2,
                        DESKTOP_PET_WORKSPACE_UPLOAD_MAX_BYTES=4,
                        DESKTOP_PET_WORKSPACE_UPLOAD_MAX_TOTAL_BYTES=6,
                        DESKTOP_PET_AUDIO_UPLOAD_MAX_BYTES=8,
                    ),
                    runtime_metrics=_Metrics(),
                    log_event=lambda *_args, **_kwargs: None,
                    resolve_identity_from_query=_resolve_query,
                    resolve_identity_from_payload=_resolve_payload,
                )
            )
            client = TestClient(app)

            accepted = client.post(
                "/desktop-pet/workspace/import-file",
                data={"user_id": "same-session", "session_id": "same-session", "real_user_id": "alice"},
                files=[("files", ("../note.txt", b"abc", "text/plain"))],
            )
            oversized = client.post(
                "/desktop-pet/workspace/import-file",
                data={"user_id": "same-session", "real_user_id": "alice"},
                files=[("files", ("large.bin", b"12345", "application/octet-stream"))],
            )
            too_many = client.post(
                "/desktop-pet/workspace/import-file",
                data={"user_id": "same-session", "real_user_id": "alice"},
                files=[
                    ("files", ("a.txt", b"a", "text/plain")),
                    ("files", ("b.txt", b"b", "text/plain")),
                    ("files", ("c.txt", b"c", "text/plain")),
                ],
            )

            self.assertEqual(accepted.status_code, 200)
            self.assertTrue(accepted.json()["ok"])
            self.assertNotIn("absolute_path", accepted.json()["items"][0])
            self.assertEqual(captured[0]["profile_user_id"], "alice")
            self.assertEqual(captured[0]["session_id"], "same-session")
            self.assertTrue(captured[0]["exists_during_import"])
            self.assertFalse(captured[0]["path"].exists())
            self.assertEqual(oversized.json()["reason"], "file_too_large")
            self.assertEqual(too_many.status_code, 413)
            self.assertEqual(too_many.json()["reason"], "too_many_files")

    def test_artifact_content_preserves_profile_identity_and_instance_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "attachments" / "note.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"profile-bound")
            captured: list[tuple[str, str, str]] = []

            def resolve_file(*, profile_user_id: str, session_id: str, target: str):
                captured.append((profile_user_id, session_id, target))
                if profile_user_id != "alice":
                    return None
                return (
                    {
                        "file_size": len(b"profile-bound"),
                        "origin_name": "note.txt",
                        "mime_type": "text/plain",
                    },
                    artifact,
                )

            engine = SimpleNamespace(
                artifact_broker=ArtifactBroker(instance_id="personal", data_root=root),
                resolve_desktop_pet_attachment_file=resolve_file,
            )
            app = FastAPI()
            app.include_router(
                build_desktop_pet_router(
                    engine=engine,
                    config_module=SimpleNamespace(DESKTOP_PET_AUDIO_UPLOAD_MAX_BYTES=8),
                    runtime_metrics=_Metrics(),
                    log_event=lambda *_args, **_kwargs: None,
                    resolve_identity_from_query=_resolve_query,
                    resolve_identity_from_payload=_resolve_payload,
                )
            )
            client = TestClient(app)

            response = client.get(
                "/desktop-pet/workspace/attachments/file_001/content",
                params={"user_id": "same-session", "real_user_id": "alice"},
            )
            denied = client.get(
                "/desktop-pet/workspace/attachments/file_001/content",
                params={"user_id": "same-session", "real_user_id": "bob"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"profile-bound")
            self.assertEqual(response.headers["x-akane-artifact-instance"], "personal")
            self.assertEqual(response.headers["x-akane-artifact-size"], str(len(b"profile-bound")))
            self.assertEqual(captured[0], ("alice", "same-session", "file_001"))
            self.assertEqual(denied.status_code, 404)

    def test_frozen_handler_and_broker_idempotency_survive_handler_swap(self) -> None:
        old = _Handler("demo_tool")
        new = _Handler("demo_tool")
        engine = _Engine({"demo_tool": new})
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("demo_tool",),
            module_names=("test",),
            resolved_handlers=MappingProxyType({"demo_tool": old}),
        )
        invocation = ToolInvocation(
            name="demo_tool",
            arguments={"value": "one"},
            id="frozen_call",
            capability_selection=selection,
        )

        first, first_envelope = execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="alice",
            session_id="same-session",
            visual_payload={},
            now_ts=1,
        )
        second, second_envelope = execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="alice",
            session_id="same-session",
            visual_payload={},
            now_ts=2,
        )

        self.assertIs(first, second)
        self.assertEqual(first_envelope.status, "ok")
        self.assertEqual(second_envelope.status, "ok")
        self.assertEqual(old.execute_calls, 1)
        self.assertEqual(new.execute_calls, 0)

    def test_broker_scopes_idempotency_and_rejects_argument_reuse(self) -> None:
        calls = 0
        broker = ExecutorBroker(None)

        def dispatch() -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"call": calls}

        first = broker.execute_server_local(
            tool_id="demo_tool",
            invocation_id="provider_call_1",
            ledger_scope="alice\x1fsession",
            request_data={"value": "one"},
            dispatch=dispatch,
        )
        replay = broker.execute_server_local(
            tool_id="demo_tool",
            invocation_id="provider_call_1",
            ledger_scope="alice\x1fsession",
            request_data={"value": "one"},
            dispatch=dispatch,
        )
        conflict = broker.execute_server_local(
            tool_id="demo_tool",
            invocation_id="provider_call_1",
            ledger_scope="alice\x1fsession",
            request_data={"value": "two"},
            dispatch=dispatch,
        )
        other_session = broker.execute_server_local(
            tool_id="demo_tool",
            invocation_id="provider_call_1",
            ledger_scope="bob\x1fsession",
            request_data={"value": "two"},
            dispatch=dispatch,
        )

        self.assertEqual(first.result, {"call": 1})
        self.assertEqual(replay.result, {"call": 1})
        self.assertEqual(conflict.reason, "invocation_id_request_conflict")
        self.assertEqual(other_session.result, {"call": 2})
        self.assertEqual(calls, 2)

    def test_worker_broker_failure_never_falls_back_to_direct_handler(self) -> None:
        class FailingBroker:
            def execute_server_local(self, **_kwargs: Any):
                raise RuntimeError("broker down")

        handler = _Handler("compose_file")
        engine = _Engine({"compose_file": handler}, broker=FailingBroker())
        worker = TaskWorkerService(
            llm=None,  # type: ignore[arg-type]
            task_workspace_service=None,  # type: ignore[arg-type]
            background_tasks=None,
            tool_handlers_provider=lambda: {"compose_file": handler},
            attachment_context_builder=lambda *_args: "",
            generated_context_builder=lambda *_args: "",
            record_tool_artifacts=lambda **_kwargs: ([], ""),
            engine_ref=engine,
        )

        result = worker._execute_worker_tool(
            tool_call={"type": "compose_file", "content_markdown": "hello"},
            handlers={"compose_file": handler},
            profile_user_id="alice",
            session_id="same-session",
            task_id="task_1",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.state_updates["worker_tool_status"], "execution_unknown")
        self.assertEqual(handler.execute_calls, 0)

    def test_browser_page_uses_its_own_server_offer_not_open_browser_satellite(self) -> None:
        ready_handler = _Handler("browser_page", ready=True)
        index = ServerLocalOfferIndex()
        index.replace_handlers({"browser_page": ready_handler})
        registry = CapabilityRegistry(server_offer_index=index)

        ready = registry.select(
            CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET),
            allowed_tool_names=("browser_page",),
        )
        index.replace_handlers({"browser_page": _Handler("browser_page", ready=False)})
        unavailable = registry.select(
            CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET),
            allowed_tool_names=("browser_page",),
        )

        self.assertIn("browser_page", ready.tool_names)
        self.assertNotIn("open_browser", ready.tool_names)
        self.assertNotIn("browser_page", unavailable.tool_names)
        self.assertFalse(any(item.state == "ready" for item in unavailable.disclosures))
        self.assertTrue(any(item.state == "unavailable" for item in unavailable.disclosures))

    def test_open_browser_normalize_and_validate_use_frozen_handler(self) -> None:
        class BrowserHandler(_Handler):
            def __init__(self, *, accepts: bool) -> None:
                super().__init__("open_browser")
                self.accepts = accepts
                self.normalize_calls = 0

            def normalize_call(self, value: Any) -> dict[str, Any] | None:
                self.normalize_calls += 1
                if not self.accepts:
                    return None
                return super().normalize_call(value)

        old = BrowserHandler(accepts=True)
        replacement = BrowserHandler(accepts=False)
        engine = _Engine({"open_browser": replacement})
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("open_browser",),
            module_names=("desktop_browser_open",),
            resolved_handlers=MappingProxyType({"open_browser": old}),
        )
        invocation = normalize_tool_invocation(
            engine,
            {
                "type": "open_browser",
                "url": "https://example.com",
                TOOL_EXECUTION_RECEIPT_FIELD: {"offer_id": "offer"},
            },
            capability_selection=selection,
        )

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertTrue(validate_tool_invocation(engine, invocation).ok)
        self.assertEqual(old.normalize_calls, 2)
        self.assertEqual(replacement.normalize_calls, 0)

    def test_completed_workflow_job_is_not_restarted_by_duplicate_scheduler_delivery(self) -> None:
        calls = 0

        def runner(_request: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"ok": True, "status": "completed", "reason": "done", "outputs": []}

        jobs = {
            "job_1": {
                "jobId": "job_1",
                "workflowId": "wf_1",
                "capabilityId": "image.generate",
                "_profileUserId": "alice",
                "_sessionId": "session",
                "inputs": {},
                "_inputAssets": {},
                "_workflow": {},
                "status": "queued",
                "events": [],
            }
        }
        lock = threading.RLock()
        broker = ExecutorBroker(None)

        _run_bound_workflow_job("job_1", runner, broker, jobs, lock)
        _run_bound_workflow_job("job_1", runner, broker, jobs, lock)

        self.assertEqual(calls, 1)
        self.assertEqual(jobs["job_1"]["status"], "completed")

    def test_mcp_offer_requires_real_tool_list_probe_and_caches_lease(self) -> None:
        calls: list[dict[str, Any]] = []

        def live_probe(*, server: dict[str, Any]) -> dict[str, Any]:
            calls.append(dict(server))
            return {"tools": [{"name": "echo"}]}

        tool = {
            "name": "echo",
            "description": "Echo",
            "promptExposed": True,
            "inputSchema": {"type": "object", "properties": {}},
        }
        adapter = McpStdioCapabilityAdapter(
            provider_id="provider.mcp.demo",
            server_id="demo",
            server_config={"enabled": True, "transport": "stdio", "command": "fake"},
            tool_configs=(tool,),
            liveness_probe=live_probe,
        )
        descriptor = adapter.descriptor_for_tool(tool)
        handler = AdapterCapabilityToolHandler(
            capability_id=descriptor.id,
            adapter=adapter,
            descriptor=descriptor,
        )

        self.assertTrue(adapter.is_live(descriptor.id))
        self.assertTrue(adapter.is_live(descriptor.id))
        self.assertEqual(len(calls), 1)
        self.assertEqual(handler.tool_spec().capability_id, descriptor.id)
        self.assertEqual(handler.tool_metadata().input_schema, handler.tool_spec().input_schema)

        def failed_probe(*, server: dict[str, Any]) -> dict[str, Any]:
            del server
            raise RuntimeError("offline")

        offline = McpStdioCapabilityAdapter(
            provider_id="provider.mcp.offline",
            server_id="offline",
            server_config={"enabled": True, "transport": "stdio", "command": "fake"},
            tool_configs=(tool,),
            liveness_probe=failed_probe,
        )
        offline_descriptor = offline.descriptor_for_tool(tool)
        self.assertFalse(offline.is_live(offline_descriptor.id))


if __name__ == "__main__":
    unittest.main()
