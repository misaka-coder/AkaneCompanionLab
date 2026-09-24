"""Lossless model presentation; device evidence and permission checks stay raw."""
from copy import deepcopy
import json

PRIORITY = ("ok", "action_state", "observation_state", "reason", "observation_reason", "error", "execution_status",
            "next_action", "recovery_hint", "retry_after_ms", "operation_id", "workflow_id", "workflow_state",
            "workflow_kind", "task_verified", "next_step", "completed_steps", "steps", "dispatch_phase",
            "input_enabled", "permission_mode", "control_status", "status", "window_id", "owner_window_id",
            "title", "window_state", "foreground_matches", "focus_evidence", "message_target", "effect_evidence", "occluder",
            "context_ref", "device_epoch", "control_session_id", "observation_id", "captured_at",
            "model_image_count", "coordinate_space", "coordinate_bounds", "window_bounds", "client_bounds",
            "dpi", "ax_status", "ax_reason", "text_complete", "ax_references", "screenshots", "elements")
ELEMENT_FIRST = ("element_id", "name", "role", "enabled", "focused", "offscreen", "stable_identity")
QQ_NOTE = ("QQ 场景：页头与编辑框标签可能不一致；message_target 只涉及收件人，不证明附件已加载或消息已送达。"
           "send_attempted 只表示尝试发送，需核对同一会话新增正文/文件和草稿状态。")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _elements(rows):
    if not isinstance(rows, list) or len(rows) < 2 or not all(isinstance(row, dict) for row in rows):
        return rows
    groups = []
    for row in rows:
        columns = [key for key in ELEMENT_FIRST if key in row] + sorted(row.keys() - set(ELEMENT_FIRST))
        # Consecutive groups preserve tree traversal order. Each group's exact
        # key set preserves the distinction between absent, null and false.
        if not groups or columns != groups[-1]["columns"]:
            groups.append({"columns": columns, "rows": []})
        groups[-1]["rows"].append([row[key] for key in columns])
    table = {"format": "columns-v1", "groups": groups}
    return table if len(dumps(table)) < len(dumps(rows)) else rows


def model_result(data):
    result = {key: deepcopy(data[key]) for key in PRIORITY if key in data}
    result.update({key: deepcopy(value) for key, value in data.items() if key not in result})
    if "elements" in result:
        result["elements"] = _elements(result["elements"])
    return result


def feedback(data):
    text = "桌面事实：输入执行不等于任务完成；缺失字段保持未知。\n实际返回数据：" + dumps(model_result(data))
    if "message_target" in data:
        text += "\n" + QQ_NOTE
    return text
