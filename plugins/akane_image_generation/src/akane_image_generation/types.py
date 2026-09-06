from __future__ import annotations

import io
import warnings
from dataclasses import dataclass, field

from PIL import Image


class ImageError(RuntimeError):
    def __init__(self, code, *, retryable=False, http_status=0, rejected_parameter=""):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.rejected_parameter = rejected_parameter
        self.notices = ()


@dataclass(frozen=True)
class ImageMaterial:
    data: bytes = field(repr=False)
    output_format: str
    media_type: str
    width: int
    height: int
    has_alpha: bool = False


@dataclass(frozen=True)
class ImageResult:
    images: tuple[ImageMaterial, ...]
    requested_count: int
    notices: tuple[str, ...] = ()


def inspect_image(data, *, reference=False, mask=False, max_bytes=25 * 1024 * 1024):
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise ImageError("image_size_limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                fmt = str(picture.format).lower()
                width, height = picture.size
                if width < 1 or height < 1 or width * height > 40_000_000:
                    raise ImageError("image_pixel_limit")
                if fmt not in ({"png", "jpeg", "webp", "gif"} if reference else {"png", "jpeg", "webp"}):
                    raise ImageError("unsupported_image_format")
                alpha = "A" in picture.getbands() or "transparency" in picture.info
                if mask and (fmt != "png" or not alpha):
                    raise ImageError("mask_requires_png_alpha")
                picture.verify()
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
    except ImageError:
        raise
    except Exception:
        raise ImageError("invalid_image_data") from None
    return ImageMaterial(data, fmt, f"image/{fmt}", width, height, alpha)


def runtime_health():
    """Actually exercise required codecs; do not send network probes."""
    try:
        for fmt in ("PNG", "JPEG", "WEBP"):
            stream = io.BytesIO()
            Image.new("RGB", (2, 2), "blue").save(stream, format=fmt)
            inspect_image(stream.getvalue())
    except Exception:
        return {"status": "unavailable", "reason": "image_codec_unavailable"}
    return {"status": "runtime_ready", "reason": "provider_checked_on_invocation"}
