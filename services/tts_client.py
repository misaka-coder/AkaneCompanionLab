from __future__ import annotations

import asyncio
import re

import edge_tts


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;…])")


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
