from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityProtocolError,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
)


_CAPABILITY_ID = "asr.transcribe"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class OpenAICompatASRAdapter:
    type = "openai_compat_asr"

    def __init__(
        self,
        *,
        provider_id: str,
        endpoint: str = "",
        session: Any | None = None,
        client: Any | None = None,
        timeout_seconds: float = 45.0,
        model: str = "whisper-1",
        display_name: str = "Speech to text",
    ) -> None:
        self.provider_id = _safe_public_token(provider_id) or "provider.asr.openai_compat.local"
        self.endpoint = _normalize_loopback_endpoint(endpoint).rstrip("/") if endpoint else ""
        self.session = session or requests.Session()
        self.client = client
        self.timeout_seconds = max(1.0, float(timeout_seconds or 45.0))
        self.model = _safe_public_token(model) or "whisper-1"
        self.display_name = str(display_name or "Speech to text").strip()[:80] or "Speech to text"

    async def health(self) -> HealthStatus:
        if self.client is not None and callable(getattr(self.client, "transcribe", None)):
            return HealthStatus(ok=True, status="ready")
        if not self.endpoint:
            return HealthStatus(ok=False, status="missing_config", reason="asr_endpoint_missing")
        return HealthStatus(ok=True, status="configured")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (
            CapabilityDescriptor(
                id=_CAPABILITY_ID,
                display_name=self.display_name,
                short_hint="Transcribe voice input into text.",
                visible_in=("desktop", "web"),
                prompt_exposed=False,
                risk="medium",
                confirm="never",
                effects=(),
                trigger=None,
                inputs=(
                    CapabilityIOSlot(name="audio", kind="audio_bytes", required=True),
                    CapabilityIOSlot(name="language", kind="string", required=False),
                ),
                outputs=(CapabilityIOSlot(name="text", kind="string", delivery="inline_text"),),
                raw={"providerId": self.provider_id},
            ),
        )

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        if str(capability_id or "").strip() != _CAPABILITY_ID:
            raise CapabilityProtocolError("unknown_capability")
        audio = args.get("audio") or args.get("audio_bytes")
        if isinstance(audio, bytearray):
            audio = bytes(audio)
        if not isinstance(audio, bytes) or not audio:
            raise CapabilityProtocolError("audio_required")
        filename = _safe_filename(args.get("filename")) or "akane_voice_input.webm"
        content_type = _safe_content_type(args.get("content_type")) or "application/octet-stream"
        language = _safe_public_token(args.get("language"))
        try:
            if self.client is not None and callable(getattr(self.client, "transcribe", None)):
                raw_result = self.client.transcribe(
                    audio=audio,
                    filename=filename,
                    content_type=content_type,
                    language=language,
                )
                if inspect.isawaitable(raw_result):
                    raw_result = await raw_result
            else:
                raw_result = await asyncio.to_thread(
                    self._transcribe_sync,
                    audio,
                    filename,
                    content_type,
                    language,
                )
        except CapabilityProtocolError:
            raise
        except Exception as exc:
            raise CapabilityProtocolError("asr_provider_failed") from exc

        result = _normalize_transcription_result(raw_result)
        if not result.get("text"):
            return CapabilityResult(is_error=True, status="no_speech", reason="asr_returned_empty_text", content=result)
        return CapabilityResult(is_error=False, status="ok", content=result)

    async def aclose(self) -> None:
        return None

    def _transcribe_sync(
        self,
        audio: bytes,
        filename: str,
        content_type: str,
        language: str,
    ) -> Mapping[str, Any]:
        if not self.endpoint:
            raise CapabilityProtocolError("asr_endpoint_missing")
        data: dict[str, Any] = {"model": self.model}
        if language:
            data["language"] = language
        response = self.session.post(
            f"{self.endpoint}/v1/audio/transcriptions",
            data=data,
            files={"file": (filename, audio, content_type)},
            timeout=self.timeout_seconds,
        )
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise CapabilityProtocolError(f"asr_http_{response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise CapabilityProtocolError("asr_returned_invalid_json") from exc
        if not isinstance(payload, Mapping):
            raise CapabilityProtocolError("asr_returned_invalid_json")
        return payload


def _normalize_transcription_result(raw_result: Any) -> dict[str, Any]:
    if isinstance(raw_result, str):
        text = raw_result
        payload: Mapping[str, Any] = {}
    else:
        payload = raw_result if isinstance(raw_result, Mapping) else {}
        text = str(payload.get("text") or "").strip()
    text = " ".join(str(text or "").split()).strip()
    result: dict[str, Any] = {"text": text}
    language = _safe_public_token(payload.get("language"))
    if language:
        result["language"] = language
    duration = payload.get("duration_seconds") or payload.get("duration")
    if isinstance(duration, (int, float)) and duration >= 0:
        result["duration_seconds"] = duration
    return result


def _normalize_loopback_endpoint(endpoint: str) -> str:
    raw = str(endpoint or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("endpoint_must_be_http_localhost")
    if parsed.username or parsed.password:
        raise ValueError("endpoint_credentials_not_allowed")
    host = (parsed.hostname or "").strip().lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("endpoint_must_be_loopback")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid_endpoint_port") from exc
    netloc_host = "127.0.0.1" if host in {"127.0.0.1", "localhost"} else "[::1]"
    netloc = f"{netloc_host}:{port}" if port else netloc_host
    return f"{parsed.scheme}://{netloc}"


def _safe_public_token(value: Any) -> str:
    text = str(value or "").strip()
    return text if _TOKEN_RE.fullmatch(text) else ""


def _safe_filename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not text or text in {".", ".."} or any(ch in text for ch in "\r\n"):
        return ""
    return text[:120]


def _safe_content_type(value: Any) -> str:
    text = str(value or "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", text):
        return ""
    return text[:80]
