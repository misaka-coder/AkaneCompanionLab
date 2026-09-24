from contextvars import ContextVar
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from companion_v01.tool_batch import execute_tool_batch
from companion_v01.tool_handlers.core import ToolExecutionResult, ToolMetadata
from companion_v01.tool_invocation import TOOL_INVOCATION_ID_FIELD


class ToolBatchTests(unittest.TestCase):
    def handler(self, call):
        if call["type"] == "unknown":
            return None
        return SimpleNamespace(tool_metadata=lambda: ToolMetadata(operation=call["type"]))

    def test_reads_overlap_inherit_context_and_keep_original_order(self):
        barrier = threading.Barrier(2)
        scope = ContextVar("batch_test_scope")
        token = scope.set("child-a")
        self.addCleanup(scope.reset, token)
        def execute(call):
            self.assertEqual(scope.get(), "child-a")
            barrier.wait(timeout=3)
            return ToolExecutionResult(tool_type="read", followup_context=call[TOOL_INVOCATION_ID_FIELD])
        calls = [{"type": "read", TOOL_INVOCATION_ID_FIELD: str(n)} for n in range(2)]
        results = execute_tool_batch(calls, execute=execute, handler_for=self.handler)
        self.assertEqual([r.followup_context for r in results], ["0", "1"])
        first, second = [r.execution_timing for r in results]
        # Barrier proves overlap; Windows wall-clock samples may share one tick.
        self.assertLessEqual(max(first["started_at"], second["started_at"]),
                        min(first["finished_at"], second["finished_at"]))
        self.assertTrue(all(r.execution_timing["duration_ms"] >= 0 for r in results))

    def test_writes_and_unknown_calls_are_order_barriers(self):
        order = []
        def execute(call):
            order.append(call["id"])
            return ToolExecutionResult(tool_type=call["type"], followup_context=str(order))
        calls = [{"type": kind, "id": n} for n, kind in enumerate(("write", "read", "unknown", "read", "write", "read"))]
        results = execute_tool_batch(calls, execute=execute, handler_for=self.handler)
        self.assertEqual(order, list(range(6)))
        self.assertEqual(len(results), 6)

    def test_failure_does_not_discard_other_results_or_expose_error_text(self):
        def execute(call):
            if call["id"] == 1:
                raise RuntimeError("secret-provider-value")
            return ToolExecutionResult(tool_type="read", followup_context="ok")
        results = execute_tool_batch([{"type": "read", "id": n} for n in range(3)],
                                     execute=execute, handler_for=self.handler)
        self.assertEqual([r.followup_context for r in (results[0], results[2])], ["ok", "ok"])
        self.assertEqual(results[1].stream_events[0]["status"], "failed")
        self.assertNotIn("secret-provider-value", str(results))

    def test_cancel_keeps_completed_result_and_pairs_unstarted_calls(self):
        stopped = threading.Event()
        executed = []
        def execute(call):
            executed.append(call["id"])
            stopped.set()
            return ToolExecutionResult(tool_type="write", followup_context="written")
        results = execute_tool_batch([{"type": "write", "id": n} for n in range(3)],
                                     execute=execute, handler_for=self.handler, cancelled=stopped.is_set)
        self.assertEqual(executed, [0])
        self.assertEqual(results[0].followup_context, "written")
        self.assertEqual([r.stream_events[0]["status"] for r in results[1:]], ["cancelled", "cancelled"])

    def test_more_calls_than_width_are_not_dropped(self):
        calls = [{"type": "read", "id": n} for n in range(11)]
        results = execute_tool_batch(calls, handler_for=self.handler,
            execute=lambda call: ToolExecutionResult(tool_type="read", followup_context=str(call["id"])))
        self.assertEqual([r.followup_context for r in results], [str(n) for n in range(11)])

    def test_diagnostics_failure_preserves_outcome_and_cached_receipt(self):
        receipt = ToolExecutionResult(tool_type="write", followup_context="written")
        with patch("companion_v01.tool_batch.logger.info", side_effect=OSError("log disk unavailable")):
            results = execute_tool_batch([{"type": "write"}], handler_for=self.handler,
                                         execute=lambda call: receipt)
        self.assertEqual(results[0].followup_context, "written")
        self.assertTrue(results[0].execution_timing)
        self.assertEqual(receipt.execution_timing, {})


if __name__ == "__main__":
    unittest.main()
