# Plugin Managed Artifacts M65-D2

Status: generic host port implemented; finance chart/report migration completed by the private artifact.

Current contract update (2026-09-06): ordered file/multi-artifact handoff replaces
the original single in-memory format-limited path. The isolated generation
outbox transports files without JSON bytes. See `optional_media_plugin_migration_v1.md`.

Model-facing interpretation of artifact results is owned by the later M65-D3
experience projection; see `plugin_result_experience_m65_d3.md`.

## Outcome

An allowlisted trusted plugin can return ordered artifact drafts from a
capability invocation. Akane—not the plugin—chooses the destination
path, writes the file, registers it in the current user's GeneratedFileStore,
and projects only a safe generated id/handle back into Engine events.

```text
plugin capability
  -> ManagedArtifactPayload(public content + ordered bytes/file drafts)
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
- an explicit positive `max_bytes` for the total size of all invocation artifacts;
- declared effects `("network", "filesystem")` under the current trusted-read policy.

Its plugin manifest must add `artifact.write` to the existing
`capability.prompt.invoke` and `network.read` permissions. The capability may
return `ManagedArtifactPayload` containing ordinary public result content plus
an `artifacts` tuple of `ManagedArtifactDraft` values. Each draft contains exactly
one of small `data` bytes or a plugin-local source `path`, plus title, output
format, MIME type, summary, and delivery intent. The path stays private to the
worker/outbox/sink chain and never appears in public results. Keep the source
available until invocation finalization. In-memory bytes remain capped at
16 MiB per draft; files stream in 1 MiB chunks under the declared total budget.

The API v1 `artifact=` constructor is a thin normalization adapter to a one-item
tuple for existing installed wheels; there is no singular runtime field or
second materialization path. Repository producers use `artifacts=`.

Safe alphanumeric extensions and MIME syntax are validated, with MIME matching
for recognized formats (including audio/video). There is no four-format business
allowlist. Client delivery support remains independent of storage support.

## Safety and Failure Semantics

- public result content is sanitized before any file is written;
- ordinary plugin results cannot forge the reserved `managed_artifacts` key;
- all artifact sizes must fit the descriptor's combined budget;
- title, summary, format, MIME type, context, and returned reference are validated;
- a temporary file is renamed into host-managed storage before registration;
- registration failure removes the unregistered file best-effort;
- cancelled copies stop and drain before temporary files are removed; no late
  background registration occurs after cancellation;
- a later artifact failure returns `partial` with earlier real references, not
  batch success; partial errors do not automatically deliver those files;
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

`ManagedArtifactDraft.delivery_mode` is `file` by default; audio drafts can request
`voice` or `both`. Invalid modes and non-audio voice requests fail validation.
The mode is integrity-bound through the isolated outbox and host reference and
projected into the existing event, not a new channel sender. Old API-v1 drafts,
references and outboxes without a mode retain file delivery.

Long Job results persist each artifact's requested mode and `send_to_user` flag.
Their completion event still says `available_not_delivered`; it supplies the
request to the ordinary Agent follow-up, which uses the channel's existing
send-audio/send-file tools. It does not treat an intent as an acknowledgement or
add a second automatic-delivery queue. Direct event delivery uses the existing
QQ voice/file/both branches; failed voice in `both` still attempts the file.

At the QQ transport edge, Akane resolves the generated id against the current
`profile_user_id` and `session_id`, passes the local path only to the gateway,
and leaves the public frame unchanged. Resolution failure becomes
`managed_artifact_unavailable` and triggers the existing file-delivery failure
notice. The user therefore either receives the actual file or sees an honest
failure; a field existing in a frame is not treated as successful delivery.

Other clients can adopt the same handle-resolution rule without giving a
plugin access to their concrete transport implementation.

## Remaining Boundaries

- no plugin-supplied absolute or relative destination;
- no direct plugin access to QQ, desktop, web, or GeneratedFileStore internals.

These limits keep the stable boundary small. They do not require separate
Akane and finance projects: Akane remains the only host, while private finance
capabilities can opt into this port. Managed generations now support market
installation and lifecycle updates; transport continues to use the same port.

## Finance Migration State

The private `akane.finance` artifact now owns two real managed-artifact
capabilities:

- `akane.finance.render_market_chart.v1` produces a deterministic PNG from
  freshly fetched bounded public OHLCV evidence;
- `akane.finance.compose_finance_report.v1` produces Markdown, PDF, or XLSX
  from complete freshly fetched evidence and embeds a same-evidence chart in
  PDF/XLSX.

Both return path-free in-memory drafts plus M65-D3 structured result
experience. Source-blind installed-wheel acceptance covers real activation,
provider parsing, rendering, host storage, model feedback, safe Engine events,
QQ handle hydration, image/file routing, and explicit delivery truth. The
public frozen chart/report providers, handlers, exports, and tests were deleted
in the same cutover, so Akane has no second finance artifact implementation.

The remaining public finance migration window covers proactive news ingestion,
subscriptions, event jobs, and EmQuant only. On-demand news moved in M65-D5
without using this artifact port. The remaining surfaces must not reuse the
artifact port as a substitute for their missing storage/job/config contracts.

## Validation

```powershell
python -m unittest tests.test_plugin_managed_artifacts tests.test_plugin_host tests.test_plugin_engine_bridge -v
python -m unittest tests.test_backend_route_modules tests.test_qq_gateway tests.test_generated_files
python -m py_compile companion_v01/plugin_api.py companion_v01/plugin_managed_artifacts.py companion_v01/plugin_contribution_policy.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py companion_v01/tool_runtime.py companion_v01/routes/qq.py companion_v01/app.py
git diff --check
```
