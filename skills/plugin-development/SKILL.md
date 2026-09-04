---
name: plugin-development
description: Use when creating, testing, updating, installing, or debugging an Akane plugin. Covers the current plugin SDK, permissions, isolated staging, publication, activation, and real runtime verification. Skip ordinary application code and MCP or Skill-only work.
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

Build the plugin as an ordinary tested Python project, then use Akane's managed
artifact lifecycle. This Skill adds no tools or permissions.

## Start from the current SDK

- Create a dedicated project with `manage_project_workspace(create)`, or open the exact
  existing source directory. Keep all source, tests, and build metadata in that project.
- Read the SDK index with `project_inspect(action="read", cwd="alias:akane-sdk",
  path="README.md")`, then inspect only the example closest to the requested behavior. The
  current-release examples are the public contract; do not search physical release paths or
  rely on remembered method names.
- `examples/plugins/akane_timer` is the reference for persisted delayed work that later
  wakes the ordinary Agent path. `akane_gentle_checkin` adds inbound activity observation
  and a contributed Skill when those extra surfaces are actually needed.
- Use the `akane.plugins.v1` Python entry point. Declare only permissions exercised by the
  implementation. A capability, Skill, prompt block, background service, fixed notification,
  internal analysis, QQ command, event observer, hook, or managed artifact is optional; do not
  add surfaces the requested behavior does not need.
- Current registrar names are `add_capability_adapter`, `add_skill`, `add_prompt_block`,
  `get_storage_dir`, `add_background_service`, `get_notification_port`,
  `get_agent_event_port`,
  `add_qq_command`, `add_event_handler`, and `add_hook_handler`.
  Read the nearest example for the exact argument and result types you actually use.

- Use `get_agent_event_port().submit(...)` for a background event that should make the
  active character respond. Store `ctx.conversation_ref` when configuring delayed work;
  it is an opaque host-issued value and must not be parsed or reconstructed. The request carries that reference,
  structured event facts, a user-facing event message, and `delivery="timeline"` or
  `delivery="current_turn"`. The host then runs the ordinary model and client pipeline;
  the plugin must not send the returned result through `NotificationPort`.

## Keep the boundary honest

- Plugins use `PluginRegistrar` contracts and scoped host ports. They do not import Engine,
  QQ gateway, MemCore internals, host credentials, or private runtime paths.
- Event handlers choose `internal`, `current_turn`, or `timeline` and separately decide
  whether to request a normal Agent turn. Registration alone does not wake the model. A
  background event that needs a character response must enter the host's ordinary Agent path;
  it must not turn a model result into a plain notification itself.
- Background work registers with `add_background_service(service_id, service)`, observes the
  shutdown controller, and uses stable idempotency keys for external delivery. The notification
  port is only for fixed text that does not need a model turn; it is not the character-response path.
- Return structured capability results and truthful status/reason values. Do not turn a
  failed probe, queued notification, or pending reload into a success claim.

## Test, stage, and activate

1. Write ordinary `unittest` tests against the real SDK imports used by the plugin. Do not
   replace `capcore`, adapters, or `companion_v01.plugin_api` with `sys.modules` stubs.
2. Call `manage_extension(action="test_source", path=<absolute working_directory>)`. The host
   runs `unittest` in a child process against the current release SDK without installing or
   activating the plugin. Fix failures until this returns `passed`.
3. Call `manage_extension(action="stage_source", path=<absolute working_directory>)`.
   The host builds a wheel and probes it in isolation; staging does not activate it.
4. Review the returned plugin id, contributions, and permissions. Fix and stage again if
   they do not match the intended design.
5. Call `manage_extension(action="install", stage_id=..., approved_permissions=[...])`,
   copying the exact permissions array returned by that stage. The host publishes and
   activates the immutable stage as one operation.
6. Call `list`, exercise the real capability, command, event, or background behavior, and
   verify the plugin is active. Source tests alone do not prove host integration.

Use `discard_stage` for an unwanted stage, `rollback` for a bad installed update, and
`uninstall` only when the user wants the installed artifact removed. Never edit instance
configuration to simulate installation, and never write into `alias:akane-sdk`.
