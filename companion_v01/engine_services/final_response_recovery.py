"""Model-authored plain-text recovery and already-delivered speech handling."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..client_protocol import ClientProtocolContext
from ..model_request_control import ModelRequestCancelled
from ..resource_manifest import ResourceManifest
from ..speech_units import normalize_unit_emotion, normalize_utterances, project_speech_units
from ..tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from .final_response_constants import (
    FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK,
    FINAL_RESPONSE_RECOVERY_FEEDBACK,
    _JSON_FRAGMENT_RE,
)
from .final_response_policy import _is_history_reading_record

logger = logging.getLogger("akane.engine.final_response")


class FinalResponseRecoveryMixin:
    """Recover model text without fabricating a successful response."""

    def _wrap_plain_text_final_speech(
        self,
        *,
        provider_output_raw: str,
        parse_fallback: bool,
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        client_context: ClientProtocolContext | None,
        resource_manifest: ResourceManifest | None,
        user_message: str,
        domain_profile_id: str,
    ) -> dict[str, Any] | None:
        """Wrap complete plain-text provider output as the final speech.

        Only when the wire text is complete ordinary text with no JSON
        structure (and therefore no pending tool call) is the model's own text
        packaged as `speech`; presentation fields use the defaults. Damaged
        JSON fragments and copied history records go to same-context
        regeneration instead.
        """
        raw = str(provider_output_raw or "").strip()
        if not raw or not parse_fallback or _JSON_FRAGMENT_RE.search(raw):
            return None
        if _is_history_reading_record(raw):
            return None
        if self._text_contains_tool_protocol(raw):
            return None
        text = raw
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        if not text:
            return None
        wrapped = self._normalize_final_output(
            result={"speech": text},
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            resource_manifest=resource_manifest,
            allow_tool_call=False,
            debug_enabled=bool(generation_context["debug_enabled"]),
            user_message=user_message,
            domain_profile_id=domain_profile_id,
            capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
            allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
        )
        wrapped["_provider_output_raw"] = raw
        wrapped["_final_recovery"] = {"kind": "plain_text_wrap"}
        # The model authored only the text on this recovery path. Keep the
        # normalized emotion for clients that require a visual state, but mark
        # it as a presentation default so QQ does not publish it as if the
        # model had deliberately chosen that semantic expression.
        wrapped["_emotion_model_authored"] = False
        return wrapped

    def _recover_final_response_plain_text(
        self,
        *,
        generation_context: dict[str, Any],
        request_projection_state: dict[str, Any] | None,
        user_images: list[dict[str, Any]] | None,
        chat_model_override: str,
        execution_target: Any,
        request_observer: Any,
        prompt_cache_key: str,
        profile_user_id: str,
        session_id: str,
        client_context: ClientProtocolContext | None,
        resource_manifest: ResourceManifest | None,
        user_message: str,
        domain_profile_id: str,
        delivered_speech_segments: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """One same-context plain-text generation after structured failures.

        Keep the task's tool schemas with calls disabled and no JSON
        requirement. The model authors text; the host wraps presentation
        defaults. Returns `None` when the provider is unavailable, lacks this
        capability, or still produced no usable text.
        """
        call = getattr(self.llm, "call_chat_text", None)
        if not callable(call):
            return None
        note = self._stream_final_response_recovery_feedback(
            delivered_speech_segments,
            plain_text=True,
        )
        try:
            request_kwargs = self._build_final_response_base_request_kwargs(
                generation_context=generation_context,
                request_projection_state=request_projection_state,
                user_images=user_images,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                prompt_cache_key=prompt_cache_key,
                retry_note=note,
                request_observer=request_observer,
            )
            # Preserve the text transport's explicit optional observer argument.
            request_kwargs["request_observer"] = request_observer
            request_kwargs["native_tools"] = generation_context.get("native_tools")
            result = call(**request_kwargs)
        except ModelRequestCancelled:
            raise
        except Exception as exc:
            logger.warning(
                "final response plain text recovery failed error_type=%s",
                exc.__class__.__name__,
            )
            return None
        speech = self._extract_final_plain_text_speech(getattr(result, "text", ""))
        if not speech or self._text_contains_tool_protocol(speech):
            return None
        normalized = self._normalize_final_output(
            result={"speech": speech},
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            resource_manifest=resource_manifest,
            allow_tool_call=False,
            debug_enabled=bool(generation_context["debug_enabled"]),
            user_message=user_message,
            domain_profile_id=domain_profile_id,
            capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
            allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
        )
        raw_text = str(getattr(result, "raw_text", "") or "") or speech
        error = str(getattr(result, "error", "") or "").strip()
        if raw_text:
            normalized["_provider_output_raw"] = raw_text
        normalized["_final_recovery"] = {
            "kind": "plain_text",
            **({"error": error} if error else {}),
        }
        # call_chat_text has no structured emotion channel. The normalizer
        # supplies a presentation default, which must not be mistaken for a
        # model-authored semantic expression by delivery surfaces.
        normalized["_emotion_model_authored"] = False
        self._record_final_plain_text_recovery_metric()
        return normalized

    @staticmethod
    def _stream_final_response_recovery_feedback(
        delivered_segments: list[str] | None,
        *,
        plain_text: bool = False,
    ) -> str:
        segments = [str(item or "").strip() for item in (delivered_segments or []) if str(item or "").strip()]
        if not segments:
            return FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK if plain_text else FINAL_RESPONSE_RECOVERY_FEEDBACK
        delivered = "\n".join(f"- {item}" for item in segments)
        feedback = (
            "【宿主反馈】\n"
            "上一条输出没有形成有效的工具调用或完整交付，但以下完整句子已经提交到本轮交付通道，"
            "用户可能已经看到或听到，不能安全撤回。"
            "不要重复、改写或否认它们。\n"
            "请基于同一用户请求和已经存在的工具结果继续：仍需操作就发出合法工具调用，"
            "任务已完成就只补充尚未交付的剩余答复；不要重复已完成的工具，最终回复沿用当前客户端的字段说明，"
            "不要讨论这次格式或传输错误。\n"
            f"【已交付句子】\n{delivered}"
        )
        if plain_text:
            feedback += "\n本次请直接输出纯文本剩余答复：不要输出 JSON，不要调用任何工具。"
        return feedback

    def _merge_streamed_speech_into_final(
        self,
        output: dict[str, Any],
        *,
        delivered_segments: list[str],
        delivered_utterances: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Rebuild canonical speech from its delivered prefix and recovery tail.

        ``delivered_segments`` are parser-derived boundaries from the model's
        earlier ``speech`` value, not an independent content authority.
        """
        segments = [str(item or "").strip() for item in delivered_segments if str(item or "").strip()]
        if not self._final_output_has_tool_call(output) and (delivered_utterances or (
            segments and isinstance(output.get("utterances"), list)
        )):
            prefix = normalize_utterances(delivered_utterances)
            if not prefix:
                # Legacy display-only prefixes have no evidence of a translation.
                prefix = normalize_utterances([{"text": "\n".join(segments)}])
            tail = normalize_utterances(output.get("utterances"))
            if not tail and str(output.get("speech") or "").strip():
                tail = normalize_utterances([{"text": output["speech"]}])
            emotion = normalize_unit_emotion(output.get("emotion"))
            if tail and emotion and not tail[0].get("emotion"):
                tail[0]["emotion"] = emotion
            units = self._merge_delivered_utterances(prefix, tail)
            normalized = project_speech_units({**output, "utterances": units})
            speech, speech_segments = self._normalize_speech_payload(
                speech=normalized["speech"], fallback_to_default=False,
            )
            normalized["speech"] = speech
            normalized["speech_segments"] = speech_segments
            return normalized
        if not segments:
            return output
        normalized = dict(output or {})
        final_speech = str(normalized.get("speech") or "").strip()
        delivered_text = "\n".join(segments)
        compact_delivered = "".join(delivered_text.split())
        compact_final = "".join(final_speech.split())
        if compact_delivered and compact_final.startswith(compact_delivered):
            return normalized
        merged_text = "\n".join([delivered_text, final_speech] if final_speech else [delivered_text])
        speech, speech_segments = self._normalize_speech_payload(
            speech=merged_text,
            fallback_to_default=False,
        )
        normalized["speech"] = speech
        normalized["speech_segments"] = speech_segments
        return normalized

    @staticmethod
    def _merge_delivered_utterances(prefix, tail):
        """Keep positional replay once, including its original voice metadata."""
        merged = [dict(unit) for unit in normalize_utterances(prefix)]
        units = normalize_utterances(tail)
        replayed = 0
        for old, new in zip(merged, units):
            if "".join(old["text"].split()) != "".join(new["text"].split()):
                break
            # A retry may complete a voice string whose committed prefix was
            # already played. Only extend that prefix; don't replace its tone.
            if new.get("say") and (not old.get("say") or new["say"].startswith(old["say"])):
                old["say"] = new["say"]
                if not old.get("style"):
                    old["style"] = new["style"]
            replayed += 1
        return merged + units[replayed:]

    @staticmethod
    def _extract_final_plain_text_speech(text: Any) -> str:
        """Best-effort speech extraction from the plain-text recovery response.

        Accepts the whole text when it carries no JSON structure; a stray JSON
        object with a usable `speech` string is also honored. Anything that
        still looks like damaged JSON is rejected so the host never fabricates
        or mangles an answer.
        """
        candidate = str(text or "").strip()
        if not candidate:
            return ""
        if _is_history_reading_record(candidate):
            return ""
        if candidate.startswith("```") and candidate.endswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3:
                candidate = "\n".join(lines[1:-1]).strip()
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            speech = str(parsed.get("speech") or "").strip()
            if speech:
                return speech
        if _JSON_FRAGMENT_RE.search(candidate):
            return ""
        return candidate
