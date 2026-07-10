from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import config


DEFAULT_DOMAIN_PROFILE_ID = "default"
FINANCE_DOMAIN_PROFILE_ID = "finance_v1"
FINANCE_MODES = ("off", "qa", "push")

FINANCE_ALLOWED_TOOL_NAMES = (
    "retrieve_memory",
    "read_memory_timeline",
    "web_search",
    "market_news_search",
    "market_quote_snapshot",
    "market_price_series",
    "market_macro_series",
    "render_market_chart",
    "compose_finance_report",
    "sync_attachment_workspace",
    "inspect_attachment",
    "retry_attachment",
    "clear_attachment_focus",
    "read_attachment_section",
    "compose_file",
    "inspect_generated_file",
    "manage_generated_file",
    "send_file",
    "set_reminder",
    "list_reminders",
    "cancel_reminder",
    "manage_task_workspace",
    "delegate_task",
)

FINANCE_HIDDEN_TOOL_NAMES = (
    "call_npc",
    "check_inventory",
    "manage_gift",
    "manage_artifact",
    "manage_persona",
    "send_sticker",
    "fetch_media_from_url",
    "inspect_media_info",
    "convert_media_file",
    "separate_audio_stems",
    "clean_voice_track",
    "transcribe_media",
    "prepare_voice_dataset",
    "open_browser",
    "open_music_search",
    "browser_page",
)

FINANCE_CAPABILITY_HINTS = (
    "金融问题依赖旧观点、关注标的、风险偏好或历史承诺时使用 retrieve_memory；依赖具体日期、时段和原始发言时使用 read_memory_timeline。",
    "涉及当前、最新、实时、价格、涨跌、公告、新闻或宏观数据时主动使用可用只读工具核验；当前尚未接通的市场数据能力必须明确说明不可用，不能编造。",
    "只有附件、既有生成物、明确的报告需求或长任务确实存在时，才展开对应文档、文件交付和后台任务工具。",
)

FINANCE_PROMPT_BLOCK_ID = "finance_v1.rules"
FINANCE_PROMPT_BLOCK = """【金融领域档案 finance_v1】
- 你仍是当前角色，保留当前角色的身份、称呼和表达风格；不要自称另一个金融机器人。
- 涉及当前、最新、实时、价格、涨跌、公告、新闻或宏观数据时，主动使用本轮真正可用的只读工具核验。一次结果不足时可以继续查询，直到证据足够或确认能力不可用。
- 明确区分来源事实、程序计算和分析推断。实时或时效性数据必须写明 as_of 时间与时区；只有标题而没有正文时，不要据此下过深结论。
- 过去观点不是当前事实。引用历史判断时，要说明新证据是强化、削弱还是尚未改变旧判断。
- 不保证收益，不编造价格、公告、财务数据、来源或工具结果，不执行交易、下单或资金动作。
- 数据或权限不可用时结构化降级并降低结论置信度；不要用人格亲近感替代证据。
- QQ 回复默认短而有信息密度。只有图表、文件或长报告确实提升理解且已经真实生成时，才说明并交付产物；普通新闻不自动生成文件。"""


def normalize_finance_mode(value: Any, *, default: str = "off") -> str:
    fallback = str(default or "off").strip().lower()
    if fallback not in FINANCE_MODES:
        fallback = "off"
    mode = str(value or "").strip().lower()
    return mode if mode in FINANCE_MODES else fallback


@dataclass(frozen=True)
class DomainProfile:
    id: str
    enabled: bool
    prompt_block_ids: tuple[str, ...] = field(default_factory=tuple)
    allowed_tool_names: tuple[str, ...] = field(default_factory=tuple)
    hidden_tool_names: tuple[str, ...] = field(default_factory=tuple)
    capability_hints: tuple[str, ...] = field(default_factory=tuple)
    default_tool_round_budget: int = 3
    hard_tool_round_limit: int = 5
    proactive_delivery_enabled: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "prompt_block_ids": list(self.prompt_block_ids),
            "allowed_tool_names": list(self.allowed_tool_names),
            "hidden_tool_names": list(self.hidden_tool_names),
            "default_tool_round_budget": self.default_tool_round_budget,
            "hard_tool_round_limit": self.hard_tool_round_limit,
            "proactive_delivery_enabled": self.proactive_delivery_enabled,
        }


