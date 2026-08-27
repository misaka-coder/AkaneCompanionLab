from __future__ import annotations

import json
from typing import Any, Mapping

from capcore import CapabilityToolSpec


def render_legacy_json_tool_instruction(spec: CapabilityToolSpec) -> str:
    """Render Akane's legacy JSON tool-call hint from one canonical ToolSpec.

    CapCore owns the structured contract. Akane owns this compatibility wording
    because ``tool_call`` is part of Akane's final-output protocol. The compact
    signature deliberately avoids copying the full JSON Schema descriptions:
    model semantics stay in ``spec.description`` and field constraints remain a
    deterministic projection of ``spec.input_schema``.
    """

    if not isinstance(spec, CapabilityToolSpec):
        raise TypeError("canonical_tool_spec_required")
    capability_id = str(spec.capability_id or "").strip()
    if not capability_id:
        raise ValueError("canonical_tool_spec_id_required")
    description = " ".join(str(spec.description or "").split()).strip()
    schema = spec.input_schema if isinstance(spec.input_schema, Mapping) else {}
    properties = schema.get("properties")
    declared = properties if isinstance(properties, Mapping) else {}
    required_raw = schema.get("required")
    required = {str(item) for item in required_raw or () if isinstance(item, str) and str(item).strip()}
    example: dict[str, Any] = {"type": capability_id}
    for raw_name, raw_schema in declared.items():
        name = str(raw_name)
        if name in required:
            example[name] = _example_value(raw_schema)
    example_text = json.dumps(example, ensure_ascii=False, separators=(",", ":"))
    field_text = "；".join(
        _render_field(str(raw_name), raw_schema, required=str(raw_name) in required)
        for raw_name, raw_schema in declared.items()
    )
    parts = [f"- {capability_id}：{description}", f"兼容 JSON tool_call：{example_text}。"]
    if field_text:
        parts.append(f"参数字段（! 为必填）：{field_text}。")
    return " ".join(parts)


def _example_value(schema: Any) -> Any:
    value = schema if isinstance(schema, Mapping) else {}
    enum = value.get("enum")
    if isinstance(enum, (list, tuple)) and enum:
        return enum[0]
    schema_type = str(value.get("type") or "").strip()
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    if schema_type == "integer":
        minimum = value.get("minimum")
        return minimum if isinstance(minimum, int) else 0
    if schema_type == "number":
        minimum = value.get("minimum")
        return minimum if isinstance(minimum, (int, float)) else 0
    if schema_type == "boolean":
        return False
    return "<string>"


def _render_field(name: str, schema: Any, *, required: bool) -> str:
    value = schema if isinstance(schema, Mapping) else {}
    schema_type = str(value.get("type") or "any").strip() or "any"
    if schema_type == "array":
        items = value.get("items")
        item_type = str(items.get("type") or "any") if isinstance(items, Mapping) else "any"
        type_text = f"{item_type}[]"
    elif schema_type == "object":
        nested = value.get("properties")
        nested_names = [str(item) for item in nested] if isinstance(nested, Mapping) else []
        type_text = f"object{{{','.join(nested_names)}}}" if nested_names else "object"
    else:
        type_text = schema_type
    details: list[str] = []
    enum = value.get("enum")
    if isinstance(enum, (list, tuple)) and enum:
        if len(enum) <= 8:
            details.append("enum=" + "|".join(str(item) for item in enum))
        else:
            details.append(f"enum_count={len(enum)}")
    minimum = value.get("minimum")
    maximum = value.get("maximum")
    if isinstance(minimum, (int, float)) or isinstance(maximum, (int, float)):
        details.append(f"range={minimum if minimum is not None else ''}..{maximum if maximum is not None else ''}")
    min_items = value.get("minItems")
    max_items = value.get("maxItems")
    if isinstance(min_items, int) or isinstance(max_items, int):
        details.append(
            f"items={min_items if min_items is not None else 0}..{max_items if max_items is not None else ''}"
        )
    suffix = "!" if required else ""
    detail_text = f"[{','.join(details)}]" if details else ""
    return f"{name}:{type_text}{suffix}{detail_text}"
