---
name: video-understanding
description: Use when the user asks what a video actually shows or says — what happened in it, what people or objects appear, what happens at a specific time, actions, scene changes, subtitles, spoken content, or a summary/timeline of the video — and a video attachment is available in the current session.
---

# Video Understanding

Answer questions about the real visual and audio content of a video: frames must be extracted with `ffmpeg` through `exec_run`, registered, then loaded through `load_material` into the multimodal round; audio evidence uses `transcribe_media`. Never answer from the filename, file size, or metadata alone, and never invent content you could not actually see or hear.

## When to load

- The user asks what the video is about, what happens in it, who or what appears, or what a person/object is doing.
- The user asks what happens at a specific time (e.g. "at 1:20") or wants a summary, timeline, or scene-by-scene account.
- The user wants a judgment about action, scene changes, or whether something (person, object, text, logo) is visible in the video.
- The user asks what is said in the video (speech, dialogue, subtitles, lyrics) — pair frames with a transcript.
- The user sends a video and asks a question about it without saying "just save it".

The video usually appears in the workspace index with a `file_*` handle and a name ending in `.mp4`, `.mov`, `.mkv`, `.webm`, or `.avi`. QQ video attachments are indexed as `file_*`, not `video_*`; always reuse the exact handle shown in context.

## When NOT to load

- Only codec, duration, or resolution are requested, or the user wants a format conversion, trim, or re-encode: that is `media-inspect-convert`.
- The user just wants the video sent or saved.
- There is no video handle in the current session at all; do not invent one.
- `exec_run` is not in this turn's tool list (Shell off): the frame-extraction path is unavailable. Stop here, tell the user video content analysis is currently unavailable, and do not fake an answer about the content.

## Workflow

### 1. Probe the media first

One `exec_run` call, then read the JSON yourself:

```
ffprobe -v error -show_format -show_streams -print_format json inputs/source.mp4
```

Stage the video with `input_resources` (`as` = a safe relative path such as `inputs/source.mp4`), and do not set `cwd` when using `input_resources`/`output_globs`. Parse: duration, width/height, fps, whether a video stream and an audio stream exist, codecs. If ffprobe fails on a corrupt or unsupported file, report the structured failure and do not guess content.

### 2. Choose the viewing strategy yourself

Decide based on the question and the probed duration; do not let the host decide for you:

- Short video: a few evenly spaced frames (e.g. every few seconds).
- Medium video: an overview pass first, then reframe around the interesting time segment.
- Long video: a low-density overview only; never extract hundreds of frames in one pass.
- Specific-time question: extract around that moment (before and after it), not the whole video.
- Obvious scene changes: `select='gt(scene,0.3)'` frame selection can be used instead of plain sampling.
- Static image or slideshow: lower the density; a few frames are enough.
- Speech/subtitle/lyrics question: prefer a transcript (step 5); frames alone are insufficient evidence.

### 3. Extract timestamped frames and register them

```
python -c "from pathlib import Path; Path('outputs').mkdir(parents=True, exist_ok=True)" && ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 -ss 00:00:00 -frames:v 1 outputs/t_000s.jpg && ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 -ss 00:00:05 -frames:v 1 outputs/t_005s.jpg
```

- Choose no more than five explicit timestamps from the probed duration before extracting. Use one `-ss HH:MM:SS -frames:v 1` output per chosen moment and put the timestamp in its filename, such as `t_000s.jpg`, `t_005s.jpg`, or `t_01m20s.jpg`. The generated filename is the model-visible timestamp evidence; do not rename it to an anonymous `frame_01.jpg`.
- If scene detection is useful, use it only to discover candidate moments. Read the reported presentation timestamps, then re-extract the selected moments with explicit `-ss` commands and timestamped filenames before calling `load_material`. A numbered scene frame with no known timestamp is not sufficient evidence for a timeline claim.
- Declare every intended output with `output_globs` (e.g. `outputs/t_*.jpg`); completed runs register real `gen_*` handles. Success is only `status=completed` with `artifact_status=registered` and real `gen_*` handles — an `exit_code=0` without registration is not a deliverable.
- Keep batches small: `load_material` accepts up to 5 images per call and output registration is capped; for more coverage, extract once more for the specific segment rather than flooding one run.
- Do not set `cwd`; ffmpeg does not create directories, so create `outputs` first with the cross-platform Python command shown above. Prefer `-hide_banner -loglevel error -y` so successful runs stay clean and failures still return the error.

### 4. Load the frames into the multimodal round

Call `load_material` with the exact `gen_*` handles from `generated_resources` (up to 5), e.g.:

```
{"type": "load_material", "targets": ["gen_001", "gen_002", "gen_003"], "purpose": "查看视频关键帧内容"}
```

The loaded images are sent to the vision model in the next round. Track which handles loaded successfully: an `unresolved` item means that frame never reached the model — do not describe it. If nothing loads, say so and stop; do not claim you saw frames you did not see.

### 5. Audio evidence when the question needs it

- If the question is about speech, dialogue, subtitles, or lyrics, call `transcribe_media` on the video handle (or a `gen_*` audio already extracted); it handles video files directly by using the audio track.
- If ffprobe showed no audio stream, do not invent dialogue; say the video has no audio track.
- If transcription fails, answer from frames only and state that the spoken content could not be verified.

### 6. Answer with the evidence

Combine the question, probed metadata, loaded frames (with their timestamps), and any transcript, and reply in character naturally. State boundaries honestly: which time range the frames covered, whether you heard speech, what failed. Offer the next segment you can look at ("前面 10 秒的画面看到了…，要不要我再看 1 分 20 秒附近？"). Do not recite internal commands or pipeline mechanics to the user.

## Model-visible information

Before answering, make sure you can state: the video handle; duration; resolution; audio-track presence; each frame's timestamp; which frames actually loaded; transcript coverage; and any failure, timeout, or missing evidence. A bare "抽帧成功" without knowing which timestamps loaded is not enough.

## Memory and reload

Tool rounds (`load_skill`, `exec_run`, `load_material`, `transcribe_media`) are recorded in order. If a later question in this session needs the same evidence (frame handles, transcript, or ffprobe results), reopen it with `open_memory` instead of re-extracting. Do not put video bytes, embedded image data, or host paths into replies.

## Failure handling

- `exec_run` absent (Shell off) or `ffmpeg`/`ffprobe` missing: report the dependency as unavailable; do not fake a content answer.
- `failed`/`timed_out`/`cancelled` run: tell the user honestly what failed; no output was produced.
- Corrupt or undecodable video: structured failure, no invented content.
- No audio track or failed transcript: answer from frames and mark the audio boundary.
- Registration failed with no `gen_*` handles: report that no frames were recorded and correct `output_globs` before re-running.
