"""Stable execution tool handlers and existing contracts.

Implementation lives in execution_runtime; provider, approval, resource and
project services remain shared by each handler instance.
"""

from __future__ import annotations

from dataclasses import (
    replace as replace,
)

from pathlib import (
    Path as Path,
)

from typing import (
    Any as Any,
)

import config as config

from capcore import (
    PermissionDecision as PermissionDecision,
)

from ..capcore_runtime import (
    authorization_profile_user_id as authorization_profile_user_id,
    approval_required_event as approval_required_event,
    manual_permission_request as manual_permission_request,
    resolve_permission_for_profile as resolve_permission_for_profile,
)

from ..capability_approval import (
    build_approval_request_fingerprint as build_approval_request_fingerprint,
)

from ..execution_resources import (
    ExecutionResourceBridge as ExecutionResourceBridge,
    ExecutionResourceScope as ExecutionResourceScope,
)

from ..execution_run import (
    ExecutionRunOwner as ExecutionRunOwner,
    execute_exec_cancel as execute_exec_cancel,
    execute_exec_run as execute_exec_run,
    execute_exec_status as execute_exec_status,
    new_run_id as new_run_id,
)

from ..execution_specs import (
    ARTIFACT_STATUS_NOT_REQUESTED as ARTIFACT_STATUS_NOT_REQUESTED,
    ARTIFACT_STATUS_REGISTRATION_FAILED as ARTIFACT_STATUS_REGISTRATION_FAILED,
    EXEC_CWD_MAX_CHARS as EXEC_CWD_MAX_CHARS,
    EXEC_CANCEL_TOOL_SPEC as EXEC_CANCEL_TOOL_SPEC,
    EXEC_INPUT_TOOL_SPEC as EXEC_INPUT_TOOL_SPEC,
    EXEC_RUN_TOOL_SPEC as EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_CANCELLED as EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED as EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED as EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING as EXEC_STATUS_RUNNING,
    EXEC_STATUS_TOOL_SPEC as EXEC_STATUS_TOOL_SPEC,
    EXEC_STATUS_TIMED_OUT as EXEC_STATUS_TIMED_OUT,
)

from ..project_workspace import (
    ProjectWorkspaceError as ProjectWorkspaceError,
    ProjectWorkspaceService as ProjectWorkspaceService,
)

from .core import (
    BaseToolHandler as BaseToolHandler,
    ToolExecutionContext as ToolExecutionContext,
    ToolExecutionResult as ToolExecutionResult,
    ToolFollowupEnvelope as ToolFollowupEnvelope,
)

from .execution_runtime.base import (
    _ExecToolHandlerBase as _ExecToolHandlerBase,
)

from .execution_runtime.run import (
    ExecRunToolHandler as ExecRunToolHandler,
)

from .execution_runtime.control import (
    ExecInputToolHandler as ExecInputToolHandler,
    ExecStatusToolHandler as ExecStatusToolHandler,
    ExecCancelToolHandler as ExecCancelToolHandler,
)
