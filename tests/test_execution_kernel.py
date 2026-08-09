from __future__ import annotations

import json
import threading
import time
import unittest

from capcore_provider_openai import build_openai_chat_tool_set

from companion_v01.execution_run import (
    ExecCancelResult,
    ExecutionAvailability,
    ExecutionRunOwner,
    ExecutionRunStore,
    ExecRunStart,
    ExecRunStatus,
    execute_exec_cancel,
    execute_exec_run,
    execute_exec_status,
    make_cursor,
    map_exec_run_outcome,
    new_run_id,
    parse_cursor,
)
from companion_v01.execution_specs import (
    EXEC_CANCEL_RESULT_MAX_BYTES,
    EXEC_CANCEL_TOOL_SPEC,
    EXEC_OUTPUT_PAGE_BYTES,
    EXEC_RUN_RESULT_MAX_BYTES,
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RESULT_MAX_BYTES,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNAVAILABLE,
    EXEC_STATUS_UNKNOWN,
    EXEC_STATUS_TOOL_SPEC,
    EXEC_TOOL_SPECS,
    normalize_initial_wait_seconds,
    normalize_timeout_seconds,
)
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec


OWNER = ExecutionRunOwner(profile_user_id="profile-a", session_id="session-a", provider_id="local")
OTHER_OWNER = ExecutionRunOwner(profile_user_id="profile-b", session_id="session-b", provider_id="local")


class _ManualClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ExecutionSpecsTests(unittest.TestCase):
    def test_all_three_specs_project_to_openai_tools(self) -> None:
        projected = {spec.capability_id: build_openai_native_tool_from_spec(spec) for spec in EXEC_TOOL_SPECS}
        self.assertEqual(set(projected), {"exec_run", "exec_status", "exec_cancel"})
        for tool_id, tool in projected.items():
            self.assertEqual(tool["function"]["name"], tool_id)

    def test_exec_run_is_high_risk_and_always_confirmed(self) -> None:
        self.assertEqual(EXEC_RUN_TOOL_SPEC.risk, "high")
        self.assertEqual(EXEC_RUN_TOOL_SPEC.confirm, "always")
        self.assertIn("command_exec", EXEC_RUN_TOOL_SPEC.effects)
        self.assertNotIn("env", EXEC_RUN_TOOL_SPEC.input_schema["properties"])
        self.assertIs(EXEC_RUN_TOOL_SPEC.input_schema["additionalProperties"], False)

    def test_specs_bound_inputs_and_results(self) -> None:
        run_props = EXEC_RUN_TOOL_SPEC.input_schema["properties"]
        self.assertEqual(run_props["command"]["maxLength"], 8192)
        self.assertEqual(run_props["cwd"]["maxLength"], 512)
        self.assertEqual(EXEC_RUN_TOOL_SPEC.max_result_bytes, EXEC_RUN_RESULT_MAX_BYTES)
        self.assertEqual(EXEC_STATUS_TOOL_SPEC.max_result_bytes, EXEC_STATUS_RESULT_MAX_BYTES)
        self.assertEqual(EXEC_CANCEL_TOOL_SPEC.max_result_bytes, EXEC_CANCEL_RESULT_MAX_BYTES)
        self.assertIn("cancel_failed", EXEC_CANCEL_TOOL_SPEC.output_schema["properties"]["status"]["enum"])

    def test_exec_description_is_honest_about_trusted_not_sandboxed(self) -> None:
        self.assertIn("不是 Shell 沙箱", EXEC_RUN_TOOL_SPEC.description)
        self.assertIn("当前宿主用户权限", EXEC_RUN_TOOL_SPEC.description)

    def test_schema_projection_is_byte_deterministic(self) -> None:
        def dump() -> str:
            tools = [build_openai_native_tool_from_spec(spec) for spec in EXEC_TOOL_SPECS]
            return json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        self.assertEqual(dump(), dump())
        first = build_openai_chat_tool_set(tool_specs=EXEC_TOOL_SPECS)
        second = build_openai_chat_tool_set(tool_specs=EXEC_TOOL_SPECS)
        self.assertEqual(
            json.dumps([dict(tool) for tool in first.tools], sort_keys=True),
            json.dumps([dict(tool) for tool in second.tools], sort_keys=True),
        )

    def test_normalization_clamps_windows(self) -> None:
        self.assertEqual(normalize_timeout_seconds(None), 120)
        self.assertEqual(normalize_timeout_seconds(99999), 600)
        self.assertEqual(normalize_timeout_seconds(0), 1)
        self.assertEqual(normalize_initial_wait_seconds(None), 8)
        self.assertEqual(normalize_initial_wait_seconds(99), 10)
        self.assertEqual(normalize_initial_wait_seconds(0), 1)


