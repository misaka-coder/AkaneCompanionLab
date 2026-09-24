from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from companion_v01.background_tasks import BackgroundTaskRunner


class BackgroundTaskRunnerTests(unittest.TestCase):
    def test_wait_idle_includes_a_lane_created_by_a_running_task(self) -> None:
        runner = BackgroundTaskRunner()
        release_parent = threading.Event()
        release_child = threading.Event()
        waiting_on_parent = threading.Event()
        child_started = threading.Event()
        waiter_finished = threading.Event()
        results = []

        def child() -> None:
            child_started.set()
            release_child.wait(timeout=3.0)

        def parent() -> None:
            release_parent.wait(timeout=3.0)
            runner.submit(lane="child", name="child", fn=child)

        runner.submit(lane="parent", name="parent", fn=parent)
        parent_lane = runner._lanes["parent"]
        original_wait = parent_lane.wait_idle

        def observe_wait(*, timeout: float) -> bool:
            waiting_on_parent.set()
            return original_wait(timeout=timeout)

        def wait_all() -> None:
            results.append(runner.wait_idle(timeout=3.0))
            waiter_finished.set()

        waiter = threading.Thread(target=wait_all, daemon=True)
        try:
            with patch.object(parent_lane, "wait_idle", side_effect=observe_wait):
                waiter.start()
                self.assertTrue(waiting_on_parent.wait(timeout=1.0))
                release_parent.set()
                self.assertTrue(child_started.wait(timeout=1.0))
                self.assertFalse(waiter_finished.wait(timeout=0.1))
                release_child.set()
                self.assertTrue(waiter_finished.wait(timeout=1.0))
                self.assertEqual(results, [True])
        finally:
            release_parent.set()
            release_child.set()
            waiter.join(timeout=3.0)
            runner.close()

    def test_submit_runs_task_and_wait_idle(self) -> None:
        runner = BackgroundTaskRunner({"attachment": 1})
        self.addCleanup(runner.close)
        done = threading.Event()
        values: list[str] = []

        handle = runner.submit(
            lane="attachment",
            name="unit-test",
            fn=lambda: (values.append("ok"), done.set()),
        )

        self.assertEqual(handle.lane, "attachment")
        self.assertTrue(done.wait(timeout=2.0))
        self.assertTrue(runner.wait_idle(lane="attachment", timeout=2.0))
        self.assertEqual(values, ["ok"])

    def test_lane_limits_concurrency(self) -> None:
        runner = BackgroundTaskRunner({"attachment": 1})
        self.addCleanup(runner.close)
        order: list[str] = []
        first_started = threading.Event()
        release_first = threading.Event()

        def first() -> None:
            order.append("first-start")
            first_started.set()
            release_first.wait(timeout=2.0)
            order.append("first-end")

        def second() -> None:
            order.append("second")

        runner.submit(lane="attachment", name="first", fn=first)
        self.assertTrue(first_started.wait(timeout=2.0))
        runner.submit(lane="attachment", name="second", fn=second)
        time.sleep(0.05)
        self.assertEqual(order, ["first-start"])

        release_first.set()
        self.assertTrue(runner.wait_idle(lane="attachment", timeout=2.0))
        self.assertEqual(order, ["first-start", "first-end", "second"])


if __name__ == "__main__":
    unittest.main()
