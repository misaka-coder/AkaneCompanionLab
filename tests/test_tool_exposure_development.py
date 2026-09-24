"""Controlled end-to-end development; live autonomy is a separate opt-in run."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import unittest

from tests import tool_exposure_development_harness as development

PLUGIN = "example.exposure-development"
CAPABILITY = PLUGIN + ".summarize"
MANIFEST = '''[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"
[project]
name = "exposure-development-acceptance"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["akane-plugin>=0.14,<0.15"]
[project.entry-points."akane.plugins.v1"]
"example.exposure-development" = "exposure_development:create_plugin"
[tool.setuptools.packages.find]
where = ["src"]
'''
SOURCE = '''from akane_plugin import Plugin
plugin = Plugin("example.exposure-development")
@plugin.tool
def summarize(values: list[int]) -> dict[str, int]:
    """Return the count, sum and sum of squares of integer values."""
    return {"count":len(values),"sum":sum(values),"squares":sum(v*v for v in values)}
def create_plugin():
    return plugin
'''
CHECK = '''import unittest
from exposure_development import summarize
class CalculationTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(summarize([3,4]), {"count":2,"sum":7,"squares":25})
    def test_empty(self):
        self.assertEqual(summarize([]), {"count":0,"sum":0,"squares":0})
'''


class ToolExposureDevelopmentTests(unittest.TestCase):
    def test_official_development_install_failure_retry_and_same_session_use(self):
        h = development.DevelopmentHarness()
        h.setUp()
        async def run():
            await h.start_development()
            try:
                h.provider("anthropic")
                h.begin(1,"Create and install a plugin for integer count, sum and sum of squares. Verify [3,4].")
                with h.engine.plugin_capability_source.turn_scope():
                    initial,first,loaded = await h.act("load_skill",{"name":"plugin-development"})
                    self.assertIn("# Akane Plugin Development",loaded.followup_context)
                    self.assertNotIn(CAPABILITY,initial[development.TOOL_CAPABILITY_SELECTION_FIELD].tool_names)
                    for path in ("README.md","akane_sdk_math/README.md","akane_sdk_math/src/akane_sdk_math/__init__.py",
                                 "akane_sdk_math/pyproject.toml"):
                        _,_,read = await h.act("project_inspect",{"action":"read","cwd":"alias:akane-sdk","path":path})
                        self.assertEqual(read.stream_events[0]["status"],"succeeded")
                    _,_,created = await h.act("manage_project_workspace",{"action":"create","display_name":"Exposure development"})
                    project = created.state_updates["project_workspace"]["working_directory"]
                    # Source and test files are all created through model-facing
                    # workspace tools. No fixture preinstall or handler injection.
                    for path,content in {"pyproject.toml":MANIFEST,"src/exposure_development/__init__.py":SOURCE,
                                         "tests/__init__.py":"", "tests/test_calculation.py":CHECK.replace('"squares":25','"squares":999')}.items():
                        _,_,written = await h.act("workspace_write",{"path":path,"content":content})
                        self.assertEqual(written.stream_events[0]["status"],"succeeded")
                    _,before_check = await asyncio.to_thread(h.request)
                    _,_,failed = await h.act("manage_extension",{"action":"test_source","path":project})
                    failure = json.loads(failed.followup_context)
                    self.assertFalse(failure["ok"],failure)
                    self.assertNotIn(CAPABILITY,h.runtime.capability_ids)
                    await h.act("workspace_write",{"path":"tests/test_calculation.py","content":CHECK})
                    _,_,passed = await h.act("manage_extension",{"action":"test_source","path":project})
                    self.assertTrue(json.loads(passed.followup_context)["ok"],passed.followup_context)
                    _,_,staged = await h.act("manage_extension",{"action":"stage_source","path":project})
                    stage = json.loads(staged.followup_context)
                    self.assertTrue(stage["ok"],stage)
                    self.assertNotIn(CAPABILITY,h.runtime.capability_ids)
                    # Incorrect permission set is a real install failure. Its
                    # stage can be retried with the exact reviewed permissions.
                    _,_,denied = await h.act("manage_extension",{"action":"install","stage_id":stage["stage_id"],"approved_permissions":[]})
                    self.assertFalse(json.loads(denied.followup_context)["ok"],denied.followup_context)
                    self.assertNotIn(CAPABILITY,h.runtime.capability_ids)
                    _,_,installed = await h.act("manage_extension",{"action":"install","stage_id":stage["stage_id"],"approved_permissions":stage["permissions"]})
                    self.assertEqual(json.loads(installed.followup_context)["status"],"active",installed.followup_context)
                    _,_,listed = await h.act("manage_extension",{"action":"list"})
                    self.assertIn(PLUGIN,listed.followup_context)
                    current,after_install = await asyncio.to_thread(h.request)
                    self.assertIn(CAPABILITY,current[development.TOOL_CAPABILITY_SELECTION_FIELD].tool_names)
                    self.assertNotIn(CAPABILITY,current[development.TOOL_CAPABILITY_SELECTION_FIELD].schema_tool_names)
                    self.assertIn(CAPABILITY,repr(after_install["messages"]))
                    self.assertEqual(initial["tool_exposure_lifecycle"]["compaction_generation"],current["tool_exposure_lifecycle"]["compaction_generation"])
                    h.compare("development","install-notice",before_check,after_install,history_prefix=True)
                    _,_,contract_result = await h.act("capability_load",{"capability_ids":[CAPABILITY]})
                    contract = json.loads(contract_result.followup_context.split("\n",1)[1])["capabilities"][0]
                    _,_,value = await h.act("capability_invoke",{"capability_id":CAPABILITY,"contract_ref":contract["contract_ref"],"arguments":{"values":[3,4]}})
                    self.assertEqual(value.capability_result.value,{"count":2,"sum":7,"squares":25})
                    _,before_update = await asyncio.to_thread(h.request)
                    # The candidate uses the public adapter protocol to report
                    # a real unhealthy activation. Staging only prepares metadata.
                    bad_source = SOURCE.replace('import Plugin','import Plugin, HealthStatus').replace(
                        'Plugin("example.exposure-development")',
                        'Plugin("example.exposure-development", version="0.2.0")').replace(
                        '    return plugin', '    return UnhealthyPlugin()') + """
