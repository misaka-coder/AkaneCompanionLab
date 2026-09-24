"""Acceptance driver over existing tools/services, never a plugin installer.

The controlled test supplies provider replies. The live runner uses LLMRuntime's
configured model without replacing its replies. Both share the production
selection, argument normalization, approval, execution and MemCore record paths.
Only persona/retrieval inputs and the test instance's storage are isolated.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
import unittest
from unittest.mock import patch

import requests

import config
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.capability_exposure_config import read_preferences, save_preferences
from companion_v01.capability_exposure_service import save_exposure_state
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.project_workspace import ProjectWorkspaceService
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.catalog import build_builtin_tool_handlers
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD, TOOL_MODEL_NAME_FIELD
from companion_v01.tool_orchestration_engine import execute_tool_invocation, normalize_tool_invocation
from tests import test_tool_exposure_wire as wire

ROOT = Path(__file__).resolve().parents[1]
DEV_TOOLS = frozenset({"load_skill", "manage_project_workspace", "project_inspect",
    "workspace_write", "workspace_patch", "exec_run", "exec_status", "exec_cancel", "manage_extension"})


class DevelopmentEngine(wire.WireEngine):
    # Exercise the normal desktop selection/permission scope, not the wire
    # fixture's no-client administration route.
    _resolve_capability_selection = AkaneMemoryEngine._resolve_capability_selection
    _resolve_client_protocol_context = AkaneMemoryEngine._resolve_client_protocol_context


class DevelopmentHarness(unittest.TestCase):
    setUp = wire.ToolExposureWireTests.setUp
    provider = wire.ToolExposureWireTests.provider
    begin = wire.ToolExposureWireTests.begin
    request = wire.ToolExposureWireTests.request
    finish = wire.ToolExposureWireTests.finish
    partitions = staticmethod(wire.ToolExposureWireTests.partitions)
    compare = wire.ToolExposureWireTests.compare

    async def start_development(self):
        prior = self.engine
        self.engine = DevelopmentEngine(memcore_manager=self.manager)
        self.engine.prompt_builder = prior.prompt_builder
        self.engine.capability_config_base_dir = self.root
        self.engine.store = MemoryStore(self.root / "users")
        self.approvals = CapabilityApprovalStore()
        self.engine._get_approval_store = lambda: self.approvals
        execution_root = self.root / "execution"
        execution_root.mkdir()
        self.workspace = ProjectWorkspaceService(store=self.engine.store, execution_workspace_root=execution_root)
        self.skills = SkillRegistry(bundled_root=ROOT / "skills", managed_root=self.root / "managed-skills",
                                    execution_workspace_root=execution_root)
        self.executor = TrustedLocalExecutor(workspace_root=execution_root, run_log_dir=self.root / "runlogs",
            mounts={**self.skills.mount_paths(), "akane-sdk": ROOT / "examples/plugins"})
        self.artifacts = ManagedPluginArtifactStore(self.root / "artifacts", instance_id="development-acceptance", project_root=ROOT)
        self.selections = PluginSelectionStore(self.root / "selections.json",defaults=(),instance_id="development-acceptance")
        self.runtime = PluginGenerationRuntime((),candidate_builder=PluginGenerationCandidateBuilder(
            source_resolver=self.artifacts, project_root=ROOT, work_root=self.root / "workers",
            plugin_storage_data_root=self.root / "plugin-data", plugin_storage_instance_id="development-acceptance"))
        self.service = ExtensionManagementService(plugin_runtime=self.runtime,selection_store=self.selections,artifact_store=self.artifacts)
        self.engine.plugin_capability_source = PluginCapabilityToolBridge(self.runtime,config_base_dir=self.root)
        self.engine.plugin_capability_source.bind_approval_store(self.approvals)
        self.engine.project_workspace_service = self.workspace
        self.engine.execution_provider = self.executor
        self.engine.skill_registry = self.skills
        builtins = build_builtin_tool_handlers(store=self.engine.store,sticker_assets=None,capability_offer_source=None,
            capability_config_base_dir=self.root,memory_timeline_service=None,context_libraries=None,
            attachment_service=None,image_material_resolver=None,workspace_file_service=None,attachment_ingest_service=None,
            generated_file_service=None,retrieve_fn=None,skill_registry=self.skills,execution_provider=self.executor,
            approval_store=self.approvals,project_workspace_service=self.workspace,extension_management_service=self.service)
        self.engine.tool_handlers = {key:value for key,value in builtins.items() if key in DEV_TOOLS}
        saved = save_preferences(base_dir=self.root,profile_user_id=self.scope["profile_user_id"],
            payload={"revision":0,"defaultMode":"on_demand","toolModes":{name:"resident" for name in DEV_TOOLS}})
        self.assertTrue(saved["ok"],saved)
        self.trace = []
        self.recorded_ids = set()
        self.approved_requests = []
        self.live = False
        self.model_responses = []
        await self.runtime.start()

    async def stop_development(self):
        await self.runtime.stop()

    def configure_live(self, settings):
        self.live = True
        self.engine.llm = LLMRuntime(settings=settings,log_dir=self.root / "llm",instance_id="development-acceptance")
        self.protocol = self.engine.llm.chat_provider_protocol()
        if self.protocol == "anthropic":
            original_post = requests.post
            def record_post(url, **kwargs):
                self.requests.append(deepcopy(kwargs["json"]))
                return original_post(url, **kwargs)
            self.stack.enter_context(patch("services.llm_client.requests.post", side_effect=record_post))
        else:
            def record_request(request):
                self.requests.append(json.loads(request.content))
            self.engine.llm.chat.client._client.event_hooks.setdefault("request",[]).append(record_request)

    def live_request(self):
        prepared = response_builder.prepare_context(self.engine,**self.scope,
            user_message=self.text,current_user_source_id=self.source,recent_raw=[],recent_episodic_summaries=[],
            recent_semantic_summaries=[],confirmed_snippets=[],now_ts=100,client_context=self.client_context,
            enable_native_tools=True,request_projection_state=self.state)
        self.assertNotIn("memcore_projection_failure",prepared,prepared)
        observer = self.engine._build_memcore_request_observer(generation_context=prepared,request_projection_state=self.state,**self.scope)
        kwargs = self.engine._build_final_response_request_kwargs(generation_context=prepared,
            request_projection_state=self.state,user_images=None,chat_model_override="",execution_target=None,
            prompt_cache_key="",retry_note="",request_observer=observer,allow_tool_call=True)
        result = self.engine.llm.call_chat_json_result(**kwargs)
        self.assertFalse(result.error, result.error)
        self.last_prepared,self.last_result = prepared,result
        self.model_responses.append({"parsed":result.parsed,"raw_text":result.raw_text})
        return prepared,result

    def execute_model_response(self):
        output = {**self.last_result.parsed,TOOL_CAPABILITY_SELECTION_FIELD:self.last_prepared[TOOL_CAPABILITY_SELECTION_FIELD]}
        _,calls,rejections = self.engine._prepare_tool_round_decisions(final_output=output,user_message=self.text,
            client_context=self.client_context,profile_user_id=self.scope["profile_user_id"],session_id=self.scope["session_id"])
        self.assertFalse(rejections,rejections)
        outcomes,items = [],[]
        for call in calls:
            invocation = normalize_tool_invocation(self.engine,call,capability_selection=call.get(TOOL_CAPABILITY_SELECTION_FIELD),
                profile_user_id=self.scope["profile_user_id"],session_id=self.scope["session_id"])
            result,envelope = execute_tool_invocation(self.engine,invocation=invocation,**self.scope,
                visual_payload={},now_ts=100,client_context=self.client_context)
            self.trace.append({"index":len(self.trace)+1,"tool":call["type"],
                "model_name":call.get(TOOL_MODEL_NAME_FIELD,call["type"]),
                "arguments":self.engine._tool_call_model_arguments(call),
                "result":result.followup_context,"events":result.stream_events,
                "has_value":bool(getattr(result.capability_result,"has_value",False)),
                "value":getattr(result.capability_result,"value",None),
                "generation":self.last_prepared["tool_exposure_lifecycle"]["compaction_generation"]})
            outcomes.append(result)
            items.append((call,result,result.followup_context))
        if items:
            sources,failure = self.engine._record_memcore_tool_batch(items=items,**self.scope,now_ts=100,
                current_user_source_id=self.source,memcore_turn_id=self.turn,recorded_tool_call_ids=self.recorded_ids)
            self.assertIsNone(failure,failure)
            self.assertTrue(sources)
        return outcomes

    def approve_pending_test_requests(self):
        """Act as the isolated test owner, through real exact approval records.

        This driver authorizes only management of the fresh acceptance project,
        and simple shell checks inside that project. No user instance is bound.
        The model receives the original approval-required result and must retry.
        """
        pending = self.approvals.list_requests(profile_user_id=self.scope["profile_user_id"],include_resolved=False)
        count = 0
        for item in pending.get("approvalRequests",[]):
            self.assertEqual(item["capabilityId"] in {"manage_extension","exec_run"},True,item)
            # Public approval previews intentionally replace arrays with shape
            # summaries. Review the exact generated tool args in this driver;
            # the real store still binds approval to its normalized fingerprint.
            payload = next(row["arguments"] for row in reversed(self.trace)
                if row["tool"] == item["capabilityId"] and
                (item["capabilityId"] != "manage_extension" or
                 "manage_extension."+row["arguments"].get("action","") == item["actionId"]))
            path = payload.get("path") or payload.get("cwd")
            if path and Path(path).is_absolute():
                Path(path).resolve().relative_to(self.root)
            if item["capabilityId"] == "exec_run":
                command = payload.get("command","").strip()
                directory_create = re.fullmatch(r"(?:New-Item -ItemType Directory -Path [\w/\\-]+ -Force(?: \| Out-Null)?|mkdir(?: -p)? [\w/\\-]+)",command)
                self.assertTrue(command in {"Get-Location","(Get-Location).Path","pwd"} or directory_create,
                    "The acceptance owner approves cwd checks and relative project directories; source checks use test_source.")
            if item["capabilityId"] == "manage_extension":
                self.assertIn(payload.get("action"),{"test_source","stage_source","install","list","discard_stage"})
                if payload.get("action") == "install":
                    # Pure calculation plugin only; no network or credential ports.
                    self.assertLessEqual(set(payload.get("approved_permissions",[])),{"capability.prompt.invoke"})
            decided = self.approvals.decide_request(profile_user_id=self.scope["profile_user_id"],
                request_id=item["requestId"],payload={"decision":"approved"})
            self.assertTrue(decided["ok"],decided)
            self.approved_requests.append({"capability":item["capabilityId"],"action":item.get("actionId")})
            count += 1
        return count

    async def act(self,name,arguments):
        """Controlled provider response, expressly not evidence of model autonomy."""
        context,payload = await asyncio.to_thread(self.request,action=(name,arguments))
        outcomes = await asyncio.to_thread(self.execute_model_response)
        self.assertEqual(len(outcomes),1)
        if self.approve_pending_test_requests():
            self.approval_notice()
            context,payload = await asyncio.to_thread(self.request,action=(name,arguments))
            outcomes = await asyncio.to_thread(self.execute_model_response)
        return context,payload,outcomes[0]

    def select_resident(self,capability_id):
        prefs = read_preferences(base_dir=self.root,profile_user_id=self.scope["profile_user_id"])
        result = save_exposure_state(self.engine,base_dir=self.root,**self.scope,
            payload={"revision":prefs["revision"],"toolModes":{**prefs["toolModes"],capability_id:"resident"}})
        self.assertTrue(result["ok"],result)
        return result

    def write_trace(self, path):
        """No credentials/settings; only generated project actions and results."""
        Path(path).write_text(json.dumps({"kind":"real_model" if self.live else "controlled_integration",
            "model":self.engine.llm.chat.model,"protocol":self.protocol,"trace":self.trace,
            "model_responses":self.model_responses,"exact_approvals":self.approved_requests},ensure_ascii=False,indent=2),encoding="utf-8")

    def approval_notice(self):
        index = len(self.approved_requests)
        recorded = self.manager.append_turn_user_input({"source_id":f"acceptance-owner-approval-{index}",
            "content":"本次临时插件验收刚才产生的精确审批已批准，请继续原任务并重试对应操作。",
            "timestamp":102+index},turn_id=self.turn,**self.scope)
        self.assertTrue(recorded["ok"],recorded)

    def finish_model(self):
        raw = self.last_result.raw_text
        content = [{"type":"text","text":raw}] if self.protocol == "anthropic" else raw
        finished = self.manager.complete_input_turn(turn_id=self.turn,
            assistant_record={"source_id":self.source+"-final","content":self.last_result.parsed.get("speech") or raw,"timestamp":200},
            memory_metadata={},provider_output_raw=raw,provider_profile=self.protocol,
            provider_projection={"role":"assistant","content":content},**self.scope)
        self.assertTrue(finished["ok"],finished)
