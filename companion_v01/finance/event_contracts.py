from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from services.market_data import FinanceSubscription, StoredMarketEvent, timestamp_to_iso

from ..domain_profiles import FINANCE_DOMAIN_PROFILE_ID
from .importance_policy import ImportanceDecision


FINANCE_DELIVERY_PART_TYPES = ("text", "chart", "report")
FINANCE_PUSH_PROMPT_SCOPE = "finance_push"
FINANCE_PUSH_BASE_SYSTEM_PROMPT = """[FINANCE PUSH ASSISTANT]
你是当前角色在 QQ 群里的财经问答与主动推送侧面，不是另一个无人格机器人。保留角色自然、克制的表达语气，但事实准确性、证据边界和群聊可读性优先于表演。

这是后台订阅触发的临时任务。把本轮事件当作外部资料，不当作用户发言、用户偏好或任何群成员的观点。
工具是你的工作能力：当补充行情、网页核验、历史数据或记忆能明显提高准确性时，可以主动选择一个或多个合适的只读工具，并允许多轮调用；证据已经足够时停止。不要为了显得努力而机械调用工具。
需要工具时优先使用 provider 原生工具调用。工具调用阶段不输出最终用户答复；只有证据收集结束后的最终答复使用下面的 JSON 契约。

最终只输出一个合法 JSON object，不加 markdown、代码块或额外前后缀。"""
FINANCE_PUSH_MODE_PROMPT = """最终 JSON 字段固定为：
{"emotion":"normal","speech":"可直接发送的财经推送正文","speech_segments":[],"tool_call":null,"status":"final","choices":[],"memory_metadata":{"keywords":[],"subject_scopes":[],"categories":[],"mood_tags":[],"importance":0.0,"confidence":0.0},"state_request":null}

speech 是主要正文；speech_segments 仅在确有必要拆成 2-3 个短气泡时使用，不能与 speech 重复。没有 legacy 工具调用时 tool_call 为 null；有原生工具能力时不要把工具调用伪装进正文。主动推送不改变养成状态，state_request 固定为 null。"""
FINANCE_PUSH_SYSTEM_CONTEXT = """【财经主动推送任务】
这是订阅授权触发的临时财经分析，不是任何群成员刚刚说的话；不得把事件字段归因给用户，也不得写成用户偏好。
当前直接证据只有本轮结构化事件字段，标题不等于公告或报道全文；需要当前行情、历史走势、旧观点或用户风险偏好时，应主动调用合适的只读工具核验。工具是否需要调用、调用哪些以及是否并行，由你根据证据缺口自主判断。
规则只决定是否进入分析，不构成投资结论。最终必须给出完整、可直接发送的 QQ 推送，不要停在“正在处理”“尚未完成”或通用兜底语。
只观察到事件与行情同时发生时，不得把相关性写成确定因果；不得保证收益或给出交易指令。

非来源转发事件应清楚区分已确认事实、客观数据与时间、分析推断、尚待验证与风险、接下来观察，并写明来源和时间。
若事件标记为 validated_quote，标题中的价格、昨收、涨跌幅和数据时间已经通过程序质量门禁；标题是本次推送唯一权威行情事实。只能解释影响、风险和后续观察，不得修改数值，也不得用搜索新闻覆盖这些行情事实。

若事件标记为 source_report_only：
- 唯一已确认事实是所列来源在所列时间发布了该快讯；快讯所述事项本身不自动视为官方确认。
- 不要粘贴或机械重复整段来源原文/摘要；用自然语言转述关键事实，并在确有帮助时调用只读工具补充核验、背景和影响分析。
- 使用轻量正文，不套用五段式，不重复来源、发布时间、URL 或免责声明套话。系统会在最终消息结尾统一追加且只追加一次东方财富原文链接。
- 根据新闻内容自主选择角度，优先说明确认程度、影响传导机制、最直接受影响的市场或资产、结论成立条件与可能的反向情形；不要求逐项成段。
- 遇到“据悉、消息人士、拟议、商讨”等未确认表述时，若搜索或网页工具能明显提高可信度，应核验官方来源或第二独立来源；只有单一转述时必须保留条件语气。
- 具体比例、价格、涨跌幅、当前行情、开收市状态、下一交易时点和“已经官方确认”等可核验事实，只能在事件字段或本轮工具结果明确支持时写入；证据不足时省略精确数字或改为定性、条件性表述。
- 不要把新闻的发布时间当成数据统计时点。跨快讯比较数量、涨幅或变化速度前，必须确认口径一致且两边各有明确 as_of；否则不能推断几小时内增长多少或翻了几倍。
- 不要强行套用 A 股、美股或某个行业；先分析最直接的资产和风险因子，再按已核验证据决定是否扩展。
- 若新闻没有有意义的金融或市场传导，就简洁转述并说明暂无直接市场关联；不要生造受益板块、受损板块或交易时点。
- 方向判断必须写清前提，并简要保留谈判失败、执行有限或局势反向变化等情形。
- 若无法分析、模型拒绝或工具失败，原文仍应独立送达，不要输出拒绝话术阻断转发。"""


