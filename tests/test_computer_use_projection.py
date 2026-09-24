"""Intercept actual provider serialization over three steps with real MemCore."""
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.capability_discovery import build_capability_discovery_handlers
from companion_v01.tool_handlers.computer_use import ComputerUseToolHandler
from companion_v01.tool_handlers.core import ToolExecutionContext,ToolExecutionResult
from companion_v01.tool_invocation import NATIVE_OPENAI,TOOL_SOURCE_FIELD
from companion_v01.computer_use.media import extract_images
from companion_v01.computer_use.contracts import GUIDE_VERSION
from companion_v01.computer_use.presentation import feedback
import tests.test_computer_use as image_fixture
from tests.test_memcore_integration import _FakeLLM,_FakeEmbeddingProvider


class ComputerUseProjectionTests(unittest.TestCase):
    def test_three_serialized_requests_have_latest_pixels_and_one_visible_guide(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=MemcoreManager(backend="memcore",storage_path=Path(directory)/"memory.db",visible_scope="conversation",
                enable_flavor=True,shadow_compare=False,llm=_FakeLLM(),embedding_provider=_FakeEmbeddingProvider())
            try:
                identity=dict(profile_user_id="owner",session_id="s",character_pack_id="char")
                opened=manager.begin_input_turn({"source_id":"user-start","content":"操作测试窗口","timestamp":100},**identity)
                engine=AkaneMemoryEngine.__new__(AkaneMemoryEngine);engine.memcore_manager=manager
                engine.llm=SimpleNamespace(supports_request_observer=True)
                runtime=LLMRuntime.__new__(LLMRuntime);bundle=SimpleNamespace(client=SimpleNamespace(_akane_protocol="responses",protocol="responses"),model="test-no-network")
                history=[];state={};wire=[];measurements=[]
                handlers,_=build_capability_discovery_handlers({"computer_use":ComputerUseToolHandler()},profile_user_id="owner",session_id="s")
                context=ToolExecutionContext(profile_user_id="owner",session_id="s",now_ts=100,visual_payload={})

                def append(name,result,index):
                    batch=manager.record_tool_batch(exchanges=[{"tool_name":name,"tool_call_id":f"call-{index}","tool_input":{"action":"observe"} if name=="computer_use" else {"capability_ids":["computer_use"]},
                        "result":result.followup_context,"source":name,"timestamp":101+index,"source_id_prefix":f"step-{index}","result_status":"success"}],turn_id=opened["turn_id"],**identity)
                    trace=[source for entry in batch["exchanges"] for source in (entry["tool_use_source_id"],entry["tool_result_source_id"])]
                    media=engine._record_memcore_tool_media_input(model_image_inputs=result.model_image_inputs,related_source_ids=trace,now_ts=101+index,memcore_turn_id=opened["turn_id"],**identity)
                    projection=engine._append_tool_history_batch(tool_history_turns=history,items=[({"type":name,TOOL_SOURCE_FIELD:NATIVE_OPENAI},result,result.followup_context)],
                        trace_source_ids=trace,media_source_ids=media,model_image_inputs=result.model_image_inputs,provider_profile="responses",
                        memcore_turn_id=opened["turn_id"],current_user_source_id=opened["source_id"],**identity)
                    self.assertTrue(projection["ok"],projection)

                guide=handlers["capability_load"].execute(call={"type":"capability_load","capability_ids":["computer_use"]},context=context)
                append("capability_load",guide,0)
                user={"role":"user","content":"操作测试窗口"}
                for step in range(1,4):
                    start=time.monotonic();data=image_fixture.ComputerUseTests.image_data()
                    data["observation_id"]=f"obs-{step}";data["screenshots"][0]["screenshot_id"]=f"shot-{step}"
                    data["context_ref"]=f"ctx-{step}"
                    data.update(action_state="unknown",observation_state="partial",reason="synthetic_capture_incomplete",text_complete=False)
                    data["elements"]=[dict(element_id=f"el-{step}-{i}",name="Synthetic fixture",role=50004,
                                           enabled=False,focused=False,stable_identity=True,value=None) for i in range(12)]
                    images=extract_images(data)
                    # Different bytes let the test detect accidental reuse of a prior frame.
                    images[0]["data_url"]=f"data:image/png;base64,PIXELS_{step}"
                    result=ToolExecutionResult(tool_type="computer_use",followup_context=feedback(data),model_image_inputs=images)
                    append("computer_use",result,step)
                    projection=manager.build_context_projection(provider_profile="responses",**identity)
                    generation={"memcore_projection_read":{"current_turn_id":opened["turn_id"],"current_turn_messages":[m for m in projection["messages"] if m.get("turn_id")==opened["turn_id"]]}}
                    observer=engine._build_memcore_request_observer(generation_context=generation,request_projection_state=state,**identity)
                    def intercept(request):
                        wire.append(request["audit_history_messages"])
                        return observer(request)
                    runtime._observe_completion_request(bundle=bundle,payload={"model":"test-no-network","messages":[{"role":"system","content":"stable"},user,*history],"tools":[]},
                        observer=intercept,persistent_turn_messages=[user,*history])
                    serialized=json.dumps(wire[-1],ensure_ascii=False)
                    self.assertEqual(serialized.count("data:image/png;base64,"),1)
                    self.assertIn(f"PIXELS_{step}",serialized)
                    for fact in (f"ctx-{step}",f"shot-{step}","columns-v1","synthetic_capture_incomplete","unknown","partial"):
                        self.assertIn(fact,serialized)
                    self.assertIsInstance(data["elements"],list)
                    self.assertEqual(serialized.count(f"computer_use guide v{GUIDE_VERSION}"),1)
                    reloaded=handlers["capability_load"].execute(call={"type":"capability_load","capability_ids":["computer_use"]},
                        context=SimpleNamespace(request_context={"_host_visible_tool_history":state["visible_tool_history"]}))
                    self.assertIn('"already_visible":true',reloaded.followup_context)
                    measurements.append({"step":step,"images":1,"characters":len(serialized),"elapsed_ms":round((time.monotonic()-start)*1000),"guides":1})
                durable=manager.build_context_projection(provider_profile="responses",**identity)
                self.assertNotIn("PIXELS_",json.dumps(durable))
                print("computer_use projection: "+json.dumps(measurements))
            finally:
                manager.close()

if __name__=="__main__":unittest.main()
