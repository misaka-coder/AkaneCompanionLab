"""Deterministic contract diagnostics. Never echo submitted values or unknown keys."""


def field_path(parent, key):
    return f"{parent}.{key}" if parent else key


def error(path, reason, expected):
    return {"path": path or "$", "reason": reason, "expected": expected}


def _declared_fields(schema):
    fields = set(schema.get("properties", {}))
    for child in schema.get("properties", {}).values():
        fields.update(_declared_fields(child))
    if "items" in schema:
        fields.update(_declared_fields(schema["items"]))
    return fields


def schema_error(value, schema, path="", known=None):
    if known is None:
        known = _declared_fields(schema)
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            return error(path, "wrong_type", "object")
        if len(value) < schema.get("minProperties", 0):
            return error(path, "too_few_fields", f"at least {schema['minProperties']} field(s)")
        issue = shape_error(value, set(schema.get("required", ())), set(schema["properties"]), path, known=known)
        if issue:
            return issue
        for key, item in value.items():
            issue = schema_error(item, schema["properties"][key], field_path(path, key), known)
            if issue:
                return issue
    elif kind == "array":
        if not isinstance(value, list):
            return error(path, "wrong_type", "array")
        if not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            return error(path, "item_count", f"{schema.get('minItems', 0)}..{schema['maxItems']} items")
        for index, item in enumerate(value):
            issue = schema_error(item, schema["items"], f"{path}[{index}]", known)
            if issue:
                return issue
    elif kind == "boolean":
        if type(value) is not bool:
            return error(path, "wrong_type", "boolean")
    elif kind == "integer":
        expected = f"integer {schema['minimum']}..{schema['maximum']}"
        if type(value) is not int:
            return error(path, "wrong_type", expected)
        if not schema["minimum"] <= value <= schema["maximum"]:
            return error(path, "out_of_range", expected)
    elif kind == "string":
        if not isinstance(value, str):
            return error(path, "wrong_type", "string")
        if "enum" in schema and value not in schema["enum"]:
            return error(path, "invalid_choice", " | ".join(schema["enum"]))
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10000):
            return error(path, "string_length", f"{schema.get('minLength', 0)}..{schema.get('maxLength', 10000)} characters")
    else:
        raise ValueError("unsupported_contract_schema_type")
    return None


def shape_error(value, required, allowed, path="", known=None):
    missing = required - value.keys()
    if missing:
        key = sorted(missing)[0]
        return error(field_path(path, key), "missing_field", "required field; " + shape_description(required, allowed))
    extra = value.keys() - allowed
    if extra:
        # Unknown keys are arbitrary user data, unlike declared field names.
        key = next((k for k in sorted(known or allowed) if k in extra), "<unknown>")
        return error(field_path(path, key), "field_not_allowed", shape_description(required, allowed))
    return None


def shape_description(required, allowed):
    return "required: " + (", ".join(sorted(required)) or "none") + "; optional: " + (", ".join(sorted(allowed - required)) or "none")


TEXT_DESCRIPTION = "单行文本；不接受 CR、LF、Tab、NUL。type_text 不可为空；set_value 可为空以清空值。"


def text_error(value, action, path):
    if action == "type_text" and not value:
        return error(path, "empty_text", TEXT_DESCRIPTION)
    for char, label in (("\r", "CR"), ("\n", "LF"), ("\t", "Tab"), ("\0", "NUL")):
        if char in value:
            return error(path, "unsupported_text_character_" + label, TEXT_DESCRIPTION)
    return None
