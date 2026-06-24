# Tool Interface

`AkaneMemoryEngine` now resolves model-emitted `tool_call` payloads through a small handler registry instead of hardcoding `call_npc` inside the main turn flow.

## Current Shape

- Registry lives in `AkaneMemoryEngine._build_tool_handlers()`
- Mode packs live in `TOOL_PACKS` / `MODE_TOOL_PACKS`
- Base protocol lives in `companion_v01/tool_runtime.py`
- Main final-response prompt receives only the tool descriptions allowed by the current `client_mode`
- Tool execution is filtered by the same mode pack, so hidden tools cannot be executed by hallucinated `tool_call`

## Mode Tool Packs

Tools are split into capability packs:

- `base`
  - `retrieve_memory`
  - `read_memory_timeline`
  - `set_reminder`
  - `list_reminders`
  - `cancel_reminder`
  - `manage_persona`
- `web_scene`
  - `call_npc`
  - `check_inventory`
  - `manage_gift`
  - `manage_artifact`
- `qq`
  - `fetch_media_from_url`
  - `sync_attachment_workspace`
  - `inspect_attachment`
  - `read_attachment_section`
  - `retry_attachment`
  - `clear_attachment_focus`
  - `compose_file`
  - `revise_generated_file`
  - `apply_style_to_existing_file`
  - `inspect_media_info`
  - `separate_audio_stems`
  - `clean_voice_track`
  - `transcribe_media`
  - `prepare_voice_dataset`
  - `convert_media_file`
  - `inspect_generated_file`
  - `send_file`
  - `manage_generated_file`
- `desktop`
  - `fetch_media_from_url`
  - `open_music_search`
  - `list_workspace`
  - `read_workspace`
  - `focus_workspace`
  - `register_workspace_items`
  - `sync_attachment_workspace`
  - `inspect_attachment`
  - `read_attachment_section`
  - `retry_attachment`
  - `clear_attachment_focus`
  - `compose_file`
  - `revise_generated_file`
  - `apply_style_to_existing_file`
  - `inspect_media_info`
  - `separate_audio_stems`
  - `clean_voice_track`
  - `transcribe_media`
  - `prepare_voice_dataset`
  - `convert_media_file`
  - `inspect_generated_file`
  - `send_file`
  - `manage_generated_file`

Mode mapping:

- `scene_static` -> `base + web_scene`
- `scene_live2d` -> `base + web_scene`
- `qq_text` -> `base + qq`
- `desktop_pet` -> `base + desktop`

## Handler Contract

Each tool handler should:

- expose a unique `tool_type`
- implement `build_prompt_instruction()` so the model knows when and how to call it
- implement `normalize_call(value)` to coerce aliases and reject invalid payloads
- implement `execute(call=..., context=...)` and return `ToolExecutionResult`

`ToolExecutionResult` currently supports:

- `raw_turns`: structured side-effect output that may be persisted into dialogue history
- `stream_events`: frontend-facing events emitted immediately during `/think`
- `followup_context`: extra context injected into Akane's second-pass reply after the tool finishes

## Adding A New Tool

1. Create a new handler class in `companion_v01/tool_runtime.py` or a sibling module.
2. Register it in `AkaneMemoryEngine._build_tool_handlers()`.
3. Add its tool name to the correct pack in `TOOL_PACKS`.
4. Keep prompt instructions concise and concrete; include one canonical JSON shape.
5. Return only normalized, bounded fields from `normalize_call()`.
6. Add focused tests for:
   - prompt injection
   - normalization
   - engine dispatch / execution
   - mode filtering, if the tool is mode-specific

## Current Tool

- `retrieve_memory`
  - performs semantic recall across the existing raw, episodic-summary, and long-term memory pipeline
  - is for people, events, preferences, agreements, and other content-based recall
  - is not used to dump an explicitly dated original transcript
