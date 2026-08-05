# config.py - 双核适配版（修正版）
import logging
import os
import re
from pathlib import Path

from pydantic_settings import BaseSettings
from akane_paths import ensure_akane_data_paths, has_explicit_akane_data_root

logger = logging.getLogger("akane.config")

BASE_DIR = Path(__file__).resolve().parent
AKANE_DATA_ROOT_EXPLICIT = has_explicit_akane_data_root()
AKANE_DATA_PATHS = ensure_akane_data_paths()
DATA_ROOT = str(AKANE_DATA_PATHS.root)
DATA_DIR = str(AKANE_DATA_PATHS.users_data)
CHARACTERS_DIR = str(AKANE_DATA_PATHS.characters)
STATE_DIR = str(AKANE_DATA_PATHS.state)
LOG_DIR = str(AKANE_DATA_PATHS.logs)
WORKSPACE_DIR = str(AKANE_DATA_PATHS.workspace)
CACHE_DIR = str(AKANE_DATA_PATHS.cache)
RUN_DIR = str(AKANE_DATA_PATHS.run)
DEFAULT_EMBEDDING_MODEL_NAME = "BAAI/bge-m3"


class Settings(BaseSettings):
    # === 运行模式 & 人设 ===
    RUN_MODE: str = "CLOUD"
    # 启动级实例选择器；留空保持 M65 前的 local-default 行为
    AKANE_INSTANCE_ID: str = ""
    # 自定义人设 TOML 文件路径（留空=内置默认）
    PERSONA_CONFIG_PATH: str = ""
    # 人设文件中的 variant 名（对应 TOML [variants.xxx]）
    PERSONA_VARIANT: str = "default"

    # === Embedding / 向量记忆 ===
    # 提供者：auto=huggingface→hashed 自动回退  huggingface  remote  jina  hashed
    EMBEDDING_PROVIDER: str = "auto"
    # 模型名或 HuggingFace 本地模型目录
    EMBEDDING_MODEL_NAME: str = DEFAULT_EMBEDDING_MODEL_NAME
    # 远程 Embedding API；仅远程 provider 使用
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_TIMEOUT_SECONDS: float = 30.0
    # 设备：空=自动  cuda  cpu
    EMBEDDING_DEVICE: str = ""
    # 只使用本地缓存/本地模型目录，避免启动时联网下载
    EMBEDDING_LOCAL_FILES_ONLY: bool = True
    # HuggingFace/sentence-transformers 缓存目录（留空=默认缓存）
    EMBEDDING_CACHE_FOLDER: str = ""
    # Embedding 模型缓存大小
    EMBEDDING_CACHE_SIZE: int = 2048
    # 重建索引时的批量大小
    EMBEDDING_REINDEX_BATCH_SIZE: int = 64
    # 启用语义记忆（长期事实/话题抽取）
    ENABLE_SEMANTIC_MEMORY: bool = True
    # 启用语义强化（跨会话话题追踪）
    ENABLE_SEMANTIC_REINFORCEMENT: bool = True

    # === 检索 / Prompt 缓存 ===
    # 预检索默认开关（每个会话开始时是否先跑检索）
    PRE_RETRIEVAL_DEFAULT_ENABLED: bool = True
    # 启用 prompt cache hints（需模型支持）
    PROMPT_CACHE_HINTS_ENABLED: bool = True
    # 强制开启 cache hints（即使模型不在已知支持列表）
    PROMPT_CACHE_HINTS_FORCE: bool = False
    # 缓存命名空间（区分不同实例，避免 key 冲突）
    PROMPT_CACHE_NAMESPACE: str = "akane"
    # 缓存保留策略：空=默认  ephemeral=短期  persistent=长期
    PROMPT_CACHE_RETENTION: str = ""
    # Responses API controls. These are deliberately separate from the legacy
    # DeepSeek thinking switch because they have different wire semantics.
    LLM_REASONING_EFFORT: str = ""
    LLM_AUX_REASONING_EFFORT: str = ""
    LLM_CHAT_REASONING_EFFORT: str = ""
    LLM_DISABLE_RESPONSE_STORAGE: bool = True
    LLM_CONTEXT_WINDOW: int = 0
    LLM_AUTO_COMPACT_TOKEN_LIMIT: int = 0
    # 记录最终回复 prompt section 的长度/hash 审计日志（不记录原文）
    LLM_PROMPT_AUDIT_ENABLED: bool = False
    # 开启后也记录辅助 LLM 调用；默认只记录 chat:final，避免日志噪声
    LLM_PROMPT_AUDIT_INCLUDE_AUX: bool = False

    # === 调试开关（默认关闭，开启后 /think 日志输出详细 JSON）===
    ROUTER_DEBUG: bool = False
    VERIFIER_DEBUG: bool = False
    FINAL_DEBUG: bool = False

    # === 记忆参数 ===
    # "走神"概率——偶尔插入不相关检索让回复更自然（0.0 ~ 1.0）
    DRIFT_PROBABILITY: float = 0.20
    # 触发摘要的累积消息数
    SUMMARY_TRIGGER_COUNT: int = 30
    # 摘要批处理大小
    SUMMARY_BATCH_SIZE: int = 20
    # 最近可见摘要数量
    RECENT_SUMMARY_LIMIT: int = 5
    # 情节记忆压缩触发阈值（条数）
    EPISODIC_COMPACT_TRIGGER_COUNT: int = 10
    # 情节记忆压缩批处理大小
    EPISODIC_COMPACT_BATCH_SIZE: int = 5
    # 可见情节记忆最大条数
    EPISODIC_VISIBLE_MAX: int = 10
    # 可见语义记忆最大条数（过多=prompt 太长）
    SEMANTIC_VISIBLE_LIMIT: int = 3
    # 语义强化回溯窗口（跨会话）
    SEMANTIC_REINFORCEMENT_LOOKBACK: int = 8
    # 语义强化最小重叠次数（低于此数不强化）
    SEMANTIC_REINFORCEMENT_MIN_OVERLAP: int = 2
    # 记忆后端：memcore=主记忆系统  legacy=旧系统兼容  dual=迁移/对比用双写
    MEMORY_BACKEND: str = "memcore"
    # memcore SQLite 路径；留空时使用 base_dir / memcore_v01.db
    MEMCORE_STORAGE_PATH: str = ""
    # memcore 可见长期记忆作用域：conversation/user
    MEMCORE_VISIBLE_SCOPE: str = "user"
    # memcore mood/flavor 温度层；桌宠陪伴默认开启
    MEMCORE_ENABLE_FLAVOR: bool = True
    # 影子检索对比开关；只记录结构化统计，不改变用户可见回复
    MEMCORE_SHADOW_COMPARE: bool = False
    # MemCore V2 raw projection 达到多少 tokens 后触发差值压缩
    MEMCORE_RAW_TOKEN_TRIGGER: int = 48000
    # 触发后计划压缩的旧 raw token 比例；实际切点对齐完整 turn/component
    MEMCORE_RAW_TOKEN_BATCH_RATIO: float = 0.67
    # 单次结构化检索结果可回填给模型的 token 预算；0 表示不设预算
    MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET: int = 0
    # 原生时间线单页 token 上限；MemCore 按完整逻辑单元分页，0/省略模型参数使用此值
    MEMCORE_NATIVE_TIMELINE_PAGE_TOKEN_BUDGET: int = 12000
    # 后台压缩全局 worker 数；与用户聊天/工具并行度无关
    MEMCORE_COMPACTION_WORKERS: int = 1
    # 一次压缩重试仍失败后，同 namespace 暂停后台摘要的秒数
    MEMCORE_COMPACTION_FAILURE_COOLDOWN_SECONDS: float = 60.0
    # === LLM 密钥 & 接入 ===
    # 键位角色：
    #   TEXT   = 辅助任务（路由判断、记忆总结、时间解析等）[必填，至少有一个 key]
    #   CHAT   = 聊天对话（缺失时回退到 TEXT）
    #   AUX    = 额外辅助（缺失时回退到 TEXT）
    #   VISION = 视觉/图像理解（缺失时回退到 CHAT → TEXT）
    # 协议设为 "auto" 时根据 base_url 自动探测（openai / anthropic / ollama）。

    # --- TEXT（辅助任务）---
    TEXT_API_KEY: str = ""
    TEXT_BASE_URL: str = ""
    TEXT_MODEL_NAME: str = "deepseek-chat"
    TEXT_API_PROTOCOL: str = "auto"

    # --- AUX（额外辅助，缺失回退 TEXT）---
    AUX_API_KEY: str = ""
    AUX_BASE_URL: str = ""
    AUX_MODEL_NAME: str = "deepseek-chat"
    AUX_API_PROTOCOL: str = "auto"

    # --- CHAT（聊天对话，缺失回退 TEXT）---
    CHAT_API_KEY: str = ""
    CHAT_BASE_URL: str = ""
    CHAT_MODEL_NAME: str = ""
    CHAT_API_PROTOCOL: str = "auto"
    # DeepSeek 等 OpenAI 兼容模型的思考模式控制：
    # default/空 = 不传参数，沿用服务商默认；disabled = 关闭；enabled = 开启。
    LLM_THINKING_MODE: str = "disabled"

    # === 视觉 / 图像理解 ===
    VISION_API_KEY: str = ""
    VISION_BASE_URL: str = ""
    VISION_MODEL_NAME: str = ""
    VISION_API_PROTOCOL: str = "auto"
    # 视觉功能总开关
    VISION_ENABLED: bool = True
    # 单次视觉请求超时（秒）
    VISION_REQUEST_TIMEOUT: float = 60.0
    # 视觉 prompt 版本
    VISION_PROMPT_VERSION: str = "v1"
    # 是否自动对场景触发视觉观察
    VISION_AUTO_SCENE_OBSERVE: bool = True
    # 是否自动对礼物触发视觉观察
    VISION_AUTO_GIFT_OBSERVE: bool = True
    # 是否自动对装扮触发视觉观察
    VISION_AUTO_OUTFIT_OBSERVE: bool = True
    # 单张图片最大字节数（默认 8 MB）
    VISION_MAX_IMAGE_BYTES: int = 8 * 1024 * 1024

    # === 云端图像生成 / 编辑 ===
    IMAGE_GENERATION_ENABLED: bool = False
    IMAGE_GENERATION_BASE_URL: str = "https://api.pinaic.com/v1"
    IMAGE_GENERATION_API_KEY: str = ""
    IMAGE_GENERATION_MODEL: str = "gpt-image-2"
    IMAGE_GENERATION_TIMEOUT_SECONDS: float = 300.0
    IMAGE_GENERATION_MAX_INPUT_IMAGES: int = 5
    IMAGE_GENERATION_MAX_OUTPUT_IMAGES: int = 4
    IMAGE_GENERATION_MAX_IMAGE_BYTES: int = 8 * 1024 * 1024
    IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES: int = 20 * 1024 * 1024
    IMAGE_GENERATION_MAX_OUTPUT_BYTES: int = 25 * 1024 * 1024

    # === 本地 RVC 自动翻唱 ===
    COVER_SONG_ENABLED: bool = False
    RVC_WEBUI_BASE_URL: str = "http://127.0.0.1:7899"
    RVC_ROOT_DIR: str = ""
    RVC_DEFAULT_MODEL: str = ""
    COVER_SONG_SEPARATION_MODEL: str = "HP5_only_main_vocal"
    COVER_SONG_DEFAULT_OUTPUT_FORMAT: str = "mp3"
    COVER_SONG_DEFAULT_DELIVERY: str = "auto"
    COVER_SONG_TIMEOUT_SECONDS: float = 1800.0
    COVER_SONG_MAX_DURATION_SECONDS: float = 900.0
    COVER_SONG_MAX_INPUT_BYTES: int = 256 * 1024 * 1024
    # 本机媒体能力宿主经 SSH reverse tunnel 映射到云端 loopback。
    # 留空时保持原有云端本地执行路径，不产生隐式远程调用。
    LOCAL_MEDIA_EXECUTOR_BASE_URL: str = ""
    LOCAL_MEDIA_EXECUTOR_TIMEOUT_SECONDS: float = 1800.0

    # === 语音 (TTS) ===
    # Edge TTS 语音角色
    TTS_VOICE: str = "zh-CN-XiaoxiaoNeural"
    # 语速（相对百分比）
    TTS_RATE: str = "+6%"
    # 音量
    TTS_VOLUME: str = "+0%"
    # 音调
    TTS_PITCH: str = "+4Hz"
    # 流式 TTS（边生成边播放）
    STREAMING_TTS_ENABLED: bool = True
    # GPT-SoVITS 外部 API 默认参数（角色 voice profile 可覆盖部分字段）
    GPT_SOVITS_TTS_TIMEOUT_SECONDS: float = 45.0
    GPT_SOVITS_TEXT_LANG: str = "zh"
    GPT_SOVITS_MEDIA_TYPE: str = "wav"
    GPT_SOVITS_STREAMING_MODE: bool = False
    GPT_SOVITS_PARALLEL_INFER: bool | None = None
    GPT_SOVITS_SPLIT_BUCKET: bool | None = None
    GPT_SOVITS_BATCH_SIZE: int | None = None
    GPT_SOVITS_SPEED_FACTOR: float | None = None
    GPT_SOVITS_FRAGMENT_INTERVAL: float | None = None
    GPT_SOVITS_TEXT_SPLIT_METHOD: str = ""

    # === 实时 ASR（VoiceCore，默认关闭）===
    FUN_ASR_REALTIME_ENABLED: bool = False
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_API_HOST: str = ""
    FUN_ASR_REALTIME_MODEL: str = "fun-asr-realtime"
    FUN_ASR_AUDIO_FORMAT: str = "pcm"
    FUN_ASR_SAMPLE_RATE: int = 16000
    FUN_ASR_MAX_SENTENCE_SILENCE: int = 800
    FUN_ASR_VOCABULARY_ID: str = ""

    # === 系统音乐感知 / 在线歌词 ===
    # 是否允许根据系统媒体的歌名/歌手访问在线歌词 provider
    MUSIC_ONLINE_LYRICS_ENABLED: bool = True
    # syncedlyrics provider 顺序，逗号分隔
    MUSIC_ONLINE_LYRICS_PROVIDERS: str = "Lrclib,NetEase,Musixmatch,Megalobiz"

    # === 公开访问保护 & 限流 ===
    # 总开关
    PUBLIC_GUARD_ENABLED: bool = False
    # 最大并发 /think 请求数
    MAX_CONCURRENT_THINKS: int = 2
    # 每日 /think 请求上限
    DAILY_THINK_LIMIT: int = 200
    # 繁忙时的提示语
    PUBLIC_BUSY_MESSAGE: str = "当前体验人数较多，请稍后再试。"
    # 每日限额用尽时的提示语
    PUBLIC_DAILY_LIMIT_MESSAGE: str = "今日体验名额已满，明天再来看看 Akane 吧。"

    # === 工具调用 & 后台任务 ===
    # 同轮工具调用的常规软预算。持续产生新调用/新结果时允许继续。
    MAX_TOOL_ROUNDS: int = 3
    # 仅用于阻止失控循环的紧急硬上限；正常工具链不应触及。
    MAX_TOOL_EMERGENCY_ROUNDS: int = 16
    # native tool 通道总开关。默认开启 native-first：allowlist 内、且 (host, model)
    # 能力档案已验证的工具走 provider native schema；未知/未验证 provider 会结构化
    # 回退 legacy JSON tool_call。需要保守兼容时可经 env 显式关闭。
    ENABLE_NATIVE_TOOL_DECISION: bool = True
    # native tool 允许列表，逗号分隔。除低风险只读工具外，受管生成、媒体处理和文件交付
    # 也走同一 provider-native 工具环；它们只接受会话 handle，不接受本机绝对路径。
    # web_search（3d live gate）、retrieve_memory / browse_memory / read_memory_timeline / open_memory
    #（记忆原始证据读取链）、
    # list_reminders / check_inventory / inspect_media_info（6b：确定性 dry-run 量尺，
    # native 链路已由 memory 5d 证明，未单独跑 live smoke）。
    # load_character_context / inspect_attachment / load_material / read_attachment_section /
    # list_workspace / read_workspace / inspect_generated_file（7b：read-only、
    # 静态 schema、generic builder 已验证，未单独跑 live smoke）。
    # generate_image（会话内受管图片输入 + PinAI 受管输出）。生成/处理工具只产出
    # gen_ handle，send_file 负责显式交付。安装插件的 native 能力由
    # PluginHost contribution policy 单独审核，不在宿主静态工具 allowlist 中重复登记。
    # sync_attachment_workspace 虽是 operation="read" 但有文件同步副作用，暂不加入。
    # 注意：这只是"允许"，是否真的走 native 仍取决于总开关和 provider/model 能力档案。
    NATIVE_TOOL_DECISION_ALLOWLIST: str = (
        "web_search,retrieve_memory,browse_memory,read_memory_timeline,open_memory,"
        "list_reminders,check_inventory,inspect_media_info,"
        "load_character_context,inspect_attachment,load_material,read_attachment_section,"
        "list_workspace,read_workspace,inspect_generated_file,generate_image,"
        "compose_file,revise_generated_file,apply_style_to_existing_file,"
        "separate_audio_stems,clean_voice_track,transcribe_media,prepare_voice_dataset,"
        "convert_media_file,cover_song,send_file"
    )
    # 额外允许的 OpenAI-compatible native tools provider/model，逗号分隔。
    # 格式：host:model 或 host:*；默认空，未知中转仍 fail-closed。
    # 可选第三段 json 表示允许 native tools 与 response_format=json_object 共存；
    # 省略时按保守 prompt-only JSON 处理。
    NATIVE_TOOL_PROVIDER_ALLOWLIST: str = ""
    # 联网搜索/网页提取类工具的同轮扩展预算
    MAX_WEB_RESEARCH_TOOL_ROUNDS: int = 8
    # AnySearch MCP 单次调用超时。batch_search 可能同时覆盖多个查询，默认比普通本地 MCP 更宽松。
    WEB_SEARCH_MCP_TIMEOUT_SECONDS: float = 35.0
    # 最终答复若落成解析兜底语或进度占位，最多重新生成几次；工具不会因此被强制调用。
    CHAT_FINAL_RESPONSE_MAX_ATTEMPTS: int = 3
    # 托管浏览器打开、滚动、点击、输入类工具的同轮扩展预算
    MAX_BROWSER_TOOL_ROUNDS: int = 10
    # 后台 Workshop Worker 最大循环轮次
    MAX_TASK_WORKER_ROUNDS: int = 3
    # Akane 可访问的单一文件工作区；留空时使用桌面/Akane Workspace
    AKANE_WORKSPACE_ROOT: str = ""
    # 单个工作区文件允许直接读取的最大字节数（技术保护，不是 prompt 预算）
    AKANE_WORKSPACE_MAX_READ_BYTES: int = 64 * 1024 * 1024

    # === QQ / NapCat 桥接 ===
    # 总开关
    QQ_BRIDGE_ENABLED: bool = False
    # OneBot HTTP 服务地址
    QQ_ONEBOT_HTTP_URL: str = "http://127.0.0.1:3001"
    # 允许读取的 OneBot 本地缓存根目录；多个目录用分号分隔，留空则禁用 cache path fallback
    QQ_ONEBOT_CACHE_ROOTS: str = ""
    # 部署侧通道引用；named instance 必须与 manifest 完全一致
    QQ_CHANNEL_PROFILE_REF: str = ""
    # Bot 自己的 QQ 号；留空时仍可使用事件里的 self_id
    QQ_BOT_QQ: str = ""
    # NapCat 反向 HTTP webhook secret（Authorization: Bearer）
    QQ_WEBHOOK_SECRET: str = ""
    # Akane 调用 OneBot HTTP API 的 access token
    QQ_ONEBOT_ACCESS_TOKEN: str = ""
    # QQ 文字聊天默认使用的 Creator Kit 角色包 id（留空=Akane 默认人设）
    QQ_CHARACTER_PACK_ID: str = ""
    # QQ 回复投递模式：text=只文字 voice=只语音 both=文字+语音 auto=模型用 reply_medium 决定
    QQ_REPLY_MODE: str = "auto"
    # QQ 语音合成读取的本地能力配置 profile（留空=WEB_OWNER_PROFILE_USER_ID/master）
    QQ_TTS_PROFILE_USER_ID: str = ""
    # QQ 联网搜索读取的本地能力配置 profile（留空=WEB_OWNER_PROFILE_USER_ID/master）
    QQ_WEB_SEARCH_PROFILE_USER_ID: str = ""
    # QQ 语音回复最大合成文本长度，超过后降级为文字
    QQ_VOICE_MAX_TEXT_CHARS: int = 280
    # QQ 语音回复最多合成的 speech segment 数
    QQ_VOICE_MAX_SEGMENTS: int = 3
    # 允许群聊使用明文（非 JSON 卡片）模式
    QQ_GROUP_PLAINTEXT_ENABLED: bool = False
    # 未触发 Bot 的群消息是否写入被动群记忆：all/allowlist/denylist/off
    QQ_GROUP_PASSIVE_MEMORY_MODE: str = "all"
    # allowlist/denylist 使用的群号，支持逗号、分号或空白分隔
    QQ_GROUP_PASSIVE_MEMORY_GROUP_IDS: str = ""
    # 群聊对话跟随 TTL（秒），超时后新卡片
    QQ_GROUP_FOLLOW_TTL_SECONDS: int = 180
    # 群附件缓冲 TTL（秒），等待多张图片到齐
    QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS: int = 180
    # 附件去抖间隔（秒）
    QQ_ATTACHMENT_DEBOUNCE_SECONDS: float = 1.2
    # 附件就绪等待时间（秒）
    QQ_ATTACHMENT_READY_WAIT_SECONDS: float = 8.0
    # 多消息段发送间隔（秒）
    QQ_REPLY_SEGMENT_DELAY_SECONDS: float = 0.8
    # 事件最大有效时间（秒），超时丢弃
    QQ_EVENT_MAX_AGE_SECONDS: int = 300
    # 允许处理过期事件
    QQ_ALLOW_STALE_EVENTS: bool = False
    # 附件下载超时（秒）
    QQ_ATTACHMENT_DOWNLOAD_TIMEOUT: float = 20.0
    # 附件下载最大字节数
    QQ_ATTACHMENT_MAX_BYTES: int = 64 * 1024 * 1024
    # QQ 文本消息附件最大读取字节数
    QQ_TEXT_ATTACHMENT_MAX_READ_BYTES: int = 256 * 1024

    # === 后台 Worker ===
    # 默认并发 Worker 数
    BACKGROUND_DEFAULT_WORKERS: int = 1
    # 附件处理并发 Worker 数
    BACKGROUND_ATTACHMENT_WORKERS: int = 3

    # === 远程媒体 (yt-dlp) ===
    # Cookie 文件路径（需登录的平台）
    REMOTE_MEDIA_YTDLP_COOKIEFILE: str = ""
    # 已停用：仅为旧配置提供结构化迁移提示；请改用受域名校验的 cookies.txt。
    REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER: str = ""
    # 自定义 User-Agent
    REMOTE_MEDIA_YTDLP_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
    # Referer 头
    REMOTE_MEDIA_YTDLP_REFERER: str = "https://www.bilibili.com/"

    # === ASR / Whisper 模型缓存目录 ===
    # 留空时使用 faster-whisper 默认缓存目录
    WHISPER_CACHE_DIR: str = ""
    # 本地媒体宿主读取这些字段；保留在 Settings 中也可避免把合法配置误报为未知键。
    AKANE_LOCAL_WHISPER_MODEL: str = "small"
    AKANE_LOCAL_ASR_DEVICE: str = "cpu"
    AKANE_LOCAL_ASR_COMPUTE_TYPE: str = "int8"
    AKANE_LOCAL_RVC_SEPARATION_MODEL: str = "HP5_only_main_vocal"

    # === Web 身份模式 ===
    # owner  : 本地主创模式，所有浏览器共享同一主人 profile
    # browser: 每个浏览器分配独立匿名 profile，适合公开试玩
    # invite : 通过 URL 邀请码映射 profile，适合小范围闭测
    WEB_IDENTITY_MODE: str = "owner"
    # owner 模式下使用的 profile 标识
    WEB_OWNER_PROFILE_USER_ID: str = "master"

    # 主人 QQ 号；留空时私聊使用独立 QQ 身份
    MASTER_QQ: str = ""

    # named instance 的管理写接口 token；不得与 QQ token 复用
    AKANE_ADMIN_TOKEN: str = ""
    # Desktop Satellite 的实例级设备凭据；不得与管理/QQ/模型 token 复用
    AKANE_DESKTOP_SATELLITE_TOKEN: str = ""

    # 监听地址 & 端口
    HOST: str = "0.0.0.0"
    PORT: int = 9999

    @property
    def DB_PATH(self):
        return os.path.join(DATA_DIR, "akane_cloud.db")

    class Config:
        env_file = ".env"
        extra = "ignore"


