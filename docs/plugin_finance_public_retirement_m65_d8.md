# Public Finance Prototype Retirement M65-D8

Status: public prototype retired; private plugin is the only active finance authority.

## Outcome

M65-D8 closes the last broad public finance migration window. Public Akane no
longer contains a dormant finance event stack or a second market-data domain
implementation. The following source and its authority tests were deleted:

- `companion_v01/finance/**` quote/news event sources, analysis contracts,
  orchestration, worker, moderation, importance, and push governance;
- `services/market_data/**` providers, public-source adapters, finance store,
  domain models, registry, cache, and EmQuant client adapter;
- the optional public-market requirements file;
- the finance-only Engine prompt scope, prompt constants, cache key, memory
  exclusions, Care exclusions, QQ context field, and `market_event` transient
  alias.

The deleted quote-event, model-analysis, moderation, and governance code was a
runtime-disconnected prototype. It was not copied into the private artifact as
inactive code. A future private quote-alert or model-analysis feature must be
implemented from an accepted user-facing requirement against the generic host
ports available at that time. Git history and the archived V1 implementation
documents remain the migration reference; they are not runtime authorities.

## Active Ownership After D8

```text
public Akane
  -> generic PluginHost lifecycle and contribution policy
  -> capability/result/artifact/storage/job/notification/QQ-command ports
  -> ordinary Engine, Prompt, Care, memory, and QQ delivery behavior

private akane.finance wheel
  -> six on-demand market capabilities
  -> chart/report artifacts
  -> owner-scoped subscriptions
  -> public-news polling, durable outbox, and proactive notifications

standalone services.emquant_bridge
  -> local Choice SDK process only
  -> bridge-owned validation and subscription state
  -> no Akane Engine, market_data, or finance package import
```

The standalone EmQuant bridge is the only remaining public finance-adjacent
source window. Its tiny code validator and structured error type now live
inside `services.emquant_bridge`; it no longer depends on the deleted
`services.market_data` package. Akane startup does not import, configure, or
start the bridge. Private-plugin configuration/secrets and an audited bridge
client are a later, separately accepted slice.

## Compatibility Semantics

- legacy `.env` finance keys remain ignored and are reported as retired;
- legacy QQ `finance_mode_overrides` are ignored and omitted on the next
  ordinary state save;
- incoming `finance_mode` and `prompt_scope=finance_push` fields are removed
  before an Engine turn and cannot activate a hidden path;
- disabled, missing, rejected, or failed finance artifacts contribute no
  finance prompt, tool, command, job, storage write, or notification;
- existing finance databases and user files are not opened or deleted.

## Validation Boundary

Public tests assert absence without importing the private wheel. Private
source-blind acceptance remains responsible for proving the installed artifact
can activate through the real host and complete its six on-demand plus
stateful-notification paths.

M65-E cloud/dual-instance deployment is not part of D8. Before M65-E, the
enabled desktop Care compatibility gate and the instance deployment profile
must be accepted independently.
