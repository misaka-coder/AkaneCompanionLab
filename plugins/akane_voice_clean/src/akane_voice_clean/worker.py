"""Offline DeepFilterNet worker. No decoder subprocesses or implicit downloads."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import wave


class CleaningError(RuntimeError):
    pass


def deny_descendant_process(event, args):
    del args
    if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"):
        # Windows platform probing may try `cmd /c ver`; PermissionError lets
        # Python use its real getwindowsversion fallback without starting it.
        raise PermissionError("cleaning_child_process_forbidden")


def load_model(model_root, *, post_filter=False):
    if model_root and not (Path(model_root) / "config.ini").is_file():
        raise CleaningError("deepfilternet_model_missing")
    try:
        import torch
        from df.enhance import init_df, enhance
        from df.utils import get_cache_dir
    except (ImportError, OSError):
        raise CleaningError("deepfilternet_runtime_incompatible") from None
    # Preserve the old product's DF2 default, but never pass its magic model
    # name to init_df: doing so could invoke the library's download fallback.
    root = Path(model_root).resolve() if model_root else Path(get_cache_dir()) / "DeepFilterNet2"
    if not (root / "config.ini").is_file():
        raise CleaningError("deepfilternet_model_missing")
    checkpoints = list((root / "checkpoints").glob("model*.ckpt.best"))
    checkpoints = checkpoints or list((root / "checkpoints").glob("model*.ckpt"))
    if not checkpoints:
        raise CleaningError("deepfilternet_model_missing")
    try:
        # DF's logger probes Git via a subprocess even at ERROR. Its supported
        # "none" level skips that metadata collection entirely, keeping this
        # worker free of unowned descendants and host-identifying log output.
        model, state, _ = init_df(str(root), post_filter=post_filter, log_file=None, log_level="none")
        # DF's loader tolerates missing parameters. Do not advertise a randomly
        # initialized/partially restored model as successful AI cleaning.
        checkpoint = max(checkpoints, key=lambda p: int(p.name.split(".")[0].split("_")[-1]))
        saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
        saved = {k.replace("clc", "df"): v for k, v in saved.items()}
        for name, parameter in model.named_parameters():
            value = saved.get(name)
            if value is None or value.shape != parameter.shape or not torch.isfinite(value).all():
                raise ValueError
            if not torch.equal(value, parameter.detach().cpu()):
                raise ValueError
        if int(state.sr()) != 48000:
            raise ValueError
    except (Exception, SystemExit):
        raise CleaningError("deepfilternet_model_unavailable") from None
    return model, state, enhance


def run(args):
    # Child-local selection; no mutation of the host's configuration.
    os.environ["DEVICE"] = "" if args.device == "auto" else args.device
    model, state, enhance = load_model(args.model_root, post_filter=args.post_filter)
    import numpy as np
    import torch

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    info = {"sample_rate": 48000, "channels": 1, "device_used": str(next(model.parameters()).device)}
    if args.probe:
        # Probe runs actual inference, not only package discovery/model load.
        samples = torch.zeros(1, 4800)
    else:
        if args.source is None or args.output is None:
            raise CleaningError("cleaning_input_missing")
        try:
            with wave.open(str(args.source), "rb") as source:
                if (source.getsampwidth(), source.getnchannels(), source.getframerate()) != (2, 1, 48000):
                    raise ValueError
                frames = source.getnframes()
                if frames <= 0:
                    raise ValueError
                raw = source.readframes(frames)
            samples = torch.from_numpy(np.frombuffer(raw, dtype="<i2").copy()).float()[None] / 32768
        except (ValueError, wave.Error, EOFError):
            raise CleaningError("cleaning_input_pcm_invalid") from None
    try:
        output = enhance(model, state, samples, pad=True).detach().cpu()
        if output.shape != samples.shape or not torch.isfinite(output).all():
            raise ValueError
    except Exception:
        raise CleaningError("deepfilternet_inference_failed") from None
    if not args.probe:
        pcm = np.round(np.clip(output.numpy(), -1, 1) * 32767).astype("<i2")
        with wave.open(str(args.output), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(48000)
            target.writeframes(pcm.tobytes())
    return {**info, "frames": samples.shape[-1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--post-filter", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.addaudithook(deny_descendant_process)
    try:
        # Third-party stdout can contain local paths; stdout is only our JSON.
        with contextlib.redirect_stdout(sys.stderr):
            result = {"ok": True, **run(args)}
    except CleaningError as exc:
        result = {"ok": False, "reason": str(exc)}
    except (Exception, SystemExit):
        result = {"ok": False, "reason": "deepfilternet_execution_failed"}
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
