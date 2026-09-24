"""Actual local/remote choice; auto fallback only before inference starts."""

from __future__ import annotations

import os

from .local import LocalTranscriber, TranscriptionError
from .remote import RemoteTranscriber


class Transcriber:
    def __init__(self):
        self.backend = os.environ.get("AKANE_ASR_BACKEND", "auto").strip()
        if self.backend not in ("auto", "local", "remote"):
            raise TranscriptionError("asr_backend_invalid")
        url = os.environ.get("AKANE_ASR_REMOTE_URL", "").strip()
        self.remote = RemoteTranscriber(url) if url else None
        self.local = LocalTranscriber()

    async def select(self):
        fallback = ""
        if self.backend != "local":
            if self.remote is not None:
                try:
                    info = await self.remote.probe()
                    return self.remote, info, ""
                except TranscriptionError as exc:
                    if self.backend == "remote":
                        raise
                    fallback = str(exc)
            elif self.backend == "remote":
                raise TranscriptionError("asr_remote_unconfigured")
        return self.local, None, fallback

    async def probe(self):
        runtime, info, fallback = await self.select()
        result = info if info is not None else await runtime.probe()
        return {**result, "fallback_reason": fallback or result.get("fallback_reason", "")}

    async def transcribe(self, *, source, options=None, work_root=None):
        runtime, _, fallback = await self.select()
        kwargs = {"work_root": work_root} if runtime is self.local else {}
        result = await runtime.transcribe(source=source, options=options, **kwargs)
        return {**result, "fallback_reason": fallback or result.get("fallback_reason", "")}

    async def aclose(self):
        await self.local.aclose()
        if self.remote is not None:
            await self.remote.aclose()
