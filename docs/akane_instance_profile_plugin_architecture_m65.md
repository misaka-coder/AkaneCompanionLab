# Akane Instance Profile and Plugin Architecture M65

Status: M65-A implemented; M65-B not started

Date: 2026-07-13

## Decision

Akane remains one product and one authoritative core runtime.

A concrete bot is composed at runtime from:

```text
Akane Core
+ one character pack
+ one instance feature profile
+ zero or more explicitly enabled plugins
+ channel bindings and private deployment configuration
= one bot instance
```

The finance direction is not a second Akane product and must not become a
permanent Git branch or a forked copy of the host. It is a private, optional
plugin that can be enabled by any compatible Akane instance, including the
author's personal Akane instance.

Bundled Akane features such as care state may be optional per instance, but
they remain core modules rather than separately distributed plugins.

## Why This Comes Before More Feature Work

The current runtime already has useful extension boundaries:

- character packs own character identity and resources;
- memcore owns reusable memory behavior;
- capcore adapters and providers own reusable capability integration;
- client modes project one turn onto Web, desktop, and QQ surfaces.

The missing boundary is instance composition. Host startup still directly
assembles product and domain behavior, feature switches are spread across
configuration checks, and the finance branch contains domain-specific routes,
workers, prompts, storage, and delivery behavior.

Without an instance model, every new bot or commissioned domain risks becoming
a source fork. M65 introduces the composition boundary before any additional
domain is applied to the public core.

## Vocabulary

| Term | Meaning | Security boundary |
| --- | --- | --- |
| Akane Core | The public, authoritative conversation and host runtime | No |
| Bot instance | One configured running bot identity | Yes |
| Character pack | Persona, resources, and character-owned content | No |
| Feature profile | Explicit selection of bundled optional core modules | No, but instance-scoped |
| Core module | Bundled Akane behavior such as `care` | Runs inside the host |
| External plugin | Separately installed domain capability such as finance | Instance-scoped and permission-scoped |
| Channel binding | QQ, Web, or desktop identity attached to an instance | Yes for credentials and delivery |
| External user | A QQ/Web user interacting with an instance | Yes for memory and relationship state |

A character id is never a tenant or instance id. Changing characters must not
be used as a substitute for isolating data, credentials, quotas, jobs, or
plugin state.

## Target Runtime Shape

```text
Akane Core
├─ mandatory host services
│  ├─ conversation engine
│  ├─ memory integration
│  ├─ character pack resolution
│  ├─ capability/output registries
│  └─ structured status and diagnostics
├─ bundled optional modules
│  ├─ care
│  ├─ music
│  ├─ speech
│  └─ desktop sensing
├─ external plugin host
│  └─ finance (private artifact, optional)
└─ bot instances
   ├─ personal Akane
   └─ hosted finance-oriented Akane
```

The tree describes runtime composition, not separate products. Every instance
uses a released Akane Core version. Product fixes are made once in the core and
become available to all compatible instances.

## Ownership Boundaries

### Akane Core owns

- turn orchestration and provider-neutral model flow;
- memory, character, attachment, output, and capability host contracts;
- channel-neutral instance identity and context propagation;
- feature and plugin activation policy;
- structured plugin/module health and failure reporting;
- permission enforcement, quota hooks, and safe instance storage roots;
- bundled optional core modules.

### Character packs own

- persona and character-specific prompt content;
- visual, audio, expression, and scene resources;
- character-owned knowledge and default presentation preferences.

Character packs do not own infrastructure switches, plugin installation,
secrets, user identity, storage roots, or business licensing.

### External plugins own

- domain prompts and domain tool definitions;
- domain-specific providers and normalization;
- domain routes, jobs, migrations, and private storage schemas;
- domain-specific delivery policy and diagnostics.

The finance plugin therefore owns finance prompts, market-data providers,
subscriptions, event analysis, reports, charts, and push governance. The public
core must not contain `if finance_enabled` branches or import finance modules.

### Private deployment configuration owns

