"""Stable host-local executor entry point and existing execution contracts.

Implementation owners share one provider instance, run store, and ordering lock.
Windows commands use the disclosed PowerShell host; execution retains host-user authority.
"""
from __future__ import annotations

import os as os
import sys as sys
import subprocess as subprocess

from .execution_run import (
    ExecutionAvailability as ExecutionAvailability,
    ExecutionProvider as ExecutionProvider,
    ExecutionRunOwner as ExecutionRunOwner,
    ExecutionRunStore as ExecutionRunStore,
    ExecCancelResult as ExecCancelResult,
    ExecRunStart as ExecRunStart,
    ExecRunStatus as ExecRunStatus,
    is_valid_run_id as is_valid_run_id,
    make_cursor as make_cursor,
    new_run_id as new_run_id,
    parse_cursor as parse_cursor,
)
from .execution_specs import (
    EXEC_CWD_MAX_CHARS as EXEC_CWD_MAX_CHARS,
    EXEC_MAX_INITIAL_WAIT_SECONDS as EXEC_MAX_INITIAL_WAIT_SECONDS,
    EXEC_STATUS_CANCELLED as EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED as EXEC_STATUS_COMPLETED,
    EXEC_STATUS_EXECUTION_UNKNOWN as EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED as EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING as EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT as EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNKNOWN as EXEC_STATUS_UNKNOWN,
    EXEC_TERMINAL_STATUSES as EXEC_TERMINAL_STATUSES,
    normalize_initial_wait_seconds as normalize_initial_wait_seconds,
    normalize_timeout_seconds as normalize_timeout_seconds,
)
from .local_execution_runtime.executor import (
    TrustedLocalExecutor as TrustedLocalExecutor,
)
from .local_execution_runtime.paths import (
    _ALIAS_PREFIX as _ALIAS_PREFIX,
    _ALIAS_RE as _ALIAS_RE,
    ExecutionPathError as ExecutionPathError,
)
from .local_execution_runtime.environment import (
    _PROXY_PROBE_TIMEOUT_SECONDS as _PROXY_PROBE_TIMEOUT_SECONDS,
    _PROXY_PROBE_TTL_SECONDS as _PROXY_PROBE_TTL_SECONDS,
    _PROXY_CONNECT_TARGET as _PROXY_CONNECT_TARGET,
    _PROXY_ENV_NAMES as _PROXY_ENV_NAMES,
    _SENSITIVE_ENV_NAME_RE as _SENSITIVE_ENV_NAME_RE,
    _HOST_INTERNAL_ENV_PREFIXES as _HOST_INTERNAL_ENV_PREFIXES,
    _CREDENTIAL_ENV_NAME_RE as _CREDENTIAL_ENV_NAME_RE,
)
from .local_execution_runtime.shell import (
    _LEADING_QUOTED_WINDOWS_EXECUTABLE_RE as _LEADING_QUOTED_WINDOWS_EXECUTABLE_RE,
    _split_windows_command_line as _split_windows_command_line,
)
from .local_execution_runtime.process import (
    _CANCEL_CONFIRM_GRACE_SECONDS as _CANCEL_CONFIRM_GRACE_SECONDS,
    _OUTPUT_DRAIN_GRACE_SECONDS as _OUTPUT_DRAIN_GRACE_SECONDS,
)
from .local_execution_runtime.output import (
    _RUN_LOG_RE as _RUN_LOG_RE,
    _READ_CHUNK_BYTES as _READ_CHUNK_BYTES,
    _CREDENTIAL_OUTPUT_MARKER as _CREDENTIAL_OUTPUT_MARKER,
    _SecretStreamRedactor as _SecretStreamRedactor,
    _unknown_status as _unknown_status,
    _bounded_utf8_prefix as _bounded_utf8_prefix,
)
