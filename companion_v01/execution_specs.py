"""Canonical ToolSpecs for the minimal general execution kernel (Phase 1 contract).

Scope: contract only. The three tools are registered by the capability fabric
in Phase 3; readiness is surfaced as structured status (``exec_status`` /
``capability_unavailable``), never by adding or removing tools from a capability
profile's schema. Whether a profile exposes these tools at all is decided by the
host's capability selection, not by this module.
"""

from __future__ import annotations

from typing import Any

from capcore import CapabilityToolSpec

EXEC_RUN_TOOL_NAME = "exec_run"
EXEC_STATUS_TOOL_NAME = "exec_status"
EXEC_CANCEL_TOOL_NAME = "exec_cancel"

# Command-inner execution statuses carried by exec_run / exec_status payloads.
# These are the command's own outcome, orthogonal to the executor broker's
# dispatch-integrity status (see execution_run.map_exec_run_outcome).
EXEC_STATUS_RUNNING = "running"
EXEC_STATUS_COMPLETED = "completed"
EXEC_STATUS_FAILED = "failed"
EXEC_STATUS_TIMED_OUT = "timed_out"
EXEC_STATUS_CANCELLED = "cancelled"
EXEC_STATUS_UNAVAILABLE = "unavailable"
EXEC_STATUS_UNKNOWN = "unknown"
EXEC_STATUS_EXECUTION_UNKNOWN = "execution_unknown"

EXEC_RUN_INNER_STATUSES: frozenset[str] = frozenset(
    {
        EXEC_STATUS_COMPLETED,
        EXEC_STATUS_FAILED,
        EXEC_STATUS_TIMED_OUT,
        EXEC_STATUS_CANCELLED,
        EXEC_STATUS_RUNNING,
        EXEC_STATUS_UNAVAILABLE,
        EXEC_STATUS_EXECUTION_UNKNOWN,
    }
)
EXEC_STATUS_QUERY_STATUSES: frozenset[str] = frozenset(
    {
        EXEC_STATUS_RUNNING,
        EXEC_STATUS_COMPLETED,
        EXEC_STATUS_FAILED,
        EXEC_STATUS_TIMED_OUT,
        EXEC_STATUS_CANCELLED,
        EXEC_STATUS_UNAVAILABLE,
        EXEC_STATUS_UNKNOWN,
        EXEC_STATUS_EXECUTION_UNKNOWN,
    }
)
EXEC_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        EXEC_STATUS_COMPLETED,
        EXEC_STATUS_FAILED,
        EXEC_STATUS_TIMED_OUT,
        EXEC_STATUS_CANCELLED,
    }
)

# exec_cancel outcome: confirmed cancellation, an ended/unknown run, or a
# failed termination attempt. A cancellation request alone is never success.
EXEC_CANCEL_RESULT_STATUSES: frozenset[str] = frozenset(
    {EXEC_STATUS_CANCELLED, "already_ended", EXEC_STATUS_UNKNOWN, "cancel_failed"}
)

# Bounded initial wait: a short command returns its final status in the current
# turn; only commands still alive after the window become running + run_id.
EXEC_DEFAULT_TIMEOUT_SECONDS = 120
EXEC_MAX_TIMEOUT_SECONDS = 600
EXEC_DEFAULT_INITIAL_WAIT_SECONDS = 8
EXEC_MIN_INITIAL_WAIT_SECONDS = 1
EXEC_MAX_INITIAL_WAIT_SECONDS = 10

EXEC_DEFAULT_RUN_RETENTION_SECONDS = 600
EXEC_DEFAULT_MAX_RUNS = 128
EXEC_DEFAULT_MAX_LOG_BYTES = 64 * 1024
EXEC_OUTPUT_PAGE_BYTES = 8 * 1024
EXEC_RUN_RESULT_MAX_BYTES = 16 * 1024
EXEC_STATUS_RESULT_MAX_BYTES = 16 * 1024
EXEC_CANCEL_RESULT_MAX_BYTES = 4 * 1024

EXEC_COMMAND_MAX_CHARS = 8192
EXEC_CWD_MAX_CHARS = 512
EXEC_RUN_ID_MAX_CHARS = 96
EXEC_CURSOR_MAX_CHARS = 160


def normalize_timeout_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = EXEC_DEFAULT_TIMEOUT_SECONDS
    return max(1, min(EXEC_MAX_TIMEOUT_SECONDS, seconds))


def normalize_initial_wait_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = EXEC_DEFAULT_INITIAL_WAIT_SECONDS
    return max(EXEC_MIN_INITIAL_WAIT_SECONDS, min(EXEC_MAX_INITIAL_WAIT_SECONDS, seconds))


_EXEC_RUN_STATUS_ENUM: list[str] = [
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_UNAVAILABLE,
    EXEC_STATUS_EXECUTION_UNKNOWN,
]
# Explicit, deterministic order (never derived from a set) so the projected
# schema and the real request-body tools prefix stay byte-identical across turns.
_EXEC_QUERY_STATUS_ENUM: list[str] = [
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_UNAVAILABLE,
    EXEC_STATUS_UNKNOWN,
    EXEC_STATUS_EXECUTION_UNKNOWN,
]
_EXEC_CANCEL_STATUS_ENUM: list[str] = [
    EXEC_STATUS_CANCELLED,
    "already_ended",
    EXEC_STATUS_UNKNOWN,
    "cancel_failed",
]


def _null_or_string() -> list[str]:
    return ["string", "null"]


