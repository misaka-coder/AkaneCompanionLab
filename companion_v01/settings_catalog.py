"""Read-only catalog of config.Settings switches (Settings Catalog · slice 1a).

Single source of truth for "what backend switches exist", paired with curated
metadata (category / scope / sensitivity) that **cannot** be auto-derived from
the pydantic model — scope especially needs read-site analysis. Field name,
type and default are read live from `config.Settings` / the config module so
they never drift.

A drift-guard test (tests/test_settings_catalog.py) asserts every Settings
field is either declared here or explicitly excluded, so adding a new switch
forces a catalog entry. That is how a new feature auto-surfaces in the control
center instead of hiding in .env.

READ-ONLY: this module never writes config. Editing is a later slice and must
follow the model-service pattern (store + apply + reload + local-request gate +
secret redaction); see companion_v01/routes/model_services.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import config

# How a change to a setting takes effect — drives the (future) editing UX.
SCOPE_RUNTIME = "runtime"               # read per request/use -> takes effect live
SCOPE_RESTART = "restart"               # only projected at startup -> needs restart/reload
SCOPE_RESTART_CLIENT = "restart_client"  # read when a subsystem/client is built -> rebuild that

VALID_SCOPES = frozenset({SCOPE_RUNTIME, SCOPE_RESTART, SCOPE_RESTART_CLIENT})

# Another surface already edits this — don't double-expose it as editable later.
MANAGED_MODEL_SERVICE = "model-service"
MANAGED_CAPABILITIES = "capabilities"

# Settings fields intentionally NOT catalogued (none today). Listing a field
# here is the explicit, reviewable way to keep it out of the drift guard.
EXCLUDED_KEYS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SettingSpec:
    key: str
    category: str
    scope: str
    description: str
    sensitive: bool = False
    managed_in: str = ""


def _s(key, category, scope, description, *, sensitive=False, managed_in=""):
    return SettingSpec(key, category, scope, description, sensitive, managed_in)


_RUN = "运行模式 & 人设"
_EMB = "Embedding / 向量记忆"
_RET = "检索 / Prompt 缓存"
_DBG = "调试开关"
_MEM = "记忆参数"
_LLM = "LLM 密钥 & 接入"
_VIS = "视觉 / 图像理解"
_TTS = "语音 (TTS)"
_MUS = "系统音乐感知 / 在线歌词"
_PUB = "公开访问保护 & 限流"
_TOOL = "工具调用 & 后台任务"
_QQ = "QQ / NapCat 桥接"
_BG = "后台 Worker"
_RM = "远程媒体 (yt-dlp)"
_WEB = "Web 身份模式"
_SRV = "服务监听"

# Display tiers. Everything stays catalogued (the drift guard still covers all
# 119 fields); tier only sets the UI's default expand/collapse so the common
# groups are seen first and the rarely-touched ones (multi-access/tuning/infra,
# 1b feedback) fold away to cut comprehension cost.
TIER_COMMON = "common"
TIER_ADVANCED = "advanced"
VALID_TIERS = frozenset({TIER_COMMON, TIER_ADVANCED})

_ADVANCED_CATEGORIES = frozenset({_LLM, _EMB, _RET, _DBG, _MEM, _PUB, _BG, _RM, _WEB, _SRV})

_SPECS: tuple[SettingSpec, ...] = (
    # 运行模式 & 人设
    _s("RUN_MODE", _RUN, SCOPE_RESTART, "运行模式"),
    _s("PERSONA_CONFIG_PATH", _RUN, SCOPE_RUNTIME, "自定义人设 TOML 文件路径（留空=内置默认）"),
    _s("PERSONA_VARIANT", _RUN, SCOPE_RUNTIME, "人设文件中的 variant 名"),
    # Embedding / 向量记忆
    _s("EMBEDDING_PROVIDER", _EMB, SCOPE_RESTART_CLIENT, "提供者：auto/huggingface/hashed"),
    _s("EMBEDDING_MODEL_NAME", _EMB, SCOPE_RESTART_CLIENT, "HuggingFace 模型名或本地模型目录"),
    _s("EMBEDDING_DEVICE", _EMB, SCOPE_RESTART_CLIENT, "设备：空=自动/cuda/cpu"),
    _s("EMBEDDING_LOCAL_FILES_ONLY", _EMB, SCOPE_RESTART_CLIENT, "只用本地缓存/模型目录，避免启动联网下载"),
    _s("EMBEDDING_CACHE_FOLDER", _EMB, SCOPE_RESTART_CLIENT, "Embedding 缓存目录（留空=默认）"),
    _s("EMBEDDING_CACHE_SIZE", _EMB, SCOPE_RESTART_CLIENT, "Embedding 模型缓存大小"),
    _s("EMBEDDING_REINDEX_BATCH_SIZE", _EMB, SCOPE_RUNTIME, "重建索引批量大小"),
    _s("ENABLE_SEMANTIC_MEMORY", _EMB, SCOPE_RUNTIME, "启用语义记忆（长期事实/话题抽取）"),
    _s("ENABLE_SEMANTIC_REINFORCEMENT", _EMB, SCOPE_RUNTIME, "启用语义强化（跨会话话题追踪）"),
    # 检索 / Prompt 缓存
    _s("PRE_RETRIEVAL_DEFAULT_ENABLED", _RET, SCOPE_RUNTIME, "预检索默认开关（会话开始是否先检索）"),
    _s("PROMPT_CACHE_HINTS_ENABLED", _RET, SCOPE_RUNTIME, "启用 prompt cache hints（需模型支持）"),
    _s("PROMPT_CACHE_HINTS_FORCE", _RET, SCOPE_RUNTIME, "强制开启 cache hints（即使模型不在已知列表）"),
    _s("PROMPT_CACHE_NAMESPACE", _RET, SCOPE_RUNTIME, "缓存命名空间（区分实例，避免 key 冲突）"),
    _s("PROMPT_CACHE_RETENTION", _RET, SCOPE_RUNTIME, "缓存保留：空/ephemeral/persistent"),
    _s("LLM_PROMPT_AUDIT_ENABLED", _RET, SCOPE_RUNTIME, "记录最终回复 prompt 的长度/hash 审计（不记原文）"),
    _s("LLM_PROMPT_AUDIT_INCLUDE_AUX", _RET, SCOPE_RUNTIME, "审计也记录辅助 LLM 调用（默认只记 chat:final）"),
    # 调试开关
    _s("ROUTER_DEBUG", _DBG, SCOPE_RUNTIME, "路由调试日志（详细 JSON）"),
    _s("VERIFIER_DEBUG", _DBG, SCOPE_RUNTIME, "校验调试日志（详细 JSON）"),
    _s("FINAL_DEBUG", _DBG, SCOPE_RUNTIME, "最终输出调试日志（详细 JSON）"),
    # 记忆参数
    _s("DRIFT_PROBABILITY", _MEM, SCOPE_RUNTIME, "“走神”概率，偶尔插入不相关检索（0.0~1.0）"),
    _s("SUMMARY_TRIGGER_COUNT", _MEM, SCOPE_RUNTIME, "触发摘要的累积消息数"),
    _s("SUMMARY_BATCH_SIZE", _MEM, SCOPE_RUNTIME, "摘要批处理大小"),
    _s("RECENT_SUMMARY_LIMIT", _MEM, SCOPE_RUNTIME, "最近可见摘要数量"),
    _s("EPISODIC_COMPACT_TRIGGER_COUNT", _MEM, SCOPE_RUNTIME, "情节记忆压缩触发阈值（条数）"),
    _s("EPISODIC_COMPACT_BATCH_SIZE", _MEM, SCOPE_RUNTIME, "情节记忆压缩批处理大小"),
    _s("EPISODIC_VISIBLE_MAX", _MEM, SCOPE_RUNTIME, "可见情节记忆最大条数"),
    _s("SEMANTIC_VISIBLE_LIMIT", _MEM, SCOPE_RUNTIME, "可见语义记忆最大条数（过多=prompt 太长）"),
    _s("SEMANTIC_REINFORCEMENT_LOOKBACK", _MEM, SCOPE_RUNTIME, "语义强化回溯窗口（跨会话）"),
    _s("SEMANTIC_REINFORCEMENT_MIN_OVERLAP", _MEM, SCOPE_RUNTIME, "语义强化最小重叠次数"),
    # LLM 密钥 & 接入（密钥/接入由模型服务页管理）
    _s("TEXT_API_KEY", _LLM, SCOPE_RESTART_CLIENT, "TEXT（辅助任务）API Key", sensitive=True, managed_in=MANAGED_MODEL_SERVICE),
    _s("TEXT_BASE_URL", _LLM, SCOPE_RESTART_CLIENT, "TEXT base_url", managed_in=MANAGED_MODEL_SERVICE),
    _s("TEXT_MODEL_NAME", _LLM, SCOPE_RESTART_CLIENT, "TEXT 模型名", managed_in=MANAGED_MODEL_SERVICE),
    _s("TEXT_API_PROTOCOL", _LLM, SCOPE_RESTART_CLIENT, "TEXT 协议：auto/openai/anthropic/ollama", managed_in=MANAGED_MODEL_SERVICE),
    _s("AUX_API_KEY", _LLM, SCOPE_RESTART_CLIENT, "AUX（额外辅助）API Key", sensitive=True, managed_in=MANAGED_MODEL_SERVICE),
    _s("AUX_BASE_URL", _LLM, SCOPE_RESTART_CLIENT, "AUX base_url", managed_in=MANAGED_MODEL_SERVICE),
    _s("AUX_MODEL_NAME", _LLM, SCOPE_RESTART_CLIENT, "AUX 模型名", managed_in=MANAGED_MODEL_SERVICE),
    _s("AUX_API_PROTOCOL", _LLM, SCOPE_RESTART_CLIENT, "AUX 协议", managed_in=MANAGED_MODEL_SERVICE),
    _s("CHAT_API_KEY", _LLM, SCOPE_RESTART_CLIENT, "CHAT（聊天对话）API Key", sensitive=True, managed_in=MANAGED_MODEL_SERVICE),
    _s("CHAT_BASE_URL", _LLM, SCOPE_RESTART_CLIENT, "CHAT base_url", managed_in=MANAGED_MODEL_SERVICE),
    _s("CHAT_MODEL_NAME", _LLM, SCOPE_RESTART_CLIENT, "CHAT 主模型名", managed_in=MANAGED_MODEL_SERVICE),
    _s("CHAT_API_PROTOCOL", _LLM, SCOPE_RESTART_CLIENT, "CHAT 协议", managed_in=MANAGED_MODEL_SERVICE),
    _s("LLM_THINKING_MODE", _LLM, SCOPE_RUNTIME, "OpenAI 兼容思考模式：default/disabled/enabled"),
    # 视觉 / 图像理解（接入由模型服务页管理，行为开关本目录）
    _s("VISION_API_KEY", _VIS, SCOPE_RUNTIME, "视觉 API Key", sensitive=True, managed_in=MANAGED_MODEL_SERVICE),
    _s("VISION_BASE_URL", _VIS, SCOPE_RUNTIME, "视觉 base_url", managed_in=MANAGED_MODEL_SERVICE),
    _s("VISION_MODEL_NAME", _VIS, SCOPE_RUNTIME, "视觉模型名", managed_in=MANAGED_MODEL_SERVICE),
    _s("VISION_API_PROTOCOL", _VIS, SCOPE_RUNTIME, "视觉协议", managed_in=MANAGED_MODEL_SERVICE),
    _s("VISION_ENABLED", _VIS, SCOPE_RUNTIME, "视觉功能总开关"),
    _s("VISION_REQUEST_TIMEOUT", _VIS, SCOPE_RUNTIME, "单次视觉请求超时（秒）"),
    _s("VISION_PROMPT_VERSION", _VIS, SCOPE_RUNTIME, "视觉 prompt 版本"),
    _s("VISION_AUTO_SCENE_OBSERVE", _VIS, SCOPE_RUNTIME, "自动对场景触发视觉观察"),
    _s("VISION_AUTO_GIFT_OBSERVE", _VIS, SCOPE_RUNTIME, "自动对礼物触发视觉观察"),
    _s("VISION_AUTO_OUTFIT_OBSERVE", _VIS, SCOPE_RUNTIME, "自动对装扮触发视觉观察"),
    _s("VISION_MAX_IMAGE_BYTES", _VIS, SCOPE_RUNTIME, "单张图片最大字节数"),
    # 语音 (TTS)（多数已由能力域 voice profile 管理）
    _s("TTS_VOICE", _TTS, SCOPE_RESTART_CLIENT, "Edge TTS 语音角色", managed_in=MANAGED_CAPABILITIES),
    _s("TTS_RATE", _TTS, SCOPE_RESTART_CLIENT, "Edge TTS 语速"),
    _s("TTS_VOLUME", _TTS, SCOPE_RESTART_CLIENT, "Edge TTS 音量"),
    _s("TTS_PITCH", _TTS, SCOPE_RESTART_CLIENT, "Edge TTS 音调"),
    _s("STREAMING_TTS_ENABLED", _TTS, SCOPE_RUNTIME, "流式 TTS（边生成边播放）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_TTS_TIMEOUT_SECONDS", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 请求超时（秒）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_TEXT_LANG", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 文本语言", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_MEDIA_TYPE", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 输出媒体类型", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_STREAMING_MODE", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 流式模式", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_PARALLEL_INFER", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 并行推理（可空）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_SPLIT_BUCKET", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 分桶（可空）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_BATCH_SIZE", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 批大小（可空）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_SPEED_FACTOR", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 语速系数（可空）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_FRAGMENT_INTERVAL", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 片段间隔（可空）", managed_in=MANAGED_CAPABILITIES),
    _s("GPT_SOVITS_TEXT_SPLIT_METHOD", _TTS, SCOPE_RUNTIME, "GPT-SoVITS 文本切分方法", managed_in=MANAGED_CAPABILITIES),
    # 系统音乐感知 / 在线歌词
    _s("MUSIC_ONLINE_LYRICS_ENABLED", _MUS, SCOPE_RUNTIME, "允许按系统媒体歌名/歌手访问在线歌词", managed_in=MANAGED_CAPABILITIES),
    _s("MUSIC_ONLINE_LYRICS_PROVIDERS", _MUS, SCOPE_RUNTIME, "歌词 provider 顺序（逗号分隔）", managed_in=MANAGED_CAPABILITIES),
    # 公开访问保护 & 限流（启动时构造 guard）
    _s("PUBLIC_GUARD_ENABLED", _PUB, SCOPE_RESTART_CLIENT, "公开访问保护总开关"),
    _s("MAX_CONCURRENT_THINKS", _PUB, SCOPE_RESTART_CLIENT, "最大并发 /think 请求数"),
    _s("DAILY_THINK_LIMIT", _PUB, SCOPE_RESTART_CLIENT, "每日 /think 请求上限"),
    _s("PUBLIC_BUSY_MESSAGE", _PUB, SCOPE_RESTART_CLIENT, "繁忙提示语（用户可见）"),
    _s("PUBLIC_DAILY_LIMIT_MESSAGE", _PUB, SCOPE_RESTART_CLIENT, "每日限额提示语（用户可见）"),
    # 工具调用 & 后台任务
    _s("MAX_TOOL_ROUNDS", _TOOL, SCOPE_RUNTIME, "同轮对话最大工具调用轮次（防循环）"),
    _s("ENABLE_NATIVE_TOOL_DECISION", _TOOL, SCOPE_RUNTIME, "native tool 通道总开关（默认关、fail-closed）"),
    _s("NATIVE_TOOL_DECISION_ALLOWLIST", _TOOL, SCOPE_RUNTIME, "native tool 允许列表（逗号分隔，只读类工具）"),
    _s("NATIVE_TOOL_PROVIDER_ALLOWLIST", _TOOL, SCOPE_RUNTIME, "native provider/model 允许列表（host:model[:json]）"),
    _s("MAX_WEB_RESEARCH_TOOL_ROUNDS", _TOOL, SCOPE_RUNTIME, "联网搜索/网页提取同轮扩展预算"),
    _s("MAX_BROWSER_TOOL_ROUNDS", _TOOL, SCOPE_RUNTIME, "托管浏览器同轮扩展预算"),
    _s("MAX_TASK_WORKER_ROUNDS", _TOOL, SCOPE_RUNTIME, "后台 Workshop Worker 最大循环轮次"),
    _s("AKANE_WORKSPACE_ROOT", _TOOL, SCOPE_RESTART_CLIENT, "单一文件工作区路径（留空=桌面/默认）"),
    _s("AKANE_WORKSPACE_MAX_READ_BYTES", _TOOL, SCOPE_RESTART_CLIENT, "单个工作区文件最大直接读取字节数"),
    # QQ / NapCat 桥接
    _s("QQ_BRIDGE_ENABLED", _QQ, SCOPE_RESTART, "QQ 桥总开关（启动时决定是否构造 gateway）"),
    _s("QQ_ONEBOT_HTTP_URL", _QQ, SCOPE_RUNTIME, "OneBot HTTP 服务地址"),
    _s("QQ_BOT_QQ", _QQ, SCOPE_RUNTIME, "Bot 自己的 QQ 号"),
    _s("QQ_CHARACTER_PACK_ID", _QQ, SCOPE_RUNTIME, "QQ 文字聊天默认角色包 id"),
    _s("QQ_REPLY_MODE", _QQ, SCOPE_RUNTIME, "QQ 回复投递模式：text/voice/both/auto"),
    _s("QQ_TTS_PROFILE_USER_ID", _QQ, SCOPE_RUNTIME, "QQ 语音合成读取的本地能力 profile"),
    _s("QQ_WEB_SEARCH_PROFILE_USER_ID", _QQ, SCOPE_RUNTIME, "QQ 联网搜索读取的本地能力 profile"),
    _s("QQ_VOICE_MAX_TEXT_CHARS", _QQ, SCOPE_RUNTIME, "QQ 语音最大合成文本长度（超过降级文字）"),
    _s("QQ_VOICE_MAX_SEGMENTS", _QQ, SCOPE_RUNTIME, "QQ 语音最多合成的 segment 数"),
    _s("QQ_GROUP_PLAINTEXT_ENABLED", _QQ, SCOPE_RUNTIME, "允许群聊明文（非 JSON 卡片）模式"),
    _s("QQ_GROUP_FOLLOW_TTL_SECONDS", _QQ, SCOPE_RUNTIME, "群聊跟随 TTL（秒）"),
    _s("QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS", _QQ, SCOPE_RESTART, "群附件缓冲 TTL（秒）"),
    _s("QQ_ATTACHMENT_DEBOUNCE_SECONDS", _QQ, SCOPE_RUNTIME, "附件去抖间隔（秒）"),
    _s("QQ_ATTACHMENT_READY_WAIT_SECONDS", _QQ, SCOPE_RUNTIME, "附件就绪等待时间（秒）"),
    _s("QQ_REPLY_SEGMENT_DELAY_SECONDS", _QQ, SCOPE_RUNTIME, "多消息段发送间隔（秒）"),
    _s("QQ_EVENT_MAX_AGE_SECONDS", _QQ, SCOPE_RUNTIME, "事件最大有效时间（秒），超时丢弃"),
    _s("QQ_ALLOW_STALE_EVENTS", _QQ, SCOPE_RUNTIME, "允许处理过期事件"),
    _s("QQ_REQUIRE_FILE_DELIVERY_INTENT", _QQ, SCOPE_RUNTIME, "仅对明确请求文件投递的消息才发文件"),
    _s("QQ_ATTACHMENT_DOWNLOAD_TIMEOUT", _QQ, SCOPE_RUNTIME, "附件下载超时（秒）"),
    _s("QQ_ATTACHMENT_MAX_BYTES", _QQ, SCOPE_RUNTIME, "附件下载最大字节数"),
    _s("QQ_TEXT_ATTACHMENT_MAX_READ_BYTES", _QQ, SCOPE_RUNTIME, "QQ 文本消息附件最大读取字节数"),
    # 后台 Worker（启动时构造 runner）
    _s("BACKGROUND_DEFAULT_WORKERS", _BG, SCOPE_RESTART_CLIENT, "默认并发 Worker 数"),
    _s("BACKGROUND_ATTACHMENT_WORKERS", _BG, SCOPE_RESTART_CLIENT, "附件处理并发 Worker 数"),
    # 远程媒体 (yt-dlp)
    _s("REMOTE_MEDIA_YTDLP_COOKIEFILE", _RM, SCOPE_RUNTIME, "Cookie 文件路径（需登录的平台）", sensitive=True),
    _s("REMOTE_MEDIA_YTDLP_COOKIES_FROM_BROWSER", _RM, SCOPE_RUNTIME, "从浏览器读取 cookie（如 chrome）", sensitive=True),
    _s("REMOTE_MEDIA_YTDLP_USER_AGENT", _RM, SCOPE_RUNTIME, "自定义 User-Agent"),
    _s("REMOTE_MEDIA_YTDLP_REFERER", _RM, SCOPE_RUNTIME, "Referer 头"),
    # Web 身份模式
    _s("WEB_IDENTITY_MODE", _WEB, SCOPE_RUNTIME, "Web 身份模式：owner/browser/invite"),
    _s("WEB_OWNER_PROFILE_USER_ID", _WEB, SCOPE_RUNTIME, "owner 模式使用的 profile 标识"),
    _s("MASTER_QQ", _WEB, SCOPE_RUNTIME, "主人 QQ 号（留空=私聊独立身份）"),
    # 服务监听（启动脚本读取）
    _s("HOST", _SRV, SCOPE_RESTART, "监听地址"),
    _s("PORT", _SRV, SCOPE_RESTART, "监听端口"),
)


def declared_keys() -> set[str]:
    return {spec.key for spec in _SPECS}


_SPEC_BY_KEY = {spec.key: spec for spec in _SPECS}


def get_spec(key: str) -> SettingSpec | None:
    return _SPEC_BY_KEY.get(key)


def is_runtime_editable(key: str) -> bool:
    """Whether a switch may be edited from the settings catalog UI. Qualifies
    only if it is runtime-scope, non-sensitive, AND not owned by another
    surface (managed_in == ""). Fields managed by the model-service or
    capabilities page stay read-only here so the same value never has two
    competing edit surfaces — the catalog links to that page instead."""
    spec = _SPEC_BY_KEY.get(key)
    return bool(
        spec is not None
        and spec.scope == SCOPE_RUNTIME
        and not spec.sensitive
        and not spec.managed_in
    )


def _type_label(field: Any) -> str:
    annotation = getattr(field, "annotation", None)
    simple = {bool: "bool", int: "int", float: "float", str: "str"}
    if annotation in simple:
        return simple[annotation]
    text = str(annotation)
    # e.g. "typing.Optional[bool]" / "bool | None" -> keep it readable
    for name in ("bool", "int", "float", "str"):
        if name in text:
            return f"{name}?" if "None" in text or "Optional" in text else name
    return getattr(annotation, "__name__", None) or text


def build_settings_catalog(config_module: Any = config) -> dict[str, Any]:
    """Build the read-only catalog. Current values are read live from
    `config_module`; sensitive fields never include their value (only whether
    they are set)."""
    fields = config.Settings.model_fields
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for spec in _SPECS:
        field = fields.get(spec.key)
        current = getattr(config_module, spec.key, None)
        entry: dict[str, Any] = {
            "key": spec.key,
            "scope": spec.scope,
            "sensitive": spec.sensitive,
            "managedIn": spec.managed_in,
            "description": spec.description,
            "type": _type_label(field) if field is not None else type(current).__name__,
            "editable": is_runtime_editable(spec.key),
        }
        if spec.sensitive:
            # Never expose the value or default of a secret — only whether it is set.
            entry["isSet"] = bool(str(current if current is not None else "").strip())
        else:
            entry["default"] = field.default if field is not None else None
            entry["current"] = current
        if spec.category not in groups:
            groups[spec.category] = []
            order.append(spec.category)
        groups[spec.category].append(entry)
    return {
        "schemaVersion": 1,
        "scopeLegend": {
            SCOPE_RUNTIME: "改动立即生效",
            SCOPE_RESTART: "需重启后端生效",
            SCOPE_RESTART_CLIENT: "需重建对应子系统/重启生效",
        },
        "categories": [
            {
                "category": name,
                "tier": TIER_ADVANCED if name in _ADVANCED_CATEGORIES else TIER_COMMON,
                "settings": groups[name],
            }
            for name in order
        ],
    }
