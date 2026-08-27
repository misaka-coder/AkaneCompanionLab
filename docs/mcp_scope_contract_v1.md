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
3. **Current task turn** — `load_mcp` expands selected native tool schemas only
   for the active task turn. It does not install a package, change permission,
   or make schemas permanently occupy the prompt.

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
