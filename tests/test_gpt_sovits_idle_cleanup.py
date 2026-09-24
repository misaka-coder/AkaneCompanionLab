from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.launch_gpt_sovits_api import IdleTTSCacheCleanup


class Timer:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


class Clock:
    def __init__(self):
        self.timers = []

    def call_later(self, seconds, callback):
        timer = Timer(callback)
        self.timers.append((seconds, timer))
        return timer


class Pipeline:
    def __init__(self):
        self.cleanups = 0
        self.inputs = []

    def empty_cache(self):
        self.cleanups += 1

    def run(self, value):
        self.inputs.append(value)
        try:
            if value == "fail":
                raise ValueError("provider_failure")
            yield (32000, b"exact_provider_audio")
        finally:
            self.empty_cache()


class IdleTTSCacheCleanupTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline()
        self.clock = Clock()
        self.policy = IdleTTSCacheCleanup(self.pipeline)
        self.policy.install()
        self.loop_patch = patch("scripts.launch_gpt_sovits_api.asyncio.get_running_loop", return_value=self.clock)
        self.loop_patch.start()
        self.addCleanup(self.loop_patch.stop)

    def synthesize(self, value="hello"):
        generator = self.pipeline.run(value)
        result = next(generator)
        generator.close()  # Upstream non-streaming API takes one yield and closes.
        return result

    def test_audio_return_is_not_blocked_by_full_cleanup_and_idle_flushes_once(self):
        self.assertEqual(self.synthesize(), (32000, b"exact_provider_audio"))
        self.assertEqual(self.pipeline.cleanups, 0)
        delay, timer = self.clock.timers[-1]
        self.assertEqual(delay, 10.0)
        timer.fire()
        timer.fire()
        self.assertEqual(self.pipeline.cleanups, 1)

    def test_next_sentence_cancels_idle_cleanup_until_its_own_completion(self):
        self.synthesize("first")
        old_timer = self.clock.timers[-1][1]
        second = self.pipeline.run("second")
        self.assertEqual(next(second), (32000, b"exact_provider_audio"))
        old_timer.fire()
        self.assertTrue(old_timer.cancelled)
        self.assertEqual(self.pipeline.cleanups, 0)
        second.close()
        self.assertEqual(self.pipeline.inputs, ["first", "second"])
        self.clock.timers[-1][1].fire()
        self.assertEqual(self.pipeline.cleanups, 1)

    def test_provider_failure_remains_failure_and_cleans_immediately(self):
        with self.assertRaisesRegex(ValueError, "provider_failure"):
            next(self.pipeline.run("fail"))
        self.assertEqual(self.pipeline.cleanups, 1)
        self.assertEqual(self.policy.active_runs, 0)
        self.assertEqual(self.clock.timers, [])

    def test_outside_inference_cleanup_for_weight_changes_remains_immediate(self):
        self.synthesize()
        timer = self.clock.timers[-1][1]
        self.pipeline.empty_cache()
        timer.fire()
        self.assertEqual(self.pipeline.cleanups, 1)
        self.assertTrue(timer.cancelled)

    def test_closing_failed_provider_generator_flushes_before_propagating(self):
        def fail_on_close(_value):
            try:
                yield (32000, b"audio")
            finally:
                self.pipeline.empty_cache()
                raise ValueError("close_failed")

        self.policy.original_run = fail_on_close
        generator = self.pipeline.run("hello")
        next(generator)
        with self.assertRaisesRegex(ValueError, "close_failed"):
            generator.close()
        self.assertEqual(self.pipeline.cleanups, 1)
        self.assertEqual(self.policy.active_runs, 0)

    def test_shutdown_cancels_timer_and_flushes_pending_cleanup(self):
        self.synthesize()
        timer = self.clock.timers[-1][1]
        self.policy.close()
        timer.fire()
        self.assertEqual(self.pipeline.cleanups, 1)
        self.assertTrue(timer.cancelled)

    def test_sync_use_without_event_loop_keeps_immediate_cleanup(self):
        with patch("scripts.launch_gpt_sovits_api.asyncio.get_running_loop", side_effect=RuntimeError):
            self.synthesize()
        self.assertEqual(self.pipeline.cleanups, 1)

    def test_worker_stream_does_not_take_ownership_of_loop_cleanup(self):
        managed = self.pipeline.run("ordinary")
        next(managed)
        with patch("scripts.launch_gpt_sovits_api.asyncio.get_running_loop", side_effect=RuntimeError):
            self.synthesize("worker_stream")
        self.assertEqual(self.pipeline.cleanups, 1)
        self.assertEqual(self.policy.active_runs, 1)
        managed.close()
        self.clock.timers[-1][1].fire()
        self.assertEqual(self.pipeline.cleanups, 2)

    def test_multiple_open_generators_do_not_clean_while_one_is_active(self):
        first = self.pipeline.run("one")
        second = self.pipeline.run("two")
        next(first)
        next(second)
        first.close()
        self.assertEqual(self.clock.timers, [])
        second.close()
        self.assertEqual(self.pipeline.cleanups, 0)
        self.clock.timers[-1][1].fire()
        self.assertEqual(self.pipeline.cleanups, 1)


if __name__ == "__main__":
    unittest.main()
