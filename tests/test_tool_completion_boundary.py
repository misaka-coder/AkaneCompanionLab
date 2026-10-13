from __future__ import annotations

import copy
import asyncio
import json
import os
import shlex
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.turn_contracts import (
    PreparedTurn, TurnIdentity, TurnRequest, TurnPersistence, TurnPromptState, TurnMemoryState, TurnDiagnostics,
)
from companion_v01.engine_services.turn_rounds import run_turn_rounds
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner
from companion_v01.execution_wait import batch_wait_budget
from companion_v01.host_completion_work import HostCompletionWork
from companion_v01.host_completion_batch import prepare_completion_turn
from companion_v01.host_execution_jobs import HostExecutionJobRuntime
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.tool_batch import execute_tool_batch
from companion_v01.tool_handlers.core import ToolExecutionContext, ToolExecutionResult, ToolMetadata
from companion_v01.tool_handlers.execution import ExecRunToolHandler, ExecStatusToolHandler
from companion_v01.tool_invocation import NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD
from tests.test_memcore_integration import _FakeEmbeddingProvider, _FakeLLM
from tests import test_host_completion_batch as completion_helpers


def python_command(code):
    if os.name == "nt":
        return "& '" + str(sys.executable).replace("'", "''") + "' -c '" + code.replace("'", "''") + "'"
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


def exhaust(iterator):
    events = []
    while True:
        try:
            events.append(next(iterator))
        except StopIteration as stop:
            return events, stop.value


class CompletionWorkFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = HostJobStore(self.root / "jobs.db")
        self.owner = HostJobOwner("master", "conversation")
        self.published = []
        self.registry = HostCompletionWork(self.store, self.published.append)
        self.work = self.registry.begin(owner=self.owner, character_pack_id="cecilia", channel="qq_text", turn_id="turn-1")
        self.addCleanup(self.registry.close, self.work)

    def job(self, number=1, **changes):
        arguments = dict(owner=self.owner, capability_source="tool", capability_id="document.compose",
            payload={}, idempotency_key=f"call-{number}", argument_fingerprint=f"args-{number}",
            character_pack_id="cecilia", channel="qq_text", delivery_target="ref", turn_id="turn-1",
            tool_call_id=f"call-{number}", completion_mode="agent", memory_mode="timeline")
        arguments.update(changes)
        created = self.store.create(**arguments)
        self.assertTrue(created["ok"], created)
        job = self.store.get(created["job_id"], owner=arguments["owner"])
        self.registry.track(job)
        return job

    def finish(self, job, text="finished", **changes):
        claim = self.store.claim(job.job_id, worker_id="test")
        done = self.store.succeed(job.job_id, claim_token=claim["claim_token"], result_summary=text, **changes)
        self.assertTrue(done["ok"], done)
        job = self.store.get(job.job_id, owner=job.owner)
        self.registry.hold(job)
        return job

