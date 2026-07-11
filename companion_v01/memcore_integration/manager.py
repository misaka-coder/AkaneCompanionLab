"""Memcore runtime manager for Akane.

memcore is the primary dialogue memory backend. ``legacy`` and ``dual`` modes
remain as explicit compatibility / migration tools while old Akane memory code
is being retired.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable

import config

from .adapters import build_akane_embedding_provider, build_akane_llm_client
from .diagnostics import snippet_hashes


SUPPORTED_MEMORY_BACKENDS = frozenset({"legacy", "dual", "memcore"})
logger = logging.getLogger("akane.memcore")


def normalize_memory_backend(value: Any) -> str:
    text = str(value or "memcore").strip().lower()
    return text if text in SUPPORTED_MEMORY_BACKENDS else "memcore"


def normalize_visible_scope(value: Any) -> str:
    text = str(value or "user").strip().lower()
    return text if text in {"conversation", "user"} else "user"


def _build_persona_text_provider(engine: Any) -> Any:
    """Build a persona_text provider that resolves character identity for memcore compaction prompts.

    Returns a callable (profile_user_id, character_pack_id) -> str that extracts the
    character's system_context persona text. Falls back to PERSONA.final_system_prompt
    when character-pack-specific context is unavailable or fails.
    """
    try:
        from ..persona_config import PERSONA
    except Exception:
        PERSONA = None  # type: ignore[assignment]

    default_text = str(getattr(PERSONA, "final_system_prompt", "") or "").strip()

    def _resolve(profile_user_id: str, character_pack_id: str) -> str:
        if not profile_user_id:
            return default_text
        try:
            build_context = getattr(engine, "_build_memory_compaction_persona_context", None)
            if not callable(build_context):
                return default_text
            context = build_context(
                profile_user_id=profile_user_id,
                session_id=profile_user_id,
                character_pack_id=character_pack_id or "",
            )
            system_context = str(context.get("system_context") or "").strip() if isinstance(context, dict) else ""
            return system_context or default_text
        except Exception:
            return default_text

    return _resolve


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
        persona_text_provider: Callable[[str, str], str] | None = None,
    ) -> None:
        self.backend = normalize_memory_backend(backend)
        self.storage_path = Path(storage_path)
        self.visible_scope = normalize_visible_scope(visible_scope)
        self.enable_flavor = bool(enable_flavor)
        self.shadow_compare = bool(shadow_compare)
        self.llm = llm
        self.embedding_provider = embedding_provider
        self._persona_text_provider = persona_text_provider
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
        self._warmed_index_keys: set[tuple[str, str, str]] = set()
        self._lock = threading.RLock()
        if self.enabled:
            self._bootstrap()

    @classmethod
    def from_engine(cls, engine: Any) -> "MemcoreManager":
        raw_path = str(getattr(config, "MEMCORE_STORAGE_PATH", "") or "").strip()
        storage_path = Path(raw_path) if raw_path else Path(engine.base_dir) / "memcore_v01.db"
        return cls(
            backend=getattr(config, "MEMORY_BACKEND", "memcore"),
            storage_path=storage_path,
            visible_scope=getattr(config, "MEMCORE_VISIBLE_SCOPE", "user"),
            enable_flavor=bool(getattr(config, "MEMCORE_ENABLE_FLAVOR", True)),
            shadow_compare=bool(getattr(config, "MEMCORE_SHADOW_COMPARE", False)),
            llm=engine.llm,
            embedding_provider=engine.embedding_provider,
            persona_text_provider=_build_persona_text_provider(engine),
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
        actor_stable_id: str = "",
        actor_display_name: str = "",
    ) -> dict[str, Any]:
        return self._record_turn(
            operation="record_user_turn",
            role="user",
            record=record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            actor_stable_id=actor_stable_id,
            actor_display_name=actor_display_name,
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

    def record_tool_exchange(
        self,
        *,
        tool_name: str,
        result: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        tool_input: Any = None,
        tool_call_id: str = "",
        source: str = "",
        timestamp: int | None = None,
        source_id_prefix: str = "",
        keywords: list[str] | None = None,
        importance: float = 0.2,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        operation = "record_tool_exchange"
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        try:
            written = system.record_tool_exchange(
                tool_name=str(tool_name or "").strip(),
                result=result,
                tool_input=tool_input,
                tool_call_id=str(tool_call_id or "").strip(),
                source=str(source or "").strip(),
                timestamp=timestamp,
                source_id_prefix=str(source_id_prefix or "").strip() or None,
                keywords=[str(item).strip() for item in (keywords or []) if str(item).strip()],
                importance=max(0.0, min(1.0, float(importance))),
                confidence=max(0.0, min(1.0, float(confidence))),
            )
            tool_use = written.get("tool_use") if isinstance(written, dict) else {}
            tool_result = written.get("tool_result") if isinstance(written, dict) else {}
            return {
                **self._status(operation, True, "recorded"),
                "tool_use_source_id": str((tool_use or {}).get("source_id") or ""),
                "tool_result_source_id": str((tool_result or {}).get("source_id") or ""),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore tool exchange dual-write failed: %s", reason)
            return self._status(operation, False, "failed", reason=reason)

    def record_material_reference(
        self,
        *,
        item: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return self._record_material_event(
            operation="record_material_reference",
            event_type="reference",
            item=item,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
        )

    def record_material_cleanup(
        self,
        *,
        item: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        return self._record_material_event(
            operation="record_material_cleanup",
            event_type="cleanup",
            item=item,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
            reason=reason,
            delete_storage=delete_storage,
        )

    def acquaintance_note(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        now_ts: int | None = None,
    ) -> str:
        """memcore 相处时间感提示（认识第N天等），无历史返回空字符串。"""
        system = self._get_system_or_none(
            operation="acquaintance_note",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return ""
        try:
            return str(system.acquaintance_note(now_ts=now_ts) or "")
        except Exception as exc:
            logger.debug("memcore acquaintance_note failed: %s", exc)
            return ""

    def update_turn_metadata(
        self,
        source_id: str,
        memory_metadata: dict[str, Any] | None,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
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
            actor = self._build_actor(actor_stable_id, actor_display_name)
            result = system.update_turn_metadata(
                sid,
                memory_metadata if isinstance(memory_metadata, dict) else {},
                actor=actor,
            )
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

    def compact_due_sync(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="compact_due_sync",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status("compact_due_sync", False, "unavailable", reason=self._reason)
        try:
            stats = dict(system.compact_due_sync())
            return {
                **self._status("compact_due_sync", True, "completed"),
                "stats": stats,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore sync compaction failed: %s", reason)
            return self._status("compact_due_sync", False, "failed", reason=reason)

    def import_legacy_raw_messages(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        operation = "import_legacy_raw_messages"
        if not self.enabled:
            return {
                **self._status(operation, False, "disabled", reason="memory_backend_legacy"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
            }
        if not self.available:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
            }
        iterator = getattr(legacy_store, "iter_messages_for_vector_reindex", None)
        if not callable(iterator):
            return {
                **self._status(operation, False, "invalid_store", reason="iter_messages_for_vector_reindex_required"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
            }

        profile_filter = str(profile_user_id or "").strip()
        character_filter = None if character_pack_id is None else str(character_pack_id or "").strip()
        max_records = self._coerce_positive_int_or_none(limit)
        scanned = upserted = filtered = skipped = failed = 0
        try:
            batches = iterator(batch_size=max(1, int(batch_size or 64)))
            for batch in batches:
                for record in list(batch or []):
                    if max_records is not None and scanned >= max_records:
                        return self._import_result(operation, scanned, upserted, filtered, skipped, failed)
                    if not isinstance(record, dict):
                        skipped += 1
                        continue
                    scanned += 1
                    record_profile = str(record.get("profile_user_id") or "").strip()
                    record_character = str(record.get("character_pack_id") or "").strip()
                    if profile_filter and record_profile != profile_filter:
                        filtered += 1
                        continue
                    if character_filter is not None and record_character != character_filter:
                        filtered += 1
                        continue
                    role = str(record.get("role") or "").strip().lower()
                    if role not in {"user", "assistant"}:
                        skipped += 1
                        continue
                    result = self._record_turn(
                        operation=operation,
                        role=role,
                        record=record,
                        profile_user_id=record_profile,
                        session_id=str(record.get("session_id") or "").strip(),
                        character_pack_id=record_character,
                    )
                    if bool(result.get("ok")):
                        upserted += 1
                    else:
                        failed += 1
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore legacy raw import failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "scanned": scanned,
                "upserted": upserted,
                "filtered": filtered,
                "skipped": skipped,
                "failed": failed + 1,
            }
        return self._import_result(operation, scanned, upserted, filtered, skipped, failed)

    def import_legacy_long_term_memory(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Import legacy episodic and semantic summaries into memcore.

        Raw backfill alone can leave Akane feeling amnesic until memcore has
        recompressed old history. This maintenance entry carries over the
        legacy long-term layers directly, preserving old distilled memories.
        """

        operation = "import_legacy_long_term_memory"
        summaries = self.import_legacy_summaries(
            legacy_store=legacy_store,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )
        semantic = self.import_legacy_semantic_summaries(
            legacy_store=legacy_store,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )
        ok = bool(summaries.get("ok")) and bool(semantic.get("ok"))
        status = "completed" if ok else "partial"
        reason = "; ".join(
            part
            for part in (
                str(summaries.get("reason") or ""),
                str(semantic.get("reason") or ""),
            )
            if part
        )
        return {
            **self._status(operation, ok, status, reason=reason),
            "summaries": summaries,
            "semantic_summaries": semantic,
            "scanned": int(summaries.get("scanned") or 0) + int(semantic.get("scanned") or 0),
            "upserted": int(summaries.get("upserted") or 0) + int(semantic.get("upserted") or 0),
            "filtered": int(summaries.get("filtered") or 0) + int(semantic.get("filtered") or 0),
            "skipped": int(summaries.get("skipped") or 0) + int(semantic.get("skipped") or 0),
            "failed": int(summaries.get("failed") or 0) + int(semantic.get("failed") or 0),
            "conflicted": int(summaries.get("conflicted") or 0) + int(semantic.get("conflicted") or 0),
        }

    def import_legacy_summaries(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self._import_legacy_layer_records(
            operation="import_legacy_summaries",
            legacy_store=legacy_store,
            iterator_name="iter_summaries_for_vector_reindex",
            entry_type="summary",
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )

    def import_legacy_semantic_summaries(
        self,
        *,
        legacy_store: Any,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self._import_legacy_layer_records(
            operation="import_legacy_semantic_summaries",
            legacy_store=legacy_store,
            iterator_name="iter_semantic_summaries_for_vector_reindex",
            entry_type="semantic_summary",
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )

    def _import_legacy_layer_records(
        self,
        *,
        operation: str,
        legacy_store: Any,
        iterator_name: str,
        entry_type: str,
        profile_user_id: str,
        character_pack_id: str | None,
        batch_size: int,
        limit: int | None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                **self._status(operation, False, "disabled", reason="memory_backend_legacy"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }
        if not self.available:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }
        iterator = getattr(legacy_store, iterator_name, None)
        if not callable(iterator):
            return {
                **self._status(operation, False, "invalid_store", reason=f"{iterator_name}_required"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }
        if self._store is None:
            return {
                **self._status(operation, False, "unavailable", reason="memcore_store_not_ready"),
                "scanned": 0,
                "upserted": 0,
                "filtered": 0,
                "skipped": 0,
                "failed": 0,
                "conflicted": 0,
            }

        profile_filter = str(profile_user_id or "").strip()
        character_filter = None if character_pack_id is None else str(character_pack_id or "").strip()
        max_records = self._coerce_positive_int_or_none(limit)
        scanned = upserted = filtered = skipped = failed = conflicted = 0
        try:
            batches = iterator(batch_size=max(1, int(batch_size or 64)))
            for batch in batches:
                for record in list(batch or []):
                    if max_records is not None and scanned >= max_records:
                        return self._import_layer_result(
                            operation, scanned, upserted, filtered, skipped, failed, conflicted
                        )
                    if not isinstance(record, dict):
                        skipped += 1
                        continue
                    scanned += 1
                    record_profile = str(record.get("profile_user_id") or "").strip()
                    record_character = str(record.get("character_pack_id") or "").strip()
                    if profile_filter and record_profile != profile_filter:
                        filtered += 1
                        continue
                    if character_filter is not None and record_character != character_filter:
                        filtered += 1
                        continue
                    result = self._import_one_legacy_layer_record(
                        operation=operation,
                        entry_type=entry_type,
                        record=record,
                        profile_user_id=record_profile,
                        session_id=str(record.get("session_id") or "").strip(),
                        character_pack_id=record_character,
                    )
                    status = str(result.get("status") or "")
                    if bool(result.get("ok")):
                        if status == "skipped":
                            skipped += 1
                        else:
                            upserted += 1
                    elif status == "conflicted":
                        conflicted += 1
                        failed += 1
                    else:
                        failed += 1
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore legacy layer import failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "scanned": scanned,
                "upserted": upserted,
                "filtered": filtered,
                "skipped": skipped,
                "failed": failed + 1,
                "conflicted": conflicted,
            }
        return self._import_layer_result(operation, scanned, upserted, filtered, skipped, failed, conflicted)

    def _import_one_legacy_layer_record(
        self,
        *,
        operation: str,
        entry_type: str,
        record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._store is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        if entry_type == "semantic_summary":
            record_id = str(record.get("semantic_id") or "").strip()
            text = str(record.get("semantic_summary") or "").strip()
            write = self._store.add_semantic_summary
        else:
            record_id = str(record.get("summary_id") or "").strip()
            text = str(record.get("diary_summary") or "").strip()
            write = self._store.add_summary
        if not record_id:
            return self._status(operation, False, "invalid_record", reason="id_required")
        if not text:
            return self._status(operation, True, "skipped", source_id=record_id, reason="empty_text")

        existing = self._store.get_record_by_source_id(record_id)
        if existing is not None:
            if self._record_belongs_to_namespace(existing, system.namespace):
                return self._status(operation, True, "skipped", source_id=record_id, reason="already_imported")
            return self._status(
                operation,
                False,
                "conflicted",
                source_id=record_id,
                reason="source_id_belongs_to_different_namespace",
            )

        payload = dict(record)
        payload["memory_metadata"] = self._legacy_import_metadata(payload.get("memory_metadata"))
        saved = write(namespace=system.namespace, record=payload)
        if entry_type == "summary" and int(record.get("is_semanticized") or 0):
            semantic_id = str(record.get("semantic_id") or "").strip()
            if semantic_id:
                self._store.mark_summaries_semanticized([record_id], semantic_id)
                saved["is_semanticized"] = 1
                saved["semantic_id"] = semantic_id
        try:
            system._reindex_record(saved)
            index_status = "indexed"
        except Exception as exc:
            index_status = "pending"
            logger.warning(
                "memcore legacy layer index pending for %s: %s",
                record_id,
                str(exc) or exc.__class__.__name__,
            )
        return self._status(operation, True, "recorded", source_id=record_id, index_status=index_status)

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
        from ..domain_profiles import FINANCE_MEMORY_CATEGORIES

        base_categories = tuple(getattr(memcore, "DEFAULT_CATEGORIES", ()))
        categories = tuple(dict.fromkeys((*base_categories, *FINANCE_MEMORY_CATEGORIES)))
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
            categories=categories,
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
        actor_stable_id: str = "",
        actor_display_name: str = "",
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
                actor = self._build_actor(actor_stable_id, actor_display_name)
                written = system.record_user_turn(
                    content,
                    actor=actor,
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

    def _record_material_event(
        self,
        *,
        operation: str,
        event_type: str,
        item: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        timestamp: int | None,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(item, dict):
            return self._status(operation, False, "invalid_record", reason="item_required")
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        profile = str(profile_user_id or item.get("profile_user_id") or "").strip()
        session = str(session_id or item.get("session_id") or "").strip()
        character = str(character_pack_id or detail.get("character_pack_id") or item.get("character_pack_id") or "").strip()
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id:
            return self._status(operation, False, "invalid_record", reason="attachment_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile,
            session_id=session,
            character_pack_id=character,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=attachment_id, reason=self._reason)

        effective_ts = int(timestamp or item.get("updated_at") or item.get("created_at") or time.time())
        handle = str(item.get("attachment_handle") or "").strip()
        file_id = handle or attachment_id
        status_part = self._attachment_file_status(item, event_type=event_type, delete_storage=delete_storage)
        source_id = f"attachment:{attachment_id}:{event_type}:{status_part}:{effective_ts}"
        try:
            if event_type == "cleanup":
                written = system.record_material_cleanup(
                    file_id=file_id,
                    kind=str(item.get("kind") or "file"),
                    filename=self._attachment_filename(item),
                    file_status=status_part,
                    derived_status=self._attachment_derived_status(item),
                    reason=reason,
                    timestamp=effective_ts,
                    source_id=source_id,
                    keywords=self._attachment_keywords(item),
                )
            else:
                actor_stable_id, actor_display_name = self._attachment_actor_identity(item)
                written = system.record_material_reference(
                    file_id=file_id,
                    kind=str(item.get("kind") or "file"),
                    actor=self._build_actor(actor_stable_id, actor_display_name),
                    filename=self._attachment_filename(item),
                    mime_type=str(item.get("mime_type") or ""),
                    file_status=status_part,
                    derived_status=self._attachment_derived_status(item),
                    timestamp=effective_ts,
                    source_id=source_id,
                    keywords=self._attachment_keywords(item),
                )
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.get("source_id") or source_id),
                index_status=str(written.get("index_status") or ""),
            )
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=failed_reason)

    @staticmethod
    def _attachment_filename(item: dict[str, Any]) -> str:
        for key in ("origin_name", "summary_title", "attachment_handle", "attachment_id"):
            text = str(item.get(key) or "").strip()
            if text:
                return text[:160]
        return "attachment"

    def _build_actor(self, stable_id: str, display_name: str = "") -> Any | None:
        actor_id = str(stable_id or "").strip()
        if not actor_id:
            return None
        memcore = self._memcore_module or self._import_memcore()
        return memcore.Actor(
            stable_id=actor_id[:160],
            display_name=str(display_name or "").strip()[:160],
        )

    @staticmethod
    def _attachment_actor_identity(item: dict[str, Any]) -> tuple[str, str]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        raw_id = str(detail.get("qq_sender_id") or "").strip()
        if not raw_id:
            return "", ""
        stable_id = raw_id if raw_id.startswith("qq:") else f"qq:{raw_id}"
        display_name = str(detail.get("qq_sender_label") or "").strip()
        return stable_id, display_name

    @classmethod
    def _attachment_keywords(cls, item: dict[str, Any]) -> list[str]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        candidates = [
            item.get("attachment_handle"),
            item.get("origin_name"),
            item.get("summary_title"),
            item.get("kind"),
            item.get("source"),
            detail.get("qq_sender_label"),
        ]
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text[:80])
            if len(out) >= 4:
                break
        return out

    @staticmethod
    def _attachment_file_status(
        item: dict[str, Any],
        *,
        event_type: str,
        delete_storage: bool,
    ) -> str:
        if event_type == "cleanup":
            return "deleted" if delete_storage else "cleared"
        status = str(item.get("status") or "").strip().lower()
        if status == "pending_observation":
            return "processing"
        if status in {"ready", "failed", "cleared"}:
            return status
        return status or "unknown"

    @staticmethod
    def _attachment_derived_status(item: dict[str, Any]) -> str:
        status = str(item.get("status") or "").strip().lower()
        if status == "pending_observation":
            return "processing"
        if status == "failed":
            return "failed"
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        if str(item.get("summary_title") or "").strip() or str(item.get("short_hint") or "").strip() or detail:
            return "ready"
        if status == "ready":
            return "ready"
        return "unknown"

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
            if existing is None:
                namespace = self._memcore_module.Namespace(
                    user_id=user_id,
                    tenant_id="",
                    domain_id=domain_id,
                    conversation_id=conversation_id,
                )
                persona_text = ""
                if self._persona_text_provider is not None:
                    try:
                        persona_text = self._persona_text_provider(profile_user_id, character_pack_id)
                    except Exception as exc:
                        logger.debug("memcore persona_text_provider failed: %s", exc)
                existing = self._memcore_module.MemorySystem(
                    llm=self._llm_client,
                    namespace=namespace,
                    timezone=str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai",
                    storage_dir=str(self.storage_path),
                    config=self._memory_config,
                    store=self._store,
                    index=self._index,
                    embedding=self._embedding,
                    persona_text=persona_text,
                    prompt_overrides=self._build_prompt_overrides(persona_text),
                )
                self._systems[key] = existing
        self._warm_index_for_system(existing, operation="get_system")
        return existing

    @staticmethod
    def _build_prompt_overrides(persona_text: str) -> Any:
        """Build PromptOverrides with Akane-specific compaction guidance."""
        try:
            from memcore.prompts import PromptOverrides
        except ImportError:
            return None
        extra_semantic = (
            "stable_facts 只保留用户反复确认过的偏好、身份、关系和长期计划；"
            "单次工具操作的执行结果和系统配置字段（finance_mode、reply_mode 等）不应出现在 stable_facts 里。"
        )
        return PromptOverrides(
            persona_text=persona_text,
            extra_semantic_guidance=extra_semantic,
        )

    def _warm_index_for_system(self, system: Any, *, operation: str) -> None:
        namespace = getattr(system, "namespace", None)
        if namespace is None:
            return
        hard_key = tuple(str(item or "") for item in namespace.hard_key())
        with self._lock:
            if hard_key in self._warmed_index_keys:
                return
            self._warmed_index_keys.add(hard_key)
        try:
            raw_limit = getattr(config, "MEMCORE_REINDEX_ON_NAMESPACE_LOAD_LIMIT", None)
            limit = self._coerce_positive_int_or_none(raw_limit)
            system.reindex_all(namespace=namespace, limit=limit, current_conversation_only=False)
        except Exception as exc:
            with self._lock:
                self._warmed_index_keys.discard(hard_key)
            logger.warning("memcore %s index warmup failed: %s", operation, str(exc) or exc.__class__.__name__)

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
    def _coerce_positive_int_or_none(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _record_belongs_to_namespace(record: dict[str, Any], namespace: Any) -> bool:
        return (
            str(record.get("tenant_id") or "") == str(getattr(namespace, "tenant_id", "") or "")
            and str(record.get("user_id") or "") == str(getattr(namespace, "user_id", "") or "")
            and str(record.get("domain_id") or "") == str(getattr(namespace, "domain_id", "") or "")
            and str(record.get("conversation_id") or "") == str(getattr(namespace, "conversation_id", "") or "")
        )

    @staticmethod
    def _legacy_import_metadata(memory_metadata: Any) -> dict[str, Any]:
        metadata = dict(memory_metadata) if isinstance(memory_metadata, dict) else {}
        metadata.setdefault("source_system", "akane_legacy")
        metadata.setdefault("legacy_import", True)
        return metadata

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
            for text in (render_summary_snippet(row, tz=tz, enable_flavor=enable_flavor) for row in episodic)
            if text
        )
        semantic_text = "\n\n".join(
            text
            for text in (render_semantic_snippet(row, tz=tz, enable_flavor=enable_flavor) for row in semantic)
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
        active_dates = sorted(
            {str(item.get("date_label") or "") for item in messages if str(item.get("date_label") or "")}
        )
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

    @classmethod
    def _import_layer_result(
        cls,
        operation: str,
        scanned: int,
        upserted: int,
        filtered: int,
        skipped: int,
        failed: int,
        conflicted: int,
    ) -> dict[str, Any]:
        payload = cls._import_result(operation, scanned, upserted, filtered, skipped, failed)
        payload["conflicted"] = int(conflicted)
        if conflicted and not payload.get("reason"):
            payload["reason"] = "some_records_conflicted"
        return payload

    @classmethod
    def _import_result(
        cls,
        operation: str,
        scanned: int,
        upserted: int,
        filtered: int,
        skipped: int,
        failed: int,
    ) -> dict[str, Any]:
        status = "completed" if failed == 0 else "partial"
        return {
            **cls._status(operation, failed == 0, status, reason="" if failed == 0 else "some_records_failed"),
            "scanned": int(scanned),
            "upserted": int(upserted),
            "filtered": int(filtered),
            "skipped": int(skipped),
            "failed": int(failed),
        }

    @staticmethod
    def _log_compaction_result(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.warning("memcore background compaction failed: %s", str(exc) or exc.__class__.__name__)