def _null_or_integer() -> list[str]:
    return ["integer", "null"]


EXEC_RUN_TOOL_SPEC = CapabilityToolSpec(
    capability_id=EXEC_RUN_TOOL_NAME,
    display_name="Run a command in the trusted execution workspace",
    description=(
        "使用拥有当前宿主用户权限的受信任执行器运行命令或脚本；它不是 Shell 沙箱。cwd 与宿主文件引用"
        "只能使用工作区相对路径或已配置的挂载别名，但命令本身仍可能访问该用户有权访问的其他资源。"
        "环境变量由宿主执行器按白名单注入，本工具不接受环境变量。短命令在本轮直接返回最终状态；"
        "超过初始等待窗口仍存活的命令返回 run_id 与 running 状态，之后用 exec_status 查询进度、"
        "exec_cancel 停止。执行失败、超时或取消都会明确返回对应状态，不会声称成功。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "maxLength": EXEC_COMMAND_MAX_CHARS,
                "description": "要执行的命令或脚本。",
            },
            "cwd": {
                "type": "string",
                "maxLength": EXEC_CWD_MAX_CHARS,
                "description": "工作区内相对路径或挂载别名（可选），默认工作区根。",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": EXEC_MAX_TIMEOUT_SECONDS,
                "description": "命令自身的超时秒数。",
            },
            "initial_wait_seconds": {
                "type": "integer",
                "minimum": EXEC_MIN_INITIAL_WAIT_SECONDS,
                "maximum": EXEC_MAX_INITIAL_WAIT_SECONDS,
                "description": "本轮最多等待秒数；窗口内未结束的命令转为 running 并返回 run_id。",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": _EXEC_RUN_STATUS_ENUM},
            "run_id": {"type": "string", "maxLength": EXEC_RUN_ID_MAX_CHARS},
            "exit_code": {"type": _null_or_integer()},
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "next_cursor": {"type": _null_or_string(), "maxLength": EXEC_CURSOR_MAX_CHARS},
            "reason": {"type": "string"},
        },
        "required": ["status"],
        "additionalProperties": False,
    },
    risk="high",
    confirm="always",
    effects=("command_exec",),
    visible_in=("desktop", "qq"),
    execution_class="long_task",
    idempotency="effectful",
    max_result_bytes=EXEC_RUN_RESULT_MAX_BYTES,
)


EXEC_STATUS_TOOL_SPEC = CapabilityToolSpec(
    capability_id=EXEC_STATUS_TOOL_NAME,
    display_name="Query a running command",
    description=(
        "按 run_id 查询已启动命令的状态，并用 cursor 增量读取输出。每次返回自 cursor 之后的新输出"
        "片段和 next_cursor；任务终止后仍可逐页读完剩余输出，全部读完后 next_cursor 才为 null。"
        "终态结果保留一段可读时间，之后返回 unknown。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "maxLength": EXEC_RUN_ID_MAX_CHARS},
            "cursor": {
                "type": _null_or_string(),
                "maxLength": EXEC_CURSOR_MAX_CHARS,
                "description": "上一次返回的 next_cursor。",
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": _EXEC_QUERY_STATUS_ENUM},
            "run_id": {"type": "string", "maxLength": EXEC_RUN_ID_MAX_CHARS},
            "exit_code": {"type": _null_or_integer()},
            "tail": {"type": "string", "description": "自 cursor 之后的新输出片段。"},
            "next_cursor": {"type": _null_or_string(), "maxLength": EXEC_CURSOR_MAX_CHARS},
            "reason": {"type": "string"},
        },
        "required": ["status", "run_id"],
        "additionalProperties": False,
    },
    risk="low",
    confirm="never",
    effects=("read_execution_status",),
    visible_in=("desktop", "qq"),
    execution_class="sync",
    idempotency="read_only",
    max_result_bytes=EXEC_STATUS_RESULT_MAX_BYTES,
)


EXEC_CANCEL_TOOL_SPEC = CapabilityToolSpec(
    capability_id=EXEC_CANCEL_TOOL_NAME,
    display_name="Stop a running command",
    description=("停止指定 run_id 的命令。命令已结束时返回 already_ended，不会误报。"),
    input_schema={
        "type": "object",
        "properties": {"run_id": {"type": "string", "maxLength": EXEC_RUN_ID_MAX_CHARS}},
        "required": ["run_id"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string", "enum": _EXEC_CANCEL_STATUS_ENUM},
            "run_id": {"type": "string", "maxLength": EXEC_RUN_ID_MAX_CHARS},
            "reason": {"type": "string"},
        },
        "required": ["ok", "status"],
        "additionalProperties": False,
    },
    risk="medium",
    confirm="never",
    effects=("cancel_execution",),
    visible_in=("desktop", "qq"),
    execution_class="sync",
    idempotency="idempotent",
    max_result_bytes=EXEC_CANCEL_RESULT_MAX_BYTES,
)


EXEC_TOOL_SPECS: tuple[CapabilityToolSpec, ...] = (
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_TOOL_SPEC,
    EXEC_CANCEL_TOOL_SPEC,
)
EXEC_TOOL_SPEC_BY_ID: dict[str, CapabilityToolSpec] = {
    spec.capability_id: spec for spec in EXEC_TOOL_SPECS
}


def exec_tool_spec(tool_id: str) -> CapabilityToolSpec | None:
    return EXEC_TOOL_SPEC_BY_ID.get(str(tool_id or "").strip())
