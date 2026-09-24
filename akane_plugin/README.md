# Akane Plugin SDK 0.14.2

`akane_plugin` is the public Python import for plugin contracts, function tools,
and program calls. It requires Python 3.11+ and CapCore 0.1.3. The SDK wheel
contains only this package; it does not import or install the Akane host.

This source release supplies wheels locally; it has not been published to a
public package index. Build the SDK with `python -m build`, then install its
wheel and the matching CapCore wheel in your virtual environment:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install path/to/capcore-0.1.3-py3-none-any.whl path/to/akane_plugin-0.14.2-py3-none-any.whl
```

The dependency resolver installs CapCore's normal dependencies. No host source
directory, `PYTHONPATH` adjustment, fake module or import alias is required.

## One function tool

Use `@plugin.tool(owner_only=True)` to restrict a tool to the host's owner.
The host checks eligibility before approval and at dispatch, including isolated
workers and composed calls. `ops` and capability approval settings still apply;
approval never changes the requesting actor. Omitted `owner_only` preserves
ordinary tools. Non-boolean declarations are rejected. This requires SDK 0.14.2
and a host that implements this declaration; upgrading only the SDK does not
upgrade an older host's admission layer.

Do not put a second owner account setting or owner comparison inside a plugin.
Keep resource-specific controls (for example a game control lease) in the plugin.

SDK 0.14.1 adds `ctx.invocation.authorization_profile_user_id` for tools that
must check the host-resolved caller identity. In a shared QQ conversation this
is the verified sender's profile, while `profile_user_id` remains the shared
storage scope. It is empty for unbound/global work and older hosts; identity-sensitive
plugins must reject missing authority, never infer it from tool arguments or
decode the opaque conversation reference. Normal host capability approval still
applies independently.

```python
from akane_plugin import Plugin

plugin = Plugin("example.math")

@plugin.tool
def add(a: int, b: int = 1) -> int:
    """Add two integers."""
    return a + b

def create_plugin():
    return plugin
```

Package this as an ordinary Python project. Its `pyproject.toml` declares:

```toml
[project]
name = "example-math"
version = "0.1.0"
dependencies = ["akane-plugin>=0.8,<0.9"]

[project.entry-points."akane.plugins.v1"]
"example.math" = "example_math:create_plugin"
```

The function above is discovered as `example.math.add`. `Plugin` supplies the
ordinary adapter and adds prompt invocation permission when a valid `@plugin.tool`
is declared. Event-only plugins do not request this permission. A rejected tool
declaration does not change the manifest. Declare additional host
permissions explicitly in `Plugin(..., permissions=(... ,))`; installation
and target invocation still use the host's normal approval rules.

Supported annotations are JSON primitives, `list[T]`, `dict[str, T]`, `Literal`,
unions and `Any`. Untyped parameters and unsupported types produce declaration
errors. Defaults are described in the input schema and applied by Python when
arguments are omitted. They do not cause a separate schema validation path.
Input and output schemas compile through CapCore, which also owns validation
at execution. Normal functions return JSON; returning `CapabilityResult` or
`ManagedArtifactPayload` preserves the advanced result contracts.

## Export files as managed artifacts

An export returns a host-managed artifact, not a JSON path. Relative paths inside
a plugin use its process directory, which is not the caller's selected project.
The host copies the bytes and returns a conversation-bound `gen_*` handle:

```python
from akane_plugin import Plugin, CapabilityIOSlot, ManagedArtifactDraft, ManagedArtifactPayload

plugin = Plugin("example.notes", permissions=("artifact.write",))

@plugin.tool(
    effects=("filesystem",),
    outputs=(CapabilityIOSlot("document", "file", required=True,
                              max_bytes=2_000_000, delivery="generated_file"),),
)
def export_markdown(text: str, send_to_user: bool = False):
    return ManagedArtifactPayload(
        content={"bytes": len(text.encode("utf-8"))},
        artifact=ManagedArtifactDraft(data=text.encode("utf-8"), title="Notes",
            output_format="md", mime_type="text/markdown", send_to_user=send_to_user),
    )
```

For an existing file, use `ManagedArtifactDraft.from_file(existing_path,
send_to_user=send_to_user)`. It resolves the producer's path immediately and infers
the final extension, title and MIME type; pass `mime_type` or `title` to override.
Keep the file alive until the invocation completes. Do not return it from inside
a temporary-directory context that deletes the file before host materialization.
The helper defaults to registration only; `send_to_user=True` requests delivery.
It preserves the existing permission and output-slot byte limits.

Documents, images, audio, video, archives and unknown extensions use the same
artifact handoff. Unknown MIME types default to `application/octet-stream`.
Extensions must currently be 1–16 ASCII letters/digits; extensionless files need
an explicit format through the draft constructor. File delivery does not promise
preview, native QQ playback or conversion. Use `delivery_mode="voice"`/`"both"`
only for supported audio when requested. A registered handle or queued delivery
does not prove the recipient received it.

For a file already in an authorized project, the caller can use
`send_file(path="exports/report.md", cwd="actual project directory")` to register
and queue the original bytes in one operation. Absolute paths omit `cwd`. Use an
existing exact handle instead when one has already been returned. No copy into
the currently selected project is required.

## Versioned program services

SDK 0.7 adds pure services. A provider owns its business implementation:

```python
import statistics
from akane_plugin import Plugin

plugin = Plugin("example.statistics")
api = plugin.service("statistics", version=1)

@api.method
def mean(values: list[float]) -> float:
    return statistics.fmean(values)
```

The declaration adds `service.provide`, with no implicit model tool permission.
Methods compile their annotations or full JSON schemas through the same
CapCore contract as tools. The host's contribution snapshot lists services
separately with their contract versions and method schemas.

Consumers call the contract from `ToolContext`, `ServiceContext` or `EventContext`:

```python
from akane_plugin import Plugin, ToolContext, ServiceDependency

plugin = Plugin("example.report", permissions=("capability.invoke",),
                requires_services=(ServiceDependency("statistics", version=1),))

@plugin.tool
async def average(values: list[float], ctx: ToolContext) -> float:
    return await ctx.services.call("statistics", "mean", {"values": values}, version=1)
