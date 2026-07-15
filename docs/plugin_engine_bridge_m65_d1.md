# Plugin Engine Bridge M65-D1

Status: generic bridge implemented; legacy finance read cutover and public-host surface cleanup complete.

## Boundary

The public Akane host now projects policy-accepted, prompt-exposed plugin
capabilities into the existing Engine tool path through one thin adapter:

```text
PluginHost immutable descriptor snapshot
→ PluginCapabilityToolBridge
→ AdapterCapabilityToolHandler
→ legacy Prompt and provider-native schema
→ PluginHost invocation on its lifecycle loop
```

The bridge does not receive or expose a plugin's raw adapter. It has no plugin
id branches, finance imports, provider configuration, routes, jobs, storage,
or delivery objects. The host contribution policy accepts prompt-visible,
low-risk, never-confirm trusted reads. Read-only capabilities declare the exact
`capability.prompt.invoke` plus `network.read` permission profile and a
`network` effect. Capabilities using the later M65-D2 managed-artifact port
also declare `artifact.write`, a `filesystem` effect, and one bounded
`generated_file` output; see `plugin_managed_artifacts_m65_d2.md`.

Plugin capabilities are dynamic Engine handlers. A disabled, missing, failed,
not-yet-started, stopping, or stopped plugin contributes no handler, Prompt
instruction, or native schema. Plugin invocations are scheduled back onto the
FastAPI lifecycle loop where adapters were activated; synchronous Engine work
must run outside that loop.

## Read Capability Cutover

Owner: Akane project maintainer.

Start milestone: `fc14a12` (generic PluginHost → Engine bridge).

Completed in the focused cutover after the bridge acceptance:

- deleted `AkaneMemoryEngine._build_market_data_tool_service()` and its
  provider/store construction;
- deleted the static finance branch in
  `AkaneMemoryEngine._build_tool_handlers()`;
- deleted public-host handlers for `market_resolve_security`,
  `market_quote_snapshot`, and `market_price_series`;
- removed the finance prompt/profile/static allowlist and the unimplemented
  `market_macro_series` claim from runtime;
- removed FastAPI finance provider/store/worker composition and lifecycle;
- removed finance command/subscription interception from the public QQ route;
- removed finance categories from the default memcore configuration;
- hid legacy finance settings from the public control-center catalog;
- added disabled, missing, and failed plugin tests proving ordinary Akane tools
  remain available without finance descriptors.

The three read capabilities now have one runtime authority: the explicitly
allowlisted private installed plugin. They are absent when that artifact is
disabled, missing, rejected, or fails activation. Akane still starts and its
ordinary tools continue to resolve in each case.

## Public-host Surface Cleanup

The follow-up cleanup removes finance controls that remained visible in the
public host after their runtime callers had already been disconnected:

- removed all finance, market-provider, event-worker, push-governance, EmQuant
  client, and QQ finance switches from `config.Settings` and module globals;
- removed QQ finance-mode commands, per-session overrides, state persistence,
  and background-delivery context hydration;
- removed the finance-only chart/report delivery branches from the QQ route and
  gateway; future plugin artifacts must use a generic, bounded managed-artifact
  port rather than regain access to the concrete QQ gateway;
- collapsed emotion-delivery suppression back to generic generated-file events
  instead of recognizing retired finance event types.

Existing QQ state files are read safely: a legacy `finance_mode_overrides`
member is ignored and omitted on the next ordinary state save. Existing
finance databases and files remain untouched. Legacy finance keys in `.env`
are ignored by pydantic-settings and reported explicitly as retired
public-host configuration; they no longer imply that an inactive host
implementation can be enabled.

## Source Migration Window Closed

M65-D2/D3 moved chart/report artifacts, M65-D5 moved on-demand news, M65-D7
adopted storage/jobs/notifications/QQ commands in the private artifact, and
M65-D8 retired the remaining runtime-disconnected public finance prototype.
`QQMessageContext.finance_mode`, the finance-only Engine prompt scope, and
`services.market_data` are deleted. Public tests remain independent of the
private wheel.

Only the standalone `services.emquant_bridge` local SDK process remains as a
separate, explicitly owned migration window. It does not import Akane Engine or
the deleted market-data/finance packages. See
`docs/plugin_finance_public_retirement_m65_d8.md`.

No database, user asset, instance data, or private artifact content is modified
by the read cutover. Existing finance databases are left untouched but are no
longer opened by public Akane startup.

## Validation

```powershell
python -m unittest tests.test_plugin_engine_bridge -v
python -m unittest tests.test_finance_plugin_absence -v
python -m unittest tests.test_plugin_host tests.test_plugin_artifact_smoke -v
python -m unittest tests.test_tool_runtime tests.test_tool_readiness -v
python -m unittest tests.test_backend_route_modules.BackendRouteModuleTests.test_think_router_handles_once_and_stream_contract_with_fake_engine -v
python -m ruff check companion_v01/plugin_api.py companion_v01/plugin_contribution_policy.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py companion_v01/engine_services/tool_rounds.py companion_v01/tool_orchestration_engine.py tests/test_plugin_engine_bridge.py
git diff --check
```
