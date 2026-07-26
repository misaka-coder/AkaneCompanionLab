"""Adapters from Akane runtime objects to memcore interfaces.

No top-level memcore imports live here. Explicit legacy-mode tests and tools can
still boot without loading the sibling package.
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


def _max_attempts(request: Any) -> int:
    try:
        retries = int(getattr(request, "max_retries", 1) or 0)
    except (TypeError, ValueError):
        retries = 0
    return 1 + max(0, retries)


def _wait_before_retry(attempts: int) -> None:
    time.sleep(min(1.0, 0.25 * (2 ** max(0, attempts - 1))))


def build_akane_llm_client(llm: Any) -> Any:
    """Build a memcore.LLMClient wrapper around Akane's LLMRuntime."""

    from memcore import LLMClient, LLMResult

    class AkaneLLMClient(LLMClient):
        def __init__(self, runtime: Any) -> None:
            self.runtime = runtime

        def call(self, request: Any) -> Any:
            start = time.perf_counter()
            fallback = request.fallback if isinstance(getattr(request, "fallback", None), dict) else {}
            response_format = _response_format_value(getattr(request, "response_format", ""))
            max_attempts = _max_attempts(request)
            attempts = 0
            last_error = ""
            last_events: list[Any] = []
            while attempts < max_attempts:
                attempts += 1
                try:
                    if response_format == "ndjson":
                        result = self.runtime.call_aux_ndjson(
                            system_prompt=str(request.system_prompt or ""),
                            user_prompt=str(request.user_prompt or ""),
                            temperature=float(getattr(request, "temperature", 0.2) or 0.2),
                            prompt_cache_key=f"memcore:{_task_type_value(getattr(request, 'task_type', ''))}",
                        )
                        last_events = list(getattr(result, "events", []) or [])
                        last_error = str(getattr(result, "error", "") or "")
                        if not last_error and last_events:
                            return LLMResult(
                                ok=True,
                                data=last_events,
                                latency_ms=_latency_ms(start),
                                attempts=attempts,
                                degraded_to_fallback=False,
                            )
                        if not last_error:
                            last_error = "empty_events_returned"
                    else:
                        data = self.runtime.call_aux_json(
                            system_prompt=str(request.system_prompt or ""),
                            user_prompt=str(request.user_prompt or ""),
                            fallback=fallback,
                            temperature=float(getattr(request, "temperature", 0.2) or 0.2),
                            prompt_cache_key=f"memcore:{_task_type_value(getattr(request, 'task_type', ''))}",
                        )
                        degraded = dict(data or {}) == fallback
                        if isinstance(data, dict) and not degraded:
                            return LLMResult(
                                ok=True,
                                data=dict(data),
                                latency_ms=_latency_ms(start),
                                attempts=attempts,
                                degraded_to_fallback=False,
                            )
                        last_error = "fallback_returned" if degraded else "invalid_json_result"
                except Exception as exc:
                    last_error = str(exc) or exc.__class__.__name__

                if attempts < max_attempts:
                    _wait_before_retry(attempts)

            if response_format == "ndjson":
                return LLMResult(
                    ok=False,
                    data=last_events,
                    error=last_error or "ndjson_call_failed",
                    latency_ms=_latency_ms(start),
                    attempts=attempts,
                    degraded_to_fallback=True,
                )
            return LLMResult(
                ok=False,
                data=fallback,
                error=last_error or "json_call_failed",
                latency_ms=_latency_ms(start),
                attempts=attempts,
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

        def embed_query(self, text: str) -> list[float]:
            method = getattr(self.inner, "embed_query", None)
            if callable(method):
                return [float(value) for value in method(str(text or ""))]
            return self.embed_text(text)

        def embed_queries(self, texts: Iterable[str]) -> list[list[float]]:
            items = [str(text or "") for text in texts]
            method = getattr(self.inner, "embed_queries", None)
            if callable(method):
                return [[float(value) for value in vector] for vector in method(items)]
            single = getattr(self.inner, "embed_query", None)
            if callable(single):
                return [[float(value) for value in single(text)] for text in items]
            return self.embed_texts(items)

        def embed_document(self, text: str) -> list[float]:
            method = getattr(self.inner, "embed_document", None)
            if callable(method):
                return [float(value) for value in method(str(text or ""))]
            return self.embed_text(text)

        def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
            items = [str(text or "") for text in texts]
            method = getattr(self.inner, "embed_documents", None)
            if callable(method):
                return [[float(value) for value in vector] for vector in method(items)]
            single = getattr(self.inner, "embed_document", None)
            if callable(single):
                return [[float(value) for value in single(text)] for text in items]
            return self.embed_texts(items)

    return AkaneEmbeddingProvider(provider)


def build_akane_token_counter() -> Any:
    """Build the explicit estimated counter used by Akane prompt audits.

    Akane supports several OpenAI-compatible routes with different tokenizers,
    so claiming an exact model tokenizer here would be false.  This estimator
    matches the host's existing audit/guardrail formula and advertises
    ``quality=estimated`` to MemCore compaction metrics.
    """

    from memcore import TokenCounter

    class AkaneEstimatedTokenCounter(TokenCounter):
        @property
        def quality(self) -> str:
            return "estimated"

        def count_text(self, text: str) -> int:
            raw = str(text or "")
            if not raw:
                return 0
            cjk_chars = sum(1 for char in raw if "\u4e00" <= char <= "\u9fff")
            non_cjk_chars = max(0, len(raw) - cjk_chars)
            return int(cjk_chars + ((non_cjk_chars + 3) // 4))

    return AkaneEstimatedTokenCounter()