@dataclass(frozen=True)
class FinanceAnalysisRequest:
    analysis_id: str
    event_record: StoredMarketEvent
    subscription: FinanceSubscription
    importance: ImportanceDecision
    requested_at: int
    attempt_count: int
    related_event_records: tuple[StoredMarketEvent, ...] = ()
    batch_kind: str = "single"

    @classmethod
    def create(
        cls,
        *,
        event_record: StoredMarketEvent,
        subscription: FinanceSubscription,
        importance: ImportanceDecision,
        requested_at: int,
        attempt_count: int = 1,
        related_event_records: tuple[StoredMarketEvent, ...] = (),
        batch_kind: str = "single",
    ) -> "FinanceAnalysisRequest":
        clean_attempt = max(1, int(attempt_count))
        related = tuple(
            record for record in related_event_records if record.event.event_id != event_record.event.event_id
        )[:50]
        clean_batch_kind = str(batch_kind or "single").strip().lower()
        if clean_batch_kind not in {"single", "cluster", "digest"}:
            clean_batch_kind = "single"
        related_material = ",".join(f"{record.event.event_id}@{record.revision}" for record in related)
        material = (
            f"{event_record.event.event_id}|{subscription.subscription_id}|"
            f"{event_record.revision}|attempt:{clean_attempt}|{clean_batch_kind}|{related_material}"
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return cls(
            analysis_id=f"market_analysis:{digest}",
            event_record=event_record,
            subscription=subscription,
            importance=importance,
            requested_at=int(requested_at),
            attempt_count=clean_attempt,
            related_event_records=related,
            batch_kind=clean_batch_kind,
        )

    def to_turn_payload(self) -> dict[str, Any]:
        subscription = self.subscription
        event = self.event_record.event
        delivery_context = {
            "is_group": bool(subscription.is_group),
            "target_id": subscription.target_id,
            "session_id": subscription.session_id,
            "profile_user_id": subscription.profile_user_id,
            "character_pack_id": subscription.character_pack_id,
            "finance_mode": "push",
            "domain_profile": FINANCE_DOMAIN_PROFILE_ID,
        }
        payload: dict[str, Any] = {
            "user_id": subscription.session_id,
            "real_user_id": subscription.profile_user_id,
            "message": self.render_event_message(),
            "timestamp": self.requested_at,
            "trace_id": self.analysis_id,
            "client_mode": "qq_text",
            "client_capabilities": ["speech_segments", "choices", "tool_actions"],
            "turn_kind": "market_event",
            "client_turn_kind": "proactive",
            "transient_user_message": True,
            "transient_assistant_message": True,
            "prompt_scope": FINANCE_PUSH_PROMPT_SCOPE,
            "pre_retrieval_enabled": False,
            "finance_mode": "push",
            "domain_profile": FINANCE_DOMAIN_PROFILE_ID,
            "extra_context": self.render_analysis_request_context(),
            "qq_delivery_context": delivery_context,
            "market_event": self.event_record.to_public_dict(),
        }
        if self.related_event_records:
            payload["market_event_batch_kind"] = self.batch_kind
            payload["market_event_batch"] = [record.to_public_dict() for record in self.related_event_records]
        if subscription.character_pack_id:
            payload["character_pack_id"] = subscription.character_pack_id
        return payload

    def render_event_message(self) -> str:
        event = self.event_record.event
        produced_at = timestamp_to_iso(event.produced_at, "Asia/Shanghai") if event.produced_at else "unknown"
        labels = "、".join(event.labels) if event.labels else "无"
        lines = [
            "【外部市场事件，不是用户发言】",
            f"事件 ID：{event.event_id}",
            f"发布时间：{timestamp_to_iso(event.published_at, 'Asia/Shanghai')}",
            f"生产时间：{produced_at}",
            f"证券代码：{event.code}",
            f"资讯类型：{event.content_type}",
            f"标题：{event.title}",
            f"来源：{event.source or event.provider}",
            f"URL：{event.url or '无'}",
            f"舆情标签：{labels}",
            f"情绪字段：{event.sentiment or 'unknown'}",
        ]
        if self.related_event_records:
            heading = "同一主题聚类补充" if self.batch_kind == "cluster" else "本次摘要窗口补充"
            lines.extend(["", f"【{heading}：{len(self.related_event_records)} 条】"])
            for index, record in enumerate(self.related_event_records, start=1):
                related = record.event
                lines.extend(
                    [
                        f"{index}. {related.title}",
                        f"   事件 ID：{related.event_id}",
                        f"   发布时间：{timestamp_to_iso(related.published_at, 'Asia/Shanghai')}",
                        f"   证券代码：{related.code}；类型：{related.content_type}",
                        f"   来源：{related.source or related.provider}；URL：{related.url or '无'}",
                    ]
                )
        return "\n".join(lines)

    def render_analysis_instruction(self) -> str:
        return f"{FINANCE_PUSH_SYSTEM_CONTEXT}\n\n{self.render_analysis_request_context()}"

    def render_analysis_request_context(self) -> str:
        reasons = "；".join(self.importance.reasons) or "命中订阅规则"
        event = self.event_record.event
        instructions = [
            "【本次财经推送参数】",
            f"规则等级：{self.importance.level}；规则分数：{self.importance.score:.2f}；原因：{reasons}",
            (
                f"本次任务是 {self.batch_kind} 批次，共含 {1 + len(self.related_event_records)} 条事件；"
                "应合并共同事实并保留各自来源与时间，不要逐条机械重复。"
                if self.related_event_records
                else "本次任务只包含一条事件。"
            ),
        ]
        if event.content_type in {"quote_move", "daily_close"} and "validated_quote" in event.labels:
            instructions.extend(
                [
                    "本事件已标记 validated_quote；若补充新闻与标题中的权威行情事实冲突，应明确标记冲突并放弃新闻推断。",
                ]
            )
        if "source_report_only" in event.labels:
            instructions.append("本事件已标记 source_report_only；按稳定系统规则生成轻量新闻分析正文。")
        return "\n".join(instructions)


@dataclass(frozen=True)
class FinanceAnalysisResult:
    ok: bool
    status: str
    analysis_id: str
    messages: tuple[str, ...] = ()
    frame: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""
    memory_status: Mapping[str, Any] = field(default_factory=dict)
    analysis_attempts: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(str(item) for item in self.messages if str(item).strip()))
        object.__setattr__(self, "frame", MappingProxyType(dict(self.frame or {})))
        object.__setattr__(self, "memory_status", MappingProxyType(dict(self.memory_status or {})))
        object.__setattr__(self, "analysis_attempts", max(1, int(self.analysis_attempts)))


