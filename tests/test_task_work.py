import threading
import unittest

from companion_v01.task_work import TaskWork


class TaskWorkTests(unittest.TestCase):
    def test_join_observes_authority_once_and_does_not_invent_terminal_state(self):
        work = TaskWork()
        completed = threading.Event()
        work.track("job_a", inspect=lambda: {"status": "succeeded"} if completed.is_set() else None,
                   cancel=completed.set)
        self.assertEqual(work.collect(), [])
        self.assertTrue(work.pending)
        completed.set()
        self.assertEqual(work.collect(), [{"id": "job_a", "status": "succeeded"}])
        self.assertEqual(work.collect(), [])
        self.assertFalse(work.pending)

    def test_cancellation_unconfirmed_is_not_success(self):
        work = TaskWork()
        cancellations = []
        work.track("run_a", inspect=lambda: None, cancel=lambda: cancellations.append("run_a"))
        self.assertEqual(work.close(), ["run_a"])
        self.assertEqual(cancellations, ["run_a"])

    def test_wait_can_be_interrupted_without_a_model_request(self):
        work = TaskWork()
        work.track("job_a", inspect=lambda: None, cancel=lambda: None)
        self.assertEqual(work.wait(lambda: True), [])


if __name__ == "__main__":
    unittest.main()
