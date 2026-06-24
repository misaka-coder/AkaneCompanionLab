from __future__ import annotations

import inspect
import os
import warnings
from contextlib import contextmanager
from typing import Iterable

from .embedding_provider import BaseEmbeddingProvider

DEFAULT_HUGGINGFACE_EMBEDDING_MODEL = "BAAI/bge-m3"


class HuggingFaceEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "huggingface"

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_HUGGINGFACE_EMBEDDING_MODEL,
        device: str | None = None,
        local_files_only: bool = False,
        cache_folder: str | None = None,
        normalize_embeddings: bool = True,
    ):
        self.model_name = str(model_name or DEFAULT_HUGGINGFACE_EMBEDDING_MODEL).strip() or DEFAULT_HUGGINGFACE_EMBEDDING_MODEL
        self.device = str(device or "").strip() or None
        self.local_files_only = bool(local_files_only)
        self.cache_folder = str(cache_folder or "").strip() or None
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
            kwargs: dict[str, object] = {"device": self.device}
            if self.cache_folder:
                kwargs["cache_folder"] = self.cache_folder
            try:
                signature = inspect.signature(SentenceTransformer)
            except (TypeError, ValueError):
                signature = None
            if signature is None or "local_files_only" in signature.parameters:
                kwargs["local_files_only"] = self.local_files_only
            with _temporary_hf_offline_env(self.local_files_only):
                return SentenceTransformer(self.model_name, **kwargs)

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


@contextmanager
def _temporary_hf_offline_env(enabled: bool):
    if not enabled:
        yield
        return
    previous_value = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        yield
    finally:
        if previous_value is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_value
