"""Mutable evidence and independent budgets owned by one dialogue turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..tool_runtime import ToolExecutionResult


@dataclass
class ToolRoundBudget:
    hard_limit: int
    warning_remaining: int
    protocol_retry_limit: int
    round_index: int = 0
    warning_emitted: bool = False
    protocol_attempts: int = 0


@dataclass
class ToolRoundState:
    final_output: dict[str, Any]
    recent_raw_for_turn: list[dict[str, Any]]
    budget: ToolRoundBudget
    provider_output_raw: str = ""
    tool_turns: list[dict[str, Any]] = field(default_factory=list)
    preface_turns: list[dict[str, str]] = field(default_factory=list)
    tool_result: ToolExecutionResult | None = None
    tool_results: list[ToolExecutionResult] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    tool_followups: list[str] = field(default_factory=list)
    tool_history_turns: list[dict[str, Any]] = field(default_factory=list)
    native_reasoning_by_call_id: dict[str, str] = field(default_factory=dict)
    provider_continuations: dict[str, dict[str, Any]] = field(default_factory=dict)
    recorded_tool_call_ids: set[str] = field(default_factory=set)
    memory_exclude_source_ids: list[str] = field(default_factory=list)
    deferred_control_snapshot: dict[str, Any] = field(default_factory=dict)
    completion_work: Any = None
    presentation_started: bool = False


@dataclass(frozen=True)
class TurnRoundOutcome:
    state: ToolRoundState
    stopped_output: dict[str, Any] | None = None
