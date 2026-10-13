"""Incremental final-response events and exact leading speech replay suppression."""

from __future__ import annotations

import logging
from typing import Any, Generator

from ..model_request_control import ModelRequestCancelled
from .. import tool_orchestration_engine
from ..tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from .final_response_contracts import FinalResponseRequest, FinalResponseStreamAttempt

logger = logging.getLogger("akane.engine.final_response")


class FinalResponseEventMixin:
    """Forward parser events immediately and release their provider iterator."""

    def _forward_final_response_attempt(
        self,
        *,
        request: FinalResponseRequest,
        request_kwargs: dict[str, Any],
        delivered_segments: list[str],
        retry_mode: bool,
    ) -> Generator[dict[str, Any], None, FinalResponseStreamAttempt]:
        context = request.generation_context
        prefix_keys = ["".join(item.split()) for item in delivered_segments]
        prefix_index = 0
        prefix_matching = retry_mode and bool(prefix_keys)
        had_speech_chunk = False
        # Retain only closed display text / committed voice from actual events.
        # The next provider attempt must not erase audio already authored here.
        units: dict[int, dict[str, Any]] = {}
        attempt_segments: list[str] = []
        iterator = self.llm.stream_chat_json(
            **request_kwargs,
            early_tool_call_validator=(
                lambda call: (
                    tool_orchestration_engine.parse_tool_call(
                        self._with_tool_execution_receipt(call, context),
                        capability_selection=context.get(TOOL_CAPABILITY_SELECTION_FIELD),
                    )
                    is not None
                )
            )
            if request.allow_tool_call
            else None,
        )
        try:
            while True:
                try:
                    event = next(iterator)
                except StopIteration as stop:
                    return FinalResponseStreamAttempt(
                        result=stop.value, had_speech_chunk=had_speech_chunk,
                        delivered_utterances=list(units.values()) or [
                            {"text": segment, "say": None, "style": ""} for segment in attempt_segments
                        ],
                        had_speech_units=bool(units),
                    )
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                event_text = str(event.get("text") or "")
                if event_type in {"speech_unit_text", "voice_segment"}:
                    index = event.get("unit_index")
                    if isinstance(index, int) and not isinstance(index, bool):
                        unit = units.setdefault(index, {"text": "", "say": None, "style": ""})
                        if event_type == "speech_unit_text":
                            unit["text"] = event_text
                        else:
                            unit["text"] = str(event.get("display_text") or unit["text"])
                            unit["say"] = str(unit["say"] or "") + event_text
                            unit["style"] = str(event.get("style") or "")
                        if event.get("emotion"):
                            unit["emotion"] = event["emotion"]
                if event_type in {"speech_chunk", "speech_segment"} and self._text_contains_tool_protocol(event_text):
                    continue
                if event_type == "speech_chunk" and event_text:
                    had_speech_chunk = True
                if event_type == "speech_segment":
                    segment = event_text.strip()
                    segment_key = "".join(segment.split())
                    if not segment or self._is_retryable_final_output({"speech": segment, "tool_call": None}):
                        continue
                    if prefix_matching:
                        if prefix_index < len(prefix_keys) and segment_key == prefix_keys[prefix_index]:
                            prefix_index += 1
                            if prefix_index >= len(prefix_keys):
                                prefix_matching = False
                            continue
                        # Once the recovery tail diverges, repeated phrases are
                        # intentional speech rather than replay of the prefix.
                        prefix_matching = False
                    delivered_segments.append(segment)
                    attempt_segments.append(segment)
                yield event
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except ModelRequestCancelled:
                    raise
                except Exception as exc:
                    logger.warning("final response stream close failed error_type=%s", exc.__class__.__name__)
