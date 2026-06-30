import json
import re
import uuid
from types import SimpleNamespace

import requests
from openai import OpenAI


def normalize_api_protocol(protocol: str = "", base_url: str = "") -> str:
    explicit = str(protocol or "").strip().lower()
    if explicit in {"openai", "anthropic", "ollama"}:
        return explicit

    lowered = str(base_url or "").strip().lower()
    if "/claude" in lowered or "anthropic" in lowered:
        return "anthropic"
    if "11434" in lowered or "ollama" in lowered:
        return "ollama"
    return "openai"


def normalize_base_url(*, protocol: str, base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if protocol == "ollama":
        normalized = normalized or "http://127.0.0.1:11434"
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
    return normalized


def build_llm_client(
    *,
    api_key: str,
    base_url: str,
    timeout: float = 60.0,
    max_retries: int = 0,
    protocol: str = "auto",
):
    resolved = normalize_api_protocol(protocol=protocol, base_url=base_url)
    if resolved == "anthropic":
        return AnthropicCompatClient(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
    normalized_base_url = normalize_base_url(protocol=resolved, base_url=base_url)
    # OpenAI SDK raises on empty api_key at construction time, which would crash
    # the backend before the user has a chance to configure credentials via the
    # control center. Use a placeholder so the client can be built; actual API
    # calls will still fail with an auth error if no real key is provided.
    client = OpenAI(
        api_key=str(api_key or "").strip() or ("ollama" if resolved == "ollama" else "not-configured"),
        base_url=normalized_base_url,
        timeout=timeout,
        max_retries=max_retries,
    )
    try:
        setattr(client, "_akane_protocol", resolved)
    except Exception:
        pass
    return client


class AnthropicCompatClient:
    def __init__(self, *, api_key: str, base_url: str, timeout: float = 60.0, max_retries: int = 0):
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip()
        self.timeout = float(timeout or 60.0)
        self.max_retries = int(max_retries or 0)
        self.protocol = "anthropic"
        self.chat = _AnthropicChat(self)


class _AnthropicChat:
    def __init__(self, client: AnthropicCompatClient):
        self.completions = _AnthropicCompletions(client)


class _AnthropicCompletions:
    def __init__(self, client: AnthropicCompatClient):
        self._client = client

    def create(self, **kwargs):
        endpoint = _anthropic_messages_endpoint(self._client.base_url)
        payload = _build_anthropic_payload(kwargs)
        stream = bool(kwargs.get("stream"))
        timeout = float(kwargs.get("timeout", self._client.timeout) or self._client.timeout)
        headers = {
            "content-type": "application/json",
            "x-api-key": self._client.api_key,
            "anthropic-version": "2023-06-01",
        }

        response = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=stream,
        )
        response.encoding = "utf-8"
        _raise_for_status(response)
        if stream:
            return _AnthropicStream(response=response, model=payload["model"])
        return _build_openai_style_response(response.json(), payload["model"])


class _AnthropicStream:
    def __init__(self, *, response: requests.Response, model: str):
        self._response = response
        self._model = model
        self.usage = None

    def __iter__(self):
        event_name = ""
        data_lines = []
        try:
            for raw_line in self._response.iter_lines(decode_unicode=False):
                if raw_line is None:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace").strip()
                else:
                    line = str(raw_line).strip()
                if not line:
                    if event_name == "message_start" and data_lines:
                        self._capture_stream_usage(data_lines)
                    chunk = _chunk_from_sse_event(event_name, data_lines, self._model)
                    if chunk is not None:
                        yield chunk
                    event_name = ""
                    data_lines = []
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())

            if data_lines:
                chunk = _chunk_from_sse_event(event_name, data_lines, self._model)
                if chunk is not None:
                    yield chunk
        finally:
            self._response.close()

    def _capture_stream_usage(self, data_lines: list) -> None:
        try:
            payload = json.loads("\n".join(data_lines).strip())
            raw = (payload.get("message") or {}).get("usage") or {}
            self.usage = SimpleNamespace(
                cache_read_input_tokens=int(raw.get("cache_read_input_tokens", 0) or 0),
                cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens", 0) or 0),
            )
        except Exception:
            pass


def _anthropic_messages_endpoint(base_url: str) -> str:
    clean = str(base_url or "").strip().rstrip("/")
    if clean.endswith("/v1/messages"):
        return clean
    if clean.endswith("/v1"):
        return f"{clean}/messages"
    return f"{clean}/v1/messages"


