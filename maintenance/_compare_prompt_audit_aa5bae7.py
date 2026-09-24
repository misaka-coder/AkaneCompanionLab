from __future__ import annotations

import json
import sys
from pathlib import Path


bot_id = sys.argv[1]
since = sys.argv[2]
path = Path(f"/var/lib/akane-host/bots/{bot_id}/logs/llm_prompt_audit/2026-07-19.jsonl")

prompts: list[dict[str, object]] = []
for line in path.read_text(encoding="utf-8").splitlines():
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        continue
    if str(raw.get("ts") or "") < since or raw.get("record_type") != "prompt":
        continue
    prompts.append(raw)


def compact_section(value: object) -> dict[str, object]:
    item = value if isinstance(value, dict) else {}
    return {
        "name": item.get("name"),
        "chars": item.get("chars"),
        "estimated_tokens": item.get("estimated_tokens"),
        "sha256_16": item.get("sha256_16"),
    }


summary: list[dict[str, object]] = []
for raw in prompts:
    summary.append(
        {
            "ts": raw.get("ts"),
            "cache_key": raw.get("prompt_cache_key"),
            "request_field_order": raw.get("request_field_order"),
            "request_fields": [compact_section(item) for item in raw.get("request_field_fingerprints", [])],
            "messages": [
                {
                    "index": item.get("index"),
                    "role": item.get("role"),
                    **compact_section(item),
                }
                for item in raw.get("message_fingerprints", [])
                if isinstance(item, dict)
            ],
            "payload_sections": [compact_section(item) for item in raw.get("payload_sections", [])],
            "source_sections": [compact_section(item) for item in raw.get("source_sections", [])],
        }
    )

print(json.dumps(summary, ensure_ascii=False, indent=2))
