from __future__ import annotations

import asyncio
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.client_protocol import ClientMode
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler
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
    def test_resource_limit_settles_real_job_as_failed_without_dispatch(self):
        from companion_v01.capability_registry import ExecutorBroker
        from companion_v01.engine import AkaneMemoryEngine
        from companion_v01.execution_resource_policy import ExecutionResourcePolicy
        with tempfile.TemporaryDirectory() as directory:
            handler = _Handler(started=threading.Event(), release=threading.Event())
            engine = _Engine(handler)
            engine.executor_broker = ExecutorBroker(None,
                resource_policy=ExecutionResourcePolicy.from_config({"max_input_bytes": 8}))
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            store = HostJobStore(Path(directory) / "jobs.db")
            runner = BackgroundTaskRunner({"host-jobs": 1})
            self.addCleanup(runner.close)
            completed = []
            runtime = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner,
                conversation_ref_issuer=lambda _: "conversation-ref",
                terminal_callback=lambda job: completed.append(job) or True)
            accepted = runtime.submit(capability_id="generate_image", invocation_id="input-limited",
                call={"type": "generate_image", "prompt": "actual request"}, context=_context())
            job_id = accepted.stream_events[0]["job_id"]
            self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=3))
            self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=3))
            job = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
            self.assertEqual(job.status, "failed")
            result = job.result["capability_result"]
            self.assertEqual(result["reason"], "execution_resource_limit_exceeded")
            self.assertEqual(result["content"]["execution_status"], "not_started")
            self.assertEqual(handler.calls, [])
            self.assertEqual(len(completed), 1)

    def test_plugin_job_cancel_waits_for_cleanup_and_preserves_suppressed_cancel_result(self):
        from companion_v01.capability_registry import ExecutorBroker
        from companion_v01.engine import AkaneMemoryEngine

        for suppress in (False, True):
            with self.subTest(suppress=suppress), tempfile.TemporaryDirectory() as directory:
                entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()

                class Adapter:
                    async def invoke(self, _capability_id, _args, _context):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            cleaning.set()
                            while not release.is_set():
                                await asyncio.sleep(0.01)
                            if suppress:
                                return CapabilityResult(is_error=False, status="ok", content={"actually_finished": True})
                            raise

                descriptor = CapabilityDescriptor(
                    id="test.cancellable.run", display_name="Cancellable", short_hint="Test cleanup",
                    visible_in=("qq",), prompt_exposed=True, risk="low", confirm="never",
                    effects=(), trigger=None, inputs=(), outputs=(),
                    raw={"execution_class": "long_task", "completion_mode": "agent", "memory_mode": "timeline"},
                )
                handler = PluginCapabilityToolHandler(
                    capability_id=descriptor.id, adapter=Adapter(), descriptor=descriptor, config_base_dir=Path(directory),
                )
                engine = _Engine(handler)
                engine.executor_broker = ExecutorBroker(None)
                engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
                store = HostJobStore(Path(directory) / "jobs.db")
                runner = BackgroundTaskRunner({"host-jobs": 1})
                completed = []
                runtime = HostToolJobRuntime(
                    engine=engine, store=store, background_tasks=runner,
                    conversation_ref_issuer=lambda _context: "conversation-ref",
                    terminal_callback=lambda job: completed.append(job) or True,
                )
                try:
                    result = runtime.submit(capability_id=descriptor.id, invocation_id="cancel-me",
                                            call={"type": descriptor.id, "arguments": {}}, context=_context())
                    self.assertEqual(result.stream_events[0]["type"], "background_job_accepted", result)
                    job_id = result.stream_events[0]["job_id"]
                    owner = HostJobOwner("profile-a", "session-a")
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(store.request_cancel(job_id, owner=owner)["status"], "cancelling")
                    self.assertTrue(cleaning.wait(2))
                    self.assertEqual(store.get(job_id, owner=owner).status, "running")
                    self.assertEqual(completed, [])
                    self.assertNotIn("cancel_requested", str(store.get(job_id, owner=owner).payload))
                    release.set()
                    self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=3))
                    self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=3))
                    terminal = store.get(job_id, owner=owner)
                    self.assertEqual(terminal.status, "succeeded" if suppress else "cancelled")
                    self.assertEqual(len(completed), 1)
                    self.assertEqual(terminal.artifacts, ())
                    self.assertEqual(runtime.recover(), 0)
                finally:
                    release.set()
                    runner.close(timeout=3)

    def test_queued_child_job_is_not_replayed_after_task_owner_disappears(self):
        from companion_v01.task_work import TaskWork
        from companion_v01.tool_handlers.core import TaskExecutionScope
        with tempfile.TemporaryDirectory() as directory:
            store = HostJobStore(Path(directory) / "jobs.db")
            started, release, hold = threading.Event(), threading.Event(), threading.Event()
            handler = _Handler(started=started, release=release)
            engine = _Engine(handler)
            work = TaskWork()
            engine._live_task_work = {"child-a": work}
            runner = BackgroundTaskRunner({"host-jobs": 1})
            runtime = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner)
            try:
                runner.submit(lane="host-jobs", name="hold", fn=hold.wait, args=(5,))
                context = replace(_context(), execution_scope=TaskExecutionScope(directory, "child-a", work))
                result = runtime.submit(capability_id="generate_image", invocation_id="call_same",
                    call={"type": "generate_image", "prompt": "test"}, context=context)
                job_id = result.state_updates["capability_execution"]["job_id"]
                engine._live_task_work.clear()
                hold.set()
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=3))
                job = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(job.status, "failed")
                self.assertEqual(job.last_error, "task_owner_not_running")
                self.assertEqual(handler.calls, [])
                self.assertEqual(store.pending_completions(), [])
            finally:
                hold.set()
                release.set()
                runner.close()

    def test_sibling_jobs_do_not_share_provider_call_ids_and_queued_cancel_is_confirmed(self):
        from companion_v01.task_work import TaskWork
        from companion_v01.tool_handlers.core import TaskExecutionScope
        with tempfile.TemporaryDirectory() as directory:
            store = HostJobStore(Path(directory) / "jobs.db")
            handler = _Handler(started=threading.Event(), release=threading.Event())
            engine = _Engine(handler)
            hold = threading.Event()
            runner = BackgroundTaskRunner({"host-jobs": 1})
            runtime = HostToolJobRuntime(engine=engine, store=store, background_tasks=runner)
            jobs = []
            try:
                runner.submit(lane="host-jobs", name="hold", fn=hold.wait, args=(5,))
                for child in ("child-a", "child-b"):
                    work = TaskWork()
                    context = replace(_context(), execution_scope=TaskExecutionScope(directory, child, work))
                    result = runtime.submit(capability_id="generate_image", invocation_id="call_same",
                        call={"type": "generate_image", "prompt": "test"}, context=context)
                    jobs.append(result.state_updates["capability_execution"]["job_id"])
                    self.assertEqual(work.close(), [])
                    self.assertEqual(work.collect()[0]["status"], "cancelled")
                self.assertNotEqual(*jobs)
                self.assertEqual(handler.calls, [])
            finally:
                hold.set()
                runner.close()

    def _terminal_job(self, store: HostJobStore):
        owner = HostJobOwner("profile-a", "session-a")
        created = store.create(
            owner=owner, capability_source="tool", capability_id="generate_image",
            payload={}, idempotency_key="finished", argument_fingerprint="test",
            completion_mode="agent",
        )
        claim = store.claim(created["job_id"], worker_id="test")
        store.succeed(created["job_id"], claim_token=claim["claim_token"])
        return store.get(created["job_id"], owner=owner)

    def test_completion_is_delivered_while_execution_lane_is_occupied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            job = self._terminal_job(store)
            background = BackgroundTaskRunner({"host-jobs": 1})
            entered, release, delivered = threading.Event(), threading.Event(), threading.Event()

            def occupy():
                entered.set()
                release.wait(timeout=5)

            runtime = HostToolJobRuntime(
                engine=SimpleNamespace(), store=store, background_tasks=background,
                terminal_callback=lambda _job: delivered.set() or True,
            )
            try:
                background.submit(lane="host-jobs", name="occupied", fn=occupy)
                self.assertTrue(entered.wait(timeout=1))
                self.assertTrue(runtime.publish_completion(job)["ok"])
                self.assertTrue(delivered.wait(timeout=1))
                self.assertFalse(release.is_set())
                self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=2))
                self.assertEqual(store.get(job.job_id, owner=job.owner).completion_status, "delivered")
            finally:
                release.set()
                background.close()

    def test_callback_unbound_after_scheduling_can_be_rebound_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            job = self._terminal_job(store)
            background = BackgroundTaskRunner({"host-job-completions": 1})
            release = threading.Event()
            runtime = HostToolJobRuntime(
                engine=SimpleNamespace(), store=store, background_tasks=background,
            )
            try:
                self.assertEqual(runtime.publish_completion(job)["reason"], "completion_callback_unavailable")
                background.submit(lane="host-job-completions", name="hold", fn=release.wait, args=(5,))
                runtime.bind_terminal_callback(lambda _job: True)
                runtime.publish_completion(job)
                runtime.bind_terminal_callback(None)
                release.set()
                self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=2))
                self.assertEqual(store.get(job.job_id, owner=job.owner).completion_status, "pending")
                runtime.bind_terminal_callback(lambda _job: True)
                self.assertEqual(runtime.recover(), 1)
                self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=2))
                self.assertEqual(store.get(job.job_id, owner=job.owner).completion_status, "delivered")
            finally:
                release.set()
                background.close()

    def test_handler_exception_publishes_failure_without_restart(self) -> None:
        class FailingHandler(_Handler):
            def execute(self, **_kwargs):
                raise RuntimeError("test failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            background = BackgroundTaskRunner({"host-jobs": 1})
            handler = FailingHandler(started=threading.Event(), release=threading.Event())
            completed = []
            runtime = HostToolJobRuntime(
                engine=_Engine(handler), store=store, background_tasks=background,
                conversation_ref_issuer=lambda _context: "conversation-ref",
                terminal_callback=lambda job: completed.append(job) or True,
            )
            try:
                with self.assertLogs("akane.host_tool_jobs", level="ERROR"):
                    result = runtime.submit(capability_id="generate_image", invocation_id="failure",
                                            call={"type": "generate_image"}, context=_context())
                    self.assertTrue(background.wait_idle(lane="host-jobs", timeout=2))
                self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=2))
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0].status, "failed")
                self.assertEqual(completed[0].job_id, result.stream_events[0]["job_id"])
            finally:
                background.close()

    def test_cancelled_queued_tool_is_not_started_and_still_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            background = BackgroundTaskRunner({"host-jobs": 1})
            release = threading.Event()
            handler = _Handler(started=threading.Event(), release=threading.Event())
            completed = []
            runtime = HostToolJobRuntime(
                engine=_Engine(handler), store=store, background_tasks=background,
                conversation_ref_issuer=lambda _context: "conversation-ref",
                terminal_callback=lambda job: completed.append(job) or True,
            )
            try:
                background.submit(lane="host-jobs", name="hold", fn=release.wait, args=(5,))
                result = runtime.submit(capability_id="generate_image", invocation_id="cancel-queued",
                                        call={"type": "generate_image"}, context=_context())
                store.request_cancel(result.stream_events[0]["job_id"], owner=HostJobOwner("profile-a", "session-a"))
                release.set()
                self.assertTrue(background.wait_idle(lane="host-jobs", timeout=2))
                self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=2))
                self.assertEqual(handler.calls, [])
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0].status, "cancelled")
            finally:
                release.set()
                background.close()

    def test_crashed_process_after_external_effect_is_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.db"
            effect = Path(temp_dir) / "effect.txt"
            store = HostJobStore(path)
            owner = HostJobOwner("profile-a", "session-a")
            created = store.create(
                owner=owner, capability_source="tool", capability_id="generate_image",
                payload={"call": {"type": "generate_image"}}, idempotency_key="crash",
                argument_fingerprint="test", completion_mode="agent",
            )
            script = (
                "import os, sys\nfrom pathlib import Path\n"
                "from companion_v01.host_jobs import HostJobStore\n"
                "store = HostJobStore(Path(sys.argv[1]))\n"
                "assert store.claim(sys.argv[2], worker_id='crashing')['ok']\n"
                "Path(sys.argv[3]).write_text('external effect', encoding='utf-8')\n"
                "os._exit(17)\n"
            )
            process = subprocess.run([sys.executable, "-c", script, str(path), created["job_id"], str(effect)],
                                     capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 17, process.stderr.decode(errors="replace"))
            self.assertEqual(effect.read_text(encoding="utf-8"), "external effect")
            restarted_store = HostJobStore(path)
            self.assertEqual(restarted_store.recover_abandoned_claims(), 1)
            handler = _Handler(started=threading.Event(), release=threading.Event())
            background = BackgroundTaskRunner({"host-jobs": 1})
            completed = []
            runtime = HostToolJobRuntime(
                engine=_Engine(handler), store=restarted_store, background_tasks=background,
                terminal_callback=lambda job: completed.append(job) or True,
            )
            try:
                self.assertEqual(runtime.recover(), 1)
                self.assertTrue(background.wait_idle(lane="host-job-completions", timeout=2))
                self.assertEqual(handler.calls, [])
                self.assertEqual(completed[0].last_error, "host_restart_outcome_unknown")
                self.assertEqual(completed[0].completion_event_id, created["completion_event_id"])
                self.assertEqual(runtime.recover(), 0)
            finally:
                background.close()

    def test_plugin_long_task_is_admitted_before_job_creation_and_executes_once(self) -> None:
        class Adapter:
            provider_id = "plugin-test"
            server_id = "plugin-test"

            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def invoke(self, capability_id: str, args: dict[str, Any], _context: Any) -> CapabilityResult:
                self.calls.append({"capability_id": capability_id, "args": dict(args)})
                return CapabilityResult(is_error=False, status="ok", content={"value": "done"})

        descriptor = CapabilityDescriptor(
            id="akane.test.long.run",
            display_name="Long plugin task",
            short_hint="Run one slow plugin operation.",
            visible_in=("qq", "desktop"),
            prompt_exposed=True,
            risk="high",
            confirm="always",
            effects=("filesystem",),
            trigger=None,
            inputs=(CapabilityIOSlot(name="value", kind="string", required=True),),
            outputs=(),
            raw={
                "execution_class": "long_task",
                "completion_mode": "agent",
                "memory_mode": "timeline",
            },
        )
        adapter = Adapter()
        approvals = CapabilityApprovalStore()
        runner = BackgroundTaskRunner({"host-jobs": 1})
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = PluginCapabilityToolHandler(
                capability_id=descriptor.id,
                adapter=adapter,
                descriptor=descriptor,
                config_base_dir=Path(temp_dir),
                approval_store=approvals,
            )
            store = HostJobStore(Path(temp_dir) / "jobs.db")

            class Engine(_Engine):
                def _resolve_tool_handlers(self, **_kwargs: Any):
                    return {handler.tool_type: handler}

            completed = []
            runtime = HostToolJobRuntime(
                engine=Engine(handler),
                store=store,
                background_tasks=runner,
                conversation_ref_issuer=lambda _context: "conversation-ref",
                terminal_callback=lambda job: completed.append(job) or True,
            )
            call = {"type": descriptor.id, "arguments": {"value": "  hello  "}}
            try:
                blocked = runtime.submit(
                    capability_id=descriptor.id,
                    invocation_id="call-plugin-long",
                    call=call,
                    context=_context(),
                    handler=handler,
                )
                self.assertEqual(blocked.stream_events[0]["type"], "capability_approval_required")
                self.assertEqual(store.pending_job_ids(), [])
                self.assertEqual(adapter.calls, [])

                approvals.decide_request(
                    profile_user_id="profile-a",
                    request_id=blocked.stream_events[0]["requestId"],
                    payload={"decision": "approved"},
                )
                accepted = runtime.submit(
                    capability_id=descriptor.id,
                    invocation_id="call-plugin-long",
                    call=call,
                    context=_context(),
                    handler=handler,
                )
                self.assertEqual(accepted.stream_events[0]["type"], "background_job_accepted")
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(adapter.calls[0]["args"], {"value": "  hello  "})
                self.assertEqual(len(completed), 1)
            finally:
                runner.close(timeout=2.0)

    def test_silent_long_task_settles_without_agent_completion(self) -> None:
        from companion_v01.bot_runtime import _dispatch_host_job_completion
        from unittest.mock import AsyncMock
        started = threading.Event()
        release = threading.Event()
        release.set()
        handler = _Handler(started=started, release=release)
        handler.background_job_policy = lambda: ("silent", "current_turn")  # type: ignore[attr-defined]
        runner = BackgroundTaskRunner({"host-jobs": 1})
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            router = SimpleNamespace(submit=AsyncMock())
            engine = _Engine(handler)
            runtime = HostToolJobRuntime(
                engine=engine,
                store=store,
                background_tasks=runner,
                conversation_ref_issuer=lambda _context: "conversation-ref",
                terminal_callback=lambda job: asyncio.run(_dispatch_host_job_completion(engine, router, job)),
            )
            try:
                accepted = runtime.submit(
                    capability_id=handler.tool_type,
                    invocation_id="call-silent",
                    call={"type": handler.tool_type, "prompt": "moon"},
                    context=_context(),
                    handler=handler,
                )
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
                job = store.get(
                    accepted.stream_events[0]["job_id"],
                    owner=HostJobOwner("profile-a", "session-a"),
                )
                self.assertEqual(job.status, "succeeded")
                self.assertEqual(job.completion_status, "delivered")
                self.assertEqual(job.delivery_receipt["status"], "silent")
                self.assertEqual(job.delivery_receipt["model_status"], "not_requested")
                router.submit.assert_not_called()
            finally:
                runner.close(timeout=2.0)

    def test_admission_failure_is_structured_and_creates_no_job(self) -> None:
        started = threading.Event()
        release = threading.Event()
        handler = _Handler(started=started, release=release)
        handler.admit_execution = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("secret"))  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            runtime = HostToolJobRuntime(
                engine=_Engine(handler),
                store=store,
                background_tasks=SimpleNamespace(),
                conversation_ref_issuer=lambda _context: "conversation-ref",
            )

            with self.assertLogs("akane.host_tool_jobs", level="ERROR"):
                result = runtime.submit(
                    capability_id=handler.tool_type,
                    invocation_id="call-broken-admission",
                    call={"type": handler.tool_type, "prompt": "moon"},
                    context=_context(),
                    handler=handler,
                )

            self.assertEqual(result.stream_events[0]["reason"], "long_tool_admission_failed")
            self.assertNotIn("secret", result.followup_context)
            self.assertEqual(store.pending_job_ids(), [])

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
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
                finished = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(finished.status, "succeeded")
                self.assertEqual(finished.result_summary, "图片生成完成。")
                self.assertEqual(finished.artifacts[0]["handle"], "generated-file:image-1")
                self.assertEqual(finished.completion_status, "delivered")
                self.assertEqual([item.job_id for item in completed], [job_id])
                duplicate = runtime.submit(
                    capability_id="generate_image", invocation_id="call-a",
                    call={"type": "generate_image", "prompt": "moon"}, context=_context(),
                )
                self.assertIn("没有重复启动", duplicate.followup_context)
                self.assertEqual(duplicate.state_updates["capability_execution"]["job_status"], "succeeded")
                self.assertEqual(len(handler.calls), 1)
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
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
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
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
                pending = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(pending.completion_status, "pending")
                self.assertEqual(pending.completion_attempts, 1)

                runtime.bind_terminal_callback(lambda _job: True)
                self.assertEqual(runtime.recover(), 1)
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
                delivered = store.get(job_id, owner=HostJobOwner("profile-a", "session-a"))
                self.assertEqual(delivered.completion_status, "delivered")
            finally:
                runner.close(timeout=2.0)

    def test_shared_completion_delivery_recovers_execution_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = BackgroundTaskRunner({"host-jobs": 1})
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = store.create(
                owner=HostJobOwner("profile-a", "session-a"),
                capability_source="execution",
                capability_id="exec_run",
                payload={"run_id": "execrun_" + "a" * 32},
                idempotency_key="execrun_" + "a" * 32,
                argument_fingerprint="sha256:test",
                channel=ClientMode.QQ_TEXT.value,
                delivery_target="conversation-ref",
                completion_mode="agent",
            )
            claimed = store.claim(created["job_id"], worker_id="execution")
            store.succeed(
                created["job_id"],
                claim_token=claimed["claim_token"],
                result_summary="command done",
            )
            completed = []
            runtime = HostToolJobRuntime(
                engine=SimpleNamespace(),
                store=store,
                background_tasks=runner,
                terminal_callback=lambda job: completed.append(job) or True,
            )
            try:
                self.assertEqual(runtime.recover(), 1)
                self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=2.0))
                self.assertTrue(runner.wait_idle(lane="host-job-completions", timeout=2.0))
                self.assertEqual([job.job_id for job in completed], [created["job_id"]])
                delivered = store.get(
                    created["job_id"],
                    owner=HostJobOwner("profile-a", "session-a"),
                )
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
