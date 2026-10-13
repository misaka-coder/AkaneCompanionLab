"""QQ outbound delivery policy and its owned state."""
from __future__ import annotations
import threading
import time
import uuid
from typing import Any
from channelcore_onebot import ReplyPart, TextPart, parse_message_chain
from ..onebot_model_actions import (
    MODEL_ONEBOT_MESSAGE_CREATING_ACTIONS,
    authorize_model_onebot_action,
    model_onebot_action_is_user_visible,
    model_onebot_capabilities,
    resolve_model_onebot_message_selector,
    resolve_model_onebot_params,
)
from ..plugin_api import (
    AFTER_DELIVERY_HOOK,
    BEFORE_OUTBOUND_PLAN_HOOK,
    PluginDeliverySnapshot,
    PluginHookEnvelope,
    PluginOutboundPlanSnapshot,
)
from ..qq_miniapp import MiniappCardStore, project_miniapp_result, validate_miniapp_params
from .context import QQMessageContext


class QQOutboundRuntime:
    """Own outbound behavior; transport and cross-domain operations use the injected port."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway
        self._miniapp_cards = MiniappCardStore()
        self._recent_bot_message_ids: dict[str, list[str]] = {}
        self._recent_bot_message_lock = threading.RLock()
        self._plugin_hook_broker: Any = None

    def bind_plugin_hook_broker(self, broker: Any | None) -> None:
        """Bind the shared PluginHost Hook broker to the QQ delivery boundary."""

        if broker is not None and (
            not callable(getattr(broker, "observes", None))
            or not callable(getattr(broker, "dispatch_from_consumer", None))
        ):
            raise TypeError("invalid_plugin_hook_broker")
        self._plugin_hook_broker = broker

    def _call_onebot_action(
        self,
        action: str,
        params: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> object:
        result = self.gateway._onebot_transport.call(action, dict(params), timeout=timeout_seconds)
        if not result.ok and result.code not in {"onebot_status_error", "onebot_retcode_error"}:
            raise RuntimeError(result.code)
        return {
            "status": "ok" if result.ok else "failed",
            "retcode": 0 if result.ok else -1,
            "data": result.data,
        }

    def call_model_onebot_action(
        self,
        context: QQMessageContext,
        *,
        action: str,
        params: dict[str, Any],
        message_selector: dict[str, Any] | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        """Execute one explicitly exposed model action against the bound Bot.

        The model never receives the endpoint, token or transport exception.
        Cross-conversation authority is derived from the triggering QQ actor,
        not from model-provided parameters.
        """

        clean_action = str(action or "").strip().lstrip("/")
        if clean_action == "capabilities":
            return model_onebot_capabilities()
        is_master = bool(self.gateway.master_qq) and str(int(context.user_id or 0)) == self.gateway.master_qq
        selector = dict(message_selector) if isinstance(message_selector, dict) else None
        recent_message_id = ""
        if selector and str(selector.get("kind") or "").strip() == "recent_bot_message":
            try:
                position = int(selector.get("position") or 1)
            except (TypeError, ValueError):
                position = 0
            if position < 1 or position > 64:
                return {
                    "ok": False,
                    "status": "invalid",
                    "reason": "recent_bot_message_position_invalid",
                    "action": clean_action or "unknown",
                }
            recent_message_id = self._recent_bot_message_id(context, position=position)
        replied_message_id = str((context.reply_reference or {}).get("message_id") or "").strip()
        selected_params, selector_applied, selector_error = resolve_model_onebot_message_selector(
            clean_action,
            params,
            selector,
            source_message_id=str(context.source_message_id or ""),
            replied_message_id=replied_message_id,
            recent_bot_message_id=recent_message_id,
        )
        if selector_error:
            return {
                "ok": False,
                "status": "unavailable" if selector_error.endswith("_unavailable") else "invalid",
                "reason": selector_error,
                "action": clean_action or "unknown",
            }
        resolved_params, defaults_applied = resolve_model_onebot_params(
            clean_action,
            selected_params,
            is_group=bool(context.is_group),
            group_id=int(context.group_id or 0),
            user_id=int(context.user_id or 0),
            source_message_id=str(context.source_message_id or ""),
        )
        allowed, reason, scope = authorize_model_onebot_action(
            clean_action,
            resolved_params,
            is_master=is_master,
            is_group=bool(context.is_group),
            group_id=int(context.group_id or 0),
            user_id=int(context.user_id or 0),
            source_message_id=str(context.source_message_id or ""),
            message_selector_applied=selector_applied,
            verified_forward_ids=context.verified_forward_ids,
        )
        if not allowed:
            return {
                "ok": False,
                "status": "forbidden",
                "reason": reason,
                "action": clean_action or "unknown",
                "scope": scope,
            }
        if clean_action in {"send_group_msg", "send_private_msg"} and "card_ref" in resolved_params:
            allowed_fields = {"card_ref", "group_id" if clean_action == "send_group_msg" else "user_id"}
            if set(resolved_params) - allowed_fields:
                return {"ok": False, "status": "invalid", "reason": "miniapp_card_params_conflict",
                        "action": clean_action, "scope": scope}
            target_id = resolved_params.get("group_id") if clean_action == "send_group_msg" else resolved_params.get("user_id")
            store = self._miniapp_cards
            send_params = {key: value for key, value in resolved_params.items() if key != "card_ref"}
            payload = store.send(
                resolved_params["card_ref"], scope=self._miniapp_card_scope(context),
                target=(clean_action, str(target_id)),
                sender=lambda message: self._call_and_track_outbound(
                    clean_action, {**send_params, "message": message}, timeout=timeout_seconds),
            )
            payload["scope"] = scope
            return payload
        if clean_action == "get_mini_app_ark":
            if "source" in resolved_params:
                if set(resolved_params) - {"source", "type"} or resolved_params.get("type", "bili") != "bili":
                    return {"ok": False, "status": "invalid", "reason": "miniapp_source_params_conflict",
                            "action": clean_action, "scope": scope}
                return {"ok": False, "status": "unavailable",
                        "reason": "bilibili_native_card_forward_required",
                        "action": clean_action, "scope": scope}
            validation_error = validate_miniapp_params(resolved_params)
            if validation_error:
                return {
                    "ok": False, "status": "invalid", "reason": validation_error,
                    "action": clean_action, "scope": scope,
                }
        payload = self._call_and_track_outbound(clean_action, resolved_params, timeout=timeout_seconds)
        if payload.get("ok") is True and payload.get("status") == "success":
            same_chat = (
                clean_action == "send_group_msg" and context.is_group
                and str(resolved_params.get("group_id")) == str(context.group_id)
            ) or (
                clean_action == "send_private_msg" and not context.is_group
                and str(resolved_params.get("user_id")) == str(context.user_id)
            )
            if same_chat:
                message = resolved_params.get("message")
                if isinstance(message, str) and resolved_params.get("auto_escape") is True:
                    delivered_text = message
                else:
                    chain = parse_message_chain({"message": message})
                    # Media/card/mention semantics cannot be reconciled as plain speech.
                    delivered_text = ("".join(part.value for part in chain.parts if isinstance(part, TextPart))
                                      if all(isinstance(part, (TextPart, ReplyPart)) for part in chain.parts) else "")
                if delivered_text.strip():
                    payload["current_conversation_text"] = delivered_text.strip()
        if clean_action == "get_mini_app_ark":
            payload = project_miniapp_result(payload, raw=resolved_params.get("rawArkData") == "true")
            if payload.get("ok") and payload.get("message"):
                store = self._miniapp_cards
                ref = store.put(self._miniapp_card_scope(context), payload["message"])
                # Keep raw provider data internal. Models get one send handle,
                # not two competing ways to copy and mutate the same card.
                payload = ({"ok": True, "status": "success", "action": clean_action,
                            "stage": "generated", "card_ref": ref, "expires_in_seconds": 900,
                            "title": resolved_params["title"], "web_url": resolved_params.get("webUrl", "")}
                           if ref else {"ok": False, "status": "unavailable", "action": clean_action,
                                        "reason": "miniapp_card_capacity_reached"})
        payload["scope"] = scope
        if selector_applied and selector_applied != "explicit_message_id":
            payload["message_selector_applied"] = selector_applied
        if defaults_applied:
            payload["defaults_applied"] = list(defaults_applied)
        return payload

    @staticmethod
    def _miniapp_card_scope(context: QQMessageContext) -> tuple:
        return (bool(context.is_group), int(context.group_id if context.is_group else context.user_id),
                int(context.user_id), context.session_id, context.character_pack_id)

    @staticmethod
    def _onebot_conversation_key(*, is_group: bool, target_id: Any) -> str:
        try:
            normalized = str(int(target_id or 0))
        except (TypeError, ValueError):
            normalized = str(target_id or "").strip()
        return f"{'group' if is_group else 'private'}:{normalized}" if normalized else ""

    def _recent_bot_message_id(self, context: QQMessageContext, *, position: int) -> str:
        key = self._onebot_conversation_key(is_group=bool(context.is_group), target_id=context.target_id)
        with self._recent_bot_message_lock:
            messages = list(self._recent_bot_message_ids.get(key) or ())
        return messages[position - 1] if key and 0 < position <= len(messages) else ""

    def _remember_successful_outbound(self, action: str, params: dict[str, Any], payload: dict[str, Any]) -> None:
        if not bool(payload.get("ok")):
            return
        if str(action or "").strip() not in MODEL_ONEBOT_MESSAGE_CREATING_ACTIONS:
            return
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        message_id = str(data.get("message_id") or "").strip()
        if not message_id:
            return
        group_id = params.get("group_id")
        user_id = params.get("user_id")
        key = self._onebot_conversation_key(
            is_group=bool(group_id),
            target_id=group_id if group_id else user_id,
        )
        if not key:
            return
        with self._recent_bot_message_lock:
            ledger = self._recent_bot_message_ids
            messages = list(ledger.get(key) or ())
            if message_id in messages:
                messages.remove(message_id)
            messages.insert(0, message_id)
            ledger[key] = messages[:64]

    def _call_and_track_outbound(
        self,
        action: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        clean_action = str(action or "").strip().lstrip("/") or "unknown"
        effective_params = dict(params)
        observable = self._is_user_visible_outbound_action(clean_action)
        observes_before = observable and self._plugin_hook_observes(BEFORE_OUTBOUND_PLAN_HOOK)
        observes_after = observable and self._plugin_hook_observes(AFTER_DELIVERY_HOOK)
        delivery_id = f"qq-delivery-{uuid.uuid4().hex}" if observes_before or observes_after else ""

        if observes_before:
            plan_snapshot = self._plugin_outbound_plan_snapshot(
                delivery_id=delivery_id,
                action=clean_action,
                params=effective_params,
            )
            dispatch = self._dispatch_plugin_hook(
                PluginHookEnvelope(
                    hook_id=f"{delivery_id}:before",
                    hook_type=BEFORE_OUTBOUND_PLAN_HOOK,
                    occurred_at=int(time.time()),
                    subject=self._plugin_outbound_subject(plan_snapshot),
                    payload=plan_snapshot,
                )
            )
            effective_params = self._apply_plugin_outbound_decorations(
                effective_params,
                dispatch,
                enabled=plan_snapshot.text_decoratable,
            )

        transport_started_at = time.perf_counter()
        payload = self.gateway._onebot_transport.call(clean_action, effective_params, timeout=timeout).as_dict()
        duration_ms = max(0.0, (time.perf_counter() - transport_started_at) * 1000)
        self._remember_successful_outbound(clean_action, effective_params, payload)

        if observes_after:
            plan_snapshot = self._plugin_outbound_plan_snapshot(
                delivery_id=delivery_id,
                action=clean_action,
                params=effective_params,
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            reason = "" if bool(payload.get("ok")) else str(
                payload.get("code") or payload.get("public_reason") or "delivery_failed"
            ).strip()
            self._dispatch_plugin_hook(
                PluginHookEnvelope(
                    hook_id=f"{delivery_id}:after",
                    hook_type=AFTER_DELIVERY_HOOK,
                    occurred_at=int(time.time()),
                    subject=self._plugin_outbound_subject(plan_snapshot),
                    payload=PluginDeliverySnapshot(
                        delivery_id=delivery_id,
                        channel="qq",
                        action=clean_action,
                        conversation_kind=plan_snapshot.conversation_kind,
                        target_id=plan_snapshot.target_id,
                        segment_types=plan_snapshot.segment_types,
                        status="delivered" if bool(payload.get("ok")) else "failed",
                        duration_ms=round(duration_ms, 3),
                        reason=reason,
                        message_id=str(data.get("message_id") or "").strip(),
                    ),
                )
            )
        return payload

    @staticmethod
    def _is_user_visible_outbound_action(action: str) -> bool:
        return (
            action in MODEL_ONEBOT_MESSAGE_CREATING_ACTIONS
            or model_onebot_action_is_user_visible(action)
        )

    def _plugin_hook_observes(self, hook_type: str) -> bool:
        broker = self._plugin_hook_broker
        if broker is None:
            return False
        try:
            return bool(broker.observes(hook_type))
        except Exception:
            return False

    def _dispatch_plugin_hook(self, hook: PluginHookEnvelope) -> Any:
        broker = self._plugin_hook_broker
        if broker is None:
            return None
        try:
            return broker.dispatch_from_consumer(hook)
        except Exception:
            return None

    @staticmethod
    def _plugin_outbound_plan_snapshot(
        *,
        delivery_id: str,
        action: str,
        params: dict[str, Any],
    ) -> PluginOutboundPlanSnapshot:
        group_id = str(params.get("group_id") or "").strip()
        user_id = str(params.get("user_id") or "").strip()
        conversation_kind = "group" if group_id else "private" if user_id else ""
        target_id = group_id or user_id
        segment_types: list[str] = []
        text_parts: list[str] = []
        reply_to_message_id = ""
        message = params.get("message")
        if isinstance(message, str):
            segment_types.append("text")
            text_parts.append(message)
        elif isinstance(message, (list, tuple)):
            for segment in message:
                if not isinstance(segment, dict):
                    continue
                segment_type = str(segment.get("type") or "").strip().lower()
                if not segment_type:
                    continue
                segment_types.append(segment_type)
                data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
                if segment_type == "text":
                    text_parts.append(str(data.get("text") or ""))
                elif segment_type == "reply" and not reply_to_message_id:
                    reply_to_message_id = str(data.get("id") or "").strip()
        elif action in {"upload_group_file", "upload_private_file"}:
            segment_types.append("file")
        elif "forward" in action:
            segment_types.append("forward")
        elif action in {"group_poke", "friend_poke"}:
            segment_types.append("poke")
        elif action == "set_msg_emoji_like":
            segment_types.append("reaction")
        elif action == "delete_msg":
            segment_types.append("recall")
        elif action == "send_like":
            segment_types.append("like")
        visible_text = "".join(text_parts)
        return PluginOutboundPlanSnapshot(
            delivery_id=delivery_id,
            channel="qq",
            action=action,
            conversation_kind=conversation_kind,
            target_id=target_id,
            segment_types=tuple(segment_types),
            text=visible_text,
            reply_to_message_id=reply_to_message_id,
            text_decoratable=bool(visible_text) and action in {"send_group_msg", "send_private_msg"},
        )

    @staticmethod
    def _plugin_outbound_subject(snapshot: PluginOutboundPlanSnapshot) -> str:
        if snapshot.conversation_kind and snapshot.target_id:
            return f"qq:{snapshot.conversation_kind}:{snapshot.target_id}"
        return f"qq:action:{snapshot.action}"

    @staticmethod
    def _apply_plugin_outbound_decorations(
        params: dict[str, Any],
        dispatch: Any,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        if not enabled:
            return dict(params)
        contributions = getattr(dispatch, "outbound_decorations", ()) if dispatch is not None else ()
        prefix = "".join(item.text_prefix for _plugin_id, item in contributions)
        suffix = "".join(item.text_suffix for _plugin_id, item in contributions)
        if not prefix and not suffix:
            return dict(params)

        decorated = dict(params)
        message = decorated.get("message")
        if isinstance(message, str):
            decorated["message"] = f"{prefix}{message}{suffix}"
            return decorated
        if not isinstance(message, (list, tuple)):
            return decorated
        segments = [dict(item) if isinstance(item, dict) else item for item in message]
        text_indexes = [
            index
            for index, item in enumerate(segments)
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "text"
        ]
        if not text_indexes:
            return decorated
        first_index = text_indexes[0]
        last_index = text_indexes[-1]
        for index in {first_index, last_index}:
            item = dict(segments[index])
            item["data"] = dict(item.get("data") or {})
            segments[index] = item
        first_data = segments[first_index]["data"]
        first_data["text"] = prefix + str(first_data.get("text") or "")
        last_data = segments[last_index]["data"]
        last_data["text"] = str(last_data.get("text") or "") + suffix
        decorated["message"] = segments
        return decorated

    def self_check(self) -> dict[str, Any]:
        """Check login and online state without exposing endpoint or credentials."""
        if not self.gateway.bridge_enabled:
            return {
                "ok": False,
                "status": "bridge_disabled",
                "reason": "QQ_BRIDGE_ENABLED 未启用；在 .env 里设置 QQ_BRIDGE_ENABLED=true 并配置 NapCat。",
            }
        login_result = self.gateway._onebot_transport.call("get_login_info", timeout=5)
        if not login_result.ok:
            return self._self_check_failure(login_result.code, login_result.public_reason)
        user_id = login_result.data.get("user_id") or ""
        nickname = login_result.data.get("nickname") or ""
        if not str(user_id).isdigit() or int(str(user_id)) <= 0:
            return {
                "ok": False,
                "status": "account_identity_unknown",
                "reason": "OneBot 未返回实际登录 QQ，无法确认账号身份。",
            }
        self.gateway._resolve_bot_nickname(login_result=login_result)
        status_result = self.gateway._onebot_transport.call("get_status", timeout=5)
        if not status_result.ok:
            return self._self_check_failure(status_result.code, status_result.public_reason)
        status_data = status_result.data
        online = status_data.get("online", status_data.get("is_online"))
        if online is not True:
            return {
                "ok": False,
                "status": "account_offline" if online is False else "account_status_unknown",
                "reason": "QQ 账号当前不在线，请在 NapCat 中重新登录并确认未被下线。",
            }
        if status_data.get("good") is False:
            return {
                "ok": False,
                "status": "account_unhealthy",
                "reason": "QQ 账号在线，但 OneBot 报告运行状态异常。",
            }

        return {
            "ok": True,
            "status": "connected",
            "bot_qq": str(user_id),
            "nickname": str(nickname),
            "wake_word_mode": "disabled",
            "wake_word_status": "disabled",
            "wake_words": [],
            "checks": {
                "bridge_enabled": True,
                "url_reachable": True,
                "login_info": True,
                "account_online": True,
                "send_test": "not_tested",
            },
        }

    @staticmethod
    def _self_check_failure(code: str, public_reason: str) -> dict[str, Any]:
        status = {
            "invalid_base_url": "invalid_url",
            "timeout": "timeout",
            "connection_error": "unreachable",
            "auth_failed": "auth_failed",
            "http_error": "http_error",
            "redirect_rejected": "redirect_rejected",
            "invalid_json": "invalid_response",
            "invalid_response": "invalid_response",
            "onebot_status_error": "onebot_error",
            "onebot_retcode_error": "onebot_error",
        }.get(code, "connection_error")
        return {"ok": False, "status": status, "reason": public_reason or "OneBot 自检失败。"}