- `read_memory_timeline`
  - reads raw dialogue only by an exact `date_from` / `date_to` range and optional time periods
  - does not run vector search and does not read episodic summaries or long-term semantic memory
  - is scoped by the current profile and character pack; model-supplied user or character ids are ignored
  - excludes the current query message from tool results while keeping it in the local daily transcript
  - for installed character packs, shares its renderer with the rebuildable local Markdown mirror under `desktop_pet_creator_kit/characters/<pack_id>/_local/memory/`
  - raw records without an installed character-pack owner remain under the compatibility mirror in `users_data/akane_memory_v01/memory/`
  - daily files keep readable dialogue, actual message time, available memory mood tags, and new assistant response emotion metadata; SQLite remains the source of truth
- `call_npc`
  - asks a temporary NPC for one short reply
  - streams an `npc_turn` event to the frontend
  - feeds the NPC line back into Akane for a follow-up answer
- `set_reminder`
  - stores a future reminder after backend-side time resolution
  - streams a `reminder_set` event when creation succeeds
  - if the time is still ambiguous, it does not create a reminder and instead pushes clarification context back to Akane
- `list_reminders`
  - reads current pending reminders for the session
  - feeds a numbered reminder list back into Akane so she can naturally summarize it to the user
- `cancel_reminder`
  - cancels a pending reminder by `reminder_id`, by list index, or by a text clue
  - if multiple reminders match, it refuses to guess and asks Akane to clarify with the user
- `inspect_attachment`
  - expands a temporary attachment card from the current session's Attachment Inbox
  - used by QQ / desktop-style clients when Akane needs details about an earlier image or file
  - does not promote the attachment into gifts, resources, or long-term memory
- `read_attachment_section`
  - expands a specific available section from a temporary document attachment, such as a line range, sheet, table, or PDF page range
  - prefers rereading the stored original attachment when available; falls back to the current parsed attachment card
  - if the file has no text layer or parser support, it returns a clear failure context
  - intended for long documents where Akane should not load every detail into the prompt by default
- `sync_attachment_workspace`
  - declaratively reorganizes the current detailed attachment workbench with a final target list
  - new QQ attachments are auto-focused first; this tool is mainly for narrowing, switching, or comparing selected materials after the batch arrives
  - accepts handles such as `img_001`, sequence clues such as `第2张图`, or title/file-name hints
  - supports multi-image / multi-file comparison; other attachments remain in the lightweight Manifest
  - uses a context-budget guard instead of a fixed 3-item cap, and asks Akane to use `read_attachment_section` for oversized files
- `clear_attachment_focus`
  - marks temporary attachments as `cleared` so they stop being injected into prompt context
  - clears only the focus inbox; it does not delete raw chat memory or gift assets
  - accepts either `target` for current/all/single-item clearing, or `targets` for arbitrary batch clearing such as `["img_001", "第2张图", "计划.md"]`
- `retry_attachment`
  - retries a failed temporary attachment through the same ingest pipeline
  - preserves the original handle such as `img_006` instead of creating a new attachment
  - useful when QQ / NapCat temporary links fail, the vision call fails, or a file parser had a transient error
- `fetch_media_from_url`
  - downloads one or more public media links into the current Attachment Inbox / workspace
  - intended for “先把这个视频链接下载下来”“把这两个公开视频拉进来再转写”
  - does not summarize, transcribe, convert, or resend by itself; after success Akane should continue with `inspect_attachment`, `inspect_media_info`, `transcribe_media`, `convert_media_file`, or `send_file`
  - supports direct media URLs immediately, and uses `yt-dlp` for ordinary public video/audio pages when installed
  - rejects playlists/collections and should not be used for login-only, paid, DRM, or private links
- `open_music_search`
  - opens a public music-platform search page for a requested song in desktop pet mode
  - accepts a title, optional artist, and optional platform (`qq_music`, `netease_music`, `bilibili`, `youtube`)
  - does not claim playback success, click results, log in, download, or control the player
  - if the user wants Akane to continue operating the page, follow-up browser actions still go through `browser_page` authorization
