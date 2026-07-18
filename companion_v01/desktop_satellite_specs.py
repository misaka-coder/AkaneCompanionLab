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


DESKTOP_SATELLITE_TOOL_SPECS: tuple[CapabilityToolSpec, ...] = (
    DESKTOP_CONTEXT_SNAPSHOT_TOOL_SPEC,
    SYSTEM_MEDIA_SNAPSHOT_TOOL_SPEC,
    SYSTEM_MEDIA_CONTROL_TOOL_SPEC,
)
DESKTOP_SATELLITE_TOOL_SPECS_BY_ID = {spec.capability_id: spec for spec in DESKTOP_SATELLITE_TOOL_SPECS}


def desktop_satellite_spec(tool_id: str) -> CapabilityToolSpec | None:
    return DESKTOP_SATELLITE_TOOL_SPECS_BY_ID.get(str(tool_id or "").strip())
