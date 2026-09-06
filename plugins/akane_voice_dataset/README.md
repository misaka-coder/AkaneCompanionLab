# Voice dataset business runtime (migration A)

This source-only migration slice is not a discoverable plugin yet. The old
product entry remains the authority until SDK composition, market installation,
Job/delivery/lifecycle validation and deletion of the old implementation finish.
Do not connect new product consumers to both paths.

The package prepares actual PCM with FFmpeg, slices at pauses and writes WAVs,
a source-linked quality manifest and a ZIP. GPT-SoVITS/RVC/archive are preparation
presets, not model training. Min/max clip lengths mark quality issues; they do
not silently chop speech at arbitrary lengths. Silence-only material is retained
as flagged, never described as recommended speech. No model download or network.

Explicit `mono=false` now preserves two-channel slice audio and metadata; the
old implementation silently downmixed it despite that option. Omitted settings
use actual profile defaults rather than treating sentinel zero as 8 kHz.
Invalid explicit values fail instead of silently altering user intent.

`clean_first` requires sources prepared by the enabled cleaning capability.
This library deliberately owns no second filter recipe. The eventual adapter
must invoke that capability through ordinary permissions and propagate failure;
until then a source not marked actually cleaned is rejected.

FFmpeg and FFprobe use `AKANE_MEDIA_FFMPEG` / `AKANE_MEDIA_FFPROBE`, otherwise
PATH. Health executes both binaries. The public subprocess SDK owns all children;
the stdlib PCM/ZIP worker cannot spawn children. Cancellation/timeout drains the
real process before removing partial files. Input audio is never modified.

Limits: 20 sources, 30 minutes per source, 512 MiB total prepared PCM, 256 MiB
ZIP, 10,000 slices. Oversize input/output has an explicit failure, not truncation.
