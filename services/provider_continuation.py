"""Private, turn-local provider state. Never a tool argument or memory record.

The host owns message order; adapters own opaque signed/encrypted wire blocks.
Replay is permitted only for the same route and exactly the same tool batch.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

FIELD = "_provider_continuation"
RESULT_FIELD = "_native_provider_continuation"


def plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "__dict__"):
        return plain(vars(value))
    return value


def route(bundle: Any) -> str:
    client = getattr(bundle, "client", bundle)
    identity = [getattr(client, "_akane_protocol", getattr(client, "protocol", "")),
                str(getattr(client, "base_url", "")), str(getattr(bundle, "model", "")),
                str(getattr(client, "api_key", ""))]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def batch(message: dict) -> list[dict]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        calls = [{"id": b.get("id"), "function": {"name": b.get("name"), "arguments": b.get("input", {})}}
                 for b in message.get("content", []) if isinstance(b, dict) and b.get("type") == "tool_use"] \
            if isinstance(message.get("content"), list) else []
    result = []
    for call in calls:
        fn = call.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                pass
        result.append({"id": call.get("id"), "name": fn.get("name"), "arguments": args})
    return result


def capture(response: Any, *, bundle: Any, calls: list[dict], extras: dict | None = None) -> dict:
    """Capture adapter-native blocks after the complete response/stream."""
    client = getattr(bundle, "client", bundle)
    protocol = getattr(client, "_akane_protocol", getattr(client, "protocol", ""))
    content = plain(getattr(response, "provider_content", None))
    data = {}
    if protocol in {"gemini", "anthropic", "responses"} and content:
        data[protocol] = content
    if protocol == "openai" and extras:
        data["openai"] = deepcopy(extras)
    if not data:
        return {}
    expected = [{"id": c.get("_tool_invocation_id"),
                 "name": c.get("_tool_model_name") or c.get("type"),
                 "arguments": deepcopy(c.get("_tool_model_arguments", {}))} for c in calls]
    return {"route": route(bundle), "calls": expected, "data": data}


def overlay(message: dict, states: dict | None) -> dict:
    calls = batch(message)
    if not calls:
        return message
    state = (states or {}).get(str(calls[0].get("id") or ""))
    if not state:
        return message
    if calls != state.get("calls"):
        raise ValueError("provider_continuation_batch_changed")
    return {**message, FIELD: deepcopy(state)}


def carry(source: dict, target: dict, *, bundle: Any = None) -> dict:
    state = source.get(FIELD)
    if not isinstance(state, dict):
        return target
    if batch(target) != state.get("calls"):
        raise ValueError("provider_continuation_batch_changed")
    if bundle is not None and state.get("route") != route(bundle):
        raise ValueError("provider_continuation_route_changed")
    return {**target, FIELD: deepcopy(state)}


def wire_data(message: dict, protocol: str) -> Any:
    state = message.get(FIELD)
    if not isinstance(state, dict):
        return None
    if batch(message) != state.get("calls"):
        raise ValueError("provider_continuation_batch_changed")
    return deepcopy(state.get("data", {}).get(protocol))


def public_message(message: dict) -> dict:
    result = deepcopy(message)
    result.pop(FIELD, None)
    result.pop("reasoning_content", None)
    return result


def chat_wire_message(message: dict) -> dict:
    result = deepcopy(message)
    extras = wire_data(message, "openai") or {}
    result.pop(FIELD, None)
    for call in result.get("tool_calls", []):
        if call.get("id") in extras:
            call["extra_content"] = deepcopy(extras[call["id"]])
    return result


def collect_chat_extras(value: Any, target: dict) -> None:
    """Index stream extensions independently; signatures can arrive late."""
    raw = plain(value)
    for choice in raw.get("choices", []) if isinstance(raw, dict) else []:
        message = choice.get("delta") or choice.get("message") or {}
        for i, call in enumerate(message.get("tool_calls") or []):
            key = call.get("index", i)
            entry = target.setdefault(key, {})
            if call.get("id"):
                entry["id"] = call["id"]
            extra = call.get("extra_content")
            if isinstance(extra, dict) and isinstance(extra.get("google"), dict):
                signature = extra["google"].get("thought_signature")
                if isinstance(signature, str):
                    entry["extra"] = {"google": {"thought_signature": signature}}


def chat_extras(target: dict) -> dict:
    return {v["id"]: v["extra"] for v in target.values() if v.get("id") and v.get("extra")}
