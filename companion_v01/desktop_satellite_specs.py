"""Canonical ToolSpecs for the low-risk Desktop Satellite surface.

These operations already have real Tauri executors.  Keeping their wire
contracts together prevents the cloud service and the desktop client from
silently inventing different names or argument shapes.
"""

from __future__ import annotations

from typing import Any

from capcore import CapabilityToolSpec


_EMPTY_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


DESKTOP_CONTEXT_SNAPSHOT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="desktop_context_snapshot",
    display_name="Read desktop context",
    description=(
        "读取用户绑定电脑当前前台窗口的有限上下文。只返回窗口标题、进程名和平台等安全摘要，"
        "不读取窗口正文、文件路径或剪贴板。"
    ),
    input_schema=_EMPTY_INPUT,
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "enabled": {"type": "boolean"},
            "capturedAt": {"type": "integer"},
            "platform": {"type": "string"},
            "foreground": {"type": "object"},
        },
        "required": ["ok", "enabled", "foreground"],
        "additionalProperties": False,
    },
    risk="low",
    confirm="never",
    effects=("read_desktop_context",),
    visible_in=("desktop", "qq"),
    idempotency="read_only",
    max_result_bytes=4096,
)


SYSTEM_MEDIA_SNAPSHOT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="system_media_snapshot",
    display_name="Read system media",
    description=("读取用户绑定电脑当前系统播放器的歌曲、进度和播放状态。没有活动播放器时返回结构化不可用状态。"),
    input_schema=_EMPTY_INPUT,
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "platform": {"type": "string"},
            "trackKey": {"type": "string"},
            "title": {"type": "string"},
            "artist": {"type": "string"},
            "album": {"type": "string"},
            "sourceApp": {"type": "string"},
            "playbackStatus": {"type": "string"},
            "isPlaying": {"type": "boolean"},
            "positionSeconds": {"type": ["number", "null"]},
            "durationSeconds": {"type": ["number", "null"]},
        },
        "required": ["ok", "status"],
        "additionalProperties": False,
    },
    risk="low",
    confirm="never",
    effects=("read_system_media",),
    visible_in=("desktop", "qq"),
    idempotency="read_only",
    max_result_bytes=4096,
)


SYSTEM_MEDIA_CONTROL_TOOL_SPEC = CapabilityToolSpec(
    capability_id="system_media_control",
    display_name="Control system media",
    description=(
        "按用户明确要求控制绑定电脑的系统播放器。只允许播放、暂停、停止、上一首和下一首，不会操作文件或网页。"
        "返回状态严格区分：已确认达到目标状态、指令已发送但状态未确认（execution_unknown，不算成功）、无媒体会话或明确失败；"
        "未确认时不要声称播放状态已经改变。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "stop", "previous", "next"],
            }
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "action": {"type": "string"},
            "platform": {"type": "string"},
            "trackKey": {"type": "string"},
            "title": {"type": "string"},
            "artist": {"type": "string"},
            "sourceApp": {"type": "string"},
            "playbackStatus": {"type": "string"},
        },
        "required": ["ok", "status", "action"],
        "additionalProperties": False,
    },
    risk="medium",
    confirm="never",
    effects=("control_system_media",),
    visible_in=("desktop", "qq"),
    idempotency="effectful",
    max_result_bytes=4096,
)


SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="system_process_snapshot",
    display_name="Read system process list",
    description=(
        "读取绑定电脑当前可见的进程列表摘要（数量有限的 pid 与进程名）。不返回命令行、路径、用户或任何凭据；"
        "没有在线桌面执行器时返回结构化不可用状态。"
    ),
    input_schema=_EMPTY_INPUT,
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "capturedAt": {"type": "integer"},
            "platform": {"type": "string"},
            "processes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pid": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                    "required": ["pid", "name"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["ok", "status", "capturedAt", "platform", "processes"],
        "additionalProperties": False,
    },
    risk="low",
    confirm="never",
    effects=("read_system_processes",),
    visible_in=("desktop", "qq"),
    idempotency="read_only",
    max_result_bytes=16 * 1024,
)


SYSTEM_PROCESS_TERMINATE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="system_process_terminate",
    display_name="Terminate a system process",
    description=(
        "按用户明确要求终止绑定电脑上的指定进程（正整数 pid）。这是高风险操作，需要用户确认后才能执行；"
        "不返回命令行、路径或凭据。"
    ),
    input_schema={
        "type": "object",
        "properties": {"pid": {"type": "integer", "minimum": 1, "maximum": 4294967295}},
        "required": ["pid"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "pid": {"type": "integer"},
            "platform": {"type": "string"},
        },
        "required": ["ok", "status", "pid"],
        "additionalProperties": False,
    },
    risk="high",
    confirm="always",
    effects=("terminate_system_process",),
    visible_in=("desktop", "qq"),
    idempotency="effectful",
    max_result_bytes=4096,
)


SYSTEM_VOLUME_TOOL_SPEC = CapabilityToolSpec(
    capability_id="system_volume",
    display_name="Read or set system volume",
    description=(
        "读取或设置绑定电脑的系统音量（0-100）。设置音量是设备级动作，只在私聊主人的请求下执行；"
        "不返回路径或凭据。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "value": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "action": {"type": "string"},
            "volume": {"type": ["integer", "null"]},
            "muted": {"type": ["boolean", "null"]},
            "platform": {"type": "string"},
        },
        "required": ["ok", "status", "action"],
        "additionalProperties": False,
    },
    risk="medium",
    confirm="never",
    effects=("control_system_volume",),
    visible_in=("desktop", "qq"),
    idempotency="effectful",
    max_result_bytes=4096,
)


DESKTOP_SATELLITE_TOOL_SPECS: tuple[CapabilityToolSpec, ...] = (
    DESKTOP_CONTEXT_SNAPSHOT_TOOL_SPEC,
    SYSTEM_MEDIA_SNAPSHOT_TOOL_SPEC,
    SYSTEM_MEDIA_CONTROL_TOOL_SPEC,
    SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC,
    SYSTEM_PROCESS_TERMINATE_TOOL_SPEC,
    SYSTEM_VOLUME_TOOL_SPEC,
)
DESKTOP_SATELLITE_TOOL_SPECS_BY_ID = {spec.capability_id: spec for spec in DESKTOP_SATELLITE_TOOL_SPECS}


def desktop_satellite_spec(tool_id: str) -> CapabilityToolSpec | None:
    return DESKTOP_SATELLITE_TOOL_SPECS_BY_ID.get(str(tool_id or "").strip())
