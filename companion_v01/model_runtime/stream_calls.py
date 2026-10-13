"""NDJSON and chat stream lifetimes, terminal results and cancellation cleanup."""

from __future__ import annotations

from typing import Any, Callable, Generator
import json
import logging

from ..model_request_control import ModelRequestCancelled
from ..tool_invocation import (
    NATIVE_REASONING_CONTENT_FIELD,
    NATIVE_TOOL_CALLS_FIELD,
    NATIVE_TOOL_CALL_FIELD,
)
from .json_parsing import _memory_metadata_truth
from .json_stream import _TopLevelJSONStreamTap
from .models import ChatJSONStreamResult, ModelBundle, NDJSONCallResult
from services import provider_continuation as continuation


logger = logging.getLogger("akane.llm_runtime")


class StreamCallsMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _record_stream_cache_metrics(self, response, usage_chunk, *, prompt_cache_key, model):
        # Usage is cumulative: one request contributes one final observation,
        # even when both the stream wrapper and multiple chunks expose it.
        if response is None:
            return
        source = response if getattr(response, "usage", None) is not None else usage_chunk
        usage = source.get("usage") if isinstance(source, dict) else getattr(source, "usage", None)
        resolved_model = source.get("model") if isinstance(source, dict) else getattr(source, "model", None)
        self._record_cache_metrics(
            {"model": resolved_model or model, "usage": usage}, prompt_cache_key=prompt_cache_key,
            requested_model=model)

    def _call_ndjson(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        on_event: Callable[[dict[str, Any]], bool] | None,
        temperature: float,
        prompt_cache_key: str,
    ) -> NDJSONCallResult:
        import time

        events: list[dict[str, Any]] = []
        event_timings: list[dict[str, Any]] = []
        response: Any = None
        usage_chunk: Any = None
        buffer = ""
        start_at = time.perf_counter()
        stopped_early = False
        stop_event_type = ""
        error = ""
        try:
            response = self._create_completion(
                bundle=bundle,
                payload=self._build_completion_kwargs(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    stream=True,
                    prompt_cache_key=prompt_cache_key,
                ),
            )
            for chunk in response:
                if (chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)) is not None:
                    usage_chunk = chunk
                text = self._extract_stream_text(chunk)
                if not text:
                    continue
                buffer += text
                buffer, parsed_events = self._drain_ndjson_buffer(buffer)
                for event in parsed_events:
                    events.append(event)
                    event_type = str(event.get("type") or "").strip().lower()
                    event_timings.append(
                        {
                            "index": len(events) - 1,
                            "type": event_type or "unknown",
                            "elapsed_ms": round((time.perf_counter() - start_at) * 1000, 1),
                        }
                    )
                    if on_event and on_event(event):
                        stopped_early = True
                        stop_event_type = event_type or "unknown"
                        break
                if stopped_early:
                    break

            tail_event = self._parse_ndjson_line(buffer) if not stopped_early else None
            if tail_event is not None:
                events.append(tail_event)
                event_type = str(tail_event.get("type") or "").strip().lower()
                event_timings.append(
                    {
                        "index": len(events) - 1,
                        "type": event_type or "unknown",
                        "elapsed_ms": round((time.perf_counter() - start_at) * 1000, 1),
                    }
                )
                if on_event:
                    if on_event(tail_event):
                        stopped_early = True
                        stop_event_type = event_type or "unknown"
        except ModelRequestCancelled:
            raise
        except Exception as exc:
            error = str(exc or "").strip()
            self._record_metric("errors")
            self._capture_runtime_error(exc, phase="call_ndjson")
        finally:
            self._record_stream_cache_metrics(response, usage_chunk, prompt_cache_key=prompt_cache_key,
                                              model=getattr(bundle, "model", ""))
            self._close_stream(response)
        elapsed_ms = round((time.perf_counter() - start_at) * 1000, 1)
        return NDJSONCallResult(
            events=events,
            event_timings=event_timings,
            elapsed_ms=elapsed_ms,
            stopped_early=stopped_early,
            completed_stream=(not stopped_early and not error),
            stop_event_type=stop_event_type,
            error=error,
        )

    def _stream_chat_json(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float,
        early_tool_call_validator: Callable[[dict[str, Any]], bool] | None,
        prompt_cache_key: str,
        user_images: list[dict[str, Any]] | None = None,
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        request_observer: Callable[[dict[str, Any]], Any] | None = None,
        force_response_json: bool = True,
    ) -> Generator[dict[str, Any], None, ChatJSONStreamResult]:
        import time

        native_requested = bool(self._normalize_native_tools(native_tools))
        response: Any = None
        error = ""
        raw_parts: list[str] = []
        reasoning_parts: list[str] = []
        native_tool_parts: dict[Any, dict[str, Any]] = {}
        provider_extras: dict = {}
        tap = _TopLevelJSONStreamTap()
        start_at = time.perf_counter()
        stopped_early = False
        early_tool_call: dict[str, Any] | None = None
        tool_probe_disabled = False
        stream_finish_reason = ""
        stream_native_finish_reason = ""
        last_stream_chunk: Any = None
        usage_chunk: Any = None
        request_payload: dict[str, Any] = {}
        request_started_at: float | None = None
        first_chunk_at: float | None = None
        first_reasoning_at: float | None = None
        first_text_at: float | None = None
        cancelled = False
        try:
            request_payload = self._build_completion_kwargs(
                bundle=bundle,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                stream=True,
                json_mode=force_response_json,
                prompt_cache_key=prompt_cache_key,
                user_images=user_images,
                native_tools=native_tools,
                native_tool_choice=native_tool_choice,
                system_extra_blocks=system_extra_blocks,
                history_turns=history_turns,
                ephemeral_turns=ephemeral_turns,
                post_user_turns=post_user_turns,
                prompt_audit_sections=prompt_audit_sections,
            )
            self._observe_completion_request(
                bundle=bundle,
                payload=request_payload,
                observer=request_observer,
                persistent_turn_messages=self._persistent_turn_messages_from_payload(
                    payload=request_payload,
                    bundle=bundle,
                    history_turns=history_turns,
                    ephemeral_turns=ephemeral_turns,
                    post_user_turns=post_user_turns,
                ),
            )
            request_started_at = time.perf_counter()
            response = self._create_completion(bundle=bundle, payload=request_payload)
            for chunk in response:
                if first_chunk_at is None:
                    first_chunk_at = time.perf_counter()
                last_stream_chunk = chunk
                chunk_finish_reason = self._response_finish_reason(chunk)
                if chunk_finish_reason:
                    stream_finish_reason = chunk_finish_reason
                chunk_native_finish_reason = self._response_finish_reason(chunk, native=True)
                if chunk_native_finish_reason:
                    stream_native_finish_reason = chunk_native_finish_reason
                if (chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)) is not None:
                    usage_chunk = chunk
                self._collect_stream_native_tool_call_parts(chunk, native_tool_parts, bundle=bundle)
                continuation.collect_chat_extras(chunk, provider_extras)
                reasoning_delta = self._extract_stream_reasoning_content(chunk)
                if reasoning_delta:
                    if first_reasoning_at is None:
                        first_reasoning_at = time.perf_counter()
                    reasoning_parts.append(reasoning_delta)
                text = self._extract_stream_text(chunk)
                if not text:
                    continue
                if first_text_at is None:
                    first_text_at = time.perf_counter()
                raw_parts.append(text)
                for event in tap.feed(text):
                    yield event
                if not tool_probe_disabled:
                    probe_state, probe_call = self._try_extract_stream_tool_call("".join(raw_parts))
                    if probe_state == "object" and isinstance(probe_call, dict):
                        if early_tool_call_validator is None or early_tool_call_validator(probe_call):
                            early_tool_call = dict(probe_call)
                            stopped_early = True
                            break
                        tool_probe_disabled = True
                    elif probe_state in {"null", "none"}:
                        tool_probe_disabled = True
                if stopped_early:
                    break
        except ModelRequestCancelled:
            cancelled = True
            raise
        except GeneratorExit:
            cancelled = True
            raise
        except Exception as exc:
            error = str(exc or "").strip()
            self._record_metric("errors")
            self._capture_runtime_error(exc, phase="stream_chat_json")
        finally:
            if request_started_at is not None:
                def latency(at: float | None) -> float | None:
                    return round((at - request_started_at) * 1000, 1) if at is not None else None

                controls = request_payload.get("extra_body") or {}
                logger.info(
                    "llm_stream_timing model=%s role=%s scope=%s thinking=%s effort=%s "
                    "first_chunk_ms=%s first_reasoning_ms=%s first_text_ms=%s "
                    "stream_ms=%s reasoning_chars=%s text_chars=%s status=%s",
                    getattr(bundle, "model", ""),
                    getattr(getattr(bundle, "client", None), "_akane_bundle_role", ""),
                    prompt_cache_key,
                    (controls.get("thinking") or {}).get("type", "default"),
                    controls.get("reasoning_effort") or (request_payload.get("reasoning") or {}).get("effort", "default"),
                    latency(first_chunk_at), latency(first_reasoning_at), latency(first_text_at),
                    latency(time.perf_counter()), sum(map(len, reasoning_parts)), sum(map(len, raw_parts)),
                    "cancelled" if cancelled else "failed" if error else "early_tool" if stopped_early
                    else "truncated" if stream_finish_reason.lower() == "length" else "completed",
                )
            self._record_stream_cache_metrics(response, usage_chunk, prompt_cache_key=prompt_cache_key,
                                              model=getattr(bundle, "model", ""))
            self._close_stream(response)

        if not stopped_early and stream_finish_reason.lower() == "length":
            self._note_truncation(last_stream_chunk, phase="stream_chat_json")
            if not error:
                error = "response_truncated"

        raw_text = "".join(raw_parts)
        native_tool_calls = self._stream_native_tool_calls_from_parts(
            native_tool_parts,
            native_tools=native_tools,
            bundle=bundle,
        )
        if error:
            # An incomplete stream may omit the final signature or arguments.
            # Never execute the partial batch or turn it into a fresh decision.
            native_tool_calls = []
        for event in tap.finish(include_incomplete_remainder=not bool(native_tool_calls)):
            yield event

        metadata_origin = "accepted_model"
        metadata_requires_signal = False
        fallback_used = False
        if native_tool_calls:
            self._record_metric("native_tool_call_extracted")
            parsed = {
                NATIVE_TOOL_CALLS_FIELD: native_tool_calls,
                NATIVE_TOOL_CALL_FIELD: native_tool_calls[0],
                "tool_call": None,
            }
            native_preface_text = tap.delivered_speech or self._native_preface_text_from_content(raw_text)
            if native_preface_text:
                parsed["speech"] = native_preface_text
            reasoning_content = "".join(reasoning_parts).strip()
            if reasoning_content:
                parsed[NATIVE_REASONING_CONTENT_FIELD] = reasoning_content
            state = continuation.capture(response, bundle=bundle, calls=native_tool_calls,
                                         extras=continuation.chat_extras(provider_extras))
            if state and not error:
                parsed[continuation.RESULT_FIELD] = state
        elif native_requested:
            self._record_metric("native_tool_no_call")
            parsed = self._extract_json(raw_text)
        elif early_tool_call is not None:
            parsed = {"tool_call": early_tool_call}
        else:
            parsed = self._extract_json(raw_text)
        if not isinstance(parsed, dict):
            recovered = self._recover_partial_chat_json(raw_text, fallback=fallback)
            if isinstance(recovered, dict):
                parsed = recovered
                metadata_origin = "accepted_host"
                metadata_requires_signal = True
            else:
                self._record_metric("chat_json_fallbacks")
                provider_finish_failure = not error and self._note_provider_finish_failure(
                    phase="stream_chat_json",
                    finish_reason=stream_finish_reason,
                    native_finish_reason=stream_native_finish_reason,
                )
                if not provider_finish_failure:
                    self._note_parse_fallback(raw_text, phase="stream_chat_json")
                parsed = dict(fallback)
                metadata_origin = "accepted_host"
                metadata_requires_signal = True
                fallback_used = True
        else:
            parsed = dict(parsed)

        if tap._units_stream.active:
            from ..speech_units import project_speech_units
            # Preserve metadata actually used by the streaming voice queue.
            parsed = project_speech_units({**parsed, "utterances": tap._units_stream.snapshot_units()})
        if tap.latest_emotion and not parsed.get("emotion"):
            parsed["emotion"] = tap.latest_emotion
        accepted_stream_speech = tap.delivered_speech if native_tool_calls else tap.latest_speech
        if accepted_stream_speech and not parsed.get("speech"):
            parsed["speech"] = accepted_stream_speech

        elapsed_ms = round((time.perf_counter() - start_at) * 1000, 1)
        metadata_status, metadata_present = _memory_metadata_truth(
            parsed,
            accepted_status=metadata_origin,
            require_signal=metadata_requires_signal,
        )
        return ChatJSONStreamResult(
            parsed=parsed,
            raw_text=raw_text,
            elapsed_ms=elapsed_ms,
            error=error,
            latest_emotion=tap.latest_emotion,
            latest_speech=tap.latest_speech,
            native_preface_text=native_preface_text if native_tool_calls else "",
            stopped_early=stopped_early,
            early_tool_call=early_tool_call,
            fallback_used=fallback_used,
            metadata_status=metadata_status,
            metadata_present=metadata_present,
            finish_reason=stream_finish_reason,
            native_finish_reason=stream_native_finish_reason,
        )

    def _drain_ndjson_buffer(self, buffer: str) -> tuple[str, list[dict[str, Any]]]:
        remaining = str(buffer or "")
        events: list[dict[str, Any]] = []
        while True:
            newline_index = remaining.find("\n")
            if newline_index < 0:
                break
            line = remaining[:newline_index]
            remaining = remaining[newline_index + 1 :]
            parsed = self._parse_ndjson_line(line)
            if parsed is not None:
                events.append(parsed)
        return remaining, events

    def _parse_ndjson_line(self, line: Any) -> dict[str, Any] | None:
        raw = str(line or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _close_stream(self, response: Any) -> None:
        if response is None:
            return
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:
                pass
        raw_response = getattr(response, "_response", None)
        close = getattr(raw_response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
