"""Native tool profiles, wire calls, argument carriers and provider call identities."""

from __future__ import annotations

from typing import Any
import json
import re

from capcore_provider_anthropic import (
    collect_anthropic_messages_stream_tool_use_delta,
    parse_anthropic_messages_stream_tool_uses,
    parse_anthropic_messages_tool_uses,
)
from capcore_provider_openai import (
    collect_openai_chat_stream_tool_call_delta,
    parse_openai_chat_stream_tool_calls,
    parse_openai_chat_tool_calls,
)

from ..native_tool_schema import native_tool_model_name_map
from ..tool_invocation import (
    NATIVE_ANTHROPIC,
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_ARGUMENTS_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_PARSE_ERROR_FIELD,
    TOOL_RAW_ARGUMENTS_FIELD,
    TOOL_SOURCE_FIELD,
)
from .models import ModelBundle, ProviderToolProfile


DEFAULT_PROVIDER_TOOL_PROFILE = ProviderToolProfile()


OPENAI_COMPAT_DEFAULT_PROVIDER_TOOL_PROFILE = ProviderToolProfile(
    supports_native_tools=True,
    verified=False,
    notes=(
        "OpenAI-compatible providers receive native tools optimistically. "
        "An unexpected native-tool rejection is surfaced explicitly instead "
        "of silently removing the model's tool surface mid-turn."
    ),
)


CONFIG_ALLOWLISTED_PROVIDER_TOOL_PROFILE = OPENAI_COMPAT_DEFAULT_PROVIDER_TOOL_PROFILE


PROVIDER_TOOL_PROFILES: dict[tuple[str, str], ProviderToolProfile] = {
    (
        "api.pinaic.com",
        "gpt-5.6-sol",
    ): ProviderToolProfile(
        supports_native_tools=True,
        verified=True,
        notes=(
            "Live PinAI Chat Completions probe: plain structured replies, auto single-tool calls, "
            "parallel two-tool calls, and tool-result follow-up all passed."
        ),
    ),
    (
        "api.pinaic.com",
        "gpt-5.6-luna",
    ): ProviderToolProfile(
        supports_native_tools=True,
        verified=True,
        notes="Live PinAI Chat Completions probe: parallel two-tool calls passed with provider ids.",
    ),
    (
        "api.deepseek.com",
        "deepseek-v4-flash",
    ): ProviderToolProfile(
        supports_native_tools=True,
        verified=True,
        notes="Project probes verified native tool calling for this model.",
    ),
    (
        "api.deepseek.com",
        "deepseek-v4-pro",
    ): ProviderToolProfile(
        supports_native_tools=True,
        verified=True,
        notes="Project probes verified native tool calling for this model.",
    ),
}


