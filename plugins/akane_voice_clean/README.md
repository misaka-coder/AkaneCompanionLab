# Voice cleaning business runtime (migration slice A)

This is not yet a discoverable plugin or market entry. The old built-in remains
the sole product entry until resource/Job/market integration and deletion are
validated. This library contains the new recipe; do not add product callers to
both implementations.

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
