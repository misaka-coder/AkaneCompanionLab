---
name: coding-project
description: Use when the user asks Akane to build an executable program, script, web app, game, or multi-file project that must actually run, be tested, or be debugged. Guides MVP scoping, environment checks, incremental builds, real failure handling, verification levels, and honest delivery. Skip for one-off short text, config, or document generation, which compose_file already covers.
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

## 1. Scope an acceptable MVP

- For open-ended goals such as "还原 Minecraft", never promise a full recreation.
  State the concrete version being built now, for example: first-person movement,
  terrain, collision, dig, place, basic save. Extend only after that is verified.
- When the goal is clear enough, pick a reasonable MVP autonomously; do not force a
  question round-trip when the scope is obvious.

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

## 3. Commands must keep real failure status

- POSIX multi-step commands: start with `set -e` and join steps with `&&`.
- PowerShell scripts: `$ErrorActionPreference = "Stop"`.
- Never chain `check_a; check_b` where check_b succeeding makes the whole thing look
  successful. When several components must be judged independently, run them
  separately or inspect each exit code explicitly.
- `exit_code` 0 only proves the command finished; it does not prove the program is
  correct. Correctness comes from real output, tests, or interaction.

## 4. Build incrementally, edit locally

- create directory -> minimal entry point -> run it -> add one feature -> run again
  -> local fix.
- After the first scaffold, prefer small local edits to regenerating the whole project
  on every iteration. Never respond to one error by rewriting the entire file set.
- Use `exec_run` with a workspace-relative `cwd` (absolute paths are rejected for that
  field; commands may still read host paths discovered from real output).
- Only when the user needs the deliverable files do you declare them with
  `output_globs` so they register as `gen_*`; intermediate project files can simply
  stay in the workspace.

## 5. Long tasks must not hide progress

- Installs, builds, tests, and other long commands: do not append `| tail`, `| head`,
  or `| grep` filters, which can hide real progress from the model for minutes.
- First call `exec_run`. A result of `running` plus `run_id` is normal, not a failure.
- Then poll with `exec_status` using real incremental output to decide whether to
  continue; use `exec_cancel` only when the task is genuinely stuck. Do not
  mechanically page through cursors, and do not let the user believe the system is
  silently frozen.

## 6. Verification levels — declare which one you reached

- Level 1: syntax, compilation, or build passes (for example `python -m py_compile`,
  `node --check file.js`).
- Level 2: unit tests or a functional check script passes with real output.
- Level 3: real interactive verification in an actual running environment.

Web apps and games must reach at least Level 1. Claim Level 3 only when a real browser
capability is visible this round; otherwise say exactly:
"代码已通过语法/构建检查，但当前环境没有完成真实浏览器交互验证。"

## 7. Web deliverables must match the deployment reality

Distinguish: desktop browser / QQ embedded browser / offline HTML / pages depending on
public CDNs. Before claiming a page works somewhere, state what it actually needs:

- public CDN scripts, Pointer Lock, WebGL, cross-origin resources, or browser
  permissions must be named explicitly. Calling `requestPointerLock()` in code is not
  evidence that QQ's embedded browser will render or capture anything.
- QQ mode has no browser tool: verification there is limited to Level 1/2. In desktop
  mode `browser_page` can open the page; treat its returned page state as what was
  observed, and check console/parse errors before calling it done.
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
