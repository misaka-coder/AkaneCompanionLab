"""Memcore configuration normalization, status contracts and shared constants."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


SUPPORTED_MEMORY_BACKENDS = frozenset({"legacy", "dual", "memcore"})


MEMCORE_PROVIDER_PROFILE_ALIASES = {
    "openai": "openai_chat",
    "openai_chat": "openai_chat",
    "native_openai": "openai_chat",
    "openai_responses": "openai_responses",
    "responses": "openai_chat",
    "deepseek": "deepseek_chat",
    "deepseek_chat": "deepseek_chat",
    "ollama": "openai_chat",
    # GeminiNativeCompatClient accepts the same OpenAI-shaped message history
    # as the rest of Akane and converts it only at the final wire boundary.
    "gemini": "openai_chat",
    "anthropic": "anthropic_messages",
    "anthropic_messages": "anthropic_messages",
    "native_anthropic": "anthropic_messages",
    "canonical": "canonical_user_assistant",
    "canonical_user_assistant": "canonical_user_assistant",
}


_EXPLICIT_RETRIEVAL_KIND_ROOTS = ("tool", "event", "skill", "material")


_EXPLICIT_KIND_PATTERN = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*(?:\.\*)?")


_STALE_OPEN_TURN_MAX_AGE_SECONDS = 30 * 60


_PROMPT_CONTEXT_SOURCE_PREFIX = "prompt-context"


_PROMPT_CONTEXT_KIND_PREFIX = "material.runtime_context"


_PROMPT_CONTEXT_RENDERER_ID = "akane.prompt_context_snapshot"


_CAPABILITY_CATALOG_NAME = "capability-catalog"


_CAPABILITY_CATALOG_SOURCE_PREFIX = f"{_PROMPT_CONTEXT_SOURCE_PREFIX}:{_CAPABILITY_CATALOG_NAME}:"


def normalize_memory_backend(value: Any) -> str:
    text = str(value or "memcore").strip().lower()
    return text if text in SUPPORTED_MEMORY_BACKENDS else "memcore"


def normalize_visible_scope(value: Any) -> str:
    text = str(value or "user").strip().lower()
    return text if text in {"conversation", "user"} else "user"


def normalize_retrieval_mode(value: Any, *, backend: str = "memcore") -> str:
    """Legacy/dual consumers still require their existing embedding provider."""
    if value not in ("hybrid", "navigation"):
        raise ValueError("MEMCORE_RETRIEVAL_MODE must be hybrid or navigation")
    return str(value) if normalize_memory_backend(backend) == "memcore" else "hybrid"


def resolve_memcore_provider_profile(value: Any) -> str:
    """Map the actual provider protocol to a MemCore projection profile."""

    return MEMCORE_PROVIDER_PROFILE_ALIASES.get(str(value or "").strip().lower(), "")


@dataclass(frozen=True)
class MemcoreRuntimeStatus:
    backend: str
    enabled: bool
    available: bool
    storage_path: str
    visible_scope: str
    enable_flavor: bool
    shadow_compare: bool
    retrieval_mode: str = "hybrid"
    degraded_embedding: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
