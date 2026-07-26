from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .embedding_provider import BaseEmbeddingProvider


class RemoteEmbeddingProvider(BaseEmbeddingProvider):
    """Thin Akane adapter for a symmetric OpenAI-compatible embeddings API."""

    provider_name = "remote"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        dimension: int,
        timeout: float = 30.0,
    ) -> None:
        resolved_key = str(api_key or "").strip()
        resolved_base_url = str(base_url or "").strip()
        resolved_model = str(model_name or "").strip()
        resolved_dimension = int(dimension)
        if not resolved_key:
            raise ValueError("remote_embedding_api_key_required")
        if not resolved_base_url:
            raise ValueError("remote_embedding_base_url_required")
        if not resolved_model:
            raise ValueError("remote_embedding_model_required")
        if resolved_dimension <= 0:
            raise ValueError("remote_embedding_dimension_invalid")
        try:
            from memcore import HTTPEmbeddingProvider
        except ImportError as exc:
            raise RuntimeError("remote_embedding_memcore_transport_unavailable") from exc

        self.model_name = resolved_model
        self.base_url = resolved_base_url.rstrip("/")
        self._inner = HTTPEmbeddingProvider(
            base_url=self.base_url,
            api_key=resolved_key,
            model=self.model_name,
            dimension=resolved_dimension,
            timeout=max(1.0, float(timeout)),
            name=f"remote:{self.model_name}",
        )
        super().__init__(dimension=resolved_dimension)

    @property
    def version(self) -> str:
        identity = json.dumps(
            {
                "base_url": self.base_url,
                "model": self.model_name,
                "dimension": self.dimension,
                "transport": self._inner.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"v1-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"

    def embed_text(self, text: str) -> list[float]:
        return self._inner.embed_text(text)

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        return self._inner.embed_texts(texts)

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
