from __future__ import annotations

import json
import sys
from pathlib import Path


bot_id = sys.argv[1]
since = sys.argv[2]
path = Path(f"/var/lib/akane-host/bots/{bot_id}/logs/llm_prompt_audit/2026-07-19.jsonl")

records: list[dict[str, object]] = []
for line in path.read_text(encoding="utf-8").splitlines():
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        continue
    if str(raw.get("ts") or "") < since:
        continue
    if raw.get("record_type") == "prompt":
        records.append(
            {
                "ts": raw.get("ts"),
                "type": "prompt",
                "cache_key": raw.get("prompt_cache_key"),
                "runtime_object_id": raw.get("runtime_object_id"),
                "client_object_id": raw.get("client_object_id"),
                "bundle_role": raw.get("bundle_role"),
                "model": raw.get("model"),
                "tool_count": raw.get("native_tool_count"),
            }
        )
    elif raw.get("record_type") == "usage":
        records.append(
            {
                "ts": raw.get("ts"),
                "type": "usage",
                "cache_key": raw.get("prompt_cache_key"),
                "input_tokens": raw.get("reported_input_tokens"),
                "cached_tokens": raw.get("cache_read_tokens"),
                "output_tokens": raw.get("reported_output_tokens"),
                "cache_creation_tokens": raw.get("cache_creation_tokens"),
                "hit_ratio": raw.get("cache_hit_ratio"),
            }
        )

print(json.dumps(records, ensure_ascii=False, indent=2))
