"""Responses wire conversion and incremental provider event adaptation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import re

from services import provider_continuation as continuation
from services.tool_history import isolate_tool_history
import config


def _responses_error_summary(value: Any) -> str:
    """Return bounded provider failure metadata without echoing raw payloads."""

    def _get(item: Any, key: str, default: Any = None) -> Any:
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

    nested = _get(value, "error")
    error = nested if nested is not None else value
    fields: list[str] = []
    for key in ("type", "code"):
        raw = str(_get(error, key, "") or "").strip()
        if raw:
            safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)[:120]
            fields.append(f"{key}={safe}")
    return " ".join(fields) or "reason=provider_reported_failure"


class _ResponsesStreamAdapter:
    """Expose Responses streaming events through the small ChatCompletion
    surface consumed by the existing Akane stream parser.

    This keeps the rest of the engine provider-neutral while still preserving
    function-call ids, incremental arguments and final usage telemetry.
    """

    def __init__(self, raw_stream: Any):
        self._raw_stream = raw_stream
        self.usage: Any = None
        self._tool_indexes: dict[str, int] = {}
        self._argument_deltas_seen: set[int] = set()
        self.provider_content: list[dict[str, Any]] = []
        self._completed_items: dict[int, dict[str, Any]] = {}

    @staticmethod
    def _get(value: Any, key: str, default: Any = None) -> Any:
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)

    def _tool_index(self, event: Any, item: Any = None) -> int:
        raw = self._get(event, "output_index", None)
        try:
            return int(raw)
        except Exception:
            pass
        identity = str(
            self._get(item, "call_id", "") or self._get(item, "id", "") or self._get(event, "item_id", "")
        ).strip()
        if identity not in self._tool_indexes:
            self._tool_indexes[identity] = len(self._tool_indexes)
        return self._tool_indexes[identity]

    @staticmethod
    def _chunk(
        *,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        usage: Any = None,
        finish_reason: str | None = None,
    ) -> Any:
        delta = SimpleNamespace(content=content or None, tool_calls=tool_calls or None)
        choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(choices=[choice], usage=usage)

    def __iter__(self):
        for event in self._raw_stream:
            event_type = str(self._get(event, "type", "") or "")
            if event_type == "response.output_text.delta":
                delta = str(self._get(event, "delta", "") or "")
                if delta:
                    yield self._chunk(content=delta)
                continue
            if event_type == "response.output_item.added":
                item = self._get(event, "item")
                if str(self._get(item, "type", "") or "") != "function_call":
                    continue
                index = self._tool_index(event, item)
                call_id = str(self._get(item, "call_id", "") or self._get(item, "id", "") or "")
                name = str(self._get(item, "name", "") or "")
                yield self._chunk(
                    tool_calls=[
                        {
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": ""},
                        }
                    ]
                )
                continue
            if event_type == "response.function_call_arguments.delta":
                index = self._tool_index(event)
                delta = str(self._get(event, "delta", "") or "")
                if delta:
                    self._argument_deltas_seen.add(index)
                    yield self._chunk(
                        tool_calls=[
                            {
                                "index": index,
                                "type": "function",
                                "function": {"arguments": delta},
                            }
                        ]
                    )
                continue
            if event_type == "response.output_item.done":
                item = self._get(event, "item")
                self._completed_items[self._tool_index(event, item)] = continuation.plain(item)
                self.provider_content = [self._completed_items[k] for k in sorted(self._completed_items)]
                if str(self._get(item, "type", "") or "") != "function_call":
                    continue
                index = self._tool_index(event, item)
                arguments = str(self._get(item, "arguments", "") or "")
                if arguments and index not in self._argument_deltas_seen:
                    yield self._chunk(
                        tool_calls=[
                            {
                                "index": index,
                                "type": "function",
                                "function": {"arguments": arguments},
                            }
                        ]
                    )
                continue
            if event_type in {"error", "response.failed", "response.cancelled"}:
                response = self._get(event, "response")
                detail = response if response is not None else event
                raise RuntimeError(f"responses_stream_failed {_responses_error_summary(detail)}")
            if event_type in {"response.completed", "response.incomplete"}:
                response = self._get(event, "response")
                output = self._get(response, "output")
                if output:
                    self.provider_content = continuation.plain(output)
                self.usage = self._get(response, "usage")
                finish_reason = "length" if event_type == "response.incomplete" else "stop"
                yield self._chunk(usage=self.usage, finish_reason=finish_reason)
                # The terminal chunk already carried this Responses usage.
                # Clear the adapter copy so the enclosing stream-finalizer does
                # not count the same provider report a second time.
                self.usage = None

    def close(self) -> None:
        close = getattr(self._raw_stream, "close", None)
        if callable(close):
            close()


class ResponsesProtocolMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _responses_payload_from_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(payload)
        messages = list(request.pop("messages", []) or [])
        request.pop("stream_options", None)
        max_tokens = int(request.pop("max_tokens", 0) or 0)
        if max_tokens > 0 and not request.get("max_output_tokens"):
            request["max_output_tokens"] = max_tokens
        system_text = ""
        if messages and str(messages[0].get("role") or "").strip().lower() == "system":
            system_text = self._flatten_message_content(messages.pop(0).get("content")).strip()
        if system_text:
            request["instructions"] = system_text
        request["input"] = self._responses_input_from_messages(messages)
        response_format = request.pop("response_format", None)
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            request["text"] = {"format": {"type": "json_object"}}
        tools = request.get("tools")
        if isinstance(tools, list):
            request["tools"] = self._responses_tools_from_chat(tools)
            request["parallel_tool_calls"] = True
        tool_choice = request.get("tool_choice")
        if isinstance(tool_choice, dict):
            function = tool_choice.get("function")
            if isinstance(function, dict) and str(function.get("name") or "").strip():
                request["tool_choice"] = {"type": "function", "name": str(function["name"]).strip()}
        request["store"] = not bool(getattr(config, "LLM_DISABLE_RESPONSE_STORAGE", True))
        if request.get("prompt_cache_retention") == "in_memory":
            request["prompt_cache_retention"] = "in-memory"
        return request

    def _responses_input_from_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = isolate_tool_history(messages).messages
        items: list[dict[str, Any]] = []

        def append_plain_message(role: str, content: Any) -> None:
            items.append({"role": role, "content": content})

        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                saved_output = continuation.wire_data(message, "responses")
                if saved_output:
                    items.extend(saved_output)
                    continue
                content = self._flatten_message_content(message.get("content")).strip()
                if content:
                    items.append({"role": "assistant", "content": content})
                for call in self._normalize_openai_history_tool_calls(message.get("tool_calls")):
                    function = call["function"]
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call["id"],
                            "name": function["name"],
                            "arguments": function["arguments"],
                        }
                    )
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if call_id:
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": self._flatten_message_content(message.get("content")),
                        }
                    )
                continue
            if role not in {"user", "assistant", "developer", "system"}:
                continue
            content = message.get("content")
            append_plain_message(role, self._responses_message_content(content))
        return items

    def _responses_message_content(self, content: Any) -> Any:
        if not isinstance(content, list):
            return str(content or "")
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                blocks.append({"type": "input_text", "text": str(block.get("text") or "")})
            elif block_type == "image_url":
                image_url = block.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if str(url or "").startswith("data:image/"):
                    blocks.append({"type": "input_image", "image_url": str(url)})
        return blocks or self._flatten_message_content(content)

    def _responses_tools_from_chat(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            item = {
                "type": "function",
                "name": str(function.get("name") or ""),
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") or {"type": "object", "additionalProperties": True},
            }
            if "strict" in function:
                item["strict"] = bool(function.get("strict"))
            result.append(item)
        return result

    def _adapt_responses_result(self, response: Any) -> Any:
        status = str(self._get_attr_or_key(response, "status") or "").strip().lower()
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"responses_{status} {_responses_error_summary(response)}")
        output = self._get_attr_or_key(response, "output") or []
        tool_calls: list[dict[str, Any]] = []
        text_parts: list[str] = []
        direct_text = str(self._get_attr_or_key(response, "output_text") or "")
        if direct_text:
            text_parts.append(direct_text)
        for item in output:
            item_type = str(self._get_attr_or_key(item, "type") or "")
            if item_type == "function_call":
                call_id = str(self._get_attr_or_key(item, "call_id") or self._get_attr_or_key(item, "id") or "")
                tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": str(self._get_attr_or_key(item, "name") or ""),
                            "arguments": str(self._get_attr_or_key(item, "arguments") or "{}"),
                        },
                    }
                )
            elif item_type == "message" and not direct_text:
                for block in self._get_attr_or_key(item, "content") or []:
                    if str(self._get_attr_or_key(block, "type") or "") == "output_text":
                        text_parts.append(str(self._get_attr_or_key(block, "text") or ""))
        message = SimpleNamespace(content="".join(text_parts), tool_calls=tool_calls)
        choice = SimpleNamespace(message=message, finish_reason="length" if status == "incomplete" else "stop")
        return SimpleNamespace(choices=[choice], usage=self._get_attr_or_key(response, "usage"), raw_response=response,
                               provider_content=continuation.plain(output))
