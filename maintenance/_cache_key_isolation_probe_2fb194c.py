from __future__ import annotations

import hashlib
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
nonce = int(time.time())
stable_a = "Stable primary chat conversation A. " * 5000
unrelated_b = "Unrelated auxiliary retrieval request B. " * 700


def run(cache_key: str, content: str) -> dict[str, int | float]:
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


chat_key = settings.get("CHAT_API_KEY", "")
text_key = settings.get("TEXT_API_KEY", "")
identity = {
    "chat_text_key_equal": bool(chat_key and text_key and chat_key == text_key),
    "chat_key_hash": hashlib.sha256(chat_key.encode()).hexdigest()[:12] if chat_key else "",
    "text_key_hash": hashlib.sha256(text_key.encode()).hexdigest()[:12] if text_key else "",
}
primary_key = f"akane-personal:bot:personal:chat:final:probe-{nonce}"
aux_families = ("router", "verifier", "summary", "semantic", "reinforcement", "memcore")
results = [run(primary_key, stable_a)]
for index, family in enumerate(aux_families):
    results.append(
        run(
            f"akane-personal:bot:personal:aux:{family}:probe-{nonce}",
            f"{unrelated_b} family-{index}",
        )
    )
results.append(run(primary_key, stable_a + " tail"))
print(json.dumps({"identity": identity, "results": results}))
