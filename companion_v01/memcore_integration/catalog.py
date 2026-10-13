"""Memcore catalog operations."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from ..owner_private_session import memory_scope
from .contracts import _CAPABILITY_CATALOG_SOURCE_PREFIX, _PROMPT_CONTEXT_KIND_PREFIX

logger = logging.getLogger("akane.memcore")


class MemcoreCatalogMixin:
    @memory_scope
    def resolve_tool_exposure_snapshot(
        self, *, snapshot: dict[str, Any], scope: str, compaction_generation: int,
        profile_user_id: str, session_id: str, character_pack_id: str = "",
        create: bool = True,
    ) -> dict[str, Any]:
        """Persist published declarations in the existing MemCore namespace.

        Context provenance is not an execution registry: no handlers, grants
        or private configuration. A stale generation never creates a new era.
        """
        operation = "tool_exposure_snapshot"
        if self.backend != "memcore":
            return self._status(operation, False, "unsupported", reason="memcore_authority_required")
        system = self._get_system_or_none(operation=operation, profile_user_id=profile_user_id,
            session_id=session_id, character_pack_id=character_pack_id)
        if system is None or self._store is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        generation = int(compaction_generation)
        source_id = f"prompt-context:tool-exposure:{scope}:{generation}"
        try:
            with self._lock:
                actual, _ = self._store.get_conversation_generations(namespace=system.namespace)
                if actual != generation:
                    return self._status(operation, False, "stale", reason="compaction_generation_changed")
                entry = self._store.get_entry(namespace=system.namespace, source_id=source_id)
                created = entry is None
                if created and not create:
                    return {**self._status(operation, True, "awaiting_baseline"),
                            "snapshot": None, "compaction_generation": generation}
                if created:
                    memcore = self._memcore_module
                    entry = system.append_standalone_entry(memcore.TimelineEntryInput(
                        source_id=source_id, kind=f"{_PROMPT_CONTEXT_KIND_PREFIX}.tool_exposure",
                        origin=memcore.EntryOrigin.ENVIRONMENT, turn_role=None,
                        semantic_text="Published tool declarations", payload={"snapshot": snapshot},
                        timestamp=int(time.time()), memory_metadata={},
                        annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                        retrieval_policy=memcore.RetrievalPolicy.NEVER,
                        retrieval_visibility=memcore.RetrievalVisibility.NEVER,
                        semanticize=False, prompt_visible=False,
                        trust=memcore.EntryTrust.TRUSTED_INSTRUCTION,
                        compatibility_role=f"{_PROMPT_CONTEXT_KIND_PREFIX}.tool_exposure",
                    ))
                actual, _ = self._store.get_conversation_generations(namespace=system.namespace)
                if actual != generation:
                    return self._status(operation, False, "stale", reason="compaction_generation_changed")
                value = dict(entry.payload.get("snapshot") or {})
                return {**self._status(operation, True, "created" if created else "restored"),
                        "snapshot": value, "compaction_generation": generation}
        except Exception:
            logger.warning("memcore tool exposure snapshot failed")
            return self._status(operation, False, "failed", reason="tool_exposure_snapshot_failed")

    @memory_scope
    def reconcile_capability_catalog(
        self,
        *,
        content: str,
        visible_source_ids: list[str] | tuple[str, ...],
        compaction_generation: int,
        anchor_source_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        timestamp: int = 0,
        capability_state: dict[str, Any] | None = None,
        client_scope: str = "",
    ) -> dict[str, Any]:
        """Keep one catalog base per compaction generation plus change updates.

        Catalog entries are prompt-visible operational material, never memory
        retrieval candidates. The presentation layer preserves every published
        same-generation update in order; ordinary tool rounds do not repeat them.
        """

        operation = "reconcile_capability_catalog"
        text = str(content or "").strip()
        if not text:
            return self._status(operation, False, "invalid_request", reason="catalog_content_required")
        digest = hashlib.sha256(json.dumps([text, capability_state or {}], ensure_ascii=False,
                                           sort_keys=True).encode("utf-8")).hexdigest()
        generation = max(0, int(compaction_generation or 0))
        visible_catalogs: list[tuple[str, int, str, str]] = []
        for raw_source_id in list(visible_source_ids or []):
            source_id = str(raw_source_id or "").strip()
            if not source_id.startswith(_CAPABILITY_CATALOG_SOURCE_PREFIX):
                continue
            parts = source_id.split(":")
            if len(parts) < 7:
                continue
            source_client = parts[7] if len(parts) > 7 else "qq_text"
            if client_scope and source_client != client_scope:
                continue
            try:
                source_generation = max(0, int(parts[2]))
            except (TypeError, ValueError):
                continue
            role = str(parts[3] or "").strip()
            source_digest = str(parts[4] or "").strip()
            if role not in {"base", "update"} or not source_digest:
                continue
            visible_catalogs.append((source_id, source_generation, role, source_digest))

        current_generation = [item for item in visible_catalogs if item[1] == generation]
        bases = [item for item in current_generation if item[2] == "base"]
        updates = [item for item in current_generation if item[2] == "update"]
        # A standalone base may sort after an open turn in MemCore's raw
        # grouping. It is the logical prefix, never newer than its updates.
        latest = updates[-1] if updates else (bases[-1] if bases else None)
        if bases and latest is not None and latest[3] == digest[:24]:
            return {
                **self._status(operation, True, "unchanged"),
                "changed": False,
                "catalog_hash": digest,
                "catalog_role": latest[2],
                "source_id": latest[0],
                "compaction_generation": generation,
            }

        catalog_role = "base" if not bases else "update"
        latest_source_id = latest[0] if latest is not None else ""

        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)

        scope_material = "\x00".join(
            (
                str(profile_user_id or ""),
                str(session_id or ""),
                str(character_pack_id or ""),
            )
        )
        scope_hash = hashlib.sha256(scope_material.encode("utf-8", errors="ignore")).hexdigest()[:16]
        anchor_material = "\x00".join(
            (
                str(anchor_source_id or "").strip() or f"projection:{int(timestamp or time.time())}",
                latest_source_id or "initial",
            )
        )
        anchor_hash = hashlib.sha256(anchor_material.encode("utf-8", errors="ignore")).hexdigest()[:16]
        source_id = (
            f"{_CAPABILITY_CATALOG_SOURCE_PREFIX}{generation}:{catalog_role}:"
            f"{digest[:24]}:{scope_hash}:{anchor_hash}"
        )
        if client_scope:
            if client_scope not in {"desktop_pet", "qq_text"}:
                return self._status(operation, False, "invalid_request", reason="catalog_client_scope_invalid")
            source_id += f":{client_scope}"
        if catalog_role == "base":
            rendered_text = f"【能力目录基线】\n{text}"
        else:
            previous_state = {}
            if latest_source_id and self._store is not None:
                previous = self._store.get_entry(namespace=system.namespace, source_id=latest_source_id)
                if previous is not None:
                    previous_state = previous.payload.get("capability_state") or {}
            if capability_state is None:
                # Compatibility for hosts without structured directory metadata.
                # The production projection supplies tool and Skill entries.
                rendered_text = f"【能力目录更新】\n{text}"
            else:
                changes = _catalog_changes(previous_state, capability_state)
                if not changes:
                    return {**self._status(operation, True, "unchanged"), "changed": False,
                            "catalog_hash": digest, "catalog_role": latest[2],
                            "source_id": latest_source_id, "compaction_generation": generation}
                rendered_text = "【能力目录更新】\n" + "\n".join(changes)
        try:
            memcore = self._memcore_module or self._import_memcore()
            anchor = self._store.get_entry(namespace=system.namespace, source_id=anchor_source_id) if anchor_source_id else None
            append_to_turn = bool(catalog_role == "update" and anchor is not None and anchor.turn_id)
            entry = memcore.TimelineEntryInput(
                    source_id=source_id,
                    # Runtime context is prompt material, not a user episode.
                    # MemCore therefore compacts it as an operational record
                    # and never lets it become autobiographical memory.
                    kind=f"{_PROMPT_CONTEXT_KIND_PREFIX}.capability_catalog",
                    origin=memcore.EntryOrigin.ENVIRONMENT,
                    turn_role=memcore.TurnRole.INTERMEDIATE if append_to_turn else None,
                    semantic_text=rendered_text,
                    payload={
                        "snapshot_name": "capability_catalog",
                        "catalog_hash": digest,
                        "catalog_role": catalog_role,
                        "compaction_generation": generation,
                        "capability_state": capability_state or {},
                    },
                    timestamp=int(timestamp or time.time()),
                    memory_metadata={},
                    annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                    retrieval_policy=memcore.RetrievalPolicy.NEVER,
                    retrieval_visibility=memcore.RetrievalVisibility.NEVER,
                    semanticize=False,
                    prompt_visible=True,
                    trust=memcore.EntryTrust.TRUSTED_INSTRUCTION,
                    compatibility_role=f"{_PROMPT_CONTEXT_KIND_PREFIX}.capability_catalog",
                )
            # Changes observed inside a tool loop belong to that writable
            # turn, so later results cannot be regrouped ahead of the notice.
            if append_to_turn:
                stored = system.append_entry(entry, turn_id=anchor.turn_id)
            else:
                stored = system.append_standalone_entry(entry)
            return {
                **self._status(operation, True, "recorded", source_id=stored.source_id),
                "changed": True,
                "catalog_hash": digest,
                "catalog_role": catalog_role,
                "compaction_generation": generation,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)


def _catalog_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Only public directory facts; connection health and prompt-block digests are separate."""
    changes = []
    before, after = previous.get("tools", {}), current.get("tools", {})
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if new is None:
            changes.append(f"- {name}：已退出当前可调用目录。")
            continue
        description = str(new.get("description") or "").strip()
        old_description = str((old or {}).get("description") or "").strip()
        if old is None:
            changes.append(f"- {name}：新增；capability_load 后通过 capability_invoke 调用。" +
                           (f"用途：{description}" if description else ""))
        elif old.get("fingerprint") != new.get("fingerprint"):
            changes.append(f"- {name}：契约已更新；重新 capability_load 后通过 capability_invoke 调用。" +
                           (f"用途：{description}" if description and description != old_description else ""))
        elif old.get("mode") != new.get("mode"):
            changes.append(f"- {name}：暴露方式改为 {new['mode']}，以本轮原生声明为准。")
        elif "description" in old and old_description != description:
            changes.append(f"- {name}：用途更新：{description}")
    # Absence means an old host did not supply Skill metadata, not a removal.
    if "skills" in current:
        before, after = previous.get("skills", {}), current["skills"]
        for name in sorted(set(before) | set(after)):
            if name not in after:
                changes.append(f"- Skill {name}：当前作用域不再可加载。")
            elif before.get(name) != after[name]:
                action = "新增" if name not in before else "已更新"
                description = str(after[name].get("description") or "").strip()
                changes.append(f"- Skill {name}：{action}；用 load_skill(name=\"{name}\") 读取手册。" +
                               (f"用途：{description}" if description and description != before.get(name, {}).get("description") else ""))
    return changes
