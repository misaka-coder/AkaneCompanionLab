# Plugin Engine Bridge M65-D1

Status: generic bridge implemented; legacy finance read cutover pending.

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

## Documented Migration Window

Owner: Akane project maintainer.

Start milestone: the M65-D1 bridge commit carrying this document.

Temporary legacy authority paths:

- `AkaneMemoryEngine._build_market_data_tool_service()`;
- the finance branch in `AkaneMemoryEngine._build_tool_handlers()`;
- `market_resolve_security`, `market_quote_snapshot`, and
  `market_price_series` in `companion_v01.finance.tool_handlers`;
- the matching static finance domain-profile tool names and tests.

Reason for temporary coexistence:

- public Akane tests must not require the private artifact;
- the private installed wheel must first pass the real Engine bridge and one
  controlled public-market acceptance turn;
- deletion and absence semantics need one focused cutover with a reversible
  checkpoint.

Window rule:

- do not add features, providers, fields, Prompt claims, or new callers to the
  three legacy read handlers;
- fixes required for migration may only reduce or freeze that path;
- all new read capability work belongs to the private installed artifact.

Exit conditions:

1. the private wheel is discovered from an installed, audited artifact;
2. resolve, quote, and series capabilities traverse Prompt/native/execution
   through `PluginCapabilityToolBridge`;
3. disabled, missing, and failed artifact tests show no finance tool in the
   real Engine handler set;
4. the three legacy read handlers and their Engine construction path are
   deleted, with old tests migrated or removed;
5. ordinary Akane, QQ chat, Care, memory, TTS, and desktop behavior still pass
   their focused regressions.

Rollback before cutover is to disable the instance plugin and revert the
generic bridge slice. No database, user asset, instance data, or private
artifact content is modified by this bridge.

## Validation

```powershell
python -m unittest tests.test_plugin_engine_bridge -v
python -m unittest tests.test_plugin_host tests.test_plugin_artifact_smoke -v
python -m unittest tests.test_tool_runtime tests.test_tool_readiness -v
python -m unittest tests.test_backend_route_modules.BackendRouteModuleTests.test_think_router_handles_once_and_stream_contract_with_fake_engine -v
python -m ruff check companion_v01/plugin_api.py companion_v01/plugin_contribution_policy.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py companion_v01/engine_services/tool_rounds.py companion_v01/tool_orchestration_engine.py tests/test_plugin_engine_bridge.py
git diff --check
```