class CompletionWorkTests(CompletionWorkFixture, unittest.TestCase):
    def test_model_receipts_commit_atomically_without_reloading_result_bodies(self):
        first = self.finish(self.job(40))
        second = self.finish(self.job(41))
        invalid = replace(second, completion_event_id="stale-event")
        with patch.object(self.store, "get", side_effect=AssertionError("no full result reload")):
            receipt = self.store.consume_model_completions([first, invalid], consumed_by="turn-1", mark_delivered=True)
        self.assertFalse(receipt["ok"])
        self.assertEqual(self.store.get(first.job_id, owner=self.owner).model_consumed_by, "")
        self.work.collect()
        with patch.object(self.store, "get", side_effect=AssertionError("no full result reload")):
            self.work.commit()
        for job in (first, second):
            stored = self.store.get(job.job_id, owner=self.owner)
            self.assertEqual(stored.model_consumed_by, "turn-1")
            self.assertEqual(stored.completion_status, "delivered")

    def test_live_wait_releases_for_stop_and_steer_without_consuming_the_control(self):
        from companion_v01.engine_services.turn_completions import turn_wait_cancelled
        from companion_v01.turn_coordination import TurnCoordinator
        self.job()
        async def exercise(action):
            coordinator = TurnCoordinator()
            async with coordinator.hold("master", "conversation", actor_id="qq:master", channel="qq") as token:
                engine = SimpleNamespace(turn_coordinator=coordinator)
                turn = SimpleNamespace(request=SimpleNamespace(turn_control_id=token))
                def control():
                    if action == "stop":
                        coordinator.request_stop_token(token)
                    else:
                        coordinator.offer_steer(profile_user_id="master", session_id="conversation",
                                                actor_id="qq:master", content="先停一下，换个做法")
                trigger = threading.Timer(0.02, control)
                trigger.start()
                try:
                    started = time.monotonic()
                    self.work.wait(turn_wait_cancelled(engine, turn), deadline=started + 1.5)
                    self.assertLess(time.monotonic() - started, 0.7)
                    snapshot = coordinator.drain(token)
                    self.assertTrue(snapshot["stop_requested"] if action == "stop" else snapshot["steers"])
                finally:
                    trigger.cancel()
                    trigger.join()
        for action in ("stop", "steer"):
            with self.subTest(action=action):
                asyncio.run(exercise(action))

    def test_multiple_ready_results_are_collected_once_and_only_consumed_after_response(self):
        first, second = self.finish(self.job(1), "result A"), self.finish(self.job(2), "result B")
        with patch.object(self.store, "pending_completions", side_effect=AssertionError("no full scan")):
            collected = self.work.collect()
        self.assertEqual([item.job.job_id for item in collected], [first.job_id, second.job_id])
        self.assertFalse(self.work.collect())
        self.assertFalse(self.store.get(first.job_id, owner=self.owner).model_consumed_by)
        self.work.commit()
        for job in (first, second):
            saved = self.store.get(job.job_id, owner=self.owner)
            self.assertEqual(saved.model_consumed_by, "turn-1")
            self.assertEqual(saved.completion_status, "delivered")
            self.assertEqual(saved.delivery_receipt, {})

    def test_unreceived_frozen_results_release_to_the_existing_idle_publisher(self):
        job = self.finish(self.job(), "keep this result")
        self.work.collect()
        self.registry.close(self.work)
        self.assertEqual([item.job_id for item in self.published], [job.job_id])
        self.assertFalse(self.published[0].model_consumed_by)

    def test_terminal_query_suppresses_automatic_event_but_retains_pagination(self):
        continuation = {"type": "inspect_generated_file", "target": "gen_001", "cursor": "page-2"}
        job = self.finish(self.job(), "first page", result={"model_presentation": {
            "producer_bounded": True, "complete": False, "continuation": continuation}})
        self.registry.query(job, consumed_by="turn-1")
        self.assertFalse(self.work.collect())
        self.assertFalse(self.store.get(job.job_id, owner=self.owner).model_consumed_by)
        self.work.commit()
        saved = self.store.get(job.job_id, owner=self.owner)
        self.assertEqual(saved.result["model_presentation"]["continuation"], continuation)
        self.assertFalse(saved.result["model_presentation"]["complete"])

    def test_scope_isolation_and_explicit_background_does_not_block_finalization(self):
        for field, value in (("owner", HostJobOwner("other", "conversation")),
                             ("character_pack_id", "reimu"), ("channel", "desktop_pet"), ("turn_id", "other-turn")):
            job = self.finish(self.job(value.__str__(), **{field: value}), "foreign")
            self.assertFalse(self.registry.hold(job))
        job = self.job(2)
        self.registry.track(job, read=lambda: ToolExecutionResult(tool_type="test", followup_context="done"), wait=False)
        started = time.monotonic()
        self.work.wait(lambda: False)
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertFalse(self.work.collect())

    def test_budget_defers_whole_items_without_consuming_or_rereading_them(self):
        reads = []
        for number in range(3):
            job = self.job(number)
            self.registry.track(job, read=lambda number=number: (reads.append(number) or ToolExecutionResult(
                tool_type="test", followup_context=str(number) * 40)))
            self.finish(job)
        with patch("companion_v01.host_completion_work.COMPLETION_BATCH_MAX_BYTES", 70):
            first = self.work.collect()
            self.assertEqual(len(first), 1)
            self.work.commit()
            second = self.work.collect()
            self.assertEqual(len(second), 1)
        self.assertFalse(self.store.get(second[0].job.job_id, owner=self.owner).model_consumed_by)
        self.assertEqual(reads, [0, 1, 2])

    def test_repeated_close_cannot_remove_a_new_live_origin(self):
        self.registry.close(self.work)
        newer = self.registry.begin(owner=self.owner, character_pack_id="cecilia", channel="qq_text", turn_id="turn-1")
        self.addCleanup(self.registry.close, newer)
        self.registry.close(self.work)
        self.assertIs(self.registry.current(owner=self.owner, character_pack_id="cecilia", channel="qq_text",
                                            turn_id="turn-1"), newer)

    def test_large_unpaged_result_preserves_truthful_storage_failure_and_bounded_preview(self):
        job = self.finish(self.job(), "界" * 60000, artifacts=[{"handle": "gen_001"}])
        with patch.object(self.store, "get", side_effect=AssertionError("do not reload the result body")):
            evidence = self.work.collect()
        self.assertEqual(len(evidence), 1)
        result = evidence[0].result
        self.assertFalse(result.followup_envelope.complete)
        self.assertIsNone(result.followup_envelope.continuation)
        self.assertIn("plugin_result_storage_unavailable", result.followup_context)
        self.assertIn("gen_001", result.followup_context)
        self.assertLess(len(result.followup_context.encode("utf-8")), 128 * 1024)
        self.assertEqual(len(self.store.get(job.job_id, owner=self.owner).result_summary), 60000)

    def test_large_unpaged_result_uses_a_real_material_and_original_read_tool(self):
        from companion_v01.attachment_inbox import AttachmentInboxService
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01.store import MemoryStore
        from companion_v01.host_completion_presentation import bound_completion_result
        from companion_v01.tool_handlers.generated_media import InspectGeneratedFileToolHandler
        store = MemoryStore(self.root / "files.db")
        service = GeneratedFileService(base_dir=self.root / "generated", store=store,
                                       attachment_service=AttachmentInboxService(store=store))
        engine = SimpleNamespace(_get_generated_file_service=lambda: service)
        self.registry.bound_result = lambda job, result: bound_completion_result(job, result, engine=engine)
        job = self.finish(self.job(), "界" * 60000 + "END-MARKER")
        evidence = self.work.collect()[0]
        continuation = evidence.result.followup_envelope.continuation
        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["type"], "inspect_generated_file")
        handler = InspectGeneratedFileToolHandler(generated_file_service=service)
        context = ToolExecutionContext(profile_user_id=self.owner.profile_user_id,
            session_id=self.owner.session_id, now_ts=100, visual_payload={})
        arguments = {**continuation, "max_chars": 10000}
        pages = []
        for _ in range(20):
            result = handler.execute(call=arguments, context=context)
            self.assertIsNotNone(result.followup_envelope)
            pages.append(result.followup_context)
            arguments = result.followup_envelope.continuation
            if not arguments:
                break
        self.assertTrue("END-MARKER" in "".join(pages), "material tail was not reachable")
        self.assertGreater(len(pages), 1)
        denied = service.inspect_generated_file(profile_user_id="other", session_id=self.owner.session_id,
                                                **{key: value for key, value in continuation.items() if key != "type"})
        self.assertFalse(denied["ok"])

    def test_real_long_tool_worker_keeps_its_producer_page_and_continuation(self):
        from companion_v01.background_tasks import BackgroundTaskRunner
        from companion_v01.host_tool_jobs import HostToolJobRuntime
        from companion_v01.tool_handlers.core import ToolFollowupEnvelope
        from tests import test_host_tool_jobs as helpers
        page = "producer page: " + "x" * 16000 + "\nCONTINUATION-MARKER"
        continuation = {"type": "inspect_generated_file", "target": "gen_001"}
        class Handler(helpers._Handler):
            def execute(self, *, call, context):
                return ToolExecutionResult(tool_type=self.tool_type, followup_context=page,
                    followup_envelope=ToolFollowupEnvelope(content=page, producer_bounded=True,
                                                          complete=False, continuation=continuation))
        handler = Handler(started=threading.Event(), release=threading.Event())
        runner = BackgroundTaskRunner({"host-jobs": 1})
        self.addCleanup(runner.close)
        runtime = HostToolJobRuntime(engine=helpers._Engine(handler), store=self.store, background_tasks=runner,
            conversation_ref_issuer=lambda context: "ref", terminal_callback=lambda job: self.published.append(job) or True)
        work = runtime.completion_work.begin(owner=self.owner, character_pack_id="cecilia",
                                             channel="qq_text", turn_id="turn-1")
        self.addCleanup(runtime.completion_work.close, work)
        context = replace(helpers._context(), profile_user_id="master", session_id="conversation",
                          character_pack_id="cecilia", request_context={"_host_origin_turn_id": "turn-1"})
        accepted = runtime.submit(capability_id=handler.tool_type, invocation_id="producer-page",
                                  call={"type": handler.tool_type}, context=context)
        self.assertEqual(accepted.state_updates["capability_execution"]["status"], "accepted")
        self.assertTrue(runner.wait_idle(lane="host-jobs", timeout=3))
        evidence = work.collect()
        self.assertEqual(len(evidence), 1)
        self.assertTrue(evidence[0].result.followup_context.endswith("CONTINUATION-MARKER"))
        self.assertEqual(evidence[0].job.result_summary, page)
        self.assertEqual(evidence[0].result.followup_envelope.continuation, continuation)
        self.assertFalse(evidence[0].result.followup_envelope.complete)
        work.commit()
        self.assertFalse(self.published)