```

`call` returns the canonical JSON value or raises `ToolCallError`;
`call_result` preserves the full `CapabilityResult`. Both use the existing
invocation, policy, cancellation, budget and schema validation chain. Service
calls do not make an implicit model request. Cross-worker calls carry JSON,
without exposing another plugin's Python objects or host internals.

SDK 0.8.1 adds `await ctx.services.list()` and `await ctx.services.list("statistics")`.
Each returned item has `service_id`, `version`, `status`, `reason`,
`configured_provider`, `selected_provider`, and `providers`. Each provider has
`plugin_id` and `methods` with `name`, `input_schema` and `output_schema`.
These are complete copied contracts from the active service declarations.
No configuration values, credentials or worker paths are included.
An unknown service returns an empty list; a configured but absent provider returns
`status="unavailable"`, `reason="service_unavailable"` and no selected provider.
Multiple unbound providers return `service_provider_ambiguous`. Waiting consumers
and providers are not advertised as active implementations.

Discovery uses the same live `capability.invoke` scope and RPC budget as calls;
errors raise `ToolCallError`. It performs no health check or business method and
does not grant permission to execute a discovered method. Within a service call
chain it inspects that chain's frozen providers; a top-level query reads a fresh
snapshot even inside an older model turn. Discovery alone does not reserve a
provider for a later top-level call. Revocation still takes priority over a frozen
snapshot. Use `requires_services` for mandatory activation dependencies.

## Host tool exposure and exact loading

The control center lets users choose resident or on-demand exposure per tool,
including different modes within one plugin. A resident tool is called by its
native name. An on-demand tool appears in a short directory with its exact id
and purpose: `capability_load(capability_ids=[id])` returns its complete contract
and `contract_ref` as a real tool result; `capability_invoke(capability_id=id,
contract_ref=ref, arguments={...})` executes through the same permission,
validation, approval, job and result chain. Loading never adds native schemas.
A previously loaded, still-valid reference can be reused without another load.
`capability_list` offers deterministic paging; optional `capability_search` uses
case-insensitive substring matching, not semantic search. No search receipt is
needed to load an exact id. Scope or query mismatch returns `cursor_invalid`;
a changed catalog returns `cursor_stale` and requires a fresh page.

Authors keep using the existing public tool descriptor and SDK decorator.
The host uses `short_hint` when the adapter provides it, then the description,
then the display name/id. Long fallback descriptions are preserved rather than
silently truncated. Give tools a concise, accurate description. Program-only
services and tools without model visibility remain outside the model directory.
Stable prompt blocks, Skills and model tools are independent contributions;
prompt-only and service-only plugins do not need dummy tools.

On an available authoritative MemCore projection (`MEMORY_BACKEND=memcore`),
resident declarations and plugin system blocks are frozen per real compaction
generation. Preferences save immediately; changes merge after successful
compaction, or when a genuinely new session establishes its baseline. Reopening
an old session or restarting the host does not clear pending changes. New tools
can be loaded and invoked immediately. Uninstall, disable and permission
revocation take effect immediately; revoked higher-priority plugin system rules
are removed immediately as an explicit prefix-stability exception.

Provider generation changes invalidate old bindings even when the input schema
is identical. A stale native call returns a paired `executed=false` error:
load the current contract, invoke with its reference, then resume direct native
calls only after a new baseline publishes that contract. Loading cannot rebind
an old native declaration. The host checks again at actual execution; contract
references identify versions and never grant permission.

`legacy` and `dual` keep their existing memory behavior and use the same execution
chain with immediate presentation, without a simulated compaction generation.
The old `ENABLE_PROGRESSIVE_CAPABILITY_DISCOVERY` flag is only a default-migration
input when no saved preference exists: true maps host/plugin defaults to
on-demand, false to resident. Explicit per-tool preferences win. No old G1
schema-injection engine remains. See the [implementation and migration record](../docs/tool_exposure_memcore_implementation_20260910.md).

SDK 0.8 adds required service declarations. The generation builder first prepares
all declarations, checks provider selection and dependency cycles, then activates
the eligible workers in dependency order. Missing contracts, ambiguous providers
and cycles keep the consumer in `waiting_dependency`, with a concrete reason and
dependency diagnostics in the management API. Its tools, events and background
jobs are unavailable until a subsequent install, enable or reload satisfies the
dependency. Disabling a provider withdraws its dependent consumers. A pending
artifact retains its installation and last-good release while waiting.

Factories and `register` must remain declarative: preparation still imports and
registers trusted plugin code. The host defers its adapter health checks and
supervised background jobs until activation. Source/wheel staging performs this
preparation without running those health checks or jobs.

Dependencies name an exact positive integer contract version. Deployment may
select a provider in the existing per-instance `plugin-selections.json` overlay:

```json
"service_bindings": [
  {"service_id": "statistics", "version": 1, "plugin_id": "example.statistics"}
]
```

Preserve the file's other fields and reload extensions after editing. Enablement
changes preserve these bindings. Without a binding, a required contract must have
one enabled declared provider. Explicit bindings never fall back to another
provider. Optional dynamic calls may omit the dependency declaration and receive
`service_unavailable` or `service_provider_ambiguous` at call time.

An implementation declared by the same plugin can satisfy a required contract;
it is prepared together with the consumer and adds no inter-plugin activation
edge. Cycles between plugins still block activation, and recursive method calls
still fail through normal call-chain validation. This permits one package to
supply a default service and a model tool that delegates to the selected provider.

### Effects, permissions and requirements are different contracts

The SDK deliberately keeps three declarations separate:

| Declaration | Owns | Does not own |
| --- | --- | --- |
| CapCore `effects` on a tool descriptor | The semantic impact of one capability call, such as `network`, `filesystem`, `send_message`, or `request_agent_turn` | Installation approval, a credential, or proof that the action ran |
| `Plugin(..., permissions=...)` | Host capability grants requested by the package and reviewed at staging/install time, such as `network.read`, `artifact.write`, or `agent.turn.request` | The meaning of a tool's business effect or a service provider selection |
| `requires_services=(ServiceDependency(...), ...)` | Exact versioned service contracts that must be available before this plugin generation activates | A host permission, a model tool, or an implicit runtime retry |

Effects are compiled into the public CapCore descriptor and are checked against
the manifest permissions by the active host contribution policy. For example,
`network` requires `network.read`, plugin state mutation requires `storage.write`,
and generated-file outputs require `artifact.write`. A declaration never widens
permissions by inference. Conversely, a permission does not claim that a plugin
will use the corresponding effect.

`requires_services` is the only public activation prerequisite for another
plugin's versioned service. The host resolves providers, configured bindings,
cycles and waiting state before publishing the generation. There is intentionally
no second generic `requires` field: adding one would create two approval and
activation authorities for the same capability. See
[`docs/plugin_system_effects_requires_v1.md`](../docs/plugin_system_effects_requires_v1.md)
for the contract matrix and failure states.

The existing image plugin now uses this arrangement for `image_generation` v1,
method `generate`. Its advanced public adapter derives tool and service contracts
from one CapCore descriptor, tags the service descriptor's `raw["service"]` with
`{service_id, version, method}`, and sets `prompt_exposed=False` on that method.
The tool calls `Services(registrar.get_capability_port()).call_result(...)`.
No host imports or second image execution path are needed. Its source and full
input/output contract are in `plugins/akane_image_generation`.

A top-level service call captures the current generation and provider bindings,
even inside an older model turn. Nested calls keep that snapshot until completion
or cancellation; the next top-level call refreshes. Explicit disable/revocation
still takes precedence. A dedicated configuration UI remains subsequent P9 work.

See `examples/plugins/akane_sdk_statistics` and
`examples/plugins/akane_sdk_statistics_report` for independently packaged
provider and consumer projects.

## Declare how a tool runs

Every tool declares an execution class. It decides whether the host can answer the
model immediately or must detach the work into the normal background job path.

```python
@plugin.tool
def lookup(city: str) -> dict:
    """Answer quickly; the model waits for this result."""

