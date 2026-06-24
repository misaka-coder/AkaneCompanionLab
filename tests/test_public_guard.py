import unittest

from companion_v01.public_guard import PublicThinkGuard


class PublicThinkGuardTests(unittest.TestCase):
    def test_disabled_guard_allows_without_acquire(self):
        guard = PublicThinkGuard(
            enabled=False,
            max_concurrent_thinks=2,
            daily_think_limit=10,
            busy_message="busy",
            daily_limit_message="limit",
        )

        decision = guard.try_acquire()

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.acquired)
        self.assertEqual("disabled", decision.reason)

    def test_concurrent_limit_blocks_second_request(self):
        guard = PublicThinkGuard(
            enabled=True,
            max_concurrent_thinks=1,
            daily_think_limit=10,
            busy_message="busy",
            daily_limit_message="limit",
        )

        first = guard.try_acquire()
        second = guard.try_acquire()

        self.assertTrue(first.allowed)
        self.assertTrue(first.acquired)
        self.assertFalse(second.allowed)
        self.assertEqual("busy", second.reason)
        self.assertEqual("busy", second.message)

    def test_daily_limit_blocks_after_threshold(self):
        guard = PublicThinkGuard(
            enabled=True,
            max_concurrent_thinks=2,
            daily_think_limit=1,
            busy_message="busy",
            daily_limit_message="limit",
        )

        first = guard.try_acquire()
        guard.release()
        second = guard.try_acquire()

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual("daily_limit", second.reason)
        self.assertEqual("limit", second.message)


if __name__ == "__main__":
    unittest.main()
