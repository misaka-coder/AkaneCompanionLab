"""Internal memory calls on a captured chat request, without UI/tool dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import copy

from services import provider_continuation as continuation
from .models import ModelBundle


@dataclass(frozen=True)
class CapturedCompletionRequest:
    bundle: ModelBundle = field(repr=False)
    payload: dict[str, Any] = field(repr=False)
    context_window: int = 0

    @classmethod
    def capture(
        cls, bundle: ModelBundle, payload: dict[str, Any], *, context_window: int = 0
    ) -> "CapturedCompletionRequest":
        return cls(ModelBundle(bundle.client, bundle.model), copy.deepcopy(dict(payload)), context_window)

    def public_messages(self) -> list[dict[str, Any]]:
        return [continuation.public_message(message) for message in self.payload.get("messages", [])]


class MemoryPrefixCallsMixin:
    def capture_memcore_generation_request(self, **kwargs: Any) -> CapturedCompletionRequest:
        observer = kwargs.pop("request_observer", None)
        working_budget = int(kwargs.pop("working_budget_tokens", 0) or 0)
        bundle = self._request_bundle(
            execution_target=kwargs.pop("execution_target", None),
            chat_model_override=kwargs.pop("chat_model_override", ""),
        )
        payload = self._build_completion_kwargs(
            bundle=bundle, json_mode=False, validate_budget=False, observe_request=False, **kwargs
        )
        captured = CapturedCompletionRequest.capture(
            bundle, payload, context_window=self._settings_view().llm_context_window
        )
        if callable(observer):

            def observe(request):
                request.update(
                    _compaction_request=captured, _internal_memory_capture=True, _working_budget_tokens=working_budget
                )
                return observer(request)

            persistent = self._persistent_turn_messages_from_payload(
                payload=payload,
                bundle=bundle,
                history_turns=kwargs.get("history_turns"),
                ephemeral_turns=kwargs.get("ephemeral_turns"),
                post_user_turns=kwargs.get("post_user_turns"),
            )
            self._observe_completion_request(
                bundle=bundle, payload=payload, observer=observe, persistent_turn_messages=persistent
            )
        return captured

    def call_memcore_prefix_json(
        self, *, chat_prefix: Any, contract: str, fallback: dict[str, Any], request_timeout_s: float = 30.0
    ) -> dict[str, Any]:
        snapshot = chat_prefix.snapshot.transport
        if not isinstance(snapshot, CapturedCompletionRequest):
            raise ValueError("memcore_chat_prefix_transport_unavailable")
        payload = dict(snapshot.payload)
        # Preserve route, model, reasoning controls, tool definitions and cache
        # identity. Only the internal output/task controls and tail change.
        payload["messages"] = [
            *snapshot.payload["messages"][: chat_prefix.prefix_end],
            {"role": "user", "content": chat_prefix.instruction(contract)},
        ]
        payload.pop("stream", None)
        payload.pop("stream_options", None)
        if payload.get("tools"):
            payload["tool_choice"] = "none"
        payload["timeout"] = max(0.1, float(request_timeout_s))
        # The automatic maintenance threshold is a soft host budget. Internal
        # compaction can cross it; only the actual model window limits this call.
        window = snapshot.context_window
        if window and self._estimate_prompt_payload_tokens(payload) + int(payload.get("max_tokens") or 0) > window:
            raise ValueError("memcore_prefix_prompt_exceeds_context_window")
        response = self._create_completion(bundle=snapshot.bundle, payload=payload)
        # Internal maintenance is not a new user/chat turn. Keep the same
        # cache identity on the wire without mixing it into chat usage charts.
        self._record_metric("memcore_prefix_calls")
        if self._response_finish_reason(response) in {"tool_calls", "function_call"}:
            raise ValueError("memcore_prefix_unexpected_tool_call")
        if self._response_finish_reason(response) in {"length", "max_tokens"}:
            raise ValueError("memcore_prefix_response_truncated")
        parsed = self._extract_json(self._extract_text(response))
        if not isinstance(parsed, dict) or not parsed or parsed == fallback:
            raise ValueError("memcore_prefix_invalid_json")
        return parsed
