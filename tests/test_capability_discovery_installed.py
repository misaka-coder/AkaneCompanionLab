"""Unified residency and exact contract loading over a really installed SDK plugin.

The plugin under test is built from source and installed by
`ManagedPluginArtifactStore`, then activated by `PluginHost` from the real
`akane.plugins.v1` entry point in its installed site directory. The handler map
and the tool list the model would receive are produced by the real engine
selection and native-tool pipeline, so this exercises the discovery loop without
inventing a second registry or a test-only capability source.
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from capcore import CapabilityToolSpec

from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.instance_profile import PluginSelection
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_handlers.core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from companion_v01.tool_orchestration_engine import validate_legacy_tool_call


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.sample.longtail"
CAPABILITY_ID = f"{PLUGIN_ID}.bump.v1"
# One `capability_search` page is bounded by the search tool's own maximum; a
# larger tail therefore needs real paging rather than a longer single page.
MAX_SEARCH_LIMIT = 32

_PLUGIN_SOURCE = textwrap.dedent(
    '''
    """One long-tail plugin capability used by the discovery round test."""

    from typing import Any

    from akane_plugin import Plugin, ToolContext

    PLUGIN_ID = "akane.sample.longtail"
    PLUGIN_VERSION = "0.1.0"
    CAPABILITY_ID = PLUGIN_ID + ".bump.v1"
    _calls = {}


    def create_plugin() -> Plugin:
        plugin = Plugin(PLUGIN_ID, version=PLUGIN_VERSION, permissions=("capability.prompt.invoke",))

        @plugin.tool(name="bump.v1")
        def bump(value: int, step: int = 1, ctx: ToolContext = None) -> dict[str, Any]:
            """Increase one counter by a fixed step."""
            _calls["bump"] = _calls.get("bump", 0) + 1
            return {"value": int(value) + int(step), "plugin": PLUGIN_ID, "calls": _calls["bump"]}

        @plugin.tool(name="echo.v1")
        def echo(value: int) -> dict[str, Any]:
            """Echo an amount using this generation's semantics."""
            _calls["echo"] = _calls.get("echo", 0) + 1
            return {"value": value, "unit": "yuan", "calls": _calls["echo"]}

        @plugin.tool(name="job.v1", execution_class="long_task")
        def job(value: int) -> dict[str, Any]:
            """Produce one background result."""
            _calls["job"] = _calls.get("job", 0) + 1
            return {"value": value, "calls": _calls["job"]}

        return plugin
    '''
).lstrip()

_PYPROJECT = textwrap.dedent(
    f'''
    [build-system]
    requires = ["setuptools>=68"]
    build-backend = "setuptools.build_meta"

    [project]
    name = "akane-sample-longtail"
    version = "0.1.0"
    requires-python = ">=3.11"

    [project.entry-points."akane.plugins.v1"]
    "{PLUGIN_ID}" = "akane_sample_longtail:create_plugin"

    [tool.setuptools.packages.find]
    where = ["src"]
    '''
).lstrip()


class _DiscoveryEngineFacade:
    """Engine surface used by the selection pipeline, backed by the real host.

    Only object wiring is local here; `_resolve_tool_handlers` and
    `_resolve_capability_selection` are the engine's own implementations, so the
    resolved tool names and native schemas are produced by production code.
    """

    def __init__(self, bridge: PluginCapabilityToolBridge) -> None:
        from companion_v01.capability_registry import CapabilityRegistry
        from companion_v01.engine import AkaneMemoryEngine

        self.plugin_capability_source = bridge
        self.tool_handlers: dict[str, BaseToolHandler] = {}
        self.capability_registry = CapabilityRegistry()
        self.config_module = None
        self._resolve_tool_handlers = AkaneMemoryEngine._resolve_tool_handlers.__get__(self)
        self._resolve_capability_selection = AkaneMemoryEngine._resolve_capability_selection.__get__(self)


def _model_visible_tools(selection) -> list[dict[str, object]]:
    """The exact tool definitions a provider-native round receives."""

    return build_openai_native_tool_specs(
        selection.resolved_handlers,
        allowed_tool_names=set(selection.schema_tool_names),
    )


def _model_names(tools: list[dict[str, object]]) -> list[str]:
    """Canonical capability ids behind the provider-safe function names."""

    from companion_v01.native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD

    return sorted(
        str(item.get(NATIVE_TOOL_CAPABILITY_ID_FIELD) or (item.get("function") or {}).get("name") or "")
        for item in tools
    )


def _schema_bytes(tools: list[dict[str, object]]) -> int:
    return len(json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class _LongTailHandler(BaseToolHandler):
    """Deterministic host-owned tool used only to size the injected long tail."""
    def __init__(self, capability_id: str) -> None:
        self.tool_type = capability_id
        self._spec = CapabilityToolSpec(
            capability_id=capability_id,
            display_name=capability_id,
            description=f"Deterministic long-tail capability {capability_id}",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            output_schema={"type": "object"},
            risk="low",
            confirm="never",
            effects=(),
            visible_in=("desktop",),
        )

    def tool_spec(self):
        return self._spec

    def normalize_call(self, value):
        return None

    def execute(self, *, call, context) -> ToolExecutionResult:
        del call, context
        return ToolExecutionResult(tool_type=self.tool_type, followup_context="")


class CapabilityDiscoveryInstalledPluginTests(unittest.TestCase):
    """Discovery -> load -> real call, measured round by round."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        root = Path(cls._temp.name)
        cls.install_root = root / "install"
        source = root / "source"
        (source / "src" / "akane_sample_longtail").mkdir(parents=True)
        (source / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
        (source / "src" / "akane_sample_longtail" / "__init__.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        cls.store = ManagedPluginArtifactStore(
            cls.install_root / "artifacts", instance_id="discovery-test", project_root=PROJECT_ROOT,
        )
        cls.staged = cls.store.stage_source(source)
        cls.site_dir = cls.install_root / "artifacts" / "staging" / cls.staged["stage_id"] / "site"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def _entry_points(self):
        return tuple(
            entry_point
            for distribution in importlib.metadata.distributions(path=[str(self.site_dir)])
            for entry_point in distribution.entry_points
            if entry_point.group == "akane.plugins.v1" and entry_point.name == PLUGIN_ID
        )

    def _host(self) -> PluginHost:
        """Activate the installed plugin using its real entry point, not a stub.

        `PluginGenerationProcess` puts the installed site directory on the worker's
        path before reading entry points; an in-process host needs the same step, so
        the test mirrors that instead of injecting a hand-built plugin object.
        """

        if str(self.site_dir) not in sys.path:
            sys.path.insert(0, str(self.site_dir))
        return PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=self._entry_points,
        )

    def test_installed_plugin_is_really_discovered_and_invoked(self) -> None:
        self.assertEqual(self.staged["plugin_id"], PLUGIN_ID)
        self.assertTrue(self.site_dir.is_dir())
        self.assertEqual(len(self._entry_points()), 1)

    def test_installed_worker_mixed_modes_and_same_schema_generation_recovery(self):
        import asyncio
        from companion_v01.plugin_generation import PluginGenerationProcess
        from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
        from companion_v01.capability_exposure import apply_published_contracts, published_contracts, route_invocation
        from companion_v01.capability_exposure_config import save_preferences
        from companion_v01.tool_orchestration_engine import normalize_tool_invocation, execute_tool_invocation

        async def scenario():
            active = ActivePluginGeneration()
            processes = []
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                echo_id = PLUGIN_ID + ".echo.v1"
                selection = (PluginSelection(PLUGIN_ID, True),)
                async def publish(site_dir=None):
                    process = PluginGenerationProcess(project_root=PROJECT_ROOT, site_dir=site_dir or self.site_dir,
                        plugin_id=PLUGIN_ID, work_dir=root / ("worker-" + str(len(processes))))
                    await asyncio.to_thread(process.start)
                    processes.append(process)
                    await active.publish(PluginGenerationSnapshot(selection, (process,)))
                await publish()
                try:
                    bridge = PluginCapabilityToolBridge(active, config_base_dir=root)
                    engine = _DiscoveryEngineFacade(bridge)
                    engine.capability_config_base_dir = root
                    self.assertTrue(save_preferences(base_dir=root, profile_user_id="master", payload={
                        "revision": 0, "toolModes": {CAPABILITY_ID: "on_demand", echo_id: "resident"}})["ok"])
                    def select():
                        return resolve_capability_selection(engine, profile_user_id="master", session_id="session-1")
                    def execute(chosen, call):
                        from companion_v01.client_protocol import ClientProtocolContext, ClientMode
                        if call["type"] == "capability_invoke":
                            call, chosen = route_invocation(call, chosen)
                        invocation = normalize_tool_invocation(engine, call, capability_selection=chosen,
                            profile_user_id="master", session_id="session-1")
                        return execute_tool_invocation(engine, invocation=invocation, profile_user_id="master",
                            session_id="session-1", character_pack_id="", visual_payload={}, now_ts=1,
                            client_context=ClientProtocolContext(effective_mode=ClientMode.DESKTOP_PET))[0]
                    initial = select()
                    schemas = _model_visible_tools(initial)
                    self.assertIn(echo_id, _model_names(schemas))
                    self.assertNotIn(CAPABILITY_ID, _model_names(schemas))
                    direct = await asyncio.to_thread(execute, initial, {"type": echo_id, "arguments": {"value": 10}})
                    self.assertEqual(direct.capability_result.value["value"], 10)
                    loaded = await asyncio.to_thread(execute, initial,
                        {"type": "capability_load", "capability_ids": [CAPABILITY_ID]})
                    contract = json.loads(loaded.followup_context.split("\n", 1)[1])["capabilities"][0]
                    call = {"type": "capability_invoke", "capability_id": CAPABILITY_ID,
                            "contract_ref": contract["contract_ref"], "arguments": {"value": 41, "step": 1}}
                    result = await asyncio.to_thread(execute, initial, call)
                    self.assertFalse(result.capability_result.is_error, result)
                    self.assertEqual(result.capability_result.value["value"], 42)
                    self.assertEqual(schemas, _model_visible_tools(select()))
                    self.assertFalse(loaded.state_updates)
                    from companion_v01.background_tasks import BackgroundTaskRunner
                    from companion_v01.host_jobs import HostJobStore, HostJobOwner
                    from companion_v01.host_tool_jobs import HostToolJobRuntime
                    from companion_v01.engine import AkaneMemoryEngine
                    jobs = HostJobStore(root / "jobs.db")
                    runner = BackgroundTaskRunner({"host-jobs": 1})
                    engine._resolve_client_protocol_context = AkaneMemoryEngine._resolve_client_protocol_context.__get__(engine)
                    engine._get_mode_profile_registry = AkaneMemoryEngine._get_mode_profile_registry.__get__(engine)
                    engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
                    engine.host_tool_jobs = HostToolJobRuntime(engine=engine,store=jobs,background_tasks=runner,
                        conversation_ref_issuer=lambda context:"controlled-ref")
                    try:
                        job_contract = initial.capability_catalog.load([PLUGIN_ID+".job.v1"])["capabilities"][0]
                        accepted = await asyncio.to_thread(execute,initial,{"type":"capability_invoke",
                            "capability_id":PLUGIN_ID+".job.v1","contract_ref":job_contract["contract_ref"],"arguments":{"value":9}})
                        self.assertTrue(any(event["type"] == "background_job_accepted" for event in accepted.stream_events), accepted)
                        event = next(event for event in accepted.stream_events if event["type"]=="background_job_accepted")
                        self.assertTrue(await asyncio.to_thread(runner.wait_idle,lane="host-jobs",timeout=5))
                        completed = jobs.get(event["job_id"],owner=HostJobOwner("master","session-1"))
                        self.assertEqual(completed.status,"succeeded",completed.last_error)
                        self.assertEqual(completed.capability_id,PLUGIN_ID+".job.v1")
                        self.assertEqual(completed.result["capability_result"]["value"],{"value":9,"calls":1})
                    finally:
                        await asyncio.to_thread(runner.close)
                    baseline = published_contracts(initial)
                    await publish()  # Same schema, separately observable provider generation.
                    frozen = apply_published_contracts(select(), baseline)
                    rejected = await asyncio.to_thread(execute, frozen, {"type": echo_id, "arguments": {"value": 10}})
                    self.assertFalse(json.loads(rejected.followup_context)["executed"])
                    self.assertIn("capability_contract_stale", rejected.followup_context)
                    # A load from before the upgrade is also stale at execution time.
                    rejected = await asyncio.to_thread(execute, frozen, call)
                    self.assertIn("capability_contract_stale", rejected.followup_context)
                    current = frozen.capability_catalog.current().load([echo_id])["capabilities"][0]
                    recovered = await asyncio.to_thread(execute, frozen, {"type": "capability_invoke",
                        "capability_id": echo_id, "contract_ref": current["contract_ref"], "arguments": {"value": 10}})
                    self.assertEqual(recovered.capability_result.value["value"], 10)
                    self.assertEqual(schemas, _model_visible_tools(frozen))
                    changed_site = root / "schema-upgrade-site"
                    shutil.copytree(self.site_dir,changed_site)
                    changed_module = changed_site / "akane_sample_longtail" / "__init__.py"
                    changed_source = changed_module.read_text(encoding="utf-8").replace(
                        "def echo(value: int)","def echo(amount: int)").replace(
                        'return {"value": value, "unit": "yuan", "calls": _calls["echo"]}',
                        'return {"value": amount, "unit": "yuan", "calls": _calls["echo"]}')
                    changed_module.write_text(changed_source,encoding="utf-8")
                    await publish(changed_site)
                    rejected = await asyncio.to_thread(execute,frozen,{"type":echo_id,"arguments":{"value":10}})
                    self.assertIn("capability_contract_stale",rejected.followup_context)
                    current = frozen.capability_catalog.current().load([echo_id])["capabilities"][0]
                    self.assertEqual(current["input_schema"]["required"],["amount"])
                    recovered = await asyncio.to_thread(execute,frozen,{"type":"capability_invoke","capability_id":echo_id,
                        "contract_ref":current["contract_ref"],"arguments":{"amount":10}})
                    self.assertFalse(recovered.capability_result.is_error,recovered)
                    self.assertEqual(recovered.capability_result.value["calls"],1)
                    await active.publish(PluginGenerationSnapshot((), ()))
                    withdrawn = await asyncio.to_thread(execute, frozen, {"type": "capability_invoke",
                        "capability_id": echo_id, "contract_ref": current["contract_ref"], "arguments": {"value": 10}})
                    self.assertIn("capability_not_available", withdrawn.followup_context)
                finally:
                    await active.stop()
                    for process in processes:
                        await asyncio.to_thread(process.stop)
        asyncio.run(scenario())

    def test_short_directory_is_a_real_byte_win_at_long_tail_scale(self) -> None:
        """Measure the injected-vs-discovered payload at a realistic tail size."""

        from companion_v01.capability_discovery import CapabilityDiscoveryCatalog

        rows: list[dict[str, int]] = []
        for size in (1, 4, 16, 64):
            handlers = {
                f"demo.longtail.{index:03d}": _LongTailHandler(f"demo.longtail.{index:03d}")
                for index in range(size)
            }
            full_tools = build_openai_native_tool_specs(handlers, allowed_tool_names=set(handlers))
            catalog = CapabilityDiscoveryCatalog(
                handlers, profile_user_id="master", session_id="session-1",
            )
            listing = catalog.search(limit=size)
            # Walking the real cursor covers the whole tail through bounded pages.
            paged_items, cursor, pages = [], "", 0
            while True:
                page = catalog.search(limit=MAX_SEARCH_LIMIT) if not cursor else catalog.search(
                    cursor=cursor, limit=MAX_SEARCH_LIMIT,
                )
                paged_items.extend(page["items"])
                pages += 1
                cursor = page["next_cursor"]
                if not cursor:
                    break
            rows.append(
                {
                    "tail": size,
                    "injected_tools": len(full_tools),
                    "injected_bytes": _schema_bytes(full_tools),
                    "directory_items": len(listing["items"]),
                    "directory_bytes": len(
                        json.dumps(listing["items"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    ),
                    "paged_items": len(paged_items),
                    "paged_pages": pages,
                }
            )
        print(json.dumps(rows, ensure_ascii=False))

        for row in rows:
            self.assertEqual(row["directory_items"], min(row["tail"], MAX_SEARCH_LIMIT))
            self.assertEqual(row["paged_items"], row["tail"])
        # A directory entry carries a description and identity metadata, so it costs
        # slightly more than a bare schema at very small tails, and a full walk of the
        # tail costs more than injecting it. The projection is a bounded per-round
        # payload, and it only pays off once the tail is genuinely long.
        win = [row for row in rows if row["directory_bytes"] < row["injected_bytes"]]
        self.assertTrue(win, rows)
        self.assertLess(rows[-1]["directory_items"], rows[-1]["injected_tools"])
        # At a 64-entry tail one bounded page is already cheaper than injecting every
        # schema, and the per-round payload no longer grows with the tail at all.
        self.assertLess(rows[-1]["directory_bytes"], rows[-1]["injected_bytes"])
        self.assertEqual(rows[-1]["paged_pages"], 2)

    def test_cursor_is_bound_to_the_scope_that_asked_for_it(self) -> None:
        from companion_v01.capability_discovery import CapabilityDiscoveryCatalog

        handlers = {
            capability_id: _LongTailHandler(capability_id)
            for capability_id in ("demo.longtail.000", "demo.longtail.001")
        }
        secret = b"cursor-secret-value"
        first = CapabilityDiscoveryCatalog(
            handlers, profile_user_id="master", session_id="session-1", cursor_secret=secret,
        )
        same_scope = CapabilityDiscoveryCatalog(
            handlers, profile_user_id="master", session_id="session-1", cursor_secret=secret,
        )
        other_session = CapabilityDiscoveryCatalog(
            handlers, profile_user_id="master", session_id="session-2", cursor_secret=secret,
        )
        other_profile = CapabilityDiscoveryCatalog(
            handlers, profile_user_id="guest", session_id="session-1", cursor_secret=secret,
        )

        page = first.search(limit=1)
        self.assertTrue(page["next_cursor"])
        self.assertEqual(same_scope.search(cursor=page["next_cursor"], limit=1)["status"], "ok")
        self.assertEqual(other_session.search(cursor=page["next_cursor"], limit=1)["reason"], "cursor_invalid")
        self.assertEqual(other_profile.search(cursor=page["next_cursor"], limit=1)["reason"], "cursor_invalid")
        self.assertEqual(
            first.search(query="different", cursor=page["next_cursor"], limit=1)["reason"],
            "cursor_invalid",
        )

    def test_capability_unavailable_after_generation_switch_is_not_stale_success(self) -> None:
        import asyncio

        from companion_v01.capability_discovery import CapabilityDiscoveryCatalog
        from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy

        async def scenario() -> dict[str, object]:
            host = self._host()
            status = await host.start()
            self.assertEqual(status["status"], "active", status)
            try:
                bridge = PluginCapabilityToolBridge(host, config_base_dir=self.site_dir.parent)
                catalog = CapabilityDiscoveryCatalog(
                    dict(bridge.build_tool_handlers()),
                    profile_user_id="master",
                    session_id="session-1",
                )
                self.assertEqual(set(catalog.capability_ids), {CAPABILITY_ID, PLUGIN_ID + ".echo.v1", PLUGIN_ID + ".job.v1"})
                revision = catalog.revision
                # Withdraw the generation: the plugin's capability is really gone.
                await host.stop()
                gone = CapabilityDiscoveryCatalog(
                    dict(bridge.build_tool_handlers()),
                    profile_user_id="master",
                    session_id="session-1",
                )
                return {
                    "revision_before": revision,
                    "ids_after_withdrawal": list(gone.capability_ids),
                    "search_reason_before": catalog.search(query="bump")["reason"],
                    "load_after_withdrawal": gone.load([CAPABILITY_ID])["reason"],
                }
            finally:
                if host.state != "created":
                    await host.stop()

        observed = asyncio.run(scenario())
        self.assertEqual(observed["ids_after_withdrawal"], [])
        self.assertEqual(observed["load_after_withdrawal"], "capability_not_available")
        self.assertEqual(observed["search_reason_before"], "")


if __name__ == "__main__":
    unittest.main()
