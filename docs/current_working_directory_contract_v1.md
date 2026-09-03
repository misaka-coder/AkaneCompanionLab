# Current Working Directory Contract V1

> Status: implemented in the Akane host
> Date: 2026-09-03

## Goal

Coding tools use one conversation-scoped current working directory.  The host
does not infer a directory from ordinary user text, and project selection does
not grant filesystem permission.

## Model workflow

- New multi-file project: `manage_project_workspace(action=create, display_name=...)`.
- Existing host directory: discover or confirm its exact absolute path, then
  `manage_project_workspace(action=open, path=...)`.
- Existing registered project: `manage_project_workspace(action=select, workspace_id=...)`.
- Inspect the current selection: `manage_project_workspace(action=current)`.
- Leave it: `manage_project_workspace(action=close)`.

Create, open, and select immediately set the current directory.  The selection
persists for that conversation across tool rounds, replies, and host restarts
until another project is selected, the project is archived/missing, or close is
called.  There is no time-to-live.

## Tool resolution

When `cwd` and `workspace_id` are omitted:

1. `project_inspect`, `workspace_write`, `workspace_patch`, and `exec_run` use
   the selected project;
2. when no project is selected, they use the trusted execution root;
3. `exec_run` with `input_resources` keeps its isolated per-run directory;
4. an explicit `cwd` or `workspace_id` overrides one invocation only and does
   not mutate the conversation selection.

The request-tail execution facts expose the exact effective working directory,
project label, and existing `workspace_id`. Each of the four coding tools also
returns one `effective_cwd` fact; it does not repeat the complete project
record. Runtime database, cache, and run-log locations remain outside the
prompt because they are not coding coordinates.

## Permission boundary

The current directory is a coordinate, not an authorization token.  Every tool
invocation continues through its normal capability and operating-system access
checks.  Closing a project changes only default path resolution; it does not
delete files, archive the project, revoke permissions, or cancel a running
process.

## Cache and memory behavior

Project state is emitted in the existing request-time capability context, not
the stable persona/system prefix.  Its text stays byte-stable while the current
selection is unchanged and changes only after create/open/select/close or loss
of the selected directory.  MemCore tool traces continue to store the actual
tool arguments and results; no workspace-specific history or compression path
is added.

## Release SDK entry

When present, `alias:akane-sdk` resolves to the current release's public plugin
examples. It is a reference coordinate, not the active coding project and not a
permission grant. Generic coding prompts do not advertise it; a later
plugin-development Skill may disclose it only for plugin work. The exact public
Python contract remains the installed `companion_v01.plugin_api` module used by
those examples.

## Non-goals

- No path extraction from natural-language messages.
- No automatic scan of the host filesystem to guess a project.
- No new workspace permission system.
- No plugin-specific source-tree special case.
- No second task-workspace or attachment-workspace implementation.