@plugin.tool(execution_class="long_task")
def transcribe(source_id: str) -> dict:
    """Return a durable job id; the model is not blocked."""
```

- `execution_class="sync"` (default) keeps the model waiting for the result. Use it
  when the work is fast enough to answer in the same turn.
- `execution_class="long_task"` runs through the host's existing background job
  store. The caller receives a real `job_id`; completion wakes the owning
  conversation when the resolved `followup` requires it. A plugin does not create
  its own scheduler, worker pool or database.
- Declaring `job.run` is only needed for a supervised background service
  contribution, not for `execution_class="long_task"`.

The execution class never changes the program contract: a program caller receives
the canonical value (or a structured error) either way. `execution_class` and
`followup` are independent — the class decides *where the work runs*, `followup`
decides *whether the result needs another model response*.

Tool calls and results stay paired for the model's protocol history in every path:
synchronous success, synchronous failure, a background result, a cancelled call and
a result with `followup="none"`. A `none` result is still stored as the call's
result; it only removes the implicit model follow-up. Authors do not construct or
repair that pairing, and no author-facing field can skip it.

Cancellation is always real: stopping a turn, disabling a plugin or revoking a
grant ends the call's authority. The plugin may keep running if it suppresses
cancellation, but its later tool/event/service calls are rejected and its result
is not replayed into a new task. The host reports the actual terminal state
instead of claiming an external side effect was undone.

## Choose whether a result needs another model response

SDK 0.6 adds `Result(value=..., followup="none")` and the declaration default
`@plugin.tool(followup="required")`. Both synchronous and background tool results
use the same decision. The default is `required`; a result can override it with
`none`, `required` or `auto`.

```python
from akane_plugin import Result

@plugin.tool
def changed(previous: str, current: str) -> Result:
    different = previous != current
    return Result(value={"changed": different},
                  followup="required" if different else "none")
```

`none` removes this result's implicit model followup. It preserves the value,
tool history, background result and normal artifact delivery. A program caller
always gets the value/error and never gets an implicit model turn. An explicitly
required model consumer, pending task report or unhandled error still needs
processing; a mixed batch continues for those results. `auto` permits an existing
direct completion only when the host knows the action is final and delivery is
managed; otherwise it conservatively requires the consumer. Followup never
cancels another result or an explicit `ctx.request_turn()`.

No `finish_turn` field is added to a plugin's input schema. Full schemas may own
a same-named business field. Advanced adapters can use `Result(value=data,
content=presentation, followup="none")`; `value` remains the canonical JSON.

Background results and separate delivery receipts are retained by the existing
host job store. Authenticated administrators can inspect them using
`GET /admin/plugins/jobs/{job_id}?profile_user_id=PROFILE&session_id=SESSION`.
The same owner-scoped surface accepts `POST /admin/plugins/jobs/{job_id}/control`
with `{"action":"pause|resume|stop"}`. A queued job can pause and resume with the
same `job_id`; a running task reports `paused` only when its execution driver has a
cooperative pause boundary. Otherwise the host returns `pause_unavailable` instead
of claiming that a running side effect stopped.
`queued` means the channel accepted a delivery; it does not prove display or
playback. Job execution can succeed while its delivery fails. No implicit retry
repeats the action. A stopped parent or disabled capability revokes accepted jobs' future work,
without erasing completed results. See the independent
`examples/plugins/akane_sdk_change_check` project and
`docs/plugin_followup_migration_v2.md` for migration details.

## Full JSON Schema

Use `input_schema` and `output_schema` for complex contracts. The input function
receives one argument object, so business keys such as `type`, `arguments` or
`ctx` retain their meaning. Internal references, compound constraints and long
descriptions remain in the canonical schema.

```python
@plugin.tool(
    input_schema={"type": "object", "properties": {"type": {"const": "invoice"}},
                  "required": ["type"], "additionalProperties": False},
    output_schema={"type": "string"},
)
def classify(arguments):
    return arguments["type"]
```

## Program composition

```python
from akane_plugin import Plugin, ToolContext

plugin = Plugin("example.report", permissions=("capability.invoke",))

@plugin.tool
async def total(a: int, b: int, ctx: ToolContext) -> int:
    """Calculate using the installed math plugin."""
    return await ctx.tools.call("example.math.add", {"a": a, "b": b})