- bot/channel account credentials;
- model and provider secrets;
- enabled plugin artifacts and private plugin configuration;
- per-instance quotas and deployment-specific endpoints;
- server, proxy, backup, and operational configuration.

No secret value or local absolute path belongs in a committed instance
manifest, snapshot, prompt, or log.

## Instance Manifest V1

The first schema is intentionally small. It describes composition, not every
existing Akane setting.

```toml
schema_version = 1
instance_id = "akane-personal"
character_pack_id = "akane_v1"

[features]
care = true

[[plugins]]
id = "akane.finance"
enabled = true

[channels.qq]
enabled = true
profile_ref = "qq.personal"
```

Rules:

- `instance_id` is a stable safe id, not a display name;
- the manifest contains secret references, never secret values;
- an external plugin entry is rejected unless a compatible installed artifact
  provides that exact plugin id;
- installed plugins are not activated implicitly;
- unknown features and plugins produce a structured validation error;
- paths are derived below the selected data root and are not accepted from the
  public manifest;
- M65-A implements only the fields required for instance identity, character
  selection, and the `care` feature decision;
- plugin loading in the example is a later M65 slice and must not be exposed as
  working before its real runtime path exists.

The runtime-selected instance is identified explicitly, for example through
`AKANE_INSTANCE_ID`. Its private manifest is resolved below:

```text
AKANE_DATA_ROOT/instances/<instance_id>/instance.toml
```

M65-A finalizes `AKANE_INSTANCE_ID` as a restart-only deployment selector. An
empty value synthesizes `local-default`; a non-empty value must be a safe id
whose manifest exists at the path above. The selector is intentionally absent
from the control-center settings catalog and cannot be changed as a live
runtime override.

M65-A resolves `features.care` into an immutable snapshot but does not yet use
that value to start or stop the care runtime. That behavior belongs to M65-B.
Until then the snapshot is host-internal and must not be presented in UI or
prompts as though care switching were already active.

## Instance Context

Host operations that can read or mutate instance-owned state eventually
receive one immutable context:

```text
InstanceContext
├─ instance_id
├─ character_pack_id
├─ client/channel identity
├─ external user identity
├─ conversation identity
├─ resolved feature snapshot
├─ resolved plugin snapshot
└─ safe instance storage service
```

M65 must not replace one global with a different implicit global. Context may
be created once at a request/job boundary and passed through typed host
services. New plugin code must not import the module-level `config` object.

Background jobs without an instance id are rejected rather than silently
assigned to a default production instance.

## Module and Plugin Interaction Rules

Core modules and external plugins must not import or call each other's concrete
runtime implementations. They may depend on stable host-owned contracts and on
services explicitly provided through an instance-scoped context.

The host uses four interaction channels. An event bus is only one of them:

| Need | Mechanism | Return value | Durability |
| --- | --- | --- | --- |
| Contribute tools, prompts, routes, or jobs | Registry | Registration result | Rebuilt on startup |
| Query or perform one explicit operation | Service Port | Required | Defined by the port |
| Announce a fact that already occurred | Event Bus | None | Ephemeral by default |
| Schedule, retry, or recover background work | Durable Job / Outbox | Job status | Persisted |

Using one mechanism for every interaction is forbidden. In particular, an
event bus must not replace explicit request/response services or durable job
state.

### Allowed dependency direction

```text
Core module / External plugin
             │
             │ imports stable contracts only
             ▼
     Akane host contracts
             │
             ▼
        PluginContext
        ├─ registries
        ├─ service ports
        ├─ instance event bus
        ├─ durable scheduler/outbox
        └─ scoped diagnostics
```

Forbidden examples include:

```python
from companion_v01.app import engine
from companion_v01.care_runtime import care_runtime
from another_plugin.internal import worker
```

An implementation may import a stable contract type from the future public
plugin API, but the contract package must not import host application modules
or plugin implementations in return.

The host may know bundled module registration entry points in one central
composition root. Direct module-specific calls scattered through routes,
prompts, workers, or delivery code are not allowed.

