from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .image_materials import ResolvedImageMaterial, SessionImageMaterialResolver


logger = logging.getLogger("akane.image_generation")


IMAGE_SIZE_RE = re.compile(r"^(\d{3,4})x(\d{3,4})$")
IMAGE_OUTPUT_FORMATS = frozenset({"png", "jpeg", "webp"})
IMAGE_QUALITY_VALUES = frozenset({"auto", "low", "medium", "high"})
IMAGE_BACKGROUND_VALUES = frozenset({"auto", "opaque"})
IMAGE_INPUT_FIDELITY_VALUES = frozenset({"auto", "low", "high"})


class ImageGenerationError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(str(code or "image_generation_failed"))
        self.code = str(code or "image_generation_failed")
        self.retryable = bool(retryable)


@dataclass(frozen=True)
class GeneratedImageBytes:
    data: bytes
    output_format: str
    media_type: str


class PinAIImageProvider:
    """Bounded OpenAI-compatible GPT Image client for PinAI."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "gpt-image-2",
        timeout_seconds: float = 300.0,
        max_output_bytes: int = 25 * 1024 * 1024,
        session: Any = None,
        readiness_ready_ttl_seconds: float = 30 * 60,
        readiness_failure_ttl_seconds: float = 5 * 60,
        readiness_probe_in_background: bool = True,
        readiness_clock=time.monotonic,
        transient_retry_count: int = 2,
        retry_sleep=time.sleep,
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "gpt-image-2").strip() or "gpt-image-2"
        self.timeout_seconds = max(30.0, min(600.0, float(timeout_seconds or 300.0)))
        self.max_output_bytes = max(512 * 1024, int(max_output_bytes or 0))
        self.session = session or requests.Session()
        self._readiness_ready_ttl_seconds = max(30.0, float(readiness_ready_ttl_seconds))
        self._readiness_failure_ttl_seconds = max(30.0, float(readiness_failure_ttl_seconds))
        self._readiness_probe_in_background = bool(readiness_probe_in_background)
        self._readiness_clock = readiness_clock
        self._transient_retry_count = max(0, min(3, int(transient_retry_count or 0)))
        self._retry_sleep = retry_sleep
        self._readiness_cache: tuple[float, dict[str, Any]] | None = None
        self._readiness_probe_inflight = False
        self._readiness_lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def capability_status(self) -> dict[str, Any]:
        if not self.configured:
            return {"enabled": False, "status": "missing_config", "reason": "image_provider_not_configured"}
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached_status = None
            if self._readiness_cache is not None:
                cached_status = dict(self._readiness_cache[1])
                if self._readiness_cache[0] > now:
                    return cached_status
            if self._readiness_probe_in_background:
                if not self._readiness_probe_inflight:
                    self._readiness_probe_inflight = True
                    threading.Thread(
                        target=self._probe_capability_background,
                        name="akane-image-generation-readiness",
                        daemon=True,
                    ).start()
                if cached_status is not None:
                    return {
                        **cached_status,
                        "refreshing": True,
                    }
                return {
                    "enabled": True,
                    "status": "degraded",
                    "reason": "image_provider_probe_pending",
                    "refreshing": True,
                    "cache_ttl_seconds": 1.0,
                }
        return self._probe_capability()

    def _probe_capability(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=(5.0, min(15.0, self.timeout_seconds)),
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            error_text = self._response_error_text(response)
            if "images api is not supported" in error_text:
                status = {
                    "enabled": False,
                    "status": "unsupported",
                    "reason": "image_key_not_bound_to_openai_platform",
                }
            elif status_code == 200:
                model_ids = self._response_model_ids(response)
                status = (
                    {"enabled": True, "status": "ready", "reason": ""}
                    if self.model in model_ids
                    else {
                        "enabled": False,
                        "status": "unsupported",
                        "reason": "image_model_not_available_for_key",
                    }
                )
            elif status_code in {401, 403}:
                status = {
                    "enabled": False,
                    "status": "permission_denied",
                    "reason": "image_provider_auth_rejected",
                }
            elif status_code == 429:
                status = {
                    "enabled": False,
                    "status": "rate_limited",
                    "reason": "image_provider_rate_limited",
                }
            elif status_code == 404:
                status = {
                    "enabled": False,
                    "status": "unsupported",
                    "reason": "image_model_or_endpoint_not_found",
                }
            else:
                status = {
                    "enabled": False,
                    "status": "unavailable",
                    "reason": "image_provider_probe_failed",
                }
        except (requests.Timeout, TimeoutError):
            status = {"enabled": False, "status": "unavailable", "reason": "image_provider_probe_timeout"}
        except requests.RequestException:
            status = {"enabled": False, "status": "unavailable", "reason": "image_provider_transport_error"}
        except Exception as exc:
            status = {
                "enabled": False,
                "status": "unavailable",
                "reason": f"image_provider_probe_failed:{type(exc).__name__}",
            }
        self._remember_capability_status(status)
        return dict(status)

    def _probe_capability_background(self) -> None:
        try:
            self._probe_capability()
        finally:
            with self._readiness_lock:
                self._readiness_probe_inflight = False

    def _remember_capability_status(self, status: dict[str, Any]) -> None:
        enabled = bool(status.get("enabled"))
        ttl = self._readiness_ready_ttl_seconds if enabled else self._readiness_failure_ttl_seconds
        normalized = {**status, "cache_ttl_seconds": min(300.0, ttl)}
        with self._readiness_lock:
            self._readiness_cache = (float(self._readiness_clock()) + ttl, normalized)

    def generate(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
        output_format: str,
        compression: int,
        n: int,
        references: list[ResolvedImageMaterial] | None = None,
        mask: ResolvedImageMaterial | None = None,
        input_fidelity: str = "auto",
    ) -> list[GeneratedImageBytes]:
        if not self.configured:
            raise ImageGenerationError("provider_not_configured")
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ImageGenerationError("prompt_required")
        request_fields = self._request_fields(
            prompt=normalized_prompt,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            compression=compression,
            n=n,
            input_fidelity=input_fidelity,
        )
        reference_items = list(references or [])
        try:
            if reference_items or mask is not None:
                outputs = self._edit(request_fields=request_fields, references=reference_items, mask=mask, n=n)
            else:
                outputs = self._generation(request_fields=request_fields, n=n)
        except ImageGenerationError as exc:
            failure_status = self._capability_status_for_generation_error(exc)
            if failure_status is not None:
                self._remember_capability_status(failure_status)
            raise
        self._remember_capability_status({"enabled": True, "status": "ready", "reason": ""})
        return outputs

    def _generation(self, *, request_fields: dict[str, Any], n: int) -> list[GeneratedImageBytes]:
        response = self._post(
            f"{self.base_url}/images/generations",
            json_body=request_fields,
        )
        return self._decode_response_images(response, requested_count=n)

    def _edit(
        self,
        *,
        request_fields: dict[str, Any],
        references: list[ResolvedImageMaterial],
        mask: ResolvedImageMaterial | None,
        n: int,
    ) -> list[GeneratedImageBytes]:
        if not references:
            raise ImageGenerationError("reference_image_required_for_mask")
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        image_field = "image" if len(references) == 1 else "image[]"
        for index, material in enumerate(references, start=1):
            try:
                data = material.path.read_bytes()
            except OSError as exc:
                raise ImageGenerationError("reference_image_unreadable") from exc
            files.append(
                (
                    image_field,
                    (self._safe_upload_name(material, index=index), data, material.media_type),
                )
            )
        if mask is not None:
            try:
                mask_bytes = mask.path.read_bytes()
            except OSError as exc:
                raise ImageGenerationError("mask_image_unreadable") from exc
            files.append(("mask", (self._safe_upload_name(mask, index=0), mask_bytes, mask.media_type)))
        try:
            response = self._post(
                f"{self.base_url}/images/edits",
                form_body={key: self._form_value(value) for key, value in request_fields.items()},
                files=files,
            )
        except ImageGenerationError as exc:
            if len(references) <= 1 or exc.code != "provider_rejected_request":
                raise
            response = self._edit_with_uploaded_file_ids(
                request_fields=request_fields,
                references=references,
                mask=mask,
            )
        return self._decode_response_images(response, requested_count=n)

    def _edit_with_uploaded_file_ids(
        self,
        *,
        request_fields: dict[str, Any],
        references: list[ResolvedImageMaterial],
        mask: ResolvedImageMaterial | None,
    ) -> Any:
        image_file_ids = [
            self._upload_material_file(material, index=index) for index, material in enumerate(references)
        ]
        payload = {
            **request_fields,
            "images": [{"file_id": file_id} for file_id in image_file_ids],
        }
        if mask is not None:
            payload["mask"] = {"file_id": self._upload_material_file(mask, index=len(references))}
        return self._post(f"{self.base_url}/images/edits", json_body=payload)

    def _upload_material_file(self, material: ResolvedImageMaterial, *, index: int) -> str:
        try:
            data = material.path.read_bytes()
        except OSError as exc:
            raise ImageGenerationError("reference_image_unreadable") from exc
        response = self._post(
            f"{self.base_url}/files",
            form_body={"purpose": "user_data"},
            files=[("file", (self._safe_upload_name(material, index=index + 1), data, material.media_type))],
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise ImageGenerationError("provider_file_upload_invalid_response") from exc
        file_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
        if not file_id:
            raise ImageGenerationError("provider_file_upload_missing_id")
        return file_id

    def _post(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, str] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        response = None
        status_code = 0
        for attempt in range(self._transient_retry_count + 1):
            try:
                response = self.session.post(
                    url,
                    headers=headers,
                    json=json_body,
                    data=form_body,
                    files=files,
                    stream=True,
                    timeout=(10.0, self.timeout_seconds),
                )
            except (requests.Timeout, TimeoutError) as exc:
                raise ImageGenerationError("provider_timeout", retryable=True) from exc
            except requests.RequestException as exc:
                raise ImageGenerationError("provider_transport_error", retryable=True) from exc
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code not in {502, 503, 504} or attempt >= self._transient_retry_count:
                break
            try:
                response.close()
            except Exception:
                pass
            self._retry_sleep(float(attempt + 1))
        if response is None:
            raise ImageGenerationError("provider_transport_error", retryable=True)
        if status_code >= 400:
            error_text = self._response_error_text(response)
            raise ImageGenerationError(
                "provider_images_api_unsupported"
                if "images api is not supported" in error_text
                else "provider_files_api_unsupported"
                if "files api is not supported" in error_text
                else "provider_auth_or_network_forbidden"
                if status_code in {401, 403}
                else "provider_rate_limited"
                if status_code == 429
                else "provider_no_compatible_accounts"
                if status_code == 503 and "no available compatible accounts" in error_text
                else "provider_unavailable"
                if status_code >= 500
                else "provider_rejected_request",
                retryable=status_code == 429 or status_code >= 500,
            )
        return response

    @staticmethod
    def _capability_status_for_generation_error(exc: ImageGenerationError) -> dict[str, Any] | None:
        code = str(exc.code or "")
        if code == "provider_images_api_unsupported":
            return {"enabled": False, "status": "unsupported", "reason": code}
        if code == "provider_auth_or_network_forbidden":
            return {"enabled": False, "status": "permission_denied", "reason": code}
        if code == "provider_rate_limited":
            return {"enabled": True, "status": "degraded", "reason": code}
        if code in {
            "provider_no_compatible_accounts",
            "provider_unavailable",
            "provider_timeout",
            "provider_transport_error",
            "provider_returned_no_image",
            "provider_stream_unreadable",
        }:
            return {"enabled": True, "status": "degraded", "reason": code}
        return None

    @staticmethod
    def _response_error_text(response: Any) -> str:
        try:
            payload = response.json()
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        values = (
            error.get("type"),
            error.get("code"),
            error.get("message"),
            payload.get("message"),
        )
        return " ".join(str(value or "").strip().lower() for value in values if str(value or "").strip())[:500]

    @staticmethod
    def _response_model_ids(response: Any) -> set[str]:
        try:
            payload = response.json()
        except Exception:
            return set()
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return set()
        return {
            str(item.get("id") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

    def _decode_response_images(self, response: Any, *, requested_count: int) -> list[GeneratedImageBytes]:
        content_type = str(getattr(response, "headers", {}).get("Content-Type") or "").lower()
        payloads: list[Any] = []
        try:
            if "text/event-stream" in content_type:
                payloads.extend(
                    self._iter_sse_payloads(
                        response,
                        requested_count=requested_count,
                    )
                )
            else:
                try:
                    payloads.append(response.json())
                except Exception:
                    payloads.extend(
                        self._iter_sse_payloads(
                            response,
                            requested_count=requested_count,
                        )
                    )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        encoded = self._select_final_base64_images(payloads, requested_count=requested_count)
        if not encoded:
            raise ImageGenerationError("provider_returned_no_image")
        decoded: list[GeneratedImageBytes] = []
        for value in encoded:
            try:
                image_bytes = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ImageGenerationError("provider_returned_invalid_base64") from exc
            if not image_bytes or len(image_bytes) > self.max_output_bytes:
                raise ImageGenerationError("provider_output_size_limit")
            detected_format, media_type = self._detect_image_format(image_bytes)
            decoded.append(GeneratedImageBytes(data=image_bytes, output_format=detected_format, media_type=media_type))
        return decoded

    def _iter_sse_payloads(
        self,
        response: Any,
        *,
        requested_count: int = 1,
    ) -> list[Any]:
        payloads: list[Any] = []
        completed_payloads: list[Any] = []
        try:
            lines = response.iter_lines(decode_unicode=True)
        except Exception as exc:
            raise ImageGenerationError("provider_stream_unreadable", retryable=True) from exc
        for raw_line in lines:
            line = str(raw_line or "").strip()
            if not line or line.startswith(":") or line.startswith("event:") or line.startswith("id:"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            payloads.append(payload)
            if not self._payload_marks_image_completion(payload):
                continue
            completed_payloads.append(payload)
            completed_images = self._select_final_base64_images(
                completed_payloads,
                requested_count=max(1, int(requested_count or 1)),
            )
            if len(completed_images) >= max(1, int(requested_count or 1)):
                # Some compatible relays keep an already-completed SSE
                # connection alive for minutes. Once the requested final
                # images are present, waiting for a later [DONE] adds latency
                # without adding evidence.
                break
        return payloads

    @staticmethod
    def _payload_marks_image_completion(value: Any) -> bool:
        if isinstance(value, dict):
            event_type = str(value.get("type") or "").strip().lower()
            if event_type == "completed" or event_type.endswith(".completed"):
                return True
            return any(PinAIImageProvider._payload_marks_image_completion(item) for item in value.values())
        if isinstance(value, list):
            return any(PinAIImageProvider._payload_marks_image_completion(item) for item in value)
        return False

    def _select_final_base64_images(self, payloads: list[Any], *, requested_count: int) -> list[str]:
        indexed: dict[int, str] = {}
        unindexed: list[str] = []

        def walk(value: Any, inherited_index: int | None = None) -> None:
            if isinstance(value, dict):
                raw_index = value.get("output_index", value.get("index", inherited_index))
                try:
                    index = int(raw_index) if raw_index is not None else inherited_index
                except (TypeError, ValueError):
                    index = inherited_index
                encoded = value.get("b64_json")
                if isinstance(encoded, str) and encoded.strip():
                    if index is None:
                        unindexed.append(encoded.strip())
                    else:
                        indexed[index] = encoded.strip()
                for item in value.values():
                    walk(item, index)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, inherited_index if inherited_index is not None else index)

        for payload in payloads:
            walk(payload)
        candidates = [indexed[key] for key in sorted(indexed)] if indexed else unindexed[-requested_count:]
        deduped: list[str] = []
        seen: set[str] = set()
        for value in candidates:
            fingerprint = hashlib.sha256(value.encode("ascii", errors="ignore")).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            deduped.append(value)
        return deduped[:requested_count]

    def _request_fields(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
        output_format: str,
        compression: int,
        n: int,
        input_fidelity: str,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self._normalize_size(size),
            "quality": quality if quality in IMAGE_QUALITY_VALUES else "auto",
            "background": background if background in IMAGE_BACKGROUND_VALUES else "auto",
            "output_format": output_format if output_format in IMAGE_OUTPUT_FORMATS else "png",
            "response_format": "b64_json",
            "n": max(1, int(n or 1)),
            "stream": True,
        }
        if fields["output_format"] in {"jpeg", "webp"}:
            fields["output_compression"] = max(0, min(100, int(compression or 90)))
        if input_fidelity in {"low", "high"}:
            fields["input_fidelity"] = input_fidelity
        return fields

    @staticmethod
    def _normalize_size(value: str) -> str:
        normalized = str(value or "auto").strip().lower().replace("×", "x") or "auto"
        if normalized == "auto":
            return normalized
        match = IMAGE_SIZE_RE.fullmatch(normalized)
        if not match:
            raise ImageGenerationError("invalid_image_size")
        width, height = int(match.group(1)), int(match.group(2))
        if width < 512 or height < 512 or width > 2048 or height > 2048 or width * height > 4_194_304:
            raise ImageGenerationError("invalid_image_size")
        return f"{width}x{height}"

    @staticmethod
    def _detect_image_format(data: bytes) -> tuple[str, str]:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png", "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "jpeg", "image/jpeg"
        if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "webp", "image/webp"
        raise ImageGenerationError("provider_returned_unsupported_image")

    @staticmethod
    def _safe_upload_name(material: ResolvedImageMaterial, *, index: int) -> str:
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(
            material.media_type,
            ".img",
        )
        return f"reference_{max(0, int(index))}{suffix}"

    @staticmethod
    def _form_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        normalized = str(value or "").strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        if parsed.query or parsed.fragment:
            return ""
        return normalized


class ImageGenerationService:
    def __init__(
        self,
        *,
        provider: PinAIImageProvider,
        image_material_resolver: SessionImageMaterialResolver,
        generated_file_service: Any,
        max_input_images: int = 5,
        max_output_images: int = 4,
        max_image_bytes: int = 8 * 1024 * 1024,
        max_total_input_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.provider = provider
        self.image_material_resolver = image_material_resolver
        self.generated_file_service = generated_file_service
        self.max_input_images = max(1, min(5, int(max_input_images or 5)))
        self.max_output_images = max(1, min(4, int(max_output_images or 4)))
        self.max_image_bytes = max(128 * 1024, int(max_image_bytes or 0))
        self.max_total_input_bytes = max(self.max_image_bytes, int(max_total_input_bytes or 0))

    def capability_status(self) -> dict[str, Any]:
        status_fn = getattr(self.provider, "capability_status", None)
        if not callable(status_fn):
            return {"enabled": False, "status": "unavailable", "reason": "image_provider_status_missing"}
        return dict(status_fn() or {})

    def generate(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        prompt: str,
        reference_targets: list[str],
        mask_target: str,
        size: str,
        quality: str,
        background: str,
        output_format: str,
        compression: int,
        input_fidelity: str,
        n: int,
        output_title: str,
        send_to_user: bool,
        timestamp: int,
    ) -> dict[str, Any]:
        if not self.provider.configured:
            return self._failure("provider_not_configured")
        references_result = (
            self.image_material_resolver.resolve_many(
                profile_user_id=profile_user_id,
                session_id=session_id,
                targets=reference_targets,
                max_count=self.max_input_images,
                max_bytes_per_image=self.max_image_bytes,
                max_total_bytes=self.max_total_input_bytes,
            )
            if reference_targets
            else {"ok": True, "materials": [], "unresolved": []}
        )
        references = [
            item for item in list(references_result.get("materials") or []) if isinstance(item, ResolvedImageMaterial)
        ]
        if reference_targets and (not references or references_result.get("unresolved")):
            return self._failure("reference_image_unavailable")

        mask = None
        if mask_target:
            mask_result = self.image_material_resolver.resolve_many(
                profile_user_id=profile_user_id,
                session_id=session_id,
                targets=[mask_target],
                max_count=1,
                max_bytes_per_image=self.max_image_bytes,
                max_total_bytes=self.max_image_bytes,
            )
            mask_items = list(mask_result.get("materials") or [])
            mask = mask_items[0] if mask_items and isinstance(mask_items[0], ResolvedImageMaterial) else None
            if mask is None:
                return self._failure("mask_image_unavailable")

        requested_count = max(1, min(self.max_output_images, int(n or 1)))
        provider_started_at = time.monotonic()
        try:
            outputs = self.provider.generate(
                prompt=prompt,
                size=size,
                quality=quality,
                background=background,
                output_format=output_format,
                compression=compression,
                n=requested_count,
                references=references,
                mask=mask,
                input_fidelity=input_fidelity,
            )
        except ImageGenerationError as exc:
            return self._failure(exc.code, retryable=exc.retryable)
        provider_duration_ms = (time.monotonic() - provider_started_at) * 1000
        logger.info(
            "image_generation_provider_completed model=%s output_count=%s duration_ms=%.1f",
            self.provider.model,
            len(outputs),
            provider_duration_ms,
        )

        source_ids = [item.source_id for item in references]
        if mask is not None and mask.source_id not in source_ids:
            source_ids.append(mask.source_id)
        generated_items: list[dict[str, Any]] = []
        clean_title = str(output_title or "生成图片").strip()[:80] or "生成图片"
        for index, output in enumerate(outputs, start=1):
            item_title = clean_title if len(outputs) == 1 else f"{clean_title}_{index}"
            output_path = self.generated_file_service.allocate_output_path(
                profile_user_id=profile_user_id,
                session_id=session_id,
                title=item_title,
                output_format=output.output_format,
                timestamp=timestamp + index - 1,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
            try:
                temp_path.write_bytes(output.data)
                temp_path.replace(output_path)
                generated = self.generated_file_service.register_generated_artifact(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    output_path=output_path,
                    output_title=item_title,
                    output_format=output.output_format,
                    mime_type=output.media_type,
                    content_card={
                        "kind": "generated_image",
                        "provider": "pinai",
                        "model": self.provider.model,
                        "prompt": str(prompt or "").strip()[:2000],
                        "reference_handles": [item.handle for item in references],
                        "mask_handle": mask.handle if mask is not None else "",
                        "size": size,
                        "quality": quality,
                    },
                    summary=f"由 {self.provider.model} 生成的图片：{item_title}",
                    created_by_tool="generate_image",
                    source_ids=source_ids,
                    send_to_user=send_to_user,
                    timestamp=timestamp + index - 1,
                )
            except Exception:
                for path in (temp_path, output_path):
                    try:
                        if path.exists():
                            path.unlink()
                    except OSError:
                        pass
                return self._failure("generated_file_registration_failed")
            generated_items.append(generated)

        handles = [
            str(item.get("generated_handle") or item.get("generated_id") or "").strip() for item in generated_items
        ]
        logger.info(
            "image_generation_artifacts_ready model=%s output_count=%s provider_duration_ms=%.1f "
            "total_duration_ms=%.1f",
            self.provider.model,
            len(generated_items),
            provider_duration_ms,
            (time.monotonic() - provider_started_at) * 1000,
        )
        return {
            "ok": True,
            "status": "ready",
            "generated": generated_items,
            "handles": handles,
            "send_to_user": bool(send_to_user),
            "reference_handles": [item.handle for item in references],
            "followup_context": (
                f"图片生成完成，得到 {', '.join(handles)}。"
                f"{'已请求发送给用户。' if send_to_user else '结果保存在当前生成文件工作区。'}"
                "这些生成图可以继续作为 load_material 或 generate_image 的参考图；不要声称生成了未列出的结果。"
                + (
                    ""
                    if send_to_user
                    else (
                        f"如果接下来要交付本次结果，请调用 send_file 并传入刚得到的精确句柄"
                        f" {', '.join(handles)}；不要改用 latest，因为最近的用户附件可能比生成物更新。"
                    )
                )
            ),
        }

    @staticmethod
    def _failure(code: str, *, retryable: bool = False) -> dict[str, Any]:
        safe_code = str(code or "image_generation_failed")[:120]
        return {
            "ok": False,
            "status": "failed",
            "reason": safe_code,
            "retryable": bool(retryable),
            "generated": [],
            "followup_context": (
                f"<tool_use_error>图片生成没有完成：{safe_code}。"
                "请自然说明这次没有产生图片；只有 retryable=true 时才可以稍后重试，"
                "不要假装成功，也不要编造生成文件 handle。</tool_use_error>"
            ),
        }
