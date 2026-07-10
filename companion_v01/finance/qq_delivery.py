from __future__ import annotations

from typing import Any

import config
from services.market_data import FinanceSubscription

from ..qq_gateway import QQMessageContext
from .event_contracts import (
    FinanceAnalysisResult,
    FinanceDeliveryAuthorization,
    FinanceDeliveryResult,
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

        target_id = int(subscription.target_id)
        context = QQMessageContext(
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
        result = self.gateway.send_replies(context, list(analysis.messages))
        if not isinstance(result, dict):
            return FinanceDeliveryResult(False, "invalid_gateway_result", "gateway_result_not_object")
        if not bool(result.get("ok")):
            reason = str(result.get("reason") or "")
            if not reason:
                failures = [
                    str(item.get("reason") or "")
                    for item in list(result.get("results") or [])
                    if isinstance(item, dict) and not bool(item.get("ok"))
                ]
                reason = next((item for item in failures if item), "qq_send_failed")
            return FinanceDeliveryResult(False, "failed", reason, detail=result)
        return FinanceDeliveryResult(True, "delivered", detail=result)