### Plugin context

Every activated module/plugin receives an immutable, permission-scoped context
for one bot instance. The target surface includes:

```text
PluginContext
├─ instance identity and resolved feature snapshot
├─ capability registry/broker
├─ prompt and output registries
├─ event bus
├─ memory port
├─ delivery port
├─ scheduler/outbox port
├─ safe storage port
├─ quota port
├─ secret reference resolver
└─ structured logger/trace service
```

The context exposes only services granted by the module/plugin manifest. It
does not expose application singletons, raw database connections, unrestricted
filesystem paths, or another instance's context.

### Registry for contributions

Registration is the only way a module/plugin contributes runtime surfaces:

```python
def register(registrar):
    registrar.add_tool(...)
    registrar.add_prompt_block(...)
    registrar.add_route(...)
    registrar.add_job_type(...)
    registrar.subscribe(TurnCompletedV1, on_turn_completed)
```

Registration is transactional. If validation or registration fails, none of
that module/plugin's prompt blocks, tools, routes, jobs, or subscriptions may
remain partially visible.

Disabled modules and plugins are not registered. The host must not register
everything and rely on late `if enabled` checks inside every caller.

Existing capability and output adapter registries remain authoritative for
their current responsibilities. The event bus does not replace them.

### Service Ports for explicit operations

An operation that requires a result, failure reason, ordering guarantee, or
caller-controlled retry uses a typed Service Port:

```python
memory_result = await context.memory.query(request)
delivery_result = await context.delivery.send(request)
quota_result = await context.quota.consume(request)
```

Examples include:

- memory reads and writes;
- sending a user-visible message or file;
- consuming model/token quota;
- resolving character resources;
- storing or retrieving plugin-owned state;
- invoking a capability and waiting for its result.

These operations must not be modeled as fire-and-forget events. The sender must
receive one authoritative status such as `delivered`, `queued`, `failed`, or
`rejected` and must not emit contradictory user-visible success messages.

### Instance-scoped Event Bus for facts

Events announce immutable facts that already occurred. Event names use past
tense and include a schema version, for example:

```text
message.received.v1
turn.completed.v1
character.changed.v1
attachment.ready.v1
instance.started.v1
instance.stopping.v1
```

Commands such as `send_this_file`, `update_memory`, or `run_finance_report` are
not events. They use a Service Port, capability invocation, or Durable Job.

Each bot instance owns a separate Event Bus. A process-global bus shared by all
instances is forbidden.

The stable event envelope includes only the identity required by the event:

```text
event_id
event_type
schema_version
occurred_at
instance_id
character_pack_id (when applicable)
external_user_id (when applicable)
conversation_id (when applicable)
correlation_id
payload
```

Secrets, raw provider credentials, local absolute paths, unrestricted prompt
content, and private storage locations must not enter the envelope.

Event handlers:

- return no business result to the publisher;
- run with bounded timeouts and structured exception isolation;
- are idempotent when an event may be replayed;
- may not recursively publish the same event type without an explicit bounded
  host policy;
- must not make producer success depend on an undocumented subscriber;
- preserve `instance_id` and correlation information in downstream work;
- expose handler failure in diagnostics rather than silently swallowing it.

For example, `care` may subscribe to `turn.completed.v1` only when enabled. A
finance plugin may subscribe to the same generic event when its own behavior
requires it. Neither implementation imports or knows about the other.

### Durable Job and Outbox for reliable work

The in-process Event Bus does not guarantee delivery across restart and must not
own scheduled or retryable workflows.

The following use persisted job/outbox state instead:

- scheduled finance reports and subscriptions;
- delayed or background analysis;
- QQ delivery that must retry after a transient failure;
- work that must resume after process restart;
- long-running generation whose completion is reported later.

The minimum lifecycle is:

```text
scheduled -> pending -> processing -> delivered / failed / cancelled
```

