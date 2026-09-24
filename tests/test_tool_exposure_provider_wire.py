"""Installed workers and MCP subprocesses in ordinary/streaming provider requests."""
from __future__ import annotations
import asyncio
import json
import sys
import unittest

from companion_v01.capability_exposure_config import save_preferences
from companion_v01.local_capability_config import save_capability_approval_mode
from companion_v01.mcp_host_manager import McpHostManager, McpManagementService
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.instance_profile import PluginSelection
from tests import test_tool_exposure_wire as wire
from tests import test_capability_discovery_installed as installed
from tests.test_tool_exposure_mcp import SERVER

class ProviderWireFixture:
    setUp = wire.ToolExposureWireTests.setUp
    provider = wire.ToolExposureWireTests.provider
    begin = wire.ToolExposureWireTests.begin
    request = wire.ToolExposureWireTests.request
    execute_response = wire.ToolExposureWireTests.execute_response
    finish = wire.ToolExposureWireTests.finish
    partitions = staticmethod(wire.ToolExposureWireTests.partitions)
    compare = wire.ToolExposureWireTests.compare

    async def roundtrip(self, protocol, stream, resident, deferred, arguments, upgrade, revoke, value_check):
        self.provider(protocol)
        self.engine.tool_handlers = {}
        saved = save_preferences(base_dir=self.root,profile_user_id=self.scope["profile_user_id"],payload={
            "revision":0,"toolModes":{resident:"resident",deferred:"on_demand"}})
        self.assertTrue(saved["ok"],saved)
        self.begin(1,"Controlled provider history. " * 1400)
        async def act(name,args):
            context,payload = await asyncio.to_thread(self.request,action=(name,args),stream=stream)
            result,envelope = await asyncio.to_thread(self.execute_response)
            return context,payload,result
        initial,first,result = await act(resident,arguments)
        self.assertFalse(result.capability_result.is_error,result)
        value_check("direct",result)
        async def load(name):
            _,_,result = await act("capability_load",{"capability_ids":[name]})
            return json.loads(result.followup_context.split("\n",1)[1])["capabilities"][0]
        contract = await load(deferred)
        _,_,result = await act("capability_invoke",{"capability_id":deferred,"contract_ref":contract["contract_ref"],"arguments":arguments})
        self.assertFalse(result.capability_result.is_error,result)
        value_check("deferred",result)
        _,before = await asyncio.to_thread(self.request)
        self.compare("provider-real",f"{resident}-{stream}-initial",first,before,history_prefix=True)
        await upgrade()
        _,stale,result = await act(resident,arguments)
        self.assertFalse(json.loads(result.followup_context)["executed"])
        self.assertIn("capability_contract_stale",result.followup_context)
        current = await load(resident)
        published = initial[wire.TOOL_CAPABILITY_SELECTION_FIELD].capability_catalog.snapshot(resident)
        self.assertEqual(published["spec"]["input_schema"],current["input_schema"])
        _,_,result = await act("capability_invoke",{"capability_id":resident,"contract_ref":current["contract_ref"],"arguments":arguments})
        self.assertFalse(result.capability_result.is_error,result)
        value_check("recovered",result)
        _,_,result = await act(resident,arguments)
        self.assertFalse(json.loads(result.followup_context)["executed"])
        _,recovered = await asyncio.to_thread(self.request)
        self.compare("provider-real",f"{resident}-{stream}-recovery",before,recovered,history_prefix=True)
        self.finish()
        compacted = await asyncio.to_thread(self.manager.compact_due_sync,provider_profile=protocol,**self.scope)
        self.begin(2)
        final,after,result = await act(resident,arguments)
        self.assertGreater(final["tool_exposure_lifecycle"]["compaction_generation"],
                           initial["tool_exposure_lifecycle"]["compaction_generation"],compacted)
        self.assertFalse(result.capability_result.is_error,result)
        value_check("boundary",result)
        current = await load(resident)
        await revoke()
        _,_,result = await act("capability_invoke",{"capability_id":resident,"contract_ref":current["contract_ref"],"arguments":arguments})
        self.assertFalse(json.loads(result.followup_context)["executed"])
        self.assertIn("capability_not_available",result.followup_context)
        self.compare("provider-real",f"{resident}-{stream}-boundary",recovered,after,tools_equal=False)

