"""Request inputs and safe QQ care, poke and commerce result formatting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from companion_v01.channels.qq.cards.commerce import QQ_COMMERCE_PAGE_SIZE
from companion_v01.channels.qq.commands.poke import PokeOutcome
from companion_v01.channels.qq.constants import _QTY_RE
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.validation import _safe_character_pack_id


@dataclass(frozen=True)
class QQCareCommandRequest:
    """Request-local inputs shared by action handlers; never retained on owners."""

    context: QQMessageContext
    runtime: Any
    items: list[dict[str, Any]]
    parsed: dict[str, Any]
    now_ms: int
    checkin_coins: int

    @property
    def profile_user_id(self) -> str:
        return self.context.profile_user_id

    @property
    def character_pack_id(self) -> str:
        return self.context.character_pack_id or ""

    @property
    def conversation_id(self) -> str:
        return self.context.session_id

    def event_id(self, action: str) -> str:
        message_id = str(self.context.source_message_id or "").strip()
        if not message_id:
            return ""
        return f"qqcare:{self.conversation_id}:{self.context.user_id}:{message_id}:{action}"

    @property
    def relation_user_id(self) -> str:
        return f"qq:{self.context.user_id}" if self.context.user_id else f"qq:{self.context.profile_user_id}"

    def snapshot(self) -> dict[str, Any]:
        return self.runtime.snapshot_for_client(
            profile_user_id=self.profile_user_id,
            conversation_id=self.conversation_id,
            character_pack_id=self.character_pack_id,
            client_mode="qq_text",
            relation_user_id=self.relation_user_id,
            now_ms=self.now_ms,
        )


def _item_usable_in_qq(item: dict[str, Any]) -> bool:
    """Return True if item has no usable_in restriction or explicitly includes 'qq'."""
    usable_in = item.get("usable_in", item.get("usableIn"))
    if not usable_in:
        return True
    return isinstance(usable_in, list) and "qq" in [str(u).strip().lower() for u in usable_in]

def _poke_actor_label(context: QQMessageContext) -> str:
    if not context.is_group:
        return "我"
    return context.sender_label or (f"QQ {context.user_id}" if context.user_id else "这位 QQ 用户")

def _poke_scope_keys(context: QQMessageContext) -> tuple[str, str]:
    character = _safe_character_pack_id(context.character_pack_id) or "default_character"
    if context.is_group:
        return (
            f"{character}|group:{int(context.group_id)}|actor:{int(context.user_id)}",
            f"{character}|group:{int(context.group_id)}",
        )
    return f"{character}|private:{int(context.user_id)}|actor:{int(context.user_id)}", ""

def _build_poke_outcome(
    *,
    actor_label: str,
    outcome_kind: str,
    event_id: str,
    memory_text: str,
    prompt_text: str,
    mutations: list[dict[str, Any]] | None = None,
    status: str = "ok",
    reason: str = "",
) -> PokeOutcome:
    return PokeOutcome(
        event_kind="qq_poke",
        source="poke",
        actor_label=actor_label,
        outcome_kind=outcome_kind,
        memory_text=memory_text,
        prompt_text=prompt_text,
        mutations=tuple(dict(item) for item in (mutations or []) if isinstance(item, dict)),
        status=status,
        reason=reason,
        event_id=event_id,
    )

def _render_poke_mutation_outcome(
    *,
    actor_label: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    event_id: str,
) -> PokeOutcome:
    outcome_kind = str(plan.get("outcome_kind") or "plain")
    item = plan.get("item") if isinstance(plan.get("item"), dict) else {}
    count = max(1, int(plan.get("count") or 1))
    item_name = str(item.get("name") or plan.get("item_id") or "物品")
    owner = "我的" if actor_label == "我" else f"{actor_label}的"
    seasonal_note = _poke_seasonal_note(item)

    if outcome_kind == "consume_inventory_item":
        item_desc = f"{item_name} x{count}" if count > 1 else item_name
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，你从{owner}背包里偷吃了{item_desc}。"
        fact_lines = [memory_text.removeprefix("刚才发生的互动：")]
        if seasonal_note:
            fact_lines.append(seasonal_note)
        fact_lines.append(f"实际效果：{_render_poke_effects(result)}。")
        mutations = [
            {
                "kind": "inventory_consume",
                "item_id": str(result.get("item_id") or plan.get("item_id") or ""),
                "item_name": item_name,
                "count": int(result.get("count_used") or count),
                "effects_applied": dict(result.get("effects_applied") or {}),
            }
        ]
    elif outcome_kind == "grant_inventory_item":
        item_desc = f"{item_name} x{count}" if count > 1 else item_name
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，{item_desc}落进了{owner}背包。"
        fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"{item_desc}已加入{owner}背包。"]
        if seasonal_note:
            fact_lines.append(seasonal_note)
        mutations = [dict(result.get("mutation") or {})]
    elif outcome_kind == "coin_change":
        mutation = result.get("mutation") if isinstance(result.get("mutation"), dict) else {}
        actual_delta = int(mutation.get("actual_delta") or 0)
        donated = int(mutation.get("donated_to_shrine") or 0)
        delta_text = _signed_number(actual_delta)
        amount = abs(actual_delta)
        if donated > 0:
            quota_after = int(mutation.get("quota_after") or 0)
            scene = (
                f"{actor_label}戳了戳你，你从{owner}口袋里顺走了{amount}枚金币，"
                f"顺手投进了赛钱箱，{actor_label}获得{amount}点兑换额度。"
            )
            memory_text = f"刚才发生的互动：{scene}"
            fact_lines = [
                memory_text.removeprefix("刚才发生的互动："),
                f"实际效果：金币 {delta_text}，赛钱箱 +{amount}，兑换额度 +{amount}（当前 {quota_after}）。",
            ]
        elif actual_delta > 0:
            scene = f"{actor_label}戳了戳你，你顺手往{owner}口袋里塞了{amount}枚金币。"
            memory_text = f"刚才发生的互动：{scene}"
            fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"实际效果：金币 {delta_text}。"]
        elif actual_delta < 0:
            scene = f"{actor_label}戳了戳你，{owner}口袋里的{amount}枚金币被你顺走了。"
            memory_text = f"刚才发生的互动：{scene}"
            fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"实际效果：金币 {delta_text}。"]
        else:
            scene = f"{actor_label}戳了戳你，{owner}口袋里的金币没有变化。"
            memory_text = f"刚才发生的互动：{scene}"
            fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"实际效果：金币 {delta_text}。"]
        mutations = [dict(mutation)]
    elif outcome_kind == "lottery":
        fortune = str(result.get("fortune") or "未知")
        net_quota = int(result.get("net_quota") or 0)
        affection_delta = int(result.get("affection_delta") or 0)
        memory_text = f"刚才发生的互动：{actor_label}戳了戳你，触发了一次抽签，结果是{fortune}。"
        effects = [f"兑换额度 {_signed_number(net_quota)}"]
        if affection_delta:
            effects.append(f"QQ 好感 {_signed_number(affection_delta)}")
        fact_lines = [memory_text.removeprefix("刚才发生的互动："), f"实际效果：{'，'.join(effects)}。"]
        mutations = [
            {
                "kind": "lottery",
                "fortune": fortune,
                "net_quota": net_quota,
                "affection_delta": affection_delta,
            }
        ]
    else:
        return _build_poke_outcome(
            actor_label=actor_label,
            outcome_kind="plain",
            event_id=event_id,
            memory_text=f"刚才发生的互动：{actor_label}在 QQ 里戳了戳你的头像。",
            prompt_text="",
        )

    return _build_poke_outcome(
        actor_label=actor_label,
        outcome_kind=outcome_kind,
        event_id=event_id,
        memory_text=memory_text,
        prompt_text="【本轮戳一戳结果】\n" + "\n".join(fact_lines),
        mutations=mutations,
    )

def _render_poke_effects(result: dict[str, Any]) -> str:
    effects = dict(result.get("effects_applied") or {})
    before = result.get("state_before") if isinstance(result.get("state_before"), dict) else {}
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
    parts: list[str] = []
    labels = (("hunger", "饥饿值"), ("energy", "精力值"), ("affection", "QQ 好感"))
    for key, label in labels:
        if f"{key}_set" in effects:
            parts.append(f"{label}设为 {int(snapshot.get(key) or effects[f'{key}_set'])}/100")
            continue
        if key not in effects and not (
            (key == "affection" and effects.get("random_affection"))
            or (key in {"hunger", "energy"} and effects.get("random_vitals"))
        ):
            continue
        old_value = int(before.get(key) or 0)
        new_value = int(snapshot.get(key) or 0)
        delta = new_value - old_value
        if delta:
            parts.append(f"{label} {_signed_number(delta)}")
    if effects.get("hunger_energy_swap"):
        parts.append("饥饿值与精力值互换")
    return "，".join(parts) or "数值未变化"

def _poke_seasonal_note(item: dict[str, Any]) -> str:
    if not bool(item.get("seasonal")):
        return ""
    label = str(item.get("seasonal_label") or "限定")
    return f"这是{label}限定商品。"

def _signed_number(value: int) -> str:
    value = int(value or 0)
    return f"+{value}" if value > 0 else str(value)

def _item_usable_as_offering(item: dict[str, Any]) -> bool:
    """Return True for QQ offering item categories."""
    category = str(item.get("category") or "").strip().lower()
    return category in {"offering", "charm", "gift"}

def _commerce_listing(kind: str, items: list[dict[str, Any]], snapshot: dict[str, Any],
                      parsed: dict[str, Any]) -> dict[str, Any]:
    page = int(parsed.get("page") or 1)
    pages = max(1, (len(items) + QQ_COMMERCE_PAGE_SIZE - 1) // QQ_COMMERCE_PAGE_SIZE)
    title = "商店" if kind == "shop" else "背包"
    if page > pages:
        return {"ok": False, "status": "invalid_page", "reply": f"{title}共 {pages} 页，请使用 /{title} 1～{pages}。"}
    # Only display fields cross this boundary, never the whole Care authority or pack metadata.
    rows = []
    for item in items:
        row = {key: item[key] for key in ("id", "name", "price", "count", "description", "category",
                                          "seasonal", "seasonal_label", "image", "art_kind") if key in item}
        row["effect_summary"] = _format_effects_summary(item.get("effects") or {})
        rows.append(row)
    return {"ok": True, "commerce_card": {"kind": kind, "items": rows, "page": page,
                                            "text_only": bool(parsed.get("text_only")),
                                            "coins": snapshot["coins"], "redeem_quota": snapshot["redeem_quota"]}}

def _merge_shop_items(
    base_items: list[dict[str, Any]],
    override_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge shop item lists by id; later lists replace earlier items with the same id."""
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for raw_item in [*(base_items or []), *(override_items or [])]:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or "").strip()
        if not item_id:
            continue
        item = dict(raw_item)
        if item_id in positions:
            merged[positions[item_id]] = item
        else:
            positions[item_id] = len(merged)
            merged.append(item)
    return merged

