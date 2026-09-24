from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .embedding_provider import BaseEmbeddingProvider

DEFAULT_JINA_EMBEDDING_BASE_URL = "https://api.jina.ai/v1"
DEFAULT_JINA_EMBEDDING_MODEL = "jina-embeddings-v3"
DEFAULT_JINA_EMBEDDING_DIMENSION = 1024
DEFAULT_JINA_QUERY_TASK = "retrieval.query"
DEFAULT_JINA_DOCUMENT_TASK = "retrieval.passage"
_ALLOWED_DIMENSIONS = frozenset({32, 64, 128, 256, 512, 768, 1024})


class JinaEmbeddingProvider(BaseEmbeddingProvider):
    """Akane product adapter over MemCore's role-aware HTTP provider."""

    provider_name = "jina"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_JINA_EMBEDDING_BASE_URL,
        model_name: str = DEFAULT_JINA_EMBEDDING_MODEL,
        dimension: int = DEFAULT_JINA_EMBEDDING_DIMENSION,
        timeout: float = 30.0,
        query_task: str = DEFAULT_JINA_QUERY_TASK,
        document_task: str = DEFAULT_JINA_DOCUMENT_TASK,
        normalized: bool = True,
    ) -> None:
        resolved_key = str(api_key or "").strip()
        if not resolved_key:
            raise ValueError("jina_api_key_required")
        resolved_dimension = int(dimension)
        if resolved_dimension not in _ALLOWED_DIMENSIONS:
            raise ValueError("jina_dimension_invalid")
        resolved_query_task = str(query_task or "").strip()
        resolved_document_task = str(document_task or "").strip()
        if not resolved_query_task or not resolved_document_task:
            raise ValueError("jina_task_required")

        self.model_name = str(model_name or DEFAULT_JINA_EMBEDDING_MODEL).strip() or DEFAULT_JINA_EMBEDDING_MODEL
        self.query_task = resolved_query_task
        self.document_task = resolved_document_task
        self.normalized = bool(normalized)
        try:
            from memcore import RoleAwareHTTPEmbeddingProvider
        except ImportError as exc:
            raise RuntimeError("jina_memcore_transport_unavailable") from exc

        self._inner = RoleAwareHTTPEmbeddingProvider(
            base_url=str(base_url or DEFAULT_JINA_EMBEDDING_BASE_URL).strip(),
            api_key=resolved_key,
            model=self.model_name,
            dimension=resolved_dimension,
            timeout=max(1.0, float(timeout)),
            name=f"jina:{self.model_name}",
            common_body={
                "dimensions": resolved_dimension,
                "normalized": self.normalized,
                "embedding_type": "float",
            },
            query_body={"task": self.query_task},
            document_body={"task": self.document_task},
        )
        super().__init__(dimension=resolved_dimension)

    @property
    def version(self) -> str:
        identity = json.dumps(
            {
                "model": self.model_name,
                "dimension": self.dimension,
                "query_task": self.query_task,
                "document_task": self.document_task,
                "normalized": self.normalized,
                "transport": self._inner.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"v1-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"

    def embed_text(self, text: str) -> list[float]:
        return self._inner.embed_document(text)

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(text)

    def embed_queries(self, texts: Iterable[str]) -> list[list[float]]:
        return self._inner.embed_queries(texts)

    def embed_document(self, text: str) -> list[float]:
        return self._inner.embed_document(text)

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def verify_retrieval_space(self) -> dict[str, Any]:
        try:
            from memcore import verify_embedding

            report = verify_embedding(
                self,
                similar=("用户喜欢喝冰可乐。", "用户喜欢什么饮料？"),
                dissimilar=("用户喜欢喝冰可乐。", "今天的股票行情怎么样？"),
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "provider": self.name,
                "model": self.model_name,
                "dimension": self.dimension,
                "reason": str(exc) or exc.__class__.__name__,
            }
        ok = bool(report.get("ok"))
        return {
            "ok": ok,
            "status": "ready" if ok else "degraded",
            "provider": self.name,
            "model": self.model_name,
            "dimension": self.dimension,
            "similar_score": float(report.get("similar_score", 0.0) or 0.0),
            "dissimilar_score": float(report.get("dissimilar_score", 0.0) or 0.0),
            "gap": float(report.get("gap", 0.0) or 0.0),
            "reason": "" if ok else "semantic_separation_too_weak",
        }
