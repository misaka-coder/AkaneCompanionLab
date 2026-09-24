# Plugin Effects and Requirements Contract v1

This is the D-slice contract for the public `akane_plugin` SDK. It records the
existing authorities without adding a parallel declaration model.

## Three authorities

| Surface | Example | Authority | Runtime meaning |
| --- | --- | --- | --- |
| `effects` | `network`, `filesystem`, `send_message`, `request_agent_turn` | CapCore tool descriptor | Semantic impact of one invocation; used by risk, confirmation and policy checks. |
| `permissions` | `network.read`, `artifact.write`, `agent.turn.request` | `PluginManifest` and host contribution policy | Host grants requested by a package; reviewed during staging/install and checked at activation/invocation. |
| `requires_services` | `ServiceDependency("statistics", version=1)` | `PluginManifest` and service dependency resolver | Exact versioned activation prerequisite supplied by an enabled provider. |

These fields are intentionally not aliases. An effect does not grant a
permission, a permission does not describe business semantics, and a service
requirement does not grant permission to invoke arbitrary capabilities.

## Enforcement

The active stateful contribution policy cross-checks the declarations:

- `network` effects require `network.read`.
- `plugin_state` effects require `storage.write`.
- Generated-file outputs require `artifact.write`.
- Service descriptors require `service.provide`.
- A plugin that calls another capability must declare `capability.invoke`.

The check is fail-closed. The plugin is not published as active when a required
grant is missing, and the host retains the concrete rejection reason.

`requires_services` is resolved before worker activation. Missing providers,
ambiguous providers, invalid explicit bindings, and dependency cycles produce a
waiting or rejected generation with diagnostics. The consumer's tools, event
handlers and background jobs are not advertised as active until the dependency
plan is satisfiable. Disabling or revoking a provider withdraws dependants from
the active generation.

## Deliberate non-goals

The SDK does not add a generic `requires` field. A second field would duplicate
the existing permission approval chain or the versioned service dependency
graph, making installation review and runtime status disagree. Future host
resources should first define their own typed contract and enforcement path
before they are exposed as a new declaration.

## Verification

The public SDK tests assert that effects remain on CapCore descriptors, while
permissions and `requires_services` remain distinct manifest fields. Runtime
dependency tests cover waiting, provider selection, cycles, reconfiguration and
withdrawal. Installation tests cover exact permission approval and atomic
publication.
