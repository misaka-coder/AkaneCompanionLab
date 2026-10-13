"""Shared local QQ stationery, Chinese fonts and atomic image caching."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

_CACHE_LIMIT = 64
_CACHE_LOCK = threading.Lock()
_WIDTH = 900
_ARTWORK_HEIGHT = 1350
_HEADER_HEIGHT = 550
_FOOTER_HEIGHT = 235
_CONTENT_TOP = 590
_MIN_HEIGHT = 900
_MAX_HEIGHT = 2400
CECILIA_STATIONERY = Path(__file__).parent / "assets" / "qq_help" / "cecilia_stationery_background_v4.png"
INK = "#4E5D54"
HEADING = "#657F69"
SAGE = "#829681"
ACCENT = "#A06D7B"
MUTED = "#8C9290"


@lru_cache(maxsize=1)
def _font_paths() -> tuple[Path, Path]:
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        (windows_fonts / "STKAITI.TTF", windows_fonts / "STKAITI.TTF"),
        (windows_fonts / "simkai.ttf", windows_fonts / "simkai.ttf"),
        (windows_fonts / "SIMYOU.TTF", windows_fonts / "SIMYOU.TTF"),
        (windows_fonts / "msyh.ttc", windows_fonts / "msyhbd.ttc"),
        (windows_fonts / "simhei.ttf", windows_fonts / "simhei.ttf"),
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ),
        (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")),
        (Path("/System/Library/Fonts/PingFang.ttc"), Path("/System/Library/Fonts/PingFang.ttc")),
    )
    for regular, bold in candidates:
        if regular.is_file():
            return regular, bold if bold.is_file() else regular
    raise RuntimeError("chinese_font_unavailable")


def _wrap_text(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    return [line.rstrip() for line in wrap_card_text(draw, text, font, max_width)]


def wrap_card_text(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    """Lossless wrapping, keeping closing punctuation with the preceding word."""
    closers = frozenset("，。！？；：、）》】」』”’…,.!?;:%")
    openers = frozenset("（《【「『“‘(")
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
        elif current and draw.textlength(current + char, font=font) > max_width:
            split = len(current)
            if char in closers:
                split -= 1
                while split > 0 and current[split] in closers:
                    split -= 1
            while split > 0 and current[split - 1] in openers:
                split -= 1
            # Prefer a nearby word boundary for ordinary English prose.
            if split == len(current) and char.isascii() and char.isalnum():
                space = current.rfind(" ")
                if space >= len(current) // 2:
                    split = space + 1
            if split > 0:
                lines.append(current[:split])
                current = current[split:] + char
            else:
                lines.append(current)
                current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines or [""]


def _background(artwork: Path | None, height: int) -> Any:
    from PIL import Image, ImageDraw

    if artwork is not None:
        with Image.open(artwork) as image:
            source = image.convert("RGB").resize((_WIDTH, _ARTWORK_HEIGHT), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (_WIDTH, height))
        canvas.paste(source.crop((0, 0, _WIDTH, _HEADER_HEIGHT)), (0, 0))
        middle_end = _ARTWORK_HEIGHT - _FOOTER_HEIGHT
        # Extend a clean strip of paper and straight borders; the portrait,
        # flowers and ornaments keep their proportions in the fixed bands.
        middle = source.crop((0, 780, _WIDTH, 820))
        middle = middle.resize((_WIDTH, height - _HEADER_HEIGHT - _FOOTER_HEIGHT), Image.Resampling.LANCZOS)
        canvas.paste(middle, (0, _HEADER_HEIGHT))
        canvas.paste(source.crop((0, middle_end, _WIDTH, _ARTWORK_HEIGHT)), (0, height - _FOOTER_HEIGHT))
        return canvas
    canvas = Image.new("RGB", (_WIDTH, height), "#FDFEFC")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((24, 24, 876, height - 24), radius=30, outline="#C2D1BE", width=2)
    return canvas


def _ornament(draw: Any, x: int, y: int) -> None:
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        draw.polygon(
            (
                (x + dx * 2, y + dy * 2),
                (x + dx * 6 - dy * 2, y + dy * 6 + dx * 2),
                (x + dx * 10, y + dy * 10),
                (x + dx * 6 + dy * 2, y + dy * 6 - dx * 2),
            ),
            outline=SAGE,
        )


def draw_card_heading(canvas: Any, *, title: str, label: str = "", subtitle: str = "") -> None:
    """Typeset in the clear left of the portrait, with a quiet berry ink gradient."""
    import math
    from PIL import Image, ImageDraw, ImageFont

    regular, bold = _font_paths()
    draw = ImageDraw.Draw(canvas)
    size = 36 if len(title) > 24 else 43 if len(title) > 15 else 49 if len(title) > 9 else 59
    font = ImageFont.truetype(str(bold), size)
    lines = _wrap_text(draw, title, font, 335)
    if len(lines) > 4:
        raise RuntimeError("card_title_too_long")
    y = 137
    for line in lines:
        mask = Image.new("L", (math.ceil(draw.textlength(line, font=font)) + 6, size + 8))
        ImageDraw.Draw(mask).text((2, 0), line, font=font, anchor="lt", fill=255)
        gradient = Image.new("RGB", mask.size)
        ink = ImageDraw.Draw(gradient)
        for row in range(mask.height):
            t = row / max(1, mask.height - 1)
            ink.line((0, row, mask.width, row), fill=tuple(
                round(a + (b - a) * t) for a, b in zip((157, 107, 123), (126, 84, 101))
            ))
        canvas.paste(gradient, (98, y), mask)
        mask.close()
        gradient.close()
        y += size + 12
    label_font = ImageFont.truetype(str(regular), 27)
    if label:
        for i, line in enumerate(_wrap_text(draw, label, label_font, 335)[:2]):
            draw.text((100, 70 + i * 32), line, font=label_font, fill=SAGE, anchor="lt")
    if subtitle:
        for line in _wrap_text(draw, subtitle, label_font, 335):
            if y + 32 > _HEADER_HEIGHT - 20:
                raise RuntimeError("card_subtitle_too_long")
            draw.text((100, y + 17), line, font=label_font, fill=MUTED, anchor="lt")
            y += 34


def cached_card(cache_dir: Path, prefix: str, signature: dict[str, Any], paint: Callable[[], Any]) -> Path:
    key = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    path = Path(cache_dir) / f"{prefix}-{key}.png"
    with _CACHE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 0:
            path.touch()
            return path
        canvas = paint()
        temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
        try:
            canvas.save(temporary, format="PNG", optimize=True)
            temporary.replace(path)
        finally:
            canvas.close()
            temporary.unlink(missing_ok=True)
        # Each renderer only evicts files in its own cache namespace.
        cached = sorted(path.parent.glob(f"{prefix}-*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old_path in cached[_CACHE_LIMIT:]:
            try:
                old_path.unlink()
            except OSError:
                pass
    return path
