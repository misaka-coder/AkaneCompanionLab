"""Adapters from Akane runtime objects to memcore interfaces.

No top-level memcore imports live here. The bridge is optional and default-off,
so a legacy Akane boot must not fail merely because the sibling package has not
been installed yet.
"""

from __future__ import annotations

import time
from typing import Any, Iterable


def _response_format_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _task_type_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _latency_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def build_akane_llm_client(llm: Any) -> Any:
    """Build a memcore.LLMClient wrapper around Akane's LLMRuntime."""

    from memcore import LLMClient, LLMResult

    class AkaneLLMClient(LLMClient):
        def __init__(self, runtime: Any) -> None:
            self.runtime = runtime

        def call(self, request: Any) -> Any:
            start = time.perf_counter()
            fallback = request.fallback if isinstance(getattr(request, "fallback", None), dict) else {}
            try:
                response_format = _response_format_value(getattr(request, "response_format", ""))
                if response_format == "ndjson":
                    result = self.runtime.call_aux_ndjson(
                        system_prompt=str(request.system_prompt or ""),
                        user_prompt=str(request.user_prompt or ""),
                        temperature=float(getattr(request, "temperature", 0.2) or 0.2),
                        prompt_cache_key=f"memcore:{_task_type_value(getattr(request, 'task_type', ''))}",
                    )
                    events = list(getattr(result, "events", []) or [])
                    error = str(getattr(result, "error", "") or "")
                    return LLMResult(
                        ok=not error and bool(events),
                        data=events,
                        error=error,
                        latency_ms=_latency_ms(start),
                        attempts=max(1, int(getattr(request, "max_retries", 1) or 1)),
                        degraded_to_fallback=bool(error),
                    )

                data = self.runtime.call_aux_json(
                    system_prompt=str(request.system_prompt or ""),
                    user_prompt=str(request.user_prompt or ""),
                    fallback=fallback,
                    temperature=float(getattr(request, "temperature", 0.2) or 0.2),
                    prompt_cache_key=f"memcore:{_task_type_value(getattr(request, 'task_type', ''))}",
                )
                degraded = dict(data or {}) == fallback
                return LLMResult(
                    ok=isinstance(data, dict) and not degraded,
                    data=dict(data or fallback),
                    error="fallback_returned" if degraded else "",
                    latency_ms=_latency_ms(start),
                    attempts=max(1, int(getattr(request, "max_retries", 1) or 1)),
                    degraded_to_fallback=degraded,
                )
            except Exception as exc:
                return LLMResult(
                    ok=False,
                    data=fallback,
                    error=str(exc) or exc.__class__.__name__,
                    latency_ms=_latency_ms(start),
                    attempts=max(1, int(getattr(request, "max_retries", 1) or 1)),
                    degraded_to_fallback=True,
                )

    return AkaneLLMClient(llm)


def build_akane_embedding_provider(provider: Any) -> Any:
    """Build a memcore.EmbeddingProvider wrapper around Akane's provider."""

    from memcore import EmbeddingProvider

    class AkaneEmbeddingProvider(EmbeddingProvider):
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        @property
        def name(self) -> str:
            return str(getattr(self.inner, "name", "") or getattr(self.inner, "provider_name", "") or "akane")

        @property
        def version(self) -> str:
            return str(getattr(self.inner, "version", "") or "v1")

        @property
        def dimension(self) -> int:
            return max(1, int(getattr(self.inner, "dimension", 0) or 1))

        def embed_text(self, text: str) -> list[float]:
            return [float(value) for value in self.inner.embed_text(str(text or ""))]

        def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
            if hasattr(self.inner, "embed_texts"):
                return [
                    [float(value) for value in vector]
                    for vector in self.inner.embed_texts([str(text or "") for text in texts])
                ]
            return [self.embed_text(str(text or "")) for text in texts]

    return AkaneEmbeddingProvider(provider)