class ExecutionRunStoreTests(unittest.TestCase):
    def _store(self, *, max_log_bytes=64 * 1024, page_bytes=EXEC_OUTPUT_PAGE_BYTES, retention=600, clock=None):
        return ExecutionRunStore(
            max_log_bytes=max_log_bytes,
            output_page_bytes=page_bytes,
            run_retention_seconds=retention,
            now=clock or time.time,
        )

    def test_owner_is_required_and_run_id_is_validated(self) -> None:
        store = self._store()
        with self.assertRaises(TypeError):
            store.register(new_run_id(), owner=None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            store.register("r", owner=OWNER)

    def test_register_terminal_transition_and_retention(self) -> None:
        clock = _ManualClock()
        store = self._store(retention=100, clock=clock)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        self.assertEqual(store.window_snapshot(run_id, owner=OWNER).status, EXEC_STATUS_RUNNING)
        self.assertTrue(store.mark_terminal(run_id, EXEC_STATUS_COMPLETED, owner=OWNER, exit_code=0))
        self.assertFalse(store.mark_terminal(run_id, EXEC_STATUS_FAILED, owner=OWNER, exit_code=1))
        clock.advance(101)
        self.assertEqual(store.evict_expired(), 1)
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_UNKNOWN)

    def test_wrong_owner_cannot_read_cancel_or_mutate(self) -> None:
        store = self._store()
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "secret", owner=OTHER_OWNER)
        self.assertEqual(store.read(run_id, owner=OTHER_OWNER).status, EXEC_STATUS_UNKNOWN)
        self.assertEqual(store.request_cancel(run_id, owner=OTHER_OWNER), EXEC_STATUS_UNKNOWN)
        self.assertFalse(store.confirm_cancelled(run_id, owner=OTHER_OWNER))
        self.assertEqual(store.read(run_id, owner=OWNER).tail, "")
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_RUNNING)

    def test_cancel_request_does_not_claim_process_stopped(self) -> None:
        store = self._store()
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        self.assertEqual(store.request_cancel(run_id, owner=OWNER), "cancel_requested")
        self.assertTrue(store.cancel_requested(run_id, owner=OWNER))
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_RUNNING)
        self.assertTrue(store.confirm_cancelled(run_id, owner=OWNER))
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_CANCELLED)

    def test_incremental_cursor_is_run_bound(self) -> None:
        store = self._store(page_bytes=1024)
        first_run, second_run = new_run_id(), new_run_id()
        store.register(first_run, owner=OWNER)
        store.register(second_run, owner=OWNER)
        store.append_output(first_run, "stdout", "one", owner=OWNER)
        cursor = store.read(first_run, owner=OWNER).next_cursor
        self.assertEqual(parse_cursor(cursor, run_id=first_run), 3)
        self.assertIsNone(parse_cursor(cursor, run_id=second_run))
        store.append_output(second_run, "stdout", "two", owner=OWNER)
        wrong = store.read(second_run, cursor=cursor, owner=OWNER)
        self.assertEqual(wrong.reason, "invalid_cursor")
        self.assertEqual(wrong.tail, "two")

    def test_repeated_compaction_uses_monotonic_absolute_cursor(self) -> None:
        store = self._store(max_log_bytes=1024, page_bytes=1024)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "A" * 512, owner=OWNER)
        store.append_output(run_id, "stdout", "B" * 512, owner=OWNER)
        store.append_output(run_id, "stdout", "C" * 512, owner=OWNER)
        first = store.read(run_id, cursor=make_cursor(run_id, 0), owner=OWNER)
        self.assertEqual(first.reason, "output_compacted")
        self.assertEqual(first.tail, "B" * 512 + "C" * 512)
        self.assertEqual(parse_cursor(first.next_cursor, run_id=run_id), 1536)

        for marker in ("D", "E", "F"):
            store.append_output(run_id, "stdout", marker * 512, owner=OWNER)
            page = store.read(run_id, cursor=first.next_cursor, owner=OWNER)
            self.assertEqual(page.tail, marker * 512)
            self.assertNotEqual(page.reason, "output_compacted")
            first = page
        self.assertEqual(parse_cursor(first.next_cursor, run_id=run_id), 3072)

    def test_unicode_paging_and_compaction_never_split_utf8(self) -> None:
        store = self._store(max_log_bytes=1024, page_bytes=257)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "你" * 500, owner=OWNER)
        cursor = None
        chunks: list[str] = []
        reasons: list[str] = []
        for _ in range(10):
            page = store.read(run_id, cursor=cursor, owner=OWNER)
            chunks.append(page.tail)
            reasons.append(page.reason)
            cursor = page.next_cursor
            if cursor is None:
                break
        retained = "".join(chunks)
        self.assertTrue(retained)
        self.assertEqual(set(retained), {"你"})
        self.assertIn("output_compacted", reasons)
        self.assertLessEqual(len(retained.encode("utf-8")), 1024)

    def test_terminal_keeps_cursor_until_all_pages_are_read(self) -> None:
        store = self._store(max_log_bytes=4096, page_bytes=512)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "X" * 1300, owner=OWNER)
        store.mark_terminal(run_id, EXEC_STATUS_COMPLETED, owner=OWNER, exit_code=0)
        cursor = None
        chunks: list[str] = []
        cursors: list[str | None] = []
        for _ in range(4):
            page = store.read(run_id, cursor=cursor, owner=OWNER)
            chunks.append(page.tail)
            cursors.append(page.next_cursor)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual("".join(chunks), "X" * 1300)
        self.assertIsNotNone(cursors[0])
        self.assertIsNotNone(cursors[1])
        self.assertIsNone(cursors[-1])

    def test_short_terminal_output_larger_than_window_is_not_silent(self) -> None:
        store = self._store(max_log_bytes=1024, page_bytes=512)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "A" * 2048, owner=OWNER)
        store.mark_terminal(run_id, EXEC_STATUS_COMPLETED, owner=OWNER, exit_code=0)
        snapshot = store.window_snapshot(run_id, owner=OWNER)
        self.assertEqual(snapshot.reason, "output_compacted")
        self.assertIsNotNone(snapshot.next_cursor)
        first = store.read(run_id, owner=OWNER)
        self.assertEqual(first.reason, "output_compacted")
        self.assertEqual(len(first.tail), 512)
        self.assertIsNotNone(first.next_cursor)

    def test_output_order_is_preserved_across_streams(self) -> None:
        store = self._store(page_bytes=1024)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "out1\n", owner=OWNER)
        store.append_output(run_id, "stderr", "err1\n", owner=OWNER)
        store.append_output(run_id, "stdout", "out2\n", owner=OWNER)
        self.assertEqual(store.read(run_id, owner=OWNER).tail, "out1\nerr1\nout2\n")

    def test_store_capacity_evicts_terminal_before_rejecting_running(self) -> None:
        store = ExecutionRunStore(max_runs=2)
        terminal, running, replacement = new_run_id(), new_run_id(), new_run_id()
        store.register(terminal, owner=OWNER)
        store.mark_terminal(terminal, EXEC_STATUS_COMPLETED, owner=OWNER, exit_code=0)
        store.register(running, owner=OWNER)
        store.register(replacement, owner=OWNER)
        self.assertEqual(store.read(terminal, owner=OWNER).status, EXEC_STATUS_UNKNOWN)
        with self.assertRaises(RuntimeError):
            store.register(new_run_id(), owner=OWNER)


