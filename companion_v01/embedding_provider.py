from __future__ import annotations

import hashlib
import math
import threading
import re
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Iterable

from .text_utils import tokenize

_COLLECTION_COMPONENT_RE = re.compile(r"[^a-z0-9_-]+")


class BaseEmbeddingProvider(ABC):
    provider_name = "base"
    version = "v1"

    def __init__(self, *, dimension: int):
        self._dimension = max(1, int(dimension))

    @property
    def name(self) -> str:
        return str(self.provider_name or "base").strip() or "base"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def legacy_collection_name(self) -> str | None:
        return None

    def collection_key(self) -> str:
        raw = f"{self.name}_{self.version}_{self.dimension}".lower()
        clean = _COLLECTION_COMPONENT_RE.sub("_", raw).strip("_")
        return clean or "embedding"

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]


class HashedEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "hashed"
    version = "v1"

    def __init__(self, *, dimension: int = 128):
        super().__init__(dimension=dimension)

    @property
    def legacy_collection_name(self) -> str | None:
        if self.dimension == 128 and str(self.version).strip().lower() == "v1":
            return "akane_memory_v01"
        return None

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + (len(token) / 10.0)
            vector[index] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector


class CachedEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self, inner: BaseEmbeddingProvider, *, max_entries: int = 2048):
        super().__init__(dimension=inner.dimension)
        self.inner = inner
        self.max_entries = max(0, int(max_entries))
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def legacy_collection_name(self) -> str | None:
        return self.inner.legacy_collection_name

    @property
    def version(self) -> str:
        return self.inner.version

    def collection_key(self) -> str:
        return self.inner.collection_key()

    def embed_text(self, text: str) -> list[float]:
        raw_text = str(text or "")
        if self.max_entries <= 0:
            return self.inner.embed_text(raw_text)

        cached = self._cache_get(raw_text)
        if cached is not None:
            return list(cached)

        vector = tuple(float(value) for value in self.inner.embed_text(raw_text))
        self._cache_put(raw_text, vector)
        return list(vector)

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        normalized_texts = [str(text or "") for text in texts]
        if self.max_entries <= 0:
            return self.inner.embed_texts(normalized_texts)

        results: list[list[float] | None] = [None] * len(normalized_texts)
        missing_positions: dict[str, list[int]] = {}
        for idx, raw_text in enumerate(normalized_texts):
            cached = self._cache_get(raw_text)
            if cached is not None:
                results[idx] = list(cached)
                continue
            missing_positions.setdefault(raw_text, []).append(idx)

        if missing_positions:
            missing_texts = list(missing_positions.keys())
            missing_vectors = self.inner.embed_texts(missing_texts)
            for raw_text, vector in zip(missing_texts, missing_vectors):
                frozen = tuple(float(value) for value in vector)
                self._cache_put(raw_text, frozen)
                for idx in missing_positions.get(raw_text, []):
                    results[idx] = list(frozen)

        return [vector or [] for vector in results]

    def _cache_get(self, text: str) -> tuple[float, ...] | None:
        with self._lock:
            vector = self._cache.get(text)
            if vector is None:
                return None
            self._cache.move_to_end(text)
            return vector

    def _cache_put(self, text: str, vector: tuple[float, ...]) -> None:
        with self._lock:
            self._cache[text] = vector
            self._cache.move_to_end(text)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
