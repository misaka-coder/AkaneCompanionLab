"""Client for existing synchronous loopback ASR; cancellation drains completion."""

from __future__ import annotations

import asyncio
import http.client
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from companion_v01.plugin_subprocess import drain
from .local import Options, PROTECTED, TranscriptionError


class RemoteTranscriptionError(TranscriptionError):
    def __init__(self, reason, *, terminal=False):
        super().__init__(reason)
        self.terminal = terminal


class RemoteTranscriber:
    def __init__(self, url, *, timeout=1800):
        parsed = urlsplit(str(url))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RemoteTranscriptionError("asr_endpoint_invalid")
        try:
            self.port = parsed.port or 80
        except ValueError:
            raise RemoteTranscriptionError("asr_endpoint_invalid") from None
        self.host, self.prefix = parsed.hostname, parsed.path.rstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9_./-]*", self.prefix) or ".." in self.prefix.split("/"):
            raise RemoteTranscriptionError("asr_endpoint_invalid")
        self.timeout, self.active, self.closed = timeout, set(), False

    async def run(self, function, *args):
        if self.closed:
            raise RemoteTranscriptionError("asr_remote_closed")
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.active.add(task)
        try:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await drain(task)
                except RemoteTranscriptionError as exc:
                    if not exc.terminal:
                        raise RemoteTranscriptionError("remote_completion_unconfirmed") from None
                raise asyncio.CancelledError
        finally:
            self.active.discard(task)

    async def probe(self):
        return await self.run(self._probe)

    def _probe(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request("GET", self.prefix + "/health")
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 1024 * 1024:
                raise ValueError
            result = json.loads(raw)
            asr = result.get("asr", {})
            if not isinstance(asr, dict) or asr.get("ready") is not True:
                raise ValueError
            return {"ok": True, "provider": "local_media_executor", "model": str(asr.get("model") or "")[:64]}
        except (OSError, http.client.HTTPException, ValueError, AttributeError):
            raise RemoteTranscriptionError("asr_remote_unavailable") from None
        finally:
            connection.close()

    async def transcribe(self, *, source, options=None):
        options = options or Options()
        if options.device != "auto" or options.compute_type != "auto":
            raise RemoteTranscriptionError("asr_remote_option_unsupported", terminal=True)
        source = Path(source)
        if source.suffix.lower().lstrip(".") in PROTECTED:
            raise RemoteTranscriptionError("protected_media_format", terminal=True)
        if source.suffix.lower() not in {
            ".wav",
            ".mp3",
            ".flac",
            ".m4a",
            ".aac",
            ".ogg",
            ".opus",
            ".webm",
            ".mp4",
            ".avi",
            ".mkv",
            ".mov",
        }:
            raise RemoteTranscriptionError("asr_source_format_invalid", terminal=True)
        if not source.is_file() or not source.stat().st_size:
            raise RemoteTranscriptionError("asr_source_missing", terminal=True)
        return await self.run(self._transcribe, source, options)

    def _transcribe(self, source, options):
        boundary = "akane-" + uuid4().hex
        fields = {
            "model": "" if options.model_size == "auto" else options.model_size,
            "language": "" if options.language == "auto" else options.language,
            "response_format": "verbose_json",
            "vad_filter": "true" if options.vad_filter else "false",
        }
        head = "".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            for name, value in fields.items()
        )
        head += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source{source.suffix.lower()}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        head, tail = head.encode(), f"\r\n--{boundary}--\r\n".encode()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        terminal = False
        try:
            connection.putrequest("POST", self.prefix + "/v1/audio/transcriptions")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(head) + source.stat().st_size + len(tail)))
            connection.endheaders()
            connection.send(head)
            with source.open("rb") as stream:
                while chunk := stream.read(256 * 1024):
                    connection.send(chunk)
            connection.send(tail)
            response = connection.getresponse()
            if response.status != 200:
                raise RemoteTranscriptionError("asr_remote_failed")
            terminal = True
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RemoteTranscriptionError("asr_output_too_large", terminal=True)
            payload = json.loads(raw)
            segments = payload.get("segments")
            if not isinstance(segments, list) or len(segments) > 20000:
                raise ValueError
            normalized = []
            for segment in segments:
                start, end = float(segment["start"]), float(segment["end"])
                text = str(segment["text"]).strip()
                if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                    raise ValueError
                if text:
                    normalized.append({"index": len(normalized) + 1, "start": start, "end": end, "text": text})
            if not normalized:
                raise RemoteTranscriptionError("asr_no_speech", terminal=True)
            duration = float(payload.get("duration") or max(s["end"] for s in normalized))
            if not math.isfinite(duration) or duration < max(s["end"] for s in normalized):
                raise ValueError
            return {
                "ok": True,
                "status": "ready",
                "provider": "local_media_executor",
                "language": str(payload.get("language") or options.language),
                "duration_seconds": duration,
                "segments": normalized,
                "segment_count": len(normalized),
                "text": "\n".join(s["text"] for s in normalized),
                "model": str(payload.get("model") or options.model_size),
                "device": "",
                "compute_type": "",
                "fallback_reason": "",
            }
        except (OSError, http.client.HTTPException):
            raise RemoteTranscriptionError(
                "asr_remote_response_failed" if terminal else "remote_completion_unconfirmed", terminal=terminal
            ) from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RemoteTranscriptionError("asr_remote_response_invalid", terminal=terminal) from None
        finally:
            connection.close()

    async def aclose(self):
        self.closed = True
        for task in tuple(self.active):
            try:
                await drain(task)
            except RemoteTranscriptionError:
                pass  # The original invocation owns its failure; no retry.