def _configured_env_file() -> str:
    """Return the launcher-bound env file without falling back across instances."""

    return str(os.environ.get("AKANE_ENV_FILE") or "").strip() or ".env"


def _load_settings() -> Settings:
    return Settings(_env_file=_configured_env_file())


settings = _load_settings()


def _normalize_web_identity_mode(value: str) -> str:
    normalized = str(value or "owner").strip().lower()
    return normalized if normalized in {"owner", "browser", "invite"} else "owner"


# ---------------------------------------------------------------------------
# Keys that appear in .env but are consumed outside of pydantic-settings
# (e.g. by launch scripts).  They are expected to be "unknown" to Settings
# and should not trigger a warning.
# ---------------------------------------------------------------------------
_KNOWN_EXTERNAL_ENV_KEYS: set[str] = {
    "ANYSEARCH_API_KEY",
    "COMPANION_HOST",
    "COMPANION_PORT",
    "EMQUANT_API_ROOT",
    "EMQUANT_BRIDGE_HOST",
    "EMQUANT_BRIDGE_PORT",
    "EMQUANT_CALLBACK_QUEUE_MAX",
    "EMQUANT_ENABLED",
    "EMQUANT_SUBSCRIPTION_STATE_PATH",
}


def _is_retired_finance_env_key(key: str) -> bool:
    return key.startswith(("FINANCE_", "QQ_FINANCE_")) or key in {
        "EMQUANT_BRIDGE_URL",
        "EMQUANT_BRIDGE_TOKEN",
        "EMQUANT_HTTP_TIMEOUT_SECONDS",
    }


