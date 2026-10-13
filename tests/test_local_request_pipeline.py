"""Provider admission must not wait for optional capability startup."""
import threading
import time
import unittest
from unittest.mock import patch

import config
from companion_v01.managed_browser.sessions import ManagedBrowserSessionManager
from companion_v01.engine_services import response_budget
from tests.test_memcore_integration import _PromptContextEngine
from tests import test_response_context


class BrowserReadinessAdmissionTests(unittest.TestCase):
    def test_slow_probe_returns_pending_without_advertising_success_or_repeating_work(self):
        started, release = threading.Event(), threading.Event()
        calls, closed = [], []

        class Runner:
            def capability_status(self):
                calls.append(1)
                started.set()
                release.wait(5)
                return {"enabled": True, "status": "ready", "reason": ""}

            def shutdown(self):
                closed.append(1)

        manager = ManagedBrowserSessionManager(runner_factory=Runner, background_probe=True)
        self.addCleanup(manager.shutdown)
        self.addCleanup(release.set)
        self.assertTrue(started.wait(2))
        before = time.perf_counter()
        statuses = [manager.capability_status() for _ in range(10)]
        self.assertLess(time.perf_counter() - before, .1)
        self.assertTrue(all(not status["enabled"] and status["status"] == "pending" for status in statuses))
        release.set()
        manager._probe_future.result(timeout=2)
        ready = manager.capability_status()
        self.assertEqual(ready["status"], "ready")
        ready["enabled"] = False
        self.assertTrue(manager.capability_status()["enabled"])
        self.assertEqual(calls, [1])
        self.assertEqual(closed, [1])

    def test_failed_startup_remains_unavailable_and_does_not_retry_on_each_turn(self):
        calls = []

        def unavailable():
            calls.append(1)
            raise RuntimeError("synthetic missing browser")

        manager = ManagedBrowserSessionManager(runner_factory=unavailable, background_probe=True)
        self.addCleanup(manager.shutdown)
        manager._probe_future.result(timeout=2)
        for _ in range(3):
            status = manager.capability_status()
            self.assertFalse(status["enabled"])
            self.assertEqual(status["reason"], "browser_runner_status_failed")
        self.assertEqual(calls, [1])


class PromptBudgetAdmissionTests(unittest.TestCase):
    def test_unchanged_context_is_estimated_once_with_budget_enabled_or_disabled(self):
        for limit in (0, 1_000_000):
            with self.subTest(limit=limit), patch.object(config, "MEMORY_BACKEND", "legacy"), \
                    patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", limit), \
                    patch.object(response_budget, "_estimate_generation_context_tokens",
                        wraps=response_budget._estimate_generation_context_tokens) as estimate:
                context = test_response_context.ResponseContextTests().prepare(_PromptContextEngine(memcore_manager=None))
                self.assertEqual(estimate.call_count, 1)
                budget = context["prompt_budget"]
                self.assertEqual(budget["initial_estimated_tokens"], budget["final_estimated_tokens"])
                self.assertEqual(budget["trimmed_layers"], [])


if __name__ == "__main__":
    unittest.main()
