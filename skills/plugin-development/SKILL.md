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
  examples and `companion_v01.plugin_api` in the current release are the authority; do not
  rely on remembered method names or old host source trees.
- Use the `akane.plugins.v1` Python entry point. Declare only permissions exercised by the
  implementation. A capability, Skill, prompt block, background service, notification,
  reasoning turn, QQ command, event observer, hook, or managed artifact is optional; do not
  add surfaces the requested behavior does not need.
- Current registrar names are `add_capability_adapter`, `add_skill`, `add_prompt_block`,
  `get_storage_dir`, `add_background_service`, `get_notification_port`,
  `get_reasoning_port`, `add_qq_command`, `add_event_handler`, and `add_hook_handler`.
  Read the nearest example for the exact argument and result types you actually use.

## Keep the boundary honest

- Plugins use `PluginRegistrar` contracts and scoped host ports. They do not import Engine,
  QQ gateway, MemCore internals, host credentials, or private runtime paths.
- Event handlers choose `internal`, `current_turn`, or `timeline` and separately decide
  whether to request a normal Agent turn. Registration alone does not wake the model.
- Background work registers with `add_background_service(service_id, service)`, observes the
  shutdown controller, and uses stable idempotency keys for external delivery.
- Return structured capability results and truthful status/reason values. Do not turn a
  failed probe, queued notification, or pending reload into a success claim.

## Test, stage, and activate

1. Run the project's real unit or integration tests in the current working directory.
2. Call `manage_extension(action="stage_source", path=<absolute working_directory>)`.
   The host builds a wheel and probes it in isolation; staging does not activate it.
3. Review the returned plugin id, contributions, and permissions. Fix and stage again if
   they do not match the intended design.
4. Call `manage_extension(action="publish", stage_id=..., approved_permissions=[...])`,
   copying the exact permissions array returned by that stage.
5. For a new plugin, call `enable` with its plugin id. For an enabled update whose publish
   result says reload is required, call `restart`.
6. Call `list`, exercise the real capability, command, event, or background behavior, and
   verify the plugin is active. Source tests alone do not prove host integration.

Use `discard_stage` for an unwanted stage, `rollback` for a bad published update, and
`uninstall` only when the user wants the installed artifact removed. Never edit instance
configuration to simulate installation, and never write into `alias:akane-sdk`.