```

`ToolContext` is injected by the host-facing adapter and is not an input field.
`tools.call` returns the complete canonical value, including `null`, `false`,
zero and large lists. It raises `ToolCallError` on failure; `error.result`
retains the structured status, reason and error/approval receipt. An unhandled
dependency error becomes the parent tool's failed result.

`tools.call_result` returns the complete `CapabilityResult` without raising for
a business failure. On success, `.value` is the canonical value and `.content`
contains the producer's public result metadata. File producers expose existing
handles in `content["managed_artifacts"]`. A plugin declaring `resource.read`
can open these via `ctx.resources.open(handle)` in its current invocation.

SDK/host 0.8.2 also supports returning that complete result unchanged, including
its experience and artifact metadata: `return await ctx.services.call_result(...)`
or `return await ctx.tools.call_result(...)`. The host verifies the result against
receipts from this invocation and checks the consumer's output schema. Existing
files are not copied or registered again. `created_by_tool` retains the producer;
the host assigns `forwarded_by_tool` for the receiving tool's normal presentation
and delivery path. Changing value, origin or delivery intent invalidates that
receipt, as does replay from another invocation. If you need a different business
value, return that value normally instead of modifying finalized result metadata.

Program calls reuse the existing tool selection, CapCore admission, executor
broker, cancellation and plugin lifecycle. They do not create a model consumer,
model preview material, detached job or automatic delivery. A producer's file
delivery metadata remains a request; it is not proof of sending. A parent that
wants to deliver a final file returns its managed artifact or forwards the complete
verified result through the normal host delivery path. Tools must expose canonical program results;
unsupported handlers are rejected before execution.

Calls use the current host-owned invocation or event scope. Cycles are rejected.
SDK/host 0.8.4 adds `await ctx.tools.budget()` (also available through
`ctx.services.budget()`) on the existing `capability.invoke` port. It returns
`policy_id`, `limits` (each with `value` and `source`), `dependency_depth`,
`used_dependency_calls`, `remaining_dependency_calls` and `remaining_dependency_depth`.
Queries consume no calls; concurrent snapshots describe that instant and do not
reserve future capacity. Nested calls across workers share one atomic counter.
Reserved attempts count even if their later admission or business execution fails;
rejected reservations do not count. A new top-level invocation starts a new budget.

Deployment defaults are no generic tool-input byte limit, depth 32 and 256 dependency
attempts per chain. `AKANE_EXECUTION_RESOURCE_POLICY` accepts a JSON object with
`max_input_bytes`, `max_dependency_depth` and `max_dependency_calls`, each a positive
integer or `null` for unlimited. For example, a deployment can use
`{"max_input_bytes":1048576,"max_dependency_depth":64,"max_dependency_calls":2000}`.
Omitted fields retain defaults. Invalid values stop runtime startup with
`execution_resource_policy_invalid`; changes take effect when the Bot restarts.
Tool-input size uses compact UTF-8 JSON of business arguments in both model and
program dispatch. It does not include host metadata or binary resource contents.

An exhausted limit returns `status=resource_exhausted`,
`reason=execution_resource_limit_exceeded`, with policy id, phase, target, budget,
actual, limit, source, retryable and recovery in `content`. `execution_status=not_started`
and `scope=target_call` refer only to that blocked target: preceding batch work can
already have completed. The same invocation id replays its recorded failure;
raising a limit does not silently rerun it. This configuration changes resource
allowances, not capability permissions or approvals. It does not configure HTTP,
private connection, event, result, time or process-memory limits.
See the independent [batch example](../examples/plugins/akane_sdk_batch/README.md).

Cancelling an invocation withdraws its tool, resource and connection grants
before notifying plugin code. Catching cancellation, clearing its task counter,
or spawning another task does not restore these grants. The host still drains
existing operations and keeps their input copies until their actual terminal
result; cancellation does not undo completed effects or turn an unconfirmed
remote outcome into a claimed success/cancellation.

## Deployer-selected execution policies

SDK/host 0.8.5 adds policy declarations on the existing versioned service runtime:

```python
plugin = Plugin("example.guard")
policy = plugin.policy("example.guard")

@policy.before
def decide(request):
    if request["tool_id"] in request["options"].get("blocked_tools", []):
        return {"decision": "deny", "reason": "deployment_disabled"}
    return {"decision": "allow"}

```

A policy contributes the service `execution_policy.<policy_id>`, version 1, with
`before`, `wrap` (SDK/host 0.8.8), `transform`, `present`, and/or `observe` methods. Both `policy.provide` and `service.provide` are
declared automatically. It needs no model tool. Installation uses normal permission
approval, workers and dependency resolution; installing a policy does not select it.
The host validates its schemas against the public contract in `akane_plugin.policies`.

Select an ordered list through the startup JSON setting `AKANE_EXECUTION_POLICIES`,
for example `[{"policy_id":"example.guard","options":{"blocked_tools":["exec_run"]}}]`.
The default is `[]`. Only `policy_id` and an optional JSON-object `options` are
accepted. Use existing instance `service_bindings` to select among providers of the
same policy contract. Selected policies and their declared service dependencies
activate before other business plugins. Missing, ambiguous or cyclic required
providers prevent candidate publication (`execution_policy_dependency_unavailable`);
a failed update retains the previous active generation. Change startup configuration
and restart the Bot to remove a required policy.
Options are non-secret deployment rules, not a credential store.

Requests contain tool_id, invocation_id, business arguments, executor, effects,
risk, client_mode and deployment options. Unknown legacy metadata stays empty.
Arguments/options are copies; mutating them cannot alter the actual call. `allow`
continues through existing target admission and approval; it grants no permission.
`deny` stops only this target before dispatch. A deny reason is an optional machine
identifier matching `[a-z][a-z0-9_.-]{0,127}`. The host returns `policy_rejected` /
`execution_policy_rejected`, or `policy_failed` / `execution_policy_failed` if a
selected policy is unavailable, revoked, throws, or returns an invalid result.
The result content identifies policy_id, phase, target, reason and not_started
with scope=target_call. Earlier batch work may already have completed. Idempotent
replay returns the recorded outcome without re-evaluating or re-executing it.

SDK/host 0.8.6 adds `@policy.transform` for successful canonical values from
plugin, Python and MCP adapter tools, including program and background calls:

```python
@policy.transform
def convert_units(request):
    if request["tool_id"] != "example.sensor.temperature":
        return {"action": "keep"}
    return {"action": "replace", "value": request["outcome"]["value"] * 1.8 + 32}
```

Transforms run in deployment order before model projection, preview storage and
artifact event generation. Each sees the previous transform's value in a copied
outcome. `keep` preserves the complete result; `replace` must include a JSON value,
including explicit null, false or zero. Every replacement passes the target's
same CapCore output schema before the next transform can see it. Original status,
reason and followup remain unchanged. Producer prose may describe the old value,
so replacement rebuilds public content as `{"value": new_value}`; existing trusted
`managed_artifacts` metadata is retained separately. Policy value fields cannot
create artifact handles, change file bytes or change delivery intent.

Transform failure returns `result_processing_failed` /
`execution_policy_result_processing_failed`, with `content.policy_failure`
identifying policy_id, phase=transform, target, invocation_id, reason,
execution_status and scope=result_processing. `retryable` is false;
`content.execution_receipt` retains the original result, including any generated
file references. The action has returned; failure processing its result does not
mean it was unexecuted. The host emits no automatic artifact delivery on this
failure, and the same invocation id replays the recorded failure without rerunning
the action. Observers receive the final processing outcome.

Failed, cancelled or approval-required results are not transformed. A host older than 0.8.6 rejects `transform`, a host older than 0.8.7 rejects `present`, and a host older than 0.8.8 rejects `wrap` during admission; it does not silently ignore these methods.

SDK/host 0.8.7 adds `@policy.present` for model-facing text after the producer renderer and after any canonical transform:

```python
@policy.present
def present_for_model(request):
    if request["tool_id"] != "example.sensor.temperature":
        return {"action": "keep"}
    return {"action": "replace", "text": "温度：" + request["rendered"]}
