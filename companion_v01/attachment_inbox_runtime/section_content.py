"""Extraction metadata stays separate from model-visible page bodies."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SectionContent:
    text: str = field(repr=False)
    source: str = "original"
    truncated: bool = False
    reason: str = ""
    error_type: str = ""
