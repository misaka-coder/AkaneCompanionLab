from __future__ import annotations

import os
import types
import unittest
import warnings
from unittest.mock import patch

from companion_v01.embedding_provider import BaseEmbeddingProvider, CachedEmbeddingProvider, HashedEmbeddingProvider
from companion_v01.huggingface_provider import HuggingFaceEmbeddingProvider
from companion_v01.text_utils import hashed_embedding


class CountingEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "counting"
    version = "v1"

    def __init__(self) -> None:
        super().__init__(dimension=2)
        self.embed_text_calls = 0
        self.embed_texts_calls = 0

    def embed_text(self, text: str) -> list[float]:
        self.embed_text_calls += 1
        size = float(len(str(text or "")))
        return [size, size + 1.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embed_texts_calls += 1
        return [self.embed_text(text) for text in texts]


class EmbeddingProviderTests(unittest.TestCase):
    def test_hashed_provider_matches_legacy_helper(self) -> None:
        provider = HashedEmbeddingProvider()
        text = "苹果手机和iPhone"
        self.assertEqual(provider.embed_text(text), hashed_embedding(text))

    def test_embed_texts_batches_single_text_embeddings(self) -> None:
        provider = HashedEmbeddingProvider()
        texts = ["欢迎回来", "去上课"]
        self.assertEqual(
            provider.embed_texts(texts),
            [provider.embed_text(text) for text in texts],
        )

    def test_cached_provider_reuses_single_text_embedding(self) -> None:
        inner = CountingEmbeddingProvider()
        provider = CachedEmbeddingProvider(inner, max_entries=8)

        first = provider.embed_text("欢迎回来")
        second = provider.embed_text("欢迎回来")

        self.assertEqual(first, second)
        self.assertEqual(inner.embed_text_calls, 1)

    def test_cached_provider_deduplicates_missing_batch_texts(self) -> None:
        inner = CountingEmbeddingProvider()
        provider = CachedEmbeddingProvider(inner, max_entries=8)

        vectors = provider.embed_texts(["欢迎回来", "去上课", "欢迎回来"])
        vectors_again = provider.embed_texts(["去上课", "欢迎回来"])

        self.assertEqual(vectors[0], vectors[2])
        self.assertEqual(vectors_again[0], vectors[1])
        self.assertEqual(inner.embed_texts_calls, 1)
        self.assertEqual(inner.embed_text_calls, 2)

    def test_cached_provider_preserves_collection_identity_of_inner_provider(self) -> None:
        provider = CachedEmbeddingProvider(HashedEmbeddingProvider(), max_entries=16)
        self.assertEqual(provider.collection_key(), HashedEmbeddingProvider().collection_key())
        self.assertEqual(provider.legacy_collection_name, "akane_memory_v01")

    def test_huggingface_provider_suppresses_known_pynvml_future_warning(self) -> None:
        class StubSentenceTransformer:
            def __init__(self, model_name: str, device=None) -> None:
                warnings.warn(
                    "The pynvml package is deprecated. Please install nvidia-ml-py instead.",
                    FutureWarning,
                    stacklevel=1,
                )
                self.model_name = model_name
                self.device = device

            def get_sentence_embedding_dimension(self) -> int:
                return 3

            def encode(self, texts, **kwargs):
                return [[1.0, 0.0, 0.0] for _ in texts]

        fake_module = types.SimpleNamespace(SentenceTransformer=StubSentenceTransformer)
        with warnings.catch_warnings(record=True) as captured, patch.dict(
            "sys.modules",
            {"sentence_transformers": fake_module},
        ):
            warnings.simplefilter("always")
            provider = HuggingFaceEmbeddingProvider(model_name="stub/model")

        self.assertEqual(provider.dimension, 3)
        self.assertFalse(
            any("pynvml package is deprecated" in str(item.message) for item in captured)
        )

    def test_huggingface_provider_can_load_from_local_cache_only(self) -> None:
        captured_kwargs: dict[str, object] = {}

        class StubSentenceTransformer:
            def __init__(
                self,
                model_name: str,
                device=None,
                local_files_only: bool = False,
                cache_folder: str | None = None,
            ) -> None:
                captured_kwargs.update(
                    {
                        "model_name": model_name,
                        "device": device,
                        "local_files_only": local_files_only,
                        "cache_folder": cache_folder,
                    }
                )

            def get_sentence_embedding_dimension(self) -> int:
                return 3

            def encode(self, texts, **kwargs):
                return [[1.0, 0.0, 0.0] for _ in texts]

        fake_module = types.SimpleNamespace(SentenceTransformer=StubSentenceTransformer)
        with patch.dict("sys.modules", {"sentence_transformers": fake_module}), patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            provider = HuggingFaceEmbeddingProvider(
                model_name="BAAI/bge-m3",
                device="cpu",
                local_files_only=True,
                cache_folder="models/cache",
            )

        self.assertEqual(provider.dimension, 3)
        self.assertEqual(
            captured_kwargs,
            {
                "model_name": "BAAI/bge-m3",
                "device": "cpu",
                "local_files_only": True,
                "cache_folder": "models/cache",
            },
        )

    def test_huggingface_provider_sets_endpoint_only_during_model_load(self) -> None:
        captured_env: dict[str, str | None] = {}

        class StubSentenceTransformer:
            def __init__(self, model_name: str, device=None, local_files_only: bool = False) -> None:
                captured_env["HF_ENDPOINT"] = os.environ.get("HF_ENDPOINT")
                captured_env["HF_HUB_OFFLINE"] = os.environ.get("HF_HUB_OFFLINE")

            def get_sentence_embedding_dimension(self) -> int:
                return 3

            def encode(self, texts, **kwargs):
                return [[1.0, 0.0, 0.0] for _ in texts]

        fake_module = types.SimpleNamespace(SentenceTransformer=StubSentenceTransformer)
        with patch.dict("sys.modules", {"sentence_transformers": fake_module}), patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            provider = HuggingFaceEmbeddingProvider(
                model_name="BAAI/bge-m3",
                local_files_only=False,
                hf_endpoint="https://hf-mirror.com/",
            )
            self.assertIsNone(os.environ.get("HF_ENDPOINT"))

        self.assertEqual(provider.dimension, 3)
        self.assertEqual(captured_env["HF_ENDPOINT"], "https://hf-mirror.com")
        self.assertIsNone(captured_env["HF_HUB_OFFLINE"])


if __name__ == "__main__":
    unittest.main()
