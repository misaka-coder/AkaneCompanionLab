"""Real WebSocket/broker framing and immediate on-demand invocation, no desktop input."""
import json
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from fastapi.testclient import TestClient
from companion_v01.computer_use.contracts import COMPUTER_USE_TOOL_SPEC
from companion_v01.computer_use.session import scope_key
from companion_v01.executor_broker import ExecutorBroker
from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.client_protocol import ClientProtocolContext, ClientMode
from companion_v01.desktop_satellite import DesktopSatelliteService
from companion_v01.tool_handlers.computer_use import ComputerUseToolHandler
from companion_v01.engine_services.tool_rounds import resolve_capability_selection
from companion_v01.capability_exposure import route_invocation
from companion_v01.tool_orchestration_engine import normalize_tool_invocation, execute_tool_invocation
from companion_v01.personal_browser_contract import PERSONAL_BROWSER_SPEC
from companion_v01.personal_browser import PersonalBrowserBackend
from companion_v01.tool_handlers.core import ToolExecutionContext
import tests.test_desktop_satellite_local_capabilities as fixtures
import tests.test_computer_use as images


class ComputerUseTransportTests(unittest.TestCase):
    def test_full_access_owner_policy_crosses_real_wire_for_send_and_resume(self):
        from companion_v01.computer_use.dispatch import dispatch
        from companion_v01.computer_use.session import dispatch_scope
        from companion_v01.local_capability_config import save_capability_approval_mode
        from companion_v01.tool_invocation import ToolInvocation
        from unittest.mock import Mock
        service = DesktopSatelliteService(instance_id="device", token="fixture")
        broker = ExecutorBroker(service)
        with tempfile.TemporaryDirectory() as directory, TestClient(fixtures.DesktopSatelliteLocalCapabilitiesTests._app(service)) as client:
            root = Path(directory)
            save_capability_approval_mode(base_dir=root, profile_user_id="master", capability_id="ops", mode="trusted_auto_allow")
            store = Mock()
            engine = SimpleNamespace(capability_config_base_dir=root, approval_store=store)
            with client.websocket_connect("/capabilities/satellite/ws", headers={"Authorization":"Bearer fixture"}) as ws:
                ws.receive_json(); ws.send_json(fixtures._registration("device")); registered = ws.receive_json()
                for action in ("press_key", "resume_steps", "run_actions", "manage_window", "type_text"):
                    arguments = dict(action=action, control_session_id="session", device_epoch=registered["lease_epoch"], observation_id="observation")
                    arguments.update({"key":"Enter"} if action=="press_key" else {"workflow_id":"checkpoint"})
                    if action=="run_actions":
                        arguments.pop("workflow_id")
                        arguments.update(screenshot_id="shot", actions=[dict(action="click",x=20,y=30),dict(action="type_text",text="fixture"),dict(action="press_key",key="Enter")])
                    if action=="manage_window":
                        arguments=dict(action=action, device_epoch=registered["lease_epoch"], window_id="window", window_action="minimize")
                    if action=="type_text":
                        arguments=dict(action=action, context_ref="ctx-exact-observation", text="fixture")
                    invocation = ToolInvocation(id="full-"+action, name="computer_use", arguments=arguments,
                        execution_receipt=service.resolve_receipt(COMPUTER_USE_TOOL_SPEC).as_dict())
                    results = {}
                    def run():
                        token = dispatch_scope.set(scope_key("qq_group_shared_fixture", "s"))
                        try:
                            results["value"] = dispatch(engine, broker=broker, spec=COMPUTER_USE_TOOL_SPEC, invocation=invocation,
                                profile_user_id="qq_group_shared_fixture", session_id="s", client_context=None,
                                request_context={"actor_profile_user_id":"master", "_model_execution_target":SimpleNamespace(role="vision",reason="")})
                        finally:
                            dispatch_scope.reset(token)
                    worker = threading.Thread(target=run, daemon=True); worker.start()
                    prepare = ws.receive_json()
                    self.assertEqual(prepare["arguments"], arguments)
                    self.assertEqual(prepare["control_dispatch"], {"phase":"prepare", "permission_mode":"trusted_auto_allow"})
                    self.assertNotIn("permission_mode", prepare["arguments"])
                    ws.send_json(fixtures._execution_message("result", registered, prepare["invocation_id"], "computer_use", status="succeeded",
                        data={"ok":True, "authorization":{"required":True, "binding":"a"*64, "preview":{"action":action}}}))
                    execution = ws.receive_json()
                    self.assertEqual(execution["arguments"], arguments)
                    self.assertEqual(execution["control_dispatch"], {"phase":"execute", "permission_mode":"trusted_auto_allow", "approval_binding":"a"*64})
                    ws.send_json(fixtures._execution_message("result", registered, execution["invocation_id"], "computer_use", status="succeeded",
                        data={"ok":True, "action_state":"executed", "workflow_state":"completed",
                              "workflow_kind":"visual_actions" if action=="run_actions" else "verified_steps", "task_verified":False,
                              "steps":[{"index":0,"action_state":"executed"}],"window_state":{"minimized":action=="manage_window"}}))
                    worker.join(5); self.assertFalse(worker.is_alive())
                    self.assertIsNone(results["value"][1])
                    self.assertEqual(results["value"][0].data["permission_mode"], "trusted_auto_allow")
                    self.assertFalse(results["value"][0].data["task_verified"])
                    self.assertEqual(results["value"][0].data["window_state"]["minimized"], action=="manage_window")
                self.assertEqual(store.mock_calls, [])

    def test_workflow_plan_and_checkpoint_cross_real_satellite_frames(self):
        from companion_v01.computer_use.dispatch import dispatch
        from companion_v01.computer_use.session import dispatch_scope
        from companion_v01.tool_invocation import ToolInvocation
        from tests.test_computer_use_workflow import ComputerUseWorkflowTests
        service=DesktopSatelliteService(instance_id="device",token="fixture")
        broker=ExecutorBroker(service)
        with TestClient(fixtures.DesktopSatelliteLocalCapabilitiesTests._app(service)) as client:
            with client.websocket_connect("/capabilities/satellite/ws",headers={"Authorization":"Bearer fixture"}) as ws:
                ws.receive_json();ws.send_json(fixtures._registration("device"));registered=ws.receive_json()
                args=ComputerUseWorkflowTests.plan();args["device_epoch"]=registered["lease_epoch"]
                invocation=ToolInvocation(id="workflow-once",name="computer_use",arguments=args,
                    execution_receipt=service.resolve_receipt(COMPUTER_USE_TOOL_SPEC).as_dict())
                results={}
                def run():
                    token=dispatch_scope.set(scope_key("owner","s"))
                    try:
                        results["value"]=dispatch(SimpleNamespace(),broker=broker,spec=COMPUTER_USE_TOOL_SPEC,invocation=invocation,
                            profile_user_id="owner",session_id="s",client_context=None,request_context={})
                    finally: dispatch_scope.reset(token)
                worker=threading.Thread(target=run,daemon=True);worker.start()
                prepare=ws.receive_json();self.assertEqual(prepare["arguments"]["steps"],args["steps"])
                self.assertEqual(prepare["control_dispatch"],{"phase":"prepare","permission_mode":"ask_each_time"})
                ws.send_json(fixtures._execution_message("result",registered,prepare["invocation_id"],"computer_use",status="succeeded",
                    data={"ok":True,"authorization":{"required":False,"binding":"a"*64,"preview":{}}}))
                execute=ws.receive_json();self.assertEqual(execute["control_dispatch"]["approval_binding"],"a"*64)
                self.assertEqual(execute["control_scope"],scope_key("owner","s"))
                ws.send_json(fixtures._execution_message("result",registered,execute["invocation_id"],"computer_use",status="succeeded",
                    data={"ok":True,"workflow_id":"native-flow","workflow_state":"paused","action_state":"executed","observation_state":"complete",
                          "next_step":0,"completed_steps":0,"steps":[{"state":"executed","input_attempted":True}],"reason":"postcondition_not_met"}))
                worker.join(5);self.assertFalse(worker.is_alive())
                result,stopped=results["value"];self.assertIsNone(stopped)
                self.assertEqual(result.data["workflow_id"],"native-flow")
                self.assertEqual(result.data["steps"][0]["state"],"executed")

    def test_load_and_invoke_immediately_over_live_satellite(self):
        with tempfile.TemporaryDirectory() as directory:
            service=DesktopSatelliteService(instance_id="device",token="fixture")
            engine=SimpleNamespace(tool_handlers={"computer_use":ComputerUseToolHandler(offer_source=service)},
                store=SimpleNamespace(list_attachment_inbox_items=lambda **_:[],list_generated_files=lambda **_:[]),
                capability_config_base_dir=Path(directory), capability_offer_source=service, executor_broker=ExecutorBroker(service), capability_registry=CapabilityRegistry(offer_source=service))
            engine._resolve_tool_handlers=lambda capability_selection=None,**_:dict(capability_selection.resolved_handlers)
            with TestClient(fixtures.DesktopSatelliteLocalCapabilitiesTests._app(service)) as client:
                with client.websocket_connect("/capabilities/satellite/ws",headers={"Authorization":"Bearer fixture"}) as ws:
                    ws.receive_json();ws.send_json(fixtures._registration("device"));registered=ws.receive_json()
                    client_context=ClientProtocolContext(effective_mode=ClientMode.DESKTOP_PET)
                    selection=resolve_capability_selection(engine,profile_user_id="owner",session_id="s",client_context=client_context)
                    self.assertNotIn("computer_use",selection.schema_tool_names)
                    load=normalize_tool_invocation(engine,{"type":"capability_load","capability_ids":["computer_use"]},profile_user_id="owner",session_id="s",capability_selection=selection)
                    result,_=execute_tool_invocation(engine,invocation=load,profile_user_id="owner",session_id="s",visual_payload={},now_ts=1)
                    reference=json.loads(result.followup_context.split("\n",1)[1])["capabilities"][0]["contract_ref"]
                    # Reject through the real loaded route before touching the
                    # live broker, then prove a corrected call can still run.
                    from unittest.mock import patch
                    from companion_v01.tool_invocation import TOOL_SOURCE_FIELD, NATIVE_OPENAI
                    bad = {"action":"run_actions", "control_session_id":"test-session", "device_epoch":registered["lease_epoch"],
                           "observation_id":"test-observation", "screenshot_id":"test-shot",
                           "actions":[{"action":"click","x":1,"y":2},{"action":"type_text","text":"synthetic\ntext"}]}
                    with patch.object(engine.executor_broker, "execute", side_effect=AssertionError("bad args reached broker")):
                        for carrier in ("computer_use", "capability_invoke"):
                            raw = ({"type":carrier, **bad} if carrier == "computer_use" else
                                   {"type":carrier,"capability_id":"computer_use","contract_ref":reference,"arguments":bad})
                            raw[TOOL_SOURCE_FIELD] = NATIVE_OPENAI
                            if carrier == "capability_invoke":
                                raw, selected = route_invocation(raw, selection)
                            else:
                                # Direct native calls need the same disclosed
                                # contract binding, not an unloaded placeholder.
                                _, selected = route_invocation({"type":"capability_invoke", "capability_id":"computer_use",
                                    "contract_ref":reference,"arguments":bad}, selection)
                            invalid = normalize_tool_invocation(engine, raw, capability_selection=selected)
                            rejected, error = execute_tool_invocation(engine, invocation=invalid,
                                profile_user_id="owner",session_id="s",visual_payload={},now_ts=1)
                            self.assertEqual(error.data["code"], "bad_args")
                            self.assertEqual(error.data["validation"]["error"]["path"], "actions[1].text")
                            self.assertIn("unsupported_text_character_LF", rejected.followup_context)
                    call,bound=route_invocation({"type":"capability_invoke","capability_id":"computer_use","contract_ref":reference,"arguments":{"action":"list_windows"}},selection)
                    invocation=normalize_tool_invocation(engine,call,profile_user_id="owner",session_id="s",capability_selection=bound)
                    results={}
                    worker=threading.Thread(target=lambda:results.update(value=execute_tool_invocation(engine,invocation=invocation,profile_user_id="owner",session_id="s",visual_payload={},now_ts=1)),daemon=True)
                    worker.start();worker.join(0.1);self.assertTrue(worker.is_alive(),str(results));frame=ws.receive_json()
                    self.assertEqual(frame["control_scope"],scope_key("owner","s"))
                    self.assertEqual(frame["arguments"],{"action":"list_windows"})
                    data=images.ComputerUseTests.image_data()
                    data["context_ref"]="ctx-exact-observation"
                    data["ax_references"]={"referenceable":1,"read_only":2}
                    data["coordinate_bounds"]={"space":"screenshot_pixels","width":200,"height":100}
                    ws.send_json(fixtures._execution_message("result",registered,frame["invocation_id"],"computer_use",status="succeeded",data=data))
                    worker.join(5);self.assertFalse(worker.is_alive())
                    result,envelope=results["value"]
                    self.assertEqual(len(result.model_image_inputs),1)
                    self.assertNotIn("imageBase64",result.followup_context)
                    self.assertIn(data["context_ref"], result.followup_context)
                    self.assertEqual(envelope.data["result"]["context_ref"],data["context_ref"])
                    self.assertEqual(envelope.data["result"]["ax_references"],data["ax_references"])
                    self.assertEqual(envelope.data["result"]["coordinate_bounds"],data["coordinate_bounds"])
                    retained=engine.executor_broker.execute(spec=COMPUTER_USE_TOOL_SPEC,receipt_value=service.resolve_receipt(COMPUTER_USE_TOOL_SPEC).as_dict(),
                        invocation_id=invocation.id,arguments=invocation.arguments,ledger_scope="owner\x1fs")
                    self.assertNotIn("imageBase64",json.dumps(retained.data))
                    self.assertEqual(retained.data["action_state"],"not_started")

    def test_private_chrome_transport_preserves_preflight_and_binds_device_scope(self):
        service=DesktopSatelliteService(instance_id="device",token="fixture")
        broker=ExecutorBroker(service);backend=PersonalBrowserBackend(offer_source=service,broker=broker)
        with TestClient(fixtures.DesktopSatelliteLocalCapabilitiesTests._app(service)) as client:
            with client.websocket_connect("/capabilities/satellite/ws",headers={"Authorization":"Bearer fixture"}) as ws:
                ws.receive_json();registration=fixtures._registration("device")
                spec=PERSONAL_BROWSER_SPEC
                registration["offers"].append({"tool_id":spec.capability_id,"spec_version":spec.spec_version,"schema_version":spec.schema_version,"schema_hash":spec.schema_hash})
                ws.send_json(registration);registered=ws.receive_json();results={}
                call={"type":"browser_page","session_source":"personal_chrome","action":"fill","browser_session_id":"b","device_epoch":registered["lease_epoch"],"observation_id":"o","ref":"r","text":"fixture draft"}
                context=ToolExecutionContext(profile_user_id="owner",session_id="s",now_ts=1,visual_payload={},invocation_id="fill-once")
                worker=threading.Thread(target=lambda:results.update(value=backend.execute(call=call,context=context)),daemon=True);worker.start()
                prepare=ws.receive_json();self.assertEqual(prepare["control_dispatch"],{"phase":"prepare","permission_mode":"ask_each_time"})
                self.assertEqual(prepare["control_scope"],scope_key("owner","s"))
                ws.send_json(fixtures._execution_message("result",registered,prepare["invocation_id"],spec.capability_id,status="succeeded",
                    data={"ok":True,"authorization":{"required":False,"binding":"a"*64,"preview":{"action":"fill"}}}))
                execute=ws.receive_json();self.assertEqual(execute["control_dispatch"],{"phase":"execute","approval_binding":"a"*64,"permission_mode":"ask_each_time"})
                self.assertNotEqual(prepare["invocation_id"],execute["invocation_id"])
                ws.send_json(fixtures._execution_message("result",registered,execute["invocation_id"],spec.capability_id,status="failed",
                    data={"ok":False,"action_state":"executed","observation_state":"failed","reason":"capture_failed"}))
                worker.join(5);self.assertFalse(worker.is_alive())
                self.assertEqual(json.loads(results["value"].followup_context)["action_state"],"executed")

if __name__=="__main__":unittest.main()
