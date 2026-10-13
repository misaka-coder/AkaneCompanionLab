"""Memcore turns operations."""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any
from ..assistant_history_projection import ASSISTANT_HISTORY_PROJECTION_VERSION, project_assistant_history_payload
from ..owner_private_session import memory_scope
from .contracts import _STALE_OPEN_TURN_MAX_AGE_SECONDS, resolve_memcore_provider_profile

logger = logging.getLogger("akane.memcore")


class MemcoreTurnWritesMixin:
    @memory_scope
    def append_standalone_message(
        self,
        record: dict[str, Any],
        *,
        role: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
        observed: bool = False,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        """Append a message that intentionally has no model-response turn."""

        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"user", "assistant"}:
            return self._status(
                "append_standalone_message",
                False,
                "invalid_record",
                source_id=str((record or {}).get("source_id") or ""),
                reason="role_must_be_user_or_assistant",
            )
        return self._append_standalone_turn(
            operation="append_standalone_message",
            role=normalized_role,
            record=record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            actor_stable_id=actor_stable_id,
            actor_display_name=actor_display_name,
            observed=observed,
            target_actor_id=target_actor_id,
            target_actor_display_name=target_actor_display_name,
        )

    @memory_scope
    def append_standalone_event(
        self,
        event: dict[str, Any],
        *,
        source_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        timestamp: int = 0,
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Append an external event, optionally into an already-open host turn."""

        operation = "append_standalone_event"
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            return self._status(operation, False, "invalid_event", reason="source_id_required")
        if not isinstance(event, dict) or not str(event.get("event_type") or "").strip():
            return self._status(
                operation,
                False,
                "invalid_event",
                source_id=normalized_source_id,
                reason="event_type_required",
            )
        fields = event.get("fields")
        if not isinstance(fields, dict):
            return self._status(
                operation,
                False,
                "invalid_event",
                source_id=normalized_source_id,
                reason="event_fields_must_be_object",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(
                operation,
                False,
                "unavailable",
                source_id=normalized_source_id,
                reason=self._reason,
            )
        effective_timestamp = int(timestamp or time.time())
        try:
            entry = self._build_timeline_input(
                role="event",
                record={
                    "source_id": normalized_source_id,
                    "timestamp": effective_timestamp,
                    "memory_metadata": {},
                    "index_in_vector": True,
                },
                turn_role="intermediate" if turn_id else None,
                external_event={
                    "event_type": str(event.get("event_type") or "").strip(),
                    "source": str(event.get("source") or "").strip(),
                    "data": {str(key): str(value) for key, value in fields.items()},
                },
            )
            written = (system.append_entry(entry, turn_id=turn_id)
                       if turn_id else system.append_standalone_entry(entry))
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.source_id or normalized_source_id),
                index_status=str(written.index_status or ""),
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(
                operation,
                False,
                "failed",
                source_id=normalized_source_id,
                reason=reason,
            )

    @memory_scope
    def begin_input_turn(
        self,
        record: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
        target_actor_id: str = "",
        target_actor_display_name: str = "",
        external_event: dict[str, Any] | None = None,
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Open one V2 model-response turn around a user or external stimulus."""

        operation = "begin_input_turn"
        source_id = str((record or {}).get("source_id") or "").strip()
        if not source_id:
            return self._status(operation, False, "invalid_record", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            self._ensure_history_projection_notice(system)
            opened_at = int(time.time())
            recover_stale = getattr(system, "recover_stale_open_turns", None)
            if callable(recover_stale):
                recovered = tuple(
                    recover_stale(
                        max_age_seconds=_STALE_OPEN_TURN_MAX_AGE_SECONDS,
                        now=opened_at,
                        reason="host_stale_open_turn_recovery",
                    )
                    or ()
                )
                if recovered:
                    logger.warning(
                        "memcore stale open turn recovery status=recovered count=%s",
                        len(recovered),
                    )
            entry = self._build_timeline_input(
                role="user",
                record=record,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
                turn_role="stimulus",
                external_event=external_event,
            )
            resolved_turn_id = str(turn_id or "").strip() or self._stable_turn_id(source_id)
            handle = system.begin_turn(
                stimuli=[entry],
                annotation_target_ids=[source_id],
                turn_id=resolved_turn_id,
                # Turn lifecycle time is host processing time, not the
                # stimulus publication time. External finance/news events may
                # arrive late and must not be born stale.
                opened_at=opened_at,
            )
            stored = handle.stimuli[0]
            writable = str(handle.status) == "open"
            return {
                **self._status(
                    operation,
                    True,
                    "opened" if writable else str(handle.status),
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": str(handle.turn_id),
                "writable": writable,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    @memory_scope
    def begin_existing_input_turn(
        self,
        *,
        source_id: str,
        stimulus_source_ids: list[str] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Open a response turn around an already persisted standalone input."""

        operation = "begin_existing_input_turn"
        resolved_source_id = str(source_id or "").strip()
        if not resolved_source_id:
            return self._status(operation, False, "invalid_record", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(
                operation,
                False,
                "unavailable",
                source_id=resolved_source_id,
                reason=self._reason,
            )
        try:
            opened_at = int(time.time())
            recover_stale = getattr(system, "recover_stale_open_turns", None)
            if callable(recover_stale):
                recover_stale(
                    max_age_seconds=_STALE_OPEN_TURN_MAX_AGE_SECONDS,
                    now=opened_at,
                    reason="host_stale_open_turn_recovery",
                )
            begin_existing = getattr(system, "begin_turn_from_existing_sources", None)
            if not callable(begin_existing):
                return self._status(
                    operation,
                    False,
                    "unsupported",
                    source_id=resolved_source_id,
                    reason="existing_stimulus_turn_unsupported",
                )
            self._ensure_history_projection_notice(system)
            linked_source_ids = list(
                dict.fromkeys(
                    str(item or "").strip()
                    for item in [*list(stimulus_source_ids or []), resolved_source_id]
                    if str(item or "").strip()
                )
            )
            handle = begin_existing(
                stimulus_source_ids=linked_source_ids,
                annotation_target_ids=[resolved_source_id],
                turn_id=str(turn_id or "").strip() or self._stable_turn_id(resolved_source_id),
                opened_at=opened_at,
            )
            writable = str(handle.status) == "open"
            return {
                **self._status(
                    operation,
                    True,
                    "opened" if writable else str(handle.status),
                    source_id=resolved_source_id,
                    index_status=handle.stimuli[0].index_status,
                ),
                "turn_id": str(handle.turn_id),
                "writable": writable,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(
                operation,
                False,
                "failed",
                source_id=resolved_source_id,
                reason=reason,
            )

    @memory_scope
    def begin_hidden_host_turn(
        self,
        *,
        referenced_source_ids: list[str] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Open a writable turn without rewriting already persisted stimuli.

        Delayed host decisions such as ambient group participation may add an
        ephemeral instruction to the current provider request.  That request
        tail is not a new conversation fact, but any tools and final assistant
        message still need a real turn owner.  A prompt-invisible host stimulus
        provides that owner while the referenced passive messages remain
        immutable standalone timeline entries.
        """

        operation = "begin_hidden_host_turn"
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        try:
            self._ensure_history_projection_notice(system)
            memcore = self._memcore_module or self._import_memcore()
            opened_at = int(time.time())
            source_id = f"host-turn:{hashlib.sha256(f'{session_id}|{time.time_ns()}'.encode()).hexdigest()[:32]}"
            resolved_references = list(
                dict.fromkeys(
                    str(item or "").strip()
                    for item in list(referenced_source_ids or [])
                    if str(item or "").strip()
                )
            )
            entry = memcore.TimelineEntryInput(
                kind="event.host_turn",
                origin=memcore.EntryOrigin.ENVIRONMENT,
                turn_role=memcore.TurnRole.STIMULUS,
                semantic_text="",
                timestamp=opened_at,
                payload={"referenced_source_ids": resolved_references},
                source_id=source_id,
                retrieval_policy=memcore.RetrievalPolicy.NEVER,
                retrieval_visibility=memcore.RetrievalVisibility.NEVER,
                semanticize=False,
                prompt_visible=False,
            )
            handle = system.begin_turn(
                stimuli=[entry],
                annotation_target_ids=[source_id],
                turn_id=str(turn_id or "").strip() or self._stable_turn_id(source_id),
                opened_at=opened_at,
            )
            writable = str(handle.status) == "open"
            return {
                **self._status(
                    operation,
                    True,
                    "opened" if writable else str(handle.status),
                    source_id=source_id,
                    index_status=handle.stimuli[0].index_status,
                ),
                "turn_id": str(handle.turn_id),
                "writable": writable,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", reason=reason)

    @memory_scope
    def append_turn_intermediate(
        self,
        record: dict[str, Any],
        *,
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "append_turn_intermediate"
        source_id = str((record or {}).get("source_id") or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        if not source_id or not resolved_turn_id:
            return self._status(
                operation,
                False,
                "invalid_record",
                source_id=source_id,
                reason="source_id_and_turn_id_required",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            entry = self._build_timeline_input(
                role="assistant",
                record=record,
                turn_role="intermediate",
            )
            stored = system.append_entry(entry, turn_id=resolved_turn_id)
            return {
                **self._status(
                    operation,
                    True,
                    "recorded",
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    @memory_scope
    def append_turn_user_input(
        self,
        record: dict[str, Any],
        *,
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        actor_stable_id: str = "",
        actor_display_name: str = "",
    ) -> dict[str, Any]:
        """Append a real user steer to an already-open model turn.

        It is an intermediate entry in the turn lifecycle, but remains a
        ``message.user`` event.  Provider projection therefore renders the
        normal timestamped user message instead of a host-authored system note.
        """

        operation = "append_turn_user_input"
        source_id = str((record or {}).get("source_id") or "").strip()
        resolved_turn_id = str(turn_id or "").strip()
        if not source_id or not resolved_turn_id:
            return self._status(
                operation,
                False,
                "invalid_record",
                source_id=source_id,
                reason="source_id_and_turn_id_required",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            entry = self._build_timeline_input(
                role="user",
                record=record,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                turn_role="intermediate",
            )
            stored = system.append_entry(entry, turn_id=resolved_turn_id)
            return {
                **self._status(
                    operation,
                    True,
                    "recorded",
                    source_id=stored.source_id,
                    index_status=stored.index_status,
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    @memory_scope
    def inspect_turn_source(
        self,
        source_id: str,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Inspect one source id without projecting message content.

        Maintenance migrations use this to distinguish an idempotent rerun from
        a cross-namespace collision.  The public result deliberately omits raw
        content, actor identity, paths, and namespace identifiers.
        """

        operation = "inspect_turn_source"
        sid = str(source_id or "").strip()
        if not sid:
            return self._status(operation, False, "invalid_source", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None or self._store is None:
            return self._status(operation, False, "unavailable", source_id=sid, reason=self._reason)
        try:
            record = self._store.get_record_by_source_id(sid)
            if record is None:
                return {
                    **self._status(operation, True, "missing", source_id=sid),
                    "exists": False,
                }
            namespace = getattr(system, "namespace", None)
            if namespace is None or not self._record_belongs_to_namespace(record, namespace):
                return {
                    **self._status(
                        operation,
                        False,
                        "conflict",
                        source_id=sid,
                        reason="source_id_owned_by_other_namespace",
                    ),
                    "exists": True,
                }
            return {
                **self._status(operation, True, "found", source_id=sid),
                "exists": True,
                "role": str(record.get("role") or ""),
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return {
                **self._status(operation, False, "failed", source_id=sid, reason=reason),
                "exists": False,
            }

    @memory_scope
    def record_tool_batch(
        self,
        *,
        exchanges: list[dict[str, Any]],
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        """Append one logical parallel batch as all actions followed by all observations."""

        operation = "record_tool_batch"
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return {**self._status(operation, False, "invalid_record", reason="turn_id_required"), "exchanges": []}
        normalized = [dict(item) for item in exchanges if isinstance(item, dict) and item]
        if not normalized:
            return {**self._status(operation, False, "invalid_record", reason="exchanges_required"), "exchanges": []}
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return {**self._status(operation, False, "unavailable", reason=self._reason), "exchanges": []}
        try:
            # Construct every exchange first: a construction-visible schema or
            # identity problem in any one of them must abort the whole batch
            # before a single append, so no action/observation half-record survives.
            prepared = [self._build_tool_entry_pair(item) for item in normalized]
            self._validate_tool_batch_prepared(prepared, operation=operation)
            stored_actions = [system.append_entry(action, turn_id=resolved_turn_id) for action, _ in prepared]
            stored_observations = [
                system.append_entry(observation, turn_id=resolved_turn_id) for _, observation in prepared
            ]
            recorded = [
                {
                    "correlation_id": action.correlation_id,
                    "tool_use_source_id": action.source_id,
                    "tool_result_source_id": observation.source_id,
                }
                for action, observation in zip(stored_actions, stored_observations)
            ]
            return {
                **self._status(operation, True, "recorded"),
                "turn_id": resolved_turn_id,
                "exchanges": recorded,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore tool batch record failed: %s", reason)
            return {**self._status(operation, False, "failed", reason=reason), "exchanges": []}

    @memory_scope
    def stage_turn_metadata(
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
        operation = "stage_turn_metadata"
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        sid = str(source_id or "").strip()
        if system is None:
            return self._status(operation, False, "unavailable", source_id=sid, reason=self._reason)
        try:
            actor = self._build_actor(actor_stable_id, actor_display_name)
            result = system.stage_turn_metadata(
                sid,
                memory_metadata if isinstance(memory_metadata, dict) else {},
                actor=actor,
            )
            return dict(result, operation=operation)
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore metadata staging failed: %s", reason)
            return self._status(operation, False, "failed", source_id=sid, reason=reason)

    @memory_scope
    def complete_input_turn(
        self,
        *,
        turn_id: str,
        assistant_record: dict[str, Any],
        memory_metadata: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        provider_output_raw: str = "",
        provider_profile: str = "",
        provider_projection: dict[str, Any] | None = None,
        annotation_status: str = "accepted_model",
        append_final: bool = True,
    ) -> dict[str, Any]:
        operation = "complete_input_turn"
        resolved_turn_id = str(turn_id or "").strip()
        source_id = str((assistant_record or {}).get("source_id") or "").strip()
        if not resolved_turn_id or (append_final and not source_id):
            return self._status(
                operation,
                False,
                "invalid_record",
                source_id=source_id,
                reason="turn_id_and_source_id_required",
            )
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)
        try:
            resolved_provider_profile = (
                resolve_memcore_provider_profile(provider_profile)
                if isinstance(provider_projection, dict) or (provider_profile and not append_final)
                else ""
            )
            mirror = getattr(self, "owner_private_group_memory", None)
            if mirror is not None and append_final and str((assistant_record or {}).get("content") or "").strip():
                mirror.prepare_completion(system=system, turn_id=resolved_turn_id, final_id=source_id,
                                          character_id=character_pack_id)
            result = system.complete_turn(
                turn_id=resolved_turn_id,
                semantic_text=str((assistant_record or {}).get("content") or ""),
                provider_output_raw=str(provider_output_raw or ""),
                memory_annotation=memory_metadata if isinstance(memory_metadata, dict) else None,
                annotation_status=str(annotation_status or "missing"),
                timestamp=int((assistant_record or {}).get("timestamp") or time.time()),
                source_id=source_id if append_final else "",
                append_final=append_final,
                payload={
                    "semantic_tags": list((assistant_record or {}).get("semantic_tags") or []),
                },
                provider_profile=resolved_provider_profile,
                provider_projection=(
                    self._memcore_module.ProjectionMessageInput(
                        provider_profile=resolved_provider_profile,
                        payload=project_assistant_history_payload(dict(provider_projection)),
                        source_ids=(source_id,),
                        projection_version=ASSISTANT_HISTORY_PROJECTION_VERSION,
                    )
                    if resolved_provider_profile and isinstance(provider_projection, dict)
                    else None
                ),
            )
            final_entry = getattr(result, "final_entry", None)
            result_status = str(getattr(result, "status", "") or "")
            if mirror is not None and getattr(result, "completed", False):
                mirror.notify_completion()
            return {
                **self._status(
                    operation,
                    bool(getattr(result, "completed", False)) or result_status == "already_completed",
                    result_status or "failed",
                    source_id=str(getattr(final_entry, "source_id", "") or ""),
                    index_status=str(getattr(final_entry, "index_status", "") or ""),
                    reason=str(getattr(result, "reason", "") or ""),
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)

    @memory_scope
    def abort_input_turn(
        self,
        *,
        turn_id: str,
        reason: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        operation = "abort_input_turn"
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return self._status(operation, False, "invalid_record", reason="turn_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", reason=self._reason)
        try:
            result = system.abort_turn(resolved_turn_id, reason=str(reason or "aborted"))
            return {
                **self._status(
                    operation,
                    str(getattr(result, "status", "")) in {"aborted", "already_aborted"},
                    str(getattr(result, "status", "") or "failed"),
                    reason=str(getattr(result, "reason", "") or ""),
                ),
                "turn_id": resolved_turn_id,
            }
        except Exception as exc:
            failed_reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, failed_reason)
            return self._status(operation, False, "failed", reason=failed_reason)

    def _append_standalone_turn(
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
        observed: bool = False,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        source_id = str((record or {}).get("source_id") or "").strip()
        if not source_id:
            return self._status(operation, False, "invalid_record", reason="source_id_required")
        system = self._get_system_or_none(
            operation=operation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if system is None:
            return self._status(operation, False, "unavailable", source_id=source_id, reason=self._reason)

        try:
            entry = self._build_timeline_input(
                role=role,
                record=record,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                observed=observed,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
                turn_role=None,
            )
            written = system.append_standalone_entry(entry)
            return self._status(
                operation,
                True,
                "recorded",
                source_id=str(written.source_id or source_id),
                index_status=str(written.index_status or ""),
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.warning("memcore %s failed: %s", operation, reason)
            return self._status(operation, False, "failed", source_id=source_id, reason=reason)
