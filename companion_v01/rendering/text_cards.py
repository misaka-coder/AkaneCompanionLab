"""Local typography on one continuous sheet; no model or transport calls."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from companion_v01.rendering.card_artwork import (
    _CONTENT_TOP,
    _FOOTER_HEIGHT,
    ACCENT,
    CECILIA_STATIONERY,
    HEADING,
    INK,
    SAGE,
    _background,
    _font_paths,
    draw_card_heading,
    wrap_card_text,
)

RENDER_VERSION = 2
MAX_BODY_CHARS = 4000
MAX_CARD_HEIGHT = 20000
LEFT, RIGHT = 98, 798


@dataclass(frozen=True)
class TextCardDocument:
    title: str
    body: str
    subtitle: str = ""
    author: str = ""
    theme: str = "neutral"


@dataclass(frozen=True)
class CardRow:
    text: str
    kind: str
    source_text: str
    height: int


@dataclass(frozen=True)
class TextCardLayout:
    rows: tuple[CardRow, ...]
    height: int
    image_size: tuple[int, int] | None = None

    @property
    def body(self) -> str:
        return "".join(row.source_text for row in self.rows)


def validate_card_call(call: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, limit in (("title", 24), ("body", MAX_BODY_CHARS), ("subtitle", 48), ("image_id", 120)):
        value = call.get(key, "")
        if not isinstance(value, str):
            raise ValueError("card_arguments_invalid")
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(value) > limit:
            raise ValueError(f"card_{key}_too_long")
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("card_arguments_invalid")
        if key != "body" and "\n" in value:
            raise ValueError("card_arguments_invalid")
        values[key] = value
    if not values["title"] or not values["body"]:
        raise ValueError("card_text_required")
    if values["image_id"] and not re.fullmatch(r"(?:img_\d+|image_\d+|gen_\d+|attachment::[\w.-]+|generated::[\w.-]+)", values["image_id"]):
        raise ValueError("card_image_handle_required")
    return values


def renderer_fingerprint(theme: str) -> list[Any]:
    fonts = _font_paths()
    if theme == "cecilia" and not CECILIA_STATIONERY.is_file():
        raise RuntimeError("card_background_unavailable")
    return [RENDER_VERSION, [(path.name, path.stat().st_mtime_ns) for path in fonts],
            CECILIA_STATIONERY.stat().st_mtime_ns if theme == "cecilia" else None]


def layout_text_card(document: TextCardDocument, *, image_path: Path | None = None) -> TextCardLayout:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    from companion_v01.image_materials import verified_file_image_media_type

    renderer_fingerprint(document.theme)
    regular, bold = _font_paths()
    fonts = {"body": ImageFont.truetype(str(regular), 34),
             "heading": ImageFont.truetype(str(bold), 39),
             "quote": ImageFont.truetype(str(regular), 34)}
    image_size = None
    if image_path is not None:
        if not verified_file_image_media_type(image_path):
            raise ValueError("image_content_invalid")
        with Image.open(image_path) as image:
            with ImageOps.exif_transpose(image) as oriented:
                ratio = min(700 / oriented.width, 480 / oriented.height)
                image_size = (max(1, round(oriented.width * ratio)), max(1, round(oriented.height * ratio)))

    rows: list[CardRow] = []
    with Image.new("RGB", (1, 1)) as measure_image:
        measure = ImageDraw.Draw(measure_image)
        for raw in document.body.splitlines(keepends=True):
            text = raw.removesuffix("\n")
            newline = "\n" if raw.endswith("\n") else ""
            if not text.strip():
                rows.append(CardRow("", "space", raw, 22))
                continue
            prefix = ""
            kind = "body"
            match = re.match(r"^(#{1,3} |>[ \t]?)", text)
            if match:
                prefix = match.group(0)
                text = text[len(prefix):]
                kind = "quote" if prefix.startswith(">") else "heading"
            width = RIGHT - LEFT - (34 if kind in {"heading", "quote"} else 0)
            lines = wrap_card_text(measure, text, fonts[kind], width)
            for index, line in enumerate(lines):
                source = (prefix if index == 0 else "") + line + (newline if index == len(lines) - 1 else "")
                rows.append(CardRow(line, kind, source, 55 if kind == "heading" else 53))

    used = _CONTENT_TOP + (image_size[1] + 32 if image_size else 0) + sum(row.height for row in rows)
    # Keep type and artwork at their original scale; extend only the paper.
    height = max(1060, ((used + _FOOTER_HEIGHT + 100 + 19) // 20) * 20)
    if height > MAX_CARD_HEIGHT:
        raise ValueError("card_image_height_limit")
    return TextCardLayout(tuple(rows), height, image_size)


def paint_text_card(document: TextCardDocument, layout: TextCardLayout, *,
                    image_path: Path | None = None) -> Any:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    regular, bold = _font_paths()
    canvas = _background(CECILIA_STATIONERY if document.theme == "cecilia" else None, layout.height)
    try:
        draw_card_heading(canvas, title=document.title, subtitle=document.subtitle)
        draw = ImageDraw.Draw(canvas)
        fonts = {"body": ImageFont.truetype(str(regular), 34),
                 "heading": ImageFont.truetype(str(bold), 39),
                 "quote": ImageFont.truetype(str(regular), 34)}
        y = _CONTENT_TOP
        if layout.image_size and image_path:
            with Image.open(image_path) as original:
                with ImageOps.exif_transpose(original).convert("RGB") as rgb:
                    with rgb.resize(layout.image_size, Image.Resampling.LANCZOS) as image:
                        canvas.paste(image, ((900 - image.width) // 2, y))
            y += layout.image_size[1] + 32
        for row in layout.rows:
            if row.text:
                x = LEFT + (34 if row.kind in {"heading", "quote"} else 0)
                color = HEADING if row.kind == "heading" else SAGE if row.kind == "quote" else INK
                if row.kind == "heading":
                    draw.ellipse((LEFT + 3, y + 16, LEFT + 11, y + 24), fill="#A9BFA1")
                elif row.kind == "quote":
                    draw.line((LEFT + 6, y + 3, LEFT + 6, y + row.height - 10), fill="#C2D1BE", width=2)
                draw.text((x, y), row.text, font=fonts[row.kind], fill=color, anchor="lt")
            y += row.height
        if document.author:
            font = ImageFont.truetype(str(regular), 30)
            for index, line in enumerate(wrap_card_text(draw, document.author, font, RIGHT - LEFT)):
                draw.text((RIGHT, y + 20 + index * 35), line, font=font, fill=ACCENT, anchor="rt")
        return canvas
    except Exception:
        canvas.close()
        raise
