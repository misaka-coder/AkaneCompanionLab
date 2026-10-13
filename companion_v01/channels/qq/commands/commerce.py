"""QQ shopping, backpack, feeding and offering actions."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from companion_v01.care_runtime import format_care_feed_prompt
from companion_v01.channels.qq.commands.care_results import (
    QQCareCommandRequest,
    _commerce_listing,
    _find_shop_item,
    _format_effects_summary,
    _item_usable_as_offering,
    _item_usable_in_qq,
    _poke_seasonal_note,
    _render_poke_effects,
    _signed_number,
)


class QQCommerceCommands:
    """Shop, inventory and item mutations using request-local inputs."""

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    def shop_list(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        qq_items = [item for item in turn.items if _item_usable_in_qq(item)]
        snapshot = turn.snapshot()
        listing = _commerce_listing("shop", qq_items, snapshot, turn.parsed)
        if not listing["ok"]:
            return listing
        if not qq_items:
            return {**listing, "reply": "商店暂时没有商品。", "status": "empty"}
        seasonal_items = [i for i in qq_items if i.get("seasonal")]
        regular_items = [i for i in qq_items if not i.get("seasonal")]
        food_items = [
            i
            for i in regular_items
            if i.get("category") not in ("offering", "charm", "gift", "trick", "potion")
        ]
        trick_items = [i for i in regular_items if i.get("category") in ("charm", "trick", "potion")]
        offer_items = [i for i in regular_items if i.get("category") in ("offering", "gift")]
        lines = ["\U0001f6d2 商店", "─" * 18]

        def _item_line(item: dict) -> str:
            eff = _format_effects_summary(item.get("effects") or {})
            line = f"  {item['name']}  {item.get('price', 0)} 额度"
            if eff:
                line += f"  ({eff})"
            return line

        if seasonal_items:
            # Group by label for display
            by_label: dict[str, list] = {}
            for i in seasonal_items:
                lbl = f"{i.get('seasonal_emoji', '🌸')} {i.get('seasonal_label', '限定')}"
                by_label.setdefault(lbl, []).append(i)
            for lbl, grp in by_label.items():
                lines.append(f"{lbl}（限时）")
                for item in grp:
                    lines.append(_item_line(item))
            lines.append("")
        if food_items:
            lines.append("\U0001f35a 食物 / 饮品")
            for item in food_items:
                lines.append(_item_line(item))
        if trick_items:
            if food_items:
                lines.append("")
            lines.append("🔮 歪门邪道")
            for item in trick_items:
                lines.append(_item_line(item))
        if offer_items:
            if food_items or trick_items:
                lines.append("")
            lines.append("⛩ 供奉 / 礼物")
            for item in offer_items:
                lines.append(_item_line(item))
        lines.append("─" * 18)
        lines.append("/购买 商品名 · /供奉 商品名 · /供奉 · /抽签（5额度）")
        lines.append("/赛钱 N 可把金币换成额度，额度在商店用喵")
        return {**listing, "reply": "\n".join(lines), "status": "ok"}

    def buy(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        item_name = str(turn.parsed.get("item_name") or "").strip()
        qty = max(1, int(turn.parsed.get("quantity") or 1))
        matched = _find_shop_item(turn.items, item_name)
        if matched is None:
            return {
                "ok": False,
                "reply": f"没有找到「{item_name}」，发送 /商店查看可用商品。",
                "status": "item_not_found",
            }
        if not _item_usable_in_qq(matched):
            return {
                "ok": False,
                "reply": f"「{matched['name']}」只能在桌宠端使用，QQ 不支持。",
                "status": "not_usable_in_qq",
            }
        price_each = int(matched.get("price") or 0)
        result = turn.runtime.buy_to_inventory(
            profile_user_id=turn.profile_user_id,
            conversation_id=turn.conversation_id,
            event_id=turn.event_id("buy_to_inventory"),
            character_pack_id=turn.character_pack_id,
            relation_user_id=turn.relation_user_id,
            item_id=str(matched.get("id") or ""),
            item_name=str(matched["name"]),
            price=price_each,
            count=qty,
            item_effects=dict(matched.get("effects") or {}),
            item_category=str(matched.get("category") or ""),
            item_metadata=matched,
            now_ms=turn.now_ms,
        )
        if result["status"] == "duplicate":
            return {
                "ok": True,
                "reply": "这次购买已经处理过了，没有重复扣除额度或增加物品。",
                "status": "duplicate",
            }
        if result["status"] == "insufficient_quota":
            needed = result["quota_needed"]
            have = result["quota_before"]
            return {
                "ok": False,
                "reply": f"额度不够，需要 {needed} 额度，当前只有 {have} 额度。先「/赛钱 N」把金币换成额度喵。",
                "status": "insufficient_quota",
            }
        quota_after = result["quota_after"]
        total_count = result["item_count"]
        qty_str = f" x{qty}" if qty > 1 else ""
        reply = (
            f"✓ 购买成功！「{matched['name']}」{qty_str} 已放入背包"
            f"（-{price_each * qty} 额度，剩余 {quota_after} 额度，背包共 x{total_count}）"
            f"\n发送「/投喂 {matched['name']}」来使用。"
        )
        return {"ok": True, "reply": reply, "status": "ok", "commerce_card": {
            "kind": "purchase", "item": dict(matched), "quantity": qty,
            "cost": result["quota_before"] - quota_after, "quota_after": quota_after, "inventory_count": total_count,
            "use_id": sum(1 for item in turn.items if item.get("name") == matched["name"]) > 1,
        }}

    def backpack(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        snapshot = turn.snapshot()
        inventory = snapshot.get("inventory") or {}
        catalogue = {str(item.get("id")): item for item in turn.items}
        inventory_items = [
            {**item_data, **catalogue.get(str(item_id), {}), "id": str(item_id), "count": item_data["count"]}
            for item_id, item_data in inventory.items() if int(item_data.get("count") or 0) > 0
        ]
        listing = _commerce_listing("backpack", inventory_items, snapshot, turn.parsed)
        if not listing["ok"]:
            return listing
        if not inventory:
            return {
                **listing,
                "reply": "\U0001f392 背包是空的，发送 /商店查看可购买的商品。",
                "status": "empty",
            }
        lines = ["\U0001f392 背包", "─" * 16]
        for item_data in inventory.values():
            name = str(item_data.get("name") or "")
            count = int(item_data.get("count") or 0)
            eff_str = _format_effects_summary(item_data.get("effects") or {})
            line = f"  {name}  x{count}"
            if eff_str:
                line += f"  [{eff_str}]"
            lines.append(line)
        lines.append("─" * 16)
        lines.append("/投喂 商品名 · /供奉 商品名")
        return {**listing, "reply": "\n".join(lines), "status": "ok"}

    def feed(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        item_name = str(turn.parsed.get("item_name") or "").strip()
        qty = max(1, int(turn.parsed.get("quantity") or 1))
        if not item_name:
            return {
                "ok": False,
                "reply": "请指定要投喂的物品，发送「/背包」查看持有。",
                "status": "no_item_specified",
            }
        matched = _find_shop_item(turn.items, item_name)
        # Resolve item_id and effects: prefer live shop, fall back to inventory snapshot
        if matched is not None:
            item_id = str(matched.get("id") or "")
            item_name_display = str(matched["name"])
            effects_for_feed: dict[str, Any] = dict(matched.get("effects") or {})
        else:
            # Item may be seasonal / expired — look it up in the user's inventory
            inv_snap = turn.snapshot()
            inv = inv_snap.get("inventory") or {}
            stored_item = _find_shop_item(
                [{**entry, "id": inv_id} for inv_id, entry in inv.items()], item_name,
            )
            if stored_item is None:
                return {
                    "ok": False,
                    "reply": f"没有找到「{item_name}」，发送「/背包」查看持有物品。",
                    "status": "item_not_found",
                }
            item_id = str(stored_item["id"])
            effects_for_feed = dict(stored_item.get("effects") or {})
            item_name_display = str(stored_item.get("name") or item_name)
            matched = stored_item
        result = turn.runtime.use_from_inventory(
            profile_user_id=turn.profile_user_id,
            conversation_id=turn.conversation_id,
            event_id=turn.event_id("use_from_inventory"),
            character_pack_id=turn.character_pack_id,
            relation_user_id=turn.relation_user_id,
            item_id=item_id,
            item_effects=effects_for_feed,
            count=qty,
            now_ms=turn.now_ms,
        )
        if result["status"] == "duplicate":
            return {
                "ok": True,
                "reply": "这次投喂已经处理过了，没有重复消耗物品。",
                "status": "duplicate",
            }
        if result["status"] == "not_in_inventory":
            return {
                "ok": False,
                "reply": (f"背包里没有「{item_name_display}」，先发送「/购买 {item_name_display}」入手。"),
                "status": "not_in_inventory",
            }
        if result["status"] == "insufficient_count":
            have = result.get("available", 0)
            return {
                "ok": False,
                "reply": (
                    f"背包里「{item_name_display}」只剩 x{have}，"
                    f"发送「/投喂 {item_name_display} x{have}」或先补货。"
                ),
                "status": "insufficient_count",
            }
        snap = result["snapshot"]
        _feed_actor = turn.context.sender_label or ("我" if not turn.context.is_group else "用户")
        applied = result.get("effects_applied") or {}
        extra_facts = [f"「{item_name_display}」已真实消耗。"]
        if isinstance(matched, dict):
            seasonal_note = _poke_seasonal_note(matched)
            if seasonal_note:
                extra_facts.append(seasonal_note)
        extra_facts.append(f"实际效果：{_render_poke_effects(result)}。")
        feed_pack = format_care_feed_prompt(
            item_name=item_name_display,
            item_description=str(matched.get("description") or ""),
            hunger_delta=int(applied.get("hunger", 0)),
            energy_delta=int(applied.get("energy", 0)),
            affection_delta=int(applied.get("affection", 0)),
            current_hunger=int(snap.get("hunger") or 0),
            current_energy=int(snap.get("energy") or 0),
            current_affection=int(snap.get("affection") or 0),
            actor_label=_feed_actor,
            quantity=qty,
            extra_facts=extra_facts,
        )
        note = (
            "【本轮投喂结果】\n"
            + feed_pack["feed_lead"].removeprefix("刚才发生的互动：") + "\n"
            + (feed_pack["description_line"] + "\n" if feed_pack["description_line"] else "")
            + "\n".join(extra_facts) + "\n"
            + f"投喂后状态：饥饿 {int(snap.get('hunger') or 0)}/100，精力 {int(snap.get('energy') or 0)}/100。"
        )
        return {
            "_llm_passthrough": True,
            "qq_action_note": note,
            "turn_message": feed_pack["memory_message"],
            "turn_kind": feed_pack["turn_kind"],
            "ok": True,
            "status": "ok",
        }

    def offering(self, turn: QQCareCommandRequest) -> dict[str, Any]:
        item_name = str(turn.parsed.get("item_name") or "").strip()
        date_key = datetime.now().strftime("%Y-%m-%d")

        if item_name:
            matched = _find_shop_item(turn.items, item_name)
            if matched is None:
                return {
                    "ok": False,
                    "reply": f"没有找到「{item_name}」，发送 /商店查看可用商品。",
                    "status": "item_not_found",
                }
            if not _item_usable_in_qq(matched):
                return {
                    "ok": False,
                    "reply": f"「{matched['name']}」只能在桌宠端使用，QQ 不支持。",
                    "status": "not_usable_in_qq",
                }
            if not _item_usable_as_offering(matched):
                return {
                    "ok": False,
                    "reply": (f"「{matched['name']}」不是供奉/礼物类商品；普通食物请先购买再投喂。"),
                    "status": "not_offering_item",
                }
            item_id = str(matched.get("id") or "")
            effects = matched.get("effects") or {}
            body_effects = {k: v for k, v in effects.items() if k in ("hunger", "energy")}
            affection_effect = int(effects.get("affection") or 0)
            result = turn.runtime.claim_daily_offering(
                profile_user_id=turn.profile_user_id, conversation_id=turn.conversation_id,
                character_pack_id=turn.character_pack_id, relation_user_id=turn.relation_user_id,
                date_key=date_key, affection_bonus=0, inventory_item_id=item_id,
                event_id=turn.event_id("offering"), item_effects={**body_effects, "affection": affection_effect},
                now_ms=turn.now_ms,
            )
            if result["status"] == "duplicate":
                return {
                    "ok": True,
                    "reply": "这次供奉已经处理过了，没有重复消耗物品。",
                    "status": "duplicate",
                }
            if result["status"] == "not_in_inventory":
                return {"ok": False, "reply": f"背包里没有「{matched['name']}」，先购买再供奉。", "status": "not_in_inventory"}
            use_result = result
            daily_affection = int(result.get("affection_granted") or 0)
            body_effect = _render_poke_effects(use_result)
            offering_memory = f"刚才发生的互动：用户供奉了「{matched['name']}」。"
            offering_facts = [
                f"用户供奉了「{matched['name']}」，该物品已真实消耗。",
                f"物品效果：{body_effect}。",
                f"本次供奉实际增加 QQ 好感 {_signed_number(daily_affection)}。",
                f"这是当天第{'一次' if result.get('daily_bonus') else '二次及以后'}供奉。",
            ]
            seasonal_note = _poke_seasonal_note(matched)
            if seasonal_note:
                offering_facts.insert(1, seasonal_note)
            note = "【本轮供奉结果】\n" + "\n".join(offering_facts)
            return {
                "_llm_passthrough": True,
                "qq_action_note": note,
                "turn_message": offering_memory,
                "ok": True,
                "status": result["status"],
            }

        # Free offering (no item)
        result = turn.runtime.claim_daily_offering(
            profile_user_id=turn.profile_user_id,
            conversation_id=turn.conversation_id,
            character_pack_id=turn.character_pack_id,
            relation_user_id=turn.relation_user_id,
            date_key=date_key,
            affection_bonus=3,
            item_price=0,
            item_effects={},
            now_ms=turn.now_ms,
        )
        aff_now = result["snapshot"]["affection"]
        note = (
            "【本轮供奉结果】\n"
            f"用户进行了{'当天第一次' if result.get('daily_bonus') else '当天再次'}供奉。\n"
            f"实际效果：QQ 好感 {_signed_number(int(result.get('affection_granted') or 0))}，当前 QQ 好感 {aff_now}/100。"
        )
        return {
            "_llm_passthrough": True,
            "qq_action_note": note,
            "turn_message": "刚才发生的互动：用户向神社进行了供奉。",
            "ok": True,
            "status": result["status"],
        }
