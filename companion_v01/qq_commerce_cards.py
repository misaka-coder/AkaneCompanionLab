"""Live QQ shelves and personal inventories on the shared Cecilia stationery."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from .shop_artwork import artwork_file, image_signature, paste_artwork

from .qq_card_artwork import (
    _background, _font_paths, _wrap_text, _FOOTER_HEIGHT, _MAX_HEIGHT, _CONTENT_TOP,
    CECILIA_STATIONERY, draw_card_heading, cached_card,
)

QQ_COMMERCE_PAGE_SIZE = 5


@dataclass(frozen=True, slots=True)
class QQShopChangeCardDocument:
    action: str
    character_label: str
    user_scope: str
    item: dict[str, Any]
    theme: str = "neutral"
    image_path: str = ""

    @property
    def title(self) -> str:
        return {"create": "已上架", "update": "已更新", "delete": "已下架"}[self.action]

    @property
    def price_label(self) -> str:
        modes = self.item["usable_in"]
        price = self.item["price"]
        return " · ".join(part for mode, part in (
            ("qq", f"QQ {price} 额度"), ("desktop_pet", f"桌宠 {price} 金币"),
        ) if mode in modes)

    @property
    def effects_label(self) -> str:
        return " · ".join(f"{name}{self.item['effects'].get(key, 0):+d}" for key, name in (
            ("hunger", "饱腹"), ("energy", "精力"), ("affection", "好感"),
        ))

    def as_text(self) -> str:
        return "\n".join(part for part in (
            f"{self.character_label}的商店 · {self.title}", self.item["name"],
            self.item["description"], self.price_label, self.effects_label,
        ) if part)


def build_qq_shop_change_card(
    action: str, item: Mapping[str, Any], *, character_label: str, user_scope: str, character_pack_id: str, resources=None,
) -> QQShopChangeCardDocument:
    if action not in {"create", "update", "delete"} or not item.get("id") or not item.get("name"):
        raise RuntimeError("commerce_snapshot_unavailable")
    effects = item.get("effects")
    price = item.get("price")
    modes = item.get("usable_in")
    if (not isinstance(price, int) or isinstance(price, bool) or not 0 <= price <= 999999
            or not isinstance(effects, dict) or not isinstance(modes, list)
            or not modes or any(not isinstance(mode, str) or mode not in {"qq", "desktop_pet"} for mode in modes)
            or any(not isinstance(effects.get(key, 0), int) for key in ("hunger", "energy", "affection"))):
        raise RuntimeError("commerce_snapshot_unavailable")
    return QQShopChangeCardDocument(action, _text(character_label, 32) or "伙伴", str(user_scope), {
        "id": _text(item["id"], 120), "name": _text(item["name"], 40),
        "description": _text(item.get("description"), 120), "price": price,
        "effects": {key: effects.get(key, 0) for key in ("hunger", "energy", "affection")},
        "usable_in": modes,
    }, "cecilia" if character_pack_id == "cecilia" else "neutral", str(artwork_file(resources, character_pack_id, item)))


@dataclass(frozen=True, slots=True)
class QQPurchaseCardDocument:
    user_label: str
    character_label: str
    user_scope: str
    item: dict[str, Any]
    quantity: int
    cost: int
    quota_after: int
    inventory_count: int
    theme: str = "neutral"
    image_path: str = ""

    @property
    def title(self) -> str:
        return "购买成功"

    @property
    def price_label(self) -> str:
        return f"消耗 {self.cost} 额度 · 剩余 {self.quota_after} 额度"

    @property
    def effects_label(self) -> str:
        return f"/投喂 {self.item['id']}" if self.item.get("use_id") else f"/投喂 {self.item['name']}"

    def as_text(self) -> str:
        return (f"{self.user_label} · {self.title}\n{self.item['name']} ×{self.quantity}\n"
                f"已放入背包 · 当前共 ×{self.inventory_count}\n{self.price_label}\n{self.effects_label}")


def build_qq_purchase_card(
    payload: Mapping[str, Any], *, user_label: str, character_label: str, user_scope: str,
    character_pack_id: str, resources=None,
) -> QQPurchaseCardDocument:
    item = payload.get("item")
    if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
        raise RuntimeError("commerce_snapshot_unavailable")
    values = {}
    for key in ("quantity", "cost", "quota_after", "inventory_count"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError("commerce_snapshot_unavailable")
        values[key] = value
    if values["quantity"] < 1 or values["inventory_count"] < values["quantity"]:
        raise RuntimeError("commerce_snapshot_unavailable")
    return QQPurchaseCardDocument(
        _text(user_label, 32), _text(character_label, 32), str(user_scope),
        {"id": _text(item["id"], 120), "name": _text(item["name"], 160), "use_id": bool(payload.get("use_id"))},
        **values, theme="cecilia" if character_pack_id == "cecilia" else "neutral",
        image_path=str(artwork_file(resources, character_pack_id, item)),
    )


def render_qq_purchase_card(document: QQPurchaseCardDocument, cache_dir: Path) -> Path:
    return _render_commerce_receipt(document, cache_dir, "qq-purchase")


def render_qq_shop_change_card(document: QQShopChangeCardDocument, cache_dir: Path) -> Path:
    return _render_commerce_receipt(document, cache_dir, "qq-shop-change")


def _render_commerce_receipt(document: QQShopChangeCardDocument | QQPurchaseCardDocument,
                             cache_dir: Path, cache_name: str) -> Path:
    """A compact receipt on the existing paper, without a model request."""
    from PIL import Image, ImageDraw, ImageFont

    regular, bold = _font_paths()
    artwork = CECILIA_STATIONERY
    themed = document.theme == "cecilia"
    if themed and not artwork.is_file():
        raise RuntimeError("commerce_background_unavailable")
    signature = {"version": 5, "document": asdict(document), "image": image_signature(document.image_path),
                 "fonts": [(p.name, p.stat().st_mtime_ns) for p in (regular, bold)],
                 "artwork": artwork.stat().st_mtime_ns if themed else None}

    def paint() -> Any:
        label = ImageFont.truetype(str(regular), 27)
        name_font = ImageFont.truetype(str(bold), 38)
        body = ImageFont.truetype(str(regular), 27)
        measure = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(measure)
        purchase = isinstance(document, QQPurchaseCardDocument)
        heading = f"{document.user_label} · {document.character_label}的商店" if purchase else f"{document.character_label}的商店"
        shop_name = _wrap_text(draw, heading, label, 600)
        name = f"{document.item['name']} ×{document.quantity}" if purchase else document.item["name"]
        names = _wrap_text(draw, name, name_font, 590)
        detail = f"已放入背包 · 当前共 ×{document.inventory_count}" if purchase else document.item["description"]
        description = _wrap_text(draw, detail, body, 590) if detail else []
        price_lines = _wrap_text(draw, document.price_label, label, 770)
        effect_lines = _wrap_text(draw, document.effects_label, label, 770)
        top = max(215, 106 + len(shop_name) * 33)
        height = max(610, top + max(150, len(names) * 47 + len(description) * 35 + 14)
                     + 105 + (len(price_lines) + len(effect_lines)) * 35)
        measure.close()
        canvas = _background(None, height)
        if themed:
            with Image.open(artwork) as source:
                source = source.convert("RGB").resize((900, 1350), Image.Resampling.LANCZOS)
                paper = source.crop((0, 780, 900, 820)).resize((900, height), Image.Resampling.LANCZOS)
                canvas.close()
                canvas = paper
                top_left = source.crop((0, 0, 450, 48))
                canvas.paste(top_left, (0, 0))
                canvas.paste(top_left.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (450, 0))
                top_left.close()
                canvas.paste(source.crop((0, 1270, 900, 1350)), (0, height - 80))
                portrait = source.crop((475, 25, 865, 420)).resize((166, 169), Image.Resampling.LANCZOS)
                mask = Image.new("L", portrait.size, 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 165, 168), fill=255)
                canvas.paste(portrait, (682, 53), mask)
                ImageDraw.Draw(canvas).ellipse((680, 51, 850, 224), outline="#C2D1BE", width=1)
                portrait.close()
                mask.close()
                source.close()
        draw = ImageDraw.Draw(canvas)
        draw.text((65, 66), document.title, font=label, fill="#A06D7B")
        for index, line in enumerate(shop_name):
            draw.text((65, 107 + index * 33), line, font=label, fill="#657F69")
        y = top
        paste_artwork(canvas, document.image_path, (60, y, 160, 160))
        for line in names:
            draw.text((245, y), line, font=name_font, fill="#4E5D54")
            y += 47
        y += 14
        for line in description:
            draw.text((245, y), line, font=body, fill="#4E5D54")
            y += 35
        y = max(y, top + 150)
        y += 26
        for lines, color in ((price_lines, "#A06D7B"), (effect_lines, "#829681")):
            for line in lines:
                draw.text((65, y), line, font=label, fill=color)
                y += 35
            y += 8
        return canvas

    return cached_card(cache_dir, cache_name, signature, paint)


@dataclass(frozen=True, slots=True)
class QQCommerceItem:
    item_id: str
    name: str
    description: str
    effects: str
    tag: str
    value: int
    same_name: bool = False
    image_path: str = ""


@dataclass(frozen=True, slots=True)
class QQCommerceCardDocument:
    kind: str
    user_label: str
    character_label: str
    user_scope: str
    coins: int
    redeem_quota: int
    entries: tuple[QQCommerceItem, ...]
    page: int
    pages: int
    total_items: int
    theme: str = "neutral"

    @property
    def title(self) -> str:
        return "商店" if self.kind == "shop" else "背包"

    def as_text(self) -> str:
        lines = [
            f"{self.user_label} · {self.title}  {self.page}/{self.pages}",
            f"金币 {self.coins} · 兑换额度 {self.redeem_quota}",
        ]
        if not self.entries:
            lines.append("商店暂时没有商品。" if self.kind == "shop" else "背包还是空的，去 /商店挑一件吧。")
        for item in self.entries:
            value = f"{item.value} 额度" if self.kind == "shop" else f"×{item.value}"
            lines.append(f"\n{item.name}  {value}")
            if item.description:
                lines.append(item.description)
            lines.append(" · ".join(part for part in (item.tag, item.effects) if part))
            if item.same_name:
                lines.append(f"同名物品请用 ID：{item.item_id}")
        lines.append(
            "\n/购买 商品名 ×数量 · /赛钱 金币数" if self.kind == "shop" else "\n/投喂 商品名 ×数量 · /供奉 商品名"
        )
        if self.pages > 1:
            lines.append(f"/{self.title} 页码 · 共 {self.total_items} 种物品")
        return "\n".join(lines)


def _text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def build_qq_commerce_card(
    payload: Mapping[str, Any], *, user_label: str, character_label: str, user_scope: str, character_pack_id: str, resources=None
) -> QQCommerceCardDocument:
    kind = str(payload.get("kind") or "")
    if kind not in {"shop", "backpack"} or not isinstance(payload.get("items"), list):
        raise RuntimeError("commerce_snapshot_unavailable")
    values = {}
    for field in ("coins", "redeem_quota", "page"):
        try:
            values[field] = int(payload[field])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("commerce_snapshot_unavailable") from None
        if values[field] < 0 or values[field] > 999999:
            raise RuntimeError("commerce_snapshot_unavailable")
    items = []
    names = Counter(_text(item.get("name"), 160).casefold() for item in payload["items"] if isinstance(item, dict))
    category_order = {"custom": 1, "charm": 2, "trick": 2, "potion": 2, "offering": 3, "gift": 3}
    ordered = (
        sorted(
            payload["items"],
            key=lambda item: (
                0 if item.get("seasonal") else 1,
                category_order.get(str(item.get("category") or ""), 0),
            ),
        )
        if all(isinstance(item, dict) for item in payload["items"])
        else payload["items"]
    )
    for raw in ordered:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
            raise RuntimeError("commerce_snapshot_unavailable")
        try:
            value = int(raw["price" if kind == "shop" else "count"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("commerce_snapshot_unavailable") from None
        if value < 0 or (kind == "backpack" and value == 0):
            raise RuntimeError("commerce_snapshot_unavailable")
        name = _text(raw["name"], 160)
        category = str(raw.get("category") or "")
        tag = (
            _text(raw.get("seasonal_label"), 24) + "限定"
            if raw.get("seasonal")
            else {
                "custom": "定制",
                "offering": "供奉",
                "gift": "礼物",
                "charm": "符咒",
                "trick": "奇妙物品",
                "potion": "药水",
                "drink": "饮品",
            }.get(category, "食物")
        )
        items.append(
            QQCommerceItem(
                _text(raw["id"], 120),
                name,
                _text(raw.get("description"), 120),
                _text(raw.get("effect_summary"), 180).replace("饥饿", "饱腹").replace("饱腹⇄精力", "饱腹、精力互换"),
                tag,
                value,
                names[name.casefold()] > 1,
                str(artwork_file(resources, character_pack_id, raw)),
            )
        )
    pages = max(1, (len(items) + QQ_COMMERCE_PAGE_SIZE - 1) // QQ_COMMERCE_PAGE_SIZE)
    page = values.pop("page")
    if not 1 <= page <= pages:
        raise RuntimeError("commerce_page_out_of_range")
    return QQCommerceCardDocument(
        kind,
        _text(user_label, 32) or "你",
        _text(character_label, 32) or "伙伴",
        str(user_scope),
        **values,
        entries=tuple(items[(page - 1) * QQ_COMMERCE_PAGE_SIZE : page * QQ_COMMERCE_PAGE_SIZE]),
        page=page,
        pages=pages,
        total_items=len(items),
        theme="cecilia" if character_pack_id == "cecilia" else "neutral",
    )


def render_qq_commerce_card(document: QQCommerceCardDocument, cache_dir: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    regular, bold = _font_paths()
    artwork = CECILIA_STATIONERY
    themed = document.theme == "cecilia"
    if themed and not artwork.is_file():
        raise RuntimeError("commerce_background_unavailable")
    signature = {
        "version": 4,
        "images": [image_signature(item.image_path) for item in document.entries],
        "document": asdict(document),
        "fonts": [(path.name, path.stat().st_mtime_ns) for path in (regular, bold)],
        "artwork": artwork.stat().st_mtime_ns if themed else None,
    }

    def paint() -> Any:
        canvas_for_measure = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(canvas_for_measure)
        label_font = ImageFont.truetype(str(regular), 30)
        small_font = ImageFont.truetype(str(regular), 25)
        left, right = 130, 775

        def preview(text: str, width: int) -> list[str]:
            if not text:
                return []
            lines = _wrap_text(draw, text, body_font, width)
            if len(lines) > 2:
                tail = lines[1]
                while tail and draw.textlength(tail + "…", font=body_font) > width:
                    tail = tail[:-1]
                lines = [lines[0], tail + "…"]
            return lines

        labels = _wrap_text(draw, f"{document.user_label} · {document.title}", label_font, right - left)
        # Ordinary shelves use larger type. Unusually long names and IDs keep
        # the existing five-item page readable within QQ's image height limit.
        for name_size, body_size, price_size, name_leading, body_leading, art_gap in (
            (34, 28, 28, 43, 35, 145), (32, 25, 26, 38, 31, 145),
            (32, 23, 26, 38, 29, 145), (32, 23, 26, 38, 29, 0),
        ):
            name_font = ImageFont.truetype(str(bold), name_size)
            body_font = ImageFont.truetype(str(regular), body_size)
            price_font = ImageFont.truetype(str(bold), price_size)
            y = _CONTENT_TOP + len(labels) * 38 + 64
            rows = []
            for item in document.entries:
                value = f"{item.value} 额度" if document.kind == "shop" else f"×{item.value}"
                width = right - left - art_gap - int(draw.textlength(value, font=price_font)) - 28
                names = _wrap_text(draw, item.name, name_font, width)
                descriptions = preview(item.description, right - left - art_gap)
                details = preview(" · ".join(part for part in (item.tag, item.effects) if part), right - left - art_gap)
                item_id = _wrap_text(draw, f"ID {item.item_id}", body_font, right - left - art_gap) if item.same_name else []
                row_height = max(145, len(names) * name_leading + (len(descriptions) + len(details) + len(item_id)) * body_leading + 30)
                rows.append((y, value, names, descriptions, details, item_id))
                y += row_height
            height = max(1250, ((y + 10 + _FOOTER_HEIGHT + 19) // 20) * 20)
            if height <= _MAX_HEIGHT:
                break
        canvas_for_measure.close()
        if height > _MAX_HEIGHT:
            raise RuntimeError("commerce_layout_too_tall")
        canvas = _background(artwork if themed else None, height)
        draw = ImageDraw.Draw(canvas)
        draw_card_heading(canvas, title=document.character_label, label=f"/{document.title}")
        for index, line in enumerate(labels):
            draw.text((left, _CONTENT_TOP + index * 38), line, font=label_font, fill="#657F69")
        balance_y = _CONTENT_TOP + len(labels) * 38 + 12
        draw.text(
            (left, balance_y),
            f"金币 {document.coins}   ·   兑换额度 {document.redeem_quota}",
            font=small_font,
            fill="#829681",
        )
        for item, (row_y, value, names, descriptions, details, item_id) in zip(document.entries, rows):
            if art_gap:
                paste_artwork(canvas, item.image_path, (left - 10, row_y, 140, 140))
            draw.text((right, row_y + 5), value, font=price_font, fill="#A06D7B", anchor="ra")
            for line in names:
                draw.text((left + art_gap, row_y), line, font=name_font, fill="#4E5D54")
                row_y += name_leading
            for line in descriptions:
                draw.text((left + art_gap, row_y), line, font=body_font, fill="#4E5D54")
                row_y += body_leading
            for line in [*details, *item_id]:
                draw.text((left + art_gap, row_y), line, font=body_font, fill="#829681")
                row_y += body_leading
        if not rows:
            message = "背包还是空的" if document.kind == "backpack" else "商店暂时没有商品"
            draw.text((450, 770), message, font=name_font, fill="#829681", anchor="ma")
            if document.kind == "backpack":
                draw.text((450, 832), "去 /商店挑一件吧", font=small_font, fill="#8C9290", anchor="ma")
        footer = "/购买 商品名 ×数量 · /赛钱 金币数" if document.kind == "shop" else "/投喂 商品名 ×数量 · /供奉 商品名"
        draw.text((450, height - 195), footer, font=small_font, fill="#829681", anchor="ma")
        nav = f"{document.page} / {document.pages}"
        if document.page < document.pages:
            nav += f"   ·   下一页 /{document.title} {document.page + 1}"
        draw.text((450, height - 151), nav, font=small_font, fill="#8C9290", anchor="ma")
        return canvas

    return cached_card(cache_dir, "qq-commerce", signature, paint)
