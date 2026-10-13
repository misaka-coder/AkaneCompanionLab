from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from companion_v01.execution_run import (
    ExecutionAvailability,
    ExecutionRunOwner,
    ExecRunStart,
    ExecRunStatus,
    execute_exec_run,
    execute_exec_status,
    make_cursor,
    map_exec_run_outcome,
)
from companion_v01.execution_specs import EXEC_INITIAL_OUTPUT_MAX_BYTES


class _SnapshotProvider:
    def __init__(self, *, start=None, status=None):
        self.start = start
        self.status_result = status
        self.run_calls = []

    def availability(self):
        return ExecutionAvailability(True, "ready")

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        return self.start

    def status(self, **kwargs):
        return self.status_result


class ExecutionRunBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.owner = ExecutionRunOwner("profile", "session", "provider")
        self.run_id = "execrun_" + "1" * 32
        self.other_run_id = "execrun_" + "2" * 32

    def _run(self, start, **kwargs):
        return execute_exec_run(
            _SnapshotProvider(start=start), owner=self.owner, command="echo sample", **kwargs
        )

    def _status(self, status):
        return execute_exec_status(
            _SnapshotProvider(status=status), owner=self.owner, run_id=self.run_id
        )

    def _assert_unknown(self, result, reason):
        self.assertEqual(result.envelope_status, "error")
        self.assertEqual(result.event_status, "execution_unknown")
        self.assertEqual(result.reason, reason)
        self.assertEqual(result.data["status"], "execution_unknown")
        self.assertEqual(result.data["reason"], reason)
        self.assertEqual(result.event["reason"], reason)
        json.dumps(asdict(result), allow_nan=False)

    def test_run_rejects_malformed_timestamps_without_raising(self):
        start = ExecRunStart("completed", self.run_id, exit_code=0, stdout="actual output")
        for field in ("started_at", "finished_at"):
            for kind, value in (("text", "not-a-time"), ("object", object()), ("overflow", 10**1000)):
                with self.subTest(field=field, kind=kind):
                    result = self._run(replace(start, **{field: value}))
                    self._assert_unknown(result, "invalid_execution_timestamps")
                    self.assertEqual(result.data["stdout"], "")

    def test_status_rejects_malformed_timestamps_without_raising(self):
        status = ExecRunStatus("completed", self.run_id, exit_code=0, tail="actual output")
        for field in ("started_at", "finished_at"):
            for kind, value in (("text", "not-a-time"), ("object", object()), ("overflow", 10**1000)):
                with self.subTest(field=field, kind=kind):
                    result = self._status(replace(status, **{field: value}))
                    self._assert_unknown(result, "invalid_status_timestamps")
                    self.assertEqual(result.data["tail"], "")

    def test_run_rejects_non_finite_timestamps(self):
        start = ExecRunStart("completed", self.run_id, exit_code=0)
        for field in ("started_at", "finished_at"):
            for value in (float("nan"), float("inf"), -float("inf"), "nan", "inf"):
                with self.subTest(field=field, value=str(value)):
                    self._assert_unknown(
                        self._run(replace(start, **{field: value})), "invalid_execution_timestamps"
                    )

    def test_status_rejects_non_finite_timestamps(self):
        status = ExecRunStatus("completed", self.run_id, exit_code=0)
        for field in ("started_at", "finished_at"):
            for value in (float("nan"), float("inf"), -float("inf"), "nan", "inf"):
                with self.subTest(field=field, value=str(value)):
                    self._assert_unknown(
                        self._status(replace(status, **{field: value})), "invalid_status_timestamps"
                    )

    def test_valid_run_timestamps_keep_numeric_coercion_and_zero_semantics(self):
        for value, expected in ((None, None), ("", None), (0, None), (-5, None), ("12.5", 12.5), (30, 30.0)):
            with self.subTest(value=value):
                result = self._run(ExecRunStart(
                    "completed", self.run_id, exit_code=0, started_at=value, finished_at=value
                ))
                self.assertEqual(result.envelope_status, "ok")
                self.assertEqual(result.data["started_at"], expected)
                self.assertEqual(result.data["finished_at"], expected)
                json.dumps(asdict(result), allow_nan=False)

    def test_valid_status_timestamps_keep_numeric_coercion_and_zero_semantics(self):
        for value, expected in ((None, None), ("", None), (0, None), (-5, None), ("12.5", 12.5), (30, 30.0)):
            with self.subTest(value=value):
                result = self._status(ExecRunStatus(
                    "completed", self.run_id, exit_code=0, started_at=value, finished_at=value
                ))
                self.assertEqual(result.envelope_status, "ok")
                self.assertEqual(result.data["started_at"], expected)
                self.assertEqual(result.data["finished_at"], expected)
                json.dumps(asdict(result), allow_nan=False)

    def test_assigned_run_id_rejects_result_for_another_run(self):
        for status in ("completed", "running", "failed", "execution_unknown", "unavailable"):
            with self.subTest(status=status):
                provider = _SnapshotProvider(start=ExecRunStart(
                    status, self.other_run_id, exit_code=0,
                    stdout="another task output", stderr="another task error",
                    next_cursor=make_cursor(self.other_run_id, 0),
                    output_ref=f"runlog:{self.other_run_id}",
                ))
                result = execute_exec_run(
                    provider, owner=self.owner, command="echo sample", run_id=self.run_id
                )
                self.assertEqual(provider.run_calls[0]["run_id"], self.run_id)
                self._assert_unknown(result, "execution_run_id_mismatch")
                self.assertEqual(result.data["run_id"], self.run_id)
                for field in ("stdout", "stderr"):
                    self.assertEqual(result.data[field], "")
                for field in ("exit_code", "next_cursor", "output_ref"):
                    self.assertIsNone(result.data[field])
                visible = json.dumps(asdict(result))
                self.assertNotIn(self.other_run_id, visible)
                self.assertNotIn("another task", visible)

    def test_assigned_run_id_accepts_matching_result(self):
        provider = _SnapshotProvider(start=ExecRunStart(
            "completed", self.run_id, exit_code=0, stdout="matching output"
        ))
        result = execute_exec_run(
            provider, owner=self.owner, command="echo sample", run_id=self.run_id
        )
        self.assertEqual(result.envelope_status, "ok")
        self.assertEqual(result.data["run_id"], self.run_id)
        self.assertEqual(result.data["stdout"], "matching output")
        self.assertEqual(provider.run_calls[0]["run_id"], self.run_id)

    def test_provider_can_allocate_run_id_when_not_assigned(self):
        provider = _SnapshotProvider(start=ExecRunStart("completed", self.other_run_id, exit_code=0))
        result = execute_exec_run(provider, owner=self.owner, command="echo sample")
        self.assertEqual(result.envelope_status, "ok")
        self.assertEqual(result.data["run_id"], self.other_run_id)
        self.assertNotIn("run_id", provider.run_calls[0])

    def test_unavailable_result_without_run_id_remains_unavailable(self):
        result = self._run(ExecRunStart("unavailable", reason="provider_offline"), run_id=self.run_id)
        self.assertEqual(result.envelope_status, "unavailable")
        self.assertEqual(result.reason, "provider_offline")
        self.assertEqual(result.data["run_id"], "")

    def test_failed_result_with_malformed_run_id_remains_unknown(self):
        result = map_exec_run_outcome(ExecRunStart("failed", "malformed", reason="spawn_failed"))
        self._assert_unknown(result, "invalid_execution_run_id")

    def test_initial_execution_unknown_preserves_sanitized_cause_and_evidence(self):
        result = self._run(ExecRunStart(
            "execution_unknown", self.run_id,
            stdout="last confirmed output", stderr="last confirmed error",
            reason="termination_unconfirmed; token=sample-only",
        ))
        self._assert_unknown(result, "termination_unconfirmed; token=[redacted]")
        self.assertEqual(result.data["stdout"], "last confirmed output")
        self.assertEqual(result.data["stderr"], "last confirmed error")
        self.assertIn("last confirmed output", result.model_feedback)
        self.assertNotIn("sample-only", json.dumps(asdict(result)))
        self.assertNotIn("process_group=terminated", result.model_feedback)
        self.assertNotIn("[cancelled", result.model_feedback)

    def test_initial_execution_unknown_without_reason_explains_uncertainty(self):
        result = self._run(ExecRunStart("execution_unknown", self.run_id))
        self._assert_unknown(result, "termination_unconfirmed")
        self.assertIn("unknown", result.model_feedback)

    def test_initial_execution_unknown_still_validates_run_id_and_page(self):
        start = ExecRunStart("execution_unknown", self.run_id, reason="termination_unconfirmed")
        cases = (
            ({"run_id": ""}, "invalid_execution_run_id"),
            ({"stdout": "x" * (EXEC_INITIAL_OUTPUT_MAX_BYTES + 1)}, "execution_output_page_too_large"),
            ({"next_cursor": make_cursor(self.other_run_id, 0)}, "invalid_execution_cursor"),
            ({"output_ref": f"runlog:{self.other_run_id}"}, "invalid_execution_output_ref"),
        )
        for values, reason in cases:
            with self.subTest(reason=reason):
                self._assert_unknown(map_exec_run_outcome(replace(start, **values)), reason)


if __name__ == "__main__":
    unittest.main()