```

Present policies run in deployment order. They receive a copied outcome and the current rendered text, and can change only that text. Program consumers return the canonical value before this stage, so program composition never sees display prose. Presenter exceptions or invalid responses preserve the producer text and add an identifier-only diagnostic. When a changed presentation is longer than the preview budget, the existing result material service stores the final display text as a text artifact for the same `inspect_generated_file` continuation; it is not auto-sent and does not rerun the action.

SDK/host 0.8.8 adds `@policy.wrap` for a declaration-based execution lifecycle. The host calls it before and after each real dispatch attempt. The `before` response may set `max_attempts` (up to eight); the `after` response may return `retry` or `return`. A retry is considered only for a real failed outcome, only when every selected wrapper votes to retry, and only for a tool with no declared effects. Effectful actions are never repeated by this contract. Wrapper callbacks do not receive a Python `next()` function, so the host remains the owner of dispatch, cancellation, liveness and side-effect safety. A post-dispatch callback failure reports a structured policy failure with the actual execution receipt rather than claiming the action did not run.

Observers receive a copied outcome containing status/reason and the complete
canonical value when one exists. Legacy presentation-only returns have status
`returned`, not an invented completed-action value. An observer returns None;
mutation or a returned replacement cannot change the finalized business result.
Observer exceptions and invalid returns enter diagnostics while preserving execution
success/failure. Callbacks are awaited; this API does not promise detached or
time-limited observation. In the isolated worker runtime, one service-generation scope retains selected policy
versions across before, execution, transformation and observation. A later invocation resolves the
new generation; explicit withdrawal cannot authorize a not-yet-dispatched target.

Policies run without user/conversation authority. A typed `ToolContext` parameter
can call declared pure service dependencies using normal global-scope admission;
effectful dependency calls are rejected. Only these host-created evaluation scopes
avoid recursively applying the policy chain. Business arguments cannot select this
scope, and user-bound event callbacks do not inherit its bypass. The outer target
still receives its original normal authorization context.

`await ctx.tools.policies()` (also `ctx.services.policies()`) inspects current order,
available stages and recent diagnostics through `capability.invoke`, without
consuming a dependency attempt. Diagnostics contain identifiers only; options,
arguments, values and raw exception messages are not included. This is a runtime
query, not a control-center policy editor. Resource checks occur before custom
policy callbacks. Lifecycle wrapping is provided by `@policy.wrap`; see the [standalone policy and deployment presets](../examples/plugins/akane_sdk_execution_policy/README.md).

## Declared connections

SDK/host 0.8.3 adds plugin-local configuration using the same public JSON Schema
validator as tools. Declare configuration once, then resolve it from a tool,
service method or bound event handler:

```python
from akane_plugin import Plugin, ToolContext

plugin = Plugin("example.client")
plugin.connection("api", schema={
    "type": "object",
    "properties": {
        "endpoint": {"type": "string"},
        "credential": {"type": "string", "minLength": 1},
        "timeout": {"type": "number", "default": 10},
    },
    "required": ["endpoint", "credential"],
    "additionalProperties": False,
}, private_fields=("credential",))

@plugin.tool
async def configured_timeout(ctx: ToolContext) -> float:
    config = await ctx.connections.require("api")
    return float(config["timeout"])
```

`plugin.connection` automatically declares `connection.<name>.read`; installing
the plugin still requires reviewing its permissions. The connection is local to
the plugin, host instance and bound user profile. Names are not a credential
lookup language and do not grant access to another plugin's configuration.
Connection access requires a live, bound invocation; constructors, health checks
and global events cannot choose a profile/session to acquire credentials.

`ctx.connections.resolve(name)` returns `PluginConnectionResult`; `.options`
contains the declared configuration on success. `require` returns that dictionary
or raises `ConnectionError` carrying `.result`. Unhandled connection errors become
structured failed tool/event results, including missing, disabled, schema-version
changed, invalid configuration and revoked scope. Each invocation freezes its
first resolution, including failures, and receives detached copies on later
reads. Concurrent reads share that snapshot. The next invocation reads rotated
settings without restarting the plugin; cancellation still revokes access.

The schema must declare an object and root properties for private fields.
Root-property defaults are applied by the host. Schemas/descriptions are public
catalog data: never put credentials in them; defaults under private fields are
rejected. `private_fields` names whole top-level values, including nested data.
The management API omits those values and reports their configured field names;
the runtime rejects known private values echoed in public results, even under
ordinary keys or from a plugin's earlier invocation. This is projection protection
for trusted plugins, not a sandbox for arbitrary Python or transformed secrets.

Configuration uses the existing per-profile capability settings file and its
atomic writer. `GET/PUT /admin/plugins/{plugin_id}/connections/{name}` requires
the normal management authorization and an explicit `profile_user_id`. PUT takes
`expected_revision`, a `values` patch, optional `clear_fields`, and optional
`enabled`. Omitted values are preserved; no masked-string sentinel is stored.
Invalid input or a revision conflict leaves the saved configuration untouched.
Disabled drafts may omit required fields. Increasing a declaration's `version`
requires saving against the new schema before it can run; removing a private
annotation does not declassify values already stored as private.

Legacy `image_generation` and `rvc` ports remain thin adapters to existing Bot
settings. Their fields and credentials are not copied into the new configuration
section. New declarations use the generic store, including if a plugin chooses
one of those names. SDK 0.8.3 is additive; existing 0.8 integrations remain valid.
The existing 16 KiB private-connection transport budget is diagnosed on save;
deployment budget configuration and control-center forms remain later slices.

See the independently installable, real HTTP example in
[`akane_sdk_http_query`](../examples/plugins/akane_sdk_http_query/README.md).

## Transient events

SDK 0.3 adds `Plugin.on`, `ToolContext.events` and `EventContext`. Install the
two ordinary projects `examples/plugins/akane_sdk_event_source` and
`examples/plugins/akane_sdk_event_calculator` to run a producer → calculation
→ result event chain. Their business code imports only this SDK.

```python
from akane_plugin import EventReceipt, Plugin, ToolContext

plugin = Plugin("example.calculator", permissions=("event.emit", "capability.invoke"))

@plugin.tool
def add(a: int, b: int) -> int:
    return a + b

@plugin.tool
async def publish(a: int, b: int, ctx: ToolContext) -> EventReceipt:
    return await ctx.events.emit("example.numbers", {"a": a, "b": b})

@plugin.on("example.numbers", sources=("example.calculator",))
async def calculate(event, ctx):
    value = await ctx.tools.call("example.calculator.add", event.data)
    return await ctx.events.emit("example.sum", {"sum": value})
