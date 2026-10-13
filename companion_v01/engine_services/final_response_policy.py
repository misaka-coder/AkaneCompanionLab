"""Final response classification and same-context retry policy."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import config
from ..assistant_history_projection import ASSISTANT_HISTORY_RECORD_HEADER, LEGACY_ASSISTANT_HISTORY_RECORD_HEADER
from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest
from ..tool_invocation import NATIVE_TOOL_CALL_FIELD, NATIVE_TOOL_CALLS_FIELD
from .final_response_constants import (
    FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK,
    FINAL_RESPONSE_HISTORY_RECORD_FEEDBACK,
    FINAL_RESPONSE_RECOVERY_FEEDBACK,
    FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK,
    _TEXT_TOOL_PROTOCOL_RE,
)

logger = logging.getLogger("akane.engine.final_response")


def _is_history_reading_record(value: Any) -> bool:
    """Recognize a copied display record, without inspecting JSON field values."""
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    if not text:
        return False
    header = text.splitlines()[0].strip()
    return header == LEGACY_ASSISTANT_HISTORY_RECORD_HEADER or bool(re.fullmatch(
        re.escape(ASSISTANT_HISTORY_RECORD_HEADER) + r"(?:（整体表情 .+）)?：", header,
    ))


def _json_structure_tokens(text: str):
    """Scan boundaries once; quoted examples cannot become JSON structure."""
    depth = 0
    in_string = False
    escaped = False
    string_start = 0
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                yield '"', string_start, index + 1, depth
            continue
        if char == '"' and depth:
            in_string = True
            string_start = index
        elif char in "{[":
            yield char, index, index + 1, depth
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
            yield char, index, index + 1, depth
        elif char in ":,":
            yield char, index, index + 1, depth


class FinalResponsePolicyMixin:
    """Distinguish usable speech, native decisions, and recoverable output."""

    @staticmethod
    def _final_response_max_attempts(generation_context: dict[str, Any]) -> int:
        del generation_context
        return max(1, int(getattr(config, "CHAT_MODEL_DECISION_MAX_ATTEMPTS", 3) or 3))

    @staticmethod
    def _final_response_retry_note(
        generation_context: dict[str, Any],
        *,
        previous_feedback: str = "",
    ) -> str:
        if previous_feedback == "text_tool_protocol":
            return FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK
        if previous_feedback == "json_external_text":
            return FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK
        if previous_feedback == "history_record_output":
            return FINAL_RESPONSE_HISTORY_RECORD_FEEDBACK
        return FINAL_RESPONSE_RECOVERY_FEEDBACK

    @staticmethod
    def _final_response_retry_feedback(
        *,
        raw_result: Any,
        normalized: Any,
        parse_fallback: bool,
        provider_output_raw: str = "",
    ) -> str:
        if FinalResponsePolicyMixin._output_contains_text_tool_protocol(
            normalized,
            provider_output_raw=provider_output_raw,
        ):
            return "text_tool_protocol"
        if _is_history_reading_record(provider_output_raw):
            return "history_record_output"
        if FinalResponsePolicyMixin._raw_json_has_external_text(provider_output_raw):
            return "json_external_text"
        if FinalResponsePolicyMixin._raw_tool_call_is_pending(provider_output_raw):
            return "tool_call_pending"
        if parse_fallback:
            return "json_parse_fallback"
        if not isinstance(raw_result, dict):
            return "result_not_object"
        if "speech" not in raw_result:
            return "speech_missing"
        raw_speech = raw_result.get("speech")
        if not isinstance(raw_speech, str):
            return "speech_wrong_type"
        if not raw_speech.strip():
            return "speech_empty"
        if not isinstance(normalized, dict) or not str(normalized.get("speech") or "").strip():
            return "speech_unusable"
        return "speech_not_deliverable"

    @staticmethod
    def _text_contains_tool_protocol(value: Any) -> bool:
        return bool(_TEXT_TOOL_PROTOCOL_RE.search(str(value or "")))

    @staticmethod
    def _output_contains_text_tool_protocol(
        output: Any,
        *,
        provider_output_raw: str = "",
    ) -> bool:
        speech = output.get("speech") if isinstance(output, dict) else ""
        return FinalResponsePolicyMixin._text_contains_tool_protocol(speech) or (
            FinalResponsePolicyMixin._text_contains_tool_protocol(provider_output_raw)
        )

    @staticmethod
    def _raw_json_has_external_text(raw: Any) -> bool:
        """Return true when valid JSON is surrounded by model-authored prose.

        Silently extracting the object is lossy: the surrounding prose can be
        the real task report while ``speech`` is only a short summary. A plain
        object, or an object wrapped only in a Markdown JSON fence, is valid.
        Other prefix/suffix content gets one same-context repair turn.

        A damaged document is not "prose around JSON". When the model starts a
        JSON object but never closes it, an inner object still decodes cleanly;
        treating that as external text sent a truncated answer into the repair
        turn (and away from the client's own field contract) instead of
        delivering the units the model had already produced. The candidate
        object is therefore only accepted at a real text boundary, i.e. with no
        unclosed outer structure before it.
        """

        text = str(raw or "").strip()
        if not text:
            return False
        try:
            return not isinstance(json.loads(text), dict)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        decoder = json.JSONDecoder()
        for token, start, _end, depth in _json_structure_tokens(text):
            if token != "{" or depth:
                continue
            try:
                payload, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            prefix = text[:start].strip().lower()
            suffix = text[end:].strip().lower()
            return prefix not in {"", "```", "```json"} or suffix not in {"", "```"}
        return False

    @staticmethod
    def _final_output_has_tool_call(output: Any) -> bool:
        if not isinstance(output, dict):
            return False
        return bool(
            output.get("tool_call") or output.get(NATIVE_TOOL_CALL_FIELD) or output.get(NATIVE_TOOL_CALLS_FIELD)
        )

    @staticmethod
    def _raw_tool_call_is_pending(raw: str) -> bool:
        """True when the wire text carries a non-null `tool_call` intent.

        A final answer is allowed to omit the optional `tool_call` field or
        write an explicit `tool_call: null`; only an explicit non-null tool
        call blocks plain-text/parse recovery so a tool preface is never
        mistaken for a final answer.
        """
        text = str(raw or "")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        else:
            return isinstance(payload, dict) and payload.get("tool_call") is not None
        # A truncated root can still declare a pending call. Inspect only root
        # keys, never a nested metadata field or a quoted example in the reply.
        wants_key = False
        for token, start, end, depth in _json_structure_tokens(text):
            if token == "{" and depth == 0:
                wants_key = True
            elif token == "," and depth == 1:
                wants_key = True
            elif token == '"' and depth == 1 and wants_key:
                wants_key = False
                try:
                    key = json.loads(text[start:end])
                except ValueError:
                    continue
                if key != "tool_call":
                    continue
                value = text[end:].lstrip()
                if not value.startswith(":"):
                    continue
                value = value[1:].lstrip()
                return not bool(re.match(r"null(?:\s*[,}]|\s*$)", value))
        return False

    def _final_attempt_terminal_output(
        self,
        *,
        normalized: dict[str, Any],
        parse_fallback: bool,
        provider_output_raw: str,
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        client_context: ClientProtocolContext | None,
        resource_manifest: ResourceManifest | None,
        user_message: str,
        domain_profile_id: str,
    ) -> dict[str, Any] | None:
        """Classify one generation attempt as a tool decision or delivery.

        Returns a deliverable normalized dict, or `None` when the attempt
        must be retried with the same-context host feedback. Shared by the
        streaming and non-streaming transports so both surfaces agree on the
        terminal semantics:
        - complete plain text without JSON structure and without a pending
          tool call is wrapped directly as `speech` with default
          presentation fields;
        - a legal tool call continues the ordinary tool loop (never recovery);
        - complete model speech is delivered (parse recovery included);
        Tool availability is controlled only by the caller's explicit
        allow_tool_call decision, never by the fact that this is a retry.
        """
        if self._output_contains_text_tool_protocol(
            normalized,
            provider_output_raw=provider_output_raw,
        ):
            return None
        if not self._final_output_has_tool_call(normalized) and _is_history_reading_record(provider_output_raw):
            return None
        if not self._final_output_has_tool_call(normalized) and self._raw_json_has_external_text(provider_output_raw):
            return None
        if not self._final_output_has_tool_call(normalized) and self._raw_tool_call_is_pending(provider_output_raw):
            # The wire text still asks for a tool call that the extracted object
            # lost. Shipping the preface would silently drop the requested action
            # and leave the reply without its own fields, so this needs one
            # same-context turn instead of a delivery.
            return None
        if not self._final_output_has_tool_call(normalized):
            wrapped = self._wrap_plain_text_final_speech(
                provider_output_raw=provider_output_raw,
                parse_fallback=parse_fallback,
                generation_context=generation_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                domain_profile_id=domain_profile_id,
            )
            if wrapped is not None:
                return wrapped
        if parse_fallback:
            if self._is_deliverable_parse_recovery(
                normalized,
                parse_fallback=True,
                provider_output_raw=provider_output_raw,
            ):
                self._record_final_response_parse_recovery_metric()
                normalized.pop("_provider_fallback", None)
                if provider_output_raw:
                    normalized["_provider_output_raw"] = provider_output_raw
                return normalized
            # The host fallback speech is not a model answer.  Retry unless
            # the provider wire text proves the complete speech above.
            return None
        if self._final_output_has_tool_call(normalized):
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
            return normalized
        if not self._is_retryable_final_output(normalized):
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
            return normalized
        return None

    def _is_deliverable_parse_recovery(
        self,
        output: Any,
        *,
        parse_fallback: bool,
        provider_output_raw: str,
    ) -> bool:
        """Accept only complete model-authored speech from malformed JSON.

        The streaming parser exposes ``speech`` only after its JSON string value
        is closed. We still require proof that no tool call remains pending.
        Disabling tools in the request does not turn a model-authored non-null
        compatibility tool_call into final speech; that preface must be retried
        rather than delivered as if the requested action happened.
        """

        if not parse_fallback or not isinstance(output, dict):
            return False
        if output.get("_deliberate_silence"):
            # A damaged JSON fragment does not prove completion of the compact
            # {"speech":""} decision protocol.
            return False
        if output.get("tool_call") or output.get(NATIVE_TOOL_CALL_FIELD) or output.get(NATIVE_TOOL_CALLS_FIELD):
            return False
        if self._is_retryable_final_output(output):
            # The provider-fallback marker is expected here.  Empty or
            # otherwise unusable speech remains retryable.
            if not output.get("_provider_fallback") or not str(output.get("speech") or "").strip():
                return False
        speech = str(output.get("speech") or "").strip()
        raw = str(provider_output_raw or "")
        encoded_speech = {
            json.dumps(speech, ensure_ascii=False)[1:-1],
            json.dumps(speech, ensure_ascii=True)[1:-1],
        }
        if not raw.strip() or not any(candidate and candidate in raw for candidate in encoded_speech):
            return False
        # A final answer is allowed to omit the optional ``tool_call`` field.
        # Only reject recovery when the wire text explicitly contains a
        # non-null tool call; requiring an explicit ``tool_call: null`` turned
        # ordinary short replies into false "undeliverable" failures.
        return not self._raw_tool_call_is_pending(provider_output_raw)

    @staticmethod
    def _log_final_response_retry(
        *,
        prompt_scope: str,
        attempt: int,
        feedback: str,
        raw_result: Any,
        provider_output_raw: str,
        finish_reason: str = "",
        native_finish_reason: str = "",
    ) -> None:
        raw_keys = sorted(str(key)[:80] for key in raw_result)[:24] if isinstance(raw_result, dict) else []
        raw_text = str(provider_output_raw or "")
        logger.warning(
            "final response unusable prompt_scope=%s attempt=%s reason=%s "
            "raw_type=%s raw_keys=%s raw_chars=%s raw_sha256=%s "
            "finish_reason=%s native_finish_reason=%s",
            str(prompt_scope or "default")[:80],
            attempt,
            feedback,
            type(raw_result).__name__,
            raw_keys,
            len(raw_text),
            hashlib.sha256(raw_text.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw_text else "",
            finish_reason,
            native_finish_reason,
        )

    def _is_retryable_final_output(self, output: Any) -> bool:
        """True when the normalized output carries no deliverable final speech.

        A parse fallback no longer forces a retry by itself: complete model
        speech extracted from malformed wire text is deliverable. Pending tool
        calls are not retryable here either — they continue the ordinary tool
        loop. Retry count alone never changes tool permissions.
        """
        if not isinstance(output, dict):
            return True
        if output.get("_provider_fallback"):
            return True
        if output.get("tool_call") or output.get(NATIVE_TOOL_CALL_FIELD) or output.get(NATIVE_TOOL_CALLS_FIELD):
            return False
        if output.get("_deliberate_silence"):
            return False
        text = str(output.get("speech") or "").strip()
        if not text:
            return True
        if self._text_contains_tool_protocol(text):
            return True
        return False

    @staticmethod
    def _llm_result_used_fallback(
        result: Any,
        *,
        metrics_before: dict[str, Any],
        metrics_after: dict[str, Any],
    ) -> bool:
        explicit = getattr(result, "fallback_used", None)
        if explicit is not None:
            return bool(explicit)
        return int(metrics_after.get("chat_json_fallbacks", 0) or 0) > int(
            metrics_before.get("chat_json_fallbacks", 0) or 0
        )
