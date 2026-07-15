# Akane Instance Profile and Plugin Architecture M65

Status: M65-A/B/C, M65-D1 through M65-D8, and M65-E1/E2 implemented

Date: 2026-07-15

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
| External plugin | Separately installed domain capability such as finance | No code sandbox; activation is instance-scoped and permission-scoped |
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
- the manifest contains deployment profile references, never secret values;
- an external plugin selection is validated before startup and its exact id is
  resolved only from installed artifact metadata;
- installed plugins are not activated implicitly;
- unknown feature fields and unknown plugin-entry fields produce a structured
  validation error; syntactically valid but uninstalled plugin ids degrade the
  optional host at startup;
- paths are derived below the selected data root and are not accepted from the
  public manifest;
- `[channels.qq]` accepts only `enabled` and `profile_ref`; an enabled channel
  requires a safe non-secret profile reference;
- M65-C accepts only `id` and `enabled` in each `[[plugins]]` entry and rejects
  duplicate ids, invalid identifiers, unknown fields, and invalid types;
- `enabled=true` requests activation but does not make an optional plugin a
  hard dependency: a missing or failed artifact degrades `PluginHost` while
  Akane core continues to start.

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

### M65-B implementation record

M65-B routes Care activation through the host-owned `CareModulePort`:

- `care=true` delegates to the existing `CareRuntimeStore` and preserves the
  `local-default` behavior;
- `care=false` does not construct or read the store and returns stable
  `status=disabled`, `reason=feature_disabled` results for Care commands;
- disabled turns remove client-supplied Care context, Care prompt contracts,
  `state_request`, and Care output state;
- QQ economy routes no longer obtain the concrete store directly, and finance
  push turns cannot update Care state;
- `/desktop-pet/health` exposes only a safe Care feature projection; it does
  not expose instance identity, paths, secrets, or storage configuration;
- the desktop runtime waits for that projection and an authoritative backend
  snapshot before exposing Care. Disabled or unavailable instances omit
  `desktop_care`, reject Care actions, stop refresh and work-presentation
  timers, clear the away presentation, and show no invented Care values;
- the primary desktop control panel keeps the shop action hidden until both the
  host Care feature and the active character's real shop configuration are
  available. The panel delegates opening to the main runtime gate instead of
  invoking the Tauri shop window directly;
- existing pre-migration Care data is preserved while disabled. Explicit
  instance activation initializes the server evaluation baseline without
  changing hunger, energy, affection, coins, inventory, or work-task values.

#### Enabled desktop state compatibility window (closed)

The compatibility window is closed. `CareRuntimeStore` is now the single state
authority for enabled desktop Care. It owns hunger decay, turn energy cost,
affinity, coins, inventory, allowance, and work-task settlement. The desktop
runtime only renders authoritative snapshots and submits validated actions
through `/desktop-pet/care/snapshot` and `/desktop-pet/care/action`.

Migration and failure semantics are:

- the first successful snapshot may import the active character's legacy Care
  block from `pet_state.json`. A persisted server-side authority marker makes
  this import one-shot; later client payloads cannot overwrite server state;
- after successful import, the active character's Care block is written as
  `null` in `pet_state.json`. Unvisited character blocks remain available as
  migration input until each character is activated once;
- `pet_state.json` is not an offline Care authority. When the backend is
  unavailable the shop is unavailable, actions fail visibly, and the client
  does not calculate or persist substitute results;
- model `state_request` is interpreted by the backend. The client accepts only
  the final `care_state` snapshot and never applies affinity locally;
- work timers are presentation timers only: they ask the backend to settle a
  due task and cannot grant coins themselves;
- the closed window is guarded by `tests.test_care_runtime`,
  `tests.test_character_pack_qq_mode`,
  `tests.test_desktop_pet_backend_contract`,
  `tests.test_desktop_pet_frontend_contract`,
  `npm run smoke:care-feature`, and `npm run build`.

## External Plugin Contract

M65-C implements a deliberately narrow installed-artifact plugin contract. It
includes:

```text
plugin_id
plugin_version
plugin_api_version
permissions
```

