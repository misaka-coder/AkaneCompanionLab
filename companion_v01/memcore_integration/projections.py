"""Memcore projections operations."""
from __future__ import annotations

import json
import logging
from typing import Any
import config
from services.tool_history import isolate_tool_history
from ..owner_private_session import memory_scope
from ..assistant_history_projection import (
    ASSISTANT_HISTORY_PROJECTION_VERSION,
    project_assistant_history_payload,
    restore_final_history_content,
)
from .contracts import resolve_memcore_provider_profile
from .request_replay import verified_terminal_projection_replay

logger = logging.getLogger("akane.memcore")


class MemcoreProjectionsMixin:
    def run_assistant_history_projection_migration(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Explicit pre-traffic upgrade of final replies, including settled history.

        Preserve user/tool payloads from the frozen ledger. MemCore atomically
        updates projection hashes/generations and rebuilds affected settlements.
        """
        if not self.available or self._store is None or self._memcore_module is None:
            return {"ok": False, "status": "unavailable", "reason": self._reason or "memcore_not_ready"}
        from memcore.projection import ProjectionAdapter, default_renderer_registry
        from memcore.projection_migration import _settlement_plan_builder

        timezone = str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai")
        adapter = ProjectionAdapter(renderer_registry=default_renderer_registry(), timezone=timezone)
        count_text = getattr(self._token_counter, "count_text", None)
        reports = []
        for tenant, user, domain, conversation in self._store.list_projection_namespaces():
            namespace = self._memcore_module.Namespace(tenant_id=tenant, user_id=user,
                domain_id=domain, conversation_id=conversation)

            def build(profile, entries):
                finals = {entry.source_id: entry for entry in entries
                          if entry.kind == "message.assistant" and str(entry.turn_role) == "final"}
                turn_id = entries[0].turn_id
                messages = self._store.get_turn_projections(namespace=namespace, turn_id=turn_id, provider_profile=profile)
                def final_payload(message):
                    payload = dict(message.payload)
                    if len(message.source_ids) == 1:
                        raw = finals[message.source_ids[0]].payload.get("provider_output_raw")
                        payload = restore_final_history_content(payload, raw)
                    return project_assistant_history_payload(payload)

                return [self._memcore_module.ProjectionMessageInput(
                    provider_profile=profile,
                    payload=(final_payload(message)
                             if message.source_ids and all(source in finals for source in message.source_ids)
                             else dict(message.payload)),
                    source_ids=message.source_ids, projection_index=message.projection_index,
                    projection_status=message.projection_status,
                    projection_version=ASSISTANT_HISTORY_PROJECTION_VERSION,
                ) for message in messages]

            report = self._store.migrate_chat_projections(
                namespace=namespace, target_version=ASSISTANT_HISTORY_PROJECTION_VERSION,
                projection_builder=build, settlement_builder=_settlement_plan_builder(adapter, count_text=count_text),
                chat_kinds=("message.assistant",), dry_run=bool(dry_run))
            reports.append(report)
        failed = any(report.get("status") not in {"ok", "dry_run"}
                     or report.get("preserved_shape_mismatch") or report.get("preserved_reprojection_failed")
                     or report.get("settled_rebuild_failed_dropped") for report in reports)
        totals = {key: sum(int(report.get(key) or 0) for report in reports)
                  for key in ("affected_chat_rows", "migrated", "version_advanced_only", "settled_rebuilt",
                              "settled_rebuilt_noop", "settled_rebuilt_fallback", "settled_rebuild_failed_dropped")}
        return {"ok": not failed, "status": "ok" if not failed else "partial",
                "dry_run": bool(dry_run), "namespace_count": len(reports), "totals": totals}

    @staticmethod
    def _serialize_context_projection(
        projection: Any,
        *,
        projection_messages: list[Any] | None = None,
        has_compact_history: bool | None = None,
    ) -> dict[str, Any]:
        selected = list(projection.messages) if projection_messages is None else list(projection_messages)
        messages = [
            {
                "turn_id": str(message.turn_id or ""),
                "payload": dict(message.payload),
                "source_ids": list(message.source_ids),
                "payload_hash": str(message.payload_hash or ""),
                "projection_status": str(message.projection_status),
                "projection_index": int(message.projection_index),
                "projection_version": int(message.projection_version),
            }
            for message in selected
        ]
        validation = isolate_tool_history([message["payload"] for message in messages])
        history_validation = {}
        stable_prefix_hash = str(projection.stable_prefix_hash or "")
        if validation.issues:
            from memcore import stable_projection_hash

            safe_messages = []
            for payload, index in zip(validation.messages, validation.source_indexes):
                message = dict(messages[index])
                if payload != message["payload"]:
                    message.update(payload=payload, payload_hash=stable_projection_hash(payload),
                                   projection_status="canonical_fallback")
                safe_messages.append(message)
            messages = safe_messages
            stable_prefix_hash = stable_projection_hash(validation.messages)
            history_validation = {"status": "isolated", "issue_count": len(validation.issues),
                                  "issues": validation.issues[:100]}
            logger.warning("memcore tool projection isolated: issues=%s", len(validation.issues))
        source_ids = list(dict.fromkeys(source_id for message in messages for source_id in message["source_ids"]))
        compact = (
            bool(getattr(projection, "has_compact_history", False))
            if has_compact_history is None
            else bool(has_compact_history)
        )
        return {
            "provider_profile": str(projection.provider_profile),
            "messages": messages,
            "payloads": [dict(message["payload"]) for message in messages],
            "source_ids": source_ids,
            "message_count": len(messages),
            "source_count": len(source_ids),
            "stable_prefix_hash": stable_prefix_hash,
            "projection_version": int(projection.projection_version),
            "compaction_generation": int(projection.compaction_generation),
            "projection_generation": int(projection.projection_generation),
            "has_compact_history": compact,
            "has_compact_arguments_history": bool(compact and getattr(projection, "has_compact_arguments_history", False)),
            **({"history_validation": history_validation} if history_validation else {}),
        }

    @memory_scope
    def build_context_projection(
        self,
        *,
        provider_profile: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "build_context_projection"
        profile = resolve_memcore_provider_profile(provider_profile)
        if not profile:
            return {
                **self._status(operation, False, "invalid_provider_profile", reason="provider_profile_unsupported"),
                "provider_profile": "",
                "messages": [],
                "payloads": [],
                "source_ids": [],
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
            }
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "provider_profile": profile,
                "messages": [],
                "payloads": [],
                "source_ids": [],
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
            }
        try:
            projection = system.build_context_projection(provider_profile=profile)
            return {
                **self._status(operation, True, "ok"),
                **self._serialize_context_projection(projection),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore context projection failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "provider_profile": profile,
                "messages": [],
                "payloads": [],
                "source_ids": [],
                "stable_prefix_hash": "",
                "projection_version": 0,
                "compaction_generation": 0,
                "projection_generation": 0,
            }

    @memory_scope
    def build_open_turn_projection(
        self,
        *,
        turn_id: str,
        provider_profile: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "build_open_turn_projection"
        profile = resolve_memcore_provider_profile(provider_profile)
        normalized_turn_id = str(turn_id or "").strip()
        if not profile or not normalized_turn_id:
            return {
                **self._status(
                    operation,
                    False,
                    "invalid",
                    reason="provider_profile_unsupported" if not profile else "turn_id_required",
                ),
                "provider_profile": profile,
                "turn_id": normalized_turn_id,
                "messages": [],
                "payloads": [],
                "source_ids": [],
            }
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "provider_profile": profile,
                "turn_id": normalized_turn_id,
                "messages": [],
                "payloads": [],
                "source_ids": [],
            }
        try:
            focused_builder = getattr(system, "build_open_turn_projection", None)
            if callable(focused_builder):
                projection = focused_builder(
                    turn_id=normalized_turn_id,
                    provider_profile=profile,
                )
                projection_messages = list(projection.messages)
            else:
                projection = system.build_context_projection(provider_profile=profile)
                projection_messages = [
                    message
                    for message in projection.messages
                    if str(message.turn_id or "") == normalized_turn_id
                ]
            return {
                **self._status(operation, True, "ok"),
                "turn_id": normalized_turn_id,
                **self._serialize_context_projection(
                    projection,
                    projection_messages=projection_messages,
                    has_compact_history=False,
                ),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore open turn projection failed: %s", reason)
            return {
                **self._status(operation, False, "failed", reason=reason),
                "provider_profile": profile,
                "turn_id": normalized_turn_id,
                "messages": [],
                "payloads": [],
                "source_ids": [],
            }

    @memory_scope
    def build_context_surface(
        self,
        *,
        provider_profile: str,
        current_source_id: str = "",
        active_turn_messages: list[dict[str, Any]] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Expose the same stable Context Contract used by external hosts."""

        operation = "build_context_surface"
        profile = resolve_memcore_provider_profile(provider_profile)
        if not profile:
            return {
                **self._status(operation, False, "invalid_provider_profile", reason="provider_profile_unsupported"),
                "version": "context_surface_v1",
                "provider_profile": "",
                "history_messages": [],
                "current_message": None,
                "active_turn_messages": [],
                "projection_hash": "",
                "projection_generation": 0,
                "diagnostics": [],
            }
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {
                **self._status(operation, False, "unavailable", reason=self._reason),
                "version": "context_surface_v1",
                "provider_profile": profile,
                "history_messages": [],
                "current_message": None,
                "active_turn_messages": [],
                "projection_hash": "",
                "projection_generation": 0,
                "diagnostics": [],
            }
        try:
            surface_builder = getattr(system, "build_context_surface", None)
            if not callable(surface_builder):
                logger.error("memcore context surface unavailable reason=context_surface_contract_missing")
                return {
                    **self._status(
                        operation,
                        False,
                        "unavailable",
                        reason="context_surface_contract_missing",
                    ),
                    "version": "context_surface_v1",
                    "provider_profile": profile,
                    "history_messages": [],
                    "current_message": None,
                    "active_turn_messages": [],
                    "projection_hash": "",
                    "projection_generation": 0,
                    "diagnostics": [],
                }
            surface = surface_builder(
                session_id=session_id,
                provider_profile=profile,
                current_source_id=str(current_source_id or "").strip() or None,
                active_turn_messages=tuple(active_turn_messages or ()),
            )
            from .context_validation import isolate_context_surface

            result = isolate_context_surface({
                **self._status(operation, True, "ok"),
                **surface.as_dict(),
                "messages": [dict(message) for message in surface.messages],
            })
            if result.get("history_validation"):
                logger.warning("memcore context history isolated: issues=%s",
                               result["history_validation"]["issue_count"])
            return result
        except Exception as exc:
            logger.warning("memcore context surface failed error_type=%s", exc.__class__.__name__)
            return {
                **self._status(operation, False, "failed", reason="context_surface_build_failed"),
                "version": "context_surface_v1",
                "provider_profile": profile,
                "history_messages": [],
                "current_message": None,
                "active_turn_messages": [],
                "projection_hash": "",
                "projection_generation": 0,
                "diagnostics": [],
            }

    def run_legacy_path_projection_migration(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Explicit pre-traffic maintenance: migrate legacy path-damage projections.

        Iterates every namespace with frozen projections in the store and runs
        the idempotent V3 migration (re-projection + settlement rebuild).  This
        must run during a release maintenance phase or before the service takes
        traffic, never on the first user request.  Returns per-namespace reports
        and aggregate counts without any payload text.
        """

        operation = "run_legacy_path_projection_migration"
        if not self.available or self._store is None or self._memcore_module is None:
            return self._status(operation, False, "unavailable", reason=self._reason or "memcore_not_ready")
        try:
            list_namespaces = getattr(self._store, "list_projection_namespaces", None)
            if not callable(list_namespaces):
                return self._status(operation, False, "unsupported", reason="store_namespace_listing_unsupported")
            namespaces = list_namespaces()
        except Exception as exc:
            return self._status(
                operation,
                False,
                "failed",
                reason=f"namespace_listing_failed:{type(exc).__name__}",
            )
        from memcore.projection import ProjectionAdapter, default_renderer_registry
        from memcore.projection_migration import migrate_legacy_path_projections

        timezone = str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        registry = default_renderer_registry()
        token_counter = self._token_counter
        count_text = getattr(token_counter, "count_text", None) if token_counter is not None else None
        totals: dict[str, int] = {}
        reports: list[dict[str, Any]] = []
        for tenant_id, user_id, domain_id, conversation_id in namespaces:
            try:
                namespace = self._memcore_module.Namespace(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    domain_id=domain_id,
                    conversation_id=conversation_id,
                )
                adapter = ProjectionAdapter(renderer_registry=registry, timezone=timezone)
                report = migrate_legacy_path_projections(
                    store=self._store,
                    adapter=adapter,
                    namespace=namespace,
                    dry_run=bool(dry_run),
                    count_text=count_text if callable(count_text) else None,
                )
            except Exception as exc:
                report = {"status": "failed", "reason": f"{type(exc).__name__}"}
            reports.append(
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "domain_id": domain_id,
                    "conversation_id": conversation_id,
                    "report": report,
                }
            )
            for key in (
                "scanned_memcore_marker_rows",
                "scanned_host_local_path_rows",
                "scanned_host_tmpdir_rows",
                "migrated",
                "version_advanced_only",
                "preserved_irrecoverable_host_redaction",
                "preserved_without_raw_source",
                "settled_rebuilt",
                "settled_rebuilt_noop",
                "settled_rebuilt_fallback",
                "settled_rebuild_failed_dropped",
            ):
                if isinstance(report, dict):
                    totals[key] = totals.get(key, 0) + int(report.get(key) or 0)
        failed_namespaces = sum(1 for item in reports if str(item["report"].get("status") or "") == "failed")
        result = {
            **self._status(
                operation,
                failed_namespaces == 0,
                "ok" if failed_namespaces == 0 else "partial",
            ),
            "dry_run": bool(dry_run),
            "namespace_count": len(namespaces),
            "failed_namespace_count": failed_namespaces,
            "totals": totals,
            "reports": reports,
        }
        logger.info(
            "memcore legacy path projection migration done: namespaces=%s failed=%s dry_run=%s totals=%s",
            len(namespaces),
            failed_namespaces,
            bool(dry_run),
            json.dumps(totals, ensure_ascii=False, sort_keys=True),
        )
        return result

    @memory_scope
    def record_request_projection(
        self,
        *,
        turn_id: str,
        provider_profile: str,
        turn_messages: list[dict[str, Any]],
        history_messages: list[dict[str, Any]],
        history_message_indexes: list[int] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        audit_history_messages: list[dict[str, Any]] | None = None,
        attempt: int = 0,
        model_route: Any = "",
        system_prefix: Any = "",
        tool_schema: Any = (),
        created_at: int | None = None,
    ) -> dict[str, Any]:
        operation = "record_request_projection"
        profile = resolve_memcore_provider_profile(provider_profile)
        if not profile:
            return self._status(
                operation,
                False,
                "invalid_provider_profile",
                reason="provider_profile_unsupported",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        try:
            projection_input = self._memcore_module.ProjectionMessageInput
            prepared = [
                projection_input(
                    provider_profile=profile,
                    payload=dict(message.get("payload") or {}),
                    source_ids=tuple(message.get("source_ids") or ()),
                    projection_index=int(message.get("projection_index", -1)),
                    projection_status=message.get("projection_status") or "complete",
                    projection_version=int(message.get("projection_version") or 1),
                )
                for message in list(turn_messages or [])
                if isinstance(message, dict)
            ]
            if len(prepared) != len(turn_messages or []):
                return self._status(operation, False, "invalid_request", reason="turn_messages_must_be_objects")
            actual_history = [dict(message) for message in list(history_messages or [])]
            request_indexes = ([int(index) for index in history_message_indexes]
                               if history_message_indexes is not None else None)

            def terminal_replay_receipt():
                terminal_status = verified_terminal_projection_replay(
                    system=system, turn_id=str(turn_id or "").strip(), provider_profile=profile,
                    turn_messages=prepared, history_messages=actual_history,
                    history_message_indexes=request_indexes,
                )
                if not terminal_status:
                    return None
                return {
                    **self._status(operation, True, "skipped", reason="projection_turn_not_open"),
                    "turn_id": str(turn_id or "").strip(), "provider_profile": profile,
                    "turn_status": terminal_status, "recorded": False,
                }

            replay = terminal_replay_receipt()
            if replay is not None:
                return replay
            try:
                result = system.record_request_projection(
                    turn_id=str(turn_id or "").strip(),
                    provider_profile=profile,
                    turn_messages=prepared,
                    history_messages=actual_history,
                    history_message_indexes=request_indexes,
                    audit_history_messages=(
                        [dict(message) for message in list(audit_history_messages or [])]
                        if audit_history_messages is not None
                        else None
                    ),
                    attempt=int(attempt),
                    model_route=model_route,
                    system_prefix=system_prefix,
                    tool_schema=tool_schema,
                    created_at=created_at,
                )
            except Exception as exc:
                # A stop/completion can win between the read and atomic commit.
                # Recheck only this known conflict, and only accept an exact
                # terminal replay. All genuine immutable conflicts still fail.
                if str(exc) == "projection_immutable_conflict":
                    replay = terminal_replay_receipt()
                    if replay is not None:
                        return replay
                raise
            audit = result.audit
            return {
                **self._status(operation, True, "recorded"),
                "turn_id": str(audit.turn_id),
                "attempt": int(audit.attempt),
                "provider_profile": str(audit.provider_profile),
                "projection_count": len(result.projections),
                "source_ids": list(
                    dict.fromkeys(source_id for item in result.projections for source_id in item.source_ids)
                ),
                "projection_hashes": [str(item.payload_hash or "") for item in result.projections],
                "model_route_hash": str(audit.model_route_hash or ""),
                "system_prefix_hash": str(audit.system_prefix_hash or ""),
                "tool_schema_hash": str(audit.tool_schema_hash or ""),
                "history_hash": str(audit.history_hash or ""),
                "full_prefix_hash": str(audit.full_prefix_hash or ""),
                "projection_version": int(audit.projection_version),
                "media_omitted": bool(audit.media_omitted),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore request projection failed: %s", reason)
            return self._status(operation, False, "failed", reason=reason)

    def bind_request_projection_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        active_turn_id: str,
    ) -> dict[str, Any]:
        """Expose MemCore's generic source-turn binding without host grouping."""

        operation = "bind_request_projection_messages"
        if not self.available or self._memcore_module is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        binder = getattr(self._memcore_module, "bind_request_projection_messages", None)
        if not callable(binder):
            return self._status(operation, False, "unavailable", reason="request_binding_api_unavailable")
        try:
            result = binder(messages, active_turn_id=active_turn_id)
        except Exception as exc:
            logger.warning("memcore %s failed: %s", operation, exc.__class__.__name__)
            return self._status(operation, False, "failed", reason="request_binding_failed")
        if not bool(getattr(result, "ok", False)):
            return self._status(
                operation,
                False,
                str(getattr(result, "status", "rejected") or "rejected"),
                reason=str(getattr(result, "reason", "request_binding_rejected") or "request_binding_rejected"),
            )
        active_group = getattr(result, "active_group", None)
        groups = []
        for group in tuple(getattr(result, "groups", ()) or ()):
            groups.append(
                {
                    "turn_id": str(getattr(group, "turn_id", "") or ""),
                    "relation": str(getattr(group, "relation", "") or ""),
                    "request_indexes": [int(index) for index in tuple(getattr(group, "request_indexes", ()) or ())],
                }
            )
        active_messages = []
        for message in tuple(getattr(active_group, "messages", ()) or ()):
            active_messages.append(
                {
                    "payload": dict(getattr(message, "payload", {}) or {}),
                    "source_ids": list(getattr(message, "source_ids", ()) or ()),
                    "turn_id": str(getattr(message, "source_turn_id", "") or ""),
                    "projection_index": int(getattr(message, "projection_index", -1)),
                    "projection_status": str(getattr(message, "projection_status", "complete") or "complete"),
                    "projection_version": int(getattr(message, "projection_version", 0)),
                    "request_index": int(getattr(message, "request_index", -1)),
                }
            )
        return {
            **self._status(operation, True, "bound"),
            "active_turn_id": str(active_turn_id or "").strip(),
            "active_messages": active_messages,
            "active_request_indexes": [int(index) for index in tuple(active_group.request_indexes or ())],
            "groups": groups,
        }

    @memory_scope
    def compare_context_projection(
        self,
        *,
        provider_profile: str,
        actual_history_messages: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        exclude_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        operation = "compare_context_projection"
        projection = self.build_context_projection(
            provider_profile=provider_profile,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if not projection.get("ok"):
            return {
                **self._status(
                    operation,
                    False,
                    str(projection.get("status") or "failed"),
                    reason="projection_unavailable",
                ),
                "provider_profile": str(projection.get("provider_profile") or ""),
                "projection_hash": "",
                "actual_history_hash": "",
                "strict_prefix": False,
                "first_divergence_index": -1,
                "divergence_reason": "projection_unavailable",
                "source_ids": [],
            }
        try:
            excluded = {
                str(source_id or "").strip()
                for source_id in list(exclude_source_ids or [])
                if str(source_id or "").strip()
            }
            projected_messages = [
                dict(message.get("payload") or {})
                for message in list(projection.get("messages") or [])
                if not excluded.intersection(str(item or "") for item in list(message.get("source_ids") or []))
            ]
            projected_source_ids = list(
                dict.fromkeys(
                    str(source_id or "")
                    for message in list(projection.get("messages") or [])
                    if not excluded.intersection(str(item or "") for item in list(message.get("source_ids") or []))
                    for source_id in list(message.get("source_ids") or [])
                    if str(source_id or "")
                )
            )
            actual = [dict(message) for message in list(actual_history_messages or [])]
            stable_hash = self._memcore_module.stable_projection_hash
            canonical_bytes = self._memcore_module.canonical_json_bytes
            strict_prefix = bool(self._memcore_module.is_strict_message_prefix(projected_messages, actual))
            divergence_index = -1
            divergence_reason = ""
            for index, (expected, observed) in enumerate(zip(projected_messages, actual)):
                if canonical_bytes(expected) != canonical_bytes(observed):
                    divergence_index = index
                    divergence_reason = "message_mismatch"
                    break
            if divergence_index < 0 and len(projected_messages) > len(actual):
                divergence_index = len(actual)
                divergence_reason = "actual_history_missing_projection_suffix"
            elif divergence_index < 0 and len(projected_messages) < len(actual):
                divergence_index = len(projected_messages)
                divergence_reason = "actual_history_has_extra_suffix"
            return {
                **self._status(operation, True, "match" if strict_prefix else "diverged"),
                "provider_profile": str(projection.get("provider_profile") or ""),
                "projection_hash": str(stable_hash(projected_messages)),
                "actual_history_hash": str(stable_hash(actual)),
                "strict_prefix": strict_prefix,
                "first_divergence_index": divergence_index,
                "divergence_reason": divergence_reason,
                "projection_message_count": len(projected_messages),
                "actual_history_message_count": len(actual),
                "source_ids": projected_source_ids,
                "source_count": len(projected_source_ids),
                "compaction_generation": int(projection.get("compaction_generation") or 0),
                "projection_generation": int(projection.get("projection_generation") or 0),
            }
        except Exception as exc:
            logger.warning("memcore projection shadow comparison failed: %s", exc.__class__.__name__)
            return {
                **self._status(operation, False, "failed", reason="comparison_failed"),
                "provider_profile": str(projection.get("provider_profile") or ""),
                "projection_hash": "",
                "actual_history_hash": "",
                "strict_prefix": False,
                "first_divergence_index": -1,
                "divergence_reason": "comparison_failed",
                "source_ids": [],
            }