def _parse_item_and_quantity(text: str) -> "tuple[str, int]":
    """Parse 'item_nameX5' or 'item_name x5' → ('item_name', 5). Returns (text, 1) if no qty suffix."""
    m = _QTY_RE.match(text.strip())
    if m:
        qty = max(1, min(99, int(m.group(2))))
        return m.group(1).strip(), qty
    return text.strip(), 1

def _find_shop_item(items: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Find a shop item by id or name (case-insensitive)."""
    query_clean = str(query or "").strip().lower()
    if not query_clean:
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip().lower() == query_clean:
            return item
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip().lower() == query_clean:
            return item
    return None

def _format_effects_summary(effects: dict[str, Any]) -> str:
    """Format item effects as a short human-readable summary (for shop/backpack listing)."""
    parts: list[str] = []
    hunger = int(effects.get("hunger") or 0)
    energy = int(effects.get("energy") or 0)
    affection = int(effects.get("affection") or 0)
    if hunger:
        parts.append(f"饥饿{'+' if hunger > 0 else ''}{hunger}")
    if energy:
        parts.append(f"精力{'+' if energy > 0 else ''}{energy}")
    if affection:
        parts.append(f"好感{'+' if affection > 0 else ''}{affection}")
    if "hunger_set" in effects:
        parts.append(f"饥饿→{int(effects['hunger_set'])}")
    if "energy_set" in effects:
        parts.append(f"精力→{int(effects['energy_set'])}")
    if "affection_set" in effects:
        parts.append(f"好感→{int(effects['affection_set'])}")
    if effects.get("hunger_energy_swap"):
        parts.append("饥饿⇄精力")
    if effects.get("random_vitals"):
        parts.append("体征随机±")
    if effects.get("random_affection"):
        parts.append("好感随机±")
    return "  ".join(parts)
