from __future__ import annotations

import json
import re
import time
import uuid
from types import SimpleNamespace
from typing import Any, Iterator
from urllib.parse import quote

import requests


GEMINI_API_VERSION = "v1beta"
GEMINI_DEFAULT_MAX_OUTPUT_TOKENS = 4096
_DATA_IMAGE_RE = re.compile(
    r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_SCHEMA_TYPES = {
    "array": "ARRAY",
    "boolean": "BOOLEAN",
    "integer": "INTEGER",
    "null": "NULL",
    "number": "NUMBER",
    "object": "OBJECT",
    "string": "STRING",
}
_UNSUPPORTED_SCHEMA_KEYS = {
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "definitions",
    "dependentSchemas",
    "else",
    "if",
    "not",
    "patternProperties",
    "then",
    "unevaluatedProperties",
}


class GeminiNativeCompatClient:
    """Expose Gemini's native REST API through Akane's chat-completions boundary.

    Akane assembles one provider-neutral, append-only message history.  This
    adapter translates that history at the final wire boundary, so MemCore and
    the tool runtime do not need Gemini-specific message types or SDK objects.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 60.0,
        max_retries: int = 0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = _normalize_gemini_base_url(base_url)
        self.timeout = float(timeout or 60.0)
        self.max_retries = max(0, int(max_retries or 0))
        self.protocol = "gemini"
        self._akane_protocol = "gemini"
        self.chat = _GeminiChat(self)
        self.models = _GeminiModels(self)


class _GeminiChat:
    def __init__(self, client: GeminiNativeCompatClient) -> None:
        self.completions = _GeminiCompletions(client)


class _GeminiCompletions:
    def __init__(self, client: GeminiNativeCompatClient) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "").strip()
        if not model:
            raise ValueError("gemini_model_missing")
        stream = bool(kwargs.get("stream"))
        endpoint = _gemini_model_endpoint(
            self._client.base_url,
            model=model,
            operation="streamGenerateContent" if stream else "generateContent",
            stream=stream,
        )
        payload = _build_gemini_payload(kwargs)
        response = _request_with_retries(
            client=self._client,
            method="POST",
            url=endpoint,
            json_payload=payload,
            stream=stream,
            timeout=float(kwargs.get("timeout", self._client.timeout) or self._client.timeout),
        )
        if stream:
            return _GeminiStream(response=response, model=model)
        return _build_openai_style_response(response.json(), model=model)


class _GeminiModels:
    def __init__(self, client: GeminiNativeCompatClient) -> None:
        self._client = client

    def list(self) -> Any:
        model_ids: set[str] = set()
        page_token = ""
        for _page_index in range(10):
            endpoint = _gemini_models_endpoint(self._client.base_url)
            params: dict[str, Any] = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            response = _request_with_retries(
                client=self._client,
                method="GET",
                url=endpoint,
                params=params,
                timeout=self._client.timeout,
            )
            payload = response.json()
            for item in payload.get("models") or []:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("name") or "").strip()
                if model_id.startswith("models/"):
                    model_id = model_id[len("models/") :]
                if model_id:
                    model_ids.add(model_id)
            page_token = str(payload.get("nextPageToken") or "").strip()
            if not page_token:
                break
        return SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in sorted(model_ids, key=str.casefold)])


class _GeminiStream:
    def __init__(self, *, response: requests.Response, model: str) -> None:
        self._response = response
        self._model = model
        self.usage: Any = None
        self._closed = False

    def __iter__(self) -> Iterator[Any]:
        next_tool_index = 0
        seen_tool_calls: set[str] = set()
        try:
            for payload in _iter_sse_payloads(self._response):
                usage = _gemini_usage(payload.get("usageMetadata"))
                if usage is not None:
                    self.usage = usage
                model = str(payload.get("modelVersion") or self._model)
                candidates = payload.get("candidates") or []
                if not isinstance(candidates, list) or not candidates:
                    continue
                candidate = candidates[0] if isinstance(candidates[0], dict) else {}
                content = candidate.get("content") if isinstance(candidate, dict) else {}
                parts = content.get("parts") if isinstance(content, dict) else []
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for part in parts if isinstance(parts, list) else []:
                    if not isinstance(part, dict):
                        continue
                    if not bool(part.get("thought")) and part.get("text") is not None:
                        text_parts.append(str(part.get("text") or ""))
                    function_call = part.get("functionCall")
                    if not isinstance(function_call, dict):
                        continue
                    call_id = str(function_call.get("id") or "").strip()
                    name = str(function_call.get("name") or "").strip()
                    arguments = function_call.get("args")
                    if not isinstance(arguments, dict):
                        arguments = {}
                    fingerprint = call_id or json.dumps(
                        [name, arguments],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if fingerprint in seen_tool_calls:
                        continue
                    seen_tool_calls.add(fingerprint)
                    if not call_id:
                        call_id = f"call_gemini_{uuid.uuid4().hex[:16]}"
                    tool_calls.append(
                        {
                            "index": next_tool_index,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    )
                    next_tool_index += 1
                finish_reason = _map_gemini_finish_reason(
                    candidate.get("finishReason") if isinstance(candidate, dict) else ""
                )
                if not text_parts and not tool_calls and finish_reason is None:
                    continue
                yield _build_stream_chunk(
                    model=model,
                    text="".join(text_parts),
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                )
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._response.close()


def _normalize_gemini_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        normalized = "https://generativelanguage.googleapis.com"
    for suffix in ("/v1beta/models", "/v1beta"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    return normalized


def _gemini_models_endpoint(base_url: str) -> str:
    return f"{str(base_url or '').rstrip('/')}/{GEMINI_API_VERSION}/models"


def _gemini_model_endpoint(
    base_url: str,
    *,
    model: str,
    operation: str,
    stream: bool = False,
) -> str:
    clean_model = str(model or "").strip()
    if clean_model.startswith("models/"):
        clean_model = clean_model[len("models/") :]
    endpoint = (
        f"{_gemini_models_endpoint(base_url)}/{quote(clean_model, safe='-._~')}"
        f":{operation}"
    )
    return f"{endpoint}?alt=sse" if stream else endpoint


def _request_with_retries(
    *,
    client: GeminiNativeCompatClient,
    method: str,
    url: str,
    timeout: float,
    json_payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    stream: bool = False,
) -> requests.Response:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-goog-api-key": client.api_key,
    }
    response: requests.Response | None = None
    for attempt in range(client.max_retries + 1):
        response = requests.request(
            method,
            url,
            headers=headers,
            json=json_payload,
            params=params,
            timeout=timeout,
            stream=stream,
        )
        response.encoding = "utf-8"
        if response.ok:
            return response
        if response.status_code not in {408, 409, 429, 500, 502, 503, 504} or attempt >= client.max_retries:
            break
        response.close()
        time.sleep(min(2.0, 0.25 * (2**attempt)))
    assert response is not None
    _raise_gemini_status(response)
    raise RuntimeError("gemini_request_failed")


def _raise_gemini_status(response: requests.Response) -> None:
    if response.ok:
        return
    message = ""
    code = ""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = str(error.get("status") or error.get("code") or "").strip()
        elif error:
            message = str(error).strip()
    except Exception:
        message = str(response.text or "").strip()
    response.close()
    detail = " ".join(part for part in [code, message] if part).strip()
    raise RuntimeError(detail or f"gemini_http_{response.status_code}")


def _build_gemini_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    system_parts, contents = _convert_openai_messages(kwargs.get("messages"))
    payload: dict[str, Any] = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": text} for text in system_parts]}

    generation_config: dict[str, Any] = {}
    if kwargs.get("temperature") is not None:
        generation_config["temperature"] = float(kwargs["temperature"])
    if kwargs.get("top_p") is not None:
        generation_config["topP"] = float(kwargs["top_p"])
    max_tokens = kwargs.get("max_tokens")
    if max_tokens is None:
        max_tokens = kwargs.get("max_completion_tokens")
    if max_tokens is not None and int(max_tokens or 0) > 0:
        generation_config["maxOutputTokens"] = int(max_tokens)
    stop = kwargs.get("stop")
    if isinstance(stop, str) and stop:
        generation_config["stopSequences"] = [stop]
    elif isinstance(stop, list):
        stop_sequences = [str(item) for item in stop if str(item)]
        if stop_sequences:
            generation_config["stopSequences"] = stop_sequences
    response_format = kwargs.get("response_format")
    if isinstance(response_format, dict) and str(response_format.get("type") or "") == "json_object":
        generation_config["responseMimeType"] = "application/json"
    if generation_config:
        payload["generationConfig"] = generation_config

    tools = _convert_openai_tools(kwargs.get("tools"))
    if tools:
        payload["tools"] = [{"functionDeclarations": tools}]
        tool_config = _convert_tool_choice(kwargs.get("tool_choice"))
        if tool_config:
            payload["toolConfig"] = {"functionCallingConfig": tool_config}
    return payload


def _convert_openai_messages(value: Any) -> tuple[list[str], list[dict[str, Any]]]:
    messages = value if isinstance(value, list) else []
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    tool_names_by_id: dict[str, str] = {}
    open_tool_response_group: dict[str, Any] | None = None

    for raw in messages:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user").strip().lower()
        if role in {"system", "developer"}:
            text = _flatten_content_to_text(raw.get("content"))
            if text:
                system_parts.append(text)
            open_tool_response_group = None
            continue
        if role == "tool":
            call_id = str(raw.get("tool_call_id") or "").strip()
            name = tool_names_by_id.get(call_id, "")
            if not name:
                name = str(raw.get("name") or "").strip()
            if not name:
                name = "unknown_tool"
            function_response: dict[str, Any] = {
                "name": name,
                "response": _tool_result_response(raw.get("content")),
            }
            if call_id:
                function_response["id"] = call_id
            part = {"functionResponse": function_response}
            if open_tool_response_group is None:
                open_tool_response_group = {"role": "user", "parts": [part]}
                contents.append(open_tool_response_group)
            else:
                open_tool_response_group["parts"].append(part)
            continue

        open_tool_response_group = None
        provider_role = "model" if role == "assistant" else "user"
        parts = _content_to_gemini_parts(raw.get("content"))
        if role == "assistant":
            for tool_call in raw.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                call_id = str(tool_call.get("id") or "").strip()
                name = str(function.get("name") or "").strip()
                if not name:
                    continue
                arguments = _json_object(function.get("arguments"))
                function_call: dict[str, Any] = {
                    "name": name,
                    "args": arguments,
                }
                if call_id:
                    function_call["id"] = call_id
                    tool_names_by_id[call_id] = name
                parts.append({"functionCall": function_call})
        if not parts:
            continue
        contents.append({"role": provider_role, "parts": parts})
    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})
    return system_parts, contents


def _content_to_gemini_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if not isinstance(content, list):
        text = str(content or "")
        return [{"text": text}] if text else []
    parts: list[dict[str, Any]] = []
    for raw in content:
        if not isinstance(raw, dict):
            continue
        block_type = str(raw.get("type") or "").strip()
        if block_type in {"text", "input_text"}:
            text = str(raw.get("text") or raw.get("input_text") or "")
            if text:
                parts.append({"text": text})
            continue
        if block_type in {"image_url", "input_image"}:
            image_url = raw.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            image_part = _image_url_to_part(image_url)
            if image_part is not None:
                parts.append(image_part)
    return parts


def _image_url_to_part(value: Any) -> dict[str, Any] | None:
    url = str(value or "").strip()
    if not url:
        return None
    match = _DATA_IMAGE_RE.match(url)
    if not match:
        raise ValueError("gemini_remote_image_url_unsupported")
    return {
        "inlineData": {
            "mimeType": match.group(1),
            "data": match.group(2),
        }
    }


def _flatten_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") not in {"text", "input_text"}:
            continue
        text = str(item.get("text") or item.get("input_text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _tool_result_response(content: Any) -> dict[str, Any]:
    text = _flatten_content_to_text(content) if isinstance(content, list) else str(content or "")
    try:
        value = json.loads(text)
    except Exception:
        value = text
    if isinstance(value, dict):
        return value
    return {"result": value}


def _convert_openai_tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    declarations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value[:64]:
        if not isinstance(raw, dict) or str(raw.get("type") or "") != "function":
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name or name in seen:
            continue
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "additionalProperties": True}
        declarations.append(
            {
                "name": name,
                "description": str(function.get("description") or "").strip(),
                "parameters": _normalize_gemini_schema(parameters),
            }
        )
        seen.add(name)
    return declarations


def _normalize_gemini_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_gemini_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    nullable = False
    for key, item in value.items():
        clean_key = str(key)
        if clean_key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if clean_key == "oneOf":
            clean_key = "anyOf"
        if clean_key == "const":
            normalized["enum"] = [item]
            continue
        if clean_key == "type":
            if isinstance(item, list):
                non_null = [str(raw).lower() for raw in item if str(raw).lower() != "null"]
                nullable = len(non_null) != len(item)
                if len(non_null) == 1:
                    normalized["type"] = _SCHEMA_TYPES.get(non_null[0], non_null[0].upper())
                continue
            raw_type = str(item or "").strip().lower()
            normalized["type"] = _SCHEMA_TYPES.get(raw_type, raw_type.upper())
            continue
        normalized[clean_key] = _normalize_gemini_schema(item)
    if nullable:
        normalized["nullable"] = True
    if not normalized.get("type") and isinstance(normalized.get("properties"), dict):
        normalized["type"] = "OBJECT"
    return normalized


def _convert_tool_choice(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        function = value.get("function")
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
            if name:
                return {"mode": "ANY", "allowedFunctionNames": [name]}
        choice_type = str(value.get("type") or "").strip().lower()
        if choice_type == "none":
            return {"mode": "NONE"}
        if choice_type in {"required", "any"}:
            return {"mode": "ANY"}
        return {"mode": "AUTO"}
    raw = str(value or "").strip().lower()
    if raw == "none":
        return {"mode": "NONE"}
    if raw in {"required", "any"}:
        return {"mode": "ANY"}
    return {"mode": "AUTO"}


def _build_openai_style_response(payload: dict[str, Any], *, model: str) -> Any:
    candidates = payload.get("candidates") or []
    candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate, dict) else {}
    parts = content.get("parts") if isinstance(content, dict) else []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        if not bool(part.get("thought")) and part.get("text") is not None:
            text_parts.append(str(part.get("text") or ""))
        function_call = part.get("functionCall")
        if not isinstance(function_call, dict):
            continue
        call_id = str(function_call.get("id") or "").strip() or f"call_gemini_{uuid.uuid4().hex[:16]}"
        arguments = function_call.get("args")
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": str(function_call.get("name") or ""),
                    "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                },
            }
        )
    finish_reason = _map_gemini_finish_reason(candidate.get("finishReason"))
    if tool_calls:
        finish_reason = "tool_calls"
    resolved_model = str(payload.get("modelVersion") or model)
    return SimpleNamespace(
        id=f"chatcmpl-gemini-{uuid.uuid4().hex}",
        object="chat.completion",
        created=0,
        model=resolved_model,
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    role="assistant",
                    content="".join(text_parts),
                    tool_calls=tool_calls,
                ),
            )
        ],
        usage=_gemini_usage(payload.get("usageMetadata")),
        raw_response=payload,
    )


def _build_stream_chunk(
    *,
    model: str,
    text: str,
    tool_calls: list[dict[str, Any]],
    finish_reason: str | None,
) -> Any:
    return SimpleNamespace(
        id=f"chatcmpl-gemini-{uuid.uuid4().hex}",
        object="chat.completion.chunk",
        created=0,
        model=model,
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(
                    content=text or None,
                    tool_calls=tool_calls or None,
                ),
                finish_reason=finish_reason,
            )
        ],
    )


def _iter_sse_payloads(response: requests.Response) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=False):
        if raw_line is None:
            continue
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else str(raw_line)
        ).strip()
        if not line:
            if data_lines:
                payload = _json_object("\n".join(data_lines))
                if payload:
                    yield payload
                data_lines = []
            continue
        if line.startswith("data:"):
            raw_data = line.split(":", 1)[1].strip()
            if raw_data and raw_data != "[DONE]":
                data_lines.append(raw_data)
    if data_lines:
        payload = _json_object("\n".join(data_lines))
        if payload:
            yield payload


def _gemini_usage(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    prompt_tokens = _int_value(value.get("promptTokenCount"))
    completion_tokens = _int_value(value.get("candidatesTokenCount"))
    total_tokens = _int_value(value.get("totalTokenCount")) or prompt_tokens + completion_tokens
    cached_tokens = _int_value(value.get("cachedContentTokenCount"))
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        cache_read_input_tokens=cached_tokens,
        cache_creation_input_tokens=0,
    )


def _map_gemini_finish_reason(value: Any) -> str | None:
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "LANGUAGE": "content_filter",
        "BLOCKLIST": "content_filter",
        "PROHIBITED_CONTENT": "content_filter",
        "SPII": "content_filter",
    }
    raw = str(value or "").strip().upper()
    return mapping.get(raw, None)


def _json_object(value: Any) -> dict[str, Any]:
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


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0
