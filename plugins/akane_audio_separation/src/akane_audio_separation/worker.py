"""Run with an administrator-selected ML Python, never inside the host process.

The parent prepares PCM WAV using its separately cancellable FFmpeg process.
This worker therefore starts no decoder subprocesses or multiprocessing pool.
Only existing, checksum-verified model files are loaded; no implicit downloads.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import wave


MODELS = ("htdemucs", "htdemucs_ft")


class SeparationError(RuntimeError):
    pass


def load_model(*, model_name: str, model_root: Path | None = None):
    if model_name not in MODELS:
        raise SeparationError("separation_model_not_supported")
    try:
        import torch
        from demucs.pretrained import REMOTE_ROOT
        from demucs.repo import BagOnlyRepo, check_checksum
        from demucs.states import load_model as restore_model
    except ImportError:
        raise SeparationError("demucs_runtime_incompatible") from None
    root = model_root or Path(torch.hub.get_dir()) / "checkpoints"
    if not root.is_dir():
        raise SeparationError("demucs_model_missing")

    # Demucs ships model-bag YAMLs. Resolve their signatures exclusively in a
    # local repository: RemoteRepo/get_model's download fallback is not used.
    class CachedRepo:
        def get_model(self, signature):
            # Signatures come from Demucs' installed bag definition, not model
            # arguments. Refuse unsigned filenames; verify the bundled hash.
            matches = list(root.glob(f"{signature}-*.th"))
            if len(matches) != 1:
                raise SeparationError("demucs_model_missing")
            path = matches[0]
            checksum = path.stem.split("-", 1)[1]
            if len(checksum) < 8 or any(c not in "0123456789abcdef" for c in checksum):
                raise SeparationError("demucs_model_invalid")
            check_checksum(path, checksum)
            # Demucs 4 checkpoints contain their model class. Match its legacy
            # pretrained loader explicitly on PyTorch >=2.6. Only administrator-
            # supplied/trusted model caches belong here, never user attachments.
            package = torch.load(path, map_location="cpu", weights_only=False)
            return restore_model(package)

    try:
        model = BagOnlyRepo(REMOTE_ROOT, CachedRepo()).get_model(model_name)
    except SeparationError:
        raise
    except Exception:
        raise SeparationError("demucs_model_unavailable") from None
    model.eval()
    return model


def model_info(model) -> dict:
    import torch

    names = list(model.sources)
    rate = int(model.samplerate)
    channels = int(model.audio_channels)
    if "vocals" not in names or len(names) < 2 or rate <= 0 or channels not in (1, 2):
        raise SeparationError("demucs_model_invalid")
    return {
        "sample_rate": rate,
        "channels": channels,
        "sources": names,
        "cuda_available": bool(torch.cuda.is_available()),
    }


def read_pcm(path: Path, *, sample_rate: int, channels: int):
    import numpy as np
    import torch

    try:
        with wave.open(str(path), "rb") as source:
            if (
                source.getsampwidth() != 2
                or source.getframerate() != sample_rate
                or source.getnchannels() != channels
                or source.getnframes() <= 0
            ):
                raise SeparationError("separation_input_pcm_invalid")
            data = source.readframes(source.getnframes())
        samples = np.frombuffer(data, dtype="<i2").reshape(-1, channels).copy()
        return torch.from_numpy(samples).float().transpose(0, 1) / 32768.0
    except (wave.Error, ValueError, EOFError):
        raise SeparationError("separation_input_pcm_invalid") from None


def write_pcm(tensor, path: Path, *, sample_rate: int) -> None:
    import numpy as np

    data = tensor.detach().cpu().transpose(0, 1).contiguous().numpy()
    if data.ndim != 2 or not np.isfinite(data).all():
        raise SeparationError("separation_output_invalid")
    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(pcm.shape[1])
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def separate_pcm(*, source_path: Path, output_root: Path, model, device: str = "auto") -> dict:
    import torch
    from demucs.apply import apply_model

    info = model_info(model)
    waveform = read_pcm(source_path, sample_rate=info["sample_rate"], channels=info["channels"])
    if device not in ("auto", "cpu", "cuda"):
        raise SeparationError("separation_device_invalid")
    preferred = ("cuda" if info["cuda_available"] else "cpu") if device == "auto" else device
    if preferred == "cuda" and not info["cuda_available"]:
        raise SeparationError("separation_cuda_unavailable")
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    torch.manual_seed(0)

    def run(selected):
        model.to(selected)
        with torch.no_grad():
            return (
                apply_model(
                    model,
                    waveform[None].to(selected),
                    device=selected,
                    progress=False,
                    num_workers=0,
                )[0]
                .detach()
                .cpu()
            )

    used = preferred
    try:
        separated = run(preferred)
    except RuntimeError as exc:
        if preferred != "cuda" or not any(word in str(exc).lower() for word in ("cuda", "cudnn", "out of memory")):
            raise SeparationError("demucs_inference_failed") from None
        model.to("cpu")
        torch.cuda.empty_cache()
        used = "cpu"
        try:
            separated = run("cpu")
        except RuntimeError:
            raise SeparationError("demucs_inference_failed") from None
    vocals_index = info["sources"].index("vocals")
    others = [index for index in range(len(info["sources"])) if index != vocals_index]
    if separated.shape != (len(info["sources"]), info["channels"], waveform.shape[-1]):
        raise SeparationError("separation_output_invalid")
    output_root.mkdir(parents=True, exist_ok=True)
    write_pcm(separated[vocals_index], output_root / "vocals.wav", sample_rate=info["sample_rate"])
    write_pcm(separated[others].sum(dim=0), output_root / "instrumental.wav", sample_rate=info["sample_rate"])
    return {**info, "device_used": used, "frames": waveform.shape[-1]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--model", choices=MODELS, default="htdemucs")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    try:
        if args.package_root is not None:
            root = args.package_root.resolve()
            if not (root / "demucs").is_dir():
                raise SeparationError("demucs_package_root_invalid")
            sys.path.insert(0, str(root))
        model = load_model(model_name=args.model, model_root=args.model_root)
        if args.probe:
            data = model_info(model)
        else:
            if args.source is None or args.output_root is None:
                raise SeparationError("separation_input_missing")
            data = separate_pcm(source_path=args.source, output_root=args.output_root, model=model, device=args.device)
        result = {"ok": True, "status": "ready", **data}
    except SeparationError as exc:
        result = {"ok": False, "status": "unavailable", "reason": str(exc)}
    except Exception:
        # Imported ML packages may include private paths in exception strings.
        result = {"ok": False, "status": "unavailable", "reason": "demucs_execution_failed"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
