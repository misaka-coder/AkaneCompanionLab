"""Single offline model/PCM/segment implementation for file and voice callers."""

from __future__ import annotations

import math
from pathlib import Path
import wave


class AsrError(RuntimeError):
    pass


def finite(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def load_model(*, model_size, device, compute_type, cache_dir=None):
    try:
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
    except Exception:
        raise AsrError("asr_runtime_incompatible") from None
    try:
        model_path = download_model(model_size, cache_dir=cache_dir or None, local_files_only=True)
    except Exception:
        raise AsrError("asr_model_missing") from None
    if not all((Path(model_path) / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
        raise AsrError("asr_model_missing")
    try:
        return WhisperModel(model_path, device=device, compute_type=compute_type, cpu_threads=4, num_workers=1)
    except Exception:
        raise AsrError("asr_model_unavailable") from None


def read_pcm(path):
    import numpy as np

    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                raise ValueError
            pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if not len(pcm) or not np.isfinite(pcm).all():
            raise ValueError
        return pcm
    except Exception:
        raise AsrError("asr_input_pcm_invalid") from None


def recognize(model, pcm, *, language, vad_filter, allow_empty=False):
    duration = len(pcm) / 16000
    try:
        raw, info = model.transcribe(
            pcm, beam_size=5, language=None if language in ("", "auto") else language, vad_filter=vad_filter
        )
        segments, characters = [], 0
        for segment in raw:
            text = str(segment.text).strip()
            if not text:
                continue
            start, end = finite(segment.start), finite(segment.end)
            if start is None or end is None or start < 0 or start > duration or end < start or end > duration + 1:
                raise ValueError
            characters += len(text)
            if len(segments) >= 20000 or characters > 1000000:
                raise AsrError("asr_output_too_large")
            segments.append(
                {
                    "index": len(segments) + 1,
                    "start": round(start, 3),
                    "end": round(min(duration, end), 3),
                    "text": text,
                    "avg_logprob": finite(segment.avg_logprob),
                    "no_speech_prob": finite(segment.no_speech_prob),
                }
            )
        if not allow_empty and not segments:
            raise AsrError("asr_no_speech")
        return {
            "ok": True,
            "status": "ready",
            "provider": "faster_whisper",
            "duration_seconds": round(duration, 3),
            "language": info.language,
            "segments": segments,
            "segment_count": len(segments),
            "text": "\n".join(s["text"] for s in segments),
        }
    except AsrError:
        raise
    except Exception:
        raise AsrError("asr_inference_failed") from None
