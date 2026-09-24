---
name: coding-project
description: Use for building or modifying executable programs, scripts, interactive web apps, games, or multi-file projects that must be run, tested, or debugged. Skip one-off text, documents, and static presentation files that need no runtime validation.
metadata:
  required_tools:
    - project_inspect
    - workspace_write
    - workspace_patch
    - exec_run
    - exec_status
    - exec_cancel
---

# Coding Project Loop

Turn “generate code” into a project with real run evidence. This Skill adds no tools or
permissions; use only the tools visible in the current request.

## Keep engineering authoritative

- During a coding task, character style may shape wording, but it must not change the
  requested scope, technical judgment, verification standard, or completion criteria.
- Convert explicit requirements into a short acceptance checklist before substantial
  edits. Inspect enough of the repository and its conventions to choose a coherent
  approach, then execute; do not substitute a smaller MVP for requirements the user
  already specified. Planning is preparation for the next real action, not a separate
  ceremony or a reason to delay useful work.
- When `manage_task_workspace` is visible and the task has many acceptance items or may
  span a long run, use it as a lightweight task checklist. Update it at meaningful phase
  boundaries or when evidence changes, not after every command. The checklist supports
  execution; it must not become a gate before useful tools can run.

## Work in the real project

- Use the real task directory supplied by the host, or discover it from tool output; never
  guess. For a new multi-file project, call `manage_project_workspace(create)` with a clear
  display name. For an existing host directory, confirm the exact absolute directory from
  tool output and call `manage_project_workspace(open)`. Use `select` for an already
  registered project.
- Create, open, and select set the conversation's current working directory. Afterwards,
  `project_inspect`, `workspace_write`, `workspace_patch`, and `exec_run` all use it when
  `cwd`/`workspace_id` is omitted. An explicit cwd overrides one call only. Use `close`
  when the user wants to leave the project; closing does not delete files or change
  permissions.
- A Project Workspace is a persistent directory identity, not a file-access permission.
  Tool and operating-system permissions are still evaluated for every operation.
- `workspace:/` is a material-reading namespace, not the coding directory, and
  task-workspace records are not a filesystem.

## Inspect and edit deliberately

- Inspect the relevant files and repository conventions before changing them. Use
  `project_inspect` for precise code context and Shell for version-control or specialized
  repository queries. Preserve user work and unrelated changes.
- Use `workspace_write` for creation or deliberate full replacement and `workspace_patch`
  for targeted atomic edits. Their current tool definitions own the exact syntax, path,
  hash, and paging contracts. When a tool reports stale state or a non-applicable edit,
  reread the affected region and follow its structured feedback before retrying.
- Shell-based generation remains valid when it is the clearest mechanical workflow;
  keep its output inspectable and verify the resulting files.
- Build in small verified increments. Fix the narrow root cause instead of regenerating
  the project or changing unrelated code.

## Execute and diagnose honestly

- Read the platform, Shell, filesystem, and runtime facts supplied by the host. Probe exact
  availability or versions only when the task needs them. Do not download a language
  runtime into a project to work around a missing host toolchain; report the blocker.
- Keep dependency manifests and lock files per project, but use the package manager's
  host-shared cache/store. Respect the existing package manager, project environment, and
  lock files; create project-local isolation only when version isolation is needed.
- Preserve dependent-step failures. Use `&&` or explicit status checks on POSIX/cmd.
  In PowerShell, set `$ErrorActionPreference = "Stop"` for cmdlets and check
  `$LASTEXITCODE` after native programs. Never let a later success mask an earlier
  failure.
- `running` plus `run_id` is a live task, not failure. Use `exec_status` and its cursor;
  cancel only when warranted, and claim cancellation only after confirmed `cancelled`.
- Treat `status`, `reason`, `recommended_action`, stdout, stderr, and exit code as the
  next-step API. Do not blindly repeat an unchanged failed call.

## Verify and deliver

- Run the narrowest relevant check after each edit, then broaden according to risk.
  Exit code 0, a generated file, or queue entry alone does not prove correctness.
- A command snippet is not an automated test suite. If the request requires tests, create
  durable test files, run the declared suite, and report its real count. Do not claim
  “full tests” when only ad hoc shell assertions were run.
- Claim only what was actually observed. A browser-open request, generated artifact, or
  later final state is not evidence of behavior that was never exercised.
- Before finishing, reconcile the acceptance checklist against real files and command
  output. Report what passed, what was not run, and any remaining blocker; a progress
  narration or generated file alone is not completion evidence.
- Keep working in the same user turn while requested, executable work remains. Each
  decision either issues the next real tool call or delivers an honest user reply;
  never emit a tool-less `status="continue"` frame and expect the host to guess the
  next action.
