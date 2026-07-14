# Plugin Engine Bridge M65-D1

Status: generic bridge implemented; legacy finance read cutover complete.

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
or delivery objects. The host contribution policy accepts only the exact
`capability.prompt.invoke` plus `network.read` permission profile and
prompt-visible, low-risk, never-confirm capabilities whose only declared
effect is `network`.

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

## Remaining Source Migration Window

The following public source remains temporarily for later private-plugin
migration, but has no composition-root, Engine, prompt, settings, or QQ route
entry point:

- market news search and event ingestion;
- deterministic chart/report providers and handlers;
- finance subscription, orchestration, delivery, and worker classes;
- legacy finance config fields and QQ gateway compatibility state/methods;
- `services.market_data` and EmQuant bridge source.

Reason:

- these capabilities need future plugin contracts for storage, jobs, commands,
  notifications, and managed file delivery;
- deleting source before those contracts exist would discard tested behavior,
  while reactivating it in the host would recreate two authorities.

Window rule:

- do not add public runtime callers, settings, prompts, routes, providers, or
  feature work to the frozen finance source;
- fixes may only preserve tests, remove coupling, or support transfer into the
  private artifact;
- new finance behavior belongs to the private plugin repository.

Remaining exit conditions:

1. define generic plugin ports for each capability actually being migrated;
2. move the corresponding implementation and tests into the private artifact;
3. delete the transferred public source and compatibility fields in the same
   change window;
4. keep public Akane tests independent of the private wheel.

No database, user asset, instance data, or private artifact content is modified
by the read cutover. Existing finance databases are left untouched but are no
longer opened by public Akane startup.

## Validation

```powershell
python -m unittest tests.test_plugin_engine_bridge -v
python -m unittest tests.test_finance_plugin_absence tests.test_finance_domain_profile -v
python -m unittest tests.test_plugin_host tests.test_plugin_artifact_smoke -v
python -m unittest tests.test_tool_runtime tests.test_tool_readiness -v
python -m unittest tests.test_backend_route_modules.BackendRouteModuleTests.test_think_router_handles_once_and_stream_contract_with_fake_engine -v
python -m ruff check companion_v01/plugin_api.py companion_v01/plugin_contribution_policy.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py companion_v01/engine_services/tool_rounds.py companion_v01/tool_orchestration_engine.py tests/test_plugin_engine_bridge.py
git diff --check
```
