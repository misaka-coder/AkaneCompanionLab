# Plugin On-Demand Market News M65-D5

Status: on-demand market-news cutover complete; proactive ingestion,
subscriptions, jobs, notifications, and EmQuant remain closed.

## Ownership Probe

The probe required after M65-D4 separates one user-requested read from the
stateful proactive system. They do not share a safe migration boundary.

| Surface | Real owner | Contract needed before migration | M65-D5 decision |
|---|---|---|---|
| on-demand public news search | private `akane.finance` wheel | existing capability invoke, `network.read`, structured result experience | migrated |
| market event database, watchlists, delivery claims | private finance runtime | scoped plugin data root, schema ownership, migration/backup policy | remain frozen |
| subscription and QQ finance commands | private finance domain + public channel shell | generic authenticated command contribution and owner-scoped state | remain frozen |
| proactive poll/analysis/retry worker | private finance runtime | restart-only supervised job lifecycle and bounded shutdown/status | remain frozen |
| proactive user notification | public client/channel shell | generic notification intent, authorization, idempotency, and delivery result | remain frozen |
| EmQuant SDK and bridge | private external service | scoped config/secret resolution and process deployment contract | remain frozen |

No generic port is introduced without a real consumer. In particular, D5 does
not disguise polling as capability invocation, pass a concrete QQ gateway into
the plugin, let a plugin choose arbitrary host paths, or read retired public
finance environment variables.

## Implemented Cutover

The installed private artifact owns
`akane.finance.search_market_news.v1`. The capability:

- fetches a bounded page from Eastmoney's public 7×24 endpoint only when the
  model explicitly invokes it;
- accepts a bounded keyword, trusted fixed-registry security codes, one real
  content type, ISO dates, and a bounded result limit;
- performs deterministic keyword/security/date filtering;
- preserves event id, publication time, source link, and verification labels;
- states that source reports need verification and never converts news tone
  into price causality or a trading claim;
- returns M65-D3 structured result experience for Akane-owned final wording;
- has no database, background thread, subscription, command, notification, or
  delivery side effect.

The source-blind wheel acceptance invokes the news capability through the real
`PluginHost` and `PluginCapabilityToolBridge`, proving that its facts,
limitations, and source evidence reach the existing model feedback path. QQ
uses the ordinary Akane response path; D5 does not claim proactive QQ delivery.

The frozen public `MarketNewsSearchToolHandler`, its read orchestration service,
and their exports were deleted in the same cutover. Public provider/event-store
source still used by the frozen proactive stack remains in its documented
migration window and has no composition-root or prompt entry point.

## Failure and Safety Rules

- HTTP status, response size, JSON shape, and item count are bounded.
- One bounded retry is allowed for transient transport/server failures, never
  for rate limits.
- Provider failures return stable status/reason values without raw exceptions.
- Unknown securities, unsupported content types, invalid dates, and empty
  query/code scope fail before network access.
- Import, factory, registration, health, and capability enumeration are network
  inert.
- The plugin never persists news, mutates the old finance database, or claims a
  notification was sent.

## Remaining Exit Order

The next stateful slice must be driven by the existing subscription/event
consumer and introduce only the contracts it actually needs:

1. scoped plugin data root plus private finance schema ownership;
2. supervised restart-only job contribution;
3. authenticated QQ command contribution and generic notification intent;
4. private event/subscription/orchestration migration and public-source deletion;
5. EmQuant scoped config/secret and external bridge migration;
6. only after those gates and the Care compatibility gate, M65-E instance
   isolation and hosted deployment.

Cloud deployment is intentionally not combined with this cutover.

## Validation

Private artifact:

```powershell
$env:AKANE_HOST_ROOT = '<public Akane root>'
$env:PYTHONPATH = $env:AKANE_HOST_ROOT
python -m unittest discover -s tests -p "test_*.py"
python -m ruff check src tests
```

Public Akane:

```powershell
python -m unittest tests.test_plugin_engine_bridge tests.test_finance_plugin_absence
python -m unittest tests.test_public_market_provider tests.test_public_market_news
python -m unittest tests.test_backend_route_modules
git diff --check
```
