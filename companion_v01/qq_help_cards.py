"""Small local PNG help cards. Missing Pillow/fonts fall back at the QQ route."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .qq_commands import QQHelpDocument
from .qq_card_artwork import (
    _background,
    _font_paths,
    _wrap_text,
    _ornament,
    _MIN_HEIGHT,
    _MAX_HEIGHT,
    _FOOTER_HEIGHT,
    _CONTENT_TOP,
    CECILIA_STATIONERY,
    draw_card_heading,
    cached_card,
)


_RENDER_VERSION = 6


def render_qq_help_card(document: QQHelpDocument, cache_dir: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    regular, bold = _font_paths()
    artwork = CECILIA_STATIONERY
    themed = document.theme == "cecilia"
    if themed and not artwork.is_file():
        raise RuntimeError("help_background_unavailable")
    signature = {
        "version": _RENDER_VERSION,
        "document": asdict(document),
        "fonts": [(path.name, path.stat().st_mtime_ns) for path in (regular, bold)],
        "artwork": artwork.stat().st_mtime_ns if themed else None,
    }

    def paint() -> Any:
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        footer_font = ImageFont.truetype(str(regular), 24)
        category_font = ImageFont.truetype(str(bold), 44)
        command_font = ImageFont.truetype(str(regular), 31)
        left, content_y = 100, _CONTENT_TOP + (76 if document.category else 0)
        footer_lines = [line for text in document.footer for line in _wrap_text(measure, text, footer_font, 700)]
        rows = []

        if not document.category:
            bottom = content_y + max(0, len(document.entries) - 1) * 88 + 50
        else:
            # Measure first, extend the paper, then draw. Font reduction is only
            # for unusually long plugin commands approaching the image limit.
            for usage_size in (32, 30, 28, 26):
                usage_font = ImageFont.truetype(str(bold), usage_size)
                body_font = ImageFont.truetype(str(regular), max(23, usage_size - 7))
                rows = []
                for entry in document.entries:
                    usages = _wrap_text(measure, entry.usage, usage_font, 646)
                    descriptions = _wrap_text(measure, entry.description, body_font, 646)
                    row_height = (usage_size + 10) * len(usages) + (body_font.size + 9) * len(descriptions) + 24
                    rows.append((usages, descriptions, row_height))
                bottom = content_y + sum(row[2] + 18 for row in rows) - 18
                if bottom + 42 + max(_FOOTER_HEIGHT, len(footer_lines) * 34 + 130) <= _MAX_HEIGHT:
                    break
            else:
                raise RuntimeError("help_card_too_tall")

        footer_y = bottom + 42
        footer_height = max(_FOOTER_HEIGHT, len(footer_lines) * 34 + 130)
        height = max(_MIN_HEIGHT, ((footer_y + footer_height + 19) // 20) * 20)
        if height > _MAX_HEIGHT:
            raise RuntimeError("help_card_too_tall")
        canvas = _background(artwork if themed else None, height)
        draw = ImageDraw.Draw(canvas)
        draw_card_heading(canvas, title="指令手册", label="/help")
        if document.category:
            _ornament(draw, left + 12, _CONTENT_TOP + 25)
            draw.text((left + 35, _CONTENT_TOP), document.category, font=category_font, fill="#657F69")
        if not document.category:
            for index, entry in enumerate(document.entries):
                row_y = content_y + index * 88
                _ornament(draw, 245, row_y + 26)
                _ornament(draw, 415, row_y + 26)
                draw.text((275, row_y), entry.usage.removeprefix("/help "), font=category_font, fill="#657F69")
                draw.text((465, row_y + 10), entry.usage, font=command_font, fill="#A06D7B")
        else:
            y = content_y
            for usages, descriptions, row_height in rows:
                text_y = y + 10
                for line in usages:
                    draw.text((left + 30, text_y), line, font=usage_font, fill="#A06D7B")
                    text_y += usage_font.size + 10
                for line in descriptions:
                    draw.text((left + 30, text_y), line, font=body_font, fill="#4E5D54")
                    text_y += body_font.size + 9
                y += row_height + 18

        footer_y = max(footer_y, height - len(footer_lines) * 34 - 145)
        for line in footer_lines:
            draw.text((450, footer_y), line, font=footer_font, fill="#829681", anchor="ma")
            footer_y += 34
        return canvas

    return cached_card(cache_dir, "qq-help", signature, paint)
