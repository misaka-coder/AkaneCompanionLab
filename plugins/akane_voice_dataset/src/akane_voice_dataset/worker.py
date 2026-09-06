"""Stdlib PCM/ZIP worker; no FFmpeg, SDK, model downloads or child processes."""

from __future__ import annotations

from array import array
import io
import json
import math
from pathlib import Path
import sys
import time
import wave
import zipfile

if __package__:
    from .options import DatasetError, MAX_PCM_BYTES, MAX_SLICES, MAX_ZIP_BYTES, Options
else:
    from options import DatasetError, MAX_PCM_BYTES, MAX_SLICES, MAX_ZIP_BYTES, Options


def rms(samples):
    if not samples:
        return -120.0
    value = math.sqrt(sum(int(s) ** 2 for s in samples) / len(samples))
    return round(20 * math.log10(value / 32768), 2) if value else -120.0


def peak(samples):
    value = max((abs(s) for s in samples), default=0)
    return round(20 * math.log10(value / 32768), 2) if value else -120.0


def intervals(samples, rate, channels, options):
    frames = len(samples) // channels
    hop = max(1, int(rate * 0.02))
    minimum = max(hop, int(rate * options.min_silence_ms / 1000))
    keep = int(rate * options.max_silence_kept_ms / 1000)
    cuts, start, silence, best, best_db = [], None, None, 0, 0.0
    for index in range(0, frames, hop):
        # Frame energy across channels avoids anti-phase stereo becoming fake silence.
        db = rms(samples[index * channels : min(frames, index + hop) * channels])
        quiet = db < options.silence_threshold_db
        if start is None:
            if not quiet:
                start = max(0, index - keep)
            continue
        if quiet:
            if silence is None:
                silence, best, best_db = index, index, db
            elif db < best_db:
                best, best_db = index, db
            if index - silence + hop >= minimum:
                end = min(frames, best + keep)
                if end > start:
                    cuts.append((start, end))
                start, silence = None, None
        else:
            silence = None
    if start is not None and frames > start:
        cuts.append((start, frames))
    merged = []
    for start, end in cuts:
        if merged and start - merged[-1][1] <= int(rate * 0.12):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    # Preserve inspectable silence with quality flags, not a made-up speech slice.
    return merged or ([(0, frames)] if frames else [])


def metrics(samples, duration, options):
    level, maximum, flags = rms(samples), peak(samples), []
    if duration < options.min_clip_seconds:
        flags.append("too_short")
    if duration > options.max_clip_seconds:
        flags.append("too_long")
    if level < -35:
        flags.append("low_volume")
    if maximum > -0.5:
        flags.append("clipping")
    if level < -60 or duration <= 0.05:
        flags.append("empty_or_failed")
    return {"rms_dbfs": level, "peak_dbfs": maximum, "flags": flags}


