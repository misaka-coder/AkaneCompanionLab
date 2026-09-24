"""Shared constants and framing for the PluginHost generation protocol."""

from __future__ import annotations

import json
from typing import Any, Mapping, TextIO


PLUGIN_GENERATION_PROTOCOL = "akane.plugin-generation.v1"
PLUGIN_GENERATION_START_TIMEOUT_SECONDS = 45.0
PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS: float | None = None


def emit_protocol_message(stream: TextIO, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
    stream.flush()


def response_base(*, generation_id: str, request_id: str) -> dict[str, Any]:
    return {
        "protocol": PLUGIN_GENERATION_PROTOCOL,
        "type": "response",
        "generation_id": generation_id,
        "request_id": request_id,
    }


__all__ = [
    "PLUGIN_GENERATION_PROTOCOL",
    "PLUGIN_GENERATION_START_TIMEOUT_SECONDS",
    "PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS",
    "emit_protocol_message",
    "response_base",
]
