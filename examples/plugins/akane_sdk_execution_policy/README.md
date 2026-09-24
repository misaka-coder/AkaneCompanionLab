# Execution policy through the public SDK

This independent project requires SDK/host `akane-plugin >=0.8.8,<0.9`. It contributes
`execution_policy.example.effect_guard` version 1, with `before`, `transform`, and `present` methods and no
model tool. Its implementation imports no host module.

Install via normal `stage_source` / `install`, approving the returned
`policy.provide` and `service.provide` permissions. Installation alone changes no
business behavior. Select the policy by setting `AKANE_EXECUTION_POLICIES` to the
contents of a preset and restarting the Bot:

- [personal.json](presets/personal.json): continue to existing capability admission
  and user approval. It does not auto-approve tools.
- [shared.json](presets/shared.json): allow no declared effects and require known
  risk metadata. A legacy route without metadata is rejected rather than assumed
  harmless. Existing authorization still applies to permitted calls.

Options support `blocked_tools`, `blocked_effects`, optional `allow_effects`, and
`require_metadata`. For example, selecting
`[{"policy_id":"example.effect_guard","options":{"blocked_tools":["example.batch.measure"]}}]`
allows the batch entry but rejects its nested measure calls. The batch reports
its actual partial result; the target receipt says it was not started.

The optional `numeric_factors` maps exact tool ids to numeric factors. For example,
`[{"policy_id":"example.effect_guard","options":{"numeric_factors":{"example.batch.measure":2}}}]`
turns each successful byte-count value from 6 into 12 before the batch consumes it.
Unlisted tools keep their result unchanged. This is a deployment-owned unit-conversion
example; the deployer must select tools for which that conversion makes sense.
With factor 0.1, a count of 6 would become 0.6 and fail the measure tool's integer
schema. The batch records `result_processing_failed` and the original value 6 in
the execution receipt; the action is not automatically retried. `numeric_factors`
does not change status, approval, file bytes or delivery requests. Existing personal
and shared presets omit it and retain the original values.

Use `await ctx.tools.policies()` from an authorized plugin to inspect order,
availability and diagnostics. A missing selected policy blocks execution. Removing
a required provider through plugin management fails atomically and retains the
active generation; change deployment selection first when retiring the policy.

The SDK also supports `@policy.present`: it receives the producer-rendered model text and can return `{"action":"replace","text":...}` without changing the canonical program value. A changed long model result is saved as text for the same `inspect_generated_file` continuation; no action or artifact delivery is repeated.

The SDK also supports `@policy.observe`: it receives a copy of the finalized
outcome and returns None. Observer failure affects diagnostics, not the business
result. Integration tests exercise real policy installation, nested worker calls,
native requests, a controlled WebSocket executor, background jobs, and an upgrade
while before is paused: the old call retains its old transformer and observer, the new call uses
the new decision. This is not a control-center editor or a real desktop click test.


The lifecycle wrapper is configured by deployment options. For a read-only tool, set `max_attempts` and list the failure statuses that may be retried in `retry_statuses`. The host still rejects retries for tools that declare effects, and it keeps the real final receipt when a wrapper callback fails.
