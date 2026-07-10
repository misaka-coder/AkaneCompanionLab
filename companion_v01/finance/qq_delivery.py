from __future__ import annotations

from typing import Any

import config
from services.market_data import FinanceSubscription

from ..qq_gateway import QQMessageContext
from .event_contracts import (
    FinanceAnalysisResult,
    FinanceDeliveryAuthorization,
    FinanceDeliveryPartSpec,
    FinanceDeliveryResult,
    build_finance_delivery_parts,
)


class QQFinanceDeliveryAdapter:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def authorize(self, subscription: FinanceSubscription) -> FinanceDeliveryAuthorization:
        if not bool(getattr(config, "FINANCE_ASSISTANT_ENABLED", False)):
            return FinanceDeliveryAuthorization(False, "disabled", "finance_assistant_disabled")
        if not bool(getattr(config, "QQ_FINANCE_PUSH_ENABLED", False)):
            return FinanceDeliveryAuthorization(False, "disabled", "qq_finance_push_disabled")
        if not bool(getattr(config, "QQ_BRIDGE_ENABLED", False)):
            return FinanceDeliveryAuthorization(False, "disabled", "qq_bridge_disabled")
        if not subscription.enabled:
            return FinanceDeliveryAuthorization(False, "disabled", "subscription_disabled")
        if subscription.client != "qq":
            return FinanceDeliveryAuthorization(False, "unsupported_client", "subscription_client_not_qq")
        if subscription.finance_mode != "push":
            return FinanceDeliveryAuthorization(False, "mode_rejected", "subscription_not_in_push_mode")
        try:
            target_id = int(subscription.target_id)
        except (TypeError, ValueError):
            target_id = 0
        if target_id <= 0:
            return FinanceDeliveryAuthorization(False, "invalid_target", "qq_target_id_invalid")
        return FinanceDeliveryAuthorization(True, "authorized")

    def deliver(
        self,
        *,
        subscription: FinanceSubscription,
        analysis: FinanceAnalysisResult,
    ) -> FinanceDeliveryResult:
        authorization = self.authorize(subscription)
        if not authorization.allowed:
            return FinanceDeliveryResult(False, authorization.status, authorization.reason)
        if not analysis.ok or not analysis.messages:
            return FinanceDeliveryResult(False, "invalid_analysis", analysis.reason or "empty_analysis_messages")
        parts = build_finance_delivery_parts(analysis)
        if not parts:
            return FinanceDeliveryResult(False, "invalid_analysis", "analysis_has_no_delivery_parts")
        results = [self.deliver_part(subscription=subscription, part=part) for part in parts]
        ok = all(result.ok for result in results)
        return FinanceDeliveryResult(
            ok,
            "delivered" if ok else "failed",
            next((result.reason for result in results if not result.ok and result.reason), ""),
            detail={
                "parts": [
                    {
                        "part_key": part.part_key,
                        "part_type": part.part_type,
                        "ok": result.ok,
                        "status": result.status,
                        "reason": result.reason,
                        "detail": dict(result.detail),
                    }
                    for part, result in zip(parts, results, strict=True)
                ]
            },
        )

    def deliver_part(
        self,
        *,
        subscription: FinanceSubscription,
        part: FinanceDeliveryPartSpec,
    ) -> FinanceDeliveryResult:
        authorization = self.authorize(subscription)
        if not authorization.allowed:
            return FinanceDeliveryResult(False, authorization.status, authorization.reason)
        context = self._build_context(subscription)
        try:
            if part.part_type == "text":
                message = str(part.payload.get("message") or "").strip()
                if not message:
                    return FinanceDeliveryResult(False, "invalid_part", "empty_text_delivery_part")
                result = self.gateway.send_replies(context, [message])
            elif part.part_type == "chart":
                sender = getattr(self.gateway, "send_market_charts", None)
                if not callable(sender):
                    return FinanceDeliveryResult(False, "unavailable", "market_chart_sender_unavailable")
                event = part.payload.get("tool_event")
                if not isinstance(event, dict):
                    return FinanceDeliveryResult(False, "invalid_part", "market_chart_event_missing")
                result = sender(context, [event], authorization="finance_subscription_push")
            elif part.part_type == "report":
                sender = getattr(self.gateway, "send_finance_reports", None)
                if not callable(sender):
                    return FinanceDeliveryResult(False, "unavailable", "finance_report_sender_unavailable")
                event = part.payload.get("tool_event")
                if not isinstance(event, dict):
                    return FinanceDeliveryResult(False, "invalid_part", "finance_report_event_missing")
                result = sender(context, [event], authorization="finance_subscription_push")
            else:
                return FinanceDeliveryResult(False, "invalid_part", "unsupported_delivery_part_type")
        except Exception as exc:
            return FinanceDeliveryResult(
                False,
                "failed",
                f"{part.part_type}_delivery_exception:{type(exc).__name__}",
            )
        return self._normalize_gateway_result(result, part_type=part.part_type)

    @staticmethod
    def _normalize_gateway_result(result: Any, *, part_type: str) -> FinanceDeliveryResult:
        if not isinstance(result, dict):
            return FinanceDeliveryResult(False, "invalid_gateway_result", "gateway_result_not_object")
        try:
            delivered_count = int(result.get("count") or 0)
        except (TypeError, ValueError):
            delivered_count = 0
        if bool(result.get("ok")) and (part_type == "text" or delivered_count > 0):
            return FinanceDeliveryResult(True, "delivered", detail=result)
        reason = str(result.get("reason") or "").strip()
        if not reason:
            failures = [
                str(item.get("reason") or "").strip()
                for item in list(result.get("results") or [])
                if isinstance(item, dict) and not bool(item.get("ok"))
            ]
            reason = next((item for item in failures if item), "qq_send_failed")
        if bool(result.get("ok")) and part_type in {"chart", "report"}:
            reason = f"{part_type}_delivery_target_missing"
        return FinanceDeliveryResult(False, str(result.get("status") or "failed"), reason, detail=result)

    @staticmethod
    def _build_context(subscription: FinanceSubscription) -> QQMessageContext:
        target_id = int(subscription.target_id)
        return QQMessageContext(
            should_respond=True,
            reason="finance_subscription_push",
            is_group=bool(subscription.is_group),
            target_id=target_id,
            user_id=0 if subscription.is_group else target_id,
            group_id=target_id if subscription.is_group else 0,
            session_id=subscription.session_id,
            profile_user_id=subscription.profile_user_id,
            character_pack_id=subscription.character_pack_id,
            finance_mode="push",
            clean_message="",
            raw_message="",
            attachments=[],
        )
