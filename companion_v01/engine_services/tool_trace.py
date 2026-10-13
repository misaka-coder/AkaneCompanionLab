"""Tool timeline writes, durable action/result traces, sanitization and outcome states."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Mapping
import config
from ..text_utils import extract_semantic_tags
from ..tool_invocation import (
    NATIVE_ANTHROPIC,
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_ARGUMENTS_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
)
from ..tool_runtime import ToolExecutionResult
from ..model_image_inputs import image_source_metadata
from .. import tool_orchestration_engine

logger = logging.getLogger("akane.engine")


class ToolTraceMixin:
    @staticmethod
    def _tool_call_allows_assistant_preface(tool_call: dict[str, Any]) -> bool:
        return str(tool_call.get("type") or "") not in {
            "retrieve_memory",
            "browse_memory",
            "read_memory_timeline",
            "open_memory",
            "load_character_context",
        }

    def _record_assistant_preface_for_tool_call(
        self,
        *,
        tool_call: dict[str, Any],
        final_output: dict[str, Any],
        preface_turns: list[dict[str, str]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        date_label: str,
        time_of_day: str,
        memcore_turn_id: str = "",
    ) -> str:
        preface_turn = (
            self._build_assistant_dialogue_turn(final_output.get("speech"))
            if self._tool_call_allows_assistant_preface(tool_call)
            else None
        )
        if not preface_turn:
            return ""
        preface_turns.append(preface_turn)
        preface_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            role="assistant",
            content=preface_turn["speech"],
            timestamp=now_ts,
            date_label=date_label,
            time_of_day=time_of_day,
            semantic_tags=extract_semantic_tags(preface_turn["speech"]),
            memory_metadata=self._build_assistant_timeline_metadata(final_output),
        )
        self._upsert_raw_record(preface_record)
        self._append_memcore_turn_intermediate(
            turn_id=memcore_turn_id,
            assistant_record=preface_record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        recent_raw_for_turn.append(preface_record)
        return str(preface_record.get("source_id") or "").strip()

    def _record_tool_round_result(
        self,
        *,
        tool_call: dict[str, Any],
        tool_result: ToolExecutionResult,
        tool_results: list[ToolExecutionResult],
        tool_events: list[dict[str, Any]],
        tool_followups: list[str],
        tool_turns: list[dict[str, Any]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
    ) -> tuple[list[dict[str, Any]], str]:

        tool_results.append(tool_result)
        current_events = list(tool_result.stream_events)
        tool_events.extend(current_events)
        result_followup = tool_orchestration_engine.append_structured_artifact_receipts(
            tool_result.followup_context,
            stream_events=tool_result.stream_events,
        )
        shaped_followup = tool_orchestration_engine.shape_tool_followup(
            (
                tool_result.followup_envelope.with_content(result_followup)
                if tool_result.followup_envelope is not None
                else result_followup
            ),
            tool_type=tool_result.tool_type,
        )
        if str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() not in {NATIVE_ANTHROPIC, NATIVE_OPENAI}:
            tool_followups.append(f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n{shaped_followup}")
        self._record_tool_dialogue_turns(raw_turns=tool_result.raw_turns,
            tool_turns=tool_turns, recent_raw_for_turn=recent_raw_for_turn,
            profile_user_id=profile_user_id, session_id=session_id,
            character_pack_id=character_pack_id, now_ts=now_ts)
        return current_events, shaped_followup

    def _record_tool_dialogue_turns(self, *, raw_turns, tool_turns, recent_raw_for_turn,
                                    profile_user_id, session_id, character_pack_id, now_ts):
        current_tool_turns = list(raw_turns)
        tool_turns.extend(current_tool_turns)
        for tool_turn in current_tool_turns:
            speaker = str(tool_turn.get("speaker") or "NPC").strip() or "NPC"
            speech = str(tool_turn.get("speech") or "").strip()
            if not speech:
                continue
            tool_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                role=f"npc:{speaker}",
                content=speech,
                timestamp=max(now_ts, int(time.time())),
                semantic_tags=extract_semantic_tags(speech),
            )
            self._upsert_raw_record(tool_record)
            recent_raw_for_turn.append(tool_record)

    def _record_memcore_tool_media_input(
        self,
        *,
        model_image_inputs: list[dict[str, Any]],
        related_source_ids: list[str],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        memcore_turn_id: str,
    ) -> list[str]:
        if not model_image_inputs or not str(memcore_turn_id or "").strip():
            return []
        manager = getattr(self, "memcore_manager", None)
        append_media = getattr(manager, "append_turn_media_input", None)
        if not callable(append_media):
            return []
        safe_items = [
            {
                "attachment_id": str(item.get("attachment_id") or "").strip(),
                "attachment_handle": str(item.get("attachment_handle") or "").strip(),
                "mime_type": str(item.get("mime_type") or item.get("content_type") or "").strip(),
                **image_source_metadata(item),
            }
            for item in model_image_inputs
            if isinstance(item, dict)
        ]
        try:
            recorded = append_media(
                items=safe_items,
                turn_id=memcore_turn_id,
                related_source_ids=related_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                timestamp=max(now_ts, int(time.time())),
            )
            self._warn_memcore_write_result("tool media input record", recorded)
            source_id = str((recorded or {}).get("source_id") or "").strip()
            return [source_id] if bool((recorded or {}).get("ok")) and source_id else []
        except Exception as exc:
            logger.warning("memcore tool media input record failed reason=%s", type(exc).__name__)
            return []

    @staticmethod
    def _tool_call_model_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
        """Recover the arguments the provider originally saw.

        Adapter/plugin handlers use an internal ``arguments`` envelope for
        CapCore validation. Provider-native schemas do not expose that envelope,
        so replaying it would change the assistant tool_call and break strict
        continuation gateways such as PinAI. Only unwrap mapped native calls;
        ordinary tools and legacy calls retain their existing shape.
        """

        model_arguments = tool_call.get(TOOL_MODEL_ARGUMENTS_FIELD)
        if isinstance(model_arguments, Mapping):
            return {str(key): value for key, value in model_arguments.items()}
        payload = {
            str(key): value
            for key, value in dict(tool_call or {}).items()
            if key != "type" and not str(key).startswith("_tool_")
        }
        wrapped = payload.get("arguments")
        if str(tool_call.get(TOOL_MODEL_NAME_FIELD) or "").strip() and len(payload) == 1 and isinstance(wrapped, dict):
            return {str(key): value for key, value in wrapped.items() if not str(key).startswith("_tool_")}
        return payload

    def _record_memcore_tool_batch(
        self,
        *,
        items: list[tuple[dict[str, Any], ToolExecutionResult, str]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        memcore_turn_id: str,
        recorded_tool_call_ids: set[str] | None,
    ) -> tuple[list[str], dict[str, Any] | None]:
        manager = getattr(self, "memcore_manager", None)
        if manager is None or not getattr(manager, "enabled", False) or not str(memcore_turn_id or "").strip():
            return [], None
        exchanges: list[dict[str, Any]] = []
        trace_keys: list[str] = []
        for tool_call, tool_result, shaped_followup in items:
            tool_type = str(tool_call.get("type") or tool_result.tool_type or "unknown").strip() or "unknown"
            call_id = str(tool_call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
            if not call_id:
                call_id = (
                    "call_" + hashlib.sha256(self._tool_call_signature(tool_call).encode("utf-8")).hexdigest()[:16]
                )
            trace_key = f"{tool_type}:{call_id}"
            if recorded_tool_call_ids is not None:
                if trace_key in recorded_tool_call_ids:
                    continue
            trace_keys.append(trace_key)
            feedback = str(shaped_followup or "").strip()
            stored_result: Any = self._sanitize_tool_trace_text(feedback)
            trace_receipt = getattr(tool_result, "trace_receipt", None)
            retention_anchor = (
                self._sanitize_tool_trace_value(dict(trace_receipt))
                if isinstance(trace_receipt, Mapping) and trace_receipt
                else None
            )
            result_status = self._tool_result_trace_status(tool_result)
            source_material = f"{current_user_source_id}|{session_id}|{call_id}|{tool_type}"
            exchanges.append(
                {
                    "tool_name": str(tool_call.get(TOOL_MODEL_NAME_FIELD) or "").strip() or tool_type,
                    "tool_call_id": call_id,
                    "tool_input": self._sanitize_tool_trace_value(self._tool_call_model_arguments(tool_call)),
                    "result": stored_result,
                    "source": str(tool_call.get(TOOL_SOURCE_FIELD) or tool_result.tool_type or tool_type),
                    "timestamp": max(now_ts, int(time.time())),
                    "source_id_prefix": (
                        "tooltrace:" + hashlib.sha256(source_material.encode("utf-8")).hexdigest()[:32]
                    ),
                    "result_status": result_status,
                    "retention_anchor": retention_anchor,
                }
            )
        if not exchanges:
            return [], None
        try:
            recorded = manager.record_tool_batch(
                exchanges=exchanges,
                turn_id=memcore_turn_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("tool batch record", recorded)
            if not isinstance(recorded, dict) or not bool(recorded.get("ok")):
                detail = self._safe_memcore_failure_code(
                    (recorded or {}).get("reason") or (recorded or {}).get("status"),
                    fallback="tool_trace_store_rejected",
                )
                return [], {
                    "status": self._safe_memcore_failure_code(
                        (recorded or {}).get("status"),
                        fallback="failed",
                    ),
                    "reason": "tool_trace_record_failed",
                    "detail": detail,
                }
            source_ids = [
                source_id
                for exchange in list((recorded or {}).get("exchanges") or [])
                if isinstance(exchange, dict)
                for source_id in (
                    str(exchange.get("tool_use_source_id") or "").strip(),
                    str(exchange.get("tool_result_source_id") or "").strip(),
                )
                if source_id
            ]
            if len(source_ids) != 2 * len(exchanges):
                return source_ids, {
                    "status": "failed",
                    "reason": "tool_trace_record_failed",
                    "detail": "tool_trace_incomplete",
                }
            if recorded_tool_call_ids is not None:
                recorded_tool_call_ids.update(trace_keys)
            return source_ids, None
        except Exception as exc:
            logger.warning("memcore tool batch record failed reason=%s", type(exc).__name__)
            return [], {
                "status": "failed",
                "reason": "tool_trace_record_failed",
                "detail": self._safe_memcore_failure_code(
                    f"exception_{type(exc).__name__}",
                    fallback="tool_trace_store_exception",
                ),
            }

    def _sanitize_tool_trace_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._sanitize_tool_trace_value(item)
                for key, item in value.items()
                if not self._is_sensitive_tool_trace_key(key)
            }
        if isinstance(value, list):
            return [self._sanitize_tool_trace_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_tool_trace_value(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_tool_trace_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._sanitize_tool_trace_text(str(value))

    @staticmethod
    def _is_sensitive_tool_trace_key(value: Any) -> bool:
        """Reject credentials, not model-visible operation coordinates.

        Paths are legitimate tool arguments and often the only durable way for
        a later native-tool round to continue work.  Removing them here made
        MemCore's projected assistant action differ from the provider action
        that was actually executed.  Transport/cache/database paths that must
        stay private belong outside model-call arguments in the first place;
        credential-shaped values remain filtered below.
        """

        key = re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())
        if not key:
            return False
        if key in {
            "apikey",
            "authorization",
            "authtoken",
            "accesstoken",
            "clientsecret",
            "cookie",
            "password",
            "refreshtoken",
            "secret",
        }:
            return True
        if key.endswith("apikey") or key.endswith("secret") or key.endswith("password"):
            return True
        return False

    @staticmethod
    def _configured_tool_trace_secret_values() -> tuple[str, ...]:
        """Return exact deployment secrets that may be removed from traces.

        Source code and command output are opaque model evidence.  Guessing a
        secret from text such as ``Token = LexToken`` changes the action/result
        that MemCore later projects and can corrupt a following file rewrite.
        Credential-shaped structured keys are filtered separately; free text
        is only scrubbed when it contains a value the host actually configured
        as a credential.
        """

        markers = (
            "API_KEY",
            "ACCESS_TOKEN",
            "AUTH_TOKEN",
            "CLIENT_SECRET",
            "COOKIE",
            "PASSWORD",
            "REFRESH_TOKEN",
            "WEBHOOK_SECRET",
        )
        values: set[str] = set()
        for name, raw in vars(config).items():
            upper_name = str(name or "").upper()
            if not any(marker in upper_name for marker in markers):
                continue
            if not isinstance(raw, str):
                continue
            secret = raw.strip()
            if len(secret) < 8 or secret.lower() in {"configured", "redacted", "none", "null"}:
                continue
            values.add(secret)
        return tuple(sorted(values, key=len, reverse=True))

    @classmethod
    def _sanitize_tool_trace_text(cls, value: str) -> str:
        text = str(value or "")
        text = re.sub(
            r"(?i)(\bauthorization\s*:\s*bearer\s+)[^\s,;]+",
            r"\1[redacted]",
            text,
        )
        for secret in cls._configured_tool_trace_secret_values():
            text = text.replace(secret, "[redacted]")
        return text

    def _tool_result_is_error(self, tool_result: ToolExecutionResult) -> bool:
        feedback = str(getattr(tool_result, "followup_context", "") or "")
        if "<tool_use_error>" in feedback:
            return True
        for event in getattr(tool_result, "stream_events", []) or []:
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or event.get("state") or "").strip().lower()
            if status in {
                "error",
                "failed",
                "failure",
                "unavailable",
                "unavailable_before_dispatch",
                "execution_unknown",
                "rejected",
                "denied",
                "blocked",
                "canceled",
                "cancelled",
            }:
                return True
        return False

    def _tool_result_trace_status(self, tool_result: ToolExecutionResult) -> str:
        for event in getattr(tool_result, "stream_events", []) or []:
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or event.get("state") or "").strip().lower()
            if status in {"canceled", "cancelled"}:
                return "cancelled"
            if (
                str(event.get("type") or "").strip() == "capability_approval_required"
                or status == "approval_required"
            ):
                return "approval_required"
            # Preserve trusted terminal states in the MemCore observation
            # instead of collapsing them to a generic error/success, so a
            # timed-out or unconfirmed operation (exec commands and reviewed
            # desktop satellite capabilities alike) is never remembered as
            # done.  capability_execution_result events are only emitted by
            # reviewed execution paths, so their known terminal states are
            # safe to keep verbatim.
            if (
                str(event.get("type") or "").strip() == "capability_execution_result"
                and status in {"failed", "timed_out", "execution_unknown"}
            ):
                return status
        return "error" if self._tool_result_is_error(tool_result) else "success"
