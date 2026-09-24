import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.personal_browser import PersonalBrowserBackend
from companion_v01.personal_browser_contract import PERSONAL_BROWSER_SPEC, normalize_personal_call
from companion_v01.computer_use.session import dispatch_control, dispatch_scope, scope_key
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.web_browser import BrowserPageToolHandler
from companion_v01.desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS, desktop_satellite_spec
from tests.test_computer_use import ComputerUseTests


class PersonalBrowserTests(unittest.TestCase):
    def context(self, **kwargs):
        return ToolExecutionContext(profile_user_id="owner",session_id="s",now_ts=1,visual_payload={},invocation_id="real-call",**kwargs)

    def test_private_transport_and_rust_contract_match(self):
        self.assertNotIn(PERSONAL_BROWSER_SPEC, DESKTOP_SATELLITE_TOOL_SPECS)
        self.assertIs(desktop_satellite_spec("browser_page_personal"), PERSONAL_BROWSER_SPEC)
        source=(Path(__file__).parents[1]/"desktop_pet_next/src-tauri/src/personal_browser/mod.rs").read_text(encoding="utf-8")
        self.assertIn(PERSONAL_BROWSER_SPEC.schema_hash,source)

    def test_facade_keeps_managed_default_and_rejects_cross_backend_targets(self):
        handler=BrowserPageToolHandler(browser_runner=SimpleNamespace())
        self.assertEqual(handler.normalize_call({"type":"browser_page","action":"current"})["action"],"current")
        self.assertEqual(handler.normalize_call({"type":"browser_page","session_source":"personal_chrome","action":"connect"})["session_source"],"personal_chrome")
        self.assertIsNone(handler.normalize_call({"type":"browser_page","session_source":"personal_chrome","action":"click","selector":"#send"}))
        base={"type":"browser_page","session_source":"personal_chrome","action":"fill","browser_session_id":"b","device_epoch":"e","observation_id":"o","ref":"1_1","text":"draft"}
        self.assertIsNotNone(normalize_personal_call(base))
        for field in ({"approved":True},{"text":"draft\n"},{"coordinate":[1,2]},{"_control_scope":"forged"}):
            self.assertIsNone(normalize_personal_call({**base,**field}))

    def test_owner_check_precedes_device_reads(self):
        backend=PersonalBrowserBackend(offer_source=SimpleNamespace(resolve_receipt=lambda _:self.fail("must not read device")))
        with patch("config.MASTER_QQ","12345"):
            result=backend.execute(call={"type":"browser_page","session_source":"personal_chrome","action":"connect"},
                context=self.context(request_context={"qq_delivery_context":{"user_id":"67890","is_group":True}}))
        self.assertIn("device_action_requires_owner",json.dumps(result.state_updates))

    def test_native_dispatch_scope_and_ephemeral_media(self):
        seen=[]
        def execute(**kwargs):
            seen.append((kwargs,dispatch_scope.get(),dispatch_control.get()))
            return SimpleNamespace(status="succeeded",reason="",data=ComputerUseTests.image_data())
        source=SimpleNamespace(resolve_receipt=lambda _:SimpleNamespace(as_dict=lambda:{"instance_id":"device","lease_epoch":"epoch"}))
        backend=PersonalBrowserBackend(offer_source=source,broker=SimpleNamespace(execute=execute))
        result=backend.execute(call={"type":"browser_page","session_source":"personal_chrome","action":"connect"},context=self.context())
        self.assertEqual(seen[0][0]["spec"],PERSONAL_BROWSER_SPEC)
        self.assertEqual(seen[0][1],scope_key("owner","s"))
        self.assertEqual(dispatch_scope.get(),"")
        self.assertEqual(len(result.model_image_inputs),1)
        self.assertNotIn("imageBase64",result.followup_context)
        self.assertNotIn("data:image",result.followup_context)

    def test_effect_preflight_and_one_dispatch_keep_executed_on_observation_failure(self):
        phases=[]
        def execute(**kwargs):
            phase=dispatch_control.get();phases.append(phase)
            data={"authorization":{"required":False,"binding":"a"*64}} if phase["phase"]=="prepare" else {"ok":False,"action_state":"executed","observation_state":"failed","reason":"capture_failed"}
            return SimpleNamespace(status="succeeded" if phase["phase"]=="prepare" else "failed",reason="",data=data)
        source=SimpleNamespace(resolve_receipt=lambda _:SimpleNamespace(as_dict=lambda:{"instance_id":"device","lease_epoch":"epoch"}))
        backend=PersonalBrowserBackend(offer_source=source,broker=SimpleNamespace(execute=execute))
        result=backend.execute(call={"type":"browser_page","session_source":"personal_chrome","action":"fill","browser_session_id":"b","device_epoch":"e","observation_id":"o","ref":"1_1","text":"draft"},context=self.context())
        self.assertEqual([p["phase"] for p in phases],["prepare","execute"])
        self.assertEqual(json.loads(result.followup_context)["action_state"],"executed")
        self.assertIsNone(dispatch_control.get())

    def test_full_access_reaches_personal_browser_as_host_metadata(self):
        from companion_v01.local_capability_config import save_capability_approval_mode
        phases=[]
        def execute(**kwargs):
            self.assertNotIn("permission_mode",kwargs["arguments"])
            phases.append(dict(dispatch_control.get()))
            return SimpleNamespace(status="succeeded",reason="",data={"ok":True,
                "authorization":{"required":False,"binding":"b"*64},"action_state":"executed"})
        source=SimpleNamespace(resolve_receipt=lambda _:SimpleNamespace(as_dict=lambda:{"instance_id":"device","lease_epoch":"epoch"}))
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(save_capability_approval_mode(base_dir=Path(root),profile_user_id="owner",capability_id="browser_page",mode="trusted_auto_allow")["ok"])
            backend=PersonalBrowserBackend(offer_source=source,broker=SimpleNamespace(execute=execute),config_base_dir=Path(root))
            result=backend.execute(call={"type":"browser_page","session_source":"personal_chrome","action":"click","browser_session_id":"b","device_epoch":"e","observation_id":"o","ref":"1_1"},context=self.context())
        self.assertEqual([p["permission_mode"] for p in phases],["trusted_auto_allow"]*2)
        self.assertEqual(json.loads(result.followup_context)["action_state"],"executed")

if __name__=="__main__":unittest.main()
