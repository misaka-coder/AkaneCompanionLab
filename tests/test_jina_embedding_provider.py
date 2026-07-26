from __future__ import annotations

import unittest
from unittest.mock import patch

from memcore import RoleAwareHTTPEmbeddingProvider

from companion_v01.memcore_integration.adapters import build_akane_embedding_provider
from companion_v01.jina_embedding_provider import JinaEmbeddingProvider


class _FakeRoleAwareHTTPEmbeddingProvider:
    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)
        self.version = "transport-v1"
        self.calls: list[tuple[str, list[str]]] = []

    def embed_document(self, text: str) -> list[float]:
        self.calls.append(("document", [text]))
        return _vector(0)

    def embed_documents(self, texts) -> list[list[float]]:
        items = list(texts)
        self.calls.append(("documents", items))
        return [_vector(0) for _ in items]

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(("query", [text]))
        return _vector(0 if "饮料" in text else 31)

    def embed_queries(self, texts) -> list[list[float]]:
        items = list(texts)
        self.calls.append(("queries", items))
        return [_vector(0 if "饮料" in text else 31) for text in items]


def _vector(index: int) -> list[float]:
    vector = [0.0] * 32
    vector[index] = 1.0
    return vector


class JinaEmbeddingProviderTests(unittest.TestCase):
    def _provider(self) -> tuple[JinaEmbeddingProvider, _FakeRoleAwareHTTPEmbeddingProvider]:
        with patch("memcore.RoleAwareHTTPEmbeddingProvider", _FakeRoleAwareHTTPEmbeddingProvider):
            provider = JinaEmbeddingProvider(
                api_key="private-key",
                model_name="jina-embeddings-v3",
                dimension=32,
            )
        return provider, provider._inner

    def test_maps_neutral_roles_to_jina_tasks_and_batches(self) -> None:
        provider, inner = self._provider()

        self.assertEqual(provider.embed_documents(["历史一", "历史二"]), [_vector(0)] * 2)
        self.assertEqual(provider.embed_query("饮料问题"), _vector(0))
        self.assertEqual(inner.calls, [("documents", ["历史一", "历史二"]), ("query", ["饮料问题"])])
        self.assertEqual(inner.kwargs["query_body"], {"task": "retrieval.query"})
        self.assertEqual(inner.kwargs["document_body"], {"task": "retrieval.passage"})
        self.assertEqual(inner.kwargs["common_body"]["dimensions"], 32)
        self.assertNotIn("private-key", provider.collection_key())

    def test_missing_key_is_a_structured_configuration_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "^jina_api_key_required$"):
            JinaEmbeddingProvider(api_key="")

    def test_invalid_dimension_is_rejected_before_network(self) -> None:
        with self.assertRaisesRegex(ValueError, "^jina_dimension_invalid$"):
            JinaEmbeddingProvider(api_key="private-key", dimension=333)

    def test_old_memcore_transport_is_a_structured_error(self) -> None:
        with patch("builtins.__import__", side_effect=ImportError("missing transport")):
            with self.assertRaisesRegex(RuntimeError, "^jina_memcore_transport_unavailable$"):
                JinaEmbeddingProvider(api_key="private-key")

    def test_retrieval_space_probe_uses_one_document_and_one_query_batch(self) -> None:
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

    def test_probe_returns_structured_unavailable_without_key_or_body_leak(self) -> None:
        provider, inner = self._provider()

        def fail(_texts):
            raise RuntimeError("embedding_http_error:401")

        inner.embed_documents = fail
        report = provider.verify_retrieval_space()

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "embedding_http_error:401")
        self.assertNotIn("private-key", str(report))

    def test_real_akane_to_memcore_adapter_preserves_jina_roles(self) -> None:
        payloads: list[dict[str, object]] = []

        def request_api(_inner, payload) -> list[list[float]]:
            frozen = dict(payload)
            payloads.append(frozen)
            task = str(frozen.get("task") or "")
            vectors: list[list[float]] = []
            for text in frozen["input"]:
                if task == "retrieval.passage":
                    vectors.append(_vector(0 if "可乐" in str(text) else 31))
                else:
                    vectors.append(_vector(0 if "饮料" in str(text) else 31))
            return vectors

        with patch.object(
            RoleAwareHTTPEmbeddingProvider,
            "_request_api",
            autospec=True,
            side_effect=request_api,
        ):
            provider = JinaEmbeddingProvider(
                api_key="private-key",
                model_name="jina-embeddings-v3",
                dimension=32,
            )
            memcore_provider = build_akane_embedding_provider(provider)
            documents = memcore_provider.embed_documents(["用户喜欢喝可乐。", "用户关注股票。"])
            query = memcore_provider.embed_query("用户喜欢什么饮料？")

        self.assertEqual(documents, [_vector(0), _vector(31)])
        self.assertEqual(query, _vector(0))
        self.assertEqual(
            [payload["task"] for payload in payloads],
            ["retrieval.passage", "retrieval.query"],
        )
        self.assertEqual(payloads[0]["input"], ["用户喜欢喝可乐。", "用户关注股票。"])
        self.assertEqual(payloads[1]["input"], ["用户喜欢什么饮料？"])


if __name__ == "__main__":
    unittest.main()
