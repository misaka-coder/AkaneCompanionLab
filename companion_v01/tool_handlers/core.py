"""Shared tool-handler contract: context, result, metadata and base handler.

Owns only the cross-domain protocol types and the canonical ToolSpec/metadata
projection tables. No domain handler implementation lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Mapping

from capcore import CapabilityResult
from ..tool_continuation import FollowupDecision

@dataclass(frozen=True)
class TaskExecutionScope:
    """Host-owned execution coordinates; not a grant of filesystem permission."""

    working_directory: str
    task_id: str = ""
    pending_work: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ToolExecutionContext:
    profile_user_id: str
    session_id: str
    now_ts: int
    visual_payload: dict[str, Any]
    character_pack_id: str = ""
    current_user_source_id: str = ""
    client_mode: str = ""
    request_context: dict[str, Any] = field(default_factory=dict)
    execution_scope: TaskExecutionScope | None = None
    invocation_id: str = ""
    capability_selection: Any = None
    # Invocation-local host control; never persisted or sent in plugin arguments.
    cancel_requested: Callable[[], bool] | None = field(default=None, compare=False, repr=False)
    # Chosen by the host caller, never supplied as a tool argument.
    result_consumer: str = "model"
    model_result_required: bool = False
    followup_default: str | None = None
    global_scope: bool = False


@dataclass
class ToolFollowupEnvelope:
    """Model-facing tool evidence whose producer owns completeness and continuation."""

    content: str
    producer_bounded: bool = False
    complete: bool = True
    continuation: Mapping[str, Any] | None = None
    diagnostics: Mapping[str, Any] | None = None

    def with_content(self, content: Any) -> "ToolFollowupEnvelope":
        return ToolFollowupEnvelope(
            content=str(content or ""),
            producer_bounded=bool(self.producer_bounded),
            complete=bool(self.complete),
            continuation=dict(self.continuation or {}) or None,
            diagnostics=dict(self.diagnostics or {}) or None,
        )


@dataclass
class ToolExecutionResult:
    tool_type: str
    raw_turns: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    followup_context: str = ""
    followup_envelope: ToolFollowupEnvelope | None = None
    state_updates: dict[str, Any] = field(default_factory=dict)
    # Optional compact navigation/retention record chosen by the producer.
    # MemCore stores it beside, never instead of, the complete model-visible
    # followup_context so the same observation remains available across turns.
    trace_receipt: Mapping[str, Any] | None = None
    # Internal-only provider image blocks. Never copy this field into prompt
    # text, stream events, logs, memcore, or public final output.
    model_image_inputs: list[dict[str, Any]] = field(default_factory=list)
    # Host diagnostics only: never part of model feedback or capability schema.
    execution_timing: dict[str, float] = field(default_factory=dict)
    # Bound by the host, separate from the plugin's result-level preference.
    followup: FollowupDecision | None = None
    # Canonical program result, separate from model previews and UI events.
    capability_result: CapabilityResult | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ToolExecutionAdmission:
    """Host-side admission result for a durable background invocation.

    ``call`` is the exact, normalized call that may be persisted and executed
    later.  ``result`` is an ordinary tool result when validation or approval
    stops submission in the foreground turn.
    """

    call: Mapping[str, Any] | None = None
    result: ToolExecutionResult | None = None

    @classmethod
    def allow(cls, call: Mapping[str, Any]) -> "ToolExecutionAdmission":
        return cls(call=dict(call))

    @classmethod
    def stop(cls, result: ToolExecutionResult) -> "ToolExecutionAdmission":
        return cls(result=result)


def operation_tool_result(
    *,
    tool_type: str,
    operation_result: Any,
    success_events: list[dict[str, Any]] | None = None,
    state_updates: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    """Map a service operation into one honest model-facing tool result.

    Generated-file and media services return dictionaries with ``ok`` plus a
    human-readable ``followup_context``.  Older handlers copied only the text,
    so ``ok=false`` with no stream event looked successful to the orchestration
    loop.  Keep successful payloads unchanged, but make every explicit failure
    observable and safe to append to the current MemCore turn.
    """

    payload = dict(operation_result) if isinstance(operation_result, Mapping) else {}
    followup = str(payload.get("followup_context") or "").strip()
    explicit_failure = not isinstance(operation_result, Mapping) or payload.get("ok") is False
    if not explicit_failure:
        return ToolExecutionResult(
            tool_type=tool_type,
            stream_events=[
                dict(event)
                for event in list(success_events or [])
                if isinstance(event, Mapping)
            ],
            followup_context=followup,
            state_updates=dict(state_updates or {}),
        )

    raw_reason = str(
        payload.get("error")
        or payload.get("reason")
        or payload.get("status")
        or "operation_failed"
    ).strip()
    reason = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw_reason)[:120] or "operation_failed"
    failure_event = {
        "type": "tool_execution_failed",
        "tool_type": str(tool_type or "unknown"),
        "status": "failed",
        "reason": reason,
    }
    if not followup:
        followup = f"工具 {tool_type} 没有完成（{reason}）。"
    if "<tool_use_error>" not in followup:
        followup = (
            f"<tool_use_error>{followup} "
            "请基于这个真实失败结果自然告诉用户；不要声称已经完成，也不要虚构产物句柄。"
            "</tool_use_error>"
        )
    return ToolExecutionResult(
        tool_type=tool_type,
        # A failed service response may still contain partial/stale artifact
        # fields. Never project success events from those fields alongside the
        # failure terminal state.
        stream_events=[failure_event],
        followup_context=followup,
        state_updates={
            **dict(state_updates or {}),
            "operation_failure": {
                "tool_type": str(tool_type or "unknown"),
                "reason": reason,
            },
        },
    )


@dataclass(frozen=True)
class ToolMetadata:
    family: str = "general"
    operation: str = "mixed"
    risk: str = "medium"
    default_round_budget: int = 3
    background: bool = False
    aliases: tuple[str, ...] = ()
    input_schema: Mapping[str, Any] | None = None
    requires_confirmation: bool = False

    @property
    def is_read_only(self) -> bool:
        return str(self.operation or "").strip().lower() == "read"


_BUILTIN_EXPORTS = frozenset({
    "BROWSER_PAGE_TOOL_SPEC",
    "BROWSE_MEMORY_TOOL_SPEC",
    "CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC",
    "FETCH_MEDIA_FROM_URL_TOOL_SPEC",
    "INSPECT_ATTACHMENT_TOOL_SPEC",
    "INSPECT_GENERATED_FILE_TOOL_SPEC",
    "INSPECT_MEDIA_INFO_TOOL_SPEC",
    "LIST_WORKSPACE_TOOL_SPEC",
    "LOAD_CHARACTER_CONTEXT_TOOL_SPEC",
    "LOAD_MATERIAL_TOOL_SPEC",
    "MANAGE_GENERATED_FILE_TOOL_SPEC",
    "OPEN_BROWSER_TOOL_SPEC",
    "OPEN_MEMORY_TOOL_SPEC",
    "OPEN_MUSIC_SEARCH_TOOL_SPEC",
    "ONEBOT_ACTION_TOOL_SPEC",
    "READ_ATTACHMENT_SECTION_TOOL_SPEC",
    "READ_MEMORY_TIMELINE_TOOL_SPEC",
    "READ_WORKSPACE_TOOL_SPEC",
    "REGISTER_WORKSPACE_ITEMS_TOOL_SPEC",
    "RETRIEVE_MEMORY_TOOL_SPEC",
    "RETRY_ATTACHMENT_TOOL_SPEC",
    "SEND_AUDIO_TOOL_SPEC",
    "SEND_FILE_TOOL_SPEC",
    "SEND_MUSIC_CARD_TOOL_SPEC",
    "SEND_STICKER_TOOL_SPEC",
    "WEB_SEARCH_TOOL_SPEC",
    "EXEC_CANCEL_TOOL_SPEC",
    "EXEC_RUN_TOOL_SPEC",
    "EXEC_STATUS_TOOL_SPEC",
    "LOAD_SKILL_TOOL_SPEC",
    "MANAGE_SKILL_TOOL_SPEC",
    "INVOKE_MCP_TOOL_SPEC",
    "LOAD_MCP_TOOL_SPEC",
    "MCP_MANAGE_TOOL_SPEC",
    "MANAGE_EXTENSION_TOOL_SPEC",
    "MANAGE_PROJECT_WORKSPACE_TOOL_SPEC",
    "PROJECT_INSPECT_TOOL_SPEC",
    "WORKSPACE_PATCH_TOOL_SPEC",
    "WORKSPACE_WRITE_TOOL_SPEC",
    "CAPABILITY_LOAD_TOOL_SPEC",
    "CAPABILITY_SEARCH_TOOL_SPEC",
    "INSPECT_MEDIA_INFO_INPUT_SCHEMA",
    "LOAD_CHARACTER_CONTEXT_INPUT_SCHEMA",
    "INSPECT_ATTACHMENT_INPUT_SCHEMA",
    "LOAD_MATERIAL_INPUT_SCHEMA",
    "READ_ATTACHMENT_SECTION_INPUT_SCHEMA",
    "LIST_WORKSPACE_INPUT_SCHEMA",
    "READ_WORKSPACE_INPUT_SCHEMA",
    "INSPECT_GENERATED_FILE_INPUT_SCHEMA",
    "TOOL_METADATA_BY_TYPE",
    "TOOL_SPEC_BY_TYPE",
    "_project_tool_metadata_from_spec",
})


def __getattr__(name):
    if name not in _BUILTIN_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import builtin_tables
    value = getattr(builtin_tables, name)
    globals()[name] = value
    return value


def _builtin_table(name):
    return globals()[name] if name in globals() else __getattr__(name)


class BaseToolHandler:
    tool_type: str = ""

    def tool_spec(self):
        """M66-C: Return the canonical ToolSpec for this tool. Handlers with an
        explicit override take precedence; all others resolve via TOOL_SPEC_BY_TYPE."""
        return _builtin_table("TOOL_SPEC_BY_TYPE").get(str(self.tool_type or "").strip())

    def tool_metadata(self) -> ToolMetadata:
        metadata = _builtin_table("TOOL_METADATA_BY_TYPE").get(str(self.tool_type or "").strip())
        if metadata is not None:
            return metadata
        return ToolMetadata()

    def build_prompt_instruction(self) -> str:
        from ..legacy_tool_prompt import render_legacy_json_tool_instruction

        spec = self.tool_spec()
        return render_legacy_json_tool_instruction(spec)

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def call_arguments(self, call):
        """Business input of a normalized flat legacy call, without its discriminator."""
        return {key: value for key, value in call.items() if key != "type" and not key.startswith("_tool_")}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        raise NotImplementedError

    def admit_execution(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionAdmission:
        """Validate and normalize a call before an execution is committed."""

        del context
        return ToolExecutionAdmission.allow(call)

    def execute_admitted(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute a call already admitted by :meth:`admit_execution`."""

        return self.execute(call=call, context=context)

    def background_job_policy(self) -> tuple[str, str]:
        """Return host completion and memory modes for a detached invocation."""

        return "agent", "timeline"
