from __future__ import annotations

import json
import time
from pathlib import Path

from openai import OpenAI


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


settings = read_env(Path("/etc/akane/host.env"))
client = OpenAI(
    api_key=settings["CHAT_API_KEY"],
    base_url=settings["CHAT_BASE_URL"],
    timeout=120,
    max_retries=0,
)
stable_context_a = "Stable personal streaming prefix block A. " * 350
stable_context_b = "Stable personal streaming memory block B. " * 350
long_instructions = "Stable personal system instructions and output contract. " * 300
cache_key = f"akane-personal-stream-shape-{time.time_ns()}"
dummy_tools = [
    {
        "type": "function",
        "name": f"stable_tool_{index}",
        "description": (f"Stable personal tool {index}. " * 18).strip(),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
    for index in range(14)
]


def run_stream(input_items: list[dict[str, str]]) -> dict[str, int | float]:
    stream = client.responses.create(
        model=settings["CHAT_MODEL_NAME"],
        instructions=long_instructions,
        input=input_items,
        tools=dummy_tools,
        tool_choice="auto",
        parallel_tool_calls=True,
        reasoning={"effort": "high"},
        prompt_cache_key=cache_key,
        prompt_cache_retention="in-memory",
        stream=True,
        store=False,
    )
    usage = None
    try:
        for event in stream:
            if str(getattr(event, "type", "")) in {"response.completed", "response.incomplete"}:
                usage = getattr(getattr(event, "response", None), "usage", None)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if usage is None:
        raise SystemExit("stream_usage_missing")
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "hit_ratio": round(cached_tokens / input_tokens, 6) if input_tokens else 0.0,
    }


first_input = [
    {"role": "user", "content": f"{stable_context_a}\n\n{stable_context_b}"},
    *[
        {
            "role": "assistant" if index % 2 == 0 else "user",
            "content": f"Stable rendered history turn {index}. " * 3,
        }
        for index in range(9)
    ],
    {"role": "user", "content": "Personal dynamic tail A. " * 250},
]
second_input = [
    {"role": "user", "content": f"{stable_context_a}\n\n{stable_context_b}"},
    *[
        {
            "role": "assistant" if index % 2 == 0 else "user",
            "content": f"Stable rendered history turn {index}. " * 3,
        }
        for index in range(9)
    ],
    {"role": "user", "content": "Rendered personal user A."},
    {"role": "assistant", "content": "Rendered personal assistant A."},
    {"role": "user", "content": "Personal dynamic tail B. " * 250},
]

first_result = run_stream(first_input)
time.sleep(30)
print(json.dumps([first_result, run_stream(second_input)]))