def _build_anthropic_payload(kwargs: dict) -> dict:
    raw_messages = kwargs.get("messages") or []
    system_text, messages = _convert_messages(raw_messages)
    system_extra_blocks = [
        str(b or "").strip()
        for b in (kwargs.get("system_extra_blocks") or [])
        if str(b or "").strip()
    ]
    payload = {
        "model": kwargs.get("model", ""),
        "messages": messages,
        "max_tokens": int(kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 1024),
    }
    max_system_cache_blocks = 4
    system_blocks = []
    if system_text:
        system_blocks.append(_build_system_text_block(system_text, cache_enabled=max_system_cache_blocks > 0))
        max_system_cache_blocks -= 1
    for extra in system_extra_blocks:
        system_blocks.append(_build_system_text_block(extra, cache_enabled=max_system_cache_blocks > 0))
        max_system_cache_blocks -= 1
    if system_blocks:
        payload["system"] = system_blocks

    if "temperature" in kwargs and kwargs.get("temperature") is not None:
        payload["temperature"] = max(0.0, min(1.0, float(kwargs["temperature"])))
    if kwargs.get("top_p") is not None:
        payload["top_p"] = float(kwargs["top_p"])

    stop = kwargs.get("stop")
    if isinstance(stop, str) and stop.strip():
        payload["stop_sequences"] = [stop.strip()]
    elif isinstance(stop, list):
        stop_sequences = [str(item).strip() for item in stop if str(item).strip()]
        if stop_sequences:
            payload["stop_sequences"] = stop_sequences

    if kwargs.get("stream") is not None:
        payload["stream"] = bool(kwargs.get("stream"))

    extra_body = kwargs.get("extra_body")
    if isinstance(extra_body, dict):
        for key, value in extra_body.items():
            if key not in payload:
                payload[key] = value

    return payload


def _build_system_text_block(text: str, *, cache_enabled: bool) -> dict:
    block = {"type": "text", "text": text}
    if cache_enabled:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _convert_messages(messages: list) -> tuple[str, list]:
    system_parts = []
    converted = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user") or "user").strip().lower()
        content = item.get("content", "")
        if role in {"system", "developer"}:
            text = _flatten_content_to_text(content)
            if text:
                system_parts.append(text)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        converted.append(
            {
                "role": role,
                "content": _convert_content_blocks(content),
            }
        )
    return "\n".join(part for part in system_parts if part).strip(), converted


def _flatten_content_to_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip() == "text":
                text = str(item.get("text", "")).strip()
                if text:
                    chunks.append(text)
        return "\n".join(chunks).strip()
    return str(content or "").strip()


def _convert_content_blocks(content):
    if isinstance(content, str):
        return content

    blocks = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            block_type = str(item.get("type", "")).strip()
            if block_type == "text":
                blocks.append({"type": "text", "text": str(item.get("text", ""))})
                continue
            if block_type == "image_url":
                image_payload = _convert_image_url_block(item.get("image_url"))
                if image_payload is not None:
                    blocks.append(image_payload)
        if blocks:
            return blocks
    return _flatten_content_to_text(content)


def _convert_image_url_block(image_url_payload):
    if isinstance(image_url_payload, dict):
        url = str(image_url_payload.get("url", "")).strip()
    else:
        url = str(image_url_payload or "").strip()
    if not url:
        return None

    data_match = re.match(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", url, re.IGNORECASE)
    if data_match:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": data_match.group(1),
                "data": data_match.group(2),
            },
        }

    return {
        "type": "image",
        "source": {
            "type": "url",
            "url": url,
        },
    }


def _raise_for_status(response: requests.Response) -> None:
    if response.ok:
        return
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message", "")).strip()
            elif error:
                message = str(error).strip()
    except Exception:
        message = response.text.strip()
    raise RuntimeError(message or f"Anthropic request failed: HTTP {response.status_code}")


def _build_openai_style_response(payload: dict, model: str):
    text = _extract_anthropic_text(payload)
    usage = payload.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens", 0) or 0)
    completion_tokens = int(usage.get("output_tokens", 0) or 0)
    return SimpleNamespace(
        id=payload.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
        object="chat.completion",
        created=0,
        model=payload.get("model") or model,
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason=_map_finish_reason(payload.get("stop_reason")),
                message=SimpleNamespace(
                    role="assistant",
                    content=text,
                    tool_calls=[],
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        ),
    )


def _chunk_from_sse_event(event_name: str, data_lines: list[str], model: str):
    if not data_lines:
        return None
    raw = "\n".join(data_lines).strip()
    if not raw or raw == "[DONE]":
        return None

    payload = json.loads(raw)
    if event_name == "content_block_start":
        block = payload.get("content_block") or {}
        text = str(block.get("text", "") or "")
        if text:
            return _build_stream_chunk(text=text, model=model)
        return None
    if event_name == "content_block_delta":
        delta = payload.get("delta") or {}
        text = str(delta.get("text", "") or "")
        if text:
            return _build_stream_chunk(text=text, model=model)
        return None
    if event_name == "message_delta":
        delta = payload.get("delta") or {}
        finish_reason = _map_finish_reason(delta.get("stop_reason"))
        return _build_stream_chunk(text="", model=model, finish_reason=finish_reason)
    return None


def _build_stream_chunk(text: str, model: str, finish_reason=None):
    return SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        object="chat.completion.chunk",
        created=0,
        model=model,
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(content=text),
                finish_reason=finish_reason,
            )
        ],
    )


def _extract_anthropic_text(payload: dict) -> str:
    parts = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip() == "text":
            parts.append(str(item.get("text", "")))
    return "".join(parts).strip()


def _map_finish_reason(stop_reason):
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    return mapping.get(str(stop_reason or "").strip(), None)
