from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from services.market_data import MarketDataValidationError, MarketEventStore, normalize_market_code

from ..domain_profiles import normalize_finance_mode


_WATCHLIST_LIST_COMMANDS = {
    "关注列表",
    "财经关注列表",
    "金融关注列表",
    "查看关注列表",
    "当前关注列表",
}
_WATCHLIST_ADD_RE = re.compile(r"^(?:(?:财经|金融)\s*)?(?:添加关注|加入关注|关注)[:：\s]+(.+)$")
_WATCHLIST_REMOVE_RE = re.compile(r"^(?:(?:财经|金融)\s*)?(?:取消关注|移除关注|删除关注)[:：\s]+(.+)$")
_FULL_PROVIDER_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9][A-Za-z0-9_-]*$")


class FinanceSubscriptionService:
    def __init__(self, *, store: MarketEventStore, provider_id: str = "choice_emquant") -> None:
        self.store = store
        self.provider_id = str(provider_id or "choice_emquant").strip().lower() or "choice_emquant"

    def sync_mode(self, context: Any, finance_mode: str) -> dict[str, Any]:
        mode = normalize_finance_mode(finance_mode)
        if mode not in {"off", "qa", "push"}:
            return {"ok": False, "status": "invalid_mode", "reason": "unsupported_finance_mode"}
        try:
            matches = self._matching_subscriptions(context)
            if mode != "push":
                disabled_ids: list[str] = []
                for subscription in matches:
                    if subscription.enabled:
                        self.store.set_subscription_enabled(subscription.subscription_id, enabled=False)
                        disabled_ids.append(subscription.subscription_id)
                return {
                    "ok": True,
                    "status": "disabled" if disabled_ids else "not_subscribed",
                    "subscription_id": disabled_ids[0] if disabled_ids else "",
                    "disabled_count": len(disabled_ids),
                    "watchlist_count": 0,
                }

            existing = matches[0] if matches else None
            duplicate_watchlist = [
                item for duplicate in matches[1:] for item in self.store.list_watchlist(duplicate.subscription_id)
            ]
            duplicate_ids = [subscription.subscription_id for subscription in matches[1:] if subscription.enabled]
            for duplicate_id in duplicate_ids:
                self.store.set_subscription_enabled(duplicate_id, enabled=False)
            subscription_id = (
                existing.subscription_id if existing is not None else self._subscription_id_for_context(context)
            )
            subscription = self.store.upsert_subscription(
                subscription_id=subscription_id,
                client="qq",
                target_id=str(int(getattr(context, "target_id", 0) or 0)),
                is_group=bool(getattr(context, "is_group", False)),
                session_id=str(getattr(context, "session_id", "") or ""),
                profile_user_id=str(getattr(context, "profile_user_id", "") or ""),
                character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
                finance_mode="push",
                enabled=True,
                filters=dict(existing.filters) if existing is not None else {},
                delivery_policy=(dict(existing.delivery_policy) if existing is not None else {"level": "notify"}),
                created_by_actor_id=(existing.created_by_actor_id if existing is not None else self._actor_id(context)),
            )
            for item in duplicate_watchlist:
                self.store.upsert_watchlist_item(
                    subscription_id=subscription.subscription_id,
                    provider=item.provider,
                    code=item.code,
                    display_name=item.display_name,
                    aliases=item.aliases,
                    priority=item.priority,
                    created_by_actor_id=item.created_by_actor_id,
                )
            watchlist_count = len(self._list_provider_watchlist(subscription.subscription_id))
            return {
                "ok": True,
                "status": "enabled",
                "subscription_id": subscription.subscription_id,
                "watchlist_count": watchlist_count,
                "is_group": subscription.is_group,
                "disabled_duplicate_count": len(duplicate_ids),
            }
        except MarketDataValidationError as exc:
            return {
                "ok": False,
                "status": "invalid_subscription",
                "reason": f"{exc.field}:{exc.reason}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "subscription_failed",
                "reason": type(exc).__name__,
            }

    def parse_watchlist_command(self, message: str) -> dict[str, str] | None:
        text = self._normalize_command_text(message)
        if not text:
            return None
        if text in _WATCHLIST_LIST_COMMANDS:
            return {"action": "list"}
        add_match = _WATCHLIST_ADD_RE.fullmatch(text)
        if add_match:
            return {"action": "add", "query": add_match.group(1).strip()[:160]}
        remove_match = _WATCHLIST_REMOVE_RE.fullmatch(text)
        if remove_match:
            return {"action": "remove", "query": remove_match.group(1).strip()[:160]}
        return None

    def handle_watchlist_command(
        self,
        context: Any,
        *,
        authorized: bool,
    ) -> dict[str, Any] | None:
        command = self.parse_watchlist_command(str(getattr(context, "clean_message", "") or ""))
        if command is None:
            return None
        if bool(getattr(context, "is_group", False)) and command.get("action") != "list" and not authorized:
            return {
                "handled": True,
                "ok": False,
                "status": "forbidden",
                "reply": "群聊财经关注列表只能由主人、群主或管理员修改。",
            }
        subscription = self._enabled_push_subscription(context)
        if subscription is None:
            return {
                "handled": True,
                "ok": False,
                "status": "subscription_required",
                "reply": "当前 QQ 会话还没有启用财经推送，请先发送“开启财经推送”。",
            }

        action = command["action"]
        if action == "list":
            items = self._list_provider_watchlist(subscription.subscription_id)
            if not items:
                reply = "当前财经关注列表为空。发送“关注 证券代码或精确名称”即可添加。"
            else:
                lines = [f"当前财经关注列表（{len(items)} 项）"]
                for index, item in enumerate(items[:30], start=1):
                    label = item.display_name or item.code
                    lines.append(f"{index}. {label}（{item.code}）")
                if len(items) > 30:
                    lines.append(f"还有 {len(items) - 30} 项未显示。")
                reply = "\n".join(lines)
            return {
                "handled": True,
                "ok": True,
                "status": "listed",
                "reply": reply,
                "subscription_id": subscription.subscription_id,
                "watchlist_count": len(items),
            }

        query = str(command.get("query") or "").strip()
        resolved = self._resolve_watchlist_target(
            query,
            subscription_id=subscription.subscription_id,
            profile_user_id=subscription.profile_user_id,
            session_id=subscription.session_id,
        )
        if not resolved.get("ok"):
            return {
                "handled": True,
                "ok": False,
                "status": str(resolved.get("status") or "unresolved"),
                "reply": str(resolved.get("reply") or "无法唯一确认这个证券，请提供完整 provider code。"),
                "subscription_id": subscription.subscription_id,
            }
        code = str(resolved["code"])
        display_name = str(resolved.get("display_name") or "")
        if action == "remove":
            removed = self.store.remove_watchlist_item(
                subscription_id=subscription.subscription_id,
                provider=self.provider_id,
                code=code,
            )
            return {
                "handled": True,
                "ok": removed,
                "status": "removed" if removed else "not_found",
                "reply": (
                    f"已从财经关注列表移除 {display_name or code}（{code}）。"
                    if removed
                    else f"财经关注列表里没有 {display_name or code}（{code}）。"
                ),
                "subscription_id": subscription.subscription_id,
                "code": code,
                "watchlist_count": len(self._list_provider_watchlist(subscription.subscription_id)),
            }

        aliases = (query,) if query and self._normalize_alias(query) not in {self._normalize_alias(code)} else ()
        item = self.store.upsert_watchlist_item(
            subscription_id=subscription.subscription_id,
            provider=self.provider_id,
            code=code,
            display_name=display_name,
            aliases=aliases,
            priority=0.7,
            created_by_actor_id=self._actor_id(context),
        )
        return {
            "handled": True,
            "ok": True,
            "status": "added",
            "reply": f"已关注 {item.display_name or item.code}（{item.code}）。后续重要事件才会进入即时分析，普通资讯不会刷屏。",
            "subscription_id": subscription.subscription_id,
            "code": item.code,
            "watchlist_count": len(self._list_provider_watchlist(subscription.subscription_id)),
        }

    def _resolve_watchlist_target(
        self,
        query: str,
        *,
        subscription_id: str,
        profile_user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        text = str(query or "").strip()
        if not text:
            return {"ok": False, "status": "empty_query", "reply": "请提供证券代码或精确名称。"}
        if _FULL_PROVIDER_CODE_RE.fullmatch(text):
            try:
                return {"ok": True, "code": normalize_market_code(text), "display_name": ""}
            except MarketDataValidationError:
                return {"ok": False, "status": "invalid_code", "reply": "证券代码格式不正确。"}

        watchlist_matches = [
            item
            for item in self._list_provider_watchlist(subscription_id)
            if self._normalize_alias(text)
            in {
                self._normalize_alias(item.code),
                self._normalize_alias(item.display_name),
                *{self._normalize_alias(alias) for alias in item.aliases},
            }
        ]
        if len(watchlist_matches) == 1:
            item = watchlist_matches[0]
            return {"ok": True, "code": item.code, "display_name": item.display_name}

        candidates = self.store.resolve_security(
            text,
            provider=self.provider_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=10,
        )
        exact_by_code = {
            str(item.get("code") or ""): item
            for item in candidates
            if str(item.get("match_type") or "") == "exact" and str(item.get("code") or "")
        }
        if len(exact_by_code) == 1:
            candidate = next(iter(exact_by_code.values()))
            return {
                "ok": True,
                "code": str(candidate.get("code") or ""),
                "display_name": str(candidate.get("display_name") or ""),
            }
        if exact_by_code:
            labels = [
                f"{str(item.get('display_name') or code)}（{code}）" for code, item in list(exact_by_code.items())[:6]
            ]
            return {
                "ok": False,
                "status": "ambiguous",
                "reply": "找到多个同名证券：" + "、".join(labels) + "。请发送完整 provider code。",
            }
        return {
            "ok": False,
            "status": "needs_confirmation" if candidates else "not_found",
            "reply": "无法唯一确认这个证券。请使用完整 provider code，例如 600000.SH；系统不会自行拼交易所后缀。",
        }

    def _enabled_push_subscription(self, context: Any):
        return next(
            (
                subscription
                for subscription in self._matching_subscriptions(context)
                if subscription.enabled and subscription.finance_mode == "push"
            ),
            None,
        )

    def _list_provider_watchlist(self, subscription_id: str):
        return tuple(item for item in self.store.list_watchlist(subscription_id) if item.provider == self.provider_id)

    def _matching_subscriptions(self, context: Any):
        target_id = str(int(getattr(context, "target_id", 0) or 0))
        session_id = str(getattr(context, "session_id", "") or "")
        profile_user_id = str(getattr(context, "profile_user_id", "") or "")
        return tuple(
            subscription
            for subscription in self.store.list_subscriptions(client="qq", target_id=target_id)
            if subscription.session_id == session_id and subscription.profile_user_id == profile_user_id
        )

    @staticmethod
    def _subscription_id_for_context(context: Any) -> str:
        scope = "group" if bool(getattr(context, "is_group", False)) else "private"
        material = "|".join(
            [
                scope,
                str(getattr(context, "target_id", 0) or 0),
                str(getattr(context, "session_id", "") or ""),
                str(getattr(context, "profile_user_id", "") or ""),
            ]
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return f"qq_finance:{scope}:{digest}"

    @staticmethod
    def _actor_id(context: Any) -> str:
        user_id = int(getattr(context, "user_id", 0) or 0)
        return f"qq:{user_id}" if user_id > 0 else ""

    @staticmethod
    def _normalize_command_text(value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        text = re.sub(r"^akane(?:[\s,，:：;；、-]+|$)", "", text, count=1, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _normalize_alias(value: str) -> str:
        return re.sub(r"[^\w\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())
