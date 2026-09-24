from __future__ import annotations

import unittest

from companion_v01.memcore_integration.adapters import build_akane_embedding_provider


class _LegacyProvider:
    name = "legacy"
    version = "v1"
    dimension = 2

    def __init__(self) -> None:
        self.batch_calls = 0

    def embed_text(self, text: str) -> list[float]:
        return [float(len(text)), 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [self.embed_text(text) for text in texts]


class _RoleAwareProvider(_LegacyProvider):
    def __init__(self) -> None:
        super().__init__()
        self.query_calls: list[str] = []
        self.document_batches: list[list[str]] = []

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [1.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        items = list(texts)
        self.document_batches.append(items)
        return [[0.0, 1.0] for _ in items]


class MemcoreEmbeddingAdapterTests(unittest.TestCase):
    def test_role_aware_provider_methods_are_forwarded(self) -> None:
        inner = _RoleAwareProvider()
        provider = build_akane_embedding_provider(inner)

        self.assertEqual(provider.embed_query("问题"), [1.0, 0.0])
        self.assertEqual(provider.embed_documents(["记忆一", "记忆二"]), [[0.0, 1.0], [0.0, 1.0]])
        self.assertEqual(inner.query_calls, ["问题"])
        self.assertEqual(inner.document_batches, [["记忆一", "记忆二"]])

    def test_legacy_symmetric_provider_remains_compatible(self) -> None:
        inner = _LegacyProvider()
        provider = build_akane_embedding_provider(inner)

        self.assertEqual(provider.embed_query("问题"), [2.0, 0.0])
        self.assertEqual(provider.embed_documents(["记忆一", "记忆二"]), [[3.0, 0.0], [3.0, 0.0]])
        self.assertEqual(inner.batch_calls, 1)


if __name__ == "__main__":
    unittest.main()