def _warn_unknown_env_keys(settings_obj: Settings) -> None:
    """Warn about .env keys that don't match any Settings field.

    pydantic-settings ``extra = "ignore"`` silently drops unrecognised keys,
    so a typo like ``VISION_ENABLD=true`` is invisible.  This function reads
    the env file manually and flags keys that are genuinely unknown.
    """
    env_file = _configured_env_file()
    env_path = Path(env_file)
    if not env_path.exists():
        return

    known = set(settings_obj.model_fields.keys()) | _KNOWN_EXTERNAL_ENV_KEYS

    retired_finance: list[str] = []
    unknown: list[str] = []
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if m:
                key = m.group(1)
                if key in known:
                    continue
                if _is_retired_finance_env_key(key):
                    retired_finance.append(key)
                else:
                    unknown.append(key)
    except OSError:
        return

    if retired_finance:
        logger.warning(
            "Retired public-host finance keys in %s are ignored: %s",
            env_file,
            ", ".join(sorted(retired_finance)),
        )
    if unknown:
        logger.warning(
            "Unrecognized keys in %s (typo?): %s — these are ignored by pydantic-settings.",
            env_file,
            ", ".join(sorted(unknown)),
        )


# ---------------------------------------------------------------------------
# _apply_settings  —  the single source of truth that transforms a Settings
# instance into the module-level globals consumed by the rest of the app.
#
# Previously this logic was duplicated ~110 lines in reload_settings() and
# again as module-level code.  The two copies had already drifted (e.g.
# VISION_AUTO_OUTFIT_OBSERVE was missing from the module-level block).
# ---------------------------------------------------------------------------
def _apply_settings(s: Settings) -> None:
    # ---- global declarations (one batch, kept in sync with the body) ----
    global TEXT_API_KEY, TEXT_BASE_URL, TEXT_MODEL_NAME, TEXT_API_PROTOCOL
    global AUX_API_KEY, AUX_BASE_URL, AUX_MODEL_NAME, AUX_API_PROTOCOL
    global CHAT_API_KEY, CHAT_BASE_URL, CHAT_MODEL_NAME, CHAT_API_PROTOCOL
    global VISION_API_KEY, VISION_BASE_URL, VISION_MODEL_NAME, VISION_API_PROTOCOL
    global LLM_THINKING_MODE, LLM_REASONING_EFFORT, LLM_AUX_REASONING_EFFORT, LLM_CHAT_REASONING_EFFORT
    global LLM_DISABLE_RESPONSE_STORAGE
    global LLM_CONTEXT_WINDOW, LLM_AUTO_COMPACT_TOKEN_LIMIT
    global VISION_ENABLED, VISION_REQUEST_TIMEOUT, VISION_PROMPT_VERSION
    global VISION_AUTO_SCENE_OBSERVE, VISION_AUTO_GIFT_OBSERVE, VISION_AUTO_OUTFIT_OBSERVE, VISION_MAX_IMAGE_BYTES
    global IMAGE_GENERATION_ENABLED, IMAGE_GENERATION_BASE_URL, IMAGE_GENERATION_API_KEY, IMAGE_GENERATION_MODEL
    global IMAGE_GENERATION_TIMEOUT_SECONDS, IMAGE_GENERATION_MAX_INPUT_IMAGES, IMAGE_GENERATION_MAX_OUTPUT_IMAGES
    global IMAGE_GENERATION_MAX_IMAGE_BYTES, IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES, IMAGE_GENERATION_MAX_OUTPUT_BYTES
    global COVER_SONG_ENABLED, RVC_WEBUI_BASE_URL, RVC_ROOT_DIR, RVC_DEFAULT_MODEL
    global COVER_SONG_SEPARATION_MODEL, COVER_SONG_DEFAULT_OUTPUT_FORMAT, COVER_SONG_DEFAULT_DELIVERY
    global COVER_SONG_TIMEOUT_SECONDS, COVER_SONG_MAX_DURATION_SECONDS, COVER_SONG_MAX_INPUT_BYTES
    global LOCAL_MEDIA_EXECUTOR_BASE_URL, LOCAL_MEDIA_EXECUTOR_TIMEOUT_SECONDS
    global TTS_VOICE, TTS_RATE, TTS_VOLUME, TTS_PITCH, STREAMING_TTS_ENABLED
    global GPT_SOVITS_TTS_TIMEOUT_SECONDS, GPT_SOVITS_TEXT_LANG, GPT_SOVITS_MEDIA_TYPE
    global GPT_SOVITS_STREAMING_MODE, GPT_SOVITS_PARALLEL_INFER, GPT_SOVITS_SPLIT_BUCKET
    global GPT_SOVITS_BATCH_SIZE, GPT_SOVITS_SPEED_FACTOR, GPT_SOVITS_FRAGMENT_INTERVAL, GPT_SOVITS_TEXT_SPLIT_METHOD
    global FUN_ASR_REALTIME_ENABLED, DASHSCOPE_API_KEY, DASHSCOPE_API_HOST
    global FUN_ASR_REALTIME_MODEL, FUN_ASR_AUDIO_FORMAT, FUN_ASR_SAMPLE_RATE
    global FUN_ASR_MAX_SENTENCE_SILENCE, FUN_ASR_VOCABULARY_ID
    global MUSIC_ONLINE_LYRICS_ENABLED, MUSIC_ONLINE_LYRICS_PROVIDERS
    global PUBLIC_GUARD_ENABLED, MAX_CONCURRENT_THINKS, DAILY_THINK_LIMIT
    global PUBLIC_BUSY_MESSAGE, PUBLIC_DAILY_LIMIT_MESSAGE, MAX_TOOL_ROUNDS, MAX_TOOL_EMERGENCY_ROUNDS
    global ENABLE_NATIVE_TOOL_DECISION
    global NATIVE_TOOL_DECISION_ALLOWLIST, NATIVE_TOOL_PROVIDER_ALLOWLIST, MAX_WEB_RESEARCH_TOOL_ROUNDS
    global WEB_SEARCH_MCP_TIMEOUT_SECONDS, CHAT_FINAL_RESPONSE_MAX_ATTEMPTS
    global MAX_BROWSER_TOOL_ROUNDS, MAX_TASK_WORKER_ROUNDS
    global AKANE_WORKSPACE_ROOT, AKANE_WORKSPACE_MAX_READ_BYTES
    global QQ_BRIDGE_ENABLED, QQ_ONEBOT_HTTP_URL, QQ_ONEBOT_CACHE_ROOTS, QQ_CHANNEL_PROFILE_REF, QQ_BOT_QQ
    global QQ_WEBHOOK_SECRET, QQ_ONEBOT_ACCESS_TOKEN, QQ_CHARACTER_PACK_ID
    global \
        QQ_REPLY_MODE, \
        QQ_TTS_PROFILE_USER_ID, \
        QQ_WEB_SEARCH_PROFILE_USER_ID, \
        QQ_VOICE_MAX_TEXT_CHARS, \
        QQ_VOICE_MAX_SEGMENTS
    global \
        QQ_GROUP_PLAINTEXT_ENABLED, \
        QQ_GROUP_PASSIVE_MEMORY_MODE, \
        QQ_GROUP_PASSIVE_MEMORY_GROUP_IDS, \
        QQ_GROUP_FOLLOW_TTL_SECONDS, \
        QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS
    global QQ_ATTACHMENT_DEBOUNCE_SECONDS, QQ_ATTACHMENT_READY_WAIT_SECONDS, QQ_REPLY_SEGMENT_DELAY_SECONDS
    global QQ_EVENT_MAX_AGE_SECONDS, QQ_ALLOW_STALE_EVENTS
    global QQ_ATTACHMENT_DOWNLOAD_TIMEOUT, QQ_ATTACHMENT_MAX_BYTES, QQ_TEXT_ATTACHMENT_MAX_READ_BYTES
    global BACKGROUND_DEFAULT_WORKERS, BACKGROUND_ATTACHMENT_WORKERS
    global REMOTE_MEDIA_YTDLP_COOKIEFILE, REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER
    global REMOTE_MEDIA_YTDLP_USER_AGENT, REMOTE_MEDIA_YTDLP_REFERER
    global WEB_IDENTITY_MODE, WEB_OWNER_PROFILE_USER_ID
    global RUN_MODE, AKANE_INSTANCE_ID, PERSONA_CONFIG_PATH, PERSONA_VARIANT
    global \
        EMBEDDING_PROVIDER, \
        EMBEDDING_MODEL_NAME, \
        EMBEDDING_BASE_URL, \
        EMBEDDING_API_KEY, \
        EMBEDDING_DIMENSION, \
        EMBEDDING_TIMEOUT_SECONDS, \
        EMBEDDING_DEVICE, \
        EMBEDDING_LOCAL_FILES_ONLY, \
        EMBEDDING_CACHE_FOLDER
    global EMBEDDING_CACHE_SIZE, EMBEDDING_REINDEX_BATCH_SIZE
    global ENABLE_SEMANTIC_MEMORY, ENABLE_SEMANTIC_REINFORCEMENT, PRE_RETRIEVAL_DEFAULT_ENABLED
    global PROMPT_CACHE_HINTS_ENABLED, PROMPT_CACHE_HINTS_FORCE, PROMPT_CACHE_NAMESPACE, PROMPT_CACHE_RETENTION
    global LLM_PROMPT_AUDIT_ENABLED, LLM_PROMPT_AUDIT_INCLUDE_AUX
    global ROUTER_DEBUG, VERIFIER_DEBUG, FINAL_DEBUG
    global DRIFT_PROBABILITY, SUMMARY_TRIGGER_COUNT, SUMMARY_BATCH_SIZE, RECENT_SUMMARY_LIMIT
    global EPISODIC_COMPACT_TRIGGER_COUNT, EPISODIC_COMPACT_BATCH_SIZE, EPISODIC_VISIBLE_MAX, SEMANTIC_VISIBLE_LIMIT
    global SEMANTIC_REINFORCEMENT_LOOKBACK, SEMANTIC_REINFORCEMENT_MIN_OVERLAP
    global MEMORY_BACKEND, MEMCORE_STORAGE_PATH, MEMCORE_VISIBLE_SCOPE, MEMCORE_ENABLE_FLAVOR, MEMCORE_SHADOW_COMPARE
    global \
        MEMCORE_RAW_TOKEN_TRIGGER, \
        MEMCORE_RAW_TOKEN_BATCH_RATIO, \
        MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET, \
        MEMCORE_NATIVE_TIMELINE_PAGE_TOKEN_BUDGET, \
        MEMCORE_COMPACTION_WORKERS
    global MEMCORE_COMPACTION_FAILURE_COOLDOWN_SECONDS
    global WHISPER_CACHE_DIR
    global MASTER_QQ, AKANE_ADMIN_TOKEN, AKANE_DESKTOP_SATELLITE_TOKEN, PORT, HOST

    # === LLM / API keys ===
    TEXT_API_KEY = s.TEXT_API_KEY or ""
    TEXT_BASE_URL = s.TEXT_BASE_URL or ""
    TEXT_MODEL_NAME = s.TEXT_MODEL_NAME or "deepseek-chat"
    TEXT_API_PROTOCOL = s.TEXT_API_PROTOCOL or "auto"

    AUX_API_KEY = s.AUX_API_KEY or TEXT_API_KEY
    AUX_BASE_URL = s.AUX_BASE_URL or TEXT_BASE_URL
    AUX_MODEL_NAME = s.AUX_MODEL_NAME or "deepseek-chat"
    AUX_API_PROTOCOL = s.AUX_API_PROTOCOL or TEXT_API_PROTOCOL

    CHAT_API_KEY = s.CHAT_API_KEY or TEXT_API_KEY
    CHAT_BASE_URL = s.CHAT_BASE_URL or TEXT_BASE_URL
    CHAT_MODEL_NAME = s.CHAT_MODEL_NAME or TEXT_MODEL_NAME
    CHAT_API_PROTOCOL = s.CHAT_API_PROTOCOL or TEXT_API_PROTOCOL
    LLM_THINKING_MODE = str(s.LLM_THINKING_MODE or "disabled").strip().lower()
    LLM_REASONING_EFFORT = str(s.LLM_REASONING_EFFORT or "").strip().lower()
    LLM_AUX_REASONING_EFFORT = str(s.LLM_AUX_REASONING_EFFORT or "").strip().lower()
    LLM_CHAT_REASONING_EFFORT = str(s.LLM_CHAT_REASONING_EFFORT or "").strip().lower()
    LLM_DISABLE_RESPONSE_STORAGE = bool(s.LLM_DISABLE_RESPONSE_STORAGE)
    LLM_CONTEXT_WINDOW = max(0, int(s.LLM_CONTEXT_WINDOW or 0))
    LLM_AUTO_COMPACT_TOKEN_LIMIT = max(0, int(s.LLM_AUTO_COMPACT_TOKEN_LIMIT or 0))

    VISION_API_KEY = s.VISION_API_KEY or ""
    VISION_BASE_URL = s.VISION_BASE_URL or ""
    VISION_MODEL_NAME = s.VISION_MODEL_NAME or ""
    VISION_API_PROTOCOL = s.VISION_API_PROTOCOL or CHAT_API_PROTOCOL or TEXT_API_PROTOCOL
    VISION_ENABLED = bool(s.VISION_ENABLED)
    VISION_REQUEST_TIMEOUT = float(max(1.0, s.VISION_REQUEST_TIMEOUT))
    VISION_PROMPT_VERSION = str(s.VISION_PROMPT_VERSION or "v1").strip() or "v1"
    VISION_AUTO_SCENE_OBSERVE = bool(s.VISION_AUTO_SCENE_OBSERVE)
    VISION_AUTO_GIFT_OBSERVE = bool(s.VISION_AUTO_GIFT_OBSERVE)
    VISION_AUTO_OUTFIT_OBSERVE = bool(s.VISION_AUTO_OUTFIT_OBSERVE)
    VISION_MAX_IMAGE_BYTES = max(128 * 1024, int(s.VISION_MAX_IMAGE_BYTES))
    IMAGE_GENERATION_ENABLED = bool(s.IMAGE_GENERATION_ENABLED)
    IMAGE_GENERATION_BASE_URL = (
        str(s.IMAGE_GENERATION_BASE_URL or "https://api.pinaic.com/v1").strip().rstrip("/")
        or "https://api.pinaic.com/v1"
    )
    # PinAI keys are platform-group scoped. A chat key that works for Claude/Responses
    # may still receive "Images API is not supported for this platform", so image
    # generation must use an explicitly configured OpenAI-group key.
    IMAGE_GENERATION_API_KEY = str(s.IMAGE_GENERATION_API_KEY or "").strip()
    IMAGE_GENERATION_MODEL = str(s.IMAGE_GENERATION_MODEL or "gpt-image-2").strip() or "gpt-image-2"
    IMAGE_GENERATION_TIMEOUT_SECONDS = max(30.0, min(600.0, float(s.IMAGE_GENERATION_TIMEOUT_SECONDS)))
    IMAGE_GENERATION_MAX_INPUT_IMAGES = max(1, min(5, int(s.IMAGE_GENERATION_MAX_INPUT_IMAGES)))
    IMAGE_GENERATION_MAX_OUTPUT_IMAGES = max(1, min(4, int(s.IMAGE_GENERATION_MAX_OUTPUT_IMAGES)))
    IMAGE_GENERATION_MAX_IMAGE_BYTES = max(128 * 1024, int(s.IMAGE_GENERATION_MAX_IMAGE_BYTES))
    IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES = max(
        IMAGE_GENERATION_MAX_IMAGE_BYTES,
        int(s.IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES),
    )
    IMAGE_GENERATION_MAX_OUTPUT_BYTES = max(512 * 1024, int(s.IMAGE_GENERATION_MAX_OUTPUT_BYTES))

    COVER_SONG_ENABLED = bool(s.COVER_SONG_ENABLED)
    RVC_WEBUI_BASE_URL = (
        str(s.RVC_WEBUI_BASE_URL or "http://127.0.0.1:7899").strip().rstrip("/") or "http://127.0.0.1:7899"
    )
    RVC_ROOT_DIR = str(s.RVC_ROOT_DIR or "").strip()
    RVC_DEFAULT_MODEL = str(s.RVC_DEFAULT_MODEL or "").strip()
    COVER_SONG_SEPARATION_MODEL = (
        str(s.COVER_SONG_SEPARATION_MODEL or "HP5_only_main_vocal").strip() or "HP5_only_main_vocal"
    )
    normalized_cover_format = str(s.COVER_SONG_DEFAULT_OUTPUT_FORMAT or "mp3").strip().lower().lstrip(".")
    COVER_SONG_DEFAULT_OUTPUT_FORMAT = (
        normalized_cover_format if normalized_cover_format in {"mp3", "flac", "wav"} else "mp3"
    )
    normalized_cover_delivery = str(s.COVER_SONG_DEFAULT_DELIVERY or "auto").strip().lower()
    COVER_SONG_DEFAULT_DELIVERY = (
        normalized_cover_delivery if normalized_cover_delivery in {"auto", "voice", "file", "both", "none"} else "auto"
    )
    COVER_SONG_TIMEOUT_SECONDS = max(30.0, min(7200.0, float(s.COVER_SONG_TIMEOUT_SECONDS)))
    COVER_SONG_MAX_DURATION_SECONDS = max(15.0, min(3600.0, float(s.COVER_SONG_MAX_DURATION_SECONDS)))
    COVER_SONG_MAX_INPUT_BYTES = max(1024 * 1024, int(s.COVER_SONG_MAX_INPUT_BYTES))
    LOCAL_MEDIA_EXECUTOR_BASE_URL = str(s.LOCAL_MEDIA_EXECUTOR_BASE_URL or "").strip().rstrip("/")
    LOCAL_MEDIA_EXECUTOR_TIMEOUT_SECONDS = max(
        5.0,
        min(7200.0, float(s.LOCAL_MEDIA_EXECUTOR_TIMEOUT_SECONDS)),
    )

    # === TTS / guard ===
    TTS_VOICE = s.TTS_VOICE or "zh-CN-XiaoxiaoNeural"
    TTS_RATE = s.TTS_RATE or "+6%"
    TTS_VOLUME = s.TTS_VOLUME or "+0%"
    TTS_PITCH = s.TTS_PITCH or "+4Hz"
    STREAMING_TTS_ENABLED = bool(s.STREAMING_TTS_ENABLED)
    GPT_SOVITS_TTS_TIMEOUT_SECONDS = float(max(1.0, s.GPT_SOVITS_TTS_TIMEOUT_SECONDS))
    GPT_SOVITS_TEXT_LANG = str(s.GPT_SOVITS_TEXT_LANG or "zh").strip() or "zh"
    GPT_SOVITS_MEDIA_TYPE = str(s.GPT_SOVITS_MEDIA_TYPE or "wav").strip() or "wav"
    GPT_SOVITS_STREAMING_MODE = bool(s.GPT_SOVITS_STREAMING_MODE)
    GPT_SOVITS_PARALLEL_INFER = s.GPT_SOVITS_PARALLEL_INFER
    GPT_SOVITS_SPLIT_BUCKET = s.GPT_SOVITS_SPLIT_BUCKET
    GPT_SOVITS_BATCH_SIZE = s.GPT_SOVITS_BATCH_SIZE
    GPT_SOVITS_SPEED_FACTOR = s.GPT_SOVITS_SPEED_FACTOR
    GPT_SOVITS_FRAGMENT_INTERVAL = s.GPT_SOVITS_FRAGMENT_INTERVAL
    GPT_SOVITS_TEXT_SPLIT_METHOD = str(s.GPT_SOVITS_TEXT_SPLIT_METHOD or "").strip()
    FUN_ASR_REALTIME_ENABLED = bool(s.FUN_ASR_REALTIME_ENABLED)
    DASHSCOPE_API_KEY = str(s.DASHSCOPE_API_KEY or "").strip()
    DASHSCOPE_API_HOST = str(s.DASHSCOPE_API_HOST or "").strip()
    FUN_ASR_REALTIME_MODEL = str(s.FUN_ASR_REALTIME_MODEL or "fun-asr-realtime").strip() or "fun-asr-realtime"
    FUN_ASR_AUDIO_FORMAT = str(s.FUN_ASR_AUDIO_FORMAT or "pcm").strip().lower() or "pcm"
    FUN_ASR_SAMPLE_RATE = max(8000, min(192000, int(s.FUN_ASR_SAMPLE_RATE)))
    FUN_ASR_MAX_SENTENCE_SILENCE = max(200, min(6000, int(s.FUN_ASR_MAX_SENTENCE_SILENCE)))
    FUN_ASR_VOCABULARY_ID = str(s.FUN_ASR_VOCABULARY_ID or "").strip()
    MUSIC_ONLINE_LYRICS_ENABLED = bool(s.MUSIC_ONLINE_LYRICS_ENABLED)
    MUSIC_ONLINE_LYRICS_PROVIDERS = (
        str(s.MUSIC_ONLINE_LYRICS_PROVIDERS or "Lrclib,NetEase,Musixmatch,Megalobiz").strip()
        or "Lrclib,NetEase,Musixmatch,Megalobiz"
    )
    PUBLIC_GUARD_ENABLED = bool(s.PUBLIC_GUARD_ENABLED)
    MAX_CONCURRENT_THINKS = max(0, int(s.MAX_CONCURRENT_THINKS))
    DAILY_THINK_LIMIT = max(0, int(s.DAILY_THINK_LIMIT))
    PUBLIC_BUSY_MESSAGE = (
        str(s.PUBLIC_BUSY_MESSAGE or "当前体验人数较多，请稍后再试。").strip() or "当前体验人数较多，请稍后再试。"
    )
    PUBLIC_DAILY_LIMIT_MESSAGE = (
        str(s.PUBLIC_DAILY_LIMIT_MESSAGE or "今日体验名额已满，明天再来看看 Akane 吧。").strip()
        or "今日体验名额已满，明天再来看看 Akane 吧。"
    )
    MAX_TOOL_ROUNDS = max(1, min(5, int(s.MAX_TOOL_ROUNDS)))
    MAX_TOOL_EMERGENCY_ROUNDS = max(
        MAX_TOOL_ROUNDS + 1,
        min(32, int(s.MAX_TOOL_EMERGENCY_ROUNDS)),
    )
    ENABLE_NATIVE_TOOL_DECISION = bool(s.ENABLE_NATIVE_TOOL_DECISION)
    NATIVE_TOOL_DECISION_ALLOWLIST = str(s.NATIVE_TOOL_DECISION_ALLOWLIST or "web_search").strip()
    NATIVE_TOOL_PROVIDER_ALLOWLIST = str(s.NATIVE_TOOL_PROVIDER_ALLOWLIST or "").strip()
    MAX_WEB_RESEARCH_TOOL_ROUNDS = max(MAX_TOOL_ROUNDS, min(12, int(s.MAX_WEB_RESEARCH_TOOL_ROUNDS)))
    WEB_SEARCH_MCP_TIMEOUT_SECONDS = max(5.0, min(90.0, float(s.WEB_SEARCH_MCP_TIMEOUT_SECONDS)))
    CHAT_FINAL_RESPONSE_MAX_ATTEMPTS = max(1, min(5, int(s.CHAT_FINAL_RESPONSE_MAX_ATTEMPTS)))
    MAX_BROWSER_TOOL_ROUNDS = max(MAX_TOOL_ROUNDS, min(12, int(s.MAX_BROWSER_TOOL_ROUNDS)))
    MAX_TASK_WORKER_ROUNDS = max(1, min(5, int(s.MAX_TASK_WORKER_ROUNDS)))
    AKANE_WORKSPACE_ROOT = str(s.AKANE_WORKSPACE_ROOT or "").strip()
    AKANE_WORKSPACE_MAX_READ_BYTES = max(1024, int(s.AKANE_WORKSPACE_MAX_READ_BYTES))

    # === QQ / NapCat ===
    QQ_BRIDGE_ENABLED = bool(s.QQ_BRIDGE_ENABLED)
    QQ_ONEBOT_HTTP_URL = (
        str(s.QQ_ONEBOT_HTTP_URL or "http://127.0.0.1:3001").strip().rstrip("/") or "http://127.0.0.1:3001"
    )
    QQ_ONEBOT_CACHE_ROOTS = str(s.QQ_ONEBOT_CACHE_ROOTS or "").strip()
    QQ_CHANNEL_PROFILE_REF = str(s.QQ_CHANNEL_PROFILE_REF or "").strip()
    raw_qq_bot_qq = str(s.QQ_BOT_QQ or "").strip()
    QQ_BOT_QQ = raw_qq_bot_qq if raw_qq_bot_qq.isdigit() else ""
    QQ_WEBHOOK_SECRET = str(s.QQ_WEBHOOK_SECRET or "").strip()
    QQ_ONEBOT_ACCESS_TOKEN = str(s.QQ_ONEBOT_ACCESS_TOKEN or "").strip()
    raw_qq_character_pack_id = str(s.QQ_CHARACTER_PACK_ID or "").strip()
    QQ_CHARACTER_PACK_ID = (
        raw_qq_character_pack_id
        if raw_qq_character_pack_id and re.fullmatch(r"[A-Za-z0-9_.-]+", raw_qq_character_pack_id)
        else ""
    )
    raw_qq_reply_mode = str(s.QQ_REPLY_MODE or "auto").strip().lower()
    QQ_REPLY_MODE = raw_qq_reply_mode if raw_qq_reply_mode in {"text", "voice", "both", "auto"} else "auto"
    raw_qq_tts_profile_user_id = str(s.QQ_TTS_PROFILE_USER_ID or s.WEB_OWNER_PROFILE_USER_ID or "master").strip()
    QQ_TTS_PROFILE_USER_ID = (
        raw_qq_tts_profile_user_id
        if raw_qq_tts_profile_user_id and re.fullmatch(r"[A-Za-z0-9_.-]+", raw_qq_tts_profile_user_id)
        else "master"
    )
    raw_qq_web_search_profile_user_id = str(
        s.QQ_WEB_SEARCH_PROFILE_USER_ID or s.WEB_OWNER_PROFILE_USER_ID or "master"
    ).strip()
    QQ_WEB_SEARCH_PROFILE_USER_ID = (
        raw_qq_web_search_profile_user_id
        if raw_qq_web_search_profile_user_id
        and (
            raw_qq_web_search_profile_user_id.lower() in {"conversation", "context", "current"}
            or re.fullmatch(r"[A-Za-z0-9_.-]+", raw_qq_web_search_profile_user_id)
        )
        else "master"
    )
    QQ_VOICE_MAX_TEXT_CHARS = max(20, min(1200, int(s.QQ_VOICE_MAX_TEXT_CHARS)))
    QQ_VOICE_MAX_SEGMENTS = max(1, min(10, int(s.QQ_VOICE_MAX_SEGMENTS)))
    QQ_GROUP_PLAINTEXT_ENABLED = bool(s.QQ_GROUP_PLAINTEXT_ENABLED)
    raw_group_passive_memory_mode = str(s.QQ_GROUP_PASSIVE_MEMORY_MODE or "all").strip().lower()
    QQ_GROUP_PASSIVE_MEMORY_MODE = {
        "whitelist": "allowlist",
        "blacklist": "denylist",
        "enabled": "all",
        "disabled": "off",
    }.get(raw_group_passive_memory_mode, raw_group_passive_memory_mode)
    if QQ_GROUP_PASSIVE_MEMORY_MODE not in {"all", "allowlist", "denylist", "off"}:
        QQ_GROUP_PASSIVE_MEMORY_MODE = "all"
    QQ_GROUP_PASSIVE_MEMORY_GROUP_IDS = str(s.QQ_GROUP_PASSIVE_MEMORY_GROUP_IDS or "").strip()
    QQ_GROUP_FOLLOW_TTL_SECONDS = max(20, int(s.QQ_GROUP_FOLLOW_TTL_SECONDS))
    QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS = max(
        20,
        int(s.QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS or s.QQ_GROUP_FOLLOW_TTL_SECONDS),
    )
    QQ_ATTACHMENT_DEBOUNCE_SECONDS = min(5.0, max(0.0, float(s.QQ_ATTACHMENT_DEBOUNCE_SECONDS)))
    QQ_ATTACHMENT_READY_WAIT_SECONDS = min(60.0, max(0.0, float(s.QQ_ATTACHMENT_READY_WAIT_SECONDS)))
    QQ_REPLY_SEGMENT_DELAY_SECONDS = min(3.0, max(0.0, float(s.QQ_REPLY_SEGMENT_DELAY_SECONDS)))
    QQ_EVENT_MAX_AGE_SECONDS = max(0, int(s.QQ_EVENT_MAX_AGE_SECONDS))
    QQ_ALLOW_STALE_EVENTS = bool(s.QQ_ALLOW_STALE_EVENTS)
    QQ_ATTACHMENT_DOWNLOAD_TIMEOUT = max(1.0, float(s.QQ_ATTACHMENT_DOWNLOAD_TIMEOUT))
    QQ_ATTACHMENT_MAX_BYTES = max(1024, int(s.QQ_ATTACHMENT_MAX_BYTES))
    QQ_TEXT_ATTACHMENT_MAX_READ_BYTES = max(1024, int(s.QQ_TEXT_ATTACHMENT_MAX_READ_BYTES))
    BACKGROUND_DEFAULT_WORKERS = max(1, int(s.BACKGROUND_DEFAULT_WORKERS))
    BACKGROUND_ATTACHMENT_WORKERS = max(1, int(s.BACKGROUND_ATTACHMENT_WORKERS))

    # === remote media ===
    REMOTE_MEDIA_YTDLP_COOKIEFILE = str(s.REMOTE_MEDIA_YTDLP_COOKIEFILE or "").strip()
    REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER = str(s.REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER or "").strip()
    REMOTE_MEDIA_YTDLP_USER_AGENT = (
        str(s.REMOTE_MEDIA_YTDLP_USER_AGENT or "").strip()
        or Settings.model_fields["REMOTE_MEDIA_YTDLP_USER_AGENT"].default
    )
    REMOTE_MEDIA_YTDLP_REFERER = str(s.REMOTE_MEDIA_YTDLP_REFERER or "").strip() or "https://www.bilibili.com/"

    # === web identity ===
    WEB_IDENTITY_MODE = _normalize_web_identity_mode(s.WEB_IDENTITY_MODE)
    WEB_OWNER_PROFILE_USER_ID = str(s.WEB_OWNER_PROFILE_USER_ID or "master").strip() or "master"

    # === persona / embedding / memory ===
    RUN_MODE = s.RUN_MODE
    AKANE_INSTANCE_ID = str(s.AKANE_INSTANCE_ID or "")
    PERSONA_CONFIG_PATH = s.PERSONA_CONFIG_PATH or ""
    PERSONA_VARIANT = str(s.PERSONA_VARIANT or "default").strip() or "default"
    EMBEDDING_PROVIDER = str(s.EMBEDDING_PROVIDER or "auto").strip().lower() or "auto"
    EMBEDDING_MODEL_NAME = (
        str(s.EMBEDDING_MODEL_NAME or DEFAULT_EMBEDDING_MODEL_NAME).strip() or DEFAULT_EMBEDDING_MODEL_NAME
    )
    EMBEDDING_BASE_URL = str(s.EMBEDDING_BASE_URL or "").strip()
    EMBEDDING_API_KEY = str(s.EMBEDDING_API_KEY or "").strip()
    EMBEDDING_DIMENSION = max(1, int(s.EMBEDDING_DIMENSION or 1024))
    EMBEDDING_TIMEOUT_SECONDS = max(1.0, min(120.0, float(s.EMBEDDING_TIMEOUT_SECONDS or 30.0)))
    EMBEDDING_DEVICE = str(s.EMBEDDING_DEVICE or "").strip()
    EMBEDDING_LOCAL_FILES_ONLY = bool(s.EMBEDDING_LOCAL_FILES_ONLY)
    EMBEDDING_CACHE_FOLDER = str(s.EMBEDDING_CACHE_FOLDER or "").strip()
    EMBEDDING_CACHE_SIZE = max(0, int(s.EMBEDDING_CACHE_SIZE))
    EMBEDDING_REINDEX_BATCH_SIZE = max(1, int(s.EMBEDDING_REINDEX_BATCH_SIZE))
    ENABLE_SEMANTIC_MEMORY = bool(s.ENABLE_SEMANTIC_MEMORY)
    ENABLE_SEMANTIC_REINFORCEMENT = bool(s.ENABLE_SEMANTIC_REINFORCEMENT)
    PRE_RETRIEVAL_DEFAULT_ENABLED = bool(s.PRE_RETRIEVAL_DEFAULT_ENABLED)
    PROMPT_CACHE_HINTS_ENABLED = bool(s.PROMPT_CACHE_HINTS_ENABLED)
    PROMPT_CACHE_HINTS_FORCE = bool(s.PROMPT_CACHE_HINTS_FORCE)
    PROMPT_CACHE_NAMESPACE = str(s.PROMPT_CACHE_NAMESPACE or "akane").strip()
    PROMPT_CACHE_RETENTION = str(s.PROMPT_CACHE_RETENTION or "").strip().lower()
    LLM_PROMPT_AUDIT_ENABLED = bool(s.LLM_PROMPT_AUDIT_ENABLED)
    LLM_PROMPT_AUDIT_INCLUDE_AUX = bool(s.LLM_PROMPT_AUDIT_INCLUDE_AUX)
    ROUTER_DEBUG = bool(s.ROUTER_DEBUG)
    VERIFIER_DEBUG = bool(s.VERIFIER_DEBUG)
    FINAL_DEBUG = bool(s.FINAL_DEBUG)
    DRIFT_PROBABILITY = float(max(0.0, min(1.0, s.DRIFT_PROBABILITY)))
    SUMMARY_TRIGGER_COUNT = max(1, int(s.SUMMARY_TRIGGER_COUNT))
    SUMMARY_BATCH_SIZE = max(1, int(s.SUMMARY_BATCH_SIZE))
    RECENT_SUMMARY_LIMIT = max(1, int(s.RECENT_SUMMARY_LIMIT))
    EPISODIC_COMPACT_TRIGGER_COUNT = max(1, int(s.EPISODIC_COMPACT_TRIGGER_COUNT))
    EPISODIC_COMPACT_BATCH_SIZE = max(1, int(s.EPISODIC_COMPACT_BATCH_SIZE))
    EPISODIC_VISIBLE_MAX = max(1, int(s.EPISODIC_VISIBLE_MAX))
    SEMANTIC_VISIBLE_LIMIT = max(1, int(s.SEMANTIC_VISIBLE_LIMIT))
    SEMANTIC_REINFORCEMENT_LOOKBACK = max(1, int(s.SEMANTIC_REINFORCEMENT_LOOKBACK))
    SEMANTIC_REINFORCEMENT_MIN_OVERLAP = max(1, int(s.SEMANTIC_REINFORCEMENT_MIN_OVERLAP))
    raw_memory_backend = str(s.MEMORY_BACKEND or "memcore").strip().lower()
    MEMORY_BACKEND = raw_memory_backend if raw_memory_backend in {"legacy", "dual", "memcore"} else "memcore"
    MEMCORE_STORAGE_PATH = str(s.MEMCORE_STORAGE_PATH or "").strip()
    raw_memcore_visible_scope = str(s.MEMCORE_VISIBLE_SCOPE or "user").strip().lower()
    MEMCORE_VISIBLE_SCOPE = (
        raw_memcore_visible_scope if raw_memcore_visible_scope in {"conversation", "user"} else "user"
    )
    MEMCORE_ENABLE_FLAVOR = bool(s.MEMCORE_ENABLE_FLAVOR)
    MEMCORE_SHADOW_COMPARE = bool(s.MEMCORE_SHADOW_COMPARE)
    MEMCORE_RAW_TOKEN_TRIGGER = max(
        1000,
        min(200000, int(s.MEMCORE_RAW_TOKEN_TRIGGER or 48000)),
    )
    MEMCORE_RAW_TOKEN_BATCH_RATIO = max(0.01, min(0.99, float(s.MEMCORE_RAW_TOKEN_BATCH_RATIO or 0.67)))
    MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET = max(
        0,
        min(200000, int(s.MEMCORE_RETRIEVAL_RESULT_TOKEN_BUDGET or 0)),
    )
    MEMCORE_NATIVE_TIMELINE_PAGE_TOKEN_BUDGET = max(
        1,
        min(200000, int(s.MEMCORE_NATIVE_TIMELINE_PAGE_TOKEN_BUDGET or 12000)),
    )
    MEMCORE_COMPACTION_WORKERS = max(1, min(8, int(s.MEMCORE_COMPACTION_WORKERS or 1)))
    MEMCORE_COMPACTION_FAILURE_COOLDOWN_SECONDS = max(
        0.0,
        min(3600.0, float(s.MEMCORE_COMPACTION_FAILURE_COOLDOWN_SECONDS or 0.0)),
    )
    WHISPER_CACHE_DIR = str(s.WHISPER_CACHE_DIR or "").strip()

    # === host / port / qq ===
    raw_master_qq = str(s.MASTER_QQ or "").strip()
    MASTER_QQ = raw_master_qq if raw_master_qq.isdigit() else ""
    AKANE_ADMIN_TOKEN = str(s.AKANE_ADMIN_TOKEN or "").strip()
    AKANE_DESKTOP_SATELLITE_TOKEN = str(s.AKANE_DESKTOP_SATELLITE_TOKEN or "").strip()
    PORT = s.PORT
    HOST = s.HOST


# ---------------------------------------------------------------------------
# Module-level initialisation
# ---------------------------------------------------------------------------
_warn_unknown_env_keys(settings)
_apply_settings(settings)


# ---------------------------------------------------------------------------
# Hot-reload entry point (called by control-centre / admin routes)
# ---------------------------------------------------------------------------
def reload_settings() -> None:
    global settings
    settings = _load_settings()
    _warn_unknown_env_keys(settings)
    _apply_settings(settings)