```

`on` adds the subscription permission. Emission and tool calls require the
explicit permissions above. `event.source` is assigned by the host from the
actual emitting plugin; `@host` identifies trusted host publication. Payload
fields never grant permissions or select a conversation. Each subscriber
receives an independent JSON snapshot, preserving numbers, booleans, null and
nested values. Events do not write chat history, deliver messages or run a model.

`emit` returns an `EventReceipt` immediately. `accepted` means queued;
`ctx.events.status(receipt.dispatch_id)` gives each subscriber's actual status,
reason, value and cancellation request. An unhandled `ToolCallError` fails that
subscriber. Returning a newly emitted `EventReceipt` from a handler explicitly
links its child dispatch and waits for its terminal receipt; calling `emit`
without returning the receipt leaves the child independent. Cyclic waits fail
with `event_link_cycle`. A child with no subscribers ends as `unobserved` and
does not claim a downstream action happened.

### Event identity is signed by the host

A plugin supplies only the business fact. `ctx.events.emit` takes `event_type`,
`data`, an optional `event_key` (business idempotency) and an optional
`occurred_at_ms`. The host signs everything else:

| Field | Owner | Meaning |
| --- | --- | --- |
| `event_id` | host | unique publication id |
| `source` | host | the real publishing plugin or `@host` |
| `scope` | host | opaque routing scope; read, log or echo it, never parse it, construct it or use it as a business key |
| `version` | host | 1-based counter per `(generation, scope, event_type)` stream |
| `occurred_at_ms` | plugin (optional) | reported time in milliseconds; recorded as-is, never a rejection reason |
| `received_at_ms` | host | always the host's own receipt time |

`version` is **not** a business state version: it restarts with a new plugin
generation or a process restart and is never persisted. A game or any other
stateful plugin must carry its own `state_version` inside `data` and validate
actions against it. `event_key` is scoped by the host to
`plugin_id + generation + source + scope + event_type`: the same key with the
same content returns the original receipt, conflicting content is rejected, and
the same key in another scope is a different event.

```python
receipt = await ctx.events.emit(
    "game.state_changed",
    {"state_version": 42, "hp": 8},
    event_key="turn:42",
)
```

`event.scope` is not the subscription binding handle: `EventBinding.scope_id`
identifies a conversation subscription you created and may unbind. The two are
never interchangeable.

### Record an event in the conversation timeline

`ctx.timeline.append(event)` asks the host to persist one event you received as a
durable conversation record. It never requests a model turn and never sends a
message:

```python
@plugin.on("game.state_changed", name="keep", scope="conversation")
async def keep(event, ctx):
    receipt = await ctx.timeline.append(event)
    return event.data
```

The host owns everything that identifies the record: user, session, character,
timestamp and the durable source id (derived from the host-signed event id, so a
retry is idempotent). The plugin supplies only the event it actually received; an
event that was not delivered to this plugin is rejected with
`timeline_event_not_delivered`. A global handler records to the conversation that
published the event; with no conversation identity at all the host returns
`context_unbound` instead of guessing a target. `receipt.status == "recorded"` is
the only success value; storage failure keeps the real reason and does not pretend
the record exists.

### Host-published channel events

The host publishes real inbound channel facts with `source="@host"`. Subscribe
with the SDK constant rather than a guessed string:

| Constant | Published when | Data |
| --- | --- | --- |
| `DIRECT_CONVERSATION_EVENT` | a private message arrives | the public inbound message summary, `channel`, `parts` |
| `GROUP_CONVERSATION_EVENT` | a group message arrives | the same shape with group identity |
| `POKE_CONVERSATION_EVENT` | a QQ poke targets the bot | `event_kind="poke"`, `channel`, `conversation_kind`, `conversation_id`, `actor_id`, `actor_label`, `outcome_kind`, `status`, `reason` |

`POKE_CONVERSATION_EVENT` is a separate event type, not a `trigger_reason` inside
a message event. The host applies its own care effect before publishing, so a
subscriber receives the resolved `outcome_kind`/`status` and never the host
prompt text. Subscribing does not grant permission to poke back, and receiving
the event does not authorize an action: a reaction needs its own permission and
must go through a public call. Publishing the event does not by itself create a
model turn; use `ctx.request_turn` if the character should respond.

Handlers run asynchronously on the host runtime with no V1 two-second observer
deadline. Each subscription/scope runs serially. `coalesce="latest"` replaces
only pending items and records them as `superseded`; running work finishes by
the normal cancellation contract. Default `coalesce="queue"` preserves order.
An `event_key` deduplicates identical publications within the host-defined event
scope while its receipt is retained; conflicting content returns
`event_key_conflict`. The host does not retry failed business actions.

### Declare what a subscription does with the event

`@plugin.on` takes two independent declarations, so a subscription states its
intent instead of relying on an implicit side effect:

```python
@plugin.on("game.state_changed", name="keep", scope="conversation", persistence="timeline")
async def keep(event, ctx):
    return event.data

@plugin.on("game.turn_ready", name="decide", scope="conversation", request_turn=True)
async def decide(event, ctx):
    return event.data
```

- `persistence="none"` (default) only runs the handler.
- `persistence="timeline"` asks the host to persist the delivered event once for
  the whole publication, even when several subscribers declare it. The delivery
  fails with the real storage reason when the record was not written; it never
  reports a fake completion. `observation` is reserved for a future slice and is
  currently rejected at declaration time.
- `request_turn=True` queues one normal Agent turn through the existing session
  queue after this handler finishes; the delivery waits for the real terminal
  receipt and exposes it in `linked_turn_requests`. It is rejected with
  `context_unbound` for a global subscription.
- Declaring `persistence="timeline"` adds `context.observe`; declaring
  `request_turn=True` adds `agent.turn.request`.

Neither declaration changes what other subscribers see, and neither is implied by
the other.

The default `scope="global"` has no conversation identity. It can call admitted
pure tools (`risk="low"`, `confirm="never"`, no effects or file outputs), but
cannot read conversation resources or resolve private connections. It never
borrows a recent user. For conversation resources declare
`@plugin.on(..., scope="conversation")` and call
`await ctx.events.bind("example.plugin.subscription_name")` from your own
conversation tool. The opaque returned `scope_id` is used with
`ctx.events.unbind(scope_id)`. Matching publications use the subscriber's
explicit binding, not publisher permissions or payload identities. A global
feed can reach explicitly bound conversations when its source is allowed.
The host preserves a conversation publication's routing scope through global
handlers and nested tool calls, without giving those handlers that conversation's
authority. A conversation binding narrows subsequent publications to its own
conversation, including when it receives a global feed. Payloads cannot expand
that routing scope.

Bindings and receipts are in memory only. Disabling, replacing or stopping a
subscription revokes bindings and queued/running work; re-enabling needs new
conversation bindings. Retained processes keep their bindings. Historical
receipts remain queryable by the publishing plugin in the same conversation
until eviction or runtime restart. By default the host retains 1,000 receipts,
evicting terminal records first and pinning active dependent receipts.

Desktop and QQ routes publish public JSON events under
`DIRECT_CONVERSATION_EVENT` / `GROUP_CONVERSATION_EVENT`, with source `@host`.
Bind a conversation subscription before observing its messages. QQ preserves
ordered public message parts; private media locators are not event data.
Result-owned model followup uses the contract above.

## Observe current context without waking the model

SDK 0.4 adds `ctx.observe(key, data)` to both `ToolContext` and `EventContext`.
Declare `context.observe`; callers never supply user, session, character or
memory identifiers. An ordinary tool can update a board with typed JSON:

```python
from typing import Any
from akane_plugin import ObservationReceipt, Plugin, ToolContext

