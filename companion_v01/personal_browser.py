"""browser_page's device backend; no Chrome process is started on the host."""
import json
from types import SimpleNamespace
from .personal_browser_contract import PERSONAL_BROWSER_SPEC, EFFECT_ACTIONS, normalize_personal_call
from .tool_handlers.core import ToolExecutionResult, ToolFollowupEnvelope
from .executor_broker import ExecutorBroker
from .tool_invocation import ToolInvocation
from .computer_use.session import dispatch_scope, scope_key
from .computer_use.media import extract_images


class PersonalBrowserBackend:
    def __init__(self, *, offer_source=None, broker=None, approval_store=None, config_base_dir=None):
        self.offer_source = offer_source
        self.broker = broker or ExecutorBroker(offer_source)
        self.approvals = SimpleNamespace(approval_store=approval_store, capability_config_base_dir=config_base_dir)

    def available(self):
        try:
            return self.offer_source is not None and self.offer_source.resolve_receipt(PERSONAL_BROWSER_SPEC) is not None
        except Exception:
            return False

    def execute(self, *, call, context):
        from .tool_orchestration_engine import _satellite_channel_rejection
        from .computer_use.dispatch import dispatch
        from .capability_registry import BROWSER_PAGE_TOOL_SPEC
        normalized = normalize_personal_call(call)
        if normalized is None:
            return self._result({"ok":False, "reason":"personal_browser_arguments_invalid"})
        arguments = {k:v for k,v in normalized.items() if k not in {"type", "session_source"}}
        invocation = ToolInvocation(name="browser_page", arguments=arguments, id=context.invocation_id)
        blocked = _satellite_channel_rejection(BROWSER_PAGE_TOOL_SPEC, invocation, context.request_context)
        if blocked:
            return blocked[0]
        receipt = self.offer_source.resolve_receipt(PERSONAL_BROWSER_SPEC) if self.offer_source is not None else None
        if receipt is None:
            return self._result({"ok":False,"reason":"personal_chrome_device_offline"})
        invocation.execution_receipt = receipt.as_dict()
        token = dispatch_scope.set(scope_key(context.profile_user_id, context.session_id))
        try:
            result, stopped = dispatch(self.approvals, broker=self.broker, spec=PERSONAL_BROWSER_SPEC,
                invocation=invocation, profile_user_id=context.profile_user_id, session_id=context.session_id,
                client_context=SimpleNamespace(effective_mode=SimpleNamespace(value=context.client_mode)),
                request_context=context.request_context, effect_actions=EFFECT_ACTIONS, approval_spec=BROWSER_PAGE_TOOL_SPEC)
        finally:
            dispatch_scope.reset(token)
        if stopped:
            stopped[0].tool_type = "browser_page"
            return stopped[0]
        data = dict(result.data or {})
        data.setdefault("ok", result.status == "succeeded")
        data.setdefault("reason", result.reason)
        data.setdefault("action_state", "unknown" if result.status == "execution_unknown" else "not_started")
        data.setdefault("observation_state", "complete" if data["ok"] else "failed")
        return self._result(data)

    @staticmethod
    def _result(data):
        data = dict(data)
        data["backend"] = "personal_chrome"
        images = []
        try:
            images = extract_images(data)
        except (ValueError, OSError):
            data["observation_state"] = "partial"
            data["visual_reason"] = "invalid_screenshot"
        data.setdefault("action_state", "not_started")
        data.setdefault("observation_state", "failed")
        data.pop("authorization", None)
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return ToolExecutionResult(tool_type="browser_page", followup_context=text,
            followup_envelope=ToolFollowupEnvelope(content=text, producer_bounded=True, complete=bool(data.get("complete",True))),
            model_image_inputs=images,
            stream_events=[{"type":"browser_page_read","provider":"personal_chrome","status":"ready" if data.get("ok") else "failed",
                            "reason":data.get("reason",""),"action_state":data["action_state"],"observation_state":data["observation_state"]}],
            state_updates={"browser_page_status":"ready" if data.get("ok") else "failed","browser_page_source":"personal_chrome"})
