---
name: video-understanding
description: Use when the user asks what a video actually shows or says — what happened in it, what people or objects appear, what happens at a specific time, actions, scene changes, subtitles, spoken content, or a summary/timeline of the video — and a video attachment is available in the current session.
metadata:
  required_tools:
    - exec_run
    - load_material
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

Every `exec_run` that uses `input_resources`/`output_globs` starts in a fresh per-run resource workspace. Files staged for the probe do not carry into the next command. Repeat the exact video handle and `as: inputs/source.mp4` in every later `exec_run` that reads the video. A `gen_*` output from an earlier run must likewise be restaged through `input_resources` before another run can read it.

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
- Speech/subtitle/lyrics question: prefer a transcript (step 6); frames alone are insufficient evidence.

### 3. Make one or more contact sheets for the overview

```
ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 -vf "fps=36/DURATION,scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,drawtext=text='%{pts\\:hms}':x=8:y=h-th-8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.65,tile=4x3:nb_frames=12:padding=4:margin=4" -frames:v 3 overview_%02d.jpg
```

- For a broad "what happens" question, prefer contact sheets before loading many individual frames. Use one 3x3 sheet (9 frames) for a short or quiet video; use two to five 3x3/4x3 sheets for a longer, faster, or scene-dense video. Read sheets by filename and cells left-to-right, top-to-bottom.
- Choose `SHEET_COUNT` from 1 to 5 and `CELLS_PER_SHEET` as 9 or 12. Replace `36` in the example with `SHEET_COUNT * CELLS_PER_SHEET`, replace `DURATION` with the probed duration, and set `-frames:v SHEET_COUNT`. This samples the entire video in temporal order and emits sequential files such as `overview_01.jpg`, `overview_02.jpg`, and `overview_03.jpg`.
- Keep every completed sheet roughly 1200-1600 pixels wide. Prefer several legible sheets over one sheet with dozens of tiny cells; do not exceed five sheets in one overview because `load_material` accepts at most five images per call.
- Burn a timestamp into every cell when `drawtext` is available. If this ffmpeg build lacks `drawtext` or a usable font, retry without that filter and put the exact row-major cell-to-time mapping in the subsequent `load_material.purpose`; never silently pretend an unlabeled cell has a precise timestamp.
- Repeat `input_resources=[{"handle": "<exact file_* handle>", "as": "inputs/source.mp4"}]` on this extraction call even though the probe already staged the same video. Declare `output_globs=["overview_*.jpg"]`; do not set `cwd`.
- Load all registered overview `gen_*` handles together in one `load_material` call, in filename order, and state each sheet's covered time range in `purpose`. Use the sheets jointly to locate scenes, actions, cuts, and promising time ranges. A contact sheet is an overview, not reliable evidence for small subtitles, fine UI text, faces, brief objects, or exact fast motion.
- This usually reduces image blocks and tool feedback, but it does not guarantee a fixed visual-token reduction: providers may tile a large image internally. Never trade away evidence merely to minimize token count.

### 4. Deep-read selected moments at original frame size

After reviewing the contact sheet or sheets, extract only the two to five moments needed to answer the question. For a specific-time question, obvious small text, or very short video, you may skip the overview and go directly here.

```
ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 -ss 00:00:00 -frames:v 1 t_000s.jpg && ffmpeg -hide_banner -loglevel error -y -i inputs/source.mp4 -ss 00:00:05 -frames:v 1 t_005s.jpg
```

- Repeat the exact video `input_resources` again for this new `exec_run`; a prior run's `inputs/source.mp4` no longer exists here.
- Choose no more than five explicit timestamps using `-ss HH:MM:SS`. Put the timestamp in each filename, such as `t_000s.jpg`, `t_005s.jpg`, or `t_01m20s.jpg`, and declare `output_globs=["t_*.jpg"]`.
- If scene detection is useful, use it only to discover candidate moments. Read the reported presentation timestamps, then re-extract selected moments with explicit `-ss` commands and timestamped filenames. A numbered scene frame with no known timestamp is not sufficient evidence for a timeline claim.
- Success is only `status=completed` with `artifact_status=registered` and real `gen_*` handles. An `exit_code=0` without registration is not a deliverable.
- Prefer `-hide_banner -loglevel error -y` so successful runs stay clean and failures still return the error. Write outputs directly in the managed current directory; no Python helper or extra output directory is required.

### 5. Load the frames into the multimodal round

Call `load_material` with the exact `gen_*` handles from `generated_resources` (up to 5), e.g.:

```
{"type": "load_material", "targets": ["gen_001", "gen_002", "gen_003"], "purpose": "查看视频关键帧内容"}
```

The loaded images are sent to the vision model in the next round. Track which handles loaded successfully: an `unresolved` item means that frame never reached the model — do not describe it. If nothing loads, say so and stop; do not claim you saw frames you did not see.

### 6. Audio evidence when the question needs it

- If the question is about speech, dialogue, subtitles, or lyrics, call `transcribe_media` on the video handle (or a `gen_*` audio already extracted); it handles video files directly by using the audio track.
- If ffprobe showed no audio stream, do not invent dialogue; say the video has no audio track.
- If transcription fails, answer from frames only and state that the spoken content could not be verified.

### 7. Answer with the evidence

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
