"""Personal QQ care cards rendered from the existing Care authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .qq_card_artwork import (
    _background, _font_paths, _ornament, _wrap_text, _FOOTER_HEIGHT, _CONTENT_TOP,
    CECILIA_STATIONERY, draw_card_heading, cached_card,
)


@dataclass(frozen=True, slots=True)
class QQCareCardDocument:
    user_label: str
    character_label: str
    user_scope: str
    affection: int
    coins: int
    redeem_quota: int
    hunger: int
    energy: int
    checkin_streak: int
    shared_vitals: bool
    theme: str = "neutral"


def build_qq_care_card(
    snapshot: Mapping[str, Any],
    *,
    user_label: str,
    character_label: str,
    user_scope: str,
    character_pack_id: str,
) -> QQCareCardDocument:
    values = {}
    for field in ("affection", "coins", "redeem_quota", "hunger", "energy", "checkin_streak"):
        try:
            value = int(snapshot[field])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("care_snapshot_unavailable") from None
        maximum = 100 if field in {"affection", "hunger", "energy"} else 999999
        if not 0 <= value <= maximum:
            raise RuntimeError("care_snapshot_unavailable")
        values[field] = value
    return QQCareCardDocument(
        user_label=" ".join(str(user_label or "你").split())[:32] or "你",
        character_label=" ".join(str(character_label or "伙伴").split())[:32] or "伙伴",
        user_scope=str(user_scope),
        shared_vitals=bool(snapshot.get("shared_vitals")),
        theme="cecilia" if character_pack_id == "cecilia" else "neutral",
        **values,
    )


def render_qq_care_card(document: QQCareCardDocument, cache_dir: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    regular, bold = _font_paths()
    artwork = CECILIA_STATIONERY
    themed = document.theme == "cecilia"
    if themed and not artwork.is_file():
        raise RuntimeError("care_background_unavailable")
    signature = {
        "version": 4,
        "document": asdict(document),
        "fonts": [(path.name, path.stat().st_mtime_ns) for path in (regular, bold)],
        "artwork": artwork.stat().st_mtime_ns if themed else None,
    }

    def paint() -> Any:
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        name_font = ImageFont.truetype(str(bold), 38)
        label_font = ImageFont.truetype(str(regular), 31)
        number_font = ImageFont.truetype(str(bold), 55)
        value_font = ImageFont.truetype(str(regular), 35)
        small_font = ImageFont.truetype(str(regular), 24)
        name_lines = _wrap_text(measure, f"{document.user_label}的专属状态", name_font, 646)
        first_y = _CONTENT_TOP + len(name_lines) * 50 + 24
        assets_y = first_y + 122
        vitals_y = assets_y + 150
        hunger_y, energy_y = vitals_y + 58, vitals_y + 160
        height = ((energy_y + 84 + _FOOTER_HEIGHT + 19) // 20) * 20
        canvas = _background(artwork if themed else None, height)
        draw = ImageDraw.Draw(canvas)
        draw_card_heading(canvas, title=document.character_label, label="/养成")
        for index, line in enumerate(name_lines):
            draw.text((130, _CONTENT_TOP + index * 50), line, font=name_font, fill="#657F69")

        def progress(y: int, label: str, value: int, color: str) -> None:
            draw.text((130, y), label, font=label_font, fill="#4E5D54")
            draw.text((775, y), f"{value}/100", font=value_font, fill="#A06D7B", anchor="ra")
            draw.rounded_rectangle((130, y + 56, 775, y + 69), radius=6, fill="#EAF0E7")
            if value:
                draw.rounded_rectangle((130, y + 56, 130 + 645 * value / 100, y + 69), radius=6, fill=color)

        progress(first_y, "好感度", document.affection, "#D6ACBC")
        for x, label, value in ((130, "金币", document.coins), (485, "兑换额度", document.redeem_quota)):
            draw.text((x, assets_y), label, font=label_font, fill="#4E5D54")
            draw.text((x, assets_y + 42), str(value), font=number_font, fill="#A06D7B")
        _ornament(draw, 432, assets_y + 66)
        scope = "陪伴状态 · 当前会话"
        draw.text((130, vitals_y), scope, font=label_font, fill="#657F69")
        progress(hunger_y, "饱腹度", document.hunger, "#A9BFA1")
        progress(energy_y, "精力", document.energy, "#B2C7CB")
        draw.text(
            (450, height - 195), f"连续签到 {document.checkin_streak} 天", font=small_font, fill="#829681", anchor="ma"
        )
        draw.text((450, height - 151), "/签到 · /商店 · /背包", font=small_font, fill="#8C9290", anchor="ma")
        return canvas

    return cached_card(cache_dir, "qq-care", signature, paint)