Claims, retries, idempotency keys, attempt limits, and recovery remain owned by
the Durable Job/Outbox service or by plugin-owned durable state behind that
service. An in-memory event subscriber is not a substitute.

### Cross-plugin capability use

Direct plugin-to-plugin imports are forbidden. If one plugin needs an optional
capability provided by another component, it invokes a versioned capability
contract through the host broker:

```python
result = await context.capabilities.invoke("chart.render.v1", request)
```

The consumer manifest declares the required or optional capability id and
version. Startup validation reports `missing_capability` or
`incompatible_capability` before the plugin becomes visible.

If two plugins require broad knowledge of each other's internal types or call
each other continuously, they should normally be one plugin. The capability
broker must not be used to hide a tightly coupled distributed implementation.

### Interaction acceptance rules

M65 interaction tests eventually prove:

- no external plugin imports `companion_v01.app`, host singletons, or another
  plugin implementation;
- disabling a module removes all of its registry contributions and event
  subscriptions;
- events cannot cross instance boundaries;
- a Service Port returns one authoritative operation status;
- subscriber failure is visible but does not create fake producer failure or
  success;
- durable jobs survive process recreation and do not double-deliver after a
  repeated claim;
- missing required capabilities prevent transactional plugin registration;
- event and job diagnostics preserve correlation ids without leaking secrets,
  prompts, or local paths.

## Bundled Optional Module Contract

A bundled module is shipped with Akane Core but participates in a consistent
activation contract:

```text
validate
register_prompt_blocks
register_capabilities
register_jobs
health
shutdown
```

This contract is an internal host boundary. It does not require every small
Akane function to become a plugin.

### Disabled means absent

When a module is disabled for an instance, it must not:

- add prompt content or capability hints;
- register tools or user-visible actions;
- start timers, workers, or scheduled jobs;
- mutate module-owned state;
- emit UI/runtime states that imply the feature is active;
- leave future-only or not-implemented messages in the model-visible path.

The module may preserve its existing instance-owned data so that disabling a
feature is not destructive.

### `care` is the first proof module

For `features.care = false`:

- hunger, energy, affection, coins, and care actions are absent from prompts;
- care tools and QQ care actions are not registered;
- passive decay/recovery and care background work do not run;
- care values are not mutated by unrelated turns;
- user-visible status does not pretend care is active.

If care is later re-enabled, the activation path resets the module's evaluation
baseline before applying time-based change. A long disabled interval must not
produce an immediate accumulated hunger/energy jump.

## External Plugin Contract

External plugin loading is deliberately deferred until the instance and core
module contracts are proven. The target contract includes:

```text
plugin_id
plugin_version
plugin_api_version
compatible_core_versions
permissions
config_schema
secret_fields
storage_schema_version
supported_surfaces
```

Lifecycle:

```text
discover -> validate -> register -> startup -> health -> shutdown
```

Rules:

- discovery uses installed versioned artifacts, never sibling source paths;
- activation requires an explicit instance allowlist;
- registration is transactional: a failed plugin is not partially visible;
- plugin failures return `status/reason` and do not claim success;
- a plugin cannot import host application singletons or reach another
  instance's storage;
- a plugin receives only declared services and permission-scoped operations;
- removing a plugin removes its prompt, tools, routes, jobs, and status surface;
- data migration is owned and versioned by the plugin;
- public Akane starts and passes acceptance without private plugins installed.

The first finance artifact remains private but is not a separate product. It
depends on a released Akane plugin API and can be enabled by any authorized
compatible Akane instance.

## Backward Compatibility

M65-A must preserve the current single-instance development experience.

When no instance selector/manifest is configured, the runtime synthesizes a
`local-default` compatibility instance whose behavior matches the current
client-specific defaults. It must not silently change the current Akane
character, care behavior, QQ settings, data root, or desktop startup path.

The compatibility instance is a migration aid, not a production fallback for
an invalid explicitly selected instance. If an explicit manifest is missing or
invalid, startup returns a structured failure.