plugin = Plugin("example.board", permissions=("context.observe",))

@plugin.tool
async def update_board(state: dict[str, Any], ctx: ToolContext) -> ObservationReceipt:
    return await ctx.observe("board", state)
```

The host keeps the latest accepted value per plugin/key/user/session/character.
`observed` means the value is available to the next model decision, not that a
reply has been generated. The receipt includes its version. Numbers, `False`,
`None` and nested objects retain their JSON types. Observation updates create
no chat message, memory entry, model request, notification or file delivery.
A tool invoked by a model still follows the normal tool-result continuation
contract; observing itself does not add another model request.

Each model decision receives a frozen snapshot in the existing volatile prompt
context. Later updates do not mutate an in-flight request. A slow older update
cannot replace a newer accepted version and returns `superseded`. Oversized
values use the existing generated-material store and a real
`inspect_generated_file` reference; the full JSON is available for continued
reading without rerunning the update. If storage fails, the update is rejected
with a structured reason and the previous observation stays available.

A conversation event handler can call the same method after its subscription
has been explicitly bound with `ctx.events.bind`. It observes only that bound
context. A global handler returns `context_unbound`, even if the publisher had
a conversation. Bindings and routing also separate characters within the same
session. Unbinding revokes observations owned by that binding; disabling,
stopping or replacing the plugin generation revokes its observations. Ordinary
tool observations survive that tool's normal completion. Observation state is
transient and is lost on runtime restart. Old material handles follow the
existing material-store lifecycle; withdrawing an observation does not promise
to erase a file the model already read.

Updating an observation alone never wakes the character.

## Explicitly request a model turn

SDK 0.5 adds `ctx.request_turn(reason, data=None, *, observations={},
stale="latest", coalesce_key=None)`, `ctx.turn_status(request_id)` and
`ctx.cancel_turn(request_id)` to tools and event handlers. Declare
`agent.turn.request`. Context and destination come from the host's signed
conversation scope; data cannot select a different user, session or character.
Global handlers return `context_unbound`.

```python
from akane_plugin import Plugin, ToolContext, TurnReceipt

plugin = Plugin("example.decisions", permissions=("agent.turn.request",))

@plugin.tool
async def decide(ctx: ToolContext) -> TurnReceipt:
    return await ctx.request_turn("Choose the next move.", {"options": ["A1", "B2"]})
```

The host uses the existing per-session queue and normal channel delivery.
`queued` and `merged` acknowledge pending work; `running` means processing has
started. `complete` marks a terminal receipt. `model_status` and
`delivery_status` describe separate outcomes: desktop `queued` means a frame
entered the desktop output queue, not that the user has heard its TTS.
Delivery failure retains the successful model status. There is no extra model
runner in the SDK or event broker.

Pass observation key/version pairs to require state available at the actual
model decision. `latest` uses the latest accepted versions; `reject` rejects
an outdated dependency. Missing/revoked observations are rejected. The receipt
records the versions used. A snapshot already submitted to a model is frozen.
Requests sharing a nonempty `coalesce_key` may replace one owned pending request
within the same conversation, generation and grant. Running requests are not
rewritten. Large data uses the existing material store and a readable full JSON
reference; storage failure rejects admission.

Returning a `TurnReceipt` from an event handler explicitly links the request.
That delivery stays `waiting` until the real terminal result and exposes
`linked_turn_requests`. Fabricating a receipt for another handler's request is
rejected. Unbinding, disabling or replacing its generation revokes pending work.
Cancelling a running request first returns `cancelling`; completion follows the
runner's actual stop. Old work captured before a user stop cannot wake that
task again; independent requests created afterward are allowed.

The legacy desktop and QQ `current_turn` adapters join the accepted user input instead
of starting another reply. Revoking this participant cannot stop the independent
user input: a running participation receipt ends as `cancelled` with
`model_status=detached`. Desktop `response_ready` means the HTTP response was
constructed; `streamed` means the stream was output normally. Neither confirms
that the window displayed the reply or finished playing speech. QQ steering waits
for actual Engine acceptance before marking the participation as running, then
shares the normal delivery result. Queue and image waits retain the receipt;
`sent` means gateway send success. A passive message woken only by a plugin cannot
run after that grant is revoked or lost on restart. Model followup uses the
normal `ctx.request_turn` contract and its real terminal receipt.

Receipts, grants and observations are transient. A queued plugin request whose
grant was lost on restart is rejected, not replayed as a fresh authorization.
The separate [board example](../examples/plugins/akane_sdk_board/README.md)
demonstrates observation, explicit decisions and linked event receipts.

## Host-owned long tasks

`ctx.task` is the public lifecycle projection for a plugin-owned long task. It
uses the host's existing durable Job store; a plugin does not create a second
database, scheduler or worker pool, and it cannot choose the task owner or
generation.

```python
from akane_plugin import Plugin, ToolContext

plugin = Plugin("example.game")

@plugin.tool
async def start_game(ctx: ToolContext) -> dict:
    task = await ctx.task.create(
        "game.match",
        {"game_id": "match-1"},
        idempotency_key="game:match-1",
    )
    started = await task.update(status="running")
    return {"task": started.as_dict()}
