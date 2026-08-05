from __future__ import annotations

import unittest
from unittest.mock import patch

from memcore import LLMRequest, TaskType

from companion_v01.memcore_integration.adapters import build_akane_llm_client


class _JSONRuntime:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls = 0

    def call_aux_json(self, **_kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class MemcoreLLMAdapterTests(unittest.TestCase):
    @patch("companion_v01.memcore_integration.adapters.time.sleep")
    def test_json_call_retries_fallback_and_reports_actual_attempts(self, _sleep) -> None:
        fallback = {"summary": ""}
        runtime = _JSONRuntime([fallback, {"summary": "usable"}])
        client = build_akane_llm_client(runtime)

        result = client.call(
            LLMRequest(
                task_type=TaskType.SUMMARY,
                system_prompt="system",
                user_prompt="user",
                max_retries=2,
                fallback=fallback,
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"summary": "usable"})
        self.assertEqual(result.attempts, 2)
        self.assertEqual(runtime.calls, 2)
        _sleep.assert_called_once()

    @patch("companion_v01.memcore_integration.adapters.time.sleep")
    def test_json_call_exhausts_initial_attempt_plus_configured_retries(self, _sleep) -> None:
        fallback = {"summary": ""}
        runtime = _JSONRuntime([RuntimeError("502"), fallback, fallback])
        client = build_akane_llm_client(runtime)

        result = client.call(
            LLMRequest(
                task_type=TaskType.SUMMARY,
                system_prompt="system",
                user_prompt="user",
                max_retries=2,
                fallback=fallback,
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data, fallback)
        self.assertEqual(result.error, "fallback_returned")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(runtime.calls, 3)
        self.assertEqual(_sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
