from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from ..local_capability_config import normalize_local_http_endpoint


COMFYUI_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
COMFYUI_IMAGE_TYPES = {"input", "output", "temp"}


class ComfyUiClientError(RuntimeError):
    """Raised when the ComfyUI HTTP boundary returns an unusable response."""


@dataclass(frozen=True)
class ComfyUiImageRef:
    filename: str
    subfolder: str = ""
    image_type: str = "output"


@dataclass(frozen=True)
class ComfyUiImageBytes:
    data: bytes
    content_type: str


class ComfyUiClient:
    """Tiny loopback-only client for the public ComfyUI HTTP API."""

    def __init__(
        self,
        endpoint: str,
        *,
        session: Any | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        normalized = normalize_local_http_endpoint(endpoint)
        if not normalized.get("ok"):
            raise ValueError(str(normalized.get("reason") or "invalid_comfyui_endpoint"))
        self.endpoint = str(normalized["endpoint"]).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = max(0.5, float(timeout_seconds or 30.0))

    def upload_image(
        self,
        image_bytes: bytes,
        *,
        filename: str,
        subfolder: str = "akane",
        image_type: str = "input",
        overwrite: bool = True,
        content_type: str | None = None,
    ) -> ComfyUiImageRef:
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("image_bytes_required")
        safe_filename = _safe_comfyui_value(filename, "filename")
        safe_subfolder = _safe_comfyui_value(subfolder, "subfolder") if subfolder else ""
        safe_type = _safe_comfyui_image_type(image_type)
        response = self.session.post(
            self._url("/upload/image"),
            data={
                "subfolder": safe_subfolder,
                "type": safe_type,
                "overwrite": "true" if overwrite else "false",
            },
            files={
                "image": (
                    safe_filename,
                    image_bytes,
                    content_type or _guess_image_content_type(safe_filename),
                )
            },
            timeout=self.timeout_seconds,
        )
        payload = _json_response(response, "upload_image")
        return ComfyUiImageRef(
            filename=_safe_response_value(payload.get("name"), safe_filename),
            subfolder=_safe_response_value(payload.get("subfolder"), safe_subfolder),
            image_type=_safe_comfyui_image_type(payload.get("type") or safe_type),
        )

    def queue_prompt(self, workflow: Mapping[str, Any], *, client_id: str = "") -> str:
        if not isinstance(workflow, Mapping) or not workflow:
            raise ValueError("workflow_prompt_required")
        payload: dict[str, Any] = {"prompt": dict(workflow)}
        if client_id:
            payload["client_id"] = _safe_comfyui_value(client_id, "client_id")
        response = self.session.post(
            self._url("/prompt"),
            json=payload,
            timeout=self.timeout_seconds,
        )
        data = _json_response(response, "queue_prompt")
        prompt_id = str(data.get("prompt_id") or "").strip()
        if not COMFYUI_SAFE_VALUE_RE.match(prompt_id):
            raise ComfyUiClientError("queue_prompt_missing_prompt_id")
        return prompt_id

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        safe_prompt_id = _safe_comfyui_value(prompt_id, "prompt_id")
        response = self.session.get(
            self._url(f"/history/{safe_prompt_id}"),
            timeout=self.timeout_seconds,
        )
        return dict(_json_response(response, "get_history"))

    def get_image(
        self,
        image: ComfyUiImageRef | str,
        *,
        subfolder: str = "",
        image_type: str = "output",
    ) -> ComfyUiImageBytes:
        if isinstance(image, ComfyUiImageRef):
            filename = image.filename
            subfolder = image.subfolder
            image_type = image.image_type
        else:
            filename = str(image or "")
        safe_filename = _safe_comfyui_value(filename, "filename")
        safe_subfolder = _safe_comfyui_value(subfolder, "subfolder") if subfolder else ""
        safe_type = _safe_comfyui_image_type(image_type)
        response = self.session.get(
            self._url("/view"),
            params={"filename": safe_filename, "subfolder": safe_subfolder, "type": safe_type},
            timeout=self.timeout_seconds,
        )
        _raise_for_status(response, "get_image")
        content = bytes(getattr(response, "content", b"") or b"")
        if not content:
            raise ComfyUiClientError("get_image_empty_response")
        headers = getattr(response, "headers", {}) or {}
        return ComfyUiImageBytes(
            data=content,
            content_type=str(headers.get("content-type") or headers.get("Content-Type") or "application/octet-stream"),
        )

    def _url(self, path: str) -> str:
        suffix = "/" + str(path or "").strip().lstrip("/")
        return f"{self.endpoint}{suffix}"


def _json_response(response: Any, action: str) -> Mapping[str, Any]:
    _raise_for_status(response, action)
    try:
        payload = response.json()
    except Exception as exc:
        raise ComfyUiClientError(f"{action}_invalid_json") from exc
    if not isinstance(payload, Mapping):
        raise ComfyUiClientError(f"{action}_invalid_json")
    return payload


def _raise_for_status(response: Any, action: str) -> None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code >= 400:
        raise ComfyUiClientError(f"{action}_http_{status_code}")


def _safe_comfyui_value(value: Any, field: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if (
        not COMFYUI_SAFE_VALUE_RE.match(text)
        or "://" in text
        or "/" in text
        or "\\" in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        raise ValueError(f"{field}_must_be_safe_opaque_id")
    return text


def _safe_response_value(value: Any, fallback: str) -> str:
    try:
        return _safe_comfyui_value(value, "response_value")
    except ValueError:
        return fallback


def _safe_comfyui_image_type(value: Any) -> str:
    image_type = str(value or "").strip().lower()
    if image_type not in COMFYUI_IMAGE_TYPES:
        raise ValueError("image_type_invalid")
    return image_type


def _guess_image_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/png"
