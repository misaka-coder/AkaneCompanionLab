from capcore import CapabilityToolSpec

from .core import BaseToolHandler, ToolMetadata, operation_tool_result


LOCAL_MUSIC_CONTROL_SPEC = CapabilityToolSpec(
    capability_id="local_music_control", display_name="桌宠音乐控制",
    description="控制桌宠本地播放队列：播放、暂停、继续、停止、上一首或下一首。source_id 可选，使用当前媒体或材料中已有的音频编号。工具返回实际执行结果；系统播放器请用 system_media_control。",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["play", "pause", "resume", "stop", "previous", "next"]},
        "source_id": {"type": "string", "maxLength": 160}}, "required": ["action"], "additionalProperties": False},
    output_schema={"type": "object"}, risk="low", confirm="never", effects=("control_local_music",),
    visible_in=("desktop",), idempotency="effectful", max_result_bytes=2048,
)


class LocalMusicControlToolHandler(BaseToolHandler):
    tool_type = "local_music_control"
    policy_accepted_native_tool = True

    def __init__(self, service_provider):
        self.service_provider = service_provider

    def tool_spec(self):
        return LOCAL_MUSIC_CONTROL_SPEC

    def tool_metadata(self):
        return ToolMetadata(family="music", operation="write", risk="low", input_schema=LOCAL_MUSIC_CONTROL_SPEC.input_schema)

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        args = {k: v for k, v in value.items() if not k.startswith("_tool_")}
        if set(args) - {"type", "action", "source_id"} or args.get("action") not in {"play", "pause", "resume", "stop", "previous", "next"}:
            return None
        if "source_id" in args and (not isinstance(args["source_id"], str) or len(args["source_id"]) > 160):
            return None
        return args

    def capability_status(self, **kwargs):
        service = self.service_provider()
        ready = bool(service and service.available())
        return {"enabled": ready, "status": "ready" if ready else "unavailable", "reason": "" if ready else "desktop_ui_unavailable"}

    def execute(self, *, call, context):
        service = self.service_provider()
        result = service.execute(call, context) if service else {"ok": False, "status": "unavailable", "reason": "desktop_ui_unavailable"}
        return operation_tool_result(tool_type=self.tool_type, operation_result=result)
