"""Independent rendering library; importing it requires no Akane/SDK module."""

from .render import render_document, style_document
from .validation import DocumentError

__all__ = ["DocumentError", "render_document", "style_document"]
