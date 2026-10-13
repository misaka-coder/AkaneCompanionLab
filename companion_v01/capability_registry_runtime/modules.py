"""Ordered product capability profiles and their context activation rules."""
from __future__ import annotations

from ..client_protocol import ClientMode
from .models import CapabilityModule
from .context import (
    _always,
    _has_any_attachment,
    _has_document_context,
    _has_media_context,
    _execution_enabled,
    _execution_qq_enabled,
    _has_image_context,
    _has_generated_file,
    _has_deliverable_file,
)


COMMON_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D, ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)
CHAT_FILE_CLIENT_MODES = (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)

COMMON_TOOL_NAMES = (
    "retrieve_memory",
    "read_memory_timeline",
    "browse_memory",
    "open_memory",
    "list_memory_conversations",
    "load_skill",
    "load_mcp",
    "invoke_mcp",
    "load_character_context",
)

WEB_SEARCH_TOOL_NAMES = ("web_search",)
EXEC_TOOL_NAMES = (
    "spawn_subagent",
    "manage_subagents",
    "manage_project_workspace",
    "project_inspect",
    "workspace_write",
    "workspace_patch",
    "exec_run",
    "exec_status",
    "exec_cancel",
    "exec_input",
    "manage_generated_file",
    "manage_skill",
    "mcp_manage",
)
EXTENSION_MANAGEMENT_TOOL_NAMES = ("manage_extension",)
DESKTOP_BROWSER_TOOL_NAMES = ("browser_page",)
DESKTOP_MUSIC_REQUEST_TOOL_NAMES = ("open_music_search",)
DESKTOP_WORKSPACE_TOOL_NAMES = (
    "list_workspace",
    "read_workspace",
    "register_workspace_items",
)

REMOTE_MEDIA_TOOL_NAMES = ("fetch_media_from_url",)

ATTACHMENT_WORKSPACE_TOOL_NAMES = (
    "inspect_attachment",
    "retry_attachment",
    "clear_attachment_focus",
)

IMAGE_MATERIAL_TOOL_NAMES = ("load_material",)

DOCUMENT_WORKBENCH_TOOL_NAMES = (
    "read_attachment_section",
)

MEDIA_WORKBENCH_TOOL_NAMES = (
    "inspect_media_info",
)

# M68: Shell may replace built-in inspection. Installed extension descriptors
# remain independently discoverable; they are never hidden by this profile.
MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES = ("inspect_media_info",)

GENERATED_FILE_MANAGEMENT_TOOL_NAMES = (
    "inspect_generated_file",
    "manage_generated_file",
)

FILE_HANDOFF_TOOL_NAMES = ("send_file",)
TEXT_CARD_TOOL_NAMES = ("render_card",)
QQ_STICKER_TOOL_NAMES = ("send_sticker",)
QQ_ONEBOT_ACTION_TOOL_NAMES = ("onebot_action",)
QQ_MUSIC_CARD_TOOL_NAMES = ("send_music_card",)
QQ_AUDIO_DELIVERY_TOOL_NAMES = ("send_audio",)