The only M65-C permission is `diagnostics.invoke`, and the only contribution is
a capcore `CapabilityAdapter` whose capabilities are non-Prompt, low-risk,
never-confirm, and side-effect-free. Config, secrets, storage, prompts, routes,
jobs, events, QQ commands, and hot mutation remain outside this slice.

These are composition-policy decisions, not permanent `PluginHost` mechanics.
The host owns discovery, identity, lifecycle, transactional publication,
immutable invocation snapshots, and cleanup. The app composition root supplies
`M65CDiagnosticContributionPolicy`, which owns the currently allowed
permissions and descriptor shape. Consequently:

- adding another diagnostic plugin does not modify `PluginHost`;
- a later capability policy may admit other permissions, risk levels,
  confirmation modes, effects, or Prompt visibility without plugin-id branches
  in `PluginHost`;
- a genuinely new contribution surface such as prompts, routes, jobs, or
  service ports must receive its own host-owned registry when a real M65-D
  requirement exists; it must not be faked through `CapabilityAdapter` or by
  continually expanding one giant registrar;
- the current exact `plugin_api_version == 1` check is the initial compatibility
  strategy, not a promise that all future host API versions require exact
  equality;
- packaged-only artifact loading is the M65-C production policy. A future
  development policy may explicitly admit editable artifacts and must mark the
  host non-production; no automatic editable fallback is allowed;
- the empty service context and absence of plugin routes are M65-C scope
  limits, not claims that useful future plugins can never receive scoped host
  services or constrained route groups.

The rule is: strict identity, lifecycle, transaction, and sensitive-data
boundaries; replaceable policy for the contribution surfaces deliberately
opened by one composition.

### Trust and transaction boundary

M65-C plugins are trusted in-process extensions selected by an explicit
instance allowlist. Wheel installation, artifact audit, and allowlisting do
not provide a Python sandbox or prevent arbitrary behavior by a trusted
artifact.

`entry_point.load()` imports Python code. Import-created threads, globals,
filesystem changes, or network effects cannot be rolled back. Therefore:

- plugin module import must have no external side effects;
- the entry point must load a zero-parameter factory callable;
- the factory must return an object with a valid `PluginManifest` and
  synchronous `register(registrar)` implementation;
- transactional activation means only that failed staged adapters are never
  committed to the active plugin and capability snapshots;
- transactional activation does not unload an imported Python module;
- activation failure closes every staged adapter in reverse order with a
  bounded, best-effort `aclose()`;
- close failure never prevents later adapters from closing and never replaces
  the original activation failure reason.

Artifact audit is completed before `entry_point.load()`. If reliable
distribution metadata is unavailable, activation fails with
`distribution_metadata_unavailable`; the host does not import the plugin,
read module `__file__`, or scan a source directory as a fallback. Editable and
source-directory installations are rejected by the same
`distribution_artifacts.py` authority used by the packaged dependency check.

### Restart-only lifecycle

`PluginHost.__init__` only captures immutable selections and callables. It
does not discover distributions, audit artifacts, import modules, construct
plugins, register adapters, call health, or enumerate capabilities. Those
operations occur only in asynchronous FastAPI startup through
`PluginHost.start()`:

```text
created
  -> starting
  -> active / degraded
  -> stopping
  -> stopped
```

`start()` and `stop()` are idempotent. The host does not support live install,
registration, enable/disable, or capability-map mutation. Startup publishes
immutable active-plugin and capability snapshots. Once stopping begins, new
invocations return `host_unavailable`; active adapters close at most once in
reverse activation order.

An enabled but missing or failed optional plugin yields aggregate `degraded`
status without preventing Akane core startup. A future hard dependency must
use a separate `required`/dependency contract and must never be inferred from
`enabled`.

### Identity and invocation rules

- plugin ids are lowercase safe ids of at most 64 characters;
- entry point name, instance selection id, and `PluginManifest.plugin_id` must
  match exactly;
- capability ids are safe ids of at most 128 characters and must start with
  the complete `<plugin_id>.` prefix;
