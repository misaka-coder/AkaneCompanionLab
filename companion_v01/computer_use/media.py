"""Ephemeral images for the current model continuation, never generated files."""
import base64
import io
import re
from PIL import Image


def sanitize_device_result(value):
    """Preserve this contract's bounded trees and private pixels through Satellite.

    The generic device summary cleaner intentionally truncates strings and
    strips 'authorization'; neither behavior is valid for this wire contract.
    Unknown top-level fields are never projected.
    """
    if not isinstance(value, dict):
        return {}
    allowed = {"ok", "action_state", "observation_state", "reason", "next_action", "recovery_hint", "retry_after_ms", "input_enabled", "operation_id", "device_epoch",
               "context_ref", "control_session_id", "window_id", "observation_id", "captured_at", "foreground_matches",
               "window_bounds", "client_bounds", "dpi", "coordinate_space", "coordinate_bounds", "ax_status", "ax_reason", "ax_references",
               "focus_evidence", "text_complete", "elements", "screenshots", "control_status", "status",
               "windows", "total", "next_offset", "effect_evidence", "process_started", "pid", "authorization",
               "workflow_id", "workflow_state", "workflow_kind", "task_verified", "observation_reason", "next_step", "completed_steps", "steps", "message_target", "permission_mode", "occluder", "window_state",
               "browser_session_id", "backend", "tabs", "tab_id", "url", "title", "capabilities", "complete", "text", "owner_window_id"}
    def bounded(item, depth=0):
        if depth > 7:
            return None
        if isinstance(item, dict):
            return {str(k): bounded(v, depth + 1) for k, v in list(item.items())[:64]
                    if str(k) not in {"imageBase64", "token", "secret", "cookie", "cookies"}}
        if isinstance(item, list):
            return [bounded(v, depth + 1) for v in item[:128]]
        if isinstance(item, str):
            return item[:8000]
        return item if type(item) in (bool, int, float, type(None)) else None
    result = {k: bounded(v) for k, v in value.items() if k in allowed}
    shots = result.get("screenshots")
    if isinstance(shots, list):
        for clean, raw in zip(shots[:1], value.get("screenshots", [])[:1]):
            if isinstance(clean, dict) and isinstance(raw, dict):
                encoded = raw.get("imageBase64")
                if isinstance(encoded, str) and len(encoded) <= 12 * 1024 * 1024:
                    clean["imageBase64"] = encoded
    if "authorization" in result:
        auth = result["authorization"]
        if not isinstance(auth, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(auth.get("binding", ""))) or type(auth.get("required")) is not bool:
            result.pop("authorization")
    return result


def extract_images(data):
    images = []
    screenshots = data.get("screenshots", [])
    if not isinstance(screenshots, list):
        raise ValueError("invalid_screenshots")
    # Strip every byte payload before anything reaches history, including on failure.
    payloads = [(item, item.pop("imageBase64", None)) for item in screenshots if isinstance(item, dict)]
    for item, encoded in payloads[:1]:
        if encoded is None:
            continue
        if not isinstance(encoded, str) or len(encoded) > 12 * 1024 * 1024:
            raise ValueError("invalid_screenshot")
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 8 * 1024 * 1024:
            raise ValueError("invalid_screenshot")
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.format != "PNG" or picture.width * picture.height > 16_777_216:
                raise ValueError("invalid_screenshot")
            if (picture.width, picture.height) != (item.get("width"), item.get("height")):
                raise ValueError("invalid_screenshot_dimensions")
            picture.verify()
        images.append({"data_url": "data:image/png;base64," + encoded, "media_type": "image/png", "mime_type": "image/png",
                       "attachment_id": data.get("observation_id", ""),
                       "attachment_handle": item.get("screenshot_id", ""), "title": "desktop-window", "ephemeral_observation":True})
    data["model_image_count"] = len(images)
    return images
