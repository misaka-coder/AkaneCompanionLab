"""Offline faster-whisper worker. Only PCM decoding, no child processes."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import math
import os
from pathlib import Path
import sys
import wave


class AsrError(RuntimeError):
    pass


def deny_child(event, args):
    del args
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"}:
        raise PermissionError("asr_descendant_process_forbidden")


def finite(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def execute(args):
    try:
        import ctranslate2
        import numpy as np
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
    except Exception:
        raise AsrError("asr_runtime_incompatible") from None
    try:
        model_path = download_model(args.model, cache_dir=args.cache_dir or None, local_files_only=True)
    except Exception:
        raise AsrError("asr_model_missing") from None
    # faster-whisper otherwise asks tokenizers to fetch a fallback tokenizer,
    # even when the model path itself came from a local-only lookup.
    if not all((Path(model_path) / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
        raise AsrError("asr_model_missing")
    if args.source:
        try:
            with wave.open(args.source, "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise ValueError
                pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            if not len(pcm) or not np.isfinite(pcm).all():
                raise ValueError
        except Exception:
            raise AsrError("asr_input_pcm_invalid") from None
    else:
        pcm = np.zeros(16000, dtype=np.float32)
    duration = len(pcm) / 16000
    try:
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda = False
    device = ("cuda" if cuda else "cpu") if args.device == "auto" else args.device
    attempts = [device] + (["cpu"] if args.device == "auto" and device == "cuda" else [])
    for index, selected in enumerate(attempts):
        compute = ("float16" if selected == "cuda" else "int8") if args.compute_type == "auto" else args.compute_type
        try:
            model = WhisperModel(model_path, device=selected, compute_type=compute, cpu_threads=4, num_workers=1)
        except Exception:
            if index + 1 < len(attempts):
                continue
            raise AsrError("asr_model_unavailable") from None
        try:
            raw, info = model.transcribe(
                pcm,
                beam_size=5,
                language=None if args.language == "auto" else args.language,
                vad_filter=args.vad_filter if args.source else False,
            )
            segments = []
            for segment in raw:
                text = str(segment.text).strip()
                if not text:
                    continue
                start, end = finite(segment.start), finite(segment.end)
                if start is None or end is None or start < 0 or start > duration or end < start or end > duration + 1:
                    raise ValueError
                if len(segments) >= 20000 or len(text) > 50000:
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
            if args.source and not segments:
                raise AsrError("asr_no_speech")
            return {
                "ok": True,
                "status": "ready",
                "provider": "faster_whisper",
                "model": args.model,
                "device": selected,
                "compute_type": compute,
                "fallback_reason": "asr_cuda_failed" if index else "",
                "duration_seconds": round(duration, 3),
                "language": info.language,
                "segments": segments,
                "segment_count": len(segments),
                "text": "\n".join(s["text"] for s in segments),
            }
        except AsrError:
            raise
        except Exception:
            if index + 1 < len(attempts):
                continue
            raise AsrError("asr_inference_failed") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("tiny", "base", "small", "medium", "large-v2", "large-v3"), default="small")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--compute-type", choices=("auto", "float16", "float32", "int8", "int8_float16"), default="auto"
    )
    parser.add_argument("--language", default="zh")
    parser.add_argument("--source")
    parser.add_argument("--vad-filter", action="store_true")
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.addaudithook(deny_child)
    try:
        with redirect_stdout(sys.stderr):
            result = execute(args)
    except AsrError as exc:
        result = {"ok": False, "reason": str(exc)}
    except Exception:
        result = {"ok": False, "reason": "asr_execution_failed"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
