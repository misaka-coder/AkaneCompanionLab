# Plugin Managed Artifacts M65-D2

Status: generic host port implemented; finance chart/report migration not yet performed.

Model-facing interpretation of artifact results is owned by the later M65-D3
experience projection; see `plugin_result_experience_m65_d3.md`.

## Outcome

An allowlisted trusted plugin can now return one bounded, path-free artifact
draft from a capability invocation. Akane—not the plugin—chooses the output
path, writes the file, registers it in the current user's GeneratedFileStore,
and projects only a safe generated id/handle back into Engine events.

```text
plugin capability
  -> ManagedArtifactPayload(public content + bytes draft)
  -> PluginHost contract and result validation
  -> host-owned GeneratedFileManagedArtifactSink
  -> atomic temp write + rename + GeneratedFileStore registration
  -> path-free generated_file_ready event
  -> client delivery edge resolves handle for the current user/session
```

This is a generic plugin port. It contains no finance id, provider, chart,
report, QQ command, subscription, or private repository branch.

## Contract

An artifact-producing descriptor must declare exactly one output with:

- `delivery="generated_file"`;
- `kind="file"` and `required=true`;
- an explicit positive `max_bytes` no larger than the host-wide 16 MiB limit;
- declared effects `("network", "filesystem")` under the current trusted-read policy.

Its plugin manifest must add `artifact.write` to the existing
`capability.prompt.invoke` and `network.read` permissions. The capability may
return `ManagedArtifactPayload` containing ordinary public result content plus
one `ManagedArtifactDraft`. The draft contains bytes, title, output format,
MIME type, summary, and delivery intent; it never contains a path.

M65-D2 initially accepts PNG, Markdown, PDF, and XLSX with exact MIME matching.
This format allowlist is a versioned host policy, not a claim that the plugin
architecture is limited to finance files. New formats can be added at the
single materialization boundary after their storage and delivery behavior is
tested.

## Safety and Failure Semantics

- public result content is sanitized before any file is written;
- ordinary plugin results cannot forge the reserved `managed_artifacts` key;
- artifact bytes must fit both the descriptor limit and the host-wide limit;
- title, summary, format, MIME type, context, and returned reference are validated;
- a temporary file is renamed into host-managed storage before registration;
- registration failure removes the unregistered file best-effort;
- the result exposed to diagnostics, prompts, frames, and logs has no
  `absolute_path`, `storage_relpath`, plugin module path, or distribution path;
- missing sink, invalid contract, unsafe content, write failure, timeout, or
  missing delivery target returns a structured reason and never reports fake success.

The sink can only be bound while PluginHost is in `created`. Runtime rebinding,
hot mutation, and plugin-owned storage paths remain outside the restart-only
lifecycle.

## Delivery Behavior

`PluginCapabilityToolHandler` emits the same generic
`generated_file_ready` event used by normal generated files, with
`delivery_scope="plugin_managed_artifact"`. The event contains only safe
metadata.

At the QQ transport edge, Akane resolves the generated id against the current
`profile_user_id` and `session_id`, passes the local path only to the gateway,
and leaves the public frame unchanged. Resolution failure becomes
`managed_artifact_unavailable` and triggers the existing file-delivery failure
notice. The user therefore either receives the actual file or sees an honest
failure; a field existing in a frame is not treated as successful delivery.

Other clients can adopt the same handle-resolution rule without giving a
plugin access to their concrete transport implementation.

## Deliberate V1 Limits

- one artifact per invocation;
- in-process trusted plugins only;
- no plugin-supplied absolute or relative destination;
- no hot install, hot enable/disable, or capability-map mutation;
- no direct plugin access to QQ, desktop, web, or GeneratedFileStore internals.

These limits keep the stable boundary small. They do not require separate
Akane and finance projects: Akane remains the only host, while private finance
capabilities can opt into this port. Multiple artifacts, background jobs, or
new delivery channels should be added as new generic contracts only when a
real capability needs them.

## Migration State

No finance chart/report implementation was moved in this slice. Frozen public
finance chart/report source remains without a runtime entry point under the
M65-D1 migration-window rules. The next slice may:

1. implement chart/report capabilities in the private plugin using this API;
2. return the M65-D3 structured result experience instead of plugin-authored prompt text;
3. verify real wheel activation, storage, model feedback, Engine event projection, and QQ delivery;
4. delete the corresponding public implementations and inactive tests in the
   same cutover window.

## Validation

```powershell
python -m unittest tests.test_plugin_managed_artifacts tests.test_plugin_host tests.test_plugin_engine_bridge -v
python -m unittest tests.test_backend_route_modules tests.test_qq_gateway tests.test_generated_files
python -m py_compile companion_v01/plugin_api.py companion_v01/plugin_managed_artifacts.py companion_v01/plugin_contribution_policy.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py companion_v01/tool_runtime.py companion_v01/routes/qq.py companion_v01/app.py
git diff --check
```
