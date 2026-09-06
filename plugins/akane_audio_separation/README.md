# Audio separation runtime (migration in progress)

This package contains the isolated Demucs business worker, the public SDK
adapter and the existing loopback media-service protocol client. A private
release catalog is used for real wheel installation/lifecycle acceptance.
It is not yet listed in the user-facing market: the existing built-in remains
the product entry until old-callers/configuration cutover is verified.

The worker is independent of Akane private services. Its parent prepares
16-bit PCM WAV with FFmpeg; the worker uses real Demucs inference to produce
`vocals.wav` and `instrumental.wav`. It does not split stereo channels or copy
input as a synthetic successful result.

## Dependencies

- Python 3.11+, a compatible Demucs 4 / PyTorch / NumPy installation.
- The plugin worker uses host-provided CapCore and the public Akane plugin SDK;
  the standalone ML worker needs neither. No other Python dependencies are
  borrowed from the host for the remote HTTP transport.
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

## Adapter and remote service configuration

The administrator supplies process environment variables before starting Akane:

- `AKANE_MEDIA_FFMPEG` / `AKANE_MEDIA_FFPROBE`: optional executable paths;
  otherwise discover `ffmpeg` / `ffprobe` on PATH.
- `AKANE_SEPARATION_BACKEND`: `auto` (default), `local`, or `remote`.
- `AKANE_SEPARATION_MODEL`: `htdemucs` (default) or `htdemucs_ft` for local use.
- `AKANE_SEPARATION_DEVICE`: `auto` (default), `cpu`, or `cuda` for local use.
- `AKANE_SEPARATION_REMOTE_URL`: existing loopback HTTP media host, optionally
  reached through the existing reverse tunnel. No URL is silently invented.
- `AKANE_SEPARATION_UVR_MODEL`: existing remote UVR model name; default
  `HP5_only_main_vocal`.

Auto mode checks remote Demucs, then remote UVR, then local Demucs if remote is
unavailable. This selection is refreshed for each invocation. Explicit remote
mode never silently executes locally. A successful local fallback is reported
as local, not as a successful remote action.

These are environment variables, not newly added model-service UI fields.
The old `LOCAL_MEDIA_EXECUTOR_BASE_URL` setting also serves ASR/RVC and is not
removed or automatically imported from the host's private configuration. Full
configuration/caller cutover remains a migration gate; do not assume adding
these names to the old `.env` settings model will export them to subprocesses.

The plugin declares `network.read`, `resource.read`, `artifact.write` and
`capability.prompt.invoke`. It accepts only current-session material handles,
streams uploads and two-file ZIP responses, rejects archive path tricks and
registers the two resulting files through the host. WAV/FLAC/MP3 outputs are
probed with FFprobe. Matching remote codecs are not re-encoded unnecessarily.

The legacy media endpoints cannot abort inference remotely. Cancellation waits
for the actual successful response, discards its output, then acknowledges the
cancel. A network loss or server error during cancel may leave nested RVC work
unconfirmed and returns `remote_completion_unconfirmed`; it never claims the
remote model was stopped. No retries or new job queue are created.

The model tool defaults `send_to_user` to false, so two registered files do not
automatically become duplicate playback/delivery. The existing host file tools
can deliver the returned handles explicitly.

## Verification

Run from the repository root:

```powershell
python -m unittest discover -s plugins/akane_audio_separation/tests -v
python -m unittest tests.test_audio_separation_plugin -v
```

Real inference tests require existing model weights and the compatible ML
runtime. They do not download missing weights. Generic process tests also cover
cancellation during startup, repeated cancellation, and actual child exit.
Remote tests use a real loopback HTTP server with clearly marked fixture
outputs; they prove protocol/cancellation behavior, not real UVR inference.
