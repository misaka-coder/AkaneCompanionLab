"""Loopback Irodori API client for a registered Japanese speaker voice."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from urllib.parse import urlparse

MAX_AUDIO = 16 * 1024 * 1024
CONTINUOUS_MAX_CHARS = 120
CAPTION_MAX_CHARS = 80
CAPTION_CFG_SCALE = 2.0


class IrodoriClientError(ValueError):
    def __init__(self, reason, *, outcome_known=True):
        super().__init__(reason)
        self.outcome_known = outcome_known


class IrodoriTTSClient:
    def __init__(self, endpoint, *, timeout_seconds=90.0, session=None):
        parsed = urlparse(str(endpoint))
        if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise IrodoriClientError("tts_endpoint_invalid")
        self.endpoint = str(endpoint).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.session = session
        self.closed = False

    async def synthesize(self, text, *, profile, caption=""):
        if self.closed:
            raise IrodoriClientError("tts_client_closed")
        text = str(text or "").strip()
        if not text or len(text) > 4096:
            raise IrodoriClientError("tts_text_invalid")
        if not isinstance(caption, str) or len(caption) > CAPTION_MAX_CHARS:
            raise IrodoriClientError("tts_irodori_caption_invalid")
        caption = caption.strip()
        voice = str(profile.get("irodoriVoiceId") or "") if isinstance(profile, Mapping) else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", voice) or voice.lower() in {"none", "no_ref", "noref"}:
            raise IrodoriClientError("tts_irodori_voice_invalid")
        # Bounded host delivery chunks are generated intact. Only long direct
        # requests use the server's fallback grouping, never one sentence each.
        # Caption is bounded delivery text. Identity and model settings stay in
        # the registered voice; callers cannot inject paths or load adapters.
        body = {"model": "irodori-tts", "input": text, "voice": voice, "response_format": "wav",
                "irodori": {"num_steps": 32, "num_candidates": 1, "seed": 20261022,
                    "cfg_scale_text": 3.0, "cfg_scale_speaker": 5.0, "cfg_guidance_mode": "independent",
                    "t_schedule_mode": "linear", "context_kv_cache": True,
                    "decode_mode": "sequential", "trim_tail": True,
                    "chunking_enabled": len(text) > CONTINUOUS_MAX_CHARS, "chunk_min_chars": 80,
                    "first_sentence_chunk_min_chars": 80}}
        if caption:
            body["irodori"].update(caption=caption, cfg_scale_caption=CAPTION_CFG_SCALE)
        return await self._post(body)

    async def _post(self, body):
        import aiohttp
        if self.session is None:
            # A native async connection can close a queued request when the
            # caller cancels. The server continues draining an active GPU call.
            self.session = aiohttp.ClientSession(trust_env=False,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds, connect=3.0))
        try:
            async with self.session.post(self.endpoint + "/v1/audio/speech", json=body, allow_redirects=False) as response:
                if response.status != 200:
                    reason = {400: "tts_irodori_request_rejected", 422: "tts_irodori_request_rejected",
                              429: "tts_irodori_queue_full", 503: "tts_irodori_not_ready"}.get(
                                  response.status, "tts_irodori_service_error")
                    raise IrodoriClientError(reason, outcome_known=response.status < 500)
                if response.headers.get("Content-Type", "").split(";", 1)[0].lower() not in {"audio/wav", "audio/x-wav"}:
                    raise IrodoriClientError("tts_media_type_unsupported")
                parts, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_AUDIO:
                        raise IrodoriClientError("tts_audio_size_invalid")
                    parts.append(chunk)
                return {"audio": b"".join(parts), "media_type": "audio/wav"}
        except asyncio.TimeoutError as exc:
            raise IrodoriClientError("tts_irodori_timeout", outcome_known=False) from exc
        except aiohttp.ClientError as exc:
            raise IrodoriClientError("tts_irodori_connection_failed", outcome_known=False) from exc

    async def aclose(self):
        self.closed = True
        if self.session is not None:
            await self.session.close()
