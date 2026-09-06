# Voice dataset plugin

An optional market wheel using Akane's public resource, capability composition,
artifact and process SDK. Install and enable it explicitly through the existing
market; only then does `akane.voice-dataset.run.v1` become discoverable. The old
built-in dataset entry and slicing implementation are deleted.

The package prepares actual PCM with FFmpeg, slices at pauses and writes WAVs,
a source-linked quality manifest and a ZIP. GPT-SoVITS/RVC/archive are preparation
presets, not model training. Min/max clip lengths mark quality issues; they do
not silently chop speech at arbitrary lengths. Silence-only material is retained
as flagged, never described as recommended speech. No model download or network.

Explicit `mono=false` now preserves two-channel slice audio and metadata; the
old implementation silently downmixed it despite that option. Omitted settings
use actual profile defaults rather than treating sentinel zero as 8 kHz.
Invalid explicit values fail instead of silently altering user intent.

`clean_first` invokes the installed and enabled `akane.voice-clean.run.v1`
through normal permissions using the public `capability.invoke` port. It requests
basic voice-focus processing, not AI. Missing/disabled/unapproved cleaning is a
source failure, never an unprocessed success. This package has no second filter
recipe. Cleaned intermediate audio is registered separately but never sent or
played; the dataset result contains one ZIP and reports failed sources honestly.

Inputs are current-session `source_ids` (attachment or generated handles), not
host paths. Options: profile, target_sr, mono, min_clip_seconds, max_clip_seconds,
silence_threshold_db, min_silence_ms, max_silence_kept_ms, clean_first,
normalize_volume, output_title, send_to_user (default false). Schema and legacy
instructions derive from one descriptor. User cancellation drains dependencies
and children before its terminal; an already completed intermediate is not
falsely described as rolled back.

FFmpeg and FFprobe use `AKANE_MEDIA_FFMPEG` / `AKANE_MEDIA_FFPROBE`, otherwise
PATH. Health executes both binaries. The public subprocess SDK owns all children;
the stdlib PCM/ZIP worker cannot spawn children. Cancellation/timeout drains the
real process before removing partial files. Input audio is never modified.

Limits: 20 sources, 30 minutes per source, 512 MiB total prepared PCM, 256 MiB
ZIP, 10,000 slices. Oversize input/output has an explicit failure, not truncation.

Validation: `python -m unittest discover -s plugins/akane_voice_dataset/tests -q`
and `python -m unittest tests.test_voice_dataset_plugin -q`. The latter installs
real wheels, checks actual ZIP/WAV contents, clean-first composition, scoped
discovery, failed candidates, running disable/cancel, Job/Memory and desktop/QQ
delivery boundaries. QQ HTTP is an explicit fixture, not a live send.
