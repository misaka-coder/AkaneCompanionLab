from __future__ import annotations

import re
from typing import Any, Mapping


NATIVE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NATIVE_TOOL_DESCRIPTION_MAX_CHARS = 900


def build_openai_native_tool_specs(
    handlers: Mapping[str, Any] | None,
    *,
    allowed_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build conservative OpenAI-style tool specs from registered handlers.

    The returned specs are an adapter boundary only: Akane still normalizes and
    executes tool calls through the existing ToolHandler layer.
    """
    if not isinstance(handlers, Mapping):
        return []

    allowed = {str(item or "").strip() for item in allowed_tool_names or set()}
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_name, handler in sorted(handlers.items(), key=lambda item: str(item[0] or "")):
        tool_name = str(getattr(handler, "tool_type", "") or raw_name or "").strip()
        if not tool_name or tool_name in seen:
            continue
        if allowed and tool_name not in allowed:
            continue
        if not NATIVE_TOOL_NAME_RE.fullmatch(tool_name):
            continue
        seen.add(tool_name)
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": _handler_description(handler, tool_name=tool_name),
                    "parameters": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }
        )
    return specs


def _handler_description(handler: Any, *, tool_name: str) -> str:
    description = ""
    build_prompt_instruction = getattr(handler, "build_prompt_instruction", None)
    if callable(build_prompt_instruction):
        try:
            description = str(build_prompt_instruction() or "").strip()
        except Exception:
            description = ""
    if not description:
        description = f"Call Akane tool {tool_name}."
    description = " ".join(description.split())
    return description[:NATIVE_TOOL_DESCRIPTION_MAX_CHARS]