class DomainProfileRegistry:
    def __init__(
        self,
        *,
        finance_enabled: bool | None = None,
        finance_tool_round_budget: int | None = None,
        finance_tool_round_hard_limit: int | None = None,
        finance_push_enabled: bool | None = None,
    ) -> None:
        enabled = bool(
            getattr(config, "FINANCE_ASSISTANT_ENABLED", False)
            if finance_enabled is None
            else finance_enabled
        )
        hard_limit = _bounded_int(
            getattr(config, "FINANCE_TOOL_ROUND_HARD_LIMIT", 16)
            if finance_tool_round_hard_limit is None
            else finance_tool_round_hard_limit,
            default=16,
            lower=1,
            upper=16,
        )
        round_budget = _bounded_int(
            getattr(config, "FINANCE_TOOL_ROUND_BUDGET", 12)
            if finance_tool_round_budget is None
            else finance_tool_round_budget,
            default=12,
            lower=1,
            upper=hard_limit,
        )
        push_enabled = bool(
            getattr(config, "QQ_FINANCE_PUSH_ENABLED", False)
            if finance_push_enabled is None
            else finance_push_enabled
        )
        self._profiles = {
            DEFAULT_DOMAIN_PROFILE_ID: DomainProfile(
                id=DEFAULT_DOMAIN_PROFILE_ID,
                enabled=True,
            ),
            FINANCE_DOMAIN_PROFILE_ID: DomainProfile(
                id=FINANCE_DOMAIN_PROFILE_ID,
                enabled=enabled,
                prompt_block_ids=(FINANCE_PROMPT_BLOCK_ID,),
                allowed_tool_names=FINANCE_ALLOWED_TOOL_NAMES,
                hidden_tool_names=FINANCE_HIDDEN_TOOL_NAMES,
                capability_hints=FINANCE_CAPABILITY_HINTS,
                default_tool_round_budget=round_budget,
                hard_tool_round_limit=hard_limit,
                proactive_delivery_enabled=push_enabled,
            ),
        }

    def get(self, profile_id: Any) -> DomainProfile:
        key = str(profile_id or "").strip().lower()
        profile = self._profiles.get(key)
        if profile is None or not profile.enabled:
            return self._profiles[DEFAULT_DOMAIN_PROFILE_ID]
        return profile

    def resolve(self, *, profile_id: Any = "", finance_mode: Any = "off") -> DomainProfile:
        mode = normalize_finance_mode(finance_mode)
        requested = str(profile_id or "").strip().lower()
        if mode not in {"qa", "push"}:
            return self._profiles[DEFAULT_DOMAIN_PROFILE_ID]
        if requested and requested != FINANCE_DOMAIN_PROFILE_ID:
            return self._profiles[DEFAULT_DOMAIN_PROFILE_ID]
        return self.get(FINANCE_DOMAIN_PROFILE_ID)


def resolve_turn_domain_context(payload: dict[str, Any] | None) -> tuple[DomainProfile, str]:
    source = payload if isinstance(payload, dict) else {}
    mode = normalize_finance_mode(source.get("finance_mode"))
    profile = DomainProfileRegistry().resolve(
        profile_id=source.get("domain_profile"),
        finance_mode=mode,
    )
    if profile.id != FINANCE_DOMAIN_PROFILE_ID:
        return profile, "off"
    if mode == "push" and not profile.proactive_delivery_enabled:
        mode = "qa"
    return profile, mode


def build_domain_profile_prompt(profile: DomainProfile | None) -> str:
    if profile is None or not profile.enabled or profile.id == DEFAULT_DOMAIN_PROFILE_ID:
        return ""
    blocks = {
        FINANCE_PROMPT_BLOCK_ID: FINANCE_PROMPT_BLOCK,
    }
    return "\n\n".join(
        blocks[block_id]
        for block_id in profile.prompt_block_ids
        if block_id in blocks and str(blocks[block_id]).strip()
    )


def filter_tool_names(tool_names: tuple[str, ...] | list[str], profile: DomainProfile | None) -> tuple[str, ...]:
    names = tuple(str(name or "").strip() for name in tool_names if str(name or "").strip())
    if profile is None or profile.id == DEFAULT_DOMAIN_PROFILE_ID:
        return names
    allowed = set(profile.allowed_tool_names)
    hidden = set(profile.hidden_tool_names)
    return tuple(name for name in names if name in allowed and name not in hidden)


def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))
