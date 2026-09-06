"""Independent rendering library; importing it requires no Akane/SDK module."""

from .render import render_document, style_document
from .validation import DocumentError
from .sources import render_sources

__all__ = ["DocumentError", "render_document", "render_sources", "style_document"]
