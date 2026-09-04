from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.client_protocol import ClientMode
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.tool_handlers.core import ToolExecutionContext, ToolExecutionResult


class _Broker:
    def execute_server_local(self, **kwargs: Any) -> SimpleNamespace:
        try:
            return SimpleNamespace(status="succeeded", reason="", result=kwargs["dispatch"]())
        except Exception as exc:
            return SimpleNamespace(status="failed", reason=type(exc).__name__, result=None)


class _Handler:
    tool_type = "generate_image"

    def __init__(self, *, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def tool_spec() -> SimpleNamespace:
        return SimpleNamespace(execution_class="long_task")

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.calls.append({"call": dict(call), "context": context})
        self.started.set()
        self.release.wait(timeout=2.0)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{"type": "generated_file_ready", "status": "completed"}],
            followup_context="图片生成完成。",
            state_updates={"generated_file_handles": ["generated-file:image-1"]},
        )


class _Engine:
    def __init__(self, handler: _Handler) -> None:
        self.handler = handler
        self.executor_broker = _Broker()

    @staticmethod
    def _resolve_client_protocol_context(payload: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(effective_mode=payload.get("client_mode"))

    def _resolve_tool_handlers(self, **_kwargs: Any) -> dict[str, _Handler]:
        return {self.handler.tool_type: self.handler}

    @staticmethod
    def _tool_hook_result_status(result: ToolExecutionResult) -> tuple[str, str]:
        if "<tool_use_error>" in str(result.followup_context or ""):
            return "failed", "tool_use_error"
        return "succeeded", ""


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="profile-a",
        session_id="session-a",
        now_ts=123,
        visual_payload={},
        character_pack_id="reimu",
        current_user_source_id="message-a",
        client_mode=ClientMode.QQ_TEXT.value,
        request_context={"group_id": 87, "unsafe": object()},
    )


class HostToolJobRuntimeTests(unittest.TestCase):
    def test_submit_persists_then_returns_accepted_while_handler_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            started = threading.Event()
            release = threading.Event()
            handler = _Handler(started=started, release=release)
            runner = BackgroundTaskRunner({"host-jobs": 1})
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            completed = []

            def record_completion(job: Any) -> bool:
                completed.append(job)
                return True

            runtime = HostToolJobRuntime(
                engine=_Engine(handler),
                store=store,
                background_tasks=runner,
                conversation_ref_issuer=lambda _context: "conversation-ref",
                terminal_callback=record_completion,
            )
            try:
                result = runtime.submit(
                    capability_id="generate_image",
                    invocation_id="call-a",
                    call={"type": "generate_image", "prompt": "moon"},
                    context=_context(),
                )

                self.assertEqual(result.stream_events[0]["status"], "accepted")
                job_id = result.stream_events[0]["job_id"]
                self.assertTrue(started.wait(timeout=1.0))
                running = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(running.status, "running")
                self.assertEqual(running.delivery_target, "conversation-ref")
                self.assertNotIn("unsafe", running.payload["request_context"])

                release.set()
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                finished = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(finished.status, "succeeded")
                self.assertEqual(finished.result_summary, "图片生成完成。")
                self.assertEqual(finished.artifacts[0]["handle"], "generated-file:image-1")
                self.assertEqual(finished.completion_status, "delivered")
                self.assertEqual([item.job_id for item in completed], [job_id])
            finally:
                release.set()
                runner.close(timeout=2.0)

    def test_recover_schedules_persisted_tool_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            started = threading.Event()
            release = threading.Event()
            release.set()
            handler = _Handler(started=started, release=release)
            runner = BackgroundTaskRunner({"host-jobs": 1})
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = store.create(
                owner=HostJobOwner("profile-a", "session-a"),
                capability_source="tool",
                capability_id="generate_image",
                payload={
                    "call": {"type": "generate_image", "prompt": "moon"},
                    "client_mode": ClientMode.QQ_TEXT.value,
                    "domain_profile_id": "",
                    "current_user_source_id": "message-a",
                    "request_context": {},
                },
                idempotency_key="call-a",
                argument_fingerprint="sha256:test",
                character_pack_id="reimu",
                channel=ClientMode.QQ_TEXT.value,
                delivery_target="conversation-ref",
            )
            runtime = HostToolJobRuntime(
                engine=_Engine(handler),
                store=store,
                background_tasks=runner,
            )
            try:
                self.assertEqual(runtime.recover(), 1)
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                finished = store.get(
                    created["job_id"],
                    owner=HostJobOwner("profile-a", "session-a"),
                )
                self.assertEqual(finished.status, "succeeded")
                self.assertTrue(started.is_set())
            finally:
                runner.close(timeout=2.0)

    def test_failed_completion_delivery_stays_pending_and_recover_retries_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            started = threading.Event()
            release = threading.Event()
            release.set()
            handler = _Handler(started=started, release=release)
            runner = BackgroundTaskRunner({"host-jobs": 1})
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            runtime = HostToolJobRuntime(
                engine=_Engine(handler),
                store=store,
                background_tasks=runner,
                conversation_ref_issuer=lambda _context: "conversation-ref",
                terminal_callback=lambda _job: SimpleNamespace(ok=False, reason="channel_offline"),
            )
            try:
                result = runtime.submit(
                    capability_id="generate_image",
                    invocation_id="call-a",
                    call={"type": "generate_image", "prompt": "moon"},
                    context=_context(),
                )
                job_id = result.stream_events[0]["job_id"]
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                pending = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(pending.completion_status, "pending")
                self.assertEqual(pending.completion_attempts, 1)

                runtime.bind_terminal_callback(lambda _job: True)
                self.assertEqual(runtime.recover(), 1)
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                delivered = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(delivered.completion_status, "delivered")
            finally:
                runner.close(timeout=2.0)

    def test_only_supported_channel_long_tasks_are_detached(self) -> None:
        started = threading.Event()
        release = threading.Event()
        handler = _Handler(started=started, release=release)
        runtime = HostToolJobRuntime(
            engine=_Engine(handler),
            store=SimpleNamespace(),
            background_tasks=SimpleNamespace(),
        )

        self.assertTrue(runtime.accepts(handler=handler, context=_context()))
        handler.tool_type = "exec_run"
        self.assertFalse(runtime.accepts(handler=handler, context=_context()))
        handler.tool_type = "generate_image"
        web_context = replace(_context(), client_mode=ClientMode.SCENE_STATIC.value)
        self.assertFalse(runtime.accepts(handler=handler, context=web_context))


if __name__ == "__main__":
    unittest.main()
