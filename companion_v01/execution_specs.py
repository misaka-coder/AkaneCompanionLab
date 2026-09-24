"""Canonical schemas for command execution, status, cancellation and stdin.

The capability fabric registers these tools when a provider is configured.
Runtime readiness is reported explicitly; schema exposure follows host selection.
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
        # Bounded kill-retry fallback: the executor gave up confirming
        # termination; the run is terminal so it can be evicted, and the
        # status honestly reports "cannot confirm" instead of fake success.
        EXEC_STATUS_EXECUTION_UNKNOWN,
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
EXEC_DEFAULT_STATUS_WAIT_SECONDS = 0
EXEC_MIN_STATUS_WAIT_SECONDS = 0
EXEC_MAX_STATUS_WAIT_SECONDS = 30

EXEC_DEFAULT_RUN_RETENTION_SECONDS = 600
EXEC_DEFAULT_MAX_RUNS = 128
EXEC_DEFAULT_MAX_LOG_BYTES = 64 * 1024
# The initial result is intentionally generous: most commands should be usable
# without a mechanical follow-up. Status reads are smaller incremental views.
EXEC_INITIAL_OUTPUT_MAX_BYTES = 50 * 1024
EXEC_INITIAL_OUTPUT_MAX_LINES = 2000
EXEC_STATUS_OUTPUT_MAX_BYTES = 32 * 1024
EXEC_STATUS_OUTPUT_MAX_LINES = 1000
# Leave room for JSON escaping, status metadata, and model feedback around the
# bounded text. These are envelope budgets, not additional visible output.
EXEC_RUN_RESULT_MAX_BYTES = 128 * 1024
EXEC_STATUS_RESULT_MAX_BYTES = 96 * 1024
EXEC_CANCEL_RESULT_MAX_BYTES = 4 * 1024

EXEC_CWD_MAX_CHARS = 512
EXEC_RUN_ID_MAX_CHARS = 96
EXEC_CURSOR_MAX_CHARS = 160

# Output-artifact registration states, orthogonal to the command's own status.
# ``registered`` is the only success; everything else is a structured reason
# for the model to act on without pretending the file was delivered.
ARTIFACT_STATUS_REGISTERED = "registered"
ARTIFACT_STATUS_REGISTRATION_FAILED = "registration_failed"
ARTIFACT_STATUS_NOT_REGISTERED = "not_registered"
ARTIFACT_STATUS_NOT_REQUESTED = "not_requested"


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


def normalize_status_wait_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = EXEC_DEFAULT_STATUS_WAIT_SECONDS
    return max(EXEC_MIN_STATUS_WAIT_SECONDS, min(EXEC_MAX_STATUS_WAIT_SECONDS, seconds))


_EXEC_RUN_STATUS_ENUM: list[str] = [
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_UNAVAILABLE,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    "rejected",
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


def _null_or_number() -> list[str]:
    return ["number", "null"]


EXEC_RUN_TOOL_SPEC = CapabilityToolSpec(
    capability_id=EXEC_RUN_TOOL_NAME,
    display_name="Run a command in the trusted execution workspace",
    description=(
        "使用当前宿主用户权限运行 Shell 命令或脚本；它不是 Shell 沙箱。cwd 可为工作区相对路径、"
        "已配置别名或真实宿主绝对目录；省略时使用当前项目，没有当前项目时使用执行根。先从真实命令输出发现路径。"
        "命令语言以执行宿主事实中的 command_shell 为准；Windows 使用 PowerShell，需用 cmd 时显式调用 cmd.exe /c。"
        "短命令直接返回终态，仍在运行时返回 run_id，之后用 exec_status 或 exec_cancel。"
        "input_resources 用于暂存现有材料，output_globs 用于登记生成资源；精确路径约束见对应参数。"
        "根据 status、exit_code、stdout、stderr、reason 和 recommended_action 判断真实结果。"
        "长任务暂无输出不等于停滞；不要仅据此取消或重复启动。下载前确定目标目录，"
        "仅按实际字节数/日志报告进度；结束后核验文件完整性或程序可用性，不凭退出码推断安装成功。"
        "安装依赖前核对实际解释器、已有环境及版本约束，优先复用兼容环境；工作目录不等于 Python 环境。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "要执行的命令或脚本。参数字段名必须是 command（不是 cmd）。"
                    "命令直接交给执行宿主声明的 command_shell，不经过另一层隐式 Shell。"
                    "长源码优先用 workspace_write/workspace_patch 传输，但执行器不按命令字符数拒绝有效命令。"
                ),
            },
            "cwd": {
                "type": "string",
                "maxLength": EXEC_CWD_MAX_CHARS,
                "description": (
                    "启动目录（可选）：工作区相对路径、挂载别名或真实宿主绝对目录；已注册项目可使用 alias:project。"
                    "使用 input_resources 时必须省略 cwd；output_globs 可与 alias:project 同用。"
                    "output_globs 支持执行根或已登记项目内的绝对 cwd；已有文件用 manage_generated_file(action=register) 登记。"
                    "既无资源参数又省略时使用当前项目，没有当前项目时使用执行根；"
                    "省略但仅声明 output_globs 时，当前项目存在则在其中执行并登记，否则使用本次临时目录。"
                ),
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
            "interactive": {"type": "boolean", "description": "Keep stdin open for exec_input UTF-8 writes (default false). This is a pipe, not a TTY; read output with exec_status, stop with exec_cancel."},
            "input_resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "maxLength": 120,
                            "description": (
                                "当前会话材料索引实际显示的精确资源句柄，"
                                "如 file_001 / img_001 / audio_001 / gen_001（按索引原样抄写）；不接受 latest 等别名。"
                            ),
                        },
                        "as": {
                            "type": "string",
                            "maxLength": 512,
                            "description": "命令工作区内的安全相对路径，如 inputs/source.wav；拒绝绝对路径、.. 与盘符。",
                        },
                    },
                    "required": ["handle", "as"],
                    "additionalProperties": False,
                },
                "maxItems": 8,
                "description": "可选：要暂存进执行工作区的现有资源。输入会复制到本次运行的独立工作区，模型只使用 as 相对路径。",
            },
            "output_globs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 1024},
                "maxItems": 32,
                "description": (
                    "可选：命令完成后要登记为 gen_* 的输出路径 glob。路径相对本次受管当前目录；"
                    "与 cwd=alias:project 同用时只登记本次新建或变更的匹配文件；宿主绝对 cwd 不支持直接登记。"
                    "否则命令必须把产物写在"
                    "本次临时目录内。不要写到 /tmp 等外部目录。全部展开并去重，只登记明确声明的输出。"
                ),
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
            "output_ref": {
                "type": _null_or_string(),
                "maxLength": EXEC_RUN_ID_MAX_CHARS + 16,
                "description": "完整输出已真实保存时返回的不透明引用；绝不包含宿主绝对路径。",
            },
            "reason": {"type": "string"},
            "effective_cwd": {
                "type": "string",
                "description": "该命令实际使用的工作目录；资源模式下为本次独立临时目录。",
            },
            "workspace_id": {
                "type": "string",
                "description": "命令继承或显式使用当前项目时返回；否则为空。",
            },
            "max_chars": {"type": _null_or_integer()},
            "actual_chars": {"type": _null_or_integer()},
            "recommended_action": {"type": _null_or_string()},
            "started_at": {"type": _null_or_number(), "description": "命令启动 epoch 秒。"},
            "finished_at": {"type": _null_or_number(), "description": "命令进入终态的 epoch 秒。"},
            "observed_at": {"type": _null_or_number(), "description": "本次工具观察 epoch 秒。"},
            "generated_resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string"},
                        "name": {"type": "string"},
                        "media_type": {"type": "string"},
                        "size_bytes": {"type": "integer"},
                        "sha256": {"type": "string"},
                    },
                    "required": ["handle"],
                    "additionalProperties": False,
                },
                "description": "命令完成后按 output_globs 登记的 gen_* 资源，不含绝对路径。",
            },
            "artifact_status": {
                "type": "string",
                "enum": [
                    ARTIFACT_STATUS_REGISTERED,
                    ARTIFACT_STATUS_REGISTRATION_FAILED,
                    ARTIFACT_STATUS_NOT_REGISTERED,
                    ARTIFACT_STATUS_NOT_REQUESTED,
                ],
            },
            "artifact_reason": {
                "type": "string",
                "description": "输出未完整登记时的结构化原因；为空表示没有额外登记错误。",
            },
        },
        "required": ["status"],
        "additionalProperties": False,
    },
    risk="high",
    confirm="always",
    effects=("command_exec",),
    visible_in=("desktop", "qq"),
    spec_version="1.4.0",
    schema_version=4,
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
        "如果任务仍在运行，可用 wait_seconds 在同一次工具调用内等待进入终态或等待窗口结束，"
        "期间产生的输出会在返回时一并给出，避免进度行或警告触发反复的模型轮询；进入终态会提前返回。"
        "cursor 用于超长输出或运行中增量观察，普通命令不需要机械翻页。终态结果保留一段可读时间，"
        "之后返回 unknown。"
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
            "wait_seconds": {
                "type": "integer",
                "minimum": EXEC_MIN_STATUS_WAIT_SECONDS,
                "maximum": EXEC_MAX_STATUS_WAIT_SECONDS,
                "description": "运行中最多等待进入终态或窗口结束的秒数；默认 0，continuation 通常使用 30。",
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
            "output_ref": {
                "type": _null_or_string(),
                "maxLength": EXEC_RUN_ID_MAX_CHARS + 16,
                "description": "完整输出已真实保存时返回的不透明引用。",
            },
            "reason": {"type": "string"},
            "started_at": {"type": _null_or_number(), "description": "命令启动 epoch 秒。"},
            "finished_at": {"type": _null_or_number(), "description": "命令进入终态的 epoch 秒。"},
            "observed_at": {"type": _null_or_number(), "description": "本次状态查询 epoch 秒。"},
            "generated_resources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string"},
                        "name": {"type": "string"},
                        "media_type": {"type": "string"},
                        "size_bytes": {"type": "integer"},
                        "sha256": {"type": "string"},
                    },
                    "required": ["handle"],
                    "additionalProperties": False,
                },
                "description": "该 run 按 output_globs 登记且幂等返回的 gen_* 资源，不含绝对路径。",
            },
            "artifact_status": {
                "type": "string",
                "enum": [
                    ARTIFACT_STATUS_REGISTERED,
                    ARTIFACT_STATUS_REGISTRATION_FAILED,
                    ARTIFACT_STATUS_NOT_REGISTERED,
                    ARTIFACT_STATUS_NOT_REQUESTED,
                ],
            },
            "artifact_reason": {
                "type": "string",
                "description": "输出未完整登记时的结构化原因；为空表示没有额外登记错误。",
            },
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


EXEC_INPUT_TOOL_SPEC = CapabilityToolSpec(
    capability_id="exec_input", display_name="Write to a running command",
    description="对 interactive=true 启动的命令写入 UTF-8 输入或关闭 stdin。action=status 查询上次写入；write 的 sequence 从 1 递增，重试同一序号不会重复输入。pending 时先查询；written 只表示写入管道，程序结果用 exec_status 核验。不是 PTY，停止进程用 exec_cancel。",
    input_schema={"type": "object", "properties": {
        "run_id": {"type": "string", "maxLength": EXEC_RUN_ID_MAX_CHARS},
        "action": {"type": "string", "enum": ["write", "status"]},
        "sequence": {"type": "integer", "minimum": 1},
        "text": {"type": "string", "maxLength": 16384},
        "close": {"type": "boolean", "description": "Close stdin after this write to deliver EOF."},
    }, "required": ["run_id", "action"], "additionalProperties": False},
    output_schema={"type": "object", "additionalProperties": True},
    risk="high", confirm="always", effects=("command_exec",), visible_in=("desktop", "qq"),
    execution_class="sync", idempotency="effectful", max_result_bytes=4096,
)

EXEC_TOOL_SPECS: tuple[CapabilityToolSpec, ...] = (
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_TOOL_SPEC,
    EXEC_CANCEL_TOOL_SPEC,
    EXEC_INPUT_TOOL_SPEC,
)
EXEC_TOOL_SPEC_BY_ID: dict[str, CapabilityToolSpec] = {
    spec.capability_id: spec for spec in EXEC_TOOL_SPECS
}


def exec_tool_spec(tool_id: str) -> CapabilityToolSpec | None:
    return EXEC_TOOL_SPEC_BY_ID.get(str(tool_id or "").strip())
