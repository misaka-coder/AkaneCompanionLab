"""Provider-agnostic internal representation of a tool call.

Step 2 of the tool-system decoupling (see `docs/tool_system_decoupling_v1.md`):
introduce a single internal shape that every tool-call source — the legacy
`{"type": name, ...args}` JSON field, and later native OpenAI / Anthropic
tool_use — normalises into, so the engine stops caring which provider produced
the call.

The live legacy dispatch path now routes already-normalized legacy tool calls
through this boundary before handing them back to the existing execute path. The
round-trip guarantee
`invocation_to_legacy_tool_call(legacy_tool_call_to_invocation(tc)) == tc`
holds for normalized dicts carrying a clean non-empty "type"; raw model output
is still normalized by the existing handlers first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


# Known invocation sources. Only LEGACY_JSON is produced today; the native
# variants are placeholders for the later migration steps.
LEGACY_JSON = "legacy_json"
NATIVE_OPENAI = "native_openai"
NATIVE_ANTHROPIC = "native_anthropic"
TOOL_SOURCE_FIELD = "_tool_source"
TOOL_INVOCATION_ID_FIELD = "_tool_invocation_id"
NATIVE_TOOL_CALL_FIELD = "_native_tool_call"


@dataclass
class ToolInvocation:
    """A single tool call, independent of how the model expressed it."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = LEGACY_JSON
    id: str = ""

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            self.id = f"call_{uuid.uuid4().hex[:16]}"


@dataclass
class ValidationResult:
    """Outcome of validating a ToolInvocation against a tool's contract.

    On failure, `message` is model-facing (fed back verbatim so the model can
    self-correct) and `code` is machine-readable for logging/metrics
    (e.g. "unknown_tool", "bad_args", "not_available").
    """

    ok: bool
    message: str = ""
    code: str = ""

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(ok=True)

    @classmethod
    def fail(cls, code: str, message: str) -> "ValidationResult":
        return cls(ok=False, code=str(code or ""), message=str(message or ""))


@dataclass
class ToolResultEnvelope:
    """Provider-agnostic result shape for future tool feedback plumbing.

    The live path still consumes ToolExecutionResult today. This envelope is
    introduced alongside ToolInvocation so later slices can move execution
    results to the same provider-neutral boundary without inventing another
    shape.
    """

    invocation_id: str
    status: str
    model_feedback: str
    data: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


def legacy_tool_call_to_invocation(
    tool_call: Any,
    *,
    source: str = LEGACY_JSON,
    invocation_id: str = "",
) -> ToolInvocation | None:
    """Wrap a legacy ``{"type": name, ...args}`` tool_call dict as a ToolInvocation.

    Returns None when the value is not a usable tool-call dict — callers must
    treat None exactly like "the model did not call a tool", which is how the
    current code already treats a falsy normalised tool_call. No behaviour change.
    """
    if not isinstance(tool_call, dict):
        return None
    name = str(tool_call.get("type") or "").strip()
    if not name:
        return None
    embedded_source = str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip()
    embedded_id = str(tool_call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
    arguments = {
        key: value
        for key, value in tool_call.items()
        if key != "type" and not str(key).startswith("_tool_")
    }
    return ToolInvocation(
        name=name,
        arguments=arguments,
        source=embedded_source or source,
        id=embedded_id or invocation_id,
    )


def invocation_to_legacy_tool_call(
    invocation: ToolInvocation,
    *,
    include_metadata: bool = False,
) -> dict[str, Any]:
    """Round-trip back to the legacy ``{"type": name, ...args}`` shape that the
    current execute path consumes.

    Keeping this exact inverse is what lets later steps route the live tool_call
    through ToolInvocation without changing which tool runs or with what args.
    """
    tool_call = {"type": invocation.name, **dict(invocation.arguments or {})}
    if include_metadata and str(invocation.source or LEGACY_JSON) != LEGACY_JSON:
        tool_call[TOOL_SOURCE_FIELD] = str(invocation.source or "")
        tool_call[TOOL_INVOCATION_ID_FIELD] = str(invocation.id or "")
    return tool_call


def round_trip_legacy_tool_call(tool_call: Any) -> dict[str, Any] | None:
    """Route an already-normalized legacy tool_call through ToolInvocation.

    This is the behaviour-preserving bridge for step 2b: old handlers still
    normalize and execute legacy dicts, while the live dispatch path starts
    crossing the new internal boundary.
    """
    invocation = legacy_tool_call_to_invocation(tool_call)
    if invocation is None:
        return None
    return invocation_to_legacy_tool_call(invocation)