```

`TaskHandle.status()`, `update(status=...)`, `checkpoint(value, version=...)`,
`checkpoint_status()`, `pause()`, `pause_at_boundary()`, `resume()` and
`cancel()` return a real `TaskReceipt`. `TaskContext.open(task_id,
recover=True)` explicitly reopens a task after a host or worker restart. The
public lifecycle states are `created` (durably queued), `running`, `paused`,
`completed`, `failed`, `cancelled`, `stale` and `recovery_unknown`.
`recovery_unknown` is non-terminal: the previous worker may have started an
external side effect, so the host does not retry it automatically. A plugin
must inspect the latest checkpoint, explicitly adopt/resume the task, and
decide how to reconcile the domain state. No old model turn, action, timeline
entry or completion delivery is replayed by reopening a task.

`complete` describes the task lifecycle only: it is true when the task really
reached `completed`, `failed` or `cancelled`. A request rejected because this
invocation's generation was replaced or its scope expired reports
`status="stale"` with `scope_expired=True` and `complete=False`; the task is
still owned elsewhere, so recover it with an explicit
`open(task_id, recover=True)` in an active generation instead of treating it as
finished. `cancel_requested` likewise means the durable stop request was
recorded, not that execution has stopped.

Checkpoint values must be JSON-safe and carry a plugin-owned monotonic
`version`. Repeating the same value at the same version is idempotent; a
different value at that version returns a conflict, and an older version is
stale. The host binds the checkpoint to the task, owner scope, plugin and
generation, and exposes `checkpoint`, `checkpoint_version`,
`checkpoint_generation_id`, `checkpoint_fingerprint` and
`checkpoint_updated_at` in the receipt. A checkpoint is domain state, not a
replacement for host lifecycle state.

Task controls do not request a model turn. A game or other domain plugin must
explicitly publish an event, update an observation, or call `ctx.request_turn`
when that behavior is needed. A queued task can pause and resume normally. A
running task reports `pause_unavailable` unless it was created with
`pause_mode="cooperative"`, has written a current checkpoint, and calls
`pause_at_boundary()` after reaching a safe boundary with no unconfirmed side
effect. Resuming a boundary-paused task returns it to `created`; the plugin
must read the latest checkpoint and explicitly `update(status="running")`
before continuing. For a running task, `cancel()` is only a durable stop
request and normally returns `cancelling`; the executor must stop issuing new
actions and explicitly confirm the safe boundary with
`update(status="cancelled")`. A queued or already paused task can be cancelled
immediately. Generation withdrawal revokes new calls immediately; a late
worker cannot revive the task, and an unconfirmed external effect remains
`recovery_unknown` after restart. Domain state versions are separate from
host-signed `Event.version` and must be checked by the domain service before
applying an action. See
[`akane_sdk_turn_game`](../examples/plugins/akane_sdk_turn_game/README.md) for
the complete event -> observation/timeline -> Agent turn -> action example.

## One QQ command

SDK 0.9 adds `@plugin.qq_command`. The function receives the host's
`PluginQQCommandRequest` and returns a `PluginQQCommandResult`, a mapping, a reply
string, or `None`:

```python
from akane_plugin import Plugin, PluginQQCommandResult

plugin = Plugin("example.ping")

@plugin.qq_command("/ping")
async def ping(request) -> PluginQQCommandResult:
    return PluginQQCommandResult(handled=True, reply_text=f"pong {request.args}".strip())
```

Declaring the command adds `qq.command.register`; a command-only plugin needs no
model tool and creates none. `request` carries the matched `command`, the raw
`args`, `qq_number`, `group_id`, `is_group`, the host-normalized `sender_role` and
an `idempotency_key`. The host decides which senders may use the command and never
lets the plugin choose the target conversation. Returning `handled=False` passes
the message through to the normal model turn; `reply_text` is delivered through
the existing QQ path and does not prove the participant read it.

## Supervised background service

SDK 0.9 adds `@plugin.background`. Use it for work that must keep running between
turns — polling an external state, watching a device, or scheduling your own
timer. The function receives a `BackgroundContext` and runs until the host asks it
to stop or it returns:

```python
from akane_plugin import Plugin

plugin = Plugin("example.sensor", permissions=("event.emit",))

@plugin.background("poll")
async def poll(ctx) -> None:
    while not ctx.shutdown_requested:
        reading = await read_sensor()
        await ctx.events.emit("example.sensor.reading", reading)
        if not await ctx.sleep(30):
            break
```

Declaring the service adds `job.run`; a service-only plugin needs no model tool and
creates none. `ctx` carries the same `tools`, `events`, `connections` and
`resources` ports as a tool or event handler, plus `shutdown_requested`,
`wait_for_shutdown()` and `sleep(seconds)` (which returns `False` as soon as
shutdown is requested). The host owns the task, the restart policy and the bounded
shutdown timeout; the service must not hold Engine, gateway or care-runtime
references and must exit cleanly. Emitting events and observations does not wake
the model by itself — use `ctx.request_turn` when the character should react.

## Providing the TTS service

Declare `service_id="tts"`, `version=1`, `method="synthesize"` on a capability
with `prompt_exposed=False`. A service binding selects the installed plugin;
the request's voice provider selects a voice engine within that implementation.
These are different identities. A pure service adds no model tool.

The request contains `text`, `voice: {provider, profile_id}`, and optional
`emotion`. Return a program value containing `provider_id`, `voice_profile_id`,
`emotion`, `emotion_voice_id`, `profile_fingerprint`, and `media_type`, plus exactly
one managed audio artifact. Echo the requested voice identity; reject an
unsupported voice instead of choosing another voice silently. The host validates
the artifact, its invocation ownership, and the returned voice identity.
Registration does not mean playback or delivery succeeded.

Use the public `tts` connection for existing voice configuration and public
resources for reference audio. Resolve each reference inside the current
invocation; never persist scoped handles or paths in a client pool. Keep reusable
clients inside the worker, keyed by configuration revision, and close them on
shutdown. Health checks must not synthesize speech. Installed permissions and
per-user execution approval are separate checks.

The independently installable [tone example](../examples/plugins/akane_sdk_tts_tone/)
shows the complete SDK-only descriptor, value and managed artifact. The
[first-party plugin](../plugins/akane_tts/) implements Edge/GPT through the shared
speech package. Follow the [installation and binding guide](../docs/plugin_tts_upgrade_v1.md)
to replace a provider through the existing management lifecycle.

## Existing plugins

API-v1 entry points and contracts remain supported. The old
`companion_v01.plugin_api` is a compatibility re-export of these same types,
not a second implementation. New projects import `akane_plugin`.

The old `get_capability_port().invoke()` is a thin compatibility projection:
it forces `send_to_user=false` and returns the historical `content.artifacts`
handle list for file calls. New code uses `call_result` or `ToolContext.tools`
for arbitrary values. This adapter does not own an execution path.

The public `PluginProcessRunner` and `drain` helpers own direct subprocess
lifetime, including repeated cancellation and shutdown. Their old
`companion_v01.plugin_subprocess` imports also re-export the same implementation.
No media policy or job scheduler is packaged with them.

Release verification builds both wheel and source archive, checks their file
scope, creates a fresh virtual environment, installs/invokes the separate math
project and loads both event projects with the Akane host unavailable:

```powershell
python scripts/verify_plugin_sdk_release.py --capcore-wheel path/to/capcore-0.1.3-py3-none-any.whl
```
