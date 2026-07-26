from __future__ import annotations

import unittest
from unittest.mock import patch

import config
from memcore import HTTPEmbeddingProvider

from companion_v01.embedding_provider import BaseEmbeddingProvider
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.memcore_integration.adapters import build_akane_embedding_provider
from companion_v01.remote_embedding_provider import RemoteEmbeddingProvider


def _vector(index: int) -> list[float]:
    vector = [0.0] * 32
    vector[index] = 1.0
    return vector


class _FakeHTTPEmbeddingProvider:
    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)
        self.version = "transport-v1"
        self.calls: list[tuple[str, list[str]]] = []

    def embed_text(self, text: str) -> list[float]:
        self.calls.append(("text", [text]))
        return _vector(0 if "饮料" in text or "可乐" in text else 31)

    def embed_texts(self, texts) -> list[list[float]]:
        items = list(texts)
        self.calls.append(("texts", items))
        return [_vector(0 if "饮料" in text or "可乐" in text else 31) for text in items]

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(("query", [text]))
        return _vector(0 if "饮料" in text or "可乐" in text else 31)

    def embed_queries(self, texts) -> list[list[float]]:
        items = list(texts)
        self.calls.append(("queries", items))
        return [_vector(0 if "饮料" in text or "可乐" in text else 31) for text in items]

    def embed_document(self, text: str) -> list[float]:
        self.calls.append(("document", [text]))
        return _vector(0 if "饮料" in text or "可乐" in text else 31)

    def embed_documents(self, texts) -> list[list[float]]:
        items = list(texts)
        self.calls.append(("documents", items))
        return [_vector(0 if "饮料" in text or "可乐" in text else 31) for text in items]


class RemoteEmbeddingProviderTests(unittest.TestCase):
    def _provider(self) -> tuple[RemoteEmbeddingProvider, _FakeHTTPEmbeddingProvider]:
        with patch("memcore.HTTPEmbeddingProvider", _FakeHTTPEmbeddingProvider):
            provider = RemoteEmbeddingProvider(
                api_key="private-key",
                base_url="https://embedding.example/v1",
                model_name="example/model",
                dimension=32,
            )
        return provider, provider._inner

    def test_symmetric_remote_provider_batches_both_roles(self) -> None:
        provider, inner = self._provider()

        self.assertEqual(provider.embed_documents(["历史一", "历史二"]), [_vector(31)] * 2)
        self.assertEqual(provider.embed_query("饮料问题"), _vector(0))
        self.assertEqual(
            inner.calls,
            [
                ("documents", ["历史一", "历史二"]),
                ("query", ["饮料问题"]),
            ],
        )
        self.assertEqual(inner.kwargs["model"], "example/model")
        self.assertEqual(inner.kwargs["dimension"], 32)
        self.assertNotIn("private-key", provider.collection_key())

    def test_remote_configuration_failures_are_structured(self) -> None:
        with self.assertRaisesRegex(ValueError, "^remote_embedding_api_key_required$"):
            RemoteEmbeddingProvider(
                api_key="",
                base_url="https://embedding.example/v1",
                model_name="example/model",
                dimension=32,
            )
        with self.assertRaisesRegex(ValueError, "^remote_embedding_base_url_required$"):
            RemoteEmbeddingProvider(
                api_key="private-key",
                base_url="",
                model_name="example/model",
                dimension=32,
            )

    def test_probe_uses_memcore_semantic_verifier(self) -> None:
        provider, inner = self._provider()

        report = provider.verify_retrieval_space()

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(
            inner.calls,
            [
                ("documents", ["用户喜欢喝冰可乐。"]),
                ("queries", ["用户喜欢什么饮料？", "今天的股票行情怎么样？"]),
            ],
        )

    def test_real_akane_to_memcore_adapter_keeps_symmetric_batches(self) -> None:
        payloads: list[dict[str, object]] = []

        def request_api(_inner, payload) -> list[list[float]]:
            frozen = dict(payload)
            payloads.append(frozen)
            return [
                _vector(0 if "饮料" in str(text) or "可乐" in str(text) else 31)
                for text in frozen["input"]
            ]

        with patch.object(
            HTTPEmbeddingProvider,
            "_request_api",
            autospec=True,
            side_effect=request_api,
        ):
            provider = RemoteEmbeddingProvider(
                api_key="private-key",
                base_url="https://embedding.example/v1",
                model_name="example/model",
                dimension=32,
            )
            memcore_provider = build_akane_embedding_provider(provider)
            documents = memcore_provider.embed_documents(["用户喜欢喝可乐。", "用户关注股票。"])
            query = memcore_provider.embed_query("用户喜欢什么饮料？")

        self.assertEqual(documents, [_vector(0), _vector(31)])
        self.assertEqual(query, _vector(0))
        self.assertEqual(payloads[0]["input"], ["用户喜欢喝可乐。", "用户关注股票。"])
        self.assertEqual(payloads[1]["input"], ["用户喜欢什么饮料？"])
        self.assertEqual(payloads[0]["model"], "example/model")
        self.assertNotIn("task", payloads[0])
        self.assertNotIn("task", payloads[1])

    def test_engine_selects_remote_without_hashed_fallback(self) -> None:
        class StubRemoteProvider(BaseEmbeddingProvider):
            provider_name = "remote"
            version = "v-test"

            def __init__(self, **kwargs) -> None:
                super().__init__(dimension=int(kwargs["dimension"]))
                self.kwargs = dict(kwargs)

            def embed_text(self, text: str) -> list[float]:
                return [1.0] * self.dimension

            def verify_retrieval_space(self):
                return {
                    "ok": True,
                    "status": "ready",
                    "provider": "remote",
                    "model": self.kwargs["model_name"],
                    "dimension": self.dimension,
                    "reason": "",
                }

        engine = object.__new__(AkaneMemoryEngine)
        with (
            patch.object(config, "EMBEDDING_PROVIDER", "remote"),
            patch.object(config, "EMBEDDING_CACHE_SIZE", 0),
            patch.object(config, "EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
            patch.object(config, "EMBEDDING_API_KEY", "private-key"),
            patch.object(config, "EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1"),
            patch.object(config, "EMBEDDING_DIMENSION", 1024),
            patch.object(config, "EMBEDDING_TIMEOUT_SECONDS", 20.0),
            patch("companion_v01.engine.RemoteEmbeddingProvider", StubRemoteProvider),
        ):
            provider = engine._build_embedding_provider()

        self.assertIsInstance(provider, StubRemoteProvider)
        self.assertEqual(provider.kwargs["model_name"], "BAAI/bge-m3")
        self.assertEqual(engine._embedding_startup_status["status"], "ready")


if __name__ == "__main__":
    unittest.main()
