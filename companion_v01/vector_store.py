from __future__ import annotations

import math
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import chromadb

from .embedding_provider import BaseEmbeddingProvider, HashedEmbeddingProvider
from .text_utils import tokenize


class VectorStore:
    def __init__(
        self,
        base_dir: Path,
        *,
        embedding_provider: BaseEmbeddingProvider | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.embedding_provider = embedding_provider or HashedEmbeddingProvider()
        self.collection_name = self._build_collection_name(self.embedding_provider)
        self.client = chromadb.PersistentClient(path=str(self.base_dir))
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata=self._build_collection_metadata(),
        )

    def reset(self) -> None:
        with self._lock:
            try:
                self.client.delete_collection(self.collection_name)
            except Exception:
                pass
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata=self._build_collection_metadata(),
            )

    def count_entries(self) -> int:
        with self._lock:
            try:
                return int(self.collection.count())
            except Exception:
                return 0

    def upsert_entry(self, *, source_id: str, text: str, metadata: dict[str, Any]) -> None:
        self.upsert_entries(
            [
                {
                    "source_id": source_id,
                    "text": text,
                    "metadata": metadata,
                }
            ]
        )

    def upsert_entries(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for entry in entries:
            source_id = str(entry.get("source_id") or "").strip()
            if not source_id:
                continue
            text = str(entry.get("text") or "")
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            clean_meta = {
                key: value
                for key, value in metadata.items()
                if isinstance(value, (str, int, float, bool))
            }
            ids.append(source_id)
            texts.append(text)
            metadatas.append(clean_meta)
        if not ids:
            return
        embeddings = self.embedding_provider.embed_texts(texts)
        with self._lock:
            self.collection.upsert(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )

    def semantic_search(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        query_text: str,
        time_hint: dict[str, Any] | None,
        n_results: int = 8,
    ) -> list[dict[str, Any]]:
        where = self._build_where(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            time_hint=time_hint,
        )
        with self._lock:
            result = self.collection.query(
                query_embeddings=self.embedding_provider.embed_texts([str(query_text or "")]),
                n_results=max(1, int(n_results)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        hits: list[dict[str, Any]] = []
        for idx, source_id in enumerate(result.get("ids", [[]])[0]):
            metadata = (result.get("metadatas", [[]])[0] or [{}])[idx] or {}
            document = (result.get("documents", [[]])[0] or [""])[idx] or ""
            distance = float((result.get("distances", [[]])[0] or [1.0])[idx] or 1.0)
            hits.append(
                {
                    "source_id": source_id,
                    "document": document,
                    "metadata": metadata,
                    "semantic_score": max(0.0, 1.0 - distance),
                }
            )
        return hits

    def keyword_search(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        query_text: str,
        keywords: list[str],
        time_hint: dict[str, Any] | None,
        n_results: int = 8,
    ) -> list[dict[str, Any]]:
        where = self._build_where(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            time_hint=time_hint,
        )
        with self._lock:
            result = self.collection.get(
                where=where,
                include=["documents", "metadatas"],
            )
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        if not ids:
            return []

        query_terms = [term for term in keywords if str(term).strip()]
        if not query_terms:
            query_terms = tokenize(query_text)
        if not query_terms:
            return []

        doc_terms: dict[str, list[str]] = {}
        doc_lengths: dict[str, int] = {}
        term_doc_freq: Counter[str] = Counter()

        for source_id, document, metadata in zip(ids, documents, metadatas):
            combined = self._keyword_doc_text(str(document or ""), metadata or {})
            terms = tokenize(combined)
            doc_terms[source_id] = terms
            doc_lengths[source_id] = len(terms)
            for term in set(terms):
                term_doc_freq[term] += 1

        avgdl = sum(doc_lengths.values()) / max(1, len(doc_lengths))
        hits: list[dict[str, Any]] = []
        for source_id, document, metadata in zip(ids, documents, metadatas):
            score = self._bm25_score(
                query_terms=query_terms,
                doc_terms=doc_terms[source_id],
                doc_len=doc_lengths[source_id],
                avgdl=avgdl,
                doc_count=len(ids),
                term_doc_freq=term_doc_freq,
            )
            if score <= 0:
                continue
            hits.append(
                {
                    "source_id": source_id,
                    "document": document or "",
                    "metadata": metadata or {},
                    "tag_score": float(score),
                }
            )

        hits.sort(key=lambda item: item["tag_score"], reverse=True)
        return hits[: max(1, int(n_results))]

    def _keyword_doc_text(self, document: str, metadata: dict[str, Any]) -> str:
        tag_text = str(metadata.get("semantic_tags_text", "") or "")
        memory_keywords = str(metadata.get("memory_keywords_text", "") or "")
        memory_categories = str(metadata.get("memory_categories_text", "") or "")
        memory_subjects = str(metadata.get("memory_subject_scopes_text", "") or "")
        memory_moods = str(metadata.get("memory_mood_tags_text", "") or "")
        return f"{document} {tag_text} {memory_keywords} {memory_categories} {memory_subjects} {memory_moods}".strip()

    def _bm25_score(
        self,
        *,
        query_terms: list[str],
        doc_terms: list[str],
        doc_len: int,
        avgdl: float,
        doc_count: int,
        term_doc_freq: Counter[str],
    ) -> float:
        if not doc_terms:
            return 0.0
        tf = Counter(doc_terms)
        k1 = 1.5
        b = 0.75
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if freq <= 0:
                continue
            df = max(1, term_doc_freq.get(term, 0))
            idf = math.log(1 + ((doc_count - df + 0.5) / (df + 0.5)))
            numerator = freq * (k1 + 1)
            denominator = freq + k1 * (1 - b + b * (doc_len / max(1e-6, avgdl)))
            score += idf * (numerator / max(1e-6, denominator))
        return score

    def _build_where(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        time_hint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = [{"profile_user_id": str(profile_user_id)}]
        if character_pack_id is not None:
            normalized_character_pack_id = str(character_pack_id or "").strip()
            clauses.append({"character_pack_id": normalized_character_pack_id})
        hint = time_hint if isinstance(time_hint, dict) else {}
        if hint.get("date_label"):
            clauses.append({"date_label": str(hint["date_label"])})
        if hint.get("time_of_day"):
            clauses.append({"time_of_day": str(hint["time_of_day"])})
        if hint.get("start_ts") is not None:
            clauses.append({"timestamp": {"$gte": int(hint["start_ts"])}})
        if hint.get("end_ts") is not None:
            clauses.append({"timestamp": {"$lte": int(hint["end_ts"])}})
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    @staticmethod
    def _build_collection_name(embedding_provider: BaseEmbeddingProvider) -> str:
        legacy_name = embedding_provider.legacy_collection_name
        if legacy_name:
            return legacy_name
        return f"akane_memory_{embedding_provider.collection_key()}"

    def _build_collection_metadata(self) -> dict[str, Any]:
        return {
            "hnsw:space": "cosine",
            "embedding_provider": self.embedding_provider.name,
            "embedding_dimension": int(self.embedding_provider.dimension),
            "embedding_version": str(self.embedding_provider.version),
        }


def fuse_with_rrf(
    semantic_hits: list[dict[str, Any]],
    keyword_hits: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    semantic_rank = {hit["source_id"]: idx + 1 for idx, hit in enumerate(semantic_hits)}
    keyword_rank = {hit["source_id"]: idx + 1 for idx, hit in enumerate(keyword_hits)}
    semantic_map = {hit["source_id"]: hit for hit in semantic_hits}
    keyword_map = {hit["source_id"]: hit for hit in keyword_hits}

    all_ids: list[str] = []
    for bucket in (semantic_hits, keyword_hits):
        for hit in bucket:
            source_id = hit["source_id"]
            if source_id not in all_ids:
                all_ids.append(source_id)

    fused: list[dict[str, Any]] = []
    for source_id in all_ids:
        rrf_score = 0.0
        if source_id in semantic_rank:
            rrf_score += 1.0 / (k + semantic_rank[source_id])
        if source_id in keyword_rank:
            rrf_score += 1.0 / (k + keyword_rank[source_id])

        sample = semantic_map.get(source_id) or keyword_map.get(source_id) or {}
        fused.append(
            {
                "source_id": source_id,
                "dual_hit": source_id in semantic_rank and source_id in keyword_rank,
                "entry_type": str((sample.get("metadata") or {}).get("entry_type", "")),
                "rrf_score": float(rrf_score),
                "semantic_score": float((semantic_map.get(source_id) or {}).get("semantic_score", 0.0)),
                "tag_score": float((keyword_map.get(source_id) or {}).get("tag_score", 0.0)),
                "metadata": sample.get("metadata") or {},
                "document": sample.get("document", "") or "",
            }
        )

    fused.sort(
        key=lambda item: (
            -item["rrf_score"],
            -item["semantic_score"],
            -item["tag_score"],
        )
    )
    return fused
