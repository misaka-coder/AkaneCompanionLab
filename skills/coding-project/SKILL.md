---
name: coding-project
description: Use for building or modifying executable programs, scripts, interactive web apps, games, or multi-file projects that must be run, tested, or debugged. Skip one-off text, documents, and static presentation files that need no runtime validation.
metadata:
  required_tools:
    - manage_project_workspace
    - workspace_write
    - workspace_patch
    - exec_run
    - exec_status
    - exec_cancel
---

# Coding Project Loop

Turn “generate code” into a project with real run evidence. This Skill adds no tools or
permissions; use only the tools visible in the current request.

## Establish the project authority

- Call `manage_project_workspace(action="current")` before editing. Continue in the
  selected project when it matches the request; otherwise list, select, create, or open one.
- The project catalog follows the same user across QQ private and group conversations.
  The current selection is conversation-local so concurrent tasks cannot switch each
  other's directory. A new conversation may need `list` then `select` even though the
  project already exists.
- The selected Project Workspace is the file-editing authority, not a filesystem sandbox.
  `workspace:/` is a material-reading namespace, not the project, and task-workspace
  records are not a filesystem.
- Use project-relative paths in file tools and `cwd="alias:project"` for project
  commands. Shell can inspect every host directory available to its operating-system
  user. Discover real directories with `pwd`, `find`, or the platform equivalent; never
  guess. Register an existing directory with `manage_project_workspace(action="open")`
  before using project file tools there.
- An archived or missing registered project is not writable through project file tools.
  Follow the structured reason, then select/create a project or open a real existing
  directory. Do not invent a substitute path.

## Scope a verifiable increment

- For open-ended requests, choose and state a concrete MVP that can be verified now.
  Continue until that scope works or a real external blocker remains.
- Inspect the selected project before changing it: relevant files, instructions,
  dependencies, version-control state, and narrow tests. Preserve user work and local
  conventions.

## Write through project tools

- Use `workspace_write` to create or replace UTF-8 source files. For an existing file,
  pass its latest SHA-256 when available so concurrent changes fail visibly.
- Use `workspace_patch` for local edits to existing UTF-8 files. It validates every
  hunk before committing; on `base_hash_mismatch` or `hunk_not_applicable`, reread and
  build a fresh patch. V1 patching does not create, delete, or rename files.
- Do not transport source through Shell, base64, here-docs, echoed strings, or long
  `-Command` arguments. If a write or patch exceeds its declared schema budget, split
  it into coherent files or edits; never hide missing content.
- Build in small verified increments. Fix the narrow root cause instead of regenerating
  the project or changing unrelated code.

## Execute and diagnose honestly

- Read the real platform, Shell, and toolchain manifest in the `exec_run` instruction.
  Language runtimes, version managers, and general CLIs come from the host PATH. Never
  download or unpack a runtime inside a project to work around an unavailable toolchain;
  report the structured blocker so the host can be provisioned once.
- Keep dependency manifests and lock files per project, but use the package manager's
  host-shared cache/store. Respect an existing Node project's `packageManager` field and
  lock file; for a new Node project, prefer `pnpm` when available because its
  content-addressed store avoids physically duplicating packages while each project's
  dependency graph remains version-correct. For Python, Rust, Go, Java, and other
  ecosystems, respect the existing project environment and lock files; create a new
  project-local environment only when version isolation is actually required.
- Run registered project commands with `cwd="alias:project"`; for host inspection or
  administration, an absolute cwd discovered from real output is valid. Shell is for inspect/build/run/test,
  not source transfer. A rejected `command_too_long` means use `workspace_write` or
  `workspace_patch`, not retry a differently quoted giant command.
- Preserve dependent-step failures. Use `&&` or explicit status checks on POSIX/cmd.
  In PowerShell, set `$ErrorActionPreference = "Stop"` for cmdlets and check
  `$LASTEXITCODE` after native programs. Never let a later success mask an earlier
  failure.
- `running` plus `run_id` is a live task, not failure. Use `exec_status` and its cursor;
  cancel only when warranted, and claim cancellation only after confirmed `cancelled`.
- Long tool results are complete logical pages. If the current page is enough, answer;
  otherwise use its cursor. On stale/changed cursors, do not combine old and new pages.
- Treat `status`, `reason`, `recommended_action`, stdout, stderr, and exit code as the
  next-step API. Do not blindly repeat an unchanged failed call.

## Verify and deliver

- Level 1: syntax, compilation, or build passes (for example `python -m py_compile`,
  `node --check file.js`).
- Level 2: unit tests or a functional check script passes with real output.
- Level 3: real interactive verification in an actual running environment.
- Run the narrowest relevant check after each edit, then broaden according to risk.
  Exit code 0, a generated file, or queue entry alone does not prove correctness.
- Claim only the level actually observed. Without a real browser/runtime observation,
  say interactive verification was not run. `open_browser` is a request, not evidence.
- Register deliverables with `output_globs` relative to `alias:project`, then use the
  returned `gen_*` handle with `send_file` when that tool is available. Queue success
  means “已进入发送队列”, not that the user received it.
