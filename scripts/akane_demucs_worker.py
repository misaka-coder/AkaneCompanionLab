from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _prepare_imports(package_root: Path) -> None:
    resolved_packages = package_root.resolve(strict=True)
    if not resolved_packages.is_dir() or not (resolved_packages / "demucs").is_dir():
        raise RuntimeError("demucs_package_root_invalid")
    sys.path.insert(0, str(resolved_packages))
    sys.path.insert(0, str(PROJECT_ROOT))


def _probe() -> dict[str, object]:
    import torch
    import demucs

    return {
        "ok": True,
        "status": "ready",
        "cuda_available": bool(torch.cuda.is_available()),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "torch_version": str(torch.__version__),
        "demucs_version": str(getattr(demucs, "__version__", "4.0.1")),
    }


def _separate(*, source_path: Path, output_root: Path, model: str) -> dict[str, object]:
    from companion_v01.generated_files_media import separate_audio_with_demucs

    source = source_path.resolve(strict=True)
    output = output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    stems = separate_audio_with_demucs(
        source_path=source,
        output_root=output,
        model_name=model,
    )
    vocals = stems.get("vocals")
    instrumental = stems.get("instrumental")
    if not isinstance(vocals, Path) or not isinstance(instrumental, Path):
        raise RuntimeError("demucs_outputs_missing")
    if not vocals.is_file() or not instrumental.is_file():
        raise RuntimeError("demucs_outputs_missing")
    return {
        "ok": True,
        "status": "ready",
        "device": str(stems.get("device_used") or ""),
        "seconds": round(time.perf_counter() - started, 3),
        "vocals_bytes": vocals.stat().st_size,
        "instrumental_bytes": instrumental.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated CUDA Demucs worker")
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--source", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--model", default="htdemucs", choices=("htdemucs", "htdemucs_ft"))
    args = parser.parse_args()
    try:
        _prepare_imports(Path(args.package_root))
        if args.probe:
            payload = _probe()
        else:
            if not args.source or not args.output_root:
                raise RuntimeError("demucs_worker_input_missing")
            payload = _separate(
                source_path=Path(args.source),
                output_root=Path(args.output_root),
                model=args.model,
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "status": "failed",
            "reason": str(exc or "demucs_worker_failed").splitlines()[0][:160],
        }
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
        return 1
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
