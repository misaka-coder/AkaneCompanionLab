# Voice cleaning plugin

The optional market plugin `akane.voice-clean` exposes
`akane.voice-clean.run.v1` through the public SDK. The old built-in cleaning
tool, status, command helpers and filter implementation are deleted. Current
dataset `clean_first` calls the installed capability through normal permission
admission; missing/disabled/not-admitted cleaning fails that source rather than
claiming a cleaned dataset. Historical generated files remain readable.

The descriptor owns native and legacy discovery. Only installation/activation
makes it visible in QQ/desktop; it uses scoped resource handles and returns one
managed artifact. Host Jobs, cancellation, terminal MemCore records and explicit
`send_file` delivery remain host-owned. No new task queue or automatic playback
is introduced. Build the repository static market with
`python scripts/build_plugin_market.py`; installation remains explicit.

The basic pipeline uses real FFmpeg high/low-pass and spectral denoising. It
preserves `denoise`, `voice_focus`, `dereverb`, `deecho`, optional post-filter and
WAV/FLAC/MP3 output. The latter two modes are stronger denoising presets, NOT
dedicated dereverberation/acoustic echo cancellation models. Results report
that limitation, actual mono 48 kHz output, actual backend and fallback reason.

`quality=basic` never invokes AI. `auto` falls back to FFmpeg on a structured AI
failure. Explicit `ai` fails without returning a basic result. Original media
are unchanged; partial output is removed after failure/cancellation. Playlist,
network stream and protected-cache input are rejected. The shared public
`companion_v01.plugin_subprocess` SDK owns child lifecycle, not a new job queue.

Dependencies are administrator-provisioned, not silently installed:

- Python 3.11+ and current Akane SDK; FFmpeg/FFprobe on PATH or
  `AKANE_MEDIA_FFMPEG` / `AKANE_MEDIA_FFPROBE`.
- Optional `AKANE_CLEAN_PYTHON`: compatible DeepFilterNet/PyTorch/Torchaudio
  environment. Default is the selected interpreter. Merely having `df` is not
  considered ready. AI probing loads weights and runs actual short inference.
- Optional `AKANE_CLEAN_MODEL_ROOT`: trusted existing DeepFilterNet model
  directory containing `config.ini` and trained `checkpoints/model*.ckpt[.best]`.
  Default is the selected environment's existing DeepFilterNet2 cache, matching
  the prior product. Absolute directory input prevents implicit model download.
  Model directories are administrator configuration, never user attachments;
  the third-party checkpoint loader can deserialize trusted model artifacts.
- `AKANE_CLEAN_DEVICE`: `auto` (default), `cpu`, `cuda`. Device selection is
  child-local; basic output never claims to have used a GPU or AI model.

No DataLoader workers/decoder descendants are launched. Incompatible runtime,
missing model and failed model/inference have separate stable failure reasons;
library logs/private paths do not enter the JSON result. This wheel does not
install a GPU environment or download weights.

Run `python -m unittest discover -s plugins/akane_voice_clean/tests -v` from an
Akane SDK development environment. Model tests require a prepared environment;
skipped tests must not be reported as real AI acceptance.

## Verified Windows CPU environment

On 2026-09-06 a separate Python 3.11 environment successfully loaded the official
DeepFilterNet2 checkpoint and performed real inference with
`torch==2.6.0+cpu`, `torchaudio==2.6.0+cpu`, `deepfilternet==0.5.6`,
`numpy==1.26.4`, `soundfile==0.12.1`. CPU Torch wheels use the
[official PyTorch index](https://download.pytorch.org/whl/cpu); the remaining
packages use PyPI. This does not claim that an arbitrary newer TorchAudio build
is compatible with DeepFilterNet 0.5.6.

For local provisioning, choose a disk with sufficient free space and put the
virtual environment, `UV_CACHE_DIR` (or `PIP_CACHE_DIR`), `TEMP`/`TMP`, archive
and extracted model on that disk. Keep them outside the source repository;
do not change the host/global Python. Configure `AKANE_CLEAN_PYTHON` and
`AKANE_CLEAN_MODEL_ROOT` in the process which runs the plugin/tests. Installing
these dependencies does not by itself install or activate this business plugin.

The official [DeepFilterNet2 archive](https://github.com/Rikorose/DeepFilterNet/tree/main/models)
used for acceptance has SHA256
`850e695d1c27f2a7b4dcd89b16bc8d49367effc680b1369e995e31d46376cba7`.
Keep `config.ini` and `checkpoints/model_96.ckpt.best` together. The worker uses
DeepFilterNet's supported `log_level="none"`, preventing its Git metadata
subprocess and host-identifying logging. The worker also rejects Python
subprocess creation via an audit hook. Python 3.11's Windows platform probe can
then use its real OS API fallback instead of starting `cmd /c ver`; no platform
values or third-party compatibility modules are fabricated. A real-model test
verifies inference still succeeds with descendant process creation denied.
