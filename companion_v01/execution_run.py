"""Stable execution contracts and tools; implementation lives in execution_runtime.

Providers own process lifecycle. The kernel shares one run store and lock for
ownership, ordered output pages, and provider-confirmed terminal notifications.
"""

from __future__ import annotations

import hashlib as hashlib

import logging as logging

import re as re

import threading as threading

import time as time

import uuid as uuid

from dataclasses import (
    dataclass as dataclass,
    field as field,
)

from typing import (
    Any as Any,
    Mapping as Mapping,
    Protocol as Protocol,
    runtime_checkable as runtime_checkable,
)

from .execution_specs import (
    EXEC_CWD_MAX_CHARS as EXEC_CWD_MAX_CHARS,
    EXEC_DEFAULT_MAX_LOG_BYTES as EXEC_DEFAULT_MAX_LOG_BYTES,
    EXEC_DEFAULT_MAX_RUNS as EXEC_DEFAULT_MAX_RUNS,
    EXEC_DEFAULT_RUN_RETENTION_SECONDS as EXEC_DEFAULT_RUN_RETENTION_SECONDS,
    EXEC_MAX_INITIAL_WAIT_SECONDS as EXEC_MAX_INITIAL_WAIT_SECONDS,
    EXEC_INITIAL_OUTPUT_MAX_BYTES as EXEC_INITIAL_OUTPUT_MAX_BYTES,
    EXEC_INITIAL_OUTPUT_MAX_LINES as EXEC_INITIAL_OUTPUT_MAX_LINES,
    EXEC_RUN_TOOL_NAME as EXEC_RUN_TOOL_NAME,
    EXEC_STATUS_TOOL_NAME as EXEC_STATUS_TOOL_NAME,
    EXEC_STATUS_OUTPUT_MAX_BYTES as EXEC_STATUS_OUTPUT_MAX_BYTES,
    EXEC_STATUS_OUTPUT_MAX_LINES as EXEC_STATUS_OUTPUT_MAX_LINES,
    EXEC_CANCEL_TOOL_NAME as EXEC_CANCEL_TOOL_NAME,
    EXEC_CURSOR_MAX_CHARS as EXEC_CURSOR_MAX_CHARS,
    EXEC_RUN_ID_MAX_CHARS as EXEC_RUN_ID_MAX_CHARS,
    EXEC_STATUS_CANCELLED as EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED as EXEC_STATUS_COMPLETED,
    EXEC_STATUS_EXECUTION_UNKNOWN as EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED as EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING as EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT as EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNAVAILABLE as EXEC_STATUS_UNAVAILABLE,
    EXEC_STATUS_UNKNOWN as EXEC_STATUS_UNKNOWN,
    EXEC_TERMINAL_STATUSES as EXEC_TERMINAL_STATUSES,
    normalize_initial_wait_seconds as normalize_initial_wait_seconds,
    normalize_status_wait_seconds as normalize_status_wait_seconds,
    normalize_timeout_seconds as normalize_timeout_seconds,
)

from .execution_runtime.models import (
    ExecutionRunOwner as ExecutionRunOwner,
    ExecutionAvailability as ExecutionAvailability,
    ExecRunStart as ExecRunStart,
    ExecRunStatus as ExecRunStatus,
    ExecCancelResult as ExecCancelResult,
    ExecMappedResult as ExecMappedResult,
    ExecutionRunTerminalEvent as ExecutionRunTerminalEvent,
    ExecutionProvider as ExecutionProvider,
    _OutputSegment as _OutputSegment,
    _RunRecord as _RunRecord,
)

from .execution_runtime.output import (
    _RUN_ID_PREFIX as _RUN_ID_PREFIX,
    _RUN_ID_RE as _RUN_ID_RE,
    _CURSOR_RE as _CURSOR_RE,
    new_run_id as new_run_id,
    is_valid_run_id as is_valid_run_id,
    _run_cursor_tag as _run_cursor_tag,
    make_cursor as make_cursor,
    parse_cursor as parse_cursor,
    _utf8_boundary_at_or_after as _utf8_boundary_at_or_after,
    _utf8_prefix_at_most as _utf8_prefix_at_most,
    _utf8_prefix_at_least as _utf8_prefix_at_least,
    _prefix_through_newlines as _prefix_through_newlines,
    _normalize_output_ref as _normalize_output_ref,
)

from .execution_runtime.store import (
    logger as logger,
    ExecutionRunStore as ExecutionRunStore,
    _unknown_status as _unknown_status,
)

from .execution_runtime.feedback import (
    _sanitize_model_text as _sanitize_model_text,
    _preview as _preview,
    _unknown_mapped as _unknown_mapped,
    _rejected_mapped as _rejected_mapped,
    _SILENT_RUNNING_HINT as _SILENT_RUNNING_HINT,
    _SILENT_COMPLETED_HINT as _SILENT_COMPLETED_HINT,
    _format_execution_output as _format_execution_output,
    _append_execution_markers as _append_execution_markers,
    _status_unknown_result as _status_unknown_result,
    _cancel_result as _cancel_result,
)

from .execution_runtime.mapping import (
    map_exec_run_outcome as map_exec_run_outcome,
    _unavailable_mapped as _unavailable_mapped,
)

from .execution_runtime.commands import (
    _READY_AVAILABILITY_STATUSES as _READY_AVAILABILITY_STATUSES,
    execute_exec_run as execute_exec_run,
    execute_exec_status as execute_exec_status,
    execute_exec_cancel as execute_exec_cancel,
)
