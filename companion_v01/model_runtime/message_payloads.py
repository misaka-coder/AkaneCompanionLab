"""Completion messages, image inputs, history normalization and token guards."""

from __future__ import annotations

from typing import Any
import json
import logging
import re

from ..engine_services.turn_context import build_desktop_screen_image_label
from ..model_image_inputs import model_image_blocks, model_image_source_label
from .models import ModelBundle, _CompletionPayload
from services import provider_continuation as continuation
from services.gemini_native_client import sanitize_openai_tools_for_gemini
from services.tool_history import isolate_tool_history


logger = logging.getLogger("akane.llm_runtime")


class MessagePayloadsMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _flatten_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text is None:
                        text = item.get("content")
                    if text is None and isinstance(item.get("input_text"), str):
                        text = item.get("input_text")
                else:
                    text = getattr(item, "text", None)
                    if text is None:
                        text = getattr(item, "content", None)
                if text is not None:
                    parts.append(str(text))
            return "".join(parts)
        return str(content or "")

    def _build_completion_kwargs(
        self,
        *,
        bundle: ModelBundle,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        stream: bool = False,
        json_mode: bool = False,
        prompt_cache_key: str = "",
        user_images: list[dict[str, Any]] | None = None,
        native_tools: list[dict[str, Any]] | None = None,
        native_tool_choice: Any = "",
        system_extra_blocks: list[str] | None = None,
        history_turns: list[dict[str, Any]] | None = None,
        ephemeral_turns: list[dict[str, Any]] | None = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_audit_sections: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 0,
        request_timeout_s: float = 0.0,
        validate_budget: bool = True,
        observe_request: bool = True,
    ) -> dict[str, Any]:
        user_content: str | list[dict[str, Any]]
        image_items = self._normalize_user_image_items(user_images)
        if image_items:
            user_content = [{"type": "text", "text": user_prompt}, *image_items]
        else:
            user_content = user_prompt
        filtered_system_extra_blocks = self._normalize_system_extra_blocks(system_extra_blocks)
        effective_system_prompt = str(system_prompt or "")
        if filtered_system_extra_blocks and not self._is_anthropic_protocol(bundle):
            effective_system_prompt = "\n\n".join(
                part for part in [effective_system_prompt.strip(), *filtered_system_extra_blocks] if part
            )
        messages: list[dict[str, Any]] = [{"role": "system", "content": effective_system_prompt}]
        messages.extend(self._normalize_history_turns_for_payload(history_turns, bundle=bundle))
        current_message_index = len(messages)
        messages.append({"role": "user", "content": user_content})
        ephemeral_messages, tail_messages = self._split_ephemeral_turns_for_payload(ephemeral_turns, bundle=bundle)
        messages.extend(ephemeral_messages)
        post_user_start = len(messages)
        for turn in post_user_turns or []:
            normalized_turn = self._normalize_post_user_turn_for_payload(turn, bundle=bundle)
            if normalized_turn is not None:
                messages.append(normalized_turn)
        post_user_end = len(messages)
        messages.extend(tail_messages)
        validation = isolate_tool_history(messages)
        messages = validation.messages
        if validation.issues and observe_request:
            self._record_metric("tool_history_isolated", len(validation.issues))
            logger.warning("tool history isolated: issues=%s reasons=%s", len(validation.issues),
                           sorted({item["reason"] for item in validation.issues}))
        payload = _CompletionPayload({
            "model": bundle.model,
            "messages": messages,
        })
        payload.persistent_message_indexes = tuple(
            index for index, original in enumerate(validation.source_indexes)
            if original == current_message_index or post_user_start <= original < post_user_end
        )
        if float(request_timeout_s or 0.0) > 0:
            payload["timeout"] = float(request_timeout_s)
        if (
            str(getattr(bundle.client, "_akane_bundle_role", "") or "").strip().lower()
            in {"chat", "vision"}
            and self._settings_view().llm_chat_max_output_tokens > 0
        ):
            payload["max_tokens"] = int(self._settings_view().llm_chat_max_output_tokens)
        if int(max_output_tokens or 0) > 0:
            payload["max_tokens"] = int(max_output_tokens)
        # Current OpenAI reasoning models reject sampling controls when explicit
        # reasoning effort is selected. Keep temperature for all legacy paths.
        if not (
            (self._is_responses_protocol(bundle) and self._configured_reasoning_effort(bundle))
            or self._deepseek_thinking_mode(bundle) == "enabled"
        ):
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
            if self._supports_stream_usage(bundle):
                payload["stream_options"] = {"include_usage": True}
        normalized_tools = self._normalize_native_tools(native_tools)
        if normalized_tools and self._is_gemini_family_model(bundle) and not self._is_gemini_protocol(bundle):
            # Native generateContent converts once inside its adapter. Only
            # the OpenAI-compatible route needs this compatibility projection.
            normalized_tools = sanitize_openai_tools_for_gemini(normalized_tools)
        native_tool_profile = self._native_tool_profile(bundle)
        should_send_native_tools = bool(normalized_tools and native_tool_profile.supports_native_tools)
        # Native Agent turns stay on the provider's ordinary text/tool
        # protocol. Main chat also disables forced JSON on tool-free answers;
        # json_mode remains available only to isolated internal JSON jobs.
        should_use_response_json = bool(
            json_mode
            and not should_send_native_tools
            and self._should_use_response_json_mode(bundle)
        )
        if should_use_response_json:
            payload["response_format"] = {"type": "json_object"}
            self._ensure_json_keyword(messages)
        if normalized_tools and should_send_native_tools:
            payload["tools"] = normalized_tools
            if not self._is_anthropic_protocol(bundle):
                payload["parallel_tool_calls"] = True
            if observe_request:
                self._record_metric("native_tool_decision_sent")
            tool_choice = self._normalize_native_tool_choice(native_tool_choice)
            if tool_choice:
                payload["tool_choice"] = tool_choice
        elif normalized_tools and observe_request:
            self._record_metric("native_tool_provider_unsupported")
        payload.update(self._build_reasoning_control_kwargs(bundle=bundle))
        payload.update(
            self._build_prompt_cache_kwargs(
                bundle=bundle,
                prompt_cache_key=prompt_cache_key,
            )
        )
        if filtered_system_extra_blocks and self._is_anthropic_protocol(bundle):
            payload["system_extra_blocks"] = filtered_system_extra_blocks
        if observe_request:
            self._record_prompt_audit_if_enabled(
                bundle=bundle,
                prompt_cache_key=prompt_cache_key,
                request_payload=payload,
                messages=messages,
                system_extra_blocks=filtered_system_extra_blocks if self._is_anthropic_protocol(bundle) else [],
                history_turns=history_turns,
                ephemeral_turns=ephemeral_turns,
                post_user_turns=post_user_turns,
                user_prompt=user_prompt,
                user_image_count=sum(item.get("type") == "image_url" for item in image_items),
                prompt_audit_sections=prompt_audit_sections,
                stream=stream,
                json_mode=json_mode,
                native_tools=normalized_tools if should_send_native_tools else [],
            )
        if validate_budget:
            self._enforce_prompt_token_limits(payload)
        return payload

    def _enforce_prompt_token_limits(self, payload: dict[str, Any]) -> None:
        settings = self._settings_view()
        configured_limits = [
            settings.llm_auto_compact_token_limit,
            settings.llm_context_window,
        ]
        limits = [value for value in configured_limits if value > 0]
        if not limits:
            return
        limit = min(limits)
        estimated = self._estimate_prompt_payload_tokens(payload)
        if estimated > limit:
            self._record_metric("prompt_token_limit_exceeded")
            raise ValueError(f"llm_prompt_token_limit_exceeded estimated={estimated} limit={limit}")

    def _estimate_prompt_payload_tokens(self, payload: dict[str, Any]) -> int:
        """Estimate text and native image input without tokenizing base64 as prose.

        Native image bytes are already bounded by the attachment inbox before
        this payload is built.  Counting their base64 representation as text
        overestimates a normal screenshot by hundreds of thousands of tokens
        and rejects the request before the multimodal provider sees it.  Keep a
        conservative per-image context allowance while estimating every real
        text/schema field as before.
        """

        image_count = 0

        def without_image_bytes(value: Any) -> Any:
            nonlocal image_count
            if isinstance(value, list):
                return [without_image_bytes(item) for item in value]
            if not isinstance(value, dict):
                return value
            if str(value.get("type") or "").strip().lower() == "image_url":
                image_count += 1
                image_url = value.get("image_url")
                detail = image_url.get("detail") if isinstance(image_url, dict) else None
                normalized: dict[str, Any] = {
                    "type": "image_url",
                    "image_url": {"url": "data:image/<native-image>"},
                }
                if detail not in (None, ""):
                    normalized["image_url"]["detail"] = detail
                return normalized
            return {str(key): without_image_bytes(item) for key, item in value.items()}

        estimated_payload = without_image_bytes(
            {
                "messages": payload.get("messages") or [],
                "tools": payload.get("tools") or [],
                "system_extra_blocks": payload.get("system_extra_blocks") or [],
            }
        )
        serialized = json.dumps(
            estimated_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        # Provider image accounting depends on dimensions and detail mode rather
        # than encoded file size.  8K per image is deliberately conservative
        # for the host-side guard while the byte/count limits remain authoritative.
        return self._estimate_prompt_tokens(serialized) + image_count * 8192

    def _normalize_message_content_for_payload(self, content: Any) -> str | list[dict[str, Any]]:
        if isinstance(content, list):
            blocks = [part for item in content if isinstance(item, dict) for part in model_image_blocks(item)]
            return blocks if blocks else self._flatten_message_content(content).strip()
        return str(content or "").strip()

    def _split_ephemeral_turns_for_payload(
        self,
        ephemeral_turns: list[dict[str, Any]] | None,
        *,
        bundle: ModelBundle,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Place host-marked request tails after tools; strip placement from the wire."""
        immediate: list[dict[str, Any]] = []
        tail: list[dict[str, Any]] = []
        for turn in ephemeral_turns or []:
            normalized = self._normalize_post_user_turn_for_payload(turn, bundle=bundle)
            if normalized is not None:
                target = tail if turn.get("_placement") == "tail" else immediate
                target.append(normalized)
        return immediate, tail

    def _normalize_history_turns_for_payload(
        self,
        history_turns: list[dict[str, Any]] | None,
        *,
        bundle: ModelBundle,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn in history_turns or []:
            normalized = self._normalize_post_user_turn_for_payload(turn, bundle=bundle)
            if normalized is not None:
                messages.append(normalized)
        return messages

    def _normalize_post_user_turn_for_payload(
        self,
        turn: Any,
        *,
        bundle: ModelBundle,
    ) -> dict[str, Any] | None:
        if not isinstance(turn, dict):
            return None
        role = str(turn.get("role", "") or "").strip().lower()
        content = self._normalize_message_content_for_payload(turn.get("content"))
        if not self._is_anthropic_protocol(bundle):
            if role == "assistant":
                tool_calls = self._normalize_openai_history_tool_calls(turn.get("tool_calls"))
                if tool_calls:
                    message: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
                    if content:
                        message["content"] = content
                    reasoning_content = str(turn.get("reasoning_content") or "").strip()
                    if reasoning_content and self._deepseek_thinking_mode(bundle) == "enabled":
                        message["reasoning_content"] = reasoning_content
                    return continuation.carry(turn, message, bundle=bundle)
            if role == "tool":
                tool_call_id = str(turn.get("tool_call_id") or "").strip()
                if tool_call_id:
                    return {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": content if isinstance(content, str) else self._flatten_message_content(content),
                    }
        if content and role in {"user", "assistant"}:
            return continuation.carry(turn, {"role": role, "content": content}, bundle=bundle)
        return None

    @staticmethod
    def _normalize_openai_history_tool_calls(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for raw in value:
            if not isinstance(raw, dict):
                continue
            call_id = str(raw.get("id") or "").strip()
            function = raw.get("function")
            if not call_id or not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                continue
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                except (TypeError, ValueError):
                    continue
            normalized.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
        return normalized

    def _normalize_system_extra_blocks(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item or "").strip() for item in value if str(item or "").strip()]

    def _normalize_user_image_items(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(value[:5], 1):
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("data_url") or raw.get("dataUrl") or raw.get("url") or "").strip()
            if not url.startswith("data:image/"):
                continue
            label = build_desktop_screen_image_label(raw, index)
            if label:
                items.append({"type": "text", "text": label})
            source_label = model_image_source_label(raw)
            if source_label:
                items.append({"type": "text", "text": source_label})
            items.extend(model_image_blocks({"type": "image_url", "image_url": {"url": url}}))
        return items
