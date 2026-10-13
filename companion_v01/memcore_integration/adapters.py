"""Adapters from Akane runtime objects to memcore interfaces.

No top-level memcore imports live here. Explicit legacy-mode tests and tools can
still boot without loading the sibling package.
"""

from __future__ import annotations

import time
import inspect
from typing import Any, Callable, Iterable

from ..text_utils import estimate_prompt_tokens


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


def _supports_keyword(call: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def build_akane_llm_client(
    llm: Any,
    *,
    cancellation_requested: Callable[[], bool] | None = None,
    chat_model_override_provider: Callable[[], str] | None = None,
) -> Any:
    """Build a memcore.LLMClient wrapper around Akane's LLMRuntime."""

    from memcore import LLMClient, LLMResult

    class AkaneLLMClient(LLMClient):
        def __init__(self, runtime: Any) -> None:
            self.runtime = runtime

        @staticmethod
        def _cancelled() -> bool:
            return bool(cancellation_requested and cancellation_requested())

        def call(self, request: Any) -> Any:
            start = time.perf_counter()
            fallback = request.fallback if isinstance(getattr(request, "fallback", None), dict) else {}
            max_attempts = _max_attempts(request)
            attempts = 0
            last_error = ""
            while attempts < max_attempts:
                if self._cancelled():
                    last_error = "shutdown_requested"
                    break
                attempts += 1
                try:
                    prefix = getattr(request, "chat_prefix", None)
                    call_prefix = getattr(self.runtime, "call_memcore_prefix_json", None)
                    if prefix is not None:
                        if not callable(call_prefix):
                            raise ValueError("memcore_chat_prefix_transport_unavailable")
                        data = call_prefix(
                            chat_prefix=prefix, contract=str(request.system_prompt or ""), fallback=fallback,
                            request_timeout_s=float(getattr(request, "timeout_s", 30.0) or 30.0),
                        )
                        if self._cancelled():
                            last_error = "shutdown_requested"
                            break
                        if isinstance(data, dict) and data and data != fallback:
                            return LLMResult(ok=True, data=data, latency_ms=_latency_ms(start), attempts=attempts)
                        raise ValueError("prefix_invalid_json_result")
                    call_memcore = getattr(self.runtime, "call_memcore_json", None)
                    call = call_memcore if callable(call_memcore) else self.runtime.call_aux_json
                    kwargs = dict(
                        system_prompt=str(request.system_prompt or ""),
                        user_prompt=str(request.user_prompt or ""),
                        fallback=fallback,
                        temperature=float(getattr(request, "temperature", 0.2) or 0.2),
                        prompt_cache_key=f"memcore:{_task_type_value(getattr(request, 'task_type', ''))}",
                    )
                    if _supports_keyword(call, "request_timeout_s"):
                        kwargs["request_timeout_s"] = max(
                            0.1,
                            float(getattr(request, "timeout_s", 30.0) or 30.0),
                        )
                    if callable(call_memcore) and chat_model_override_provider is not None and _supports_keyword(call, "chat_model_override"):
                        kwargs["chat_model_override"] = chat_model_override_provider()
                    data = call(**kwargs)
                    if self._cancelled():
                        last_error = "shutdown_requested"
                        break
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

                if attempts < max_attempts and not self._cancelled():
                    _wait_before_retry(attempts)

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
            return estimate_prompt_tokens(text)

    return AkaneEstimatedTokenCounter()
