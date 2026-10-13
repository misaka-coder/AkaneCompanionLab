"""Portable shop artwork. Uploads are decoded locally; no remote URL fetching."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageOps, UnidentifiedImageError

KINDS = ("auto", "dessert", "drink", "meal", "gift")
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
DEFAULTS = Path(__file__).resolve().parent.parent / "web" / "assets" / "shop"


def artwork_kind(item):
    kind = item.get("art_kind", "auto")
    if kind in KINDS and kind != "auto":
        return kind
    name = str(item.get("name", ""))
    if any(word in name for word in ("蛋糕", "甜", "布丁", "团子", "巧克力")):
        return "dessert"
    if any(word in name for word in ("茶", "奶", "咖啡", "饮", "果汁")):
        return "drink"
    if any(word in name for word in ("面", "饭", "便当", "寿司", "饼", "餐")):
        return "meal"
    return "gift"


def resolve_artwork(service, pack_id, relative):
    """Use the resource service's pack boundary, then enforce the assets boundary."""
    if not service or not isinstance(relative, str) or not relative:
        return None
    if "\\" in relative or ":" in relative or ".." in relative.split("/"):
        return None
    if not relative.startswith("assets/") or Path(relative).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    try:
        pack = service._resolve_pack_dir(pack_id)
        if pack is None:
            return None
        pack = Path(pack).resolve()
        assets = (pack / "assets").resolve()
        assets.relative_to(pack)
        target = (pack / relative).resolve()
        target.relative_to(assets)
        return target if target.is_file() and target.stat().st_size <= MAX_UPLOAD_BYTES else None
    except (OSError, ValueError):
        return None


def artwork_file(service, pack_id, item):
    return resolve_artwork(service, pack_id, item.get("image")) or DEFAULTS / f"{artwork_kind(item)}.png"


def artwork_url(service, pack_id, item):
    if resolve_artwork(service, pack_id, item.get("image")):
        prefix = getattr(service, "public_prefix", "/desktop-pet-character-packs")
        return f"{prefix}/{quote(pack_id, safe='')}/{quote(item['image'], safe='/')}"
    return f"/assets/shop/{artwork_kind(item)}.svg"


def update_artwork(pack, entry, payload):
    if "art_kind" in payload:
        if payload["art_kind"] not in KINDS:
            raise ValueError("invalid_shop_art_kind")
        entry["art_kind"] = payload["art_kind"]
    if "image_upload" not in payload:
        return
    value = payload["image_upload"]
    if value == "":
        entry.pop("image", None)
        return
    if not isinstance(value, str) or len(value) > (MAX_UPLOAD_BYTES * 4 // 3 + 128):
        raise ValueError("invalid_shop_image")
    try:
        header, encoded = value.split(",", 1)
        if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError()
        with Image.open(io.BytesIO(raw)) as source:
            if (source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1
                    or source.width * source.height > 16_000_000):
                raise ValueError()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((768, 768), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            image.close()
        data = output.getvalue()
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("invalid_shop_image") from None
    pack = Path(pack).resolve()
    directory = (pack / "assets" / "shop").resolve()
    try:
        directory.relative_to(pack)
    except ValueError:
        raise ValueError("invalid_shop_image") from None
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(data).hexdigest() + ".png"
    target = directory / name
    if target.is_symlink():
        raise ValueError("invalid_shop_image")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if temp:
            temp.unlink(missing_ok=True)
    entry["image"] = f"assets/shop/{name}"


def image_signature(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else ""
    except OSError:
        return ""


def paste_artwork(canvas, path, box):
    """Unreadable artwork must not break a shelf or hide its text."""
    try:
        with Image.open(path) as source:
            image = source.convert("RGBA")
            image.thumbnail((box[2], box[3]), Image.Resampling.LANCZOS)
            canvas.paste(image, (box[0] + (box[2] - image.width) // 2,
                                 box[1] + (box[3] - image.height) // 2), image)
            image.close()
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
