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

    def test_observed_terminal_tool_result_is_not_repeated_as_an_event(self):
        work = TaskWork()
        work.track("run_a", inspect=lambda: {"status": "completed"}, cancel=lambda: None)
        work.acknowledge("run_a")
        self.assertEqual(work.collect(), [])
        self.assertFalse(work.pending)

    def test_pause_blocks_wait_until_resume_and_preserves_task_identity(self):
        work = TaskWork()
        completed = threading.Event()
        work.track("job_a", inspect=lambda: {"status": "succeeded"} if completed.is_set() else None,
                   cancel=lambda: None)
        self.assertEqual(work.control("pause")["status"], "paused")
        observed = []
        waiter = threading.Thread(target=lambda: observed.extend(work.wait(lambda: False)), daemon=True)
        waiter.start()
        self.assertTrue(waiter.is_alive())
        completed.set()
        self.assertTrue(waiter.is_alive())
        self.assertEqual(work.control("resume")["status"], "running")
        waiter.join(timeout=2)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(observed, [{"id": "job_a", "status": "succeeded"}])
        self.assertEqual(work.resume_generation, 1)

    def test_stop_reports_request_until_unconfirmed_cancellation_is_observed(self):
        work = TaskWork()
        cancellations = []
        work.track("run_a", inspect=lambda: None, cancel=lambda: cancellations.append("run_a"))
        result = work.control("stop")
        self.assertEqual(result["status"], "stopping")
        self.assertEqual(cancellations, ["run_a"])
        self.assertEqual(work.state, "stopping")


if __name__ == "__main__":
    unittest.main()