- manifest version must match installed distribution version and API version
  must equal `1`;
- capcore remains the authority for descriptor types, argument validation,
  invocation context, health, results, and adapter shutdown;
- every host consumer must supply an explicit immutable `InvocationContext`;
  the M65-C admin diagnostic route supplies only
  `client_mode="plugin_admin"` and never invents a user or session identity;
- M65-C capabilities still never enter Engine, ordinary Prompt, QQ, Care,
  finance, or desktop paths.

The diagnostic surface is limited to:

```text
GET  /admin/plugins/status
POST /admin/plugins/capabilities/<capability_id>/invoke
```

It trusts only the actual ASGI socket peer and ignores forwarded headers.
Only loopback IP peers are accepted. Invoke bodies must be bounded JSON
objects; results are recursively projected to bounded JSON-safe values.
Tracebacks, raw exception messages, local absolute paths, distribution paths,
entry-point module paths, and obvious secret-bearing fields are never returned.

### Implemented M65-C rules

- discovery uses installed versioned artifacts, never sibling source paths;
- activation requires an explicit instance allowlist;
- registration is transactional: a failed plugin is not partially visible;
- plugin failures return `status/reason` and do not claim success;
- the M65-C registrar exposes only diagnostic capability adapters and provides
  no host singleton, config, instance storage, user identity, or secret port;
- the host cannot prevent trusted Python code from importing other modules or
  performing arbitrary side effects; artifact review and plugin policy remain
  mandatory;
- a disabled, missing, or failed plugin contributes no capability and never
  enters Prompt, Engine, QQ, Care, finance, or desktop runtime paths;
- public Akane starts and passes acceptance without private plugins installed.

Policy rejection is projected as the stable public pair
`reason=contribution_policy_rejected` plus a bounded `stage`; policy-internal
Python shape details are not promoted into permanent public error codes.

### M65-D1 host-consumer foundation

The first M65-D1 public slice does not enable a finance plugin or change the
current Engine tool catalog. It adds only the generic host contract needed by
a later Engine bridge:

- `PluginHost.capability_descriptors` returns an immutable point-in-time
  mapping of copied capcore descriptors and never exposes raw adapters;
- `PluginHost.invoke()` requires the caller's `InvocationContext` and passes
  its profile, session, and client dimensions unchanged to the adapter;
- all host consumers share one bounded result projector before plugin content
  leaves the host boundary;
- safe plugin business failures retain their structured `status`, `reason`,
  and content; invalid status codes are normalized, while paths,
  secret-bearing fields, non-JSON values, and oversized values become a
  generic structured failure.

This slice deliberately does not add Prompt, profile, route, command, job,
storage, configuration, secret, or event registries. The Engine consumer and
the private installed finance artifact remain later, separately reversible
M65-D1 slices.

The installed-wheel acceptance fixture registers only
`akane.test.diagnostic.ping.v1`. Its module import and invocation do not read
configuration or user data, write files or databases, start threads, call the
network, or mutate Akane state. It is a dev/acceptance artifact and is not a
runtime dependency.

M65-C validation:

```powershell
python -m unittest tests.test_instance_profile tests.test_plugin_host tests.test_plugin_artifact_smoke -v
python -m unittest tests.test_package_independence tests.test_package_reintegration_policy -v
python -m py_compile companion_v01\plugin_api.py companion_v01\plugin_contribution_policy.py companion_v01\plugin_result_projection.py companion_v01\distribution_artifacts.py companion_v01\plugin_host.py companion_v01\routes\plugins.py
python -m ruff check companion_v01\plugin_api.py companion_v01\plugin_contribution_policy.py companion_v01\plugin_result_projection.py companion_v01\distribution_artifacts.py companion_v01\plugin_host.py companion_v01\routes\plugins.py tests\test_plugin_host.py tests\test_plugin_artifact_smoke.py
git diff --check
```

`python scripts\check_packaged_dependencies.py --json` remains the release
artifact gate. It intentionally reports `editable_install_forbidden` in a
developer checkout whose extracted packages are installed editable; the
installed-wheel plugin smoke separately proves the M65-C accepted artifact
path without weakening that release rule.

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