class _FakeExecutionProvider:
    def __init__(
        self,
        store: ExecutionRunStore,
        *,
        availability: ExecutionAvailability | Exception | object | None = None,
        start: ExecRunStart | Exception | object | None = None,
        cancel_fails: bool = False,
    ) -> None:
        self.store = store
        self.availability_value = availability or ExecutionAvailability(True, "ready")
        self.start = start
        self.cancel_fails = cancel_fails
        self.run_called = False
        self.last_run_kwargs = None

    def availability(self):
        if isinstance(self.availability_value, Exception):
            raise self.availability_value
        return self.availability_value

    def run(self, *, owner, command, cwd="", timeout_seconds=120, initial_wait_seconds=8):
        self.run_called = True
        self.last_run_kwargs = {
            "owner": owner,
            "command": command,
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
            "initial_wait_seconds": initial_wait_seconds,
        }
        if isinstance(self.start, Exception):
            raise self.start
        if self.start is not None:
            return self.start
        run_id = new_run_id()
        self.store.register(run_id, owner=owner)
        self.store.append_output(run_id, "stdout", "done", owner=owner)
        self.store.mark_terminal(run_id, EXEC_STATUS_COMPLETED, owner=owner, exit_code=0)
        return self.store.window_snapshot(run_id, owner=owner)

    def status(self, *, owner, run_id, cursor=None) -> ExecRunStatus:
        return self.store.read(run_id, cursor=cursor, owner=owner)

    def cancel(self, *, owner, run_id) -> ExecCancelResult:
        requested = self.store.request_cancel(run_id, owner=owner)
        if requested == EXEC_STATUS_UNKNOWN:
            return ExecCancelResult(True, EXEC_STATUS_UNKNOWN, run_id, "run_not_found")
        if requested == "already_ended":
            return ExecCancelResult(True, "already_ended", run_id)
        if self.cancel_fails:
            return ExecCancelResult(False, "cancel_failed", run_id, "process_group_still_running")
        if not self.store.confirm_cancelled(run_id, owner=owner):
            return ExecCancelResult(False, "cancel_failed", run_id, "termination_not_confirmed")
        return ExecCancelResult(True, EXEC_STATUS_CANCELLED, run_id)


