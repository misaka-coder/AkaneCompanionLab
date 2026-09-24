"""Portable image views at the provider boundary; original assets stay intact."""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError


MAX_GIF_BYTES = 8 * 1024 * 1024
MAX_GIF_PIXELS = 16 * 1024 * 1024


def model_image_blocks(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep ordinary images unchanged; expose GIF as a labelled PNG first frame.

    Used before protocol conversion for current images, history and tool images.
    Invalid GIFs become an explicit unavailable-image notice, not a poisoned
    request repeatedly sent to the provider. No URL fetching or asset mutation.
    """
    if block.get("type") != "image_url":
        return [dict(block)]
    value = block.get("image_url")
    url = value.get("url", "") if isinstance(value, dict) else value
    if not isinstance(url, str) or not url.startswith("data:image/"):
        return [dict(block)]
    header, separator, encoded = url.partition(",")
    # Also catch GIF bytes with stale MIME metadata, without decoding normal PNGs.
    is_gif = header.lower().startswith("data:image/gif") or encoded.startswith("R0lGOD")
    if not is_gif:
        return [dict(block)]
    reason = "image_decode_failed"
    try:
        if len(encoded) > (MAX_GIF_BYTES + 2) // 3 * 4:
            reason = "image_size_limit"
            raise ValueError(reason)
        if not separator or not header.lower().endswith(";base64"):
            raise ValueError(reason)
        data = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > MAX_GIF_PIXELS:
                reason = "image_pixel_limit"
                raise ValueError(reason)
            image.seek(0)
            output = BytesIO()
            image.convert("RGBA").save(output, format="PNG")
        data = output.getvalue()
        if len(data) > MAX_GIF_BYTES:
            reason = "image_size_limit"
            raise ValueError(reason)
    except (ValueError, OSError, binascii.Error, UnidentifiedImageError, Image.DecompressionBombError):
        return [{"type": "text", "text": f"[图片未能加载：{reason}；请勿推断其内容。]"}]
    image_url = dict(value) if isinstance(value, dict) else {}
    image_url["url"] = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    return [
        {"type": "text", "text": "[以下是图片的静态首帧，未提供 GIF 动画过程。]"},
        {**block, "image_url": image_url},
    ]
