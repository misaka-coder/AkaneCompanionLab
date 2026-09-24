"""Thin calling-shape adapters for the existing realtime voice product entry."""

from __future__ import annotations

from pathlib import Path
import sys

from companion_v01.plugin_subprocess import run_completed
from .inference import AsrError, load_model, read_pcm, recognize
from .local import LocalTranscriber, MODELS, TranscriptionError


def normalize_model(value):
    text = str(value or "small").strip().lower().replace("_", "-")
    text = "large-v3" if text == "large" else text
    return text if text in MODELS and text != "auto" else "small"


def normalize_device(value):
    text = str(value or "auto").strip().lower()
    return text if text in ("auto", "cpu", "cuda") else "auto"


def normalize_compute(value):
    text = str(value or "auto").strip().lower()
    return text if text in ("auto", "float16", "float32", "int8", "int8_float16") else "auto"


def normalize_language(value):
    text = str(value or "zh").strip().lower().replace("_", "-")
    aliases = {
        "中文": "zh",
        "chinese": "zh",
        "普通话": "zh",
        "国语": "zh",
        "英文": "en",
        "english": "en",
        "自动": "",
        "auto": "",
        "detect": "",
    }
    return aliases.get(text, text)


def cached_model(cache, *, model_size, device, compute_type, download_root=None):
    key = (model_size, device, compute_type, str(download_root or ""))
    if key not in cache:
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
            except Exception:
                device = "cpu"
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        cache[key] = load_model(
            model_size=model_size, device=device, compute_type=compute_type, cache_dir=download_root
        )
    return cache[key]


def prepare_input(*, ffmpeg_path, source_path, prepared_path):
    async def execute():
        # Realtime preparation is independent of optional file-plugin settings.
        runtime = LocalTranscriber(
            ffmpeg=ffmpeg_path, python=sys.executable, model="small", device="cpu", compute_type="int8", cache_dir=""
        )
        try:
            await runtime.prepare(source=source_path, prepared=prepared_path)
            return {"ok": True}
        finally:
            await runtime.aclose()

    try:
        return run_completed(execute)
    except (TranscriptionError, OSError):
        return {"ok": False, "error": "asr_audio_prepare_failed"}


def transcribe_prepared(*, model, audio_path, source, source_index, language, vad_filter):
    card = {key: str(source.get(key) or "") for key in ("source_type", "source_id", "handle", "title", "input_ext")}
    card["title"] = card["title"] or Path(audio_path).name
    try:
        result = recognize(model, read_pcm(audio_path), language=language, vad_filter=bool(vad_filter))
        return {**result, "source": card, "source_index": source_index}
    except AsrError as exc:
        return {
            "status": "failed",
            "error": str(exc),
            "source": card,
            "source_index": source_index,
            "segments": [],
            "text": "",
        }
