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
    if (
        str(raw.get("ts") or "") < since
        or raw.get("record_type") != "responses_request"
        or raw.get("bundle_role") != "chat"
    ):
        continue
    records.append(raw)


def compact(item: object) -> dict[str, object]:
    value = item if isinstance(item, dict) else {}
    return {
        "index": value.get("index"),
        "type": value.get("type"),
        "role": value.get("role"),
        "name": value.get("name"),
        "field_order": value.get("field_order"),
        "chars": value.get("chars"),
        "estimated_tokens": value.get("estimated_tokens"),
        "sha256_16": value.get("sha256_16"),
    }


summary = [
    {
        "ts": raw.get("ts"),
        "runtime_object_id": raw.get("runtime_object_id"),
        "client_object_id": raw.get("client_object_id"),
        "prompt_cache_key": raw.get("prompt_cache_key"),
        "request_field_order": raw.get("request_field_order"),
        "request_fields": [compact(item) for item in raw.get("request_field_fingerprints", [])],
        "input_items": [compact(item) for item in raw.get("input_item_fingerprints", [])],
        "tools": [compact(item) for item in raw.get("tool_fingerprints", [])],
    }
    for raw in records
]

comparison: dict[str, object] = {}
if len(summary) >= 2:
    first, second = summary[-2:]
    first_inputs = first["input_items"]
    second_inputs = second["input_items"]
    common_inputs = 0
    for left, right in zip(first_inputs, second_inputs):
        if left != right:
            break
        common_inputs += 1
    first_fields = {item["name"]: item for item in first["request_fields"]}
    second_fields = {item["name"]: item for item in second["request_fields"]}
    comparison = {
        "common_input_item_count": common_inputs,
        "first_input_item_count": len(first_inputs),
        "second_input_item_count": len(second_inputs),
        "first_divergent_input": {
            "first": first_inputs[common_inputs] if common_inputs < len(first_inputs) else None,
            "second": second_inputs[common_inputs] if common_inputs < len(second_inputs) else None,
        },
        "changed_request_fields": [
            name for name in first_fields.keys() | second_fields.keys() if first_fields.get(name) != second_fields.get(name)
        ],
        "tools_identical": first["tools"] == second["tools"],
        "request_field_order_identical": first["request_field_order"] == second["request_field_order"],
    }

print(json.dumps({"records": summary[-2:], "comparison": comparison}, ensure_ascii=False, indent=2))