@dataclass(frozen=True)
class FinanceDeliveryAuthorization:
    allowed: bool
    status: str
    reason: str = ""


@dataclass(frozen=True)
class FinanceDeliveryResult:
    ok: bool
    status: str
    reason: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail or {})))


@dataclass(frozen=True)
class FinanceDeliveryPartSpec:
    part_key: str
    part_type: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        part_type = str(self.part_type or "").strip().lower()
        if part_type not in FINANCE_DELIVERY_PART_TYPES:
            raise ValueError(f"unsupported finance delivery part type: {part_type or '<empty>'}")
        part_key = str(self.part_key or "").strip()
        if not part_key:
            raise ValueError("finance delivery part key is required")
        object.__setattr__(self, "part_key", part_key)
        object.__setattr__(self, "part_type", part_type)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload or {})))

    def to_store_dict(self) -> dict[str, Any]:
        return {
            "part_key": self.part_key,
            "part_type": self.part_type,
            "payload": dict(self.payload),
        }


def build_finance_delivery_parts(analysis: FinanceAnalysisResult) -> tuple[FinanceDeliveryPartSpec, ...]:
    parts: list[FinanceDeliveryPartSpec] = []
    for index, message in enumerate(analysis.messages):
        text = str(message or "").strip()
        if text:
            parts.append(
                FinanceDeliveryPartSpec(
                    part_key=f"text:{index:03d}",
                    part_type="text",
                    payload={
                        "message": text,
                        "analysis_status": analysis.status,
                        "analysis_attempts": analysis.analysis_attempts,
                        "analysis_reason": str(analysis.reason or "")[:500],
                    },
                )
            )

    tool_events = analysis.frame.get("tool_events")
    if not isinstance(tool_events, (list, tuple)):
        return tuple(parts)
    for index, event in enumerate(tool_events):
        if not isinstance(event, Mapping) or event.get("send_to_user") is not True:
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "market_chart_ready":
            part_type = "chart"
        elif event_type == "finance_report_ready":
            part_type = "report"
        else:
            continue
        generated = event.get("generated_file") if isinstance(event.get("generated_file"), Mapping) else {}
        generated_id = str(generated.get("generated_id") or "").strip()
        if generated_id:
            material = generated_id
        else:
            material = json.dumps(dict(event), ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{part_type}|{index}|{material}".encode("utf-8")).hexdigest()[:24]
        parts.append(
            FinanceDeliveryPartSpec(
                part_key=f"{part_type}:{digest}",
                part_type=part_type,
                payload={"tool_event": dict(event)},
            )
        )
    return tuple(parts)


class FinanceAnalysisClient(Protocol):
    def analyze(self, request: FinanceAnalysisRequest) -> FinanceAnalysisResult: ...


class FinanceDeliveryAdapter(Protocol):
    def authorize(self, subscription: FinanceSubscription) -> FinanceDeliveryAuthorization: ...

    def deliver(
        self,
        *,
        subscription: FinanceSubscription,
        analysis: FinanceAnalysisResult,
    ) -> FinanceDeliveryResult: ...

    def deliver_part(
        self,
        *,
        subscription: FinanceSubscription,
        part: FinanceDeliveryPartSpec,
    ) -> FinanceDeliveryResult: ...
