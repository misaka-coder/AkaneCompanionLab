"""Portable image views at the provider boundary; original assets stay intact."""
from __future__ import annotations

import base64
import binascii
import json
import re
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError


MAX_GIF_BYTES = 8 * 1024 * 1024
MAX_GIF_PIXELS = 16 * 1024 * 1024


def image_source_metadata(image: dict[str, Any]) -> dict[str, str]:
    """Keep only bounded original-source fields, never the requesting actor."""
    fields: dict[str, str] = {}
    for key, limit in (
        ("source_sender_id", 120),
        ("source_sender_label", 80),
        ("source_message_id", 120),
    ):
        value = image.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()[:limit]
        if text:
            fields[key] = text
    return fields


def attachment_image_source_metadata(item: dict[str, Any]) -> dict[str, str]:
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    return image_source_metadata(
        {
            "source_sender_id": (
                detail.get("qq_sender_id") or detail.get("source_sender_id") or detail.get("sender_id")
            ),
            "source_sender_label": (
                detail.get("qq_sender_label") or detail.get("source_sender_label") or detail.get("sender_label")
            ),
            "source_message_id": item.get("source_message_id"),
        }
    )


def model_image_source_label(image: dict[str, Any]) -> str:
    """Put each material handle and its original sender next to its pixels."""
    handle = str(image.get("attachment_handle") or "").strip()[:120]
    source = image_source_metadata(image)
    if not handle and not source:
        return ""
    fields = {"handle": handle} if handle else {}
    if source.get("source_sender_label"):
        fields["original_sender"] = source["source_sender_label"]
    if source.get("source_sender_id"):
        fields["sender_id"] = source["source_sender_id"]
    if source.get("source_message_id"):
        fields["source_message_id"] = source["source_message_id"]
    return "【图片来源】" + " ".join(
        key + "=" + json.dumps(value, ensure_ascii=False) for key, value in fields.items()
    )


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
