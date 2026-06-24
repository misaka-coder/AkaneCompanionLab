from __future__ import annotations

import threading
import time
import unittest

from companion_v01.background_tasks import BackgroundTaskRunner


class BackgroundTaskRunnerTests(unittest.TestCase):
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
