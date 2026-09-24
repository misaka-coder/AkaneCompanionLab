---
name: plugin-development
description: Use when creating, testing, updating, installing, or debugging an Akane plugin. Covers the public `akane_plugin` SDK, permissions, isolated staging, publication, activation, and real runtime verification. Skip ordinary application code and MCP or Skill-only work.
metadata:
  required_tools:
    - manage_project_workspace
    - project_inspect
    - workspace_write
    - workspace_patch
    - exec_run
    - exec_status
    - exec_cancel
    - manage_extension
---

# Akane Plugin Development

Write the plugin as an ordinary tested Python project, then install it through
Akane's managed artifact lifecycle. This Skill adds no tools or permissions.

## Start from the public SDK

- Create a dedicated project with `manage_project_workspace(create)`, or open the
  exact existing source directory. Keep source, tests and build metadata there.
- Read the current examples before writing code:
  `project_inspect(action="read", cwd="alias:akane-sdk", path="README.md")`, then
  the example closest to the requested behavior. `alias:akane-sdk` is the
  read-only reference for the running release; never write into it, and do not
  rely on remembered method names.
- Every new plugin imports only `akane_plugin`. Its `pyproject.toml` declares the
  `akane.plugins.v1` entry point and a `akane-plugin>=0.14,<0.15` dependency. Do not
  import `companion_v01`, Engine, MemCore, the QQ gateway or private host paths.
- Common shapes:
  - `@plugin.tool` on a typed function — one model-callable capability.
  - `@plugin.on(event_type)` — react to a published event without waking the model.
  - `@plugin.qq_command("/name")` — a plain QQ command handler that runs before the
    model and can answer directly.
  - `@plugin.background("service_id")` — a supervised background service for work
    that must keep running between turns, such as polling an external state.
  - `@plugin.service("id", version=1)` plus `@service.method` — a versioned service
    another plugin (or the host) can depend on.
  - `@plugin.connection("name")` — a declared connection the host stores, projects
    into a settings form, and resolves for authorized calls.
  - `@plugin.policy("id").before/wrap/transform/present/observe` — deployer-selected
    execution policy, not business logic.
- Declare only the permissions the implementation actually exercises. `@plugin.tool`
  adds prompt invocation automatically; event-only and service-only plugins must
  not fake a model tool to become loadable.
- `execution_class="long_task"` detaches a slow tool into the host's existing job
  path; `followup="none"` on a result removes only that result's implicit model
  reply. Both are declarations, not new runtime code. Read the SDK README section
  "Declare how a tool runs" for the exact contract.

## Keep the boundary honest

- Plugins talk to the host only through public SDK objects: tools, events,
  observations, turn requests, services, connections, resources and artifacts.
  They never read or write memory storage, construct host identity, or send
  channel messages directly.
- Four author intents stay separate: `emit` notifies subscribers, `observe` updates
  what the next model decision sees, `request_turn` asks the normal conversation
  queue for a reply, and leaving a durable shared experience is a separate,
  explicit capability. A plugin does not need to understand MemCore to choose.
- Return the canonical JSON value from `execute`. Do not make callers parse prose
  for ids or fields, and do not report a queued, pending or failed step as done.
- Long work belongs to `execution_class="long_task"` or a supervised background
  service; do not invent a scheduler inside the plugin.

## Test, stage, and activate

For file exports, return `ManagedArtifactPayload` with a `ManagedArtifactDraft`
instead of a JSON `path`. Use `ManagedArtifactDraft.from_file(existing_path,
send_to_user=...)` for an existing producer-local file, or the bytes constructor
for in-memory content. Declare `artifact.write` and a file output with
`delivery="generated_file"` and an appropriate `max_bytes`. The host returns a
conversation-bound `gen_*` handle. The plugin process directory is not the user's
selected project, and returning a relative path does not register or send it.
Keep source files alive until the invocation finishes; do not delete temporary
files before the host consumes the result. The SDK README has a complete export
example. Test host materialization and the returned handle, not merely disk writes.

For an already existing authorized project file, `send_file(path=..., cwd=...)`
registers and queues its original bytes in one call; use the exact handle when
one already exists. No copy into the selected workspace is needed. Unknown file
formats can be delivered as files; native image/voice playback has a narrower
support range. Registration and queue admission do not prove recipient delivery.

1. Write ordinary `unittest` tests against the real `akane_plugin` imports. Do not
   replace `akane_plugin`, `capcore` or adapters with `sys.modules` stubs.
2. Call `manage_extension(action="test_source", path=<absolute working_directory>)`.
   The host runs `unittest` in a child process against the current release SDK
   without installing or activating the plugin. Fix failures until it returns
   `passed`.
3. Call `manage_extension(action="stage_source", path=<absolute working_directory>)`.
   The host builds a wheel and probes it in isolation; staging does not activate it.
4. Review the returned plugin id, contributions, permissions and requirements. Fix
   and stage again if they do not match the intended design.
5. Call `manage_extension(action="install", stage_id=..., approved_permissions=[...])`,
   copying the exact permissions array returned by that stage. The host publishes
   and activates the immutable stage as one operation.
6. Call `manage_extension(action="list")`, exercise the real tool, event, service or
   command, and verify the plugin is active. Source tests alone do not prove host integration.
   For a new model tool, use the exact ID from the capability update or
   `capability_list`, call `capability_load(capability_ids=[...])`, then
   `capability_invoke(capability_id=..., contract_ref=..., arguments={...})`.
   Installation does not require a restart or a new conversation. A new tool
   can execute through this fixed entry immediately; loading does not add a
   native declaration. A resident preference is merged at the host's supported
   context boundary. If a previously native contract becomes stale, load the
   current contract and use `capability_invoke` until its declaration is updated.
7. For a published update, stage the new version and install the new stage; the
   previous release stays usable until the new one activates. Use `discard_stage`
   for an unwanted stage, `rollback` for a bad update, and `uninstall` only when the
   user wants the artifact removed.

Runtime dependencies are a deployment decision, not something the plugin installs
itself: declare them in `pyproject.toml` and let the host resolve them from its
configured wheelhouse or package index. If staging reports a missing dependency
source, report that to the user instead of vendoring or downloading packages.

Never edit instance configuration to simulate installation, and never write into
`alias:akane-sdk`.
