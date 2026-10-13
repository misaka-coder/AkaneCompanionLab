from __future__ import annotations

import inspect
import math
import os
import warnings
from contextlib import contextmanager
from typing import Iterable

from .embedding_provider import BaseEmbeddingProvider
from .embedding_runtime import LocalEmbeddingRuntime

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
        precision: str = "float32",
        batch_size: int = 8,
        idle_seconds: float = 30.0,
    ):
        self.model_name = str(model_name or DEFAULT_HUGGINGFACE_EMBEDDING_MODEL).strip() or DEFAULT_HUGGINGFACE_EMBEDDING_MODEL
        self.device = str(device or "").strip() or None
        self.local_files_only = bool(local_files_only)
        self.cache_folder = str(cache_folder or "").strip() or None
        self.normalize_embeddings = bool(normalize_embeddings)
        self.precision = str(precision).strip().lower()
        if self.precision not in {"float32", "float16", "bfloat16"}:
            raise ValueError("embedding_precision_invalid")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("embedding_batch_size_invalid")
        idle_seconds = float(idle_seconds)
        if not math.isfinite(idle_seconds) or idle_seconds < 0:
            raise ValueError("embedding_idle_seconds_invalid")
        # CPU inference keeps its original full precision behavior.
        if self.device == "cpu":
            self.precision = "float32"
        self.batch_size = batch_size
        self._model = self._load_model()
        device = str(getattr(self._model, "device", None) or self.device or "cpu")
        self._runtime = LocalEmbeddingRuntime(
            self._model, device=device, batch_size=batch_size, idle_seconds=idle_seconds,
        )

        dimension = self._model.get_sentence_embedding_dimension()
        if not dimension:
            probe = self.embed_texts(["探针"])
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
            if self.precision != "float32":
                kwargs["model_kwargs"] = {"torch_dtype": self.precision}
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
        with self._runtime.inference():
            batch_size = self._runtime.batch_size
            while True:
                try:
                    vectors = self._model.encode(
                        raw_texts,
                        batch_size=batch_size,
                        normalize_embeddings=self.normalize_embeddings,
                        convert_to_numpy=True,
                        show_progress_bar=False,
                    )
                except Exception as exc:
                    if not self._runtime.is_cuda_oom(exc):
                        raise
                    if batch_size == 1:
                        raise RuntimeError("embedding_cuda_out_of_memory") from None
                else:
                    result = [list(map(float, vector)) for vector in vectors]
                    dimension = getattr(self, "_dimension", len(result[0]) if result else 0)
                    if len(result) != len(raw_texts) or any(
                        len(vector) != dimension or not all(math.isfinite(value) for value in vector)
                        for vector in result
                    ):
                        raise RuntimeError("embedding_vectors_invalid")
                    return result
                # Leave the exception scope before releasing temporary CUDA tensors.
                batch_size = max(1, batch_size // 2)
                self._runtime.reduce_batch(batch_size)

    def snapshot_runtime(self) -> dict[str, object]:
        return self._runtime.snapshot()

    def close(self, *, timeout: float = 5.0) -> bool:
        return self._runtime.close(self._release_model, timeout=timeout)

    def _release_model(self) -> None:
        self._model = None


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
