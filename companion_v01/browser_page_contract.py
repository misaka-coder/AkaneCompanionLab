"""Managed browser features shared by admission and capability reporting."""
CONTROL_ACTIONS = frozenset({"click", "fill", "press", "hover", "select_option", "set_checked", "upload"})
MANAGED_ACTIONS = frozenset({"navigate", "read_text", "current", "snapshot", "screenshot", "scroll", "elements", "download_status", "capabilities", "run_actions"}) | CONTROL_ACTIONS
OPTION_KEYS = ("frame_selector", "scope_selector", "values", "checked", "files", "dialog")
EXTRA_PROPERTIES = {
    "frame_selector": {"type": "string", "maxLength": 220, "description": "Managed only: CSS selector identifying one iframe; observe that frame before acting in it."},
    "scope_selector": {"type": "string", "maxLength": 220, "description": "Managed snapshot/read_text only: read one container within the selected document."},
    "values": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "maxLength": 500}, "description": "Managed select_option: exact option values."},
    "checked": {"type": "boolean", "description": "Managed set_checked desired checkbox/radio state."},
    "files": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "maxLength": 120}, "description": "Managed upload: exact current-session material handles; target must be a file input. Register CLI output first; no host paths."},
    "dialog": {"type": "object", "properties": {"action": {"type": "string", "enum": ["accept", "dismiss"]}, "prompt_text": {"type": "string", "maxLength": 500}},
               "required": ["action"], "additionalProperties": False, "description": "Managed only: response to first dialog caused by this action. Default dismiss; acceptance requires existing browser authorization."},
}
STEP_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": sorted(CONTROL_ACTIONS)},
    "selector": {"type": "string", "minLength": 1, "maxLength": 220},
    "text": {"type": "string", "maxLength": 500},
    "key": {"type": "string"},
    **{k: v for k, v in EXTRA_PROPERTIES.items() if k != "scope_selector"},
}, "required": ["action", "selector"], "additionalProperties": False}
EXTRA_PROPERTIES["actions"] = {"type": "array", "minItems": 1, "maxItems": 6, "items": STEP_SCHEMA,
    "description": "Managed run_actions: up to 6 preplanned selector actions in one document/frame. Stop on failure, navigation, popup, dialog or incomplete observation. Returns per-step facts; never replay completed steps."}


def option_error(value):
    action = value.get("action")
    for field, required_action in (("values", "select_option"), ("checked", "set_checked"), ("files", "upload"), ("actions", "run_actions")):
        if field in value and action != required_action:
            return f"{field} 只用于 {required_action}；未执行。"
    for key in ("frame_selector", "scope_selector"):
        if key in value and (not isinstance(value[key], str) or not value[key] or len(value[key]) > 220 or any(ord(c) < 32 for c in value[key])):
            return f"{key} 必须是 1–220 字符的选择器。"
    if value.get("scope_selector") and action not in {"snapshot", "read_text"}:
        return "scope_selector 只用于 snapshot/read_text 局部读取。"
    if value.get("frame_selector") and action not in CONTROL_ACTIONS | {"snapshot", "read_text", "run_actions"}:
        return "当前 frame_selector 只用于控件动作及 snapshot/read_text。"
    if value.get("frame_selector") and (value.get("coordinate") is not None or value.get("candidate_index")):
        return "frame_selector 使用该 frame 内的 selector/ref，不接受主页面坐标或候选序号。"
    if value.get("frame_selector") and action == "press" and not (value.get("selector") or value.get("ref")):
        return "frame 内 press 需要 selector/ref，不能把 frame 参数当作当前焦点。"
    if action == "select_option":
        values = value.get("values")
        if not isinstance(values, list) or not values or len(values) > 20 or any(not isinstance(v, str) or len(v) > 500 for v in values):
            return "select_option.values 必须是 1–20 项 option value 字符串数组。"
    if action == "set_checked" and type(value.get("checked")) is not bool:
        return "set_checked.checked 必须是布尔值。"
    if action == "upload":
        files = value.get("files")
        if not isinstance(files, list) or not files or len(files) > 8 or any(not isinstance(v, str) or not v or len(v) > 120 for v in files):
            return "upload.files 必须是 1–8 个当前会话材料句柄，不接受宿主路径。"
    if "dialog" in value:
        dialog = value["dialog"]
        if (not isinstance(dialog, dict) or set(dialog) - {"action", "prompt_text"}
                or dialog.get("action") not in {"accept", "dismiss"}
                or not isinstance(dialog.get("prompt_text", ""), str) or len(dialog.get("prompt_text", "")) > 500):
            return "dialog 需要 action=accept/dismiss 和可选的 prompt_text（最多 500 字符）。"
        if action not in CONTROL_ACTIONS | {"navigate"}:
            return "dialog 策略只作用于本次 navigate/控件动作。"
        if dialog["action"] == "dismiss" and "prompt_text" in dialog:
            return "dismiss 不接受 prompt_text。"
    return ""


def managed_capabilities():
    return {"backend": "managed", "actions": sorted(MANAGED_ACTIONS), "fill_max_chars": 500,
            "fill_empty": True, "fill_multiline": True, "frames": "single iframe CSS selector",
            "scoped_reads": ["snapshot", "read_text"], "uploads": "current-session material handles",
            "batch_max_actions": 6, "coordinates": True, "download_transfer": True}
