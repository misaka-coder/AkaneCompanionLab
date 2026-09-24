from __future__ import annotations

from types import SimpleNamespace
import threading
import unittest

import torch

from scripts.launch_gpt_sovits_api import install_tts_inference_mode


class TTSInferenceModeTests(unittest.TestCase):
    def test_context_is_active_inside_generator_and_restored_at_each_yield(self):
        observed = []

        @torch.no_grad()
        def run(value):
            for _ in range(2):
                observed.append(torch.is_inference_mode_enabled())
                yield torch.ones(2) * value

        pipeline = SimpleNamespace(run=run)
        self.assertTrue(install_tts_inference_mode(pipeline, torch))
        generator = pipeline.run(3)
        for _ in range(2):
            result = next(generator)
            self.assertEqual(result.tolist(), [3.0, 3.0])
            self.assertTrue(torch.is_inference(result))
            self.assertFalse(torch.is_inference_mode_enabled())
        generator.close()
        self.assertEqual(observed, [True, True])
        self.assertFalse(torch.is_inference_mode_enabled())

    def test_close_failure_propagates_without_leaking_context(self):
        observed = []

        def run(_value):
            try:
                yield b"audio"
            finally:
                observed.append(torch.is_inference_mode_enabled())
                raise ValueError("provider_close_failed")

        pipeline = SimpleNamespace(run=run)
        install_tts_inference_mode(pipeline, torch)
        generator = pipeline.run("hello")
        self.assertEqual(next(generator), b"audio")
        with self.assertRaisesRegex(ValueError, "provider_close_failed"):
            generator.close()
        self.assertEqual(observed, [True])
        self.assertFalse(torch.is_inference_mode_enabled())

    def test_worker_iteration_keeps_mode_thread_local(self):
        observed = []

        def run(_value):
            observed.append(torch.is_inference_mode_enabled())
            yield b"audio"

        pipeline = SimpleNamespace(run=run)
        install_tts_inference_mode(pipeline, torch)

        def worker():
            observed.append(torch.is_inference_mode_enabled())
            generator = pipeline.run("hello")
            observed.append(next(generator) == b"audio")
            observed.append(torch.is_inference_mode_enabled())
            generator.close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed, [False, True, True, False])
        self.assertFalse(torch.is_inference_mode_enabled())

    def test_older_runtime_retains_original_callable(self):
        def run(_value):
            yield b"original"

        pipeline = SimpleNamespace(run=run)
        self.assertFalse(install_tts_inference_mode(pipeline, SimpleNamespace()))
        self.assertIs(pipeline.run, run)


if __name__ == "__main__":
    unittest.main()
