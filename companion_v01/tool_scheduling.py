"""Host-owned scheduling parameters, separate from business tool contracts."""
from __future__ import annotations

import copy
from dataclasses import replace


# These calls already own a task lifecycle, or control one. Never wrap them.
DIRECT_TASK_TOOLS = frozenset({
    "exec_run", "exec_status", "exec_cancel", "exec_input", "exec_approve",
    "spawn_subagent", "manage_subagents", "subagent_status", "subagent_cancel",
})
BACKGROUND_PARAMETER = {
    "type": "boolean",
    "description": "Return a task ID immediately without foreground waiting. Defaults to false.",
}
TOOL_EXECUTION_HINT = (
    "普通工具默认前台等待，独立调用同批共用窗口；到期返回原任务 ID。"
    "后台调度参数以工具定义为准，通常为 background=true，受理后立即返回 ID。"
    "未消费的完成结果会补入会话；已查询的终态不重复通知。"
    "交付依赖结果时用 exec_status 继续查询或等待。"
    "用户明确要求后台运行或先回复时，可结束本轮。"
    "任务 ID 或进度不代表完成，不要重复启动。"
)


def scheduling_parameter(spec, *, tool_name=""):
    name = str(getattr(spec, "capability_id", "") or tool_name)
    if name in DIRECT_TASK_TOOLS:
        return None
    schema = getattr(spec, "input_schema", {}) or {}
    properties = schema.get("properties", {})
    # Preserve a provider's existing business field; advertise an unambiguous
    # host name instead. Never steal it or send our parameter to the provider.
    if "background" in properties:
        parameter = "host_background"
        while parameter in properties:
            parameter = "host_" + parameter
        return parameter
    return "background"


def scheduled_tool_spec(spec):
    parameter = scheduling_parameter(spec)
    if parameter is None:
        return spec
    schema = copy.deepcopy(spec.input_schema)
    schema.setdefault("properties", {})[parameter] = dict(BACKGROUND_PARAMETER)
    return replace(spec, input_schema=schema, schema_hash="")


def extract_scheduling(value, *, spec=None, tool_name=""):
    """Peel the optional host flag before normalization/strict validation."""
    call = dict(value)
    parameter = scheduling_parameter(spec, tool_name=tool_name)
    background = call.get("_tool_background", False)
    error = str(call.get("_tool_scheduling_error") or "")
    if parameter:
        wire = call.get("_tool_model_arguments")
        if isinstance(wire, dict) and call.get("_tool_model_name") not in {"capability_invoke", "invoke_mcp"}:
            wire = dict(wire)
            if parameter in wire:
                flag = wire.pop(parameter)
                if isinstance(flag, bool):
                    background = flag
                else:
                    error = f"{parameter}_must_be_boolean"
            call["_tool_model_arguments"] = wire
        # Plugin compatibility calls use a business-arguments envelope; native
        # calls and ordinary built-ins put arguments at the outer level.
        args = dict(call["arguments"]) if isinstance(call.get("arguments"), dict) else None
        target = call if parameter in call else args
        if target is not None and parameter in target:
            flag = target.pop(parameter)
            if isinstance(flag, bool):
                background = flag
            else:
                error = f"{parameter}_must_be_boolean"
            if target is args:
                call["arguments"] = args
    if background:
        call["_tool_background"] = True
    if error:
        call["_tool_scheduling_error"] = error
    return call


def freeze_request_value(value):
    """Copy ordinary request containers, retain trusted services and scopes."""
    if isinstance(value, dict):
        return {key: freeze_request_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [freeze_request_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(freeze_request_value(item) for item in value)
    return value
