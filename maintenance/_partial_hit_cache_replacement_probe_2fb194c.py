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
cache_key = f"akane-partial-hit-replacement-2fb194c-{int(time.time())}"
stable_first_input = "Shared stable native-tool context. " * 2200
old_second_input = "Old volatile readiness input. " * 2600
new_second_input = "New stable linear conversation input. " * 2600
tools = [
    {
        "type": "function",
        "name": f"stable_tool_{index}",
        "description": (f"Stable tool schema {index}. " * 35).strip(),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
    for index in range(14)
]


def run(input_items: list[dict[str, str]]) -> dict[str, int | float]:
    response = client.responses.create(
        model=settings["CHAT_MODEL_NAME"],
        instructions="Stable instructions. " * 500,
        input=input_items,
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=True,
        reasoning={"effort": "high"},
        prompt_cache_key=cache_key,
        prompt_cache_retention="in-memory",
        stream=False,
        store=False,
    )
    usage = response.usage
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "hit_ratio": round(cached_tokens / input_tokens, 6) if input_tokens else 0.0,
    }


print(
    json.dumps(
        [
            run(
                [
                    {"role": "user", "content": stable_first_input},
                    {"role": "user", "content": old_second_input},
                ]
            ),
            run(
                [
                    {"role": "user", "content": stable_first_input},
                    {"role": "user", "content": new_second_input},
                    {"role": "user", "content": "dynamic tail A"},
                ]
            ),
            run(
                [
                    {"role": "user", "content": stable_first_input},
                    {"role": "user", "content": new_second_input},
                    {"role": "user", "content": "dynamic tail B"},
                ]
            ),
        ]
    )
)