class FailingHealthAdapter:
    provider_id = "acceptance-health"
    async def health(self):
        return HealthStatus(ok=False, status="unavailable", reason="acceptance_health_failed")
    async def list_capabilities(self):
        return ()
    async def invoke(self, capability_id, args, context):
        raise RuntimeError("No capabilities on health probe adapter")
    async def aclose(self):
        pass
class UnhealthyPlugin:
    manifest = plugin.manifest
    def register(self, registrar):
        plugin.register(registrar)
        registrar.add_capability_adapter(FailingHealthAdapter())
"""
                    await h.act("workspace_write",{"path":"src/exposure_development/__init__.py","content":bad_source})
                    await h.act("workspace_write",{"path":"pyproject.toml","content":MANIFEST.replace('version = "0.1.0"','version = "0.2.0"')})
                    _,_,bad_staged = await h.act("manage_extension",{"action":"stage_source","path":project})
                    bad = json.loads(bad_staged.followup_context)
                    self.assertTrue(bad["ok"],bad)
                    _,_,bad_installed = await h.act("manage_extension",{"action":"install","stage_id":bad["stage_id"],"approved_permissions":bad["permissions"]})
                    self.assertFalse(json.loads(bad_installed.followup_context)["ok"],bad_installed.followup_context)
                    self.assertIn(CAPABILITY,h.runtime.capability_ids)
                    # The old provider may have a fresh rollback generation;
                    # recover its actual current contract rather than guessing.
                    _,_,retained = await h.act("capability_load",{"capability_ids":[CAPABILITY]})
                    retained_ref = json.loads(retained.followup_context.split("\n",1)[1])["capabilities"][0]["contract_ref"]
                    _,_,old_value = await h.act("capability_invoke",{"capability_id":CAPABILITY,"contract_ref":retained_ref,"arguments":{"values":[3,4]}})
                    self.assertEqual(old_value.capability_result.value,{"count":2,"sum":7,"squares":25})
                    await h.act("workspace_write",{"path":"src/exposure_development/__init__.py","content":SOURCE.replace('Plugin("example.exposure-development")','Plugin("example.exposure-development", version="0.2.0")')})
                    _,_,fixed_stage = await h.act("manage_extension",{"action":"stage_source","path":project})
                    fixed = json.loads(fixed_stage.followup_context)
                    self.assertTrue(fixed["ok"],fixed)
                    _,_,fixed_install = await h.act("manage_extension",{"action":"install","stage_id":fixed["stage_id"],"approved_permissions":fixed["permissions"]})
                    self.assertEqual(json.loads(fixed_install.followup_context)["status"],"active",fixed_install.followup_context)
                    _,after_repair = await asyncio.to_thread(h.request)
                    h.compare("development","activation-failure-repair",before_update,after_repair,history_prefix=True)
                    # User preference is saved through the same control-center
                    # service; the very next request publishes it without compression.
                    preference = h.select_resident(CAPABILITY)
                    row = next(row for row in preference["state"]["tools"] if row["id"] == CAPABILITY)
                    self.assertTrue(row["pending"],row)
                    h.finish()
                    h.begin(2,"Use the installed calculation plugin directly for [3,4].")
                    final,after,value = await h.act(CAPABILITY,{"values":[3,4]})
                    self.assertEqual(final["tool_exposure_lifecycle"]["compaction_generation"],initial["tool_exposure_lifecycle"]["compaction_generation"])
                    self.assertEqual(value.capability_result.value,{"count":2,"sum":7,"squares":25})
                    # Current workspace context is a transient tail block and moves between turns.
                    h.compare("development","resident-next-request",after_repair,after,tools_equal=False)
                    self.assertTrue(h.approved_requests)
                report_dir = os.environ.get("AKANE_EXPOSURE_WIRE_REPORT_DIR")
                if report_dir:
                    h.write_trace(Path(report_dir)/"development-controlled-trace.json")
            finally:
                await h.stop_development()
        try:
            asyncio.run(run())
        finally:
            h.doCleanups()


if __name__ == "__main__":
    unittest.main()
