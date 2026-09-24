"""Bounded declarative desktop plans. No scripts or model-provided grants."""

from .validation import schema_error, shape_error, shape_description, text_error, TEXT_DESCRIPTION

SELECTOR = {"type": "object", "properties": {
    "name": {"type": "string", "maxLength": 256},
    "automation_id": {"type": "string", "minLength": 1, "maxLength": 256},
    "role": {"type": "integer", "minimum": 50000, "maximum": 50100},
    "editable": {"type": "boolean"}, "focused": {"type": "boolean"},
}, "minProperties": 1, "additionalProperties": False}
CONDITION = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["focused", "value_equals", "checked_equals", "element_exists", "element_absent", "window_title"]},
    "target": SELECTOR, "value": {"type": "string", "maxLength": 4000},
    "checked": {"type": "boolean"}, "title": {"type": "string", "minLength": 1, "maxLength": 512},
}, "required": ["kind"], "additionalProperties": False}
STEP = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["click", "type_text", "press_key", "scroll", "set_value", "set_checked", "wait_for", "select_window"]},
    "target": SELECTOR, "expect": CONDITION,
    "window_id": {"type": "string", "minLength": 1, "maxLength": 160},
    "text": {"type": "string", "maxLength": 4000},
    "key": {"type": "string", "minLength": 1, "maxLength": 40},
    "checked": {"type": "boolean"},
    "button": {"type": "string", "enum": ["left", "right", "middle"]},
    "click_count": {"type": "integer", "minimum": 1, "maximum": 2},
    "direction": {"type": "string", "enum": ["up", "down"]},
    "amount": {"type": "integer", "minimum": 1, "maximum": 10},
    "wait_ms": {"type": "integer", "minimum": 0, "maximum": 3000},
}, "required": ["action", "expect"], "additionalProperties": False}


CONDITION_FIELDS = {
    "window_title": {"title"}, "value_equals": {"target", "value"},
    "checked_equals": {"target", "checked"}, "focused": {"target"},
    "element_exists": {"target"}, "element_absent": {"target"},
}
STEP_FIELDS = {
    "click": ({"target"}, {"target", "button", "click_count"}),
    "scroll": ({"target", "direction"}, {"target", "direction", "amount"}),
    **{action: (fields, fields) for action, fields in {
        "type_text": {"text"}, "press_key": {"key"}, "set_value": {"target", "text"},
        "set_checked": {"target", "checked"}, "wait_for": set(), "select_window": {"window_id"},
    }.items()},
}
CONDITION["properties"]["kind"]["description"] = "每种 expect 只接受 kind 与以下字段：\n" + "\n".join(
    f"{kind}: {', '.join(sorted(fields))}" for kind, fields in CONDITION_FIELDS.items()
)
STEP["properties"]["action"]["description"] = "每步必填 action、expect，可选 wait_ms。其余字段：\n" + "\n".join(
    f"{action}: {shape_description(required, allowed)}" for action, (required, allowed) in STEP_FIELDS.items()
)
STEP["properties"]["text"]["description"] = TEXT_DESCRIPTION


def condition_error(condition, path=""):
    issue = schema_error(condition, CONDITION, path)
    if issue:
        return issue
    fields = CONDITION_FIELDS[condition["kind"]] | {"kind"}
    return shape_error(condition, fields, fields, path, known=CONDITION["properties"])


def steps_error(steps):
    issue = schema_error(steps, {"type": "array", "minItems": 1, "maxItems": 12, "items": STEP}, "steps")
    if issue:
        return issue
    for index, step in enumerate(steps):
        action = step["action"]
        path = f"steps[{index}]"
        required, allowed = STEP_FIELDS[action]
        issue = shape_error(step, required | {"action", "expect"}, allowed | {"action", "expect", "wait_ms"}, path, known=STEP["properties"])
        issue = issue or condition_error(step["expect"], f"{path}.expect")
        if not issue and "text" in step:
            issue = text_error(step["text"], action, f"{path}.text")
        if issue:
            return {**issue, "step_action": action}
    return None


