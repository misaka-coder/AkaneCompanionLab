# Audio separation runtime (migration in progress)

This package currently contains the isolated Demucs business worker and its
cancellation-aware parent. It is not yet listed in the market or registered as
a model tool. The existing built-in remains the sole product entry until the
resource/adapter, remote backend and installation acceptance slice replaces it.

The worker is independent of Akane private services. Its parent prepares
16-bit PCM WAV with FFmpeg; the worker uses real Demucs inference to produce
`vocals.wav` and `instrumental.wav`. It does not split stereo channels or copy
input as a synthetic successful result.

## Dependencies

- Python 3.11+, a compatible Demucs 4 / PyTorch / NumPy installation.
- Existing Demucs model checkpoints, with filenames retaining their checksums.
  `htdemucs` and `htdemucs_ft` use the model-bag definitions shipped with Demucs.
- `AKANE_SEPARATION_PYTHON`: optional explicit ML interpreter. By default the
  current interpreter is used, but a real model-load probe is mandatory.
- `AKANE_SEPARATION_MODEL_ROOT`: optional directory of model checkpoint files.
  Default: the selected interpreter's existing Torch hub checkpoints directory.
- No model downloads, package installation, shell command expansion or hidden
  GPU runtime installation occur. Missing/incompatible runtime or weights fail
  with a structured reason. Paths stay private to the worker boundary.

Demucs 4 checkpoints include Python model classes and are loaded as trusted
pickle-based artifacts, matching its pretrained loader. Only administrator-
provisioned model files belong in this directory, never user attachments.
Filename checksums detect corruption; they are not publisher authentication.

The worker supports auto CPU/CUDA selection and CPU retry after a CUDA runtime
failure, as the prior implementation did. This does not promise GPU support
when the configured interpreter contains a CPU-only PyTorch build.

## Verification

Run from the repository root:

```powershell
python -m unittest discover -s plugins/akane_audio_separation/tests -v
```

Real inference tests require existing model weights and the compatible ML
runtime. They do not download missing weights. Generic process tests also cover
cancellation during startup, repeated cancellation, and actual child exit.
