"""Turn private device screenshot bytes into ordinary session-owned artifacts."""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path
import uuid

from PIL import Image


def register_desktop_screenshot(engine, data, *, profile_user_id: str, session_id: str):
    encoded = data.pop("imageBase64", None)
    if not isinstance(encoded, str) or len(encoded) > 12 * 1024 * 1024:
        raise ValueError("screenshot_image_invalid")
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > 8 * 1024 * 1024 or data.get("mimeType") != "image/png":
        raise ValueError("screenshot_image_invalid")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG" or image.width * image.height > 16_777_216:
            raise ValueError("screenshot_image_invalid")
        if (image.width, image.height) != (data.get("width"), data.get("height")):
            raise ValueError("screenshot_dimensions_invalid")
        image.verify()
    service = engine._get_generated_file_service()
    path = service.allocate_output_path(profile_user_id=profile_user_id, session_id=session_id,
                                        title="desktop-screenshot", output_format="png")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
        item = service.register_generated_artifact(profile_user_id=profile_user_id, session_id=session_id,
            output_path=path, output_title="desktop-screenshot", output_format="png", mime_type="image/png",
            content_card={"kind": "desktop_screenshot", "width": data["width"], "height": data["height"]},
            summary="绑定电脑主屏幕截图", created_by_tool="desktop_screenshot", send_to_user=False)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    public = {key: item[key] for key in ("generated_id", "generated_handle", "output_title", "output_format",
              "file_ext", "mime_type", "file_size") if item.get(key) not in (None, "")}
    images = []
    try:
        resolver = engine._get_image_material_resolver()
        if resolver:
            images = list(resolver.build_model_image_inputs(profile_user_id=profile_user_id,
                session_id=session_id, targets=[item["generated_handle"]], max_count=1).get("images") or [])
    except Exception:
        pass  # The artifact is still usable with inspect_image/send_file.
    data.update(public)
    return {"type": "generated_file_ready", "generated_file": public, "send_to_user": False}, images
