from __future__ import annotations

import threading
import time
import unittest

from companion_v01.summary_queue import SummaryTaskQueue


class SummaryTaskQueueTests(unittest.TestCase):
    def test_enqueue_runs_handler_in_background(self) -> None:
        seen: list[tuple[str, str, int]] = []
        done = threading.Event()

        def handler(task) -> None:
            seen.append((task.profile_user_id, task.session_id, task.generation))
            done.set()

        queue = SummaryTaskQueue(handler)
        self.addCleanup(queue.close)

        queued = queue.enqueue(profile_user_id="user_a", session_id="session_a", generation=3)

        self.assertTrue(queued)
        self.assertTrue(done.wait(1.0))
        self.assertEqual(seen, [("user_a", "session_a", 3)])

    def test_clear_pending_drops_waiting_tasks(self) -> None:
        release_first = threading.Event()
        first_started = threading.Event()
        seen: list[str] = []

        def handler(task) -> None:
            seen.append(task.session_id)
            if task.session_id == "session_a":
                first_started.set()
                release_first.wait(1.0)

        queue = SummaryTaskQueue(handler)
        self.addCleanup(queue.close)

        self.assertTrue(queue.enqueue(profile_user_id="user_a", session_id="session_a", generation=1))
        self.assertTrue(first_started.wait(1.0))
        self.assertTrue(queue.enqueue(profile_user_id="user_a", session_id="session_b", generation=1))

        queue.clear_pending()
        release_first.set()
        time.sleep(0.15)

        self.assertEqual(seen, ["session_a"])


if __name__ == "__main__":
    unittest.main()