Implementation status: complete; the enabled desktop compatibility window is
closed by the host-owned Care authority recorded above.

- route care prompt/tool/job registration through the resolved feature
  snapshot;
- implement true disabled semantics;
- cover QQ, desktop, character switching, repeated turns, and re-enable
  behavior;
- keep one authority care implementation.

### M65-C — External plugin host

Implementation status: complete for the trusted, restart-only diagnostic
capability slice.

- define manifest, compatibility, permissions, lifecycle, and structured
  status;
- load one test plugin from an installed artifact;
- prove missing/invalid/failing plugins do not corrupt core startup;
- keep source-path and editable dependency gates enforced.

### M65-D — Private finance extraction

Implementation status: the generic host-consumer foundation and Engine tool
bridge are complete. The private installed artifact owns read-only market data,
on-demand news, charts, reports, owner-scoped subscriptions, proactive jobs,
durable delivery state, and notifications. M65-D8 deleted the dormant public
finance event/analysis/store/provider stack. Only the standalone local EmQuant
bridge remains a separate future adoption window.

The M65-D1 bridge consumes only the immutable capability descriptor snapshot
and `PluginHost.invoke_from_consumer()`. It does not expose raw adapters to the
Engine. Policy-accepted prompt capabilities enter the existing dynamic handler,
legacy Prompt, provider-native schema, and tool execution paths. Invocation is
scheduled onto the PluginHost lifecycle loop so plugin async resources are not
used from an Engine worker's temporary event loop. See
`docs/plugin_engine_bridge_m65_d1.md` for the bounded legacy finance migration
window.

- checkpoint the current finance branch as migration source;
- return genuinely generic host fixes to public Akane in focused commits;
- package finance-owned prompts, tools, routes, jobs, storage, and providers;
- delete direct finance imports and finance-specific branches from core;
- prove personal Akane may enable the same plugin without a product fork.

### M65-E — Two isolated bot instances

Implementation status: E1 fail-closed instance root and E2 instance-owned paths complete; E3 through E5
remain pending. See `docs/akane_instance_isolation_m65_e.md`.

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

The M65-D1 through M65-D8 finance extraction is closed. The installed private
artifact owns six active capabilities plus its stateful subscription,
public-news job, durable outbox, QQ command, and proactive notification path.
Public Akane retains only generic plugin, result-experience, managed-artifact,
storage, supervised-job, notification, command, Engine-event, and
client-delivery boundaries.

The ownership probe is recorded in `docs/plugin_market_news_m65_d5.md`. The
public scoped storage, supervised job, notification, and QQ command contracts
are recorded in `docs/plugin_stateful_runtime_m65_d6.md`.

M65-D7 adopts those contracts in the private finance artifact. Subscription,
seen-event, and delivery-outbox state now lives below the host-provided plugin
storage root. Five owner-scoped QQ commands, one cooperative public-news job,
and idempotent proactive text delivery are covered by source-blind installed
wheel acceptance. The cutover record is
`docs/plugin_finance_stateful_adoption_m65_d7.md`.

M65-D8 records the final ownership decision in
`docs/plugin_finance_public_retirement_m65_d8.md`: the inactive quote-event/LLM
analysis/moderation/governance prototype and shared market-data store were
deleted instead of becoming a second authority. The standalone EmQuant bridge
now owns its small validation contract but is not connected to Akane startup.

M65-E1 binds an instance id permanently to one explicit data root and holds an
exclusive process lock before root-owned saved settings or databases load.
M65-E2 moves the named-instance workspace, memory mirror, constrained Memcore
override, prompt audit, capability configuration, and background-writer
lifecycle behind that runtime layout. It also provides the fail-closed offline
one-instance migration tool documented in
`docs/akane_instance_isolation_m65_e.md`. The next gate is E3: QQ account,
ingress secret, OneBot access token, management authentication, and deployment
template isolation. EmQuant plugin configuration and secrets remain a separate
audited slice and must not enter M65-E.
