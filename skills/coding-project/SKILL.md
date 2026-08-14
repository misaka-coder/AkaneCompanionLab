---
name: coding-project
description: Use when the user asks Akane to build or modify an executable program, script, interactive web app, game, or multi-file project that must actually run, be tested, or be debugged. Guides repository inspection, MVP scoping, incremental edits, real failure handling, verification, and honest delivery. Skip for one-off short text, config, documents, or static presentation pages that do not need runtime validation.
---

# Coding Project Loop

Goal: turn "generate code files" into "make code correct with real run results". This
Skill is a workflow manual, not new tools and not a permission upgrade. Everything runs
through the existing `exec_run` / `exec_status` / `exec_cancel`, workspace, `gen_*` and
`send_file` loop.

## 0. Honest ability check first

- If `exec_run` is not in this round's visible tools, say so directly: the code can be
  produced but not run or verified, and the reply must say "未运行验证".
- Do not pretend to have executed, tested, or opened anything the environment cannot
  actually do.

## 1. Scope an acceptable MVP and keep going

- For open-ended goals such as "还原 Minecraft", never promise a full recreation.
  State the concrete version being built now, for example: first-person movement,
  terrain, collision, dig, place, basic save. Extend only after that is verified.
- When the goal is clear enough, pick a reasonable MVP autonomously; do not force a
  question round-trip when the scope is obvious.
- Continue until that stated scope is implemented and checked, or a concrete external
  blocker remains. One failed command is evidence for the next step, not a reason to
  stop and ask the user to say "continue".

## 2. Check the environment, never assume

Use `exec_run` to check real facts before writing code:

- platform facts are given by the exec_run prompt info (platform / command shell /
  preferred script shell) — generate commands for the current host, not a guessed one.
- runtimes and dependencies the project needs: `python --version`, `node --version`,
  `npm --version`, `git --version`, `python -c "import pygame"` and so on.
- whether a previous project directory already exists in the workspace.
- Do not assume pygame, npm, ffmpeg, a browser, or any tool is installed just because
  the code would need it. A failed import/version check is the real evidence.

Run each check as its own command, or make failures propagate (see section 3). A
multi-step command where the last step succeeds can hide an earlier failure.

## 3. Read before editing; preserve real failure status

- For an existing project, inspect its current files, instructions, dependencies,
  working-tree state, and relevant tests before editing. Treat current filesystem
  contents and fresh command results as authoritative; a remembered or settled tool
  result may be stale. Preserve user work and existing conventions.
- Use the current host's real command shell. On POSIX, join dependent steps with `&&`;
  `set -e` helps simple scripts but is not a substitute for checking pipeline status.
  On Windows `cmd.exe`, use `&&` or inspect `errorlevel`. If explicitly invoking
  PowerShell, set `$ErrorActionPreference = "Stop"` for cmdlets and also check
  `$LASTEXITCODE` after native programs.
- Never chain `check_a; check_b` where `check_b` succeeding hides `check_a` failing.
  When several components must be judged independently, run them separately or
  inspect each exit code explicitly.
- `exit_code` 0 only proves the command finished; it does not prove the program is
  correct. Correctness comes from real output, tests, or interaction.

## 4. Build incrementally, edit locally

- create directory -> minimal entry point -> run it -> add one feature -> run again
  -> local fix.
- After the first scaffold, prefer small local edits to regenerating the whole project
  on every iteration. Never respond to one error by rewriting the entire file set.
- Keep implementation chunks small enough to inspect and validate. Do not put a large
  interactive project into one `compose_file` call or repeatedly resend unchanged
  source just to alter a few lines.
- Fix the root cause with the smallest coherent change. Do not repair unrelated code
  or chase unrelated failing tests. In a version-controlled project, inspect the diff
  before delivery so accidental rewrites and generated debris are visible.
- Use `exec_run` with a workspace-relative `cwd` (absolute paths are rejected for that
  field; commands may still read host paths discovered from real output).
- Only when the user needs the deliverable files do you declare them with
  `output_globs` so they register as `gen_*`; intermediate project files can simply
  stay in the workspace.

## 5. Keep long tasks observable

- Before an install, large generation, build, or test that may take noticeable time,
  briefly tell the user the concrete next step. Give occasional useful checkpoints,
  not narration for every trivial command.
- Do not pipe a live install/build through filters that suppress or buffer its progress
  (for example terminal-only `tail -5`). Filters are fine for bounded output that has
  already finished or when deliberately querying a saved log.
- First call `exec_run`. A result of `running` plus `run_id` is normal, not a failure.
- Then poll with `exec_status` using real incremental output to decide whether to
  continue. No new output by itself is not proof that a process is stuck; consider
  elapsed time, known command behavior, and terminal state before `exec_cancel`. Do
  not mechanically page through cursors.

## 6. Verification levels — declare which one you reached

- Level 1: syntax, compilation, or build passes (for example `python -m py_compile`,
  `node --check file.js`).
- Level 2: unit tests or a functional check script passes with real output.
- Level 3: real interactive verification in an actual running environment.

Web apps and games must reach at least Level 1. These levels are compact final-status
labels, not a script to recite during every tool round. Claim Level 3 only when a real
browser/runtime could access the exact tested build and returned observable evidence;
otherwise say naturally:
"代码已通过语法/构建检查，但当前环境没有完成真实浏览器交互验证。"

Run the narrowest relevant check after each edit, then broaden only when the change or
project risk justifies it. Do not spend the user's time on an unrelated full suite.

## 7. Web deliverables must match the deployment reality

Distinguish: desktop browser / QQ embedded browser / offline HTML / pages depending on
public CDNs. Before claiming a page works somewhere, state what it actually needs:

- public CDN scripts, Pointer Lock, WebGL, cross-origin resources, or browser
  permissions must be named explicitly. Calling `requestPointerLock()` in code is not
  evidence that QQ's embedded browser will render or capture anything.
- QQ mode has no browser tool: verification there is limited to Level 1/2.
  `browser_page` in desktop mode only navigates public HTTP(S) pages; it rejects
  localhost, private/intranet addresses, and `file:` paths, and it does not expose a
  JavaScript console. It can verify the visible state of a publicly reachable deployed
  page, not a local workspace artifact. Use a headless browser through `exec_run` only
  when one is actually installed and can open the tested build.
- `open_browser` only requests that a page be opened for the user. It is not execution
  evidence and cannot raise the verification level.
- For offline-delivered HTML prefer self-contained assets; if a CDN is unavoidable,
  say the file needs network access.

## 8. Delivery: queued is not received

- Deliverables go through `output_globs` -> `gen_*` registration -> `send_file` with
  the exact returned handle.
- Keep the three facts separate: file generated / file registered as gen_* / file
  entered the client delivery queue. `send_file` success only proves queue entry; the
  final reply may say "已进入发送队列/正在发送", never "你已经收到了" unless a later
  real confirmation arrives.

## 9. Failures drive the next step

- Use real stderr, failing tests, or browser errors as the input for the next fix.
- Tool success, file generation, exit code 0, or a `gen_*` appearing are never proof
  that the program works. Every iteration ends by running something real again.
