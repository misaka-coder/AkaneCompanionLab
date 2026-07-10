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
        chart_result = {"ok": True, "status": "skipped", "count": 0, "results": []}
        report_result = {"ok": True, "status": "skipped", "count": 0, "results": []}
        chart_sender = getattr(self.gateway, "send_market_charts", None)
        if callable(chart_sender):
            try:
                chart_result = chart_sender(
                    context,
                    list(analysis.frame.get("tool_events") or []),
                    authorization="finance_subscription_push",
                )
            except Exception as exc:
                chart_result = {
                    "ok": False,
                    "status": "failed",
                    "reason": f"market_chart_delivery_failed:{type(exc).__name__}",
                    "count": 0,
                    "results": [],
                }
        report_sender = getattr(self.gateway, "send_finance_reports", None)
        if callable(report_sender):
            try:
                report_result = report_sender(
                    context,
                    list(analysis.frame.get("tool_events") or []),
                    authorization="finance_subscription_push",
                )
            except Exception as exc:
                report_result = {
                    "ok": False,
                    "status": "failed",
                    "reason": f"finance_report_delivery_failed:{type(exc).__name__}",
                    "count": 0,
                    "results": [],
                }
        artifact_failed = any(
            str(item.get("status") or "").strip().lower() == "failed" for item in (chart_result, report_result)
        )
        if artifact_failed:
            notice_sender = getattr(self.gateway, "send_reply", None)
            if callable(notice_sender):
                try:
                    notice_sender(context, "本次金融产物已经生成，但 QQ 图片或文件发送失败；文字分析仍然有效。")
                except Exception:
                    pass
        artifact_ok = bool(chart_result.get("ok")) and bool(report_result.get("ok"))
        return FinanceDeliveryResult(
            True,
            "delivered" if artifact_ok else "delivered_with_artifact_failure",
            detail={"text_result": result, "chart_result": chart_result, "report_result": report_result},
        )