- `register_workspace_items`
  - registers files already present under the configured Akane workspace as attachment handles without copying or moving them
  - accepts one or more `workspace:/` files or directories and supports bounded recursive batch registration
  - stores only the safe workspace URI in attachment state; absolute local paths are not added to prompts or tool results
  - reuses the existing handle for the same active workspace URI and refreshes its parsed metadata when registered again
  - makes the returned handle available to existing document, media, conversion, transcription, and file-delivery tools
  - desktop turns always receive a compact workspace overview and recent-file manifest, so Akane can discover newly added files and call `list_workspace` without asking the user for an absolute path
  - new visible files use readable date folders such as `Inbox/2026-06-10` and `Outputs/2026-06-10`; internal session UUIDs remain database identifiers and are not used as folder names
- `compose_file`
  - creates a new generated file such as `gen_001` from temporary attachments, generated files, or current dialogue content
  - keeps the model-facing interface high level: sources, task, output format, title, and final content/table
  - supports `md`, `txt`, `docx`, `xlsx`, `pdf`, `json`, `csv`, and `html` as rendering targets when dependencies are installed
  - if no final content/table is supplied, it can use a larger bounded excerpt from the stored source attachment instead of only the short prompt preview
  - for faithful source conversion/export, Akane should leave `content_markdown/table_rows` empty so the backend reads the stored original attachment or generated file body without injecting task/source metadata into the output
  - if Akane accidentally copies only a visible source-prefix preview for a faithful conversion, the backend can recover by replacing it with fuller source material
  - accepts a declarative `formatting` object for common styles, such as `header.bold`, `columns[].match_header`, `rows[].index`, `cells[]`, and `highlights[].text`
  - current style execution focuses on `xlsx` and `docx`; `pdf` remains plain/content-first
  - stores output in `GeneratedFileStore`; it never overwrites the user's original attachment
  - returns a `generated_file_ready` event so QQ can attempt file upload and future clients can expose downloads
- `revise_generated_file`
  - creates a new version from an existing generated file such as `gen_001 -> gen_002`
  - requires Akane to provide the revised final content via `content_markdown` or `table_rows`; Python only renders and versions it
  - may reuse the same declarative `formatting` object to apply colors, bolding, highlights, rows, columns, or cells to the new version
  - never overwrites older generated files
  - returns the same `generated_file_ready` event used by `compose_file`
- `apply_style_to_existing_file`
  - creates a styled copy of an existing `docx/xlsx` attachment or generated file without asking Akane to re-output the full content
  - accepts `target`, optional `target_type`, `instruction`, `output_title`, and the same declarative `formatting` object
  - is intended for large-file operations such as “姓名列标红”, “低于 60 分整行标红”, or “把重点高亮”
  - stores the result as a new generated file, preserving the original attachment/generated file
  - returns the same `generated_file_ready` event used by other generated-file tools
- `inspect_media_info`
  - reads media metadata through `ffprobe` without generating a new file
  - answers questions such as duration, codec, sample rate, channels, bitrate, resolution, frame rate, or whether the file has an audio track
  - ordinary uploaded audio/video attachments already carry a lightweight media card when ingestion succeeds, so this tool is mainly for rechecking, generated files, or cases where Akane needs exact specs before conversion
- `separate_audio_stems`
  - uses a local audio-separation model workflow to split one source into `vocals` and `instrumental`
  - currently focuses on `vocals_instrumental` and should be used for “把人声和伴奏拆开 / 提取干声 / 保留伴奏”
  - supports ordinary audio files and video-like sources with audio tracks; video sources are prepared through `ffmpeg` before separation
  - outputs multiple generated files, which can be further refined again with `convert_media_file`
- `clean_voice_track`
  - cleans one speech/vocal source for tasks such as denoising, dereverb, de-echo, or making the voice more focused
  - supports `mode: denoise|dereverb|deecho|voice_focus` and `quality: auto|ai|basic`
  - `quality=auto` prefers a local DeepFilterNet workflow when available and falls back to basic `ffmpeg` cleanup otherwise
  - is intended for speech-like material or separated vocal stems, not for ordinary format conversion
  - outputs one generated file that can still be trimmed, resampled, normalized, or transcoded again with `convert_media_file`
