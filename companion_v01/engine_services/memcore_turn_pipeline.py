"""MemCore turn lifecycle, guard state, steering inputs, and request observer."""
from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar
from typing import Any, Callable, Generator, Mapping

import config
from memcore import memory_metadata_has_signal as memcore_metadata_has_signal

from ..text_utils import extract_semantic_tags

logger = logging.getLogger("akane.engine")

_MEMCORE_OPEN_TURN_GUARD: ContextVar[dict[str, str] | None] = ContextVar(
    "_MEMCORE_OPEN_TURN_GUARD",
    default=None,
)
MEMORY_ANNOTATION_STATUS_FIELD = "_memory_annotation_status"
MEMORY_METADATA_PRESENT_FIELD = "_memory_metadata_present"


class MemcoreTurnPipelineMixin:
    """Mixin providing MemCore turn lifecycle, guard management, and request observation."""

    def _memcore_manager_if_enabled(self):
        manager = getattr(self, "memcore_manager", None)
        if manager is None or not getattr(manager, "enabled", False):
            return None
        return manager

    def _memcore_owns_compaction(self) -> bool:
        backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
        if backend != "memcore":
            return False
        manager = self._memcore_manager_if_enabled()
        return manager is not None and bool(getattr(manager, "available", False))

    def _memcore_owns_legacy_vector_index(self) -> bool:
        backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
        if backend != "memcore":
            return False
        manager = self._memcore_manager_if_enabled()
        return manager is not None and bool(getattr(manager, "available", False))

    def _memcore_owns_visible_memory(self) -> bool:
        return self._memcore_owns_legacy_vector_index()

    def _load_turn_visible_memory(
        self,
        *,
        session_id: str,
        profile_user_id: str,
        character_pack_id: str,
        user_record: dict[str, Any],
        include_transient_user_record: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        return ([dict(user_record)] if user_record else []), [], []

    def _append_memcore_passive_message(
        self,
        *,
        user_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str = "",
        actor_display_name: str = "",
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            return manager.append_standalone_message(
                user_record,
                role="user",
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                observed=True,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
            )
        except Exception as exc:
            logger.warning("memcore passive message append failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _append_memcore_standalone_assistant(
        self,
        *,
        assistant_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        """Persist a visible reply whose triggering group message already exists."""

        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            return manager.append_standalone_message(
                assistant_record,
                role="assistant",
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        except Exception as exc:
            logger.warning("memcore standalone assistant append failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    @staticmethod
    def _track_open_memcore_turn_for_guard(
        *,
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> None:
        if _MEMCORE_OPEN_TURN_GUARD.get() is None:
            return
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return
        _MEMCORE_OPEN_TURN_GUARD.set(
            {
                "turn_id": resolved_turn_id,
                "profile_user_id": str(profile_user_id or "").strip(),
                "session_id": str(session_id or "").strip(),
                "character_pack_id": str(character_pack_id or "").strip(),
            }
        )

    @staticmethod
    def _clear_open_memcore_turn_guard(turn_id: str) -> None:
        tracked = _MEMCORE_OPEN_TURN_GUARD.get()
        if not isinstance(tracked, dict):
            return
        if str(tracked.get("turn_id") or "").strip() != str(turn_id or "").strip():
            return
        _MEMCORE_OPEN_TURN_GUARD.set({})

    def _abort_open_memcore_turn_guard(self, *, reason: str) -> None:
        tracked = _MEMCORE_OPEN_TURN_GUARD.get()
        if not isinstance(tracked, dict):
            return
        turn_id = str(tracked.get("turn_id") or "").strip()
        if not turn_id:
            return
        # Clear first so a failure in the recovery path cannot recursively
        # retain or re-abort the same turn while unwinding the caller.
        _MEMCORE_OPEN_TURN_GUARD.set({})
        try:
            result = self._abort_memcore_input_turn(
                turn_id=turn_id,
                reason=str(reason or "turn_scope_exited_open"),
                profile_user_id=str(tracked.get("profile_user_id") or ""),
                session_id=str(tracked.get("session_id") or ""),
                character_pack_id=str(tracked.get("character_pack_id") or ""),
            )
            if not isinstance(result, dict) or not result.get("ok"):
                logger.warning(
                    "memcore guarded turn abort failed status=%s reason=%s",
                    str((result or {}).get("status") or "unknown")[:40] if isinstance(result, dict) else "invalid",
                    str((result or {}).get("reason") or "abort_failed")[:120]
                    if isinstance(result, dict)
                    else "invalid_result",
                )
        except Exception as exc:
            logger.warning("memcore guarded turn abort failed type=%s", type(exc).__name__)

    def _begin_memcore_input_turn(
        self,
        *,
        user_record: dict[str, Any],
        external_event: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str,
        actor_display_name: str,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.begin_input_turn(
                user_record,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
                external_event=external_event,
            )
            self._warn_memcore_write_result("input turn open", result)
            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "invalid_input_turn_open_result",
                    "turn_id": "",
                    "writable": False,
                }
            normalized = dict(result)
            status = str(normalized.get("status") or "").strip().lower()
            turn_id = str(normalized.get("turn_id") or "").strip()
            writable = bool(
                normalized.get("ok") and turn_id and status in {"open", "opened"} and normalized.get("writable", True)
            )
            normalized["writable"] = writable
            if not writable:
                # ``begin_turn`` is idempotent and may return the old handle for
                # an already completed/aborted stimulus.  Such a handle is
                # useful evidence, but it is not a writable current turn.
                # Never pass its id to tool/metadata writers.
                normalized["turn_id"] = ""
                if turn_id and status:
                    logger.warning(
                        "memcore input turn is not writable status=%s",
                        status[:80],
                    )
            else:
                self._track_open_memcore_turn_for_guard(
                    turn_id=turn_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            return normalized
        except Exception as exc:
            exception_code = f"exception_{type(exc).__name__}"
            logger.warning("memcore input turn open failed reason=%s", type(exc).__name__)
            return {
                "ok": False,
                "status": "failed",
                "reason": exception_code,
                "turn_id": "",
                "writable": False,
            }

    def _begin_memcore_existing_input_turn(
        self,
        *,
        source_id: str,
        stimulus_source_ids: list[str] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.begin_existing_input_turn(
                source_id=source_id,
                stimulus_source_ids=stimulus_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("existing input turn open", result)
            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "invalid_existing_input_turn_open_result",
                    "turn_id": "",
                    "writable": False,
                }
            normalized = dict(result)
            status = str(normalized.get("status") or "").strip().lower()
            turn_id = str(normalized.get("turn_id") or "").strip()
            writable = bool(
                normalized.get("ok") and turn_id and status in {"open", "opened"} and normalized.get("writable", True)
            )
            normalized["writable"] = writable
            if not writable:
                normalized["turn_id"] = ""
            else:
                self._track_open_memcore_turn_for_guard(
                    turn_id=turn_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            return normalized
        except Exception as exc:
            logger.warning("memcore existing input turn open failed reason=%s", type(exc).__name__)
            return {
                "ok": False,
                "status": "failed",
                "reason": f"exception_{type(exc).__name__}",
                "turn_id": "",
                "writable": False,
            }

    def _begin_memcore_hidden_host_turn(
        self,
        *,
        referenced_source_ids: list[str] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.begin_hidden_host_turn(
                referenced_source_ids=referenced_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("hidden host turn open", result)
            normalized = dict(result) if isinstance(result, dict) else {}
            status = str(normalized.get("status") or "").strip().lower()
            turn_id = str(normalized.get("turn_id") or "").strip()
            writable = bool(
                normalized.get("ok") and turn_id and status in {"open", "opened"} and normalized.get("writable", True)
            )
            normalized["writable"] = writable
            if not writable:
                normalized["turn_id"] = ""
            else:
                self._track_open_memcore_turn_for_guard(
                    turn_id=turn_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            return normalized
        except Exception as exc:
            logger.warning("memcore hidden host turn open failed reason=%s", type(exc).__name__)
            return {
                "ok": False,
                "status": "failed",
                "reason": f"exception_{type(exc).__name__}",
                "turn_id": "",
                "writable": False,
            }

    @staticmethod
    def _memcore_input_turn_failure(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict) or not result:
            return None
        status = str(result.get("status") or "failed").strip().lower()
        writable = bool(
            result.get("writable")
            or (result.get("ok") and str(result.get("turn_id") or "").strip() and status in {"open", "opened"})
        )
        if writable:
            return None
        detail = MemcoreTurnPipelineMixin._safe_memcore_failure_code(
            result.get("reason") or status,
            fallback="input_turn_unavailable",
        )
        return {
            "status": MemcoreTurnPipelineMixin._safe_memcore_failure_code(status, fallback="failed"),
            "reason": "input_turn_not_writable",
            "detail": detail,
            "delivery_status": "model_reply_preserved",
        }

    @staticmethod
    def _safe_memcore_failure_code(value: Any, *, fallback: str) -> str:
        text = str(value or "").strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,119}", text):
            return text
        return str(fallback or "memcore_failure")[:120]

    @staticmethod
    def _attach_nonfatal_memcore_failure(
        final_output: dict[str, Any],
        failure: dict[str, Any] | None,
    ) -> None:
        if not isinstance(failure, dict) or not failure:
            return
        if isinstance(final_output.get("_memcore_failure"), dict):
            return
        final_output["_memcore_failure"] = {
            "status": MemcoreTurnPipelineMixin._safe_memcore_failure_code(
                failure.get("status"),
                fallback="failed",
            ),
            "reason": MemcoreTurnPipelineMixin._safe_memcore_failure_code(
                failure.get("reason"),
                fallback="memcore_write_failed",
            ),
            "detail": MemcoreTurnPipelineMixin._safe_memcore_failure_code(
                failure.get("detail"),
                fallback="unavailable",
            ),
            "delivery_status": "model_reply_preserved",
        }

    def _stage_memcore_turn_metadata(
        self,
        *,
        source_id: str,
        memory_metadata: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str = "",
        actor_display_name: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.stage_turn_metadata(
                source_id,
                memory_metadata,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
            )
            self._warn_memcore_write_result("metadata staging", result)
            return result
        except Exception as exc:
            logger.warning("memcore metadata staging failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _append_memcore_turn_intermediate(
        self,
        *,
        turn_id: str,
        assistant_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {}
        try:
            result = manager.append_turn_intermediate(
                assistant_record,
                turn_id=turn_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("intermediate append", result)
            return result
        except Exception as exc:
            logger.warning("memcore intermediate append failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _append_memcore_turn_user_input(
        self,
        *,
        turn_id: str,
        user_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str,
        actor_display_name: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {"ok": False, "status": "unavailable", "reason": "open_memcore_turn_missing"}
        append_input = getattr(manager, "append_turn_user_input", None)
        if not callable(append_input):
            return {"ok": False, "status": "unavailable", "reason": "turn_user_input_append_unavailable"}
        try:
            result = append_input(
                user_record,
                turn_id=turn_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
            )
            self._warn_memcore_write_result("turn user input append", result)
            return result if isinstance(result, dict) else {
                "ok": False,
                "status": "failed",
                "reason": "invalid_turn_user_input_append_result",
            }
        except Exception as exc:
            logger.warning("memcore turn user input append failed: %s", type(exc).__name__)
            return {"ok": False, "status": "failed", "reason": f"exception_{type(exc).__name__}"}

    def _record_turn_steering_inputs(
        self,
        *,
        steers: list[Any],
        turn_id: str,
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        prepared_attachments: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        applied_source_ids: list[str] = []
        failed_source_ids: list[str] = []
        from ..owner_private_session import current_private_scope
        private_scope = current_private_scope(self)
        for steer in steers:
            source_id = str(getattr(steer, "source_id", "") or "").strip()
            content = str(getattr(steer, "content", "") or "").strip()
            timestamp = int(getattr(steer, "timestamp", 0) or time.time())
            if not source_id or not content:
                continue
            attachments = (prepared_attachments or {}).get(source_id) or {}
            if attachments.get("context"):
                content = self._merge_extra_user_context(content, attachments["context"])
            steer_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                role="user",
                content=content,
                timestamp=timestamp,
                semantic_tags=extract_semantic_tags(content),
                source_id=source_id,
                memory_metadata={
                    **({"current_attachment_ids": attachments["attachment_ids"]} if attachments else {}),
                    **({"channel": private_scope.channel} if private_scope and private_scope.channel else {}),
                    **({"owner_private_shared": True} if private_scope and private_scope.shared else {}),
                } or None,
            )
            self._upsert_raw_record(steer_record)
            recent_raw_for_turn.append(steer_record)
            append_result = self._append_memcore_turn_user_input(
                turn_id=turn_id,
                user_record=steer_record,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=str(getattr(steer, "actor_id", "") or ""),
                actor_display_name=str(getattr(steer, "actor_display_name", "") or ""),
            )
            if append_result.get("ok"):
                applied_source_ids.append(source_id)
            else:
                failed_source_ids.append(source_id)
        return applied_source_ids, failed_source_ids

    def _settle_turn_steering_receipts(
        self,
        *,
        steers: list[Any],
        applied_source_ids: list[str],
        failed_source_ids: list[str],
    ) -> dict[str, int]:
        """Finish durable inbox claims only after the turn accepted each steer."""

        observer = getattr(self, "plugin_steering_observer", None)
        if callable(observer):
            try:
                observer(applied_source_ids=applied_source_ids, failed_source_ids=failed_source_ids)
            except Exception as exc:
                logger.warning("plugin steering receipt update failed: %s", exc.__class__.__name__)
        inbox_store = getattr(self, "session_inbox_store", None)
        if inbox_store is None:
            return {"committed": 0, "failed": 0, "settlement_failed": 0}
        applied = set(applied_source_ids)
        failed = set(failed_source_ids)
        counts = {"committed": 0, "failed": 0, "settlement_failed": 0}
        for steer in steers:
            source_id = str(getattr(steer, "source_id", "") or "").strip()
            item_id = str(getattr(steer, "receipt_item_id", "") or "").strip()
            claim_token = str(getattr(steer, "receipt_claim_token", "") or "").strip()
            if not item_id or not claim_token:
                continue
            try:
                if source_id in applied:
                    result = inbox_store.commit(item_id, claim_token=claim_token)
                    outcome = "committed"
                elif source_id in failed:
                    result = inbox_store.fail(
                        item_id,
                        claim_token=claim_token,
                        error="memcore_turn_user_input_append_failed",
                        retryable=False,
                    )
                    outcome = "failed"
                else:
                    continue
            except Exception as exc:
                counts["settlement_failed"] += 1
                logger.warning(
                    "turn steer inbox settlement raised: item=%s source=%s reason=%s",
                    item_id,
                    source_id,
                    exc.__class__.__name__,
                )
                continue
            if isinstance(result, dict) and result.get("ok"):
                counts[outcome] += 1
            else:
                counts["settlement_failed"] += 1
                logger.warning(
                    "turn steer inbox settlement failed: item=%s source=%s status=%s",
                    item_id,
                    source_id,
                    str(result.get("status") if isinstance(result, dict) else "invalid_result"),
                )
        return counts

    def _chat_provider_protocol_for_memcore(
        self,
        *,
        chat_model_override: str = "",
        execution_target: Any = None,
    ) -> str:
        if execution_target is not None:
            protocol = str(getattr(execution_target, "protocol", "") or "").strip()
            if protocol:
                return protocol
        runtime = getattr(self, "llm", None)
        protocol_getter = getattr(runtime, "chat_provider_protocol", None)
        if not callable(protocol_getter):
            return ""
        try:
            return str(protocol_getter(chat_model_override=chat_model_override) or "").strip()
        except Exception as exc:
            logger.debug("memcore provider protocol resolution failed: %s", exc)
            return ""

    def _complete_memcore_input_turn(
        self,
        *,
        turn_id: str,
        assistant_record: dict[str, Any],
        memory_metadata: dict[str, Any] | None,
        provider_output_raw: str,
        chat_model_override: str = "",
        execution_target: Any = None,
        annotation_status: str = "",
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        append_final: bool = True,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {}
        try:
            provider_profile = ""
            provider_projection: dict[str, Any] | None = None
            if str(provider_output_raw or "") or not append_final:
                provider_profile = self._chat_provider_protocol_for_memcore(
                    chat_model_override=chat_model_override,
                    execution_target=execution_target,
                )
                if provider_profile and str(provider_output_raw or ""):
                    provider_projection = {
                        "role": "assistant",
                        "content": str(provider_output_raw),
                    }
            result = manager.complete_input_turn(
                turn_id=turn_id,
                assistant_record=assistant_record,
                memory_metadata=memory_metadata,
                provider_output_raw=str(provider_output_raw or ""),
                provider_profile=provider_profile,
                provider_projection=provider_projection,
                annotation_status=(
                    str(annotation_status or "").strip()
                    or ("accepted_model" if isinstance(memory_metadata, dict) else "missing")
                ),
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                append_final=append_final,
            )
            self._warn_memcore_write_result("input turn completion", result)
            return result
        except Exception as exc:
            logger.warning("memcore input turn completion failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _finalize_memcore_input_turn_for_delivery(
        self,
        *,
        final_output: dict[str, Any],
        turn_id: str,
        assistant_record: dict[str, Any],
        memory_metadata: dict[str, Any] | None,
        provider_output_raw: str,
        chat_model_override: str,
        execution_target: Any = None,
        annotation_status: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> bool:
        """Close a MemCore turn without discarding an already generated reply.

        ``complete_turn`` is idempotent, so one immediate retry is safe for a
        transient store failure.  If both attempts fail, close the open turn
        through the explicit abort path and attach a bounded, path-free status
        to the real model result.  A successful abort becomes a terminal turn
        and schedules the same provider-aware compaction path.
        """

        if not str(turn_id or "").strip():
            return True
        completion: dict[str, Any] = {}
        for _attempt in range(2):
            completion = self._complete_memcore_input_turn(
                turn_id=turn_id,
                assistant_record=assistant_record,
                memory_metadata=memory_metadata,
                provider_output_raw="" if final_output.get("_notification_suppressed") else provider_output_raw,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                annotation_status=annotation_status,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                append_final=not bool(
                    final_output.get("_tool_finished_turn") or final_output.get("_notification_suppressed")
                ),
            )
            if not completion or bool(completion.get("ok")):
                if completion and completion.get("ok"):
                    self._clear_open_memcore_turn_guard(turn_id)
                    if final_output.get("_notification_suppressed"):
                        try:
                            receipt = self.record_plugin_timeline_event(
                                {
                                    "source_id": f"{turn_id}:notification_suppressed",
                                    "user_id": session_id,
                                    "real_user_id": profile_user_id,
                                    "character_pack_id": character_pack_id,
                                    "timestamp": int(time.time()),
                                    "event": {
                                        "event_type": "notification.delivery",
                                        "source": "host",
                                        "fields": {
                                            "status": "suppressed",
                                            "reason": str(final_output["_notification_suppressed"]),
                                            "scope": "final_text_and_automatic_emotion",
                                            "text_sent": "false",
                                        },
                                    },
                                }
                            )
                        except Exception:
                            receipt = {"ok": False}
                        if not receipt.get("ok"):
                            self._attach_nonfatal_memcore_failure(
                                final_output,
                                {"status": "failed", "reason": "notification_suppression_receipt_failed"},
                            )
                return True
        aborted = self._abort_memcore_input_turn(
            turn_id=turn_id,
            reason="input_turn_completion_failed",
            chat_model_override=chat_model_override,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        final_output["_memcore_failure"] = {
            "status": str(completion.get("status") or "failed")[:40],
            "reason": "input_turn_completion_failed",
            "recovery_status": "aborted" if bool((aborted or {}).get("ok")) else "abort_failed",
            "delivery_status": "model_reply_preserved",
        }
        return False

    @staticmethod
    def _memory_metadata_has_signal(metadata: Any) -> bool:
        return memcore_metadata_has_signal(metadata)

    def _attach_memory_annotation_truth(
        self,
        output: dict[str, Any],
        *,
        result: Any,
        raw_result: Any,
    ) -> None:
        allowed = {"accepted_model", "accepted_host", "missing", "invalid", "plain", "fallback", "rejected"}
        status = str(getattr(result, "metadata_status", "") or "").strip()
        present_value = getattr(result, "metadata_present", None)
        if status not in allowed:
            raw_payload = raw_result if isinstance(raw_result, dict) else {}
            present_value = "memory_metadata" in raw_payload
            if not present_value:
                status = "missing"
            elif isinstance(raw_payload.get("memory_metadata"), dict):
                status = "accepted_model"
            else:
                status = "invalid"
        if status in {"missing", "invalid", "plain", "fallback"} and self._memory_metadata_has_signal(
            output.get("memory_metadata")
        ):
            status = "accepted_host"
        output[MEMORY_ANNOTATION_STATUS_FIELD] = status
        output[MEMORY_METADATA_PRESENT_FIELD] = bool(present_value)

    def _pop_memory_annotation_status(self, output: dict[str, Any]) -> str:
        status = str(output.pop(MEMORY_ANNOTATION_STATUS_FIELD, "") or "").strip()
        output.pop(MEMORY_METADATA_PRESENT_FIELD, None)
        if status in {"accepted_model", "accepted_host", "missing", "invalid", "plain", "fallback", "rejected"}:
            return status
        return "accepted_host" if self._memory_metadata_has_signal(output.get("memory_metadata")) else "missing"

    def _abort_memcore_input_turn(
        self,
        *,
        turn_id: str,
        reason: str,
        chat_model_override: str = "",
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {}
        try:
            result = manager.abort_input_turn(
                turn_id=turn_id,
                reason=reason,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("input turn abort", result)
            if isinstance(result, dict) and bool(result.get("ok")):
                self._clear_open_memcore_turn_guard(turn_id)
                compaction = self._schedule_memcore_compaction(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    chat_model_override=chat_model_override,
                )
                if compaction:
                    result = {**result, "compaction": compaction}
            return result
        except Exception as exc:
            logger.warning("memcore input turn abort failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    @staticmethod
    def _warn_memcore_write_result(operation: str, result: Any) -> None:
        if not isinstance(result, dict) or bool(result.get("ok")):
            return
        logger.warning(
            "memcore %s rejected status=%s reason=%s",
            operation,
            str(result.get("status") or "unknown")[:80],
            str(result.get("reason") or "")[:160],
        )

    def _record_attachment_material_trace(
        self,
        *,
        event_type: str,
        item: dict[str, Any],
        timestamp: int,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not isinstance(item, dict):
            return {}
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        profile_user_id = str(item.get("profile_user_id") or "").strip()
        session_id = str(item.get("session_id") or "").strip()
        character_pack_id = str(detail.get("character_pack_id") or item.get("character_pack_id") or "").strip()
        try:
            if str(event_type or "").strip().lower() == "cleanup":
                return manager.record_material_cleanup(
                    item=item,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    timestamp=timestamp,
                    reason=reason,
                    delete_storage=delete_storage,
                )
            return manager.record_material_reference(
                item=item,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                timestamp=timestamp,
            )
        except Exception as exc:
            logger.warning("memcore attachment material trace failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _record_generated_workspace_cleanup(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        action: str,
        status: str,
        managed: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        unresolved: list[str],
        reason: str,
        timestamp: int,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {"ok": True, "status": "skipped", "reason": "memcore_disabled"}
        resolved_character_pack_id = str(character_pack_id or "").strip()
        if not resolved_character_pack_id:
            store = getattr(self, "store", None)
            session = (
                store.get_session(profile_user_id, session_id)
                if store is not None and hasattr(store, "get_session")
                else None
            )
            if isinstance(session, dict):
                resolved_character_pack_id = str(session.get("character_pack_id") or "").strip()
        try:
            result = manager.record_generated_workspace_cleanup(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=resolved_character_pack_id,
                action=action,
                status=status,
                managed=managed,
                failures=failures,
                unresolved=unresolved,
                reason=reason,
                timestamp=timestamp,
            )
            self._warn_memcore_write_result("generated workspace cleanup", result)
            return result
        except Exception as exc:
            logger.warning("memcore generated workspace cleanup failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _schedule_memcore_compaction(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        chat_model_override: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            return manager.compact_due_background(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                provider_profile=self._chat_provider_protocol_for_memcore(chat_model_override=chat_model_override),
                chat_model_override=chat_model_override,
            )
        except Exception as exc:
            logger.warning("memcore compaction scheduling failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}


    def _build_memcore_request_observer(
        self,
        *,
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        request_projection_state: dict[str, Any] | None = None,
    ) -> Any:
        if isinstance(request_projection_state, dict) and request_projection_state.get(
            "record_request_projection"
        ) is False:
            return None
        if not bool(getattr(self.llm, "supports_request_observer", False)):
            return None
        manager = getattr(self, "memcore_manager", None)
        recorder = getattr(manager, "record_request_projection", None)
        binder = getattr(manager, "bind_request_projection_messages", None)
        projection_read = generation_context.get("memcore_projection_read")
        if not callable(recorder) or not callable(binder) or not isinstance(projection_read, dict):
            return None
        turn_id = str(projection_read.get("current_turn_id") or "").strip()
        current_messages = [
            dict(message)
            for message in list(projection_read.get("current_turn_messages") or [])
            if isinstance(message, dict)
        ]
        if not turn_id or not current_messages:
            return None
        frozen_turn_messages: list[dict[str, Any]] = []
        frozen_request_indexes: list[int] = []

        def observe(request: dict[str, Any]) -> dict[str, Any]:
            nonlocal frozen_turn_messages, frozen_request_indexes
            if not isinstance(request, dict):
                return {"ok": False, "status": "failed", "reason": "request_observation_invalid"}
            if isinstance(request_projection_state, dict):
                from ..capability_discovery import visible_tool_result_messages
                request_projection_state["visible_tool_history"] = visible_tool_result_messages(request.get("history_messages", []))
            persistent_messages = [
                dict(message)
                for message in list(request.get("persistent_turn_messages") or [])
                if isinstance(message, dict)
            ]
            if not frozen_turn_messages:
                if not persistent_messages:
                    return {"ok": False, "status": "failed", "reason": "persistent_turn_messages_missing"}
                if len(persistent_messages) != len(current_messages):
                    stale_frozen_turn = bool(current_messages) and all(
                        str(message.get("projection_status") or "").strip().lower() == "request_frozen"
                        for message in current_messages
                    )
                    if not stale_frozen_turn:
                        return {"ok": False, "status": "failed", "reason": "persistent_turn_count_mismatch"}
                    # An idempotently replayed stimulus can resolve to an
                    # already completed/aborted turn.  That stale turn is not
                    # writable, and its request-frozen user/tool/assistant sequence no
                    # longer has the same shape as this delivery attempt.
                    # Preserve the model reply while declining to mutate the
                    # stale projection; normal writable turns still take the
                    # strict record-and-verify path below.
                    logger.warning(
                        "memcore request projection skipped reason=persistent_turn_count_mismatch "
                        "projected_count=%s request_count=%s",
                        len(current_messages),
                        len(persistent_messages),
                    )
                    return {
                        "ok": True,
                        "status": "skipped",
                        "reason": "persistent_turn_count_mismatch",
                        "recorded": False,
                    }
                prepared: list[dict[str, Any]] = []
                for metadata, actual in zip(current_messages, persistent_messages):
                    actual_role = str(actual.get("role") or "").strip().lower()
                    if actual_role not in {"user", "assistant", "tool"}:
                        return {"ok": False, "status": "failed", "reason": "current_turn_role_invalid"}
                    source_ids = [
                        str(source_id or "").strip()
                        for source_id in list(metadata.get("source_ids") or [])
                        if str(source_id or "").strip()
                    ]
                    if not source_ids:
                        return {"ok": False, "status": "failed", "reason": "current_turn_source_ids_missing"}
                    prepared.append(
                        {
                            **metadata,
                            "payload": dict(actual),
                            "source_ids": source_ids,
                        }
                    )
                binding = binder(messages=prepared, active_turn_id=turn_id)
                if not isinstance(binding, dict) or not binding.get("ok"):
                    detail = str((binding or {}).get("reason") or "request_binding_failed")
                    return {
                        "ok": False,
                        "status": "failed",
                        "reason": "context_authority_failed",
                        "detail": detail,
                    }
                frozen_turn_messages = [
                    dict(message)
                    for message in list(binding.get("active_messages") or [])
                    if isinstance(message, dict)
                ]
                frozen_request_indexes = [int(index) for index in list(binding.get("active_request_indexes") or [])]
                if not frozen_turn_messages or len(frozen_request_indexes) != len(frozen_turn_messages):
                    return {
                        "ok": False,
                        "status": "failed",
                        "reason": "context_authority_failed",
                        "detail": "active_turn_binding_invalid",
                    }
            result = recorder(
                turn_id=turn_id,
                provider_profile=str(request.get("protocol") or ""),
                turn_messages=[dict(message) for message in frozen_turn_messages],
                history_messages=[dict(message) for message in persistent_messages],
                history_message_indexes=list(frozen_request_indexes),
                audit_history_messages=[
                    dict(message)
                    for message in list(request.get("audit_history_messages") or [])
                    if isinstance(message, dict)
                ],
                attempt=0,
                model_route=request.get("model_route") or {},
                system_prefix=request.get("system_prefix") or "",
                tool_schema=request.get("tool_schema") or [],
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            if isinstance(result, dict) and result.get("ok"):
                capture = getattr(manager, "capture_compaction_request", None)
                if callable(capture):
                    request["_compaction_source_messages"] = [
                        {**metadata, "payload": dict(actual)}
                        for metadata, actual in zip(current_messages, persistent_messages)
                    ]
                    try:
                        capture(request=request, generation_context=generation_context,
                                profile_user_id=profile_user_id, session_id=session_id, character_pack_id=character_pack_id)
                    except Exception as exc:
                        # Optional background maintenance must not cancel an
                        # otherwise valid user/tool request. The next boundary
                        # can capture again; no empty summary is published.
                        logger.warning("memcore prefix capture failed: %s", exc.__class__.__name__)
                        generation_context["memcore_compaction_capture"] = {"status": "failed", "reason": "prefix_capture_failed"}
                if result.get("status") == "skipped" and result.get("recorded") is False:
                    receipt = {key: result.get(key) for key in (
                        "status", "reason", "recorded", "turn_id", "provider_profile", "turn_status")}
                    generation_context["memcore_request_projection"] = receipt
                    return {"ok": True, **receipt}
                if isinstance(request_projection_state, dict) and persistent_messages:
                    first_message = persistent_messages[0]
                    if str(first_message.get("role") or "").strip().lower() == "user":
                        frozen_prompt = self._provider_message_text(first_message.get("content"))
                        if frozen_prompt:
                            request_projection_state.setdefault("current_user_prompt", frozen_prompt)
                            request_projection_state.setdefault("turn_id", turn_id)
                generation_context["memcore_request_projection"] = {
                    key: result.get(key)
                    for key in (
                        "status",
                        "turn_id",
                        "attempt",
                        "provider_profile",
                        "projection_count",
                        "projection_hashes",
                        "history_hash",
                        "full_prefix_hash",
                        "media_omitted",
                    )
                }
                return {"ok": True, "status": "recorded"}
            failure_reason = str((result or {}).get("reason") or "request_projection_record_failed")
            if failure_reason.startswith("projection_audit_"):
                logger.warning("memcore audit persistence failed reason=%s", failure_reason)
                public_reason = "audit_persistence_failed"
            else:
                # The binding above already proved that the provider-visible
                # current turn matches the authoritative MemCore projection.
                # Persisting a request audit is evidence about that request;
                # it is not permission to send it.  A recorder outage must be
                # observable, but must not cancel an otherwise valid Agent
                # decision or discard completed tool results.
                logger.warning("memcore request projection persistence failed reason=%s", failure_reason)
                public_reason = "request_projection_record_failed"
            failure = {
                "status": str((result or {}).get("status") or "failed"),
                "reason": public_reason,
                "detail": failure_reason,
                "delivery_status": "model_reply_preserved",
            }
            generation_context["memcore_request_projection"] = {
                "status": "degraded",
                "reason": public_reason,
                "detail": failure_reason,
            }
            generation_context["memcore_request_projection_failure"] = failure
            return {"ok": True, "status": "degraded", "reason": public_reason}

        observe.capture_compaction_context = str(getattr(config, "MEMCORE_COMPACTION_REQUEST_MODE", "independent")) == "chat_prefix"
        return observe

    @staticmethod
    def _provider_message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type not in {"text", "input_text"}:
                continue
            text = block.get("text")
            if text is None:
                text = block.get("input_text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _request_projection_user_prompt(
        request_projection_state: dict[str, Any] | None,
        fallback: str,
    ) -> str:
        if isinstance(request_projection_state, dict):
            frozen = request_projection_state.get("current_user_prompt")
            if isinstance(frozen, str) and frozen:
                return frozen
        return str(fallback or "")