class QueuedQueryTests(unittest.TestCase):
    setUp = completion_helpers.CompletionProjectionTests.setUp
    put = completion_helpers.CompletionProjectionTests.put
    # Reuse actual inbox envelopes; assertions cover the admission/query race.
    def test_already_queued_terminal_query_skips_model_but_keeps_direct_delivery(self):
        item = self.put(1)
        engine = SimpleNamespace(job_store=self.jobs)
        fields = item.payload["turn_payload"]["plugin_external_event"]["data"]
        job = self.jobs.get(fields["job_id"], owner=HostJobOwner(item.profile_user_id, item.session_id))
        self.jobs.consume_model_completion(job, consumed_by="query-turn")
        turn = prepare_completion_turn(engine, [item])
        self.assertEqual(turn.model_job_ids, frozenset())
        self.assertTrue(turn.prepared_frame["_deliberate_silence"])
        self.assertEqual(job.result_summary, self.jobs.get(job.job_id, owner=job.owner).result_summary)


class RealExecutionCompletionTests(CompletionWorkFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.provider = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=self.root / "runlogs")
        self.provider.cancel_confirm_grace_seconds = 10
        self.addCleanup(self.shutdown_provider)
        self.runtime = HostExecutionJobRuntime(store=self.store, conversation_ref_issuer=lambda context: "ref",
            completion_work=self.registry, completion_publisher=lambda job: self.registry.hold(job) or self.published.append(job))
        self.runtime.bind_provider(self.provider)
        self.handler = ExecRunToolHandler(execution_provider=self.provider)
        self.status = ExecStatusToolHandler(execution_provider=self.provider)
        self.handler.bind_job_runtime(self.runtime)
        self.status.bind_job_runtime(self.runtime)
        self.context = ToolExecutionContext(profile_user_id="master", session_id="conversation", now_ts=100,
            visual_payload={}, character_pack_id="cecilia", current_user_source_id="message-1", client_mode="qq_text",
            invocation_id="call-shell", request_context={"_host_origin_turn_id": "turn-1"})

    def shutdown_provider(self):
        self.provider.request_shutdown()
        # Confirm watcher/observer cleanup before deleting its live log/database.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.provider._lock:
                if not self.provider._logs:
                    return
            time.sleep(0.01)
        self.fail("execution watcher did not release its log before fixture cleanup")

    def test_real_process_output_is_automatically_read_after_the_initial_cursor(self):
        with batch_wait_budget(0):
            result = self.handler._execute_command(self.provider, {"type": "exec_run",
                "command": python_command('import time; print("initial", flush=True); time.sleep(0.15); print("terminal")')}, self.context)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "running")
        with self.work.condition:
            self.assertTrue(self.work.condition.wait_for(lambda: bool(self.work.ready), timeout=5))
        evidence = self.work.collect()
        self.assertEqual(len(evidence), 1)
        self.assertIn("terminal", evidence[0].result.followup_context)
        self.assertEqual(evidence[0].job.turn_id, "turn-1")
        self.assertEqual(evidence[0].job.tool_call_id, "call-shell")
        self.assertFalse(self.published)
        self.work.commit()

    def test_explicit_background_skips_default_shell_initial_wait(self):
        started = time.monotonic()
        with batch_wait_budget(8):
            result = self.handler._execute_command(self.provider, {"type": "exec_run", "background": True,
                "command": python_command('import time; time.sleep(1); print("background done")')}, self.context)
        self.assertLess(time.monotonic() - started, .75)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "running")
        self.assertFalse(self.work.waiting_ids())
        run_id = result.state_updates["capability_execution"]["run_id"]
        final = self.status.execute(call={"type": "exec_status", "run_id": run_id, "wait_seconds": 5}, context=self.context)
        self.assertIn("background done", final.followup_context)

    def test_real_terminal_query_consumes_notice_and_can_still_read_output_beyond_memory_window(self):
        with batch_wait_budget(0):
            initial = self.handler._execute_command(self.provider, {"type": "exec_run",
                "command": python_command('import sys; sys.stdout.write("A" * 180000 + "TAIL")')}, self.context)
        run_id = initial.state_updates["capability_execution"]["run_id"]
        self.work.wait(lambda: False, deadline=time.monotonic() + 5)
        first = self.status.execute(call={"type": "exec_status", "run_id": run_id}, context=self.context)
        self.assertFalse(first.followup_envelope.complete)
        self.assertFalse(self.work.collect())
        self.work.commit()
        text = first.followup_context
        cursor = first.followup_envelope.continuation
        pages = 1
        while cursor:
            page = self.status.execute(call=cursor, context=self.context)
            text += page.followup_context
            cursor = page.followup_envelope.continuation
            pages += 1
            self.assertLess(pages, 12)
        self.assertIn("TAIL", text)
        self.assertGreaterEqual(text.count("A"), 180000)
        self.assertFalse(self.published)

    def test_shared_wait_does_not_accumulate_per_command(self):
        commands = [{"type": "exec_run", "command": python_command('import time; time.sleep(0.4)')}
                    for _ in range(3)]
        contexts = [replace(self.context, invocation_id=f"call-{index}") for index in range(3)]
        seen = []
        def execute(call):
            context = contexts[len(seen)]
            seen.append(context)
            return self.handler._execute_command(self.provider, call, context)
        started = time.monotonic()
        # Declared independent reads exercise the common deadline in copied contexts.
        results = execute_tool_batch(commands, handler_for=lambda call: SimpleNamespace(
            tool_metadata=lambda: ToolMetadata(operation="read")), execute=execute, wait_seconds=0.03)
        duration = time.monotonic() - started
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.state_updates["capability_execution"]["status"] == "running" for result in results))
        self.assertLess(duration, 1.2)
        self.work.wait(lambda: False, deadline=time.monotonic() + 5)

    def test_status_can_win_the_race_with_durable_process_settlement(self):
        settling, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.store.succeed
        def delayed(*args, **kwargs):
            settling.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)
        with patch.object(self.store, "succeed", side_effect=delayed):
            with batch_wait_budget(0):
                initial = self.handler._execute_command(self.provider, {"type": "exec_run",
                    "command": python_command('print("settlement race")')}, self.context)
            run_id = initial.state_updates["capability_execution"]["run_id"]
            self.assertTrue(settling.wait(5))
            queried = self.status.execute(call={"type": "exec_status", "run_id": run_id}, context=self.context)
            self.assertEqual(queried.state_updates["capability_execution"]["status"], "completed")
            self.work.commit()
            release.set()
            deadline = time.monotonic() + 5
            while not self.published and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertTrue(self.published)
        self.assertEqual(self.published[0].model_consumed_by, "turn-1")
        self.assertFalse(self.work.collect())

    def test_stop_runs_real_cancellation_even_when_normal_dispatch_is_cancelled(self):
        from companion_v01.tool_handlers.execution import ExecCancelToolHandler
        cancel = ExecCancelToolHandler(execution_provider=self.provider)
        cancel.bind_job_runtime(self.runtime)
        with batch_wait_budget(0):
            initial = self.handler._execute_command(self.provider, {"type": "exec_run",
                "command": "Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30"}, self.context)
        run_id = initial.state_updates["capability_execution"]["run_id"]
        result = execute_tool_batch([{"type": "exec_cancel", "run_id": run_id}], handler_for=lambda call: cancel,
            execute=lambda call: cancel.execute(call=call, context=self.context), cancelled=lambda: True)
        self.assertEqual(result[0].state_updates["capability_execution"]["status"], "cancelled")
        self.assertEqual(self.provider.store.get(run_id, owner=ExecutionRunOwner("master", "conversation", "local")).status,
                         "cancelled")


