"""Generated stationery for image tests; no private character artwork needed."""

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from PIL import Image, ImageDraw


def install_stationery_fixture(case: TestCase, root: Path) -> None:
    path = root / "test-stationery.png"
    image = Image.new("RGB", (1024, 1536), "#FDFEFC")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1023, 200), fill="#DBE8DA")
    draw.rectangle((0, 1400, 1023, 1535), fill="#EAD9E2")
    image.save(path)
    image.close()
    for module in (
        "rendering.card_artwork", "channels.qq.cards.help", "channels.qq.cards.care",
        "channels.qq.cards.commerce", "channels.qq.cards.cache", "rendering.text_cards",
    ):
        case.enterContext(patch(f"companion_v01.{module}.CECILIA_STATIONERY", path))