class ProviderToolsMixin:
    """Methods use the state initialized by LLMRuntime."""

    def _should_send_native_tools(self, bundle: ModelBundle) -> bool:
        return bool(self._native_tool_profile(bundle).supports_native_tools)

    def _native_tool_profile(self, bundle: ModelBundle) -> ProviderToolProfile:
        protocol = (
            str(getattr(bundle.client, "_akane_protocol", getattr(bundle.client, "protocol", "")) or "").strip().lower()
        )
        if protocol == "anthropic":
            return ProviderToolProfile(
                supports_native_tools=True,
                native_call_shape="anthropic_tool_use",
                verified=True,
                notes="Anthropic Messages API supports native tools/tool_use; forced JSON is not sent on this protocol.",
            )
        if protocol == "responses":
            return ProviderToolProfile(
                supports_native_tools=True,
                native_call_shape="responses_function_call",
                verified=True,
                notes="OpenAI Responses wire protocol with function_call/function_call_output items.",
            )
        if protocol == "gemini":
            return ProviderToolProfile(
                supports_native_tools=True,
                native_call_shape="gemini_function_call",
                verified=True,
                notes="Gemini generateContent supports native functionCall/functionResponse items.",
            )
        if protocol != "openai":
            return DEFAULT_PROVIDER_TOOL_PROFILE
        host = self._bundle_base_host(bundle)
        model = str(getattr(bundle, "model", "") or "").strip().lower()
        profile = PROVIDER_TOOL_PROFILES.get((host, model))
        if profile is not None:
            return profile
        # OpenAI-compatible gateways are intentionally optimistic. A model or
        # gateway that truly lacks function calling must say so at request time;
        # an allowlist must not silently hide tools and make the model invent a
        # text protocol such as DSML.
        return OPENAI_COMPAT_DEFAULT_PROVIDER_TOOL_PROFILE

    def _normalize_native_tools(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Selection already owns exposure. Truncation here silently removes
        # published tools (for example load_skill and QQ delivery after plugins).
        for raw in value:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("type") or "").strip() != "function":
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if not name or name in seen or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                continue
            parameters = function.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "additionalProperties": True}
            description = str(function.get("description") or "").strip()
            tool = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description or f"Call Akane tool {name}.",
                    "parameters": parameters,
                },
            }
            if bool(function.get("strict")):
                tool["function"]["strict"] = True
            tools.append(tool)
            seen.add(name)
        return tools

    def _normalize_native_tool_choice(self, value: Any) -> Any:
        if isinstance(value, dict):
            return value
        raw = str(value or "").strip().lower()
        return raw if raw in {"auto", "none", "required"} else ""

    def _extract_native_tool_call(
        self,
        response: Any,
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> dict[str, Any] | None:
        calls = self._extract_native_tool_calls(response, native_tools=native_tools, bundle=bundle)
        return calls[0] if calls else None

    def _extract_native_tool_calls(
        self,
        response: Any,
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> list[dict[str, Any]]:
        source = NATIVE_OPENAI
        if bundle is not None and self._is_anthropic_protocol(bundle):
            invocations = parse_anthropic_messages_tool_uses(self._chat_response_message(response))
            source = NATIVE_ANTHROPIC
        else:
            invocations = parse_openai_chat_tool_calls(response)
        if not invocations:
            return []
        if len(invocations) > 1:
            self._record_metric("native_tool_calls_extra", len(invocations) - 1)
        calls = [
            self._native_invocation_to_tool_call(invocation, native_tools=native_tools, source=source)
            for invocation in invocations
        ]
        return [call for call in calls if call is not None]

    def _collect_stream_native_tool_call_parts(
        self,
        chunk: Any,
        parts: dict[Any, dict[str, Any]],
        *,
        bundle: ModelBundle | None = None,
    ) -> None:
        if bundle is not None and self._is_anthropic_protocol(bundle):
            collect_anthropic_messages_stream_tool_use_delta(chunk, parts)
            return
        collect_openai_chat_stream_tool_call_delta(chunk, parts)

    def _stream_native_tool_call_from_parts(
        self,
        parts: dict[Any, dict[str, Any]],
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> dict[str, Any] | None:
        calls = self._stream_native_tool_calls_from_parts(parts, native_tools=native_tools, bundle=bundle)
        return calls[0] if calls else None

    def _stream_native_tool_calls_from_parts(
        self,
        parts: dict[Any, dict[str, Any]],
        *,
        native_tools: list[dict[str, Any]] | None = None,
        bundle: ModelBundle | None = None,
    ) -> list[dict[str, Any]]:
        source = NATIVE_OPENAI
        if bundle is not None and self._is_anthropic_protocol(bundle):
            invocations = parse_anthropic_messages_stream_tool_uses(parts)
            source = NATIVE_ANTHROPIC
        else:
            invocations = parse_openai_chat_stream_tool_calls(parts)
        if not invocations:
            return []
        if len(invocations) > 1:
            self._record_metric("native_tool_calls_extra", len(invocations) - 1)
        calls = [
            self._native_invocation_to_tool_call(invocation, native_tools=native_tools, source=source)
            for invocation in invocations
        ]
        return [call for call in calls if call is not None]

    def _native_invocation_to_tool_call(
        self,
        invocation: Any,
        *,
        native_tools: list[dict[str, Any]] | None,
        source: str = NATIVE_OPENAI,
    ) -> dict[str, Any] | None:
        model_name = str(getattr(invocation, "model_name", "") or "").strip()
        if not model_name or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", model_name):
            return None
        name_map = native_tool_model_name_map(native_tools)
        capability_id = name_map.get(model_name, str(getattr(invocation, "capability_id", "") or model_name).strip())
        if not capability_id:
            return None
        arguments = getattr(invocation, "arguments", {})
        result = {
            **({key: value for key, value in arguments.items() if not key.startswith("_tool_")}
               if isinstance(arguments, dict) else {}),
            "type": capability_id,
            TOOL_SOURCE_FIELD: source if source in {NATIVE_OPENAI, NATIVE_ANTHROPIC} else NATIVE_OPENAI,
            TOOL_MODEL_ARGUMENTS_FIELD: dict(arguments) if isinstance(arguments, dict) else {},
        }
        call_id = self._native_invocation_provider_id(invocation, source=source)
        if call_id:
            result[TOOL_INVOCATION_ID_FIELD] = call_id
        if model_name != capability_id:
            result[TOOL_MODEL_NAME_FIELD] = model_name
        parse_error = str(getattr(invocation, "parse_error", "") or "").strip()
        if parse_error:
            result[TOOL_PARSE_ERROR_FIELD] = parse_error
            result[TOOL_RAW_ARGUMENTS_FIELD] = self._native_invocation_raw_arguments(invocation)
        return result

    @staticmethod
    def _native_invocation_raw_arguments(invocation: Any) -> Any:
        """Return the provider's exact argument carrier for a rejected call.

        CapCore intentionally preserves malformed argument text on ``raw``.
        Keeping that value on the internal invocation lets the ordinary tool
        result path explain the real parse failure to the model instead of
        silently replacing its decision with an empty argument object.
        """

        raw = getattr(invocation, "raw", None)
        function = raw.get("function") if isinstance(raw, dict) else getattr(raw, "function", None)
        if isinstance(function, dict):
            return function.get("arguments")
        return getattr(function, "arguments", None)

    def _native_invocation_provider_id(self, invocation: Any, *, source: str = NATIVE_OPENAI) -> str:
        raw = getattr(invocation, "raw", None)
        if isinstance(raw, dict) and "id" in raw:
            return str(raw.get("id") or "").strip()
        provider_id = str(getattr(invocation, "id", "") or "").strip()
        if source == NATIVE_ANTHROPIC and provider_id:
            return provider_id
        return ""

    def _decode_native_tool_arguments(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = str(value or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
