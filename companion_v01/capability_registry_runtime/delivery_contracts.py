"""Existing-file handoff and QQ native-message delivery tool contracts."""
from __future__ import annotations

from capcore import CapabilityToolSpec
from companion_v01.channels.qq.onebot.model_actions import MODEL_ONEBOT_ACTION_NAMES
from companion_v01.channels.qq.onebot.model_actions import MODEL_ONEBOT_MESSAGE_SELECTOR_KINDS
from ..tool_continuation import FINISH_TURN_PARAMETER


SEND_FILE_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_file",
    display_name="Send file",
    description=(
        "把已经存在的工作台材料或生成文件真正交付给用户。支持 file_*、img_*、audio_*、gen_*。"
        "工具结果或上下文已经给出句柄时，必须传入那个精确句柄，不要改用 latest；"
        "latest 只适合确实不知道句柄、且用户笼统指向最近文件的情况。"
        "若只知道文件类型，可用 latest_generated 或 latest_attachment，避免在生成物和用户附件之间选错。"
        "只有实际文件路径时，传 path（可选 cwd）一次完成原样登记和发送；无需复制到当前工作区。"
        "相对路径以调用任务/项目目录为准，绝对路径省略 cwd，且必须属于已授权目录。"
        "文档、图片、音视频、压缩包及未知扩展名均可按文件交付；不保证预览、播放或转码。"
        "它不会生成、修改或转码文件；只有工具成功结果才能证明文件已经进入客户端投递队列。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {
                "type": "string",
                "maxLength": 120,
                "description": (
                    "Single exact file handle (preferred), or latest/latest_generated/latest_attachment "
                    "only when no exact handle is known."
                ),
            },
            "targets": {
                "type": "array",
                "items": {"type": "string", "maxLength": 120},
                "maxItems": 10,
                "description": "One or more exact file handles. Reuse handles returned by prior tools.",
            },
            "path": {
                "type": "string", "minLength": 1, "maxLength": 4096,
                "description": "Existing file in an authorized project; register unchanged and queue for delivery. Use instead of target/targets only when no handle exists.",
            },
            "cwd": {
                "type": "string", "maxLength": 4096,
                "description": "Base directory for relative path; defaults to calling task/current project. Omit for absolute path. Never guess a plugin's process directory.",
            },
            "delivery_action": {
                "type": "string",
                "enum": ["open", "reveal", "save_desktop", "copy_path"],
                "description": "仅桌宠模式按用户明确要求打开、定位、保存到桌面或复制路径；QQ 发送时省略。",
            },
        },
        "required": [],
    },
    risk="medium",
    confirm="first_time",
    effects=("file_delivery",),
    visible_in=("desktop", "qq"),
    spec_version="1.3.0",
    schema_version=4,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

SEND_STICKER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_sticker",
    display_name="Send sticker",
    description="Send one available static sticker in the current QQ conversation.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sticker": {
                "type": "string",
                "maxLength": 80,
                "description": "Exact sticker id from the available sticker list in the current prompt.",
            },
        },
        "required": ["sticker"],
    },
    risk="low",
    confirm="never",
    effects=("sticker_delivery",),
    visible_in=("qq",),
    spec_version="2.0.0",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=2048,
)

ONEBOT_ACTION_TOOL_SPEC = CapabilityToolSpec(
    capability_id="onebot_action",
    display_name="OneBot action",
    description=(
        "通过当前 Bot 执行 QQ/NapCat 动作。"
        "当前会话正常接话请按本轮最终回复格式输出，系统会发送并分句；引用、@ 或分段也照常回复，不用 send_group_msg/send_private_msg。"
        "消息发送动作用于主人交代的跨群/跨私聊传话，或原生卡片、媒体等特殊投递；仅给文字加 reply/at 段不算特殊投递。"
        "其余动作可读取历史、转发、戳一戳、表情回应、点赞或查询成员；撤回仅限主人。"
        "参数不清楚时先查 capabilities，params 填对应动作参数（查询 capabilities 时为 {}）。"
        "语义明确时可省略当前群、私聊对象、发送者或当前消息 ID；普通成员限当前会话，跨会话需主人，凭据和账号/群管理不开放。"
        "需要 message_id 时，可用 message_selector 指向当前消息、引用消息或第 N 条最近成功发送的 Bot 消息。"
        "以 ok/status/reason 为准；成功发出的文字不再重复。只有动作已完成本轮回复且无需再说话时才设 finish_turn=true。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": list(MODEL_ONEBOT_ACTION_NAMES),
                "description": "One exposed action name; use capabilities to inspect parameter hints and policy.",
            },
            "params": {
                "type": "object",
                "additionalProperties": True,
                "description": "Exact OneBot request object for the chosen action; use {} for capabilities.",
            },
            "finish_turn": dict(FINISH_TURN_PARAMETER),
            "message_selector": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(MODEL_ONEBOT_MESSAGE_SELECTOR_KINDS),
                        "description": "Resolve a real message_id from the current QQ conversation.",
                    },
                    "position": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 64,
                        "description": "For recent_bot_message only; 1 is the latest successful Bot message.",
                    },
                },
                "required": ["kind"],
                "description": "Optional host-side message reference; omit when params.message_id is explicit.",
            },
        },
        "required": ["action", "params"],
    },
    risk="medium",
    confirm="never",
    effects=("qq_interaction",),
    visible_in=("qq",),
    spec_version="1.3.1",
    schema_version=2,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=512 * 1024,
)
SEND_MUSIC_CARD_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_music_card",
    display_name="Send music card",
    description=(
        "把网易云歌曲作为原生音乐卡片交付给当前 QQ 会话。"
        "track_id 是网易云纯数字歌曲 ID。"
        "track_id 必须是本工具结果、用户输入或已展开工具轨迹中真实出现的精确 ID，严禁根据歌名猜测或编造。"
        "本工具只尝试原生音乐卡片，并把 QQ 的真实传输结果直接返回模型；不会暗中改发语音。"
        "卡片失败后，模型可依据结果决定是否寻找公开音频 URL，再调用 send_audio。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["netease_music"],
                "description": "原生卡片平台；当前仅支持网易云。",
            },
            "track_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "精确的网易云纯数字歌曲 ID。",
            },
        },
        "required": ["platform", "track_id"],
    },
    risk="low",
    confirm="never",
    effects=("music_card_delivery",),
    visible_in=("qq",),
    spec_version="2.0.0",
    schema_version=5,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)

SEND_AUDIO_TOOL_SPEC = CapabilityToolSpec(
    capability_id="send_audio",
    display_name="Send audio",
    description=(
        "把一段真实音频交付到当前 QQ 会话。source 可以是当前会话已有的 audio_*/file_*/gen_* 音频句柄，"
        "也可以是公开 HTTP(S) 音频直链；平台名称和歌曲 ID 不是语音交付参数。"
        "本工具只发送可直接播放的 QQ 语音；需要普通文件时另用 send_file，需要两种表现时可并行调用两项工具。"
        "工具会返回真实传输结果，"
        "失败不会导致回合终止，也不会被系统改成另一种交付方式。"
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
                "description": "精确音频句柄（如 audio_001/gen_001）或公开 HTTP(S) 音频 URL。",
            },
            "name": {
                "type": "string",
                "maxLength": 160,
                "description": "可选的人类可读音频名称；不用于定位本地文件。",
            },
        },
        "required": ["source"],
    },
    risk="low",
    confirm="never",
    effects=("audio_delivery",),
    visible_in=("qq",),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)