class InstalledProviderWireTests(ProviderWireFixture,unittest.TestCase):
    setUpClass = classmethod(installed.CapabilityDiscoveryInstalledPluginTests.setUpClass.__func__)
    tearDownClass = classmethod(installed.CapabilityDiscoveryInstalledPluginTests.tearDownClass.__func__)

    def scenario(self,protocol,stream):
        async def run():
            active = ActivePluginGeneration()
            processes = []
            async def upgrade():
                process = PluginGenerationProcess(project_root=installed.PROJECT_ROOT,site_dir=self.site_dir,
                    plugin_id=installed.PLUGIN_ID,work_dir=self.root / ("worker-"+str(len(processes))))
                await asyncio.to_thread(process.start)
                processes.append(process)
                await active.publish(PluginGenerationSnapshot((PluginSelection(installed.PLUGIN_ID,True),),(process,)))
            async def revoke():
                await active.publish(PluginGenerationSnapshot((),()))
            await upgrade()
            self.engine.plugin_capability_source = PluginCapabilityToolBridge(active,config_base_dir=self.root)
            try:
                def check(stage,result):
                    self.assertEqual(result.capability_result.value["calls"],2 if stage=="boundary" else 1)
                with active.freeze_invocation_scope():
                    await self.roundtrip(protocol,stream,installed.PLUGIN_ID+".echo.v1",installed.CAPABILITY_ID,
                                         {"value":10},upgrade,revoke,check)
            finally:
                await active.stop()
                for process in processes:
                    await asyncio.to_thread(process.stop)
        asyncio.run(run())

class McpProviderWireTests(ProviderWireFixture,unittest.TestCase):
    def scenario(self,protocol,stream):
        server,state,log = self.root/"server.py",self.root/"state.json",self.root/"calls.jsonl"
        server.write_text(SERVER,encoding="utf-8")
        state.write_text('{"unit":"yuan"}',encoding="utf-8")
        manager = McpHostManager(timeout_seconds=8)
        self.engine.mcp_host_manager = manager
        service = McpManagementService(base_dir=self.root,manager=manager)
        configured = service.configure(profile_user_id=self.scope["profile_user_id"],server_id="demo",payload={
            "enabled":True,"command":sys.executable,"args":[str(server),str(state),str(log)],"transport":"stdio"})
        self.assertTrue(configured["ok"],configured)
        for name in ("resident","deferred"):
            saved = save_capability_approval_mode(base_dir=self.root,profile_user_id=self.scope["profile_user_id"],
                capability_id="mcp.demo."+name,mode="trusted_auto_allow")
            self.assertTrue(saved["ok"],saved)
        async def upgrade():
            state.write_text('{"unit":"cent"}',encoding="utf-8")
        async def revoke():
            value = await asyncio.to_thread(service.set_enabled,profile_user_id=self.scope["profile_user_id"],
                                            server_id="demo",enabled=False)
            self.assertTrue(value["ok"],value)
        def check(stage,result):
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(calls),{"direct":1,"deferred":2,"recovered":3,"boundary":4}[stage])
            self.assertEqual(calls[-1]["arguments"],{"amount":10})
            self.assertEqual(calls[-1]["unit"],"cent" if stage in {"recovered","boundary"} else "yuan")
        try:
            asyncio.run(self.roundtrip(protocol,stream,"mcp.demo.resident","mcp.demo.deferred",
                                       {"amount":10},upgrade,revoke,check))
            self.assertEqual(len(log.read_text().splitlines()),4)
        finally:
            self.assertTrue(manager.close())

for _class in (InstalledProviderWireTests,McpProviderWireTests):
    for _protocol in ("openai","anthropic"):
        for _stream in (False,True):
            setattr(_class,f"test_{_protocol}_stream_{_stream}",wire._scenario("scenario",_protocol,_stream))
# Do not export the loop's class alias as another unittest suite.
del _class
