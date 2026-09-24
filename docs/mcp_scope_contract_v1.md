# MCP scope contract v1

## Purpose

Akane treats an MCP server installation as a Host capability, not as chat
history. Installing or configuring a server in one QQ group therefore makes
the same server catalog available to private chat and other profiles on that
Host. This matches bundled and managed Skill discovery without exposing every
MCP tool schema in every request.

## Three scopes

1. **Host registry** — server id, transport, command or URL, placeholder-based
   environment/header bindings, catalog description, discovered tools and the
   default activation configuration. The authoritative file is
   `users_data/capabilities/mcp_servers.yaml`.
2. **Profile overlay** — per-profile enable/disable, activation mode, pinned
   tool selection, low-risk allowlist and the existing profile approval policy.
   These remain in
   `users_data/<profile_user_id>/capabilities/capabilities.yaml`. A profile may
   disable a shared server without disabling it for another profile.
3. **Published model context** — unified per-tool exposure separates eligible
   execution targets from native declarations. `capability_load` returns the
   current exact contract at the message tail; `capability_invoke` uses its
   scoped reference through the existing target execution chain. `load_mcp`
   is a historical server-name adapter to the same contract loader and does
   not inject schemas. `invoke_mcp` translates server/tool coordinates and a
   contract reference to the same router; historical names grant no authority.
   On an authoritative MemCore path, native preferences merge only at successful
   compaction; new sessions establish a new baseline. Other memory backends use
   immediate presentation with no compaction-stability claim.

Secrets are not copied from one QQ profile to another. MCP configuration only
accepts environment/header placeholders; their actual values remain Host
runtime configuration. Capability approval and approval requests remain
profile-scoped.

## Legacy migration

At Host startup Akane migrates legacy profile-local `mcpServers` entries when a
server id has one unambiguous endpoint definition. Equal definitions collapse
into the Host registry; differing profile preferences become profile overlays.
If the same server id names different commands or URLs, Akane leaves those
entries profile-local and reports a migration conflict instead of choosing one
silently. This legacy fallback is a migration window, not a second permanent
installation authority.

Removing a Host MCP registration removes its stale profile overlays but never
uninstalls an external npm/Python package or deletes its source directory.

## Exposure migration and contract revisions

Explicit `toolExposure.toolModes` take priority. Otherwise `activationMode=pinned`
plus `pinnedTools` maps just those tools to resident; other eligible MCP tools
remain on-demand. This preserves per-tool preferences independently of enablement,
model visibility and approval. An explicit `modelVisible=false` is restrictive;
the historical `promptExposed` injection flag does not become a new permission.

The catalog uses actual `tools/list` metadata and refreshes at contract loading
and execution checks. It includes observable tool metadata and host connection
lifecycle revisions in contract identity, so a declared semantic change can
invalidate an old binding even when JSON Schema is unchanged. Manager-side
revision checks prevent an already queued call from reviving a stopped runtime.
Unannounced behavior changes at an external service cannot be inferred reliably.

See [migration and rollback](tool_exposure_memcore_implementation_20260910.md).
