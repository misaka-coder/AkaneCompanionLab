from __future__ import annotations

import threading
import unittest
from collections import Counter

from companion_v01.embedding_provider import BaseEmbeddingProvider, HashedEmbeddingProvider
from companion_v01.vector_store import VectorStore, fuse_with_rrf


class DummyEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "dummy provider"
    version = "v9"

    def __init__(self) -> None:
        super().__init__(dimension=3)

    def embed_text(self, text: str) -> list[float]:
        text_length = float(len(str(text or "")))
        return [text_length, text_length + 1.0, text_length + 2.0]


class CountingBatchEmbeddingProvider(DummyEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.embed_texts_calls = 0

    def embed_texts(self, texts) -> list[list[float]]:
        self.embed_texts_calls += 1
        return [DummyEmbeddingProvider.embed_text(self, text) for text in texts]


class FakeCollection:
    def __init__(self) -> None:
        self.last_upsert: dict[str, object] | None = None
        self.last_query: dict[str, object] | None = None

    def upsert(self, **kwargs) -> None:
        self.last_upsert = kwargs

    def query(self, **kwargs) -> dict[str, object]:
        self.last_query = kwargs
        return {
            "ids": [["memory-1"]],
            "documents": [["欢迎回来"]],
            "metadatas": [[{"entry_type": "raw"}]],
            "distances": [[0.2]],
        }


class VectorStoreLogicTests(unittest.TestCase):
    def test_fuse_with_rrf_merges_semantic_and_keyword_hits(self) -> None:
        semantic_hits = [
            {
                "source_id": "a",
                "document": "欢迎回来",
                "metadata": {"entry_type": "raw"},
                "semantic_score": 0.9,
            },
            {
                "source_id": "b",
                "document": "去上课",
                "metadata": {"entry_type": "raw"},
                "semantic_score": 0.8,
            },
        ]
        keyword_hits = [
            {
                "source_id": "b",
                "document": "去上课",
                "metadata": {"entry_type": "raw"},
                "tag_score": 1.5,
            },
            {
                "source_id": "c",
                "document": "糖葫芦摊",
                "metadata": {"entry_type": "summary"},
                "tag_score": 1.2,
            },
        ]

        fused = fuse_with_rrf(semantic_hits, keyword_hits)
        self.assertEqual([item["source_id"] for item in fused], ["b", "a", "c"])
        self.assertTrue(fused[0]["dual_hit"])

    def test_bm25_score_rewards_query_term_overlap(self) -> None:
        store = VectorStore.__new__(VectorStore)
        hit_score = store._bm25_score(
            query_terms=["上课", "回来"],
            doc_terms=["上课", "回来", "欢迎"],
            doc_len=3,
            avgdl=3.0,
            doc_count=2,
            term_doc_freq=Counter({"上课": 1, "回来": 1, "欢迎": 1}),
        )
        miss_score = store._bm25_score(
            query_terms=["上课", "回来"],
            doc_terms=["糖葫芦", "集市"],
            doc_len=2,
            avgdl=2.5,
            doc_count=2,
            term_doc_freq=Counter({"糖葫芦": 1, "集市": 1}),
        )
        self.assertGreater(hit_score, 0.0)
        self.assertEqual(miss_score, 0.0)

    def test_keyword_doc_text_includes_semantic_tags(self) -> None:
        store = VectorStore.__new__(VectorStore)
        combined = store._keyword_doc_text("欢迎回来", {"semantic_tags_text": "上课,回来"})
        self.assertIn("欢迎回来", combined)
        self.assertIn("上课", combined)

    def test_keyword_doc_text_includes_memory_metadata_tags(self) -> None:
        store = VectorStore.__new__(VectorStore)
        combined = store._keyword_doc_text(
            "普通文本",
            {
                "memory_keywords_text": "可乐,饮料",
                "memory_categories_text": "preference",
                "memory_subject_scopes_text": "user",
                "memory_mood_tags_text": "warm,playful",
            },
        )

        self.assertIn("可乐", combined)
        self.assertIn("preference", combined)
        self.assertIn("user", combined)
        self.assertIn("playful", combined)

    def test_build_collection_name_keeps_legacy_name_for_default_hashed_provider(self) -> None:
        self.assertEqual(
            VectorStore._build_collection_name(HashedEmbeddingProvider()),
            "akane_memory_v01",
        )

    def test_build_collection_name_versions_non_legacy_provider(self) -> None:
        self.assertEqual(
            VectorStore._build_collection_name(DummyEmbeddingProvider()),
            "akane_memory_dummy_provider_v9_3",
        )

    def test_upsert_entry_uses_injected_embedding_provider(self) -> None:
        store = VectorStore.__new__(VectorStore)
        store._lock = threading.RLock()
        store.embedding_provider = DummyEmbeddingProvider()
        store.collection = FakeCollection()

        store.upsert_entry(
            source_id="memory-1",
            text="欢迎回来",
            metadata={"profile_user_id": "user-1"},
        )

        self.assertIsNotNone(store.collection.last_upsert)
        self.assertEqual(
            store.collection.last_upsert["embeddings"],
            [[4.0, 5.0, 6.0]],
        )

    def test_semantic_search_uses_injected_embedding_provider(self) -> None:
        store = VectorStore.__new__(VectorStore)
        store._lock = threading.RLock()
        store.embedding_provider = DummyEmbeddingProvider()
        store.collection = FakeCollection()

        hits = store.semantic_search(
            profile_user_id="user-1",
            query_text="你好呀",
            time_hint=None,
            n_results=4,
        )

        self.assertEqual(store.collection.last_query["query_embeddings"], [[3.0, 4.0, 5.0]])
        self.assertEqual(hits[0]["source_id"], "memory-1")

    def test_semantic_search_can_scope_to_character_pack_id(self) -> None:
        store = VectorStore.__new__(VectorStore)
        store._lock = threading.RLock()
        store.embedding_provider = DummyEmbeddingProvider()
        store.collection = FakeCollection()

        store.semantic_search(
            profile_user_id="master",
            character_pack_id="kaju",
            query_text="便当",
            time_hint=None,
            n_results=4,
        )

        self.assertEqual(
            store.collection.last_query["where"],
            {"$and": [{"profile_user_id": "master"}, {"character_pack_id": "kaju"}]},
        )

    def test_semantic_search_can_scope_to_builtin_empty_character_pack_id(self) -> None:
        store = VectorStore.__new__(VectorStore)
        store._lock = threading.RLock()
        store.embedding_provider = DummyEmbeddingProvider()
        store.collection = FakeCollection()

        store.semantic_search(
            profile_user_id="master",
            character_pack_id="",
            query_text="旧默认记忆",
            time_hint=None,
            n_results=4,
        )

        self.assertEqual(
            store.collection.last_query["where"],
            {"$and": [{"profile_user_id": "master"}, {"character_pack_id": ""}]},
        )

    def test_upsert_entries_uses_batch_embedding_interface(self) -> None:
        store = VectorStore.__new__(VectorStore)
        store._lock = threading.RLock()
        store.embedding_provider = CountingBatchEmbeddingProvider()
        store.collection = FakeCollection()

        store.upsert_entries(
            [
                {"source_id": "memory-1", "text": "欢迎回来", "metadata": {"profile_user_id": "user-1"}},
                {"source_id": "memory-2", "text": "去上课", "metadata": {"profile_user_id": "user-1"}},
            ]
        )

        self.assertEqual(store.embedding_provider.embed_texts_calls, 1)
        self.assertEqual(store.collection.last_upsert["ids"], ["memory-1", "memory-2"])


if __name__ == "__main__":
    unittest.main()