def build_default_modules() -> tuple[CapabilityModule, ...]:
    return (
        CapabilityModule(
            name="base",
            layer="common",
            modes=COMMON_CLIENT_MODES,
            tools=COMMON_TOOL_NAMES,
            light_hint="记忆：browse_memory 按时间、关键词和参与者浏览目录；open_memory 用返回的 memory_id 或 match_ref 展开正文与原话；read_memory_timeline 读取已知时段的原始时间线，并按返回的游标续页。回答旧事实、偏好与约定时以实际读到的证据为准，目录未命中不代表没有记录。Skills：任务匹配目录说明时用 load_skill 加载手册。",
            trigger=_always,
        ),
        CapabilityModule(
            name="internet_access",
            layer="web",
            modes=COMMON_CLIENT_MODES,
            tools=WEB_SEARCH_TOOL_NAMES,
            light_hint="web_search 检索当前、实时或需要公开来源验证的信息，例如指数、新番目录和模型价格；它只访问公开互联网，私密、内网和登录资源会返回不可用。",
            trigger=_always,
            unavailable_reason="联网搜索服务当前正在检测，或没有通过所在网络节点的可用性检查。",
            recovery_hint="网络或搜索服务恢复后会自动重新开放；恢复前可基于已有稳定知识回答，并明确实时信息尚未核验。",
        ),
        CapabilityModule(
            name="execution",
            layer="execution",
            modes=(ClientMode.DESKTOP_PET,),
            tools=EXEC_TOOL_NAMES,
            light_hint=(
                "执行宿主上的命令与项目工具：project_inspect、workspace_write、workspace_patch 与 exec_run 共用 cwd，"
                "可在已发现的真实目录读取、修改、构建和测试；manage_project_workspace 的 create/open/select 设置当前项目，"
                "之后四个工具省略 cwd 时会继续在该项目工作。"
                "exec_status 查询长命令进度，exec_cancel 停止命令。"
            ),
            trigger=_execution_enabled,
            unavailable_reason="本机执行提供者当前没有通过可用性检查。",
            recovery_hint="执行工作区或提供者恢复后会自动重新开放；当前不要假装已经执行命令。",
        ),
        CapabilityModule(
            name="execution_qq",
            layer="execution",
            modes=(ClientMode.QQ_TEXT,),
            tools=EXEC_TOOL_NAMES,
            light_hint=(
                "当前 QQ 会话已由主人开放 Shell；project_inspect、workspace_write、workspace_patch 与 exec_run 共用 cwd，"
                "可在 QQ Bot 后端所在机器的已发现真实目录中读取、修改、构建和测试；manage_project_workspace 的 create/open/select 设置当前项目，"
                "之后四个工具省略 cwd 时会继续在该项目工作。"
                "exec_status 查询长命令进度，exec_cancel 停止命令；/权限 本机 查看当前操作权限。"
            ),
            trigger=_execution_qq_enabled,
            unavailable_reason="当前 QQ 会话的 Shell 没有开放，或执行提供者没有通过可用性检查。",
            recovery_hint="主人可在当前私聊或群聊发送 /权限 本机 开；当前不要假装已经执行命令。",
        ),
        CapabilityModule(
            name="extension_management",
            layer="extension",
            modes=(ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT),
            tools=EXTENSION_MANAGEMENT_TOOL_NAMES,
            light_hint="manage_extension 查看、暂存、安装、启停、回滚或移除本机插件；只有主人可以修改。",
            trigger=_always,
        ),
        CapabilityModule(
            name="desktop_managed_browser",
            layer="desktop_browser",
            modes=(ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT),
            tools=DESKTOP_BROWSER_TOOL_NAMES,
            light_hint="browser_page 会打开并操作当前 Akane 宿主的可见托管浏览器窗口，用于读取、滚动、按可见候选序号打开链接，以及经授权的点击/输入；公开下载可通过 download_status 查询并进入材料工作台。个人已登录 Chrome 使用 session_source=personal_chrome，通过绑定电脑完成连接、list_tabs/select_tab；需要本机开启及 Chrome 许可。切换 computer_use 前 handoff，交接后重新观察。",
            trigger=_always,
        ),
        CapabilityModule(
            name="desktop_music_request",
            layer="music_request",
            modes=(ClientMode.DESKTOP_PET,),
            tools=DESKTOP_MUSIC_REQUEST_TOOL_NAMES,
            light_hint="桌宠模式下，当用户明确要点歌或搜索一首歌来听时，可以用 open_music_search 打开公开音乐平台搜索页；它不代表已经播放成功，后续点击/输入仍按浏览器授权边界处理。",
            trigger=_always,
        ),
        CapabilityModule(
            name="desktop_file_workspace",
            layer="desktop_workspace",
            modes=(ClientMode.DESKTOP_PET,),
            tools=DESKTOP_WORKSPACE_TOOL_NAMES,
            light_hint="桌宠模式下，你始终拥有一个可主动查询的 Akane 文件工作区。用户提到刚放入、寻找、处理或清理某个文件时，先从 workspace:/ 调用 list_workspace 查询，不要先让用户提供本机绝对路径；你也可以批量读取、聚焦材料，并把文件原地登记为文档或媒体工具可用的附件 handle。",
            trigger=_always,
        ),
        CapabilityModule(
            name="remote_media_fetch",
            layer="shared_media",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=REMOTE_MEDIA_TOOL_NAMES,
            light_hint="你也可以先把公开音频/视频链接下载进当前工作台；如果用户只要原视频/原音频，下载后直接交付原文件，不要多做转写、转码或净化。",
            trigger=_always,
        ),
        CapabilityModule(
            name="attachment_workspace",
            layer="shared_attachment_workspace",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=ATTACHMENT_WORKSPACE_TOOL_NAMES,
            light_hint="你可以接收临时图片和文件，并整理当前工作台；文件交付由当前客户端自己的文件交付层处理。",
            trigger=_has_any_attachment,
        ),
        CapabilityModule(
            name="image_material_reload",
            layer="shared_image_material",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=IMAGE_MATERIAL_TOOL_NAMES,
            light_hint="load_material 按 handle 把当前会话较早原图或生成图的像素送入视觉回合；inspect_attachment 用于先确认材料及其 handle。",
            trigger=_has_image_context,
            latent_reason="当前会话还没有可重新加载的图片材料。",
            activation_hint="用户上传图片或生成一张图片后，这项材料读取能力会自动开放。",
        ),
        CapabilityModule(
            name="qq_file_delivery",
            layer="qq_delivery",
            modes=(ClientMode.QQ_TEXT,),
            tools=FILE_HANDOFF_TOOL_NAMES,
            light_hint="在 QQ 里，你可以把已有工作台材料或生成文件发回给用户；只发送已有文件，不替代生成、转码或修改。",
            trigger=_has_deliverable_file,
        ),
        CapabilityModule(
            name="desktop_file_handoff",
            layer="desktop_workspace",
            modes=(ClientMode.DESKTOP_PET,),
            tools=FILE_HANDOFF_TOOL_NAMES,
            light_hint="在桌宠里，你可以把已有工作台材料或生成文件交给桌宠工作台打开、播放或继续处理；只交付已有文件，不替代生成、转码或修改。",
            trigger=_has_deliverable_file,
        ),
        CapabilityModule(
            name="sticker_pack",
            layer="qq_delivery",
            modes=(ClientMode.QQ_TEXT,),
            tools=QQ_STICKER_TOOL_NAMES,
            light_hint="你有一组静态表情包；聊天氛围适合时可以发送一张表情包，但不要为了展示功能而频繁发送。",
            trigger=_always,
        ),
        CapabilityModule(
            name="qq_onebot_actions",
            layer="qq_delivery",
            modes=(ClientMode.QQ_TEXT,),
            tools=QQ_ONEBOT_ACTION_TOOL_NAMES,
            light_hint=(
                "QQ 中需要读取聊天记录、投递原生卡片或合并转发、戳一戳、回应消息表情、点赞、"
                "查询成员信息或撤回消息时用 onebot_action；不确定参数先查 capabilities。"
                "当前群、当前私聊对象、当前发送者或当前消息可在语义明确时由宿主补全；"
                "普通成员限当前会话，跨群/跨私聊与撤回需要主人，工具结果是真实 OneBot 回执。"
            ),
            trigger=_always,
        ),
        CapabilityModule(
            name="qq_native_music_card",
            layer="qq_delivery",
            modes=(ClientMode.QQ_TEXT,),
            tools=QQ_MUSIC_CARD_TOOL_NAMES,
            light_hint="在 QQ 会话里，你可以把已确认歌曲 ID 的网易云歌曲作为原生音乐卡片发送；工具会返回真实成败，不会自动改发语音。",
            trigger=_always,
        ),
        CapabilityModule(
            name="qq_audio_delivery",
            layer="qq_delivery",
            modes=(ClientMode.QQ_TEXT,),
            tools=QQ_AUDIO_DELIVERY_TOOL_NAMES,
            light_hint="在 QQ 会话里，你可以把公开音频直链或已有音频句柄发送为 QQ 语音；普通文件仍使用 send_file。",
            trigger=_always,
        ),
        CapabilityModule(
            name="document_workbench",
            layer="shared_document",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=DOCUMENT_WORKBENCH_TOOL_NAMES,
            light_hint="你可以分段读取当前会话中的文本、Office、PDF 等文档材料。",
            trigger=_has_document_context,
            latent_reason="当前会话和可见工作区里还没有可处理的文档材料，因此没有展开文档读取工具。",
            activation_hint="用户上传文档，或在桌宠的 Akane 工作区放入文档后会自动开放；若工作区文件尚无 handle，先登记再继续处理。",
            unavailable_reason="文档处理组件当前没有通过可用性检查。",
            recovery_hint="文档组件恢复后会自动重新开放；已有材料无需重复上传。",
        ),
        CapabilityModule(
            name="media_workbench",
            layer="shared_media",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=MEDIA_WORKBENCH_TOOL_NAMES,
            light_hint="你可以使用当前可用的媒体工具处理素材；处理工具返回成果句柄后，再按用户要求调用 send_file 交付。",
            trigger=_has_media_context,
            latent_reason="当前会话和可见工作区里还没有可处理的音频或视频，因此没有展开媒体处理工具。",
            activation_hint="用户上传音频/视频、提供可下载的公开媒体链接，或在桌宠的 Akane 工作区放入媒体文件后会自动开放；工作区文件可先登记为 handle。",
            unavailable_reason="媒体处理所需的本地组件当前没有通过可用性检查。",
            recovery_hint="媒体组件恢复后会自动重新开放；已有材料无需重复上传。",
        ),
        CapabilityModule(
            name="text_card_authoring",
            layer="shared_file_authoring",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=TEXT_CARD_TOOL_NAMES,
            light_hint="render_card 把写好的文字排成一张角色主题长图，支持已有图片，生成后自动交付。",
            trigger=_always,
            unavailable_reason="图卡排版组件当前不可用。",
            recovery_hint="可以先用文字回应。",
        ),
        CapabilityModule(
            name="generated_file_management",
            layer="shared_file_authoring",
            modes=CHAT_FILE_CLIENT_MODES,
            tools=GENERATED_FILE_MANAGEMENT_TOOL_NAMES,
            light_hint="你可以回看、交付、归档、删除或清理自己刚生成的文件。",
            trigger=_has_generated_file,
        ),
    )
