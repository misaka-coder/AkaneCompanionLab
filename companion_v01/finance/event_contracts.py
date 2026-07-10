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
            "finance_mode": "push",
            "domain_profile": FINANCE_DOMAIN_PROFILE_ID,
            "extra_context": self.render_analysis_instruction(),
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
        reasons = "；".join(self.importance.reasons) or "命中订阅规则"
        return "\n".join(
            [
                "【财经主动推送任务】",
                "这是订阅授权触发的临时分析，不是任何群成员刚刚说的话；不得把事件字段归因给用户，也不得写成用户偏好。",
                "当前直接证据只有上方结构化事件字段，标题不等于公告或报道全文；需要当前行情、历史走势、旧观点或用户风险偏好时，应主动调用可用只读工具核验。",
                "规则只决定是否进入分析，不构成投资结论。",
                f"规则等级：{self.importance.level}；规则分数：{self.importance.score:.2f}；原因：{reasons}",
                (
                    f"本次任务是 {self.batch_kind} 批次，共含 {1 + len(self.related_event_records)} 条事件；"
                    "应合并共同事实并保留各自来源与时间，不要逐条机械重复。"
                    if self.related_event_records
                    else "本次任务只包含一条事件。"
                ),
                "最终必须给出完整、可直接发送的 QQ 推送，不要停在‘正在处理’或‘尚未完成’。",
                "重要推送要清楚区分：已确认事实、客观数据与时间、分析推断、尚待验证与风险、接下来观察，并写明来源和时间。",
                "只观察到事件与行情同时发生时，不得把相关性写成确定因果；不得保证收益或给出交易指令。",
            ]
        )


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
                    payload={"message": text},
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