No existing database is moved in M65-A. Data migration and multiple instance
roots are separate later slices with explicit backup and rollback tests.

## Isolation Requirements

The target instance-owned namespace is:

```text
instance_id
└─ character_pack_id
   └─ external_user_id
      └─ conversation_id
```

The exact database representation may differ, but every memory, relationship,
subscription, attachment, generated file, quota, job, and plugin state access
must preserve the applicable identity dimensions.

Required negative tests eventually include:

- the same external user talking to two instances receives separate memory;
- two instances using the same character pack do not share relationship state;
- a plugin enabled for one instance is absent from the other's prompt and
  capability snapshot;
- one instance cannot resolve another instance's safe storage path;
- a background job with missing or mismatched instance context is rejected;
- deleting/exporting one instance cannot include another instance's data;
- logs redact secrets and local absolute paths while retaining instance-scoped
  diagnostics.

## Milestone Slices

### M65-A — Instance identity and feature profile

Implementation status: complete.

Scope:

- define and validate the minimal `InstanceManifest`;
- create an immutable resolved feature snapshot;
- synthesize `local-default` when no explicit instance is selected;
- expose instance identity to request/runtime context without moving data;
- add tests proving default behavior is unchanged.

Explicitly out of scope:

- finance extraction;
- plugin discovery;
- UI changes;
- database migration;
- multiple containers or cloud deployment;
- new user-visible feature switches.

### M65-B — Optional `care` module

- route care prompt/tool/job registration through the resolved feature
  snapshot;
- implement true disabled semantics;
- cover QQ, desktop, character switching, repeated turns, and re-enable
  behavior;
- keep one authority care implementation.

### M65-C — External plugin host

- define manifest, compatibility, permissions, lifecycle, and structured
  status;
- load one test plugin from an installed artifact;
- prove missing/invalid/failing plugins do not corrupt core startup;
- keep source-path and editable dependency gates enforced.

### M65-D — Private finance extraction

- checkpoint the current finance branch as migration source;
- return genuinely generic host fixes to public Akane in focused commits;
- package finance-owned prompts, tools, routes, jobs, storage, and providers;
- delete direct finance imports and finance-specific branches from core;
- prove personal Akane may enable the same plugin without a product fork.

### M65-E — Two isolated bot instances

- run personal Akane and a finance-oriented Akane from one core release;
- use separate channel profiles, secrets, data roots, databases, quotas, and
  logs;
- verify cross-instance negative tests;
- only then prepare the hosted cloud deployment.

## Non-Goals

M65 does not introduce:

- a plugin marketplace;
- hot installation or hot reload;
- a customer-facing control plane;
- automated commercial licensing;
- shared-process multi-tenant SaaS;
- one Git branch per customer;
- a second Akane product;
- a rewrite of the conversation engine;
- pluginization of every ordinary core function.

## Acceptance Rules

Every M65 slice must satisfy all of the following:

- default Akane remains runnable after the slice;
- only one authority implementation exists for changed behavior;
- disabled or absent capabilities are absent from real prompt/render/job paths;
- failures are structured and do not become fake success;
- no path, key, storage location, or customer identity leaks into snapshots,
  prompts, logs, or public artifacts;
- focused tests cover the real caller path, not only constant existence;
- `git diff --check` passes;
- public Akane tests do not require the private finance artifact.

## Migration and Rollback

Before M65-A code changes:

1. checkpoint the current package-independence work without mixing unrelated
   user changes;
2. create a private checkpoint for the finance feature branch;
3. record the public main commit and the finance migration source commit;
4. keep runtime behavior unchanged until the M65-A compatibility tests pass.

Each slice is independently reversible. No slice may require deleting the
finance migration source before its replacement path and acceptance tests are
complete.

## Immediate Next Action

Keep M65-A as the committed compatibility boundary, then implement M65-B as a
separate slice: make the existing care runtime obey the resolved snapshot
without changing default behavior. Finance, plugin loading, UI, storage
migration, and cloud deployment remain untouched until their own slices.