class MainTurnBoundaryTests(CompletionWorkFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # run_turn_rounds owns a separate live origin in this fixture.
        self.registry.close(self.work)
        self.manager = MemcoreManager(backend="memcore", storage_path=self.root / "memory.db",
            visible_scope="conversation", enable_flavor=True, shadow_compare=False,
            llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
        self.addCleanup(self.manager.close)
        self.scope = dict(profile_user_id="master", session_id="conversation", character_pack_id="cecilia")
        opened = self.manager.begin_input_turn({"source_id": "message-1", "content": "do the task", "timestamp": 100},
            turn_id="turn-1", **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.turn = PreparedTurn(TurnIdentity("conversation", "master", "cecilia", "", "qq:master", "master", "trace"),
            TurnRequest("sync", {}, SimpleNamespace(effective_mode=ClientMode.QQ_TEXT), "do the task", 100, "", "", "", "", None),
            TurnPersistence(False, False, False, True, False, None, "qq_private"),
            TurnPromptState(None, "", "frozen dynamic", [], SimpleNamespace(protocol="openai"), False, [], [], [], [], [], {}),
            TurnMemoryState({}, "message-1", "turn-1", None), TurnDiagnostics({}, {}, {}, {}, {}))
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        self.engine.memcore_manager = self.manager
        self.engine.host_tool_jobs = SimpleNamespace(completion_work=self.registry)
        self.engine.llm = SimpleNamespace(chat_provider_protocol=lambda: "openai")
        self.engine._max_tool_rounds = lambda **kwargs: 0
        self.engine._tool_round_warning_remaining = lambda **kwargs: 0
        self.engine._build_tool_round_extra_context = lambda **kwargs: kwargs["turn_extra_user_context"]
        self.engine._prepare_tool_round_decisions = lambda **kwargs: (
            kwargs["final_output"], kwargs["final_output"].get("calls", []), [])
        self.engine._record_assistant_preface_for_tool_call = lambda **kwargs: ""
        self.engine._tool_batch_memcore_failure = lambda results: None
        self.engine._recompute_turn_execution_target = lambda **kwargs: kwargs["current_target"]
        self.engine._build_tool_working_stream_event = lambda call: {"type": "tool_progress"}
        self.engine._tool_call_allows_assistant_preface = lambda call: False
        self.requests = []
        self.jobs = []

    def batch(self, **kwargs):
        results = []
        for index, call in enumerate(kwargs["tool_calls"]):
            job = self.job(len(self.jobs))
            self.jobs.append(job)
            result = ToolExecutionResult(tool_type=call["type"], followup_context=f"accepted {job.job_id}")
            results.append(result)
        items = [(call, result, result.followup_context) for call, result in zip(kwargs["tool_calls"], results)]
        recorded = self.manager.record_tool_batch(exchanges=[dict(tool_name=call["type"],
            tool_call_id=call[TOOL_INVOCATION_ID_FIELD], tool_input={}, result=result.followup_context,
            source=NATIVE_OPENAI, timestamp=101, source_id_prefix="trace:" + call[TOOL_INVOCATION_ID_FIELD])
            for call, result in zip(kwargs["tool_calls"], results)], turn_id="turn-1", **self.scope)
        self.assertTrue(recorded["ok"], recorded)
        ids = [entry[key] for entry in recorded["exchanges"] for key in ("tool_use_source_id", "tool_result_source_id")]
        projected = self.engine._append_tool_history_batch(tool_history_turns=kwargs["tool_history_turns"],
            items=items, trace_source_ids=ids, provider_profile="openai", memcore_turn_id="turn-1",
            current_user_source_id="message-1", **self.scope)
        self.assertTrue(projected["ok"], projected)
        kwargs["tool_results"].extend(results)
        return results, []

    def generator(self, *, finish_during_final=False, fail=False):
        def generate(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            if len(self.requests) == 1:
                if False:
                    yield {}
                return {"calls": [{"type": "document.compose", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                                  TOOL_INVOCATION_ID_FIELD: "call-native-" + str(index)} for index in range(2)]}
            if len(self.requests) == 2:
                for index, job in enumerate(self.jobs):
                    self.finish(job, f"unique-result-{index}")
                if finish_during_final:
                    yield {"type": "speech_segment", "text": "premature final"}
                return {"speech": "premature final", "calls": []}
            if fail:
                return {"speech": "service unavailable", "_transient_final_failure": True, "calls": []}
            yield {"type": "speech_segment", "text": "combined final"}
            return {"speech": "combined final", "calls": []}
        return generate

    def run_case(self, streaming=False, fail=False):
        if streaming:
            self.turn = replace(self.turn, request=replace(self.turn.request, mode="stream"))
        self.engine._execute_and_record_tool_batch = self.batch
        self.engine._generate_round = self.generator(finish_during_final=streaming, fail=fail)
        return exhaust(run_turn_rounds(self.engine, self.turn))

    def test_real_native_memcore_projection_groups_two_completions_at_the_request_boundary(self):
        _events, outcome = self.run_case()
        self.assertEqual(outcome.state.final_output["speech"], "combined final")
        self.assertEqual(len(self.requests), 3)
        prefix = self.requests[1]["post_user_turns"]
        messages = self.requests[2]["post_user_turns"]
        self.assertEqual(messages[:len(prefix)], prefix)
        body = json.dumps(messages, ensure_ascii=False)
        for index in range(2):
            self.assertEqual(body.count(f"unique-result-{index}"), 1)
        calls = [call["id"] for message in messages for call in message.get("tool_calls", [])]
        results = [message["tool_call_id"] for message in messages if message["role"] == "tool"]
        self.assertEqual(sorted(calls), sorted(results))
        self.assertEqual(len(results), 2)
        for job in self.jobs:
            self.assertEqual(self.store.get(job.job_id, owner=self.owner).model_consumed_by, "turn-1")
        self.assertFalse(self.published)

    def test_streaming_releases_progress_reply_and_leaves_late_result_for_idle_delivery(self):
        events, outcome = self.run_case(streaming=True)
        speech = [event["text"] for event in events if event.get("type") == "speech_segment"]
        self.assertEqual(speech, ["premature final"])
        self.assertEqual(outcome.state.final_output["speech"], "premature final")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(self.published), 2)
        self.assertTrue(all(not job.model_consumed_by for job in self.published))

    def test_final_reply_and_exhausted_budget_do_not_join_unfinished_jobs(self):
        for limited in (False, True):
            with self.subTest(limited=limited):
                if limited:
                    # A fresh input scope avoids reusing the prior case's IDs.
                    self.registry.close(self.work)
                    self.turn = replace(self.turn, memory=replace(self.turn.memory, turn_id="turn-2"))
                    self.scope["session_id"] = "conversation"
                    self.manager.begin_input_turn({"source_id": "message-2", "content": "new task", "timestamp": 110},
                                                  turn_id="turn-2", **self.scope)
                    self.turn = replace(self.turn, memory=replace(self.turn.memory, source_id="message-2"))
                    self.engine._max_tool_rounds = lambda **kwargs: 1
                self.requests.clear()
                calls = [{"type": "document.compose", TOOL_SOURCE_FIELD: NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD: "pending"}]
                def generate(**kwargs):
                    self.requests.append(copy.deepcopy(kwargs))
                    if limited and len(self.requests) == 1:
                        self.jobs.append(self.job(200, turn_id="turn-2"))
                    if len(self.requests) == 1 or (limited and len(self.requests) == 2):
                        if False:
                            yield {}
                        return {"calls": calls}
                    return {"speech": "Still running in the background.", "calls": []}
                self.engine._generate_round = generate
                # The second case only needs scheduling facts, not new native
                # traces. The first case exercises the real MemCore batch.
                self.engine._execute_and_record_tool_batch = self.batch if not limited else lambda **kwargs: ([], [])
                started = time.monotonic()
                _events, outcome = exhaust(run_turn_rounds(self.engine, self.turn))
                self.assertLess(time.monotonic() - started, .8)
                self.assertEqual(outcome.state.final_output["speech"], "Still running in the background.")
                self.assertEqual(len(self.requests), 3 if limited else 2)

    def test_model_failure_does_not_acknowledge_unreceived_completion_input(self):
        _events, outcome = self.run_case(fail=True)
        self.assertTrue(outcome.state.final_output["_transient_final_failure"])
        self.assertEqual(len(self.published), 2)
        self.assertTrue(all(not job.model_consumed_by for job in self.published))

    def test_ready_results_join_the_first_continuation_without_an_extra_model_turn(self):
        original_batch = self.batch
        def batch(**kwargs):
            result = original_batch(**kwargs)
            for index, job in enumerate(self.jobs):
                self.finish(job, f"unique-result-{index}")
            return result
        initial = self.generator()
        def generate(**kwargs):
            if not self.requests:
                return (yield from initial(**kwargs))
            self.requests.append(copy.deepcopy(kwargs))
            yield {"type": "speech_segment", "text": "combined final"}
            return {"speech": "combined final", "calls": []}
        self.engine._execute_and_record_tool_batch = batch
        self.engine._generate_round = generate
        _events, outcome = exhaust(run_turn_rounds(self.engine, self.turn))
        self.assertEqual(outcome.state.final_output["speech"], "combined final")
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.published)
        rendered = json.dumps(self.requests[1]["post_user_turns"])
        self.assertEqual(rendered.count("unique-result-0"), 1)
        self.assertEqual(rendered.count("unique-result-1"), 1)

    def test_another_native_batch_preserves_the_prefix_after_completion_events(self):
        original_batch = self.batch
        def batch(**kwargs):
            first_new = len(self.jobs)
            result = original_batch(**kwargs)
            for index in range(first_new, len(self.jobs)):
                self.finish(self.jobs[index], f"unique-result-{index}")
            return result
        def generate(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            if len(self.requests) < 3:
                if False:
                    yield {}
                count = 2 if len(self.requests) == 1 else 1
                return {"calls": [{"type": "document.compose", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: f"call-{len(self.requests)}-{index}"} for index in range(count)]}
            return {"speech": "combined final", "calls": []}
        self.engine._execute_and_record_tool_batch = batch
        self.engine._generate_round = generate
        _events, outcome = exhaust(run_turn_rounds(self.engine, self.turn))
        self.assertEqual(outcome.state.final_output["speech"], "combined final")
        prefix = self.requests[1]["post_user_turns"]
        messages = self.requests[2]["post_user_turns"]
        self.assertEqual(messages[:len(prefix)], prefix)
        body = json.dumps(messages)
        self.assertTrue(all(body.count(f"unique-result-{index}") == 1 for index in range(3)))

    def test_late_result_preserves_images_state_files_and_dialogue_without_text_pixels(self):
        from companion_v01.store import MemoryStore
        from companion_v01.host_tool_job_runtime.results import stored_tool_result
        from tests.test_desktop_device_repair import picture
        self.engine.store = MemoryStore(self.root / "raw.db")
        pixels = picture()["imageBase64"]
        result = ToolExecutionResult(tool_type="document.compose", followup_context="visual result",
            model_image_inputs=[{"attachment_id": "late-picture", "mime_type": "image/png", "data_url": "data:image/png;base64," + pixels}],
            stream_events=[{"type": "generated_file_ready", "generated_file": {"generated_handle": "gen_001"}}],
            state_updates={"character_context": {"location": "library"}},
            raw_turns=[{"speaker": "售货员", "speech": "已经上架好了。"}])
        original_batch = self.batch
        def batch(**kwargs):
            actual = original_batch(**kwargs)
            for index, job in enumerate(self.jobs):
                self.finish(job, result.followup_context if index == 0 else "second result",
                            result=stored_tool_result(result) if index == 0 else {})
            return actual
        def generate(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            if len(self.requests) == 1:
                if False:
                    yield {}
                return {"calls": [{"type": "document.compose", TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                                  TOOL_INVOCATION_ID_FIELD: "with-picture"}]}
            return {"speech": "Observed the picture.", "calls": []}
        self.engine._execute_and_record_tool_batch = batch
        self.engine._generate_round = generate
        _events, outcome = exhaust(run_turn_rounds(self.engine, self.turn))
        self.assertEqual(outcome.state.tool_results[-1].state_updates, result.state_updates)
        self.assertEqual(outcome.state.tool_turns, result.raw_turns)
        self.assertIn("gen_001", json.dumps(outcome.state.tool_events))
        self.assertTrue(any(record["role"] == "npc:售货员" for record in outcome.state.recent_raw_for_turn))
        history = self.requests[-1]["post_user_turns"]
        self.assertIn(pixels, json.dumps(history))
        self.assertNotIn(pixels, str(self.manager.build_open_turn_projection(turn_id="turn-1", provider_profile="openai", **self.scope)))

    def test_idle_completion_restores_private_images_before_first_generation(self):
        from companion_v01.host_tool_job_runtime.results import stored_tool_result
        from tests.test_desktop_device_repair import picture
        image = {"attachment_id": "idle-picture", "mime_type": "image/png",
                 "data_url": "data:image/png;base64," + picture()["imageBase64"]}
        self.engine.job_store = self.store
        result = ToolExecutionResult(tool_type="document.compose", followup_context="finished during idle",
            model_image_inputs=[image], state_updates={"character_context": {"location": "desk"}})
        job = self.job(20, payload={"managed_invocation": True})
        self.finish(job, result.followup_context, result=stored_tool_result(result))
        self.turn = replace(self.turn, request=replace(self.turn.request,
            payload={"_host_completion_job_ids": [job.job_id]}))
        def generate(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            if False:
                yield {}
            return {"speech": "Observed the idle result.", "calls": []}
        self.engine._generate_round = generate
        _events, outcome = exhaust(run_turn_rounds(self.engine, self.turn))
        self.assertEqual(self.requests[0]["user_images"], [image])
        self.assertEqual(outcome.state.tool_results[0].state_updates, result.state_updates)

    def test_deferred_completion_batches_append_without_repeating_previous_results(self):
        with patch("companion_v01.host_completion_work.COMPLETION_BATCH_MAX_ITEMS", 1):
            _events, outcome = self.run_case()
        self.assertEqual(outcome.state.final_output["speech"], "combined final")
        self.assertEqual(len(self.requests), 4)
        prefix = self.requests[2]["post_user_turns"]
        messages = self.requests[3]["post_user_turns"]
        self.assertEqual(messages[:len(prefix)], prefix)
        body = json.dumps(messages)
        self.assertTrue(all(body.count(f"unique-result-{index}") == 1 for index in range(2)))


if __name__ == "__main__":
    unittest.main()
