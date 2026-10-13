"""One contract for both native tools and legacy tool prompts."""
from capcore import CapabilityToolSpec

from companion_v01.rendering.text_cards import MAX_BODY_CHARS

RENDER_CARD_TOOL_SPEC = CapabilityToolSpec(
    capability_id="render_card",
    display_name="图文卡片",
    description=(
        "把你写好的文字排成角色主题图卡，适合小记、信笺、回顾或清单。"
        "只需标题和正文；正文用空行分段，可用 # 小标题和 > 引用。"
        "整篇渲染成一张长图，已有图片可用 image_id 插入。"
        "生成后自动加入当前客户端的发送队列，无需再调用 send_file；回执中的 gen_* 可用于回看或以后补发。"
    ),
    input_schema={
        "type": "object", "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 24, "description": "简短标题。"},
            "body": {"type": "string", "minLength": 1, "maxLength": MAX_BODY_CHARS,
                     "description": "完整正文；用换行和空行安排段落，不用写排版代码。"},
            "subtitle": {"type": "string", "maxLength": 48, "description": "可选的简短副标题或日期。"},
            "image_id": {"type": "string", "maxLength": 120,
                         "description": "可选的当前会话图片句柄，如 img_001 或 gen_002。"},
        },
        "required": ["title", "body"],
    },
    risk="low", confirm="never", effects=("file_write", "message_send"),
    visible_in=("desktop", "qq"), execution_class="sync", idempotency="idempotent",
    max_result_bytes=8192,
)