- `transcribe_media`
  - transcribes one or more audio/video/generated media sources into text or subtitle files
  - accepts `source_ids`, `output_format: md|txt|srt|vtt|json`, `language`, `with_timestamps`, and `merge_outputs`
  - uses local `faster-whisper` when available; it does not call a cloud ASR API
  - can generate one merged transcript or one transcript per source
  - is intended before summarizing video/audio content, creating captions, meeting notes, highlights, or training labels
- `prepare_voice_dataset`
  - prepares one or more speech/vocal sources as a voice-training dataset batch
  - accepts `source_ids`, `profile: gpt_sovits|rvc|archive`, optional sample-rate and slicer parameters
  - standardizes sources through `ffmpeg`, slices by silence/RMS, writes `slices/*.wav`, `manifest.json`, and `README.md` into one generated zip
  - exposes issue slice filenames such as `too_short`, `too_long`, `low_volume`, and `clipping` so Akane can discuss cleanup with the user
  - is intended after `separate_audio_stems` / `clean_voice_track`, but can also process ordinary speech audio directly
- `convert_media_file`
  - converts ordinary non-encrypted media attachments/generated files through `ffmpeg`
  - supports `mp3`, `wav`, `flac`, `m4a`, `aac`, `ogg`, and `opus`
  - can compress audio, normalize sample rate/channels, or extract audio from video-like sources such as `mp4/mov/mkv/webm` if `ffmpeg` supports the input
  - can trim a single file with `start_time/end_time`, normalize loudness with `normalize_volume`, adjust overall volume with `volume_gain_db`, remove leading/trailing silence with `trim_silence`, add `fade_in_seconds/fade_out_seconds`, or change speed with `speed_ratio`
  - `bitrate`, `sample_rate`, and `channels` are optional; Akane should leave them empty unless the user asks for compression or a specific audio spec
  - media polishing fields are also optional; Akane should only fill them when the user asks to cut a clip, make volume comfortable, make it louder/quieter, add fades, or slow down/speed up
  - use `normalize_volume` for “声音忽大忽小/调正常/更舒服”; use positive `volume_gain_db` for “太小声/放大一点”, and negative values for “太吵/压低一点”; use `trim_silence=true` for “把前后空白切掉/去掉开头结尾静音”
  - for voice/speech-recognition style output, `wav + sample_rate=16000 + channels=1` is a sensible default; for music, preserving the original sample rate/channels is usually better
  - remains a single-source tool; do not use it for concatenating multiple audio files
  - refuses platform-protected/cache formats such as `kgm`, `ncm`, and `qmc`; it does not decrypt or bypass DRM/proprietary protection
  - stores the converted media as `gen_001` style generated output and returns `generated_file_ready`
- `inspect_generated_file`
  - reads back an existing generated file such as `gen_001` without sending, modifying, or deleting it
  - supports `content`, `head`, `tail`, `summary`, `file_list`, `manifest`, and `file:manifest.json` / `file:README.md` for generated zip bundles
  - text-like outputs (`md/txt/json/csv/html/srt/vtt`) are read from disk; `docx/xlsx/pdf` use local parser libraries when installed
  - for binary media it returns the generated content card and points Akane back to media tools such as `inspect_media_info` or `transcribe_media`
  - emits `generated_file_inspected` and feeds the inspected content back as follow-up context
- `send_file`
  - sends an existing local file from either Attachment Inbox (`file_001/img_001/audio_001`) or GeneratedFileStore (`gen_001/gen_002`)
  - accepts `target`, batch `targets`, handles, titles, or `latest`
  - does not modify, regenerate, transcode, archive, or delete anything
  - emits `file_ready`; QQ currently uploads this through the same delivery path used by generated files
- `send_generated_file`
  - legacy compatibility handler for resending generated files only
  - kept so older tool calls do not break, but new prompts should prefer `send_file`
- `manage_generated_file`
  - manages generated files such as `gen_001`, not temporary attachments such as `file_001/img_001`
  - accepts `action: archive|delete|purge` and batch `targets`
  - `archive` hides the generated file from the workbench; `delete` also removes the local generated file; `purge` additionally clears the generated content card
  - never deletes the user's original attachment sources
  - emits `generated_files_managed` for clients that want to update UI state
