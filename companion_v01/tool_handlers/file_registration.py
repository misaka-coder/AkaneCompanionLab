"""Shared authorized import for explicit registration and file handoff."""

from pathlib import Path

from ..project_workspace import ProjectWorkspaceError
from .core import ToolExecutionResult
from .project_workspace import _ProjectWorkspaceHandler


def register_file(*, service, generated_files, path, cwd, context, tool_type):
    if service is None:
        raise ProjectWorkspaceError("project_workspace_unconfigured")
    if not isinstance(path, str) or not path.strip() or not isinstance(cwd, str) or "\x00" in path + cwd:
        raise ProjectWorkspaceError("artifact_path_invalid")
    scope = _ProjectWorkspaceHandler(service=service)._scope(context)
    source = Path(path)
    if source.is_absolute():
        if cwd:
            raise ProjectWorkspaceError("cwd_and_absolute_path_conflict")
        cwd, source = str(source.parent), Path(source.name)
    if not cwd and context.execution_scope:
        cwd = context.execution_scope.working_directory
    if not cwd:
        cwd = str((service.current(scope=scope) or {}).get("working_directory") or service.execution_workspace_root)
    root = service.output_directory(scope=scope, cwd=cwd)
    _relative, source = service._inspection_target(root, str(source), allow_root=False)
    generated = generated_files.register_workspace_artifact(
        source=source, root=root, profile_user_id=context.profile_user_id, session_id=context.session_id,
        created_by_tool=tool_type, timestamp=context.now_ts,
    )
    return {"status": "succeeded", "handle": generated["generated_handle"], "name": source.name,
            "size_bytes": generated["file_size"], "sha256": generated["sha256"]}


def registration_failure(*, tool_type, error):
    # Keep useful reasons without exposing exception messages or host paths.
    if isinstance(error, ProjectWorkspaceError):
        reason = error.reason
    elif isinstance(error, FileNotFoundError):
        reason = "source_missing"
    elif isinstance(error, ValueError) and str(error) in {
        "artifact_source_too_large", "artifact_source_changed", "artifact_source_not_regular",
        "artifact_source_empty", "artifact_source_extension_unsupported",
    }:
        reason = str(error)
    else:
        reason = "artifact_registration_failed"
    return ToolExecutionResult(
        tool_type=tool_type,
        followup_context=(f"<tool_use_error>File was not registered or queued: {reason}. "
            "Relative paths use the calling task/project directory, never the plugin process directory. "
            "Use the producer's managed artifact handle, or its exact existing path in an authorized project. "
            "Do not substitute latest, guess another handle, or recreate the content to send it. "
            "A plugin export should return ManagedArtifactPayload; a plain path is not a delivery receipt."
            "</tool_use_error>"),
        stream_events=[{"type": "tool_execution_failed", "status": "failed", "reason": reason}],
    )
