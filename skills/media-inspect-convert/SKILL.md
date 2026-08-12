---
name: media-inspect-convert
description: Use when exec_run is available and the user needs to inspect audio/video metadata or convert, trim, resample, change speed, normalize volume, or re-encode an existing media resource.
---

# Media Inspect & Convert

Directly operate existing audio/video resources with `ffprobe` and `ffmpeg` through `exec_run`.
This skill is only relevant when `exec_run` is actually offered in the current turn; if it is not, stop here and do not fake the work.

## When to load

- The user asks for the real specs of an existing media file: duration, codec, sample rate, channels, bitrate, resolution, fps, whether it has an audio track.
- The user asks to convert, trim a segment, resample/change channels, change speed, adjust or normalize volume, fade in/out, or remove leading/trailing silence of an existing media resource.
- A media handle (`file_*`, `img_*`, `audio_*`, `gen_*`) already exists in this session's context and the user wants the resulting file delivered or kept in the workspace.

## When NOT to load

- `exec_run` is not in this turn's tool list (Shell off). Keep using the dedicated media tools instead.
- The task is transcription (`transcribe_media`), vocal separation (`separate_audio_stems`), voice cleaning (`clean_voice_track`), dataset slicing (`prepare_voice_dataset`), or voice-clone covers. Those stay on their own tools.
- A simple chat question with no actual media resource to operate on. Do not invent a command to "check" something that does not exist.

## Handle and resource rules

- Always reuse the **exact handle** already shown in context. Never invent handles and never pass `latest` to `exec_run`.
- Do not try to decrypt DRM-protected media or proprietary platform cache formats such as `ncm`, `qmc`,
  `kgm`, or `mgg`. Ask for a normal user-owned source such as WAV, MP3, FLAC, M4A, or MP4 instead.
- Stage the input with `input_resources` using `as` = a safe relative path inside the run workspace, e.g. `inputs/source.wav`. The model only ever references that relative path, never a host path.
- Declare every intended output with `output_globs` relative to the current run directory, e.g. `outputs/result.mp3`.
- **Do not set `cwd`** when using `input_resources`/`output_globs`; resource mode requires omitting `cwd`.
- Prefer directory prefixes (`inputs/`, `outputs/`) so inputs and results do not collide.
- ffmpeg does not create directories: before writing into `outputs/`, create it first in the same command, e.g. `mkdir outputs && ffmpeg -y ...`. Plain `mkdir` works on both cmd and POSIX shells; the run workspace is fresh, so the directory does not exist yet.
- For routine conversion commands, prefer `ffmpeg -hide_banner -loglevel error -y ...` so a successful run
  does not fill the tool result with progress noise; failures still return the relevant error.

## Inspect with ffprobe

One call, then read the JSON yourself:

```
ffprobe -v error -show_format -show_streams -print_format json inputs/source.mp4
```

Parse duration, bit_rate, and per-stream codec/sample_rate/channels/width/height/fps from the result and answer naturally.

## Convert with ffmpeg

Format conversion:

```
ffmpeg -y -i inputs/source.wav -codec:a libmp3lame -q:a 2 outputs/result.mp3
```

Fast video trim (`-ss` before `-i` for input seeking; stream copy may start on a nearby keyframe):

```
ffmpeg -y -ss 00:00:05 -i inputs/source.mp4 -t 10 -c copy outputs/clip.mp4
```

When the boundary must be accurate, re-encode instead of using stream copy, for example:

```
ffmpeg -y -ss 00:00:05 -i inputs/source.mp4 -t 10 -c:v libx264 -c:a aac outputs/clip.mp4
```

For an audio-only trim, also re-encode with an explicit audio codec instead of `-c copy`.

Resample / channels:

```
ffmpeg -y -i inputs/source.wav -ar 44100 -ac 2 -codec:a pcm_s16le outputs/result.wav
```

Change speed (audio only):

```
ffmpeg -y -i inputs/source.wav -filter:a atempo=1.5 outputs/faster.wav
```

One `atempo` stage should stay between 0.5 and 2.0 for broad ffmpeg compatibility. Chain stages for
larger changes, such as `atempo=2.0,atempo=2.0` for 4x or `atempo=0.5,atempo=0.5` for 0.25x.

Adjust / normalize volume:

```
ffmpeg -y -i inputs/source.wav -filter:a volume=6dB outputs/louder.wav
ffmpeg -y -i inputs/source.wav -filter:a loudnorm outputs/normalized.wav
```

Fade in / out:

```
ffmpeg -y -i inputs/source.wav -af "afade=t=in:st=0:d=1,afade=t=out:st=2:d=1" outputs/faded.wav
```

The example assumes a 3-second output. For a real fade-out, inspect the effective output duration first
and set the fade-out start to `duration - fade_duration`; do not blindly reuse `st=2`.

Remove leading and trailing silence while preserving pauses in the middle:

```
ffmpeg -y -i inputs/source.wav -af "silenceremove=start_periods=1:start_duration=0.12:start_threshold=-50dB:start_silence=0.02:detection=peak,areverse,silenceremove=start_periods=1:start_duration=0.12:start_threshold=-50dB:start_silence=0.02:detection=peak,areverse" outputs/trimmed.wav
```

Do not replace this with `stop_periods=1`: that can stop at the first silence inside speech or music and
discard everything after it. For videos known to contain both streams, apply audio filters with
`-filter:a` and map the intended video/audio streams; changing video speed requires `setpts` on the video
stream together with `atempo` on the audio.

## Platform differences

- The executor's environment provides `ffprobe`/`ffmpeg` on `PATH`; write commands the same way on Windows and Linux.
- In commands, always use forward slashes for the run-workspace relative paths (never drive letters or absolute paths).
- Long-running operations are fine: if the run returns `running` with a `run_id`, follow up with `exec_status` instead of re-running.

## Reading results

- The initial `exec_run` result already returns generous output. Normal commands do not need cursor paging; only continue with `exec_status(...cursor=...)` when output was explicitly truncated or the task is still running.
- A conversion is only a success when `status` is `completed`. Then check `artifact_status` and `generated_resources`: only a `registered` `artifact_status` with real `gen_*` handles means the outputs were recorded.
- If the user wants the file, call `send_file` with the exact `gen_*` handle from `generated_resources`.
- Deliver once. Do not loop `send_file`; do not report a file as delivered based only on the command's `exit_code`.

## Failure handling

- `failed` / `timed_out` / `cancelled`: tell the user honestly what failed and why; do not claim any output was produced.
- `registration_failed` or empty `generated_resources` after a successful command: the file bytes may exist but were not recorded — report that no deliverable result is registered, and correct `output_globs` before re-running.
- A missing `ffmpeg`/`ffprobe` returns a clear failure status; report the dependency as unavailable instead of fabricating a result.
