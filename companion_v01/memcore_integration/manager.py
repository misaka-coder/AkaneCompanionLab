"""Memcore runtime manager for Akane.

The bridge is optional and default-off. In dual-write mode the legacy Akane
memory stack remains source of truth, while this manager mirrors raw turns into
memcore with the same source_id.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any

import config

from .adapters import build_akane_embedding_provider, build_akane_llm_client
from .diagnostics import snippet_hashes


SUPPORTED_MEMORY_BACKENDS = frozenset({"legacy", "dual", "memcore"})
logger = logging.getLogger("akane.memcore")


def normalize_memory_backend(value: Any) -> str:
    text = str(value or "legacy").strip().lower()
    return text if text in SUPPORTED_MEMORY_BACKENDS else "legacy"


def normalize_visible_scope(value: Any) -> str:
    text = str(value or "user").strip().lower()
    return text if text in {"conversation", "user"} else "user"


@dataclass(frozen=True)
class MemcoreRuntimeStatus:
    backend: str
    enabled: bool
    available: bool
    storage_path: str
    visible_scope: str
    enable_flavor: bool
    shadow_compare: bool
    degraded_embedding: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemcoreManager:
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
    ) -> None:
        self.backend = normalize_memory_backend(backend)
        self.storage_path = Path(storage_path)
        self.visible_scope = normalize_visible_scope(visible_scope)
        self.enable_flavor = bool(enable_flavor)
        self.shadow_compare = bool(shadow_compare)
        self.llm = llm
        self.embedding_provider = embedding_provider
        self._available = False
        self._reason = ""
        self._degraded_embedding = str(getattr(embedding_provider, "name", "") or "").lower() == "hashed"
        self._memcore_module: Any | None = None
        self._llm_client: Any | None = None
        self._embedding: Any | None = None
        self._memory_config: Any | None = None
        self._store: Any | None = None
        self._index: Any | None = None
        self._systems: dict[tuple[str, str, str], Any] = {}
        self._lock = threading.RLock()
        if self.enabled:
            self._bootstrap()

    @classmethod
    def from_engine(cls, engine: Any) -> "MemcoreManager":
        raw_path = str(getattr(config, "MEMCORE_STORAGE_PATH", "") or "").strip()
        storage_path = Path(raw_path) if raw_path else Path(engine.base_dir) / "memcore_v01.db"
        return cls(
            backend=getattr(config, "MEMORY_BACKEND", "legacy"),
            storage_path=storage_path,
            visible_scope=getattr(config, "MEMCORE_VISIBLE_SCOPE", "user"),
            enable_flavor=bool(getattr(config, "MEMCORE_ENABLE_FLAVOR", True)),
            shadow_compare=bool(getattr(config, "MEMCORE_SHADOW_COMPARE", False)),
            llm=engine.llm,
            embedding_provider=engine.embedding_provider,
        )

    @property
    def enabled(self) -> bool:
        return self.backend != "legacy"

    @property
    def available(self) -> bool:
        return self._available

    def status(self) -> dict[str, Any]:
        return MemcoreRuntimeStatus(
            backend=self.backend,
            enabled=self.enabled,
            available=self.available,
            storage_path=str(self.storage_path),
            visible_scope=self.visible_scope,
            enable_flavor=self.enable_flavor,
            shadow_compare=self.shadow_compare,
            degraded_embedding=self._degraded_embedding,
            reason=self._reason,
        ).to_dict()

    def close(self) -> None:
        with self._lock:
            systems = list(self._systems.values())
            self._systems.clear()
            store = self._store
            self._store = None
            self._index = None
        for system in systems:
            try:
                system.close()
            except Exception as exc:
                logger.debug("memcore MemorySystem close failed: %s", exc)
        if store is not None:
            try:
                store.close()
            except Exception as exc:
                logger.debug("memcore store close failed: %s", exc)

    def record_user_turn(
        self,
        record: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        return self._record_turn(
            operation="record_user_turn",
            role="user",
            record=record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )

    def record_assistant_turn(
        self,
        record: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        return self._record_turn(
            operation="record_assistant_turn",
            role="assistant",
            record=record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )

    def update_turn_metadata(
        self,
        source_id: str,
        memory_metadata: dict[str, Any] | None,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="update_turn_metadata",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        sid = str(source_id or "").strip()
        if system is None:
            return self._status("update_turn_metadata", False, "unavailable", source_id=sid, reason=self._reason)
        try:
            result = system.update_turn_metadata(sid, memory_metadata if isinstance(memory_metadata, dict) else {})
            return dict(result, operation="update_turn_metadata")
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore metadata dual-write failed: %s", reason)
            return self._status("update_turn_metadata", False, "failed", source_id=sid, reason=reason)

    def compact_due_background(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="compact_due_background",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status("compact_due_background", False, "unavailable", reason=self._reason)
        try:
            future = system.compact_due_background()
            future.add_done_callback(self._log_compaction_result)
            return self._status("compact_due_background", True, "scheduled")
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore background compaction scheduling failed: %s", reason)
            return self._status("compact_due_background", False, "failed", reason=reason)

    def shadow_retrieve_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        current_user_record: dict[str, Any] | None = None,
        query: str,
        keywords: list[str] | None = None,
        time_hint: dict[str, Any] | None = None,
        source_layers: list[str] | None = None,
        subject_scopes: list[str] | None = None,
        categories: list[str] | None = None,
        importance_min: float | int | str | None = None,
        exclude_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        shadow_enabled = bool(getattr(config, "MEMCORE_SHADOW_COMPARE", self.shadow_compare))
        if not shadow_enabled:
            return self._status("shadow_retrieve_memory", True, "disabled", reason="shadow_compare_disabled")
        system = self._get_system_or_none(
            operation="shadow_retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status("shadow_retrieve_memory", False, "unavailable", reason=self._reason)

        start = time.perf_counter()
        current = dict(current_user_record or {})
        if not str(current.get("source_id") or "").strip() and exclude_source_ids:
            current["source_id"] = str(exclude_source_ids[0] or "").strip()
        if not int(current.get("timestamp") or 0):
            current["timestamp"] = int(time.time())
        try:
            snippets = system.retrieve_for_turn(
                current=current,
                query=str(query or ""),
                keywords=[str(item).strip() for item in (keywords or []) if str(item).strip()],
                time_hint=time_hint if isinstance(time_hint, dict) else None,
                source_layers=[str(item).strip() for item in (source_layers or []) if str(item).strip()],
                subject_scopes=[str(item).strip() for item in (subject_scopes or []) if str(item).strip()],
                categories=[str(item).strip() for item in (categories or []) if str(item).strip()],
                importance_min=self._coerce_optional_unit_float(importance_min),
                exclude_source_ids=[str(item).strip() for item in (exclude_source_ids or []) if str(item).strip()],
            )
            return {
                **self._status("shadow_retrieve_memory", True, "ok"),
                "snippet_count": len(snippets),
                "snippet_hashes": snippet_hashes(snippets),
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore shadow retrieve failed: %s", reason)
            return {
                **self._status("shadow_retrieve_memory", False, "failed", reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
            }

    def _bootstrap(self) -> None:
        try:
            memcore = self._import_memcore()

            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._llm_client = build_akane_llm_client(self.llm)
            self._embedding = build_akane_embedding_provider(self.embedding_provider)
            self._memory_config = self._build_memory_config(memcore)
            self._store = memcore.SQLiteMemoryStore(str(self.storage_path))
            self._index = memcore.InMemoryVectorIndex(embedding=self._embedding)
            self._memcore_module = memcore
            self._available = True
            self._reason = ""
        except Exception as exc:
            self._available = False
            self._reason = str(exc) or exc.__class__.__name__

    @staticmethod
    def _import_memcore() -> Any:
        try:
            import memcore  # type: ignore

            return memcore
        except ModuleNotFoundError as exc:
            missing_name = getattr(exc, "name", None)
            if missing_name not in {None, "memcore"}:
                raise
            sibling = Path(__file__).resolve().parents[2].parent / "memcore"
            if not sibling.exists():
                raise
            sibling_text = str(sibling)
            inserted = sibling_text not in sys.path
            if inserted:
                sys.path.insert(0, sibling_text)
            try:
                import memcore  # type: ignore

                return memcore
            except Exception:
                if inserted:
                    try:
                        sys.path.remove(sibling_text)
                    except ValueError:
                        pass
                raise

    def _build_memory_config(self, memcore: Any) -> Any:
        return memcore.MemoryConfig(
            raw_trigger_count=max(1, int(getattr(config, "SUMMARY_TRIGGER_COUNT", 30) or 30)),
            summary_batch_size=max(1, int(getattr(config, "SUMMARY_BATCH_SIZE", 20) or 20)),
            episodic_visible_max=max(
                1,
                int(
                    getattr(
                        config,
                        "EPISODIC_VISIBLE_MAX",
                        getattr(config, "RECENT_SUMMARY_LIMIT", 8),
                    )
                    or 8
                ),
            ),
            episodic_compact_trigger_count=max(
                1,
                int(getattr(config, "EPISODIC_COMPACT_TRIGGER_COUNT", 10) or 10),
            ),
            episodic_compact_batch_size=max(
                1,
                int(getattr(config, "EPISODIC_COMPACT_BATCH_SIZE", 5) or 5),
            ),
            semantic_visible_limit=max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 5) or 5)),
            semantic_reinforcement_lookback=max(
                1,
                int(getattr(config, "SEMANTIC_REINFORCEMENT_LOOKBACK", 8) or 8),
            ),
            semantic_reinforcement_min_overlap=max(
                1,
                int(getattr(config, "SEMANTIC_REINFORCEMENT_MIN_OVERLAP", 2) or 2),
            ),
            visible_memory_scope=self.visible_scope,
            enable_flavor=self.enable_flavor,
        )

    def _record_turn(
        self,
        *,
        operation: str,
        role: str,
        record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        source_id = str((record or {}).get("source_id") or "").strip()
        if not source_id:
            return self._status(operation, False, "invalid_record", reason="source_id_required")
        if role == "user" and not bool((record or {}).get("index_in_vector", True)):
            return self._status(
                operation,
                True,
                "skipped",
                source_id=source_id,
                reason="legacy_index_disabled",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)

        content = str((record or {}).get("content") or "")
        timestamp = int((record or {}).get("timestamp") or time.time())
        memory_metadata = (record or {}).get("memory_metadata")
        if not isinstance(memory_metadata, dict):
            memory_metadata = {}
        try:
            if role == "assistant":
                written = system.record_assistant_turn(
                    content,
                    source_id=source_id,
                    timestamp=timestamp,
                    memory_metadata=memory_metadata,
                )
            else:
                written = system.record_user_turn(
                    content,
                    source_id=source_id,
                    timestamp=timestamp,
                    memory_metadata=memory_metadata,
                )
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.get("source_id") or source_id),
                index_status=str(written.get("index_status") or ""),
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    def _get_system_or_none(
        self,
        *,
        operation: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> Any | None:
        if not self.enabled:
            self._reason = "memory_backend_legacy"
            return None
        if not self.available:
            return None
        try:
            return self._get_system(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        except Exception as exc:
            self._reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s unavailable: %s", operation, self._reason)
            return None

    def _get_system(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> Any:
        if self._memcore_module is None or self._llm_client is None or self._embedding is None:
            raise RuntimeError(self._reason or "memcore_not_bootstrapped")
        if self._store is None or self._index is None or self._memory_config is None:
            raise RuntimeError(self._reason or "memcore_dependencies_not_ready")

        user_id = str(profile_user_id or session_id or "default_user").strip() or "default_user"
        conversation_id = str(session_id or user_id).strip() or user_id
        domain_id = str(character_pack_id or "").strip()
        key = (user_id, conversation_id, domain_id)
        with self._lock:
            existing = self._systems.get(key)
            if existing is not None:
                return existing
            namespace = self._memcore_module.Namespace(
                user_id=user_id,
                tenant_id="",
                domain_id=domain_id,
                conversation_id=conversation_id,
            )
            system = self._memcore_module.MemorySystem(
                llm=self._llm_client,
                namespace=namespace,
                timezone="Asia/Shanghai",
                storage_dir=str(self.storage_path),
                config=self._memory_config,
                store=self._store,
                index=self._index,
                embedding=self._embedding,
            )
            self._systems[key] = system
            return system

    @staticmethod
    def _status(
        operation: str,
        ok: bool,
        status: str,
        *,
        source_id: str = "",
        reason: str = "",
        index_status: str = "",
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "ok": bool(ok),
            "status": status,
            "source_id": str(source_id or ""),
            "index_status": str(index_status or ""),
            "reason": str(reason or ""),
        }

    @staticmethod
    def _coerce_optional_unit_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, number))

    @staticmethod
    def _log_compaction_result(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.warning("memcore background compaction failed: %s", str(exc) or exc.__class__.__name__)
