"""Use an embeddable installed font; never redistribute a system font file."""

from __future__ import annotations

import os
from pathlib import Path

from .validation import DocumentError


def pdf_font(content: str) -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont, TTFError

    configured = os.environ.get("AKANE_DOCUMENT_FONT", "")
    candidates = (
        [Path(configured)]
        if configured
        else [
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simsun.ttc",
            Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    required = {ord(char) for char in content if not char.isspace()}
    for index, path in enumerate(candidates):
        if not path.is_file():
            continue
        try:
            font = TTFont(f"AkaneDocumentFont{index}", str(path), subfontIndex=0)
            # TTFont validates embedding rights while parsing. Require actual
            # glyphs, not a font name that merely looks multilingual.
            if any(not font.face.charToGlyph.get(code) for code in required):
                continue
            pdfmetrics.registerFont(font)
            pdfmetrics.registerFontFamily(
                font.fontName, normal=font.fontName, bold=font.fontName, italic=font.fontName, boldItalic=font.fontName
            )
            return font.fontName
        except (TTFError, OSError, ValueError):
            continue
    raise DocumentError("document_pdf_font_unavailable_or_missing_glyphs")
