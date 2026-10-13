"""Memcore retrieval operations."""
from __future__ import annotations

import logging
import time
from typing import Any
import config
from .diagnostics import snippet_hashes
from ..owner_private_session import memory_scope
from .contracts import _EXPLICIT_KIND_PATTERN, _EXPLICIT_RETRIEVAL_KIND_ROOTS

logger = logging.getLogger("akane.memcore")


class MemcoreRetrievalMixin:
    @memory_scope
    def read_memory_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        time_range: dict[str, Any] | None = None,
        date_from: str = "",
        date_to: str = "",
        time_periods: list[str] | None = None,
        anchor_source_id: str = "",
        before_turns: int = 0,
        after_turns: int = 0,
        cross_conversation: bool = False,
        projection: str = "conversation",
        page_token_budget: int = 0,
        cursor: str = "",
        arguments: dict[str, Any] | None = None,
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
                "anchor_source_id": str(anchor_source_id or ""),
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "projection": str(projection or "conversation"),
                "coverage": {},
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }

        try:
            native_arguments = dict(arguments) if isinstance(arguments, dict) else {}
            if arguments is None:
                resolved_cursor = str(cursor or "").strip()
                if resolved_cursor:
                    native_arguments["cursor"] = resolved_cursor
                else:
                    if time_range:
                        native_arguments["time_range"] = dict(time_range)
                    if str(date_from or "").strip():
                        native_arguments["date_from"] = str(date_from).strip()
                    if str(date_to or "").strip():
                        native_arguments["date_to"] = str(date_to).strip()
                    if time_periods:
                        native_arguments["time_periods"] = list(time_periods)
                    if str(anchor_source_id or "").strip():
                        native_arguments["anchor_source_id"] = str(anchor_source_id).strip()
                    if int(before_turns or 0):
                        native_arguments["before_turns"] = int(before_turns)
                    if int(after_turns or 0):
                        native_arguments["after_turns"] = int(after_turns)
                    if bool(cross_conversation):
                        native_arguments["cross_conversation"] = True
                    if str(projection or "conversation") != "conversation":
                        native_arguments["projection"] = str(projection)
                    if int(page_token_budget or 0) > 0:
                        native_arguments["page_token_budget"] = int(page_token_budget)
            result = self._memcore_module.dispatch_native_memory_tool(
                "read_timeline",
                native_arguments,
                mem=system,
            )
            return self._project_native_memory_dispatch(
                operation="read_memory_timeline",
                dispatched=result,
            )
        except Exception as exc:
            reason = f"exception_{type(exc).__name__}"
            logger.warning("memcore timeline read failed: %s", type(exc).__name__)
            return {
                **self._status("read_memory_timeline", False, "failed", reason=reason),
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": [],
                "anchor_source_id": str(anchor_source_id or ""),
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "projection": str(projection or "conversation"),
                "coverage": {},
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }

    @memory_scope
    def browse_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="browse_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._memcore_module is None:
            return {
                **self._status("browse_memory", False, "unavailable", reason=self._reason),
                "backend": "memcore",
            }
        try:
            dispatched = self._memcore_module.dispatch_native_memory_tool(
                "browse_memory",
                dict(arguments or {}),
                mem=system,
            )
            return self._project_native_memory_dispatch(
                operation="browse_memory",
                dispatched=dispatched,
            )
        except Exception as exc:
            reason = f"exception_{type(exc).__name__}"
            logger.warning("memcore catalog browse failed: %s", type(exc).__name__)
            return {
                **self._status("browse_memory", False, "failed", reason=reason),
                "backend": "memcore",
            }

    @memory_scope
    def open_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="open_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._memcore_module is None:
            return {
                **self._status("open_memory", False, "unavailable", reason=self._reason),
                "backend": "memcore",
            }
        try:
            dispatched = self._memcore_module.dispatch_native_memory_tool(
                "open_memory",
                dict(arguments or {}),
                mem=system,
            )
            return self._project_native_memory_dispatch(
                operation="open_memory",
                dispatched=dispatched,
            )
        except Exception as exc:
            reason = f"exception_{type(exc).__name__}"
            logger.warning("memcore open-memory read failed: %s", type(exc).__name__)
            return {
                **self._status("open_memory", False, "failed", reason=reason),
                "backend": "memcore",
            }

    @memory_scope
    def maintain_semantic_history(
        self, *, operation: str, profile_user_id: str, session_id: str,
        character_pack_id: str = "", plan: dict[str, Any] | None = None,
        repair_id: str = "", progress_callback: Any = None,
        prepared: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trusted operator maintenance; never exposed as a chat model tool."""
        if operation not in {"plan", "prepare", "commit", "repair", "rollback"}:
            return {"status": "invalid_arguments", "reason": "invalid_maintenance_operation"}
        if not self.enabled or not self.available:
            return {"status": "unavailable", "reason": self._reason or "memcore_unavailable"}
        system = self._get_system(profile_user_id=profile_user_id, session_id=session_id,
                                  character_pack_id=character_pack_id, warm_index=False)
        if operation == "plan":
            return system.plan_semantic_history_repair()
        if operation in {"repair", "prepare"}:
            if not isinstance(plan, dict):
                return {"status": "invalid_arguments", "reason": "repair_plan_required"}
            method = system.repair_semantic_history if operation == "repair" else system.prepare_semantic_history_repair
            return method(plan, progress_callback=progress_callback)
        if operation == "commit":
            return system.commit_prepared_semantic_history_repair(prepared)
        if not isinstance(repair_id, str) or not repair_id.strip():
            return {"status": "invalid_arguments", "reason": "repair_id_required"}
        return system.rollback_semantic_history_repair(repair_id)

    @memory_scope
    def shadow_retrieve_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        current_user_record: dict[str, Any] | None = None,
        query: str,
        entity_anchors: list[str] | None = None,
        topic_terms: list[str] | None = None,
        time_hint: dict[str, Any] | None = None,
        source_layers: list[str] | None = None,
        memory_facets: list[str] | None = None,
        about_roles: list[str] | None = None,
        within_memory_id: str = "",
        exclude_source_ids: list[str] | None = None,
        include_explicit: bool = False,
        kind_patterns: list[str] | None = None,
        external_target: bool = False,
    ) -> dict[str, Any]:
        shadow_enabled = bool(getattr(config, "MEMCORE_SHADOW_COMPARE", self.shadow_compare))
        if self.retrieval_mode == "navigation":
            return self._status("shadow_retrieve_memory", True, "disabled", reason="retrieval_disabled")
        if not shadow_enabled:
            return self._status("shadow_retrieve_memory", True, "disabled", reason="shadow_compare_disabled")
        result = self._retrieve_memory(
            operation="shadow_retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            within_memory_id=within_memory_id,
            exclude_source_ids=exclude_source_ids,
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
            include_snippets=False,
            external_target=external_target,
        )
        if "snippets" in result:
            result = dict(result)
            result.pop("snippets", None)
        return result

    @memory_scope
    def retrieve_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        current_user_record: dict[str, Any] | None = None,
        query: str,
        entity_anchors: list[str] | None = None,
        topic_terms: list[str] | None = None,
        time_hint: dict[str, Any] | None = None,
        source_layers: list[str] | None = None,
        memory_facets: list[str] | None = None,
        about_roles: list[str] | None = None,
        within_memory_id: str = "",
        exclude_source_ids: list[str] | None = None,
        include_explicit: bool = False,
        kind_patterns: list[str] | None = None,
        external_target: bool = False,
    ) -> dict[str, Any]:
        return self._retrieve_memory(
            operation="retrieve_memory",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            within_memory_id=within_memory_id,
            exclude_source_ids=exclude_source_ids,
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
            include_snippets=True,
            external_target=external_target,
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
        entity_anchors: list[str] | None,
        topic_terms: list[str] | None,
        time_hint: dict[str, Any] | None,
        source_layers: list[str] | None,
        memory_facets: list[str] | None,
        about_roles: list[str] | None,
        within_memory_id: str,
        exclude_source_ids: list[str] | None,
        include_explicit: bool,
        kind_patterns: list[str] | None,
        include_snippets: bool,
        external_target: bool = False,
    ) -> dict[str, Any]:
        if self.retrieval_mode == "navigation":
            return {
                **self._status(operation, False, "unavailable", reason="retrieval_disabled"),
                "snippet_count": 0,
                "snippet_hashes": [],
                **({"snippets": []} if include_snippets else {}),
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
        explicit_patterns, explicit_error = self._authorize_explicit_kind_patterns(
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
        )
        if explicit_error:
            status, reason = explicit_error
            return {
                **self._status(operation, False, status, reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                **({"snippets": []} if include_snippets else {}),
            }
        try:
            filters = dict(
                entity_anchors=[str(item).strip() for item in (entity_anchors or []) if str(item).strip()],
                topic_terms=[str(item).strip() for item in (topic_terms or []) if str(item).strip()],
                time_hint=time_hint if isinstance(time_hint, dict) else None,
                source_layers=[str(item).strip() for item in (source_layers or []) if str(item).strip()],
                memory_facets=[str(item).strip() for item in (memory_facets or []) if str(item).strip()],
                about_roles=[str(item).strip() for item in (about_roles or []) if str(item).strip()],
                within_memory_id=str(within_memory_id or "").strip(),
                exclude_source_ids=[str(item).strip() for item in (exclude_source_ids or []) if str(item).strip()],
                include_explicit=bool(include_explicit),
                kind_patterns=explicit_patterns,
                cross_conversation=False,
            )
            if external_target:
                # The target's prompt-visible window was not shown to the
                # initiating turn, so excluding it would hide recent evidence.
                retrieval = system.retrieve_structured(str(query or ""), **filters)
            else:
                retrieval = system.retrieve_for_turn_structured(
                    current=current, query=str(query or ""), **filters,
                )
            retrieval_status = str(getattr(retrieval, "status", "failed") or "failed")
            if retrieval_status not in {"found", "empty"}:
                return {
                    **self._status(
                        operation,
                        False,
                        retrieval_status,
                        reason=str(getattr(retrieval, "reason", "") or "retrieval_failed"),
                    ),
                    "snippet_count": 0,
                    "snippet_hashes": [],
                    **({"snippets": []} if include_snippets else {}),
                }
            snippets = list(getattr(retrieval, "rendered_texts", ()) or ())
            structured = retrieval.to_dict() if hasattr(retrieval, "to_dict") else {}
            payload = {
                **self._status(operation, True, "ok"),
                "retrieval_status": retrieval_status,
                "snippet_count": len(snippets),
                "snippet_hashes": snippet_hashes(snippets),
                "navigation": list(structured.get("navigation") or []),
                "suggested_next_actions": list(structured.get("suggested_next_actions") or []),
                "effective_filters": dict(structured.get("effective_filters") or {}),
                "candidate_counts": dict(structured.get("candidate_counts") or {}),
                "lineage_scope": dict(structured.get("lineage_scope") or {}),
                "entity_filter_relaxed": bool(structured.get("entity_filter_relaxed")),
                "relaxation_steps": list(structured.get("relaxation_steps") or []),
                "rejected_counts": dict(structured.get("rejected_counts") or {}),
                "returned_match_count": len(list(structured.get("matches") or [])),
                "token_usage": int(structured.get("token_usage") or 0),
                "truncated": bool(structured.get("truncated")),
                "omitted_match_count": int(structured.get("omitted_match_count") or 0),
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
            }
            if include_snippets:
                payload["snippets"] = snippets
                payload["matches"] = list(structured.get("matches") or [])
                if self._memcore_module is not None:
                    receipt_arguments = {
                        "query": str(query or ""),
                        "entity_anchors": [str(item).strip() for item in (entity_anchors or []) if str(item).strip()],
                        "topic_terms": [str(item).strip() for item in (topic_terms or []) if str(item).strip()],
                        "source_layers": [str(item).strip() for item in (source_layers or []) if str(item).strip()],
                        "memory_facets": [str(item).strip() for item in (memory_facets or []) if str(item).strip()],
                        "about_roles": [str(item).strip() for item in (about_roles or []) if str(item).strip()],
                        "time_hint": dict(time_hint or {}) if isinstance(time_hint, dict) else {},
                        "within_memory_id": str(within_memory_id or "").strip(),
                        "include_explicit": bool(include_explicit),
                        "kind_patterns": list(explicit_patterns),
                    }
                    payload["receipt"] = self._memcore_module.build_memory_operation_receipt(
                        "retrieve_for_turn",
                        receipt_arguments,
                        {
                            "ok": True,
                            "status": "ok",
                            "result": {**structured, "snippets": snippets},
                        },
                    )
            return payload
        except ValueError as exc:
            reason = str(exc) or "invalid_filter"
            return {
                **self._status(operation, False, "invalid_filter", reason=reason),
                "snippet_count": 0,
                "snippet_hashes": [],
                "latency_ms": max(0, int((time.perf_counter() - start) * 1000)),
                **({"snippets": []} if include_snippets else {}),
            }
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

    @staticmethod
    def _authorize_explicit_kind_patterns(
        *,
        include_explicit: bool,
        kind_patterns: list[str] | None,
    ) -> tuple[list[str], tuple[str, str] | None]:
        patterns = [str(item or "").strip().lower() for item in list(kind_patterns or []) if str(item or "").strip()]
        if not include_explicit:
            if patterns:
                return [], ("invalid_request", "kind_patterns_require_include_explicit")
            return [], None
        if not patterns:
            return [], ("invalid_request", "explicit_kind_patterns_required")
        normalized: list[str] = []
        for pattern in patterns:
            if not _EXPLICIT_KIND_PATTERN.fullmatch(pattern):
                return [], ("invalid_request", "invalid_kind_pattern")
            root = pattern.split(".", 1)[0]
            if root not in _EXPLICIT_RETRIEVAL_KIND_ROOTS:
                return [], ("forbidden", "kind_pattern_not_authorized")
            if pattern not in normalized:
                normalized.append(pattern)
        return normalized, None

    @memory_scope
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
