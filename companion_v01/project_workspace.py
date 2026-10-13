"""Stable project workspace service and contract entry point.

Implementation owners live in project_workspace_runtime and share one service
instance, filesystem authority, and transaction lock.
"""
from __future__ import annotations

from .project_workspace_runtime.definitions import (
    ProjectWorkspaceError, ProjectWorkspaceScope,
    PROJECT_WRITE_MAX_CHARS, PROJECT_PATCH_MAX_CHARS,
    PROJECT_INSPECT_MAX_TEXT_BYTES, PROJECT_INSPECT_MAX_ENTRIES,
    PROJECT_INSPECT_MAX_FILES, PROJECT_INSPECT_MAX_SCAN_BYTES,
    PROJECT_INSPECT_MAX_MATCHES, PROJECT_INSPECT_SEARCH_TIMEOUT_SECONDS,
    PROJECT_INSPECT_SEARCH_PREVIEW_CHARS as PROJECT_INSPECT_SEARCH_PREVIEW_CHARS,
    _PROJECT_ID_RE as _PROJECT_ID_RE,
    _PATCH_BEGIN as _PATCH_BEGIN, _PATCH_END as _PATCH_END,
    _PATCH_FILE_PREFIXES as _PATCH_FILE_PREFIXES,
    _ROOT_KIND_MANAGED as _ROOT_KIND_MANAGED,
    _ROOT_KIND_HOST_BOUND as _ROOT_KIND_HOST_BOUND,
    _INSPECTION_SKIPPED_DIRECTORIES as _INSPECTION_SKIPPED_DIRECTORIES,
)
from .project_workspace_runtime.service import ProjectWorkspaceService
from .project_workspace_runtime.inspection import regex_engine as regex_engine

__all__ = [
    "PROJECT_INSPECT_MAX_ENTRIES",
    "PROJECT_INSPECT_MAX_FILES",
    "PROJECT_INSPECT_MAX_MATCHES",
    "PROJECT_INSPECT_MAX_SCAN_BYTES",
    "PROJECT_INSPECT_MAX_TEXT_BYTES",
    "PROJECT_INSPECT_SEARCH_TIMEOUT_SECONDS",
    "PROJECT_PATCH_MAX_CHARS",
    "PROJECT_WRITE_MAX_CHARS",
    "ProjectWorkspaceError",
    "ProjectWorkspaceScope",
    "ProjectWorkspaceService",
]
