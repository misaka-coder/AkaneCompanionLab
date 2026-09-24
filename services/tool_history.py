"""Provider-independent isolation of broken tool exchanges in chat history.

Only complete, adjacent call/result pairs cross the model boundary. Ordinary
conversation content and valid provider fields are preserved. This never edits
the durable source, fabricates a tool result, or promotes tool output to user
instructions. Diagnostics contain positions and reasons, never message content.
"""

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ToolHistoryValidation:
    messages: list[dict[str, Any]]
    source_indexes: list[int]
    issues: list[dict[str, Any]]


def isolate_tool_history(messages) -> ToolHistoryValidation:
    rows = list(messages or [])
    issues = []
    allowed_calls = set()
    allowed_results = set()
    pending = {}
    used_ids = set()

    def issue(index, reason):
        issues.append({"message_index": index, "reason": reason})

    def close_batch():
        for index, _key in pending.values():
            issue(index, "tool_call_without_adjacent_result")
        pending.clear()

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            close_batch()
            issue(index, "message_not_object")
            continue
        role = row.get("role")
        content = row.get("content")
        blocks = content if isinstance(content, list) else []
        calls = []
        results = []
        raw_calls = row.get("tool_calls")
        if raw_calls is not None:
            if not isinstance(raw_calls, list):
                issue(index, "tool_calls_not_array")
            else:
                if not raw_calls:
                    issue(index, "tool_calls_empty")
                for position, call in enumerate(raw_calls):
                    function = call.get("function") if isinstance(call, dict) else None
                    call_id = call.get("id") if isinstance(call, dict) else None
                    valid = (role == "assistant" and isinstance(call_id, str) and bool(call_id.strip())
                             and call.get("type", "function") == "function" and isinstance(function, dict)
                             and isinstance(function.get("name"), str)
                             and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", function["name"]) is not None
                             and isinstance(function.get("arguments"), str))
                    calls.append(("chat", position, call_id, valid))
        if role == "tool":
            if isinstance(content, (str, list)):
                results.append(("chat", -1, row.get("tool_call_id")))
            else:
                issue(index, "tool_result_content_invalid")
        result_prefix = True
        for position, block in enumerate(blocks):
            if not isinstance(block, dict):
                result_prefix = False
                continue
            if block.get("type") != "tool_result":
                result_prefix = False
            if block.get("type") == "tool_use":
                call_id = block.get("id")
                valid = (role == "assistant" and isinstance(call_id, str) and bool(call_id.strip())
                         and isinstance(block.get("name"), str) and bool(block["name"].strip())
                         and isinstance(block.get("input"), dict))
                calls.append(("blocks", position, call_id, valid))
            elif block.get("type") == "tool_result":
                # Native block results must precede ordinary user content.
                if role != "user" or not result_prefix:
                    issue(index, "tool_result_wrong_position")
                else:
                    results.append(("blocks", position, block.get("tool_use_id")))

        if calls or not results:
            close_batch()
        for style, key, call_id, valid in calls:
            if not valid:
                issue(index, "tool_call_invalid")
            elif call_id in used_ids:
                issue(index, "duplicate_tool_call_id")
            else:
                used_ids.add(call_id)
                pending[(style, call_id)] = (index, key)
        for style, key, call_id in results:
            match = pending.pop((style, call_id), None) if isinstance(call_id, str) else None
            if match is None:
                issue(index, "tool_result_without_adjacent_call")
            else:
                allowed_calls.add((match[0], style, match[1]))
                allowed_results.add((index, style, key))
        # User prose begins a new stimulus, even if it shares an Anthropic
        # message with completed tool results. Never pair across that boundary.
        if results and role == "user" and any(
            not isinstance(b, dict) or b.get("type") != "tool_result" for b in blocks
        ):
            close_batch()
    close_batch()

    clean = []
    source_indexes = []
    for index, original in enumerate(rows):
        if not isinstance(original, dict):
            continue
        row = dict(original)
        if row.get("role") == "tool" and (index, "chat", -1) not in allowed_results:
            continue
        changed = False
        if "tool_calls" in row:
            raw = row["tool_calls"]
            kept = [call for key, call in enumerate(raw) if (index, "chat", key) in allowed_calls] if isinstance(raw, list) else []
            if kept:
                row["tool_calls"] = kept
            else:
                row.pop("tool_calls")
            changed = not kept or kept != raw
        if isinstance(row.get("content"), list):
            kept = []
            for key, block in enumerate(row["content"]):
                kind = block.get("type") if isinstance(block, dict) else ""
                if kind == "tool_use" and (index, "blocks", key) not in allowed_calls:
                    changed = True
                    continue
                if kind == "tool_result" and (index, "blocks", key) not in allowed_results:
                    changed = True
                    continue
                kept.append(block)
            row["content"] = kept
        # An empty assistant/tool-result shell is itself invalid on several
        # protocols. Plain empty messages unrelated to tool repair stay intact.
        if changed and not row.get("content") and not row.get("tool_calls"):
            continue
        if changed and row.get("role") == "assistant" and isinstance(row.get("content"), list) and all(
            isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}
            for block in row["content"]
        ):
            continue
        clean.append(row)
        source_indexes.append(index)
    return ToolHistoryValidation(clean, source_indexes, issues)
