"""Execution provider contracts and the shared in-memory run record."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from typing import (
    Any,
    Mapping,
    Protocol,
    runtime_checkable,
)

from ..execution_specs import (
    EXEC_DEFAULT_TIMEOUT_SECONDS,
    EXEC_MAX_INITIAL_WAIT_SECONDS,
    EXEC_STATUS_RUNNING,
)


@dataclass(frozen=True, slots=True)
class ExecutionRunOwner:
    """Host-only ownership scope; never project this into model-visible data."""

    profile_user_id: str
    session_id: str
    provider_id: str

    def __post_init__(self) -> None:
        for field_name in ("profile_user_id", "session_id", "provider_id"):
            clean = str(getattr(self, field_name) or "").strip()
            if not clean:
                raise ValueError(f"execution_owner_{field_name}_required")
            object.__setattr__(self, field_name, clean)


@dataclass(frozen=True, slots=True)
class ExecutionAvailability:
    enabled: bool
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecRunStart:
    status: str
    run_id: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    next_cursor: str | None = None
    output_ref: str | None = None
    reason: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ExecRunStatus:
    status: str
    run_id: str
    exit_code: int | None = None
    tail: str = ""
    next_cursor: str | None = None
    output_ref: str | None = None
    reason: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ExecCancelResult:
    ok: bool
    status: str
    run_id: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecMappedResult:
    envelope_status: str
    event_status: str
    reason: str
    model_feedback: str
    data: Mapping[str, Any]
    event: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutionRunTerminalEvent:
    """One provider-confirmed terminal transition, emitted exactly once."""

    run_id: str
    owner: ExecutionRunOwner
    status: str
    exit_code: int | None
    reason: str
    finished_at: float


@runtime_checkable
class ExecutionProvider(Protocol):
    def availability(self) -> ExecutionAvailability: ...

    def run(
        self,
        *,
        owner: ExecutionRunOwner,
        command: str,
        cwd: str = "",
        timeout_seconds: int = EXEC_DEFAULT_TIMEOUT_SECONDS,
        initial_wait_seconds: int = EXEC_MAX_INITIAL_WAIT_SECONDS,
        run_id: str = "",
        background: bool = False,
    ) -> ExecRunStart: ...

    def status(
        self,
        *,
        owner: ExecutionRunOwner,
        run_id: str,
        cursor: str | None = None,
    ) -> ExecRunStatus: ...

    def cancel(self, *, owner: ExecutionRunOwner, run_id: str) -> ExecCancelResult: ...


@dataclass
class _OutputSegment:
    stream: str
    data: bytes


@dataclass
class _RunRecord:
    run_id: str
    owner: ExecutionRunOwner
    output_ref: str | None = None
    status: str = EXEC_STATUS_RUNNING
    exit_code: int | None = None
    reason: str = ""
    cancel_requested: bool = False
    segments: list[_OutputSegment] = field(default_factory=list)
    window_start_bytes: int = 0
    retained_bytes: int = 0
    absolute_end_bytes: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float = 0.0
