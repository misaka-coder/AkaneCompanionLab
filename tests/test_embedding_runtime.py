from __future__ import annotations

import contextlib
import threading
import types
import unittest
from unittest.mock import patch

from companion_v01.embedding_provider import CachedEmbeddingProvider
from companion_v01.huggingface_provider import HuggingFaceEmbeddingProvider


class CudaOOM(RuntimeError):
    pass


class FakeCuda:
    def __init__(self):
        self.cleanups = 0
        self.allocated = 1024
        self.reserved = 2048
        self.error = None

    def is_initialized(self):
        return True

    def device(self, device):
        return contextlib.nullcontext()

    def empty_cache(self):
        if self.error:
            raise self.error
        self.cleanups += 1
        self.reserved = self.allocated

    def memory_allocated(self, device):
        return self.allocated

    def memory_reserved(self, device):
        return self.reserved

    def max_memory_allocated(self, device):
        return 1536

    def max_memory_reserved(self, device):
        return 3072


class FakeTimer:
    timers = []

    def __init__(self, seconds, callback, args):
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.timers.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # A cancelled timer can already have entered its callback.
        self.callback(*self.args)


class FakeModel:
    kwargs = {}

    def __init__(self, model_name, **kwargs):
        self.device = kwargs.get("device") or "cpu"
        self.dtype = kwargs.get("model_kwargs", {}).get("torch_dtype", "float32")
        self.init_kwargs = kwargs
        self.encodes = []
        self.encode_hook = None

    def parameters(self):
        size = 4 if self.dtype == "float32" else 2
        yield types.SimpleNamespace(dtype=self.dtype, numel=lambda: 16, element_size=lambda: size)

    def get_sentence_embedding_dimension(self):
        return 2

    def encode(self, texts, **kwargs):
        self.encodes.append((list(texts), kwargs))
        if self.encode_hook:
            return self.encode_hook(texts, kwargs)
        return [[float(len(text)), 1.0] for text in texts]


class EmbeddingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.cuda = FakeCuda()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        FakeTimer.timers = []
        self.stack.enter_context(patch.dict("sys.modules", {
            "sentence_transformers": types.SimpleNamespace(SentenceTransformer=FakeModel),
            "torch": types.SimpleNamespace(cuda=self.cuda, OutOfMemoryError=CudaOOM),
        }))
        self.stack.enter_context(patch("companion_v01.embedding_runtime.threading.Timer", FakeTimer))

    def provider(self, **kwargs):
        provider = HuggingFaceEmbeddingProvider(model_name="fixture", **kwargs)
        self.addCleanup(provider.close)
        return provider

    def test_half_precision_is_passed_at_load_and_identity_is_preserved(self):
        provider = self.provider(device="cuda:0", precision="float16")
        self.assertEqual(provider._model.init_kwargs["model_kwargs"], {"torch_dtype": "float16"})
        self.assertEqual(provider.version, "fixture")
        self.assertEqual(provider.dimension, 2)
        state = provider.snapshot_runtime()
        self.assertEqual(state["model_weight_bytes"], 32)
        self.assertEqual(state["precision"], "float16")

    def test_cpu_keeps_full_precision_and_does_not_create_cuda_timers(self):
        provider = self.provider(device="cpu", precision="float16")
        self.assertNotIn("model_kwargs", provider._model.init_kwargs)
        self.assertEqual(provider.snapshot_runtime()["precision"], "float32")
        self.assertFalse(provider.snapshot_runtime()["cuda_metrics_available"])
        self.assertEqual(FakeTimer.timers, [])

    def test_batch_limit_does_not_truncate_or_drop_input(self):
        provider = self.provider(batch_size=2)
        texts = ["a" * 9000, "b", "cc", "ddd", "eeee"]
        self.assertEqual(provider.embed_documents(texts), [[9000.0, 1.0], [1.0, 1.0], [2.0, 1.0], [3.0, 1.0], [4.0, 1.0]])
        self.assertEqual(provider._model.encodes[0][0], texts)
        self.assertEqual(provider._model.encodes[0][1]["batch_size"], 2)

    def test_empty_input_does_not_run_model(self):
        provider = self.provider()
        self.assertEqual(provider.embed_texts([]), [])
        self.assertEqual(provider._model.encodes, [])

    def test_invalid_resource_settings_fail_before_loading(self):
        for kwargs in ({"precision": "bad"}, {"batch_size": 0}, {"batch_size": True}, {"idle_seconds": -1}, {"idle_seconds": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.provider(**kwargs)

    def test_stale_idle_timer_cannot_clear_new_inference_cache(self):
        provider = self.provider(device="cuda:0")
        old = FakeTimer.timers[-1]
        provider.embed_text("first")
        self.assertTrue(old.cancelled)
        old.fire()
        self.assertEqual(self.cuda.cleanups, 0)
        FakeTimer.timers[-1].fire()
        self.assertEqual(self.cuda.cleanups, 1)
        self.assertIsNotNone(provider._model)
        self.assertEqual(provider.snapshot_runtime()["cuda_reserved_bytes"], 1024)

    def test_cleanup_can_be_disabled(self):
        provider = self.provider(device="cuda:0", idle_seconds=0)
        provider.embed_text("first")
        self.assertEqual(FakeTimer.timers, [])
        self.assertEqual(self.cuda.cleanups, 0)

    def test_cleanup_failure_is_observable_without_raw_exception(self):
        provider = self.provider(device="cuda:0")
        self.cuda.error = RuntimeError("private-token private-path")
        FakeTimer.timers[-1].fire()
        state = provider.snapshot_runtime()
        self.assertEqual(state["cache_cleanup_status"], "failed")
        self.assertEqual(state["last_error_type"], "RuntimeError")
        self.assertNotIn("private-", str(state))
        self.assertEqual(provider.embed_text("still usable"), [12.0, 1.0])

    def test_cuda_oom_reduces_batch_and_retries_the_same_inputs(self):
        provider = self.provider(device="cuda:0", batch_size=8)
        def encode(texts, kwargs):
            if kwargs["batch_size"] > 2:
                raise CudaOOM("fixture")
            return [[float(len(text)), 1.0] for text in texts]
        provider._model.encode_hook = encode
        self.assertEqual(provider.embed_texts(["first", "second"]), [[5.0, 1.0], [6.0, 1.0]])
        self.assertEqual([kwargs["batch_size"] for texts, kwargs in provider._model.encodes], [8, 4, 2])
        self.assertTrue(all(texts == ["first", "second"] for texts, kwargs in provider._model.encodes))
        self.assertEqual(provider.snapshot_runtime()["oom_retries"], 2)
        self.assertEqual(provider.snapshot_runtime()["effective_batch_size"], 2)
        self.assertEqual(self.cuda.cleanups, 2)

    def test_single_item_oom_is_a_real_failure(self):
        provider = self.provider(device="cuda:0", batch_size=1)
        def fail(texts, kwargs):
            raise CudaOOM("private details")
        provider._model.encode_hook = fail
        with self.assertRaisesRegex(RuntimeError, "^embedding_cuda_out_of_memory$"):
            provider.embed_text("first")
        self.assertEqual(len(provider._model.encodes), 1)
        self.assertEqual(provider.snapshot_runtime()["failed_calls"], 1)

    def test_non_cuda_failure_is_not_retried(self):
        provider = self.provider(device="cuda:0")
        def fail(texts, kwargs):
            raise ValueError("fixture")
        provider._model.encode_hook = fail
        with self.assertRaises(ValueError):
            provider.embed_text("first")
        self.assertEqual(len(provider._model.encodes), 1)
        self.assertEqual(self.cuda.cleanups, 0)

    def test_invalid_vectors_are_not_cached_as_success(self):
        provider = self.provider()
        for vectors in ([[float("nan"), 1.0]], [[1.0]], [], [[1.0, 1.0], [1.0, 1.0]]):
            with self.subTest(vectors=vectors):
                provider._model.encode_hook = lambda texts, kwargs: vectors
                with self.assertRaisesRegex(RuntimeError, "^embedding_vectors_invalid$"):
                    provider.embed_text("first")

    def test_concurrent_inference_is_serial_and_diagnostics_do_not_block(self):
        provider = self.provider(device="cuda:0")
        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        results = []
        def encode(texts, kwargs):
            if texts == ["first"]:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test_release_timeout")
            return [[float(len(text)), 1.0] for text in texts]
        provider._model.encode_hook = encode
        def second():
            second_started.set()
            results.append(provider.embed_text("second"))
        first_thread = threading.Thread(target=lambda: results.append(provider.embed_text("first")))
        second_thread = threading.Thread(target=second)
        first_thread.start()
        try:
            self.assertTrue(entered.wait(3))
            second_thread.start()
            self.assertTrue(second_started.wait(3))
            self.assertEqual(provider.snapshot_runtime()["status"], "busy")
            self.assertEqual(len(provider._model.encodes), 1)
        finally:
            release.set()
            first_thread.join(3)
            if second_thread.ident is not None:
                second_thread.join(3)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(sorted(results), [[5.0, 1.0], [6.0, 1.0]])

    def test_close_does_not_release_running_model_and_can_be_retried(self):
        provider = self.provider(device="cuda:0")
        entered = threading.Event()
        release = threading.Event()
        def encode(texts, kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test_release_timeout")
            return [[1.0, 1.0]]
        provider._model.encode_hook = encode
        thread = threading.Thread(target=lambda: provider.embed_text("first"))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertFalse(provider.close(timeout=0))
            self.assertIsNotNone(provider._model)
            self.assertEqual(provider.snapshot_runtime()["status"], "closing")
        finally:
            release.set()
            thread.join(3)
        self.assertTrue(provider.close(timeout=0))
        self.assertIsNone(provider._model)
        self.assertEqual(provider.snapshot_runtime()["status"], "closed")
        with self.assertRaisesRegex(RuntimeError, "^embedding_provider_closed$"):
            provider.embed_text("new request")

    def test_close_cancels_idle_cleanup_and_is_idempotent(self):
        provider = self.provider(device="cuda:0")
        timer = FakeTimer.timers[-1]
        self.assertTrue(provider.close())
        self.assertTrue(timer.cancelled)
        cleanups = self.cuda.cleanups
        timer.fire()
        self.assertEqual(self.cuda.cleanups, cleanups)
        self.assertTrue(provider.close())
        self.assertEqual(self.cuda.cleanups, cleanups)

    def test_cache_wrapper_forwards_runtime_metrics_and_releases_cache(self):
        provider = self.provider(device="cuda:0")
        cached = CachedEmbeddingProvider(provider, max_entries=2)
        cached.embed_text("first")
        cached.embed_text("first")
        state = cached.snapshot_runtime()
        self.assertEqual(state["cached_entries"], 1)
        self.assertEqual(state["calls"], 1)
        self.assertTrue(cached.close())
        self.assertEqual(cached.snapshot_runtime()["cached_entries"], 0)
        self.assertEqual(cached.snapshot_runtime()["status"], "closed")


class EmbeddingRuntimeWiringTests(unittest.TestCase):
    def test_factory_passes_resource_configuration_to_real_provider_slot(self):
        import config
        from companion_v01.engine_services.service_factories import ServiceFactoryMixin
        from companion_v01.embedding_provider import HashedEmbeddingProvider

        engine = ServiceFactoryMixin()
        with (
            patch.object(config, "MEMCORE_RETRIEVAL_MODE", "hybrid"),
            patch.object(config, "EMBEDDING_PROVIDER", "huggingface"),
            patch.object(config, "EMBEDDING_CACHE_SIZE", 0),
            patch.object(config, "EMBEDDING_PRECISION", "float16"),
            patch.object(config, "EMBEDDING_BATCH_SIZE", 3),
            patch.object(config, "EMBEDDING_IDLE_CACHE_SECONDS", 7.0),
            patch("companion_v01.engine_services.service_factories.HuggingFaceEmbeddingProvider",
                  return_value=HashedEmbeddingProvider()) as factory,
        ):
            engine._build_embedding_provider()
        self.assertEqual(factory.call_args.kwargs["precision"], "float16")
        self.assertEqual(factory.call_args.kwargs["batch_size"], 3)
        self.assertEqual(factory.call_args.kwargs["idle_seconds"], 7.0)

    def test_load_failure_preserves_sanitized_degraded_reason(self):
        import config
        from companion_v01.engine_services.service_factories import ServiceFactoryMixin

        engine = ServiceFactoryMixin()
        with (
            patch.object(config, "MEMCORE_RETRIEVAL_MODE", "hybrid"),
            patch.object(config, "EMBEDDING_PROVIDER", "huggingface"),
            patch("companion_v01.engine_services.service_factories.HuggingFaceEmbeddingProvider",
                  side_effect=RuntimeError("private-path private-key")),
        ):
            engine._build_embedding_provider()
        self.assertEqual(engine._embedding_startup_status["status"], "degraded")
        self.assertEqual(engine._embedding_startup_status["reason"], "local_embedding_load_failed")
        self.assertEqual(engine._embedding_startup_status["error_type"], "RuntimeError")
        self.assertNotIn("private-", str(engine._embedding_startup_status))

    def test_metrics_publish_numeric_resource_state_without_internal_details(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from companion_v01.routes.system import build_system_router

        state = {"status": "ready", "precision": "float16", "model_weight_bytes": 32,
                 "cuda_metrics_available": True, "cuda_allocated_bytes": 1024,
                 "model_path": "private-path", "last_error": "private-key"}
        engine = types.SimpleNamespace(
            vector_store=None, llm=types.SimpleNamespace(snapshot_metrics=lambda: {}),
            snapshot_embedding_reindex_status=lambda: {}, snapshot_embedding_runtime=lambda: state,
        )
        app = FastAPI()
        app.include_router(build_system_router(
            engine=engine, runtime_metrics=types.SimpleNamespace(snapshot=lambda: {}),
            public_guard=types.SimpleNamespace(snapshot=lambda: {}), log_event=lambda *args, **kwargs: None,
        ))
        with TestClient(app) as client:
            response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("akane_embedding_precision_bits 16\n", response.text)
        self.assertIn("akane_embedding_cuda_allocated_bytes 1024\n", response.text)
        self.assertNotIn("private-", response.text)
        self.assertNotIn("model_path", response.text)


if __name__ == "__main__":
    unittest.main()
