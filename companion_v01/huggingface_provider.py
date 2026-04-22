from __future__ import annotations

import warnings
from typing import Iterable

from .embedding_provider import BaseEmbeddingProvider


class HuggingFaceEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "huggingface"

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        device: str | None = None,
        normalize_embeddings: bool = True,
    ):
        self.model_name = str(model_name or "BAAI/bge-small-zh-v1.5").strip() or "BAAI/bge-small-zh-v1.5"
        self.device = str(device or "").strip() or None
        self.normalize_embeddings = bool(normalize_embeddings)
        self._model = self._load_model()

        dimension = self._model.get_sentence_embedding_dimension()
        if not dimension:
            probe = self._model.encode(
                ["探针"],
                normalize_embeddings=self.normalize_embeddings,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            dimension = int(len(probe[0]))
        super().__init__(dimension=int(dimension))

    @property
    def version(self) -> str:
        return self.model_name

    def _load_model(self):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"The pynvml package is deprecated\..*",
                category=FutureWarning,
            )
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed; install requirements-ml.txt to enable HuggingFace embeddings."
                ) from exc
            return SentenceTransformer(self.model_name, device=self.device)

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        raw_texts = [str(text or "") for text in texts]
        if not raw_texts:
            return []
        vectors = self._model.encode(
            raw_texts,
            normalize_embeddings=self.normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [list(map(float, vector)) for vector in vectors]
