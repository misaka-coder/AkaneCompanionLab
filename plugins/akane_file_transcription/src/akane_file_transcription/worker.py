"""Offline faster-whisper worker. Only PCM decoding, no child processes."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
import sys

# Direct script invocation from a prepared interpreter does not need Akane SDK.
if __package__:
    from .inference import AsrError, load_model, read_pcm, recognize
else:
    from inference import AsrError, load_model, read_pcm, recognize


def deny_child(event, args):
    del args
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"}:
        raise PermissionError("asr_descendant_process_forbidden")


def execute(args):
    try:
        import ctranslate2
        import numpy as np
    except Exception:
        raise AsrError("asr_runtime_incompatible") from None
    pcm = read_pcm(args.source) if args.source else np.zeros(16000, dtype=np.float32)
    try:
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda = False
    device = ("cuda" if cuda else "cpu") if args.device == "auto" else args.device
    attempts = [device] + (["cpu"] if args.device == "auto" and device == "cuda" else [])
    for index, selected in enumerate(attempts):
        compute = ("float16" if selected == "cuda" else "int8") if args.compute_type == "auto" else args.compute_type
        try:
            model = load_model(model_size=args.model, device=selected, compute_type=compute, cache_dir=args.cache_dir)
            result = recognize(
                model,
                pcm,
                language=args.language,
                vad_filter=args.vad_filter if args.source else False,
                allow_empty=not args.source,
            )
            return {
                **result,
                "model": args.model,
                "device": selected,
                "compute_type": compute,
                "fallback_reason": "asr_cuda_failed" if index else "",
            }
        except AsrError as exc:
            if index + 1 < len(attempts) and str(exc) in ("asr_model_unavailable", "asr_inference_failed"):
                continue
            raise


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
