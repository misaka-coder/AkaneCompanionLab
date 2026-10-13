"""Compatibility entry and instance state for the Akane Memcore manager."""
from __future__ import annotations

from concurrent.futures import Future
import logging
from pathlib import Path
import threading
from typing import Any, Callable
import config
from .catalog import MemcoreCatalogMixin
from .compaction import MemcoreCompactionMixin
from .chat_prefix import MemcoreChatPrefixMixin
from .completed_history import MemcoreCompletedHistoryMixin
from .contracts import (
    MEMCORE_PROVIDER_PROFILE_ALIASES,
    MemcoreRuntimeStatus,
    SUPPORTED_MEMORY_BACKENDS,
    normalize_memory_backend,
    normalize_retrieval_mode,
    normalize_visible_scope,
    resolve_memcore_provider_profile,
)
from .input_assembly import MemcoreInputAssemblyMixin
from .history_notice import MemcoreHistoryNoticeMixin
from .lifecycle import MemcoreLifecycleMixin, _build_persona_text_provider
from .materials import MemcoreMaterialWritesMixin
from .projections import MemcoreProjectionsMixin
from .retrieval import MemcoreRetrievalMixin
from .support import MemcoreSupportMixin
from .turns import MemcoreTurnWritesMixin
from .voice_projection import MemcoreVoiceProjectionMixin
from .subagents import MemcoreSubagentsMixin

logger = logging.getLogger("akane.memcore")


class MemcoreManager(
    MemcoreChatPrefixMixin,
    MemcoreSubagentsMixin,
    MemcoreLifecycleMixin,
    MemcoreCompactionMixin,
    MemcoreHistoryNoticeMixin,
    MemcoreSupportMixin,
    MemcoreInputAssemblyMixin,
    MemcoreTurnWritesMixin,
    MemcoreCompletedHistoryMixin,
    MemcoreVoiceProjectionMixin,
    MemcoreMaterialWritesMixin,
    MemcoreCatalogMixin,
    MemcoreProjectionsMixin,
    MemcoreRetrievalMixin,
):
    def __init__(
        self,
        *,
        backend: str,
        storage_path: Path,
        visible_scope: str,
        enable_flavor: bool,
        shadow_compare: bool,
        llm: Any,
        embedding_provider: Any,
        retrieval_mode: str = "hybrid",
        persona_text_provider: Callable[[str, str], str] | None = None,
        history_notice_policy_provider: Callable[[str], dict[str, Any]] | None = None,
        runtime: Any | None = None,
    ) -> None:
        self.backend = normalize_memory_backend(backend)
        self._retrieval_mode = normalize_retrieval_mode(retrieval_mode, backend=self.backend)
        if self._retrieval_mode == "navigation" and embedding_provider is not None:
            raise ValueError("navigation mode does not accept an embedding provider")
        self.storage_path = Path(storage_path)
        self.visible_scope = normalize_visible_scope(visible_scope)
        self.enable_flavor = bool(enable_flavor)
        self.shadow_compare = bool(shadow_compare)
        self.llm = llm
        self.embedding_provider = embedding_provider
        self._persona_text_provider = persona_text_provider
        self._history_notice_policy_provider = history_notice_policy_provider
        self._history_notice_ready: set[tuple[str, str, str, str]] = set()
        self._history_notice_refresh_pending: set[tuple[str, str, str, str]] = set()
        self._available = False
        self._reason = ""
        self._degraded_embedding = str(getattr(embedding_provider, "name", "") or "").lower() == "hashed"
        self._memcore_module: Any | None = None
        self._embedding: Any | None = None
        self._token_counter: Any | None = None
        self._memory_config: Any | None = None
        self._store: Any | None = None
        self._index: Any | None = None
        self._runtime: Any | None = runtime
        self._uses_process_runtime = False
        self._systems: dict[tuple[str, str, str], Any] = {}
        self._session_llms: dict[tuple[str, str, str], Any] = {}
        self._session_token_limits: dict[tuple[str, str, str], int] = {}
        self._deleting_namespaces: set[tuple[str, str, str]] = set()
        self._index_warmup_futures: dict[tuple[str, ...], Future[Any]] = {}
        self._compaction_chat_model_overrides: dict[tuple[str, str, str], str] = {}
        self._compaction_turn_model_hints: dict[tuple[str, str, str], str] = {}
        self._warmed_index_keys: set[tuple[str, str, str]] = set()
        self._background_futures: set[Future[Any]] = set()
        self._background_compactions: dict[tuple[str, str, str, str], Future[Any]] = {}
        self._pending_compactions: dict[tuple[str, str, str, str], tuple[Any, str]] = {}
        self._compaction_retry_after: dict[tuple[str, str, str, str], float] = {}
        self._lock = threading.RLock()
        self._jobs_condition = threading.Condition(self._lock)
        self._shutdown_event = threading.Event()
        self._closing = False
        self._closed = False
        self._close_finalizing = False
        self._deferred_close = False
        if self.backend == "legacy":
            logger.warning(
                "MEMORY_BACKEND='legacy' is deprecated and will be retired in a future release. "
                "MemCore is the authoritative memory engine. Set MEMORY_BACKEND='memcore'."
            )
        elif self.backend == "dual":
            logger.info("MEMORY_BACKEND='dual' is active as a transition/comparison mode.")
        if self.enabled:
            self._bootstrap()

    @classmethod
    def from_engine(cls, engine: Any) -> "MemcoreManager":
        from ..speech_policy import character_speech_policy

        storage_path = Path(
            getattr(engine, "memcore_storage_path", Path(engine.base_dir) / "memcore_v01.db")
        )
        return cls(
            backend=getattr(config, "MEMORY_BACKEND", "memcore"),
            storage_path=storage_path,
            visible_scope=getattr(config, "MEMCORE_VISIBLE_SCOPE", "user"),
            enable_flavor=bool(getattr(config, "MEMCORE_ENABLE_FLAVOR", True)),
            shadow_compare=bool(getattr(config, "MEMCORE_SHADOW_COMPARE", False)),
            llm=engine.llm,
            embedding_provider=engine.embedding_provider,
            retrieval_mode=getattr(engine, "memory_retrieval_mode", getattr(config, "MEMCORE_RETRIEVAL_MODE", "navigation")),
            persona_text_provider=_build_persona_text_provider(engine),
            history_notice_policy_provider=lambda pack_id: character_speech_policy(engine, pack_id),
        )

    @property
    def enabled(self) -> bool:
        return self.backend != "legacy"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def retrieval_mode(self) -> str:
        return self._retrieval_mode

    def status(self) -> dict[str, Any]:
        return MemcoreRuntimeStatus(
            backend=self.backend,
            enabled=self.enabled,
            available=self.available,
            storage_path=str(self.storage_path),
            visible_scope=self.visible_scope,
            enable_flavor=self.enable_flavor,
            shadow_compare=self.shadow_compare,
            retrieval_mode=self.retrieval_mode,
            degraded_embedding=self._degraded_embedding,
            reason=self._reason,
        ).to_dict()