class ExecOrchestrationTests(unittest.TestCase):
    def _store(self) -> ExecutionRunStore:
        return ExecutionRunStore()

    def test_short_command_completes_in_turn(self) -> None:
        mapped = execute_exec_run(_FakeExecutionProvider(self._store()), owner=OWNER, command="echo done")
        self.assertEqual(mapped.envelope_status, "ok")
        self.assertEqual(mapped.event_status, EXEC_STATUS_COMPLETED)
        self.assertEqual(mapped.data["stdout"], "done")

    def test_long_command_returns_running_with_cursor(self) -> None:
        run_id = new_run_id()
        start = ExecRunStart(status=EXEC_STATUS_RUNNING, run_id=run_id, next_cursor=make_cursor(run_id, 0))
        mapped = execute_exec_run(_FakeExecutionProvider(self._store(), start=start), owner=OWNER, command="slow")
        self.assertEqual(mapped.envelope_status, "ok")
        self.assertEqual(mapped.event_status, EXEC_STATUS_RUNNING)
        self.assertIn("exec_status", mapped.model_feedback)

    def test_run_normalizes_and_forwards_bounded_arguments(self) -> None:
        provider = _FakeExecutionProvider(self._store())
        execute_exec_run(
            provider,
            owner=OWNER,
            command="  echo done  ",
            cwd="  workspace:/job  ",
            timeout_seconds=9999,
            initial_wait_seconds=99,
        )
        self.assertEqual(
            provider.last_run_kwargs,
            {
                "owner": OWNER,
                "command": "echo done",
                "cwd": "workspace:/job",
                "timeout_seconds": 600,
                "initial_wait_seconds": 10,
            },
        )

    def test_failed_timeout_and_cancelled_are_errors(self) -> None:
        cases = (
            ExecRunStart(EXEC_STATUS_FAILED, new_run_id(), exit_code=2, stderr="boom"),
            ExecRunStart(EXEC_STATUS_TIMED_OUT, new_run_id(), reason="timeout"),
            ExecRunStart(EXEC_STATUS_CANCELLED, new_run_id(), reason="cancelled"),
        )
        for start in cases:
            with self.subTest(status=start.status):
                mapped = map_exec_run_outcome(start)
                self.assertEqual(mapped.envelope_status, "error")
                self.assertEqual(mapped.event_status, start.status)
                self.assertIn("不要声称成功", mapped.model_feedback)

    def test_cancel_failure_does_not_change_run_to_cancelled(self) -> None:
        store = self._store()
        provider = _FakeExecutionProvider(store, cancel_fails=True)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        result = provider.cancel(owner=OWNER, run_id=run_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "cancel_failed")
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_RUNNING)

    def test_cancel_success_requires_provider_confirmation(self) -> None:
        store = self._store()
        provider = _FakeExecutionProvider(store)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        result = provider.cancel(owner=OWNER, run_id=run_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, EXEC_STATUS_CANCELLED)
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_CANCELLED)

    def test_status_wrapper_preserves_pages_and_structures_unknown(self) -> None:
        store = ExecutionRunStore(output_page_bytes=512)
        provider = _FakeExecutionProvider(store)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        store.append_output(run_id, "stdout", "X" * 800, owner=OWNER)
        store.mark_terminal(run_id, EXEC_STATUS_COMPLETED, owner=OWNER, exit_code=0)
        first = execute_exec_status(provider, owner=OWNER, run_id=run_id)
        self.assertEqual(first.envelope_status, "ok")
        self.assertEqual(len(first.data["tail"]), 512)
        self.assertIsNotNone(first.data["next_cursor"])
        second = execute_exec_status(provider, owner=OWNER, run_id=run_id, cursor=first.data["next_cursor"])
        self.assertEqual(len(second.data["tail"]), 288)
        self.assertIsNone(second.data["next_cursor"])
        hidden = execute_exec_status(provider, owner=OTHER_OWNER, run_id=run_id)
        self.assertEqual(hidden.event_status, EXEC_STATUS_UNKNOWN)
        self.assertNotIn("owner", json.dumps(hidden.data))

    def test_status_wrapper_catches_provider_errors_and_invalid_results(self) -> None:
        class Broken(_FakeExecutionProvider):
            def status(self, **_kwargs):
                raise RuntimeError("boom")

        run_id = new_run_id()
        broken = execute_exec_status(Broken(self._store()), owner=OWNER, run_id=run_id)
        self.assertEqual(broken.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)

        class Oversized(_FakeExecutionProvider):
            def status(self, **_kwargs):
                return ExecRunStatus(EXEC_STATUS_RUNNING, run_id, tail="X" * (EXEC_OUTPUT_PAGE_BYTES + 1))

        oversized = execute_exec_status(Oversized(self._store()), owner=OWNER, run_id=run_id)
        self.assertEqual(oversized.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)

    def test_cancel_wrapper_only_accepts_confirmed_success(self) -> None:
        store = self._store()
        provider = _FakeExecutionProvider(store)
        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        cancelled = execute_exec_cancel(provider, owner=OWNER, run_id=run_id)
        self.assertEqual(cancelled.envelope_status, "ok")
        self.assertEqual(cancelled.event_status, EXEC_STATUS_CANCELLED)

        run_id = new_run_id()
        store.register(run_id, owner=OWNER)
        failed = execute_exec_cancel(_FakeExecutionProvider(store, cancel_fails=True), owner=OWNER, run_id=run_id)
        self.assertEqual(failed.envelope_status, "error")
        self.assertEqual(failed.event_status, "cancel_failed")
        self.assertIn("不要声称取消成功", failed.model_feedback)
        self.assertEqual(store.read(run_id, owner=OWNER).status, EXEC_STATUS_RUNNING)

    def test_cancel_wrapper_catches_exception_and_wrong_owner(self) -> None:
        class Broken(_FakeExecutionProvider):
            def cancel(self, **_kwargs):
                raise RuntimeError("boom")

        run_id = new_run_id()
        failed = execute_exec_cancel(Broken(self._store()), owner=OWNER, run_id=run_id)
        self.assertEqual(failed.event_status, "cancel_failed")
        hidden = execute_exec_cancel(_FakeExecutionProvider(self._store()), owner=OTHER_OWNER, run_id=run_id)
        self.assertEqual(hidden.event_status, EXEC_STATUS_UNKNOWN)

    def test_unready_availability_never_calls_run(self) -> None:
        for status in ("warming", "failed", "unavailable", "unknown"):
            provider = _FakeExecutionProvider(self._store(), availability=ExecutionAvailability(True, status, status))
            mapped = execute_exec_run(provider, owner=OWNER, command="echo")
            self.assertEqual(mapped.envelope_status, "unavailable")
            self.assertFalse(provider.run_called)

    def test_availability_and_run_exceptions_are_structured(self) -> None:
        unavailable = execute_exec_run(
            _FakeExecutionProvider(self._store(), availability=RuntimeError("probe")),
            owner=OWNER,
            command="echo",
        )
        self.assertEqual(unavailable.envelope_status, "unavailable")
        failed = execute_exec_run(
            _FakeExecutionProvider(self._store(), start=RuntimeError("run")),
            owner=OWNER,
            command="echo",
        )
        self.assertEqual(failed.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)
        self.assertEqual(failed.envelope_status, "error")

    def test_invalid_provider_snapshots_are_execution_unknown(self) -> None:
        run_id = new_run_id()
        cases = (
            object(),
            ExecRunStart("surprise", run_id),
            ExecRunStart(EXEC_STATUS_RUNNING, ""),
            ExecRunStart(EXEC_STATUS_RUNNING, run_id),
            ExecRunStart(EXEC_STATUS_COMPLETED, run_id, exit_code=9),
            ExecRunStart(EXEC_STATUS_COMPLETED, run_id, exit_code=0, stdout="X" * (EXEC_OUTPUT_PAGE_BYTES + 1)),
            ExecRunStart(EXEC_STATUS_RUNNING, run_id, next_cursor="bad"),
        )
        for start in cases:
            with self.subTest(start=start):
                provider = _FakeExecutionProvider(self._store(), start=start)
                mapped = execute_exec_run(provider, owner=OWNER, command="echo")
                self.assertEqual(mapped.envelope_status, "error")
                self.assertEqual(mapped.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)

    def test_unavailable_is_distinct_from_execution_unknown(self) -> None:
        mapped = map_exec_run_outcome(ExecRunStart(EXEC_STATUS_UNAVAILABLE, reason="offline"))
        self.assertEqual(mapped.envelope_status, "unavailable")
        self.assertEqual(mapped.event_status, EXEC_STATUS_UNAVAILABLE)
        self.assertIn("capability_unavailable", mapped.model_feedback)

    def test_invalid_owner_and_request_are_structured(self) -> None:
        provider = _FakeExecutionProvider(self._store())
        missing_owner = execute_exec_run(provider, owner=None, command="echo")  # type: ignore[arg-type]
        self.assertEqual(missing_owner.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)
        empty = execute_exec_run(provider, owner=OWNER, command="")
        self.assertEqual(empty.event_status, EXEC_STATUS_EXECUTION_UNKNOWN)
        self.assertFalse(provider.run_called)


if __name__ == "__main__":
    unittest.main()
