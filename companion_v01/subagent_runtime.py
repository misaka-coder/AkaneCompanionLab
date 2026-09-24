"""Provider-neutral contracts for isolated child-agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


SUBAGENT_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
SUBAGENT_CONTEXT_FIELDS = frozenset({
    "client_mode", "actor_stable_id", "actor_profile_user_id", "authorization_profile_user_id",
    "character_pack_id", "model_role", "route_fingerprint", "thinking_mode",
})
_CHILD_SESSION_ID_RE = re.compile(r"^subagent_[a-f0-9]{32}$")
_MAX_TASK_CHARS = 24_000
_MAX_LABEL_CHARS = 80
_MAX_SUMMARY_CHARS = 4_000
_MAX_ARTIFACTS = 32


@dataclass(frozen=True, slots=True)
class SubagentProviderCapabilities:
    tool_filter: bool = False
    workspace: bool = False


@dataclass(frozen=True, slots=True)
class SubagentStartRequest:
    task: str
    child_session_id: str
    parent_profile_user_id: str
    parent_session_id: str
    label: str = ""
    working_directory: str = ""
    allowed_tools: tuple[str, ...] = ()
    model: str = ""
    reasoning_effort: str = ""
    execution_context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubagentRunResult:
    status: str
    child_session_id: str
    summary: str = ""
    artifacts: tuple[dict[str, str], ...] = ()
    reason: str = ""


@runtime_checkable
class SubagentProvider(Protocol):
    name: str
    capabilities: SubagentProviderCapabilities

    def execute(
        self,
        request: SubagentStartRequest,
        *,
        cancelled: Callable[[], bool],
    ) -> SubagentRunResult: ...


class SubagentProviderRegistry:
    """Resolve configured child transports without exposing them to the model."""

    def __init__(self) -> None:
        self._providers: dict[str, SubagentProvider] = {}

    def register(self, provider: SubagentProvider) -> None:
        name = str(getattr(provider, "name", "") or "").strip()
        capabilities = getattr(provider, "capabilities", None)
        if not name or not callable(getattr(provider, "execute", None)):
            raise ValueError("subagent_provider_invalid")
        if not isinstance(capabilities, SubagentProviderCapabilities):
            raise ValueError("subagent_provider_capabilities_invalid")
        if name in self._providers:
            raise ValueError("subagent_provider_duplicate")
        self._providers[name] = provider

    def available(self, name: str) -> bool:
        return str(name or "").strip() in self._providers

    def validate(self, name: str, request: SubagentStartRequest) -> str:
        provider = self._providers.get(str(name or "").strip())
        if provider is None:
            return "subagent_provider_unavailable"
        return validate_subagent_request(request, capabilities=provider.capabilities)

    def execute(
        self,
        name: str,
        request: SubagentStartRequest,
        *,
        cancelled: Callable[[], bool],
    ) -> SubagentRunResult:
        provider = self._providers.get(str(name or "").strip())
        reason = self.validate(name, request)
        if reason:
            return _failure(request, reason)
        if cancelled():
            return _cancelled(request)
        try:
            result = provider.execute(request, cancelled=cancelled)
        except Exception as exc:
            return _failure(request, f"subagent_provider_{type(exc).__name__}")
        normalized = normalize_subagent_result(result, expected_child_session_id=request.child_session_id)
        return normalized or _failure(request, "subagent_result_invalid")


class InProcessSubagentProvider:
    """Call one host-supplied child driver behind the common provider seam."""

    name = "in_process"

    def __init__(
        self,
        runner: Callable[..., SubagentRunResult],
        *,
        capabilities: SubagentProviderCapabilities | None = None,
    ) -> None:
        if not callable(runner):
            raise TypeError("subagent_runner_required")
        self._runner = runner
        self.capabilities = capabilities or SubagentProviderCapabilities(
            tool_filter=True,
            workspace=True,
        )

    def execute(
        self,
        request: SubagentStartRequest,
        *,
        cancelled: Callable[[], bool],
    ) -> SubagentRunResult:
        return self._runner(request, cancelled=cancelled)


def validate_subagent_request(
    request: Any,
    *,
    capabilities: SubagentProviderCapabilities,
) -> str:
    if not isinstance(request, SubagentStartRequest):
        return "subagent_request_required"
    if not isinstance(request.task, str) or not request.task.strip() or len(request.task) > _MAX_TASK_CHARS or "\x00" in request.task:
        return "subagent_task_invalid"
    if not isinstance(request.child_session_id, str) or not _CHILD_SESSION_ID_RE.fullmatch(request.child_session_id):
        return "subagent_child_session_invalid"
    parent_ids = (request.parent_profile_user_id, request.parent_session_id)
    if any(not isinstance(item, str) or not item.strip() or len(item) > 500 or "\x00" in item for item in parent_ids):
        return "subagent_parent_context_required"
    if not isinstance(request.label, str) or len(request.label) > _MAX_LABEL_CHARS or "\x00" in request.label:
        return "subagent_label_invalid"
    if not isinstance(request.working_directory, str) or "\x00" in request.working_directory:
        return "subagent_workspace_invalid"
    if request.working_directory and not capabilities.workspace:
        return "subagent_workspace_unsupported"
    if not isinstance(request.allowed_tools, tuple):
        return "subagent_tool_filter_invalid"
    if request.allowed_tools and not capabilities.tool_filter:
        return "subagent_tool_filter_unsupported"
    if any(not isinstance(item, str) or not item.strip() or "\x00" in item for item in request.allowed_tools):
        return "subagent_tool_filter_invalid"
    if len(request.allowed_tools) != len(set(request.allowed_tools)):
        return "subagent_tool_filter_invalid"
    if any(not isinstance(item, str) or len(item) > 200 or "\x00" in item for item in (request.model, request.reasoning_effort)):
        return "subagent_model_route_invalid"
    if not isinstance(request.execution_context, Mapping) or any(
        key not in SUBAGENT_CONTEXT_FIELDS or not isinstance(value, str) or len(value) > 500 or "\x00" in value
        for key, value in request.execution_context.items()
    ):
        return "subagent_execution_context_invalid"
    return ""


def normalize_subagent_result(
    value: Any,
    *,
    expected_child_session_id: str,
) -> SubagentRunResult | None:
    if not isinstance(value, SubagentRunResult):
        return None
    status = str(value.status or "").strip().lower()
    child_session_id = str(value.child_session_id or "").strip()
    summary = str(value.summary or "").strip()[:_MAX_SUMMARY_CHARS]
    reason = str(value.reason or "").strip()[:500]
    if status not in SUBAGENT_TERMINAL_STATUSES or child_session_id != expected_child_session_id:
        return None
    artifacts = _normalize_artifacts(value.artifacts)
    if artifacts is None:
        return None
    if status == "succeeded" and not summary and not artifacts:
        return None
    if status != "succeeded" and not reason:
        return None
    return SubagentRunResult(
        status=status,
        child_session_id=child_session_id,
        summary=summary,
        artifacts=artifacts,
        reason=reason,
    )


def _normalize_artifacts(value: Any) -> tuple[dict[str, str], ...] | None:
    if not isinstance(value, (tuple, list)) or len(value) > _MAX_ARTIFACTS:
        return None
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        handle = str(item.get("handle") or "").strip()
        source = str(item.get("source") or "subagent").strip()[:80]
        if not handle or len(handle) > 500 or "\x00" in handle:
            return None
        if handle in seen:
            continue
        seen.add(handle)
        normalized.append({"handle": handle, "source": source or "subagent"})
        digest = str(item.get("sha256") or "")
        if digest:
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                return None
            normalized[-1]["sha256"] = digest
    return tuple(normalized)


def _failure(request: Any, reason: str) -> SubagentRunResult:
    return SubagentRunResult(
        status="failed",
        child_session_id=str(getattr(request, "child_session_id", "") or ""),
        reason=str(reason or "subagent_failed")[:500],
    )


def _cancelled(request: SubagentStartRequest) -> SubagentRunResult:
    return SubagentRunResult(
        status="cancelled",
        child_session_id=request.child_session_id,
        reason="subagent_cancelled",
    )


__all__ = [
    "InProcessSubagentProvider",
    "SUBAGENT_TERMINAL_STATUSES",
    "SubagentProvider",
    "SubagentProviderCapabilities",
    "SubagentProviderRegistry",
    "SubagentRunResult",
    "SubagentStartRequest",
    "normalize_subagent_result",
    "validate_subagent_request",
]
