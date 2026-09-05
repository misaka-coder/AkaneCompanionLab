"""Host-owned optional tool continuation; persistence is deliberately separate."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


FINISH_TURN_PARAMETER = {
    "type": "boolean",
    "description": (
        "Set true only when this action is your last step and no further reply is needed. "
        "Success may finish the turn; errors always return for follow-up. Defaults to false."
    ),
}


def optional_followup_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Add a host argument without mutating the registered business schema."""
    return {
        **schema,
        "properties": {**dict(schema.get("properties") or {}), "finish_turn": dict(FINISH_TURN_PARAMETER)},
    }


def can_finish_tool_batch(results: Sequence[Any]) -> bool:
    """Every result must opt in; one action cannot discard another observation."""
    return bool(results) and all(
        getattr(result, "finish_turn", False) is True
        and not getattr(result, "model_image_inputs", ())
        and not any(
            event.get("type") in {"tool_execution_failed", "generated_file_ready", "approval_required"}
            for event in getattr(result, "stream_events", ())
            if isinstance(event, Mapping)
        )
        for result in results
    )
