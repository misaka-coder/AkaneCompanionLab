"""Private device transport for the public browser_page facade."""
from capcore import CapabilityToolSpec

ACTIONS = ("connect", "list_tabs", "select_tab", "disconnect", "handoff", "current", "snapshot",
           "elements", "screenshot", "read_text", "navigate", "scroll", "click", "fill", "press", "status")
EFFECT_ACTIONS = frozenset({"navigate", "scroll", "click", "fill", "press"})
REFERENCE = {"type": "string", "minLength": 1, "maxLength": 160}
SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": list(ACTIONS)},
    "browser_session_id": REFERENCE, "device_epoch": REFERENCE, "tab_id": REFERENCE,
    "observation_id": REFERENCE, "ref": REFERENCE,
    "url": {"type": "string", "maxLength": 1600}, "text": {"type": "string", "maxLength": 500},
    "key": {"type": "string", "enum": ["Enter", "Escape", "Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Home", "End"]},
    "scroll_delta": {"type": "integer", "minimum": -2400, "maximum": 2400},
    "observation_mode": {"type": "string", "enum": ["hybrid", "text", "visual"]},
}, "required": ["action"], "additionalProperties": False}
PERSONAL_BROWSER_SPEC = CapabilityToolSpec(
    capability_id="browser_page_personal", display_name="个人 Chrome 操作", description="Private device transport; use browser_page.",
    input_schema=SCHEMA, output_schema={"type":"object","additionalProperties":True},
    risk="medium", confirm="never", effects=("browser_action",), visible_in=(),
    idempotency="effectful", max_result_bytes=12*1024*1024,
)

def normalize_personal_call(value):
    args = {k:v for k,v in value.items() if k not in {"type", "session_source"} and not k.startswith("_tool_")}
    action = args.get("action")
    if action not in ACTIONS or set(args) - SCHEMA["properties"].keys():
        return None
    for key, val in args.items():
        schema = SCHEMA["properties"][key]
        if schema["type"] == "integer":
            if type(val) is not int or not schema["minimum"] <= val <= schema["maximum"]: return None
        elif not isinstance(val, str) or not schema.get("minLength", 0) <= len(val) <= schema.get("maxLength", 10000): return None
        if "enum" in schema and val not in schema["enum"]: return None
    required = set() if action == "connect" else {"browser_session_id", "device_epoch"}
    if action == "select_tab": required.add("tab_id")
    if action in EFFECT_ACTIONS: required.add("observation_id")
    required |= {"navigate":{"url"}, "click":{"ref"}, "fill":{"ref","text"}, "press":{"key"}}.get(action,set())
    if not required <= args.keys(): return None
    if "text" in args and any(ch in args["text"] for ch in "\r\n\t\x00"): return None
    return {"type":"browser_page", "session_source":"personal_chrome", **args}