def read_pcm(path, *, options):
    # Controlled raw s16le avoids Python 3.11's lack of WAVE_FORMAT_EXTENSIBLE
    # support for FFmpeg output above 48 kHz. Final WAVs have classic PCM headers.
    rate, channels = options.target_sr, 1 if options.mono else 2
    count = Path(path).stat().st_size
    if not 0 < count <= MAX_PCM_BYTES:
        raise DatasetError("dataset_pcm_budget_exceeded")
    if count % (channels * 2):
        raise DatasetError("dataset_pcm_invalid")
    samples = array("h")
    with Path(path).open("rb") as reader:
        samples.fromfile(reader, count // 2)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples, rate, channels


def wav_bytes(samples, rate, channels):
    memory = io.BytesIO()
    if sys.byteorder != "little":
        samples = samples[:]
        samples.byteswap()
    with wave.open(memory, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())
    return memory.getvalue()


def build(config):
    options = Options(**config["options"])
    manifest = {
        "title": config["title"],
        "profile": options.profile,
        "created_at": int(time.time()),
        "options": config["options"],
        "sources": list(config["failures"]),
        "slices": [],
        "issue_slices": {},
    }
    total_pcm = 0
    with zipfile.ZipFile(config["output"], "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in config["sources"]:
            samples, rate, channels = read_pcm(source["prepared"], options=options)
            total_pcm += len(samples) * 2
            if total_pcm > MAX_PCM_BYTES:
                raise DatasetError("dataset_pcm_budget_exceeded")
            count = 0
            for local, (start, end) in enumerate(intervals(samples, rate, channels, options), 1):
                if len(manifest["slices"]) >= MAX_SLICES:
                    raise DatasetError("dataset_slice_budget_exceeded")
                selected = samples[start * channels : end * channels]
                duration = (end - start) / rate
                filename = f"src{source['source_index']:02d}_slice_{local:03d}.wav"
                info = {
                    "filename": filename,
                    "source_index": source["source_index"],
                    "source_handle": source["handle"],
                    "source_title": source["title"],
                    "global_index": len(manifest["slices"]) + 1,
                    "local_index": local,
                    "start_time": round(start / rate, 3),
                    "end_time": round(end / rate, 3),
                    "duration_seconds": round(duration, 3),
                    "sample_rate": rate,
                    "channels": channels,
                    "included": True,
                    **metrics(selected, duration, options),
                }
                archive.writestr("slices/" + filename, wav_bytes(selected, rate, channels))
                if archive.fp.tell() > MAX_ZIP_BYTES:
                    raise DatasetError("dataset_output_too_large")
                manifest["slices"].append(info)
                for flag in info["flags"]:
                    manifest["issue_slices"].setdefault(flag, []).append(
                        {
                            key: info[key]
                            for key in ("filename", "source_handle", "duration_seconds", "rms_dbfs", "peak_dbfs")
                        }
                    )
                count += 1
            manifest["sources"].append(
                {
                    "source_index": source["source_index"],
                    "handle": source["handle"],
                    "title": source["title"],
                    "status": "ready",
                    "slice_count": count,
                    "cleaned": source["cleaned"],
                }
            )
        slices = manifest["slices"]
        if not slices:
            raise DatasetError("dataset_no_slices_created")
        manifest["sources"].sort(key=lambda s: s["source_index"])
        flagged = sum(bool(s["flags"]) for s in slices)
        duration = round(sum(s["duration_seconds"] for s in slices), 3)
        stats = manifest["stats"] = {
            "source_count": len(manifest["sources"]),
            "slice_count": len(slices),
            "recommended_count": len(slices) - flagged,
            "flagged_count": flagged,
            "failed_source_count": sum(s["status"] != "ready" for s in manifest["sources"]),
            "total_duration_seconds": duration,
            "average_duration_seconds": round(duration / len(slices), 3),
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        lines = [
            "# " + manifest["title"],
            "",
            "- Profile: " + options.profile,
            f"- Slices: {len(slices)}",
            f"- Recommended: {len(slices) - flagged}",
            f"- Flagged: {flagged}",
            f"- Total duration: {duration} seconds",
            "",
            "## Issue Slices",
        ]
        for flag, items in manifest["issue_slices"].items():
            lines.append(f"- {flag}: " + ", ".join(s["filename"] for s in items[:30]))
        if not manifest["issue_slices"]:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Full source, timing and quality metadata is in `manifest.json`.",
                "Min/max durations are quality flags, not hard cuts. This archive does not train a model.",
            ]
        )
        archive.writestr("README.md", "\n".join(lines))
    if Path(config["output"]).stat().st_size > MAX_ZIP_BYTES:
        raise DatasetError("dataset_output_too_large")
    return {"ok": True, "stats": stats, "issues": {k: v[:8] for k, v in manifest["issue_slices"].items()}}


def reject_children(event, args):
    if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"):
        raise RuntimeError("dataset_worker_child_forbidden")


def main():
    sys.addaudithook(reject_children)
    try:
        config_path = Path(sys.argv[1])
        if config_path.stat().st_size > 128 * 1024:
            raise DatasetError("dataset_request_invalid")
        result = build(json.loads(config_path.read_text(encoding="utf-8")))
    except DatasetError as exc:
        result = {"ok": False, "reason": str(exc)}
    except Exception:
        result = {"ok": False, "reason": "dataset_worker_failed"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
