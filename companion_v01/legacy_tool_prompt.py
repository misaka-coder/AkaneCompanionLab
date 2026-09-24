from __future__ import annotations

import json

from capcore import CapabilityToolSpec


def render_legacy_json_tool_instruction(
    spec: CapabilityToolSpec, *, argument_envelope: bool = False,
) -> str:
    """Render the complete canonical contract for Akane's JSON compatibility lane."""
    if not isinstance(spec, CapabilityToolSpec):
        raise TypeError("canonical_tool_spec_required")
    capability_id = str(spec.capability_id or "").strip()
    if not capability_id:
        raise ValueError("canonical_tool_spec_id_required")
    selector = json.dumps({"type": capability_id}, ensure_ascii=False, separators=(",", ":"))
    placement = (
        "在 tool_call.arguments 对象中填写业务参数；外层 type 仅用于选择工具。"
        if argument_envelope else "在 tool_call 中与 type 同级填写业务参数。"
    )
    schema_text = json.dumps(spec.input_schema, ensure_ascii=False, separators=(",", ":"))
    return (
        f"- {capability_id}：{spec.description}\n"
        f"兼容 JSON tool_call 的工具选择字段为 {selector}；{placement}\n"
        f"业务参数完整 JSON Schema：{schema_text}"
    )
