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
cache_key = f"akane-cache-bucket-replacement-aa5bae7-{int(time.time())}"
common = "Common stable prefix for cache replacement. " * 1000
old_middle = "Old readiness-shaped prompt section. " * 3000
new_middle = "New stable native-tool prompt section. " * 3000


def run(content: str) -> dict[str, int | float]:
    response = client.responses.create(
        model=settings["CHAT_MODEL_NAME"],
        instructions="Return exactly OK.",
        input=[{"role": "user", "content": content}],
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


print(json.dumps([run(common + old_middle), run(common + new_middle), run(common + new_middle + " tail")]))
