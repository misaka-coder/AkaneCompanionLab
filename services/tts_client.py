from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

import edge_tts
import requests


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;…])")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _normalize_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized.strip()


def _split_text(text: str, *, max_chars: int = 280) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    pieces = [part.strip() for part in _SENTENCE_SPLIT_RE.split(normalized) if part.strip()]
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        if not current:
            current = piece
            continue
        if len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
            continue
        chunks.append(current)
        current = piece

    if current:
        chunks.append(current)

    flattened: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            flattened.append(chunk)
            continue
        for start in range(0, len(chunk), max_chars):
            flattened.append(chunk[start : start + max_chars].strip())

    return [chunk for chunk in flattened if chunk]


@dataclass(frozen=True)
class SynthesizedAudio:
    audio: bytes
    media_type: str = "audio/mpeg"


class GptSovitsTTSClientError(RuntimeError):
    pass


class EdgeTTSClient:
    def __init__(
        self,
        *,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+4Hz",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.voice = str(voice or "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural"
        self.rate = str(rate or "+0%").strip() or "+0%"
        self.volume = str(volume or "+0%").strip() or "+0%"
        self.pitch = str(pitch or "+4Hz").strip() or "+4Hz"
        self.timeout_seconds = float(timeout_seconds or 30.0)

    async def synthesize(self, text: str) -> bytes:
        chunks = _split_text(text)
        if not chunks:
            raise ValueError("text is empty")

        audio_parts: list[bytes] = []
        for chunk in chunks:
            audio_parts.append(await asyncio.wait_for(self._synthesize_chunk(chunk), timeout=self.timeout_seconds))

        audio = b"".join(audio_parts)
        if not audio:
            raise RuntimeError("edge-tts returned empty audio")
        return audio

    async def _synthesize_chunk(self, text: str) -> bytes:
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
        )
        audio_parts: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                audio_parts.append(bytes(chunk["data"]))
        return b"".join(audio_parts)


class GptSovitsTTSClient:
    """Loopback-only client for a GPT-SoVITS-compatible HTTP API.

    Akane does not manage model paths in this client. The external GPT-SoVITS
    service is expected to have its model/profile selected already, or to use
    the optional ``voice_profile_id`` metadata if it supports profiles.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        session: Any | None = None,
        timeout_seconds: float = 45.0,
        text_lang: str = "zh",
        media_type: str = "wav",
    ) -> None:
        self.endpoint = _normalize_loopback_endpoint(endpoint).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = max(1.0, float(timeout_seconds or 45.0))
        self.text_lang = _safe_short_token(text_lang, default="zh")
        self.media_type = _safe_short_token(media_type, default="wav")

    async def synthesize(
        self,
        text: str,
        *,
        voice_profile_id: str = "",
        profile: Mapping[str, Any] | None = None,
    ) -> SynthesizedAudio:
        normalized = _normalize_text(text)
        if not normalized:
            raise ValueError("text is empty")
        request_profile = _safe_gpt_sovits_profile(profile)
        return await asyncio.to_thread(
            self._synthesize_sync,
            normalized,
            _safe_short_token(voice_profile_id, default=""),
            request_profile,
        )

    def _synthesize_sync(
        self,
        text: str,
        voice_profile_id: str,
        profile: Mapping[str, Any],
    ) -> SynthesizedAudio:
        payload: dict[str, Any] = {
            "text": text,
            "text_lang": profile.get("textLang") or self.text_lang,
            "media_type": profile.get("mediaType") or self.media_type,
            "streaming_mode": False,
        }
        if voice_profile_id:
            payload["voice_profile_id"] = voice_profile_id
        if profile.get("promptLang"):
            payload["prompt_lang"] = profile["promptLang"]
        if profile.get("promptText"):
            payload["prompt_text"] = profile["promptText"]
        if profile.get("refAudioPath"):
            payload["ref_audio_path"] = profile["refAudioPath"]

        try:
            response = self.session.post(
                f"{self.endpoint}/tts",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GptSovitsTTSClientError("gpt_sovits_request_failed") from exc

        if response.status_code >= 400:
            raise GptSovitsTTSClientError(f"gpt_sovits_http_{response.status_code}")

        content = bytes(response.content or b"")
        if not content:
            raise GptSovitsTTSClientError("gpt_sovits_empty_audio")

        response_media_type = _safe_media_type(response.headers.get("content-type"))
        if response_media_type == "application/json" or content[:1] in {b"{", b"["}:
            raise GptSovitsTTSClientError("gpt_sovits_returned_json_error")
        if not response_media_type.startswith("audio/"):
            response_media_type = "audio/wav"
        return SynthesizedAudio(audio=content, media_type=response_media_type)


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
    return urlunparse((parsed.scheme, netloc, "", "", "", ""))


def _safe_short_token(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text):
        return default
    return text


def _safe_media_type(value: Any) -> str:
    media_type = str(value or "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type):
        return ""
    return media_type[:80]


def _safe_gpt_sovits_profile(profile: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(profile, Mapping):
        return {}
    result: dict[str, str] = {}
    text_lang = _safe_short_token(profile.get("textLang") or profile.get("text_lang"), default="")
    if text_lang:
        result["textLang"] = text_lang
    prompt_lang = _safe_short_token(profile.get("promptLang") or profile.get("prompt_lang"), default="")
    if prompt_lang:
        result["promptLang"] = prompt_lang
    media_type = _safe_short_token(profile.get("mediaType") or profile.get("media_type"), default="")
    if media_type:
        result["mediaType"] = media_type
    prompt_text = _safe_prompt_text(profile.get("promptText") or profile.get("prompt_text"))
    if prompt_text:
        result["promptText"] = prompt_text
    ref_audio_path = _safe_local_path(profile.get("refAudioPath") or profile.get("ref_audio_path"))
    if ref_audio_path:
        result["refAudioPath"] = ref_audio_path
    return result


def _safe_prompt_text(value: Any) -> str:
    text = _normalize_text(str(value or ""))
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:300]


def _safe_local_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    return text[:500]
