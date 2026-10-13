"""QQ care command dispatch, care actions and replay-safe poke reactions."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from types import MappingProxyType
from typing import Any

from companion_v01.care_runtime import (
    DEFAULT_CARE_SHOP_ITEMS,
    DEFAULT_CHECKIN_COINS,
    CareModulePort,
    get_seasonal_shop_items,
)
from companion_v01.channels.qq.commands.care_results import (
    QQCareCommandRequest,
    _build_poke_outcome,
    _merge_shop_items,
    _parse_item_and_quantity,
    _poke_actor_label,
    _poke_scope_keys,
    _render_poke_mutation_outcome,
    _signed_number,
)
from companion_v01.channels.qq.commands.commerce import QQCommerceCommands
from companion_v01.channels.qq.commands.poke import PokeEventReactor, PokeOutcome
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.validation import _safe_character_pack_id


class QQCareCommands:
    """Dispatch care actions and poke events without retaining the current turn."""

    def __init__(self, *, gateway: Any, poke_reactor: PokeEventReactor) -> None:
        self.gateway = gateway
        self.poke_reactor = poke_reactor
        self.commerce = QQCommerceCommands(gateway=gateway)
        self._actions = MappingProxyType({
            "checkin": self.checkin,
            "status": self.status,
            "saiken": self.saiken,
            "shrine": self.shrine,
            "quota": self.quota,
            "shop_list": self.commerce.shop_list,
            "buy": self.commerce.buy,
            "backpack": self.commerce.backpack,
            "feed": self.commerce.feed,
            "lottery": self.lottery,
            "offering_status": self.offering_status,
            "offering": self.commerce.offering,
        })

    def handle_economy_command(
        self,
        context: "QQMessageContext",
        *,
        command: dict[str, Any] | None = None,
        care_module: CareModulePort | None = None,
        shop_items: list[dict[str, Any]] | None = None,
        checkin_coins: int = DEFAULT_CHECKIN_COINS,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if command is not None:
            parsed = dict(command)
            if "item_text" in parsed:
                item_name, quantity = _parse_item_and_quantity(parsed.pop("item_text"))
                parsed.update(item_name=item_name, quantity=quantity)
        else:
            parsed = self.gateway.parse_economy_command(context.clean_message)
        if parsed is None:
            return None
        care_module = care_module if care_module is not None else CareModulePort.disabled("not_configured")
        if care_module.runtime is None:
            return care_module.disabled_result()
        items = _merge_shop_items(DEFAULT_CARE_SHOP_ITEMS, shop_items or [])
        items = _merge_shop_items(items, get_seasonal_shop_items())
        turn = QQCareCommandRequest(
            context=context, runtime=care_module.runtime, items=items, parsed=dict(parsed),
            now_ms=now_ms if now_ms is not None else int(time.time() * 1000),
            checkin_coins=checkin_coins,
        )
        handler = self._actions.get(str(parsed.get("action") or ""))
        if handler is None:
            return None
        try:
            return handler(turn)
        except Exception as exc:
            return {
                "ok": False, "reply": "养成系统暂时出错，请稍后再试。",
                "status": "error", "reason": "care_command_failed", "error": type(exc).__name__,
            }


    def handle_poke_event(
        self,
        context: "QQMessageContext",
        event: dict[str, Any],
        *,
        care_module: CareModulePort | None = None,
        shop_items: list[dict[str, Any]] | None = None,
        now_ms: int | None = None,
    ) -> PokeOutcome | None:
        """Resolve and apply a QQ poke side effect, then describe only its facts."""
        if str(getattr(context, "reason", "") or "") != "qq_poke":
            return None

        actor_label = _poke_actor_label(context)
        base_text = f"{actor_label}在 QQ 里戳了戳你的头像。"
        event_id = self.gateway.poke_event_id(event, context=context)
        plain = lambda *, status="ok", reason="", variant="": _build_poke_outcome(
            actor_label=actor_label,
            outcome_kind="variant" if variant else "plain",
            event_id=event_id,
            memory_text=(f"刚才发生的互动：{base_text}" if not variant else f"刚才发生的互动：{actor_label}戳了戳你，{variant}。"),
            prompt_text=(f"【本轮戳一戳结果】{actor_label}戳了戳你，{variant}。" if variant else ""),
            status=status,
            reason=reason,
        )

        if care_module is None:
            care_module = CareModulePort.disabled("not_configured")
        care_runtime = care_module.runtime
        if care_runtime is None:
            return plain(status="disabled", reason=care_module.reason or "feature_disabled")

        items = _merge_shop_items(DEFAULT_CARE_SHOP_ITEMS, shop_items or [])
        items = _merge_shop_items(items, get_seasonal_shop_items())
        profile_user_id = str(context.profile_user_id or "")
        character_pack_id = str(context.character_pack_id or "")
        relation_user_id = f"qq:{context.user_id}" if context.user_id else f"qq:{profile_user_id}"
        timestamp_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        scope_key, group_scope_key = _poke_scope_keys(context)
        try:
            snapshot = care_runtime.snapshot_for_client(
                profile_user_id=profile_user_id,
                conversation_id=context.session_id,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=relation_user_id,
                now_ms=timestamp_ms,
            )
            plan = self.poke_reactor.plan(snapshot=snapshot, shop_items=items)
            outcome_kind = str(plan.get("outcome_kind") or "plain")
            if outcome_kind == "plain":
                return plain(reason=str(plan.get("fallback_reason") or ""))
            apply_result = care_runtime.apply_poke_plan(
                profile_user_id=profile_user_id,
                conversation_id=context.session_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                plan=plan,
                event_id=event_id,
                scope_key=scope_key,
                group_scope_key=group_scope_key,
                cooldown_ms=30_000,
                group_cooldown_ms=2_000,
                now_ms=timestamp_ms,
            )
            apply_status = str(apply_result.get("status") or "error")
            if apply_status == "duplicate":
                return _build_poke_outcome(
                    actor_label=actor_label,
                    outcome_kind=outcome_kind,
                    event_id=event_id,
                    memory_text="",
                    prompt_text="",
                    status="duplicate",
                    reason="event_already_applied",
                )
            if apply_status == "cooldown":
                return plain(status="cooldown", reason="stateful_event_cooldown")
            if apply_status != "ok":
                return plain(status="failed", reason=apply_status)
            if outcome_kind == "variant":
                return plain(variant=str(plan.get("variant") or "发生了奇怪的反应"))
            return _render_poke_mutation_outcome(
                actor_label=actor_label,
                plan=plan,
                result=apply_result,
                event_id=event_id,
            )
        except Exception as exc:
            return plain(status="failed", reason=f"poke_processing_failed:{type(exc).__name__}")


    @staticmethod
    def poke_event_id(event: dict[str, Any], *, context: "QQMessageContext") -> str:
        """Build a stable replay key from the character, scope, actor, and event."""
        raw_fingerprint = str(event.get("message_id") or event.get("event_id") or "").strip()
        if not raw_fingerprint:
            raw_fingerprint = hashlib.sha256(
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        scope = f"group:{int(context.group_id)}" if context.is_group else f"private:{int(context.user_id)}"
        actor = str(int(context.user_id or 0))
        character = _safe_character_pack_id(context.character_pack_id) or "default_character"
        return f"{character}|{scope}|actor:{actor}|event:{raw_fingerprint}"


    def checkin(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        date_key = datetime.now().strftime("%Y-%m-%d")
        result = turn.runtime.claim_daily_checkin(
            profile_user_id=turn.profile_user_id,
            conversation_id=turn.conversation_id,
            character_pack_id=turn.character_pack_id,
            relation_user_id=turn.relation_user_id,
            date_key=date_key,
            coins=turn.checkin_coins,
            now_ms=turn.now_ms,
        )
        snap = result["snapshot"]
        coins_now = snap["coins"]
        streak = int(result.get("streak") or 1)

        if result["status"] == "already":
            note = (
                f"【签到通知】用户今天已经签到过了，又来说了一句签到"
                f"（当前金币 {coins_now}，连续签到 {streak} 天）。自然回应即可，不必强调规则。"
            )
            return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "already"}

        coins_granted = result["coins_granted"]
        streak_broken = bool(result.get("streak_broken"))
        days_absent = int(result.get("days_absent") or 0)
        is_milestone = bool(result.get("streak_milestone"))

        if streak_broken and days_absent >= 1:
            # Comeback after breaking a streak — LLM reacts to absence
            note = (
                f"【签到通知】用户消失了 {days_absent} 天后回来签到了（之前有连续签到纪录）。"
                f"本次签到 +{coins_granted} 金币，当前 {coins_now} 金币，连续签到重置为第 1 天。"
                f"可以用你自己的方式表达一下——不一定要抱怨，但可以让他感受到这几天有什么不同。"
            )
            return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

        if is_milestone:
            # Streak milestone — LLM celebrates (or reacts in character)
            note = (
                f"【签到里程碑】用户已连续签到 {streak} 天！"
                f"本次签到 +{coins_granted} 金币（连击奖励），当前 {coins_now} 金币。"
                f"用你自己的方式回应这个里程碑——不用过分热情，但要让他感觉到这件事有意义。"
            )
            return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

        # Normal checkin — local reply
        streak_str = f"（连续 {streak} 天）" if streak >= 2 else ""
        reply = f"✓ 签到成功！+{coins_granted} 金币{streak_str}（当前：{coins_now} 金币）"
        return {"ok": True, "reply": reply, "status": "ok"}

    def status(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        snapshot = turn.snapshot()
        h = snapshot["hunger"]
        e = snapshot["energy"]
        a = snapshot["affection"]
        c = snapshot["coins"]
        reply = f"养成状态\n饥饿 {h}/100（越低越饿）  精力 {e}/100（越高越精神）\nQQ好感 {a}/100  金币 {c}  额度 {snapshot.get('redeem_quota', 0)}"
        card_fields = ("hunger", "energy", "affection", "coins", "redeem_quota", "checkin_streak", "shared_vitals")
        return {"ok": True, "reply": reply, "status": "ok",
                "care_card": {key: snapshot[key] for key in card_fields if key in snapshot}}

    def saiken(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        amount = int(turn.parsed.get("amount") or 0)
        event_id = ""
        if str(turn.context.source_message_id or "").strip():
            event_id = (
                self.gateway.poke_event_id(
                    {"message_id": turn.context.source_message_id},
                    context=turn.context,
                )
                + "|economy:saiken"
            )
        result = turn.runtime.donate_to_shrine(
            profile_user_id=turn.profile_user_id,
            conversation_id=turn.conversation_id,
            character_pack_id=turn.character_pack_id,
            relation_user_id=turn.relation_user_id,
            amount=amount,
            event_id=event_id,
            now_ms=turn.now_ms,
        )
        if result["status"] == "invalid_amount":
            return {
                "ok": False,
                "reply": "赛钱金额必须是 1–999999，例如发送「/赛钱 5」。",
                "status": "invalid_amount",
            }
        if result["status"] == "insufficient_coins":
            return {
                "ok": False,
                "reply": (
                    f"金币不够，需要 {result['coins_needed']} 金币，"
                    f"当前只有 {result['coins_before']} 金币。先签到拿点金币，再来赛钱喵。"
                ),
                "status": "insufficient_coins",
            }
        if result["status"] == "duplicate":
            return {
                "ok": True,
                "reply": "这条赛钱已经处理过了，没有重复扣除金币。",
                "status": "duplicate",
            }
        if result["status"] == "balance_limit":
            return {
                "ok": False,
                "reply": "当前额度或赛钱累计已到存储上限，本次没有扣除金币。",
                "status": "balance_limit",
            }
        actor_label = turn.context.sender_label or ("我" if not turn.context.is_group else "用户")
        memory_text = f"刚才发生的互动：{actor_label}往赛钱箱投了 {amount} 枚金币。"
        note = (
            "【本轮赛钱结果】\n"
            f"{actor_label}往赛钱箱投了 {amount} 枚金币。\n"
            f"箱子香火总额：{result['shrine_total']} 枚。\n"
            f"{actor_label}累计投入：{result['saiken_balance']} 枚，第 {result['saiken_count']} 次。\n"
            f"{actor_label}当前兑换额度：{result['quota_after']} 枚。"
        )
        return {
            "_llm_passthrough": True,
            "qq_action_note": note,
            "turn_message": memory_text,
            "ok": True,
            "status": "ok",
        }

    def shrine(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        snapshot = turn.snapshot()
        note = (
            "【赛钱箱】\n"
            f"香火总额：{snapshot.get('shrine_total', 0)} 枚。\n"
            f"你的累计投入：{snapshot.get('saiken_balance', 0)} 枚（第 {snapshot.get('saiken_count', 0)} 次）。\n"
            f"你的可用额度：{snapshot.get('redeem_quota', 0)} 枚。"
        )
        return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

    def quota(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        snapshot = turn.snapshot()
        quota_now = int(snapshot.get("redeem_quota") or 0)
        reply = f"你当前有 {quota_now} 兑换额度。赛钱 N 枚金币可换 N 额度，额度在商店买东西喵。"
        return {"ok": True, "reply": reply, "status": "ok"}

    def lottery(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        SLIP_COST = 5
        result = turn.runtime.draw_fortune_slip(
            profile_user_id=turn.profile_user_id,
            conversation_id=turn.conversation_id,
            character_pack_id=turn.character_pack_id,
            relation_user_id=turn.relation_user_id,
            slip_cost=SLIP_COST,
            now_ms=turn.now_ms,
        )
        if result["status"] == "insufficient_quota":
            have = result["quota_before"]
            return {
                "ok": False,
                "reply": f"额度不足，抽一签需要 {SLIP_COST} 额度，当前只有 {have} 额度。先「/赛钱 N」换点额度喵。",
                "status": "insufficient_quota",
            }
        fortune = result["fortune"]
        quota_after = result["quota_after"]
        aff_delta = result["affection_delta"]

        actual_quota_delta = int(quota_after) - int(result.get("quota_before") or 0)
        effect_parts = [f"额度 {_signed_number(actual_quota_delta)}"]
        if aff_delta:
            effect_parts.append(f"QQ 好感 {_signed_number(aff_delta)}")
        note = (
            f"【本轮抽签结果】用户花费 {SLIP_COST} 额度抽了一签，结果是【{fortune}】。"
            f"实际效果：{'，'.join(effect_parts)}；当前额度 {quota_after}。"
        )
        return {"_llm_passthrough": True, "qq_action_note": note, "ok": True, "status": "ok"}

    def offering_status(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        snapshot = turn.snapshot()
        today = datetime.now().strftime("%Y-%m-%d")
        offered_today = str(snapshot.get("last_offering_date") or "") == today
        mark = "✓ 今日已供奉" if offered_today else "· 今日未供奉"
        a = snapshot["affection"]
        c = snapshot["coins"]
        reply = f"⛩ 供奉状态\n{mark}\nQQ好感 {a}/100  金币 {c}  额度 {snapshot.get('redeem_quota', 0)}"
        return {"ok": True, "reply": reply, "status": "ok"}
