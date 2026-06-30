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

    def build_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        current_user_record: dict[str, Any] | None = None,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="build_prompt_context",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status("build_prompt_context", False, "unavailable", reason=self._reason),
                "raw": [],
                "episodic": [],
                "semantic": [],
                "raw_text": "",
                "episodic_text": "",
                "semantic_text": "",
                "rendered_text": "",
            }

        current = dict(current_user_record or {})
        if not int(current.get("timestamp") or 0):
            current["timestamp"] = int(now_ts or time.time())
        try:
            context = system.build_prompt_context(current=current)
            raw = list(context.get("raw") or [])
            episodic = list(context.get("episodic") or [])
            semantic = list(context.get("semantic") or [])
            raw_text, episodic_text, semantic_text = self._render_prompt_context_layers(
                system=system,
                raw=raw,
                episodic=episodic,
                semantic=semantic,
            )
            rendered_text = "\n\n".join(part for part in [raw_text, episodic_text, semantic_text] if part)
            return {
                **self._status("build_prompt_context", True, "ok"),
                "raw": raw,
                "episodic": episodic,
                "semantic": semantic,
                "raw_count": len(raw),
                "episodic_count": len(episodic),
                "semantic_count": len(semantic),
                "raw_text": raw_text,
                "episodic_text": episodic_text,
                "semantic_text": semantic_text,
                "rendered_text": rendered_text,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore prompt context failed: %s", reason)
            return {
                **self._status("build_prompt_context", False, "failed", reason=reason),
                "raw": [],
                "episodic": [],
                "semantic": [],
                "raw_text": "",
                "episodic_text": "",
                "semantic_text": "",
                "rendered_text": "",
            }

    def read_memory_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        date_from: str,
        date_to: str = "",
        time_periods: list[str] | None = None,
        exclude_source_ids: list[str] | None = None,
        cross_conversation: bool = True,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="read_memory_timeline",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status("read_memory_timeline", False, "unavailable", reason=self._reason),
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": [],
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }

        try:
            result = system.read_timeline(
                date_from=str(date_from or ""),
                date_to=str(date_to or ""),
                time_periods=list(time_periods or []),
                cross_conversation=bool(cross_conversation),
            )
            return self._project_timeline_result(
                system=system,
                result=result,
                date_from=date_from,
                date_to=date_to,
                exclude_source_ids=exclude_source_ids,
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore timeline read failed: %s", reason)
            return {
                **self._status("read_memory_timeline", False, "failed", reason=reason),
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": [],
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }

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
        limit: int | None = None,
        exclude_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        shadow_enabled = bool(getattr(config, "MEMCORE_SHADOW_COMPARE", self.shadow_compare))
        if not shadow_enabled:
            return self._status("shadow_retrieve_memory", True, "disabled", reason="shadow_compare_disabled")
        result = self._retrieve_memory(
            operation="shadow_retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            query=query,
            keywords=keywords,
            time_hint=time_hint,
            source_layers=source_layers,
            subject_scopes=subject_scopes,
            categories=categories,
            importance_min=importance_min,
            limit=limit,
            exclude_source_ids=exclude_source_ids,
            include_snippets=False,
        )
        if "snippets" in result:
            result = dict(result)
            result.pop("snippets", None)
        return result

    def retrieve_memory(
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
        limit: int | None = None,
        exclude_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._retrieve_memory(
            operation="retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            query=query,
            keywords=keywords,
            time_hint=time_hint,
            source_layers=source_layers,
            subject_scopes=subject_scopes,
            categories=categories,
            importance_min=importance_min,
            limit=limit,
            exclude_source_ids=exclude_source_ids,
            include_snippets=True,
        )

    def _retrieve_memory(
        self,
        *,
        operation: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        current_user_record: dict[str, Any] | None,
        query: str,
        keywords: list[str] | None,
        time_hint: dict[str, Any] | None,
        source_layers: list[str] | None,
        subject_scopes: list[str] | None,
        categories: list[str] | None,
        importance_min: float | int | str | None,
        limit: int | None,
        exclude_source_ids: list[str] | None,
        include_snippets: bool,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                **({"snippets": []} if include_snippets else {}),
            }

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
            snippets = self._apply_limit(snippets, limit)
            payload = {
                **self._status(operation, True, "ok"),
                "snippet_count": len(snippets),
                "snippet_hashes": snippet_hashes(snippets),
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
            }
            if include_snippets:
                payload["snippets"] = snippets
            return payload
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
                **({"snippets": []} if include_snippets else {}),
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
    def _apply_limit(snippets: list[str], limit: Any) -> list[str]:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            return list(snippets)
        if value <= 0:
            return list(snippets)
        return list(snippets)[:value]

    @staticmethod
    def _render_prompt_context_layers(
        *,
        system: Any,
        raw: list[dict[str, Any]],
        episodic: list[dict[str, Any]],
        semantic: list[dict[str, Any]],
    ) -> tuple[str, str, str]:
        from memcore.rendering import render_semantic_snippet, render_summary_snippet, render_visible_raw

        tz = str(getattr(system, "timezone", "") or "Asia/Shanghai")
        enable_flavor = bool(getattr(getattr(system, "config", None), "enable_flavor", False))
        raw_text = render_visible_raw(raw, tz=tz)
        episodic_text = "\n\n".join(
            text
            for text in (
                render_summary_snippet(row, tz=tz, enable_flavor=enable_flavor) for row in episodic
            )
            if text
        )
        semantic_text = "\n\n".join(
            text
            for text in (
                render_semantic_snippet(row, tz=tz, enable_flavor=enable_flavor) for row in semantic
            )
            if text
        )
        return raw_text, episodic_text, semantic_text

    @staticmethod
    def _project_timeline_result(
        *,
        system: Any,
        result: dict[str, Any],
        date_from: str,
        date_to: str,
        exclude_source_ids: list[str] | None,
    ) -> dict[str, Any]:
        from memcore.rendering import render_timeline

        payload = dict(result if isinstance(result, dict) else {})
        raw_status = str(payload.get("status") or "")
        status = "invalid_range" if raw_status == "invalid_filter" else raw_status
        reason = str(payload.get("reason") or "")
        if raw_status == "invalid_filter" and not reason:
            reason = "invalid_filter"
        messages = list(payload.get("messages") or [])
        excluded = {str(item or "").strip() for item in (exclude_source_ids or []) if str(item or "").strip()}
        if excluded:
            messages = [item for item in messages if str(item.get("source_id") or "").strip() not in excluded]
        if status in {"ok", "empty"}:
            status = "ok" if messages else "empty"
            reason = "" if messages else "no_activity"
        active_dates = sorted({str(item.get("date_label") or "") for item in messages if str(item.get("date_label") or "")})
        text = render_timeline(messages, tz=str(getattr(system, "timezone", "") or "Asia/Shanghai")) if messages else ""
        return {
            "operation": "read_memory_timeline",
            "ok": status not in {"failed", "unavailable"},
            "status": status,
            "reason": reason,
            "date_from": str(payload.get("date_from") or date_from or ""),
            "date_to": str(payload.get("date_to") or date_to or date_from or ""),
            "time_periods": list(payload.get("time_periods") or []),
            "active_dates": active_dates,
            "message_count": len(messages),
            "messages": messages,
            "text": text,
            "backend": "memcore",
        }

    @staticmethod
    def _log_compaction_result(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.warning("memcore background compaction failed: %s", str(exc) or exc.__class__.__name__)
